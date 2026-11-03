"""Battery saver (#140): the power source from a fake sysfs, the Deck check, when it applies, what
it sets for one start (never the player's settings) and the in-game menu's save under it."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import config, game, ingame, power, schema
from forza_launcher.config import Config


def supply(root, name, **files):
    d = Path(root) / "class/power_supply" / name
    d.mkdir(parents=True)
    for k, v in files.items():
        (d / k).write_text(f"{v}\n")


def deck_dmi(root, board="Galileo"):
    d = Path(root) / "devices/virtual/dmi/id"
    d.mkdir(parents=True)
    (d / "board_name").write_text(board + "\n")
    (d / "board_vendor").write_text("Valve\n")


def deck(root, online, status):
    """The Deck's own supplies: ACAD (the charger) and BAT1."""
    deck_dmi(root)
    supply(root, "ACAD", type="Mains", online=online)
    supply(root, "BAT1", type="Battery", status=status, present=1, scope="System")


class PowerSource(unittest.TestCase):
    def source(self, build):
        with tempfile.TemporaryDirectory() as t:
            build(t)
            return power.power_source(t)

    def test_deck_unplugged_is_on_battery(self):
        self.assertEqual(self.source(lambda t: deck(t, 0, "Discharging")), power.BATTERY)

    def test_deck_plugged_in_is_on_mains_even_when_not_charging(self):
        self.assertEqual(self.source(lambda t: deck(t, 1, "Charging")), power.MAINS)
        self.assertEqual(self.source(lambda t: deck(t, 1, "Not charging")), power.MAINS)
        self.assertEqual(self.source(lambda t: deck(t, 1, "Full")), power.MAINS)

    def test_charger_offline_without_a_status_is_battery(self):
        def build(t):
            supply(t, "ACAD", type="Mains", online=0)
            supply(t, "BAT1", type="Battery", status="Unknown")
        self.assertEqual(self.source(build), power.BATTERY)

    def test_usb_c_charger_counts_as_mains(self):
        def build(t):
            supply(t, "ACAD", type="Mains", online=0)
            supply(t, "ucsi-source-psy-USBC000-001", type="USB", online=1)
            supply(t, "BAT1", type="Battery", status="Discharging")
        self.assertEqual(self.source(build), power.MAINS)

    def test_desktop_without_a_battery_is_unknown(self):
        self.assertEqual(self.source(lambda t: None), "")
        self.assertEqual(self.source(lambda t: supply(t, "AC", type="Mains", online=1)), "")

    def test_a_controllers_battery_is_not_the_systems(self):
        def build(t):
            supply(t, "ps-controller-battery-aa-bb", type="Battery", status="Discharging", scope="Device")
            supply(t, "hidpp_battery_0", type="Battery", status="Discharging", scope="Device")
        self.assertEqual(self.source(build), "")

    def test_empty_bay_and_full_battery_without_charger_info_are_unknown(self):
        self.assertEqual(self.source(lambda t: supply(t, "BAT0", type="Battery", status="Discharging",
                                                       present=0)), "")
        self.assertEqual(self.source(lambda t: supply(t, "BAT0", type="Battery", status="Full")), "")


class Decide(unittest.TestCase):
    def setUp(self):
        # Exercise the Linux policy on every host, retaining the explicit Windows guard test.
        platform = patch.object(power, "IS_WINDOWS", False)
        platform.start()
        self.addCleanup(platform.stop)

    def decide(self, mode, build, **settings):
        with tempfile.TemporaryDirectory() as t:
            build(t)
            return power.decide({**schema.defaults(), **settings, "battery_saver": mode}, t)

    def test_off_never_applies(self):
        self.assertFalse(self.decide("off", lambda t: deck(t, 0, "Discharging")).active)

    def test_on_applies_anywhere(self):
        s = self.decide("on", lambda t: None, frame_rate=60, resolution_scale=2)
        self.assertTrue(s.active)
        self.assertEqual(s.settings, {"frame_rate": 30, "resolution_scale": 1, "scene_msaa": "off"})
        self.assertIn("--gpu_wait_blocking=true", s.flags)
        self.assertIn("30 fps", s.note())

    def test_a_rate_for_a_40_hz_screen_stays(self):
        """20 and 40 divide a Deck's screen at 40 Hz, which 30 does not: battery saver keeps them."""
        for rate in (20, 40):
            s = self.decide("on", lambda t: None, frame_rate=rate, scene_msaa="off")
            self.assertEqual(s.settings, {})
            self.assertIn(f"{rate} fps", s.note())
        s = self.decide("on", lambda t: None, frame_rate=30, scene_msaa="2x")
        self.assertEqual((s.settings, s.frame_rate), ({"scene_msaa": "off"}, 30))

    def test_auto_applies_on_a_deck_on_battery_only(self):
        s = self.decide("auto", lambda t: deck(t, 0, "Discharging"), frame_rate=60)
        self.assertTrue(s.active)
        self.assertEqual(s.reason, "on battery")
        s = self.decide("auto", lambda t: deck(t, 1, "Charging"), frame_rate=60)
        self.assertFalse(s.active)
        self.assertEqual((s.reason, s.settings, s.flags), ("on mains power", {}, []))

    def test_auto_does_not_apply_on_a_laptop(self):
        def laptop(t):
            supply(t, "AC", type="Mains", online=0)
            supply(t, "BAT0", type="Battery", status="Discharging")
        s = self.decide("auto", laptop)
        self.assertEqual((s.active, s.reason), (False, "not a Steam Deck"))

    def test_heavy_filters_become_fsr_1(self):
        s = self.decide("on", lambda t: None, present_effect="fsr3", forza_antialiasing="smaa")
        self.assertEqual(s.settings["present_effect"], "fsr")
        self.assertEqual(s.settings["forza_antialiasing"], "game")
        s = self.decide("on", lambda t: None, present_effect="fsr", frame_rate=30, scene_msaa="off")
        self.assertEqual(s.settings, {})   # the Deck preset already is the lightest
        self.assertTrue(s.active)

    def test_windows_never_applies(self):
        with patch.object(power, "IS_WINDOWS", True):
            self.assertFalse(self.decide("on", lambda t: None).active)

    def test_auto_warns_off_the_deck(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertIn("not a Steam Deck", power.setting_warning("auto", t))
            deck_dmi(t)
            self.assertEqual(power.setting_warning("auto", t), "")
        self.assertEqual(power.setting_warning("on", "/nonexistent"), "")


class LaunchPlan(unittest.TestCase):
    def setUp(self):
        platform = patch.object(power, "IS_WINDOWS", False)
        platform.start()
        self.addCleanup(platform.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.sysfs = t / "sys"
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.cfg.online["mode"] = "offline"
        self.cfg.settings.update(frame_rate=60, resolution_scale=2, present_effect="fsr")
        self.install = game.GameInstall(t / "rt", t / "rt" / game.EXE)
        (t / "rt").mkdir()
        patcher = patch.object(power, "SYSFS", str(self.sysfs))
        patcher.start()
        self.addCleanup(patcher.stop)

    def plan(self, write=False, **kw):
        return game.build_plan(self.cfg, self.install, write=write, log=lambda _t: None, **kw)

    def test_off_starts_with_the_players_settings(self):
        plan = self.plan()
        self.assertIn("frame_rate = 60", plan.toml_text)
        self.assertNotIn("--gpu_wait_blocking=true", plan.argv)

    def test_on_starts_light_and_keeps_the_players_settings(self):
        self.cfg.settings["battery_saver"] = "on"
        plan = self.plan(write=True)
        self.assertNotIn("frame_rate", plan.toml_text)        # 30, the default
        self.assertNotIn("resolution_scale", plan.toml_text)  # 1, the default
        self.assertNotIn("battery_saver", plan.toml_text)     # the launcher's own
        self.assertIn("--gpu_wait_blocking=true", plan.argv)
        self.assertTrue(any("Battery saver" in n for n in plan.notes))
        self.assertEqual((self.cfg.settings["frame_rate"], self.cfg.settings["resolution_scale"]), (60, 2))
        saved = Config(self.cfg.path)
        self.assertEqual(saved.settings["frame_rate"], 60)
        self.assertEqual(saved.d["power"]["applied"], {"frame_rate": 30, "resolution_scale": 1, "scene_msaa": "off"})
        self.assertIn('scene_msaa = "off"', plan.toml_text)

    def test_extra_arguments_still_win(self):
        self.cfg.settings["battery_saver"] = "on"
        self.cfg.advanced["extra_args"] = "--gpu_wait_blocking=false"
        argv = self.plan().argv
        self.assertLess(argv.index("--gpu_wait_blocking=true"), argv.index("--gpu_wait_blocking=false"))

    def test_auto_on_a_deck_follows_the_charger(self):
        self.cfg.settings["battery_saver"] = "auto"
        deck(self.sysfs, 0, "Discharging")
        self.assertNotIn("frame_rate", self.plan().toml_text)
        (self.sysfs / "class/power_supply/ACAD/online").write_text("1\n")
        (self.sysfs / "class/power_supply/BAT1/status").write_text("Charging\n")
        self.assertIn("frame_rate = 60", self.plan().toml_text)

    def test_shader_prebuild_plans_for_the_players_settings(self):
        self.cfg.settings["battery_saver"] = "on"
        plan = self.plan(battery_saver=False)
        self.assertIn("frame_rate = 60", plan.toml_text)
        self.assertNotIn("--gpu_wait_blocking=true", plan.argv)

    def test_a_menu_save_under_battery_saver_keeps_the_players_rate(self):
        self.cfg.settings["battery_saver"] = "on"
        self.plan(write=True)
        pending = Path(self.cfg.advanced["data_root"]) / ingame.FILENAME
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(json.dumps({"version": 1, "settings": {
            "frame_rate": "30", "resolution_scale": "1", "anisotropic_override": "2"}}))
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        self.assertEqual(self.cfg.settings["frame_rate"], 60)
        self.assertEqual(self.cfg.settings["resolution_scale"], 2)
        self.assertEqual(self.cfg.settings["anisotropic_override"], 2)
        # a start on mains power forgets what was forced
        self.cfg.settings["battery_saver"] = "off"
        self.plan(write=True)
        self.assertIsNone(Config(self.cfg.path).d["power"]["applied"])


class Setting(unittest.TestCase):
    def test_display_choice_on_linux_never_written_to_forza_toml(self):
        s = schema.BY_KEY["battery_saver"]
        self.assertEqual((s.group, s.section, s.kind, s.default, s.platform), ("Display", "Frame rate", "choice",
                                                                               "off", "linux"))
        self.assertEqual([c[0] for c in s.choices], ["off", "on", "auto"])
        self.assertIn("battery_saver", config.LAUNCHER_ONLY)


if __name__ == "__main__":
    unittest.main()
