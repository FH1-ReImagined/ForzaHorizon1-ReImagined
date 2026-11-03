"""The Steam Deck (#140): detection from fake DMI, PCI and /proc files and Steam's SteamDeck=1, its preset in
the recommended-settings offer, Match display on its screen's refresh rates. Run:
python3 -m unittest launcher/test_steam_deck.py"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import display, gpu, power, specs  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
from forza_launcher.specs import Specs  # noqa: E402

LCD_CPU, OLED_CPU = "AMD Custom APU 0405", "AMD Custom APU 0932"
DECK = Specs(cpu_name=LCD_CPU, cpu_cores=4, cpu_threads=8, avx2=True, ram_gb=14.5, gpu_name="Steam Deck",
             vram_gb=1, integrated=True)
LAPTOP = Specs(cpu_name="AMD Ryzen 7 7840U", cpu_cores=8, cpu_threads=16, avx2=True, ram_gb=32,
               gpu_name="AMD Radeon 780M", vram_gb=4, integrated=True)


def machine(root, board="", board_vendor="Valve", product="", gpu_id="", cpu="AMD Ryzen 7 7800X3D 8-Core Processor"):
    """A fake /sys and /proc under root; returns (sysfs, proc)."""
    sysfs, proc = Path(root, "sys"), Path(root, "proc")
    dmi = sysfs / "devices/virtual/dmi/id"
    dmi.mkdir(parents=True)
    proc.mkdir()
    for name, value in (("board_name", board), ("board_vendor", board_vendor if board or product else ""),
                        ("product_name", product)):
        if value:
            (dmi / name).write_text(value + "\n")
    if gpu_id:
        vendor, device = gpu_id.split(":")
        card = sysfs / "class/drm/card0/device"
        card.mkdir(parents=True)
        (card / "vendor").write_text(f"0x{vendor}\n")
        (card / "device").write_text(f"0x{device}\n")
    (proc / "cpuinfo").write_text(f"processor\t: 0\nmodel name\t: {cpu}\ncore id\t\t: 0\nflags\t\t: avx2\n\n")
    return sysfs.as_posix(), proc.as_posix()


class Detection(unittest.TestCase):
    def reason(self, env=None, **kw):
        with tempfile.TemporaryDirectory() as t:
            sysfs, proc = machine(t, **kw)
            return gpu.steam_deck_reason(sysfs, proc, env or {})

    def test_dmi_board_or_product_from_valve(self):
        self.assertEqual(self.reason(board="Jupiter"), "dmi board name")
        self.assertEqual(self.reason(board="Galileo"), "dmi board name")
        self.assertEqual(self.reason(product="Jupiter"), "dmi board name")
        self.assertEqual(self.reason(board="Jupiter", board_vendor="Acme"), "")   # another maker's board

    def test_the_apu_when_dmi_says_nothing(self):
        self.assertEqual(self.reason(gpu_id="1002:163f"), "APU graphics 1002:163f")      # LCD, Van Gogh
        self.assertEqual(self.reason(gpu_id="1002:1435"), "APU graphics 1002:1435")      # OLED, Sephiroth
        self.assertEqual(self.reason(cpu=LCD_CPU), "APU " + LCD_CPU)
        self.assertEqual(self.reason(cpu=OLED_CPU), "APU " + OLED_CPU)
        self.assertEqual(self.reason(gpu_id="1002:15bf"), "")                              # a Ryzen 7040 laptop
        self.assertEqual(self.reason(gpu_id="10de:163f"), "")                              # not AMD

    def test_steamdeck_env_is_only_a_hint(self):
        hint = {"SteamDeck": "1"}
        self.assertEqual(self.reason(env=hint), "")                                       # a desktop in Steam's Deck UI
        self.assertEqual(self.reason(env=hint, cpu="AMD Ryzen Z1 Extreme"), "")             # another handheld
        self.assertEqual(self.reason(env=hint, cpu="AMD Custom APU 0123"), "SteamDeck=1 and AMD Custom APU 0123")
        self.assertEqual(self.reason(cpu="AMD Custom APU 0123"), "")                       # no hint, unknown APU

    def test_gpu_specs_and_battery_saver_agree(self):
        with tempfile.TemporaryDirectory() as t:
            sysfs, proc = machine(t, gpu_id="1002:1435", cpu=OLED_CPU)
            self.assertEqual(gpu.detect_linux(sysfs, proc, {}).vendor, gpu.DECK)
            s = specs.detect_linux(sysfs, proc, "", "")
            self.assertEqual((s.gpu_name, s.integrated, s.cpu_name), ("Steam Deck", True, OLED_CPU))
            self.assertTrue(specs.is_deck(s))
            self.assertEqual(specs.cpu_tier(s), specs.LOW)
            self.assertTrue(power.is_deck(sysfs, proc))
        with tempfile.TemporaryDirectory() as t:
            sysfs, proc = machine(t, gpu_id="1002:7590")
            self.assertEqual(gpu.detect_linux(sysfs, proc, {}).vendor, gpu.AMD)
            self.assertFalse(power.is_deck(sysfs, proc))

    def test_windows_on_a_deck(self):
        ps = {"gpus": [{"Name": "AMD Custom GPU 0405", "AdapterCompatibility": "Advanced Micro Devices, Inc.",
                        "PNPDeviceID": "PCI\\VEN_1002&DEV_163F"}],
              "board": "", "cpus": [{"Name": LCD_CPU, "NumberOfCores": 4, "NumberOfLogicalProcessors": 8}],
              "ram": 16 * 1024 ** 3}
        with mock.patch.object(gpu, "powershell_text", return_value=json.dumps(ps)):
            self.assertEqual(gpu.detect_windows().vendor, gpu.DECK)
        self.assertEqual(specs.parse_powershell(json.dumps(ps)).gpu_name, "Steam Deck")
        ps["gpus"][0]["Name"], ps["cpus"][0]["Name"] = "AMD Radeon(TM) Graphics", "AMD Ryzen 5 7640U"
        self.assertEqual(specs.parse_powershell(json.dumps(ps)).gpu_name, "AMD Radeon(TM) Graphics")


class Preset(unittest.TestCase):
    def test_the_deck_preset(self):
        p = gpu.PRESETS[gpu.DECK].settings
        for key, value in {"frame_rate": 30, "resolution_scale": 1, "present_effect": "fsr", "scene_msaa": "off",
                           "world_shadow_detail": "console", "world_texture_detail": "console",
                           "scene_resolution": "720", "present_letterbox": True}.items():
            self.assertEqual(p[key], value, key)
        self.assertEqual(gpu.warnings(gpu.DECK, {"frame_rate": 40}), [])           # its screen at 40 Hz
        self.assertEqual([k for k, _t, _i in gpu.warnings(gpu.DECK, {"frame_rate": 60})], ["frame_rate"])

    def test_offer(self):
        base = {**gpu.DECK_SETTINGS}
        self.assertEqual(specs.deck_offer(DECK, base), {})
        for rate in (20, 40):
            self.assertEqual(specs.deck_offer(DECK, {**base, "frame_rate": rate}), {})
        changed = {"frame_rate": 60, "scene_msaa": "game", "world_shadow_detail": "ultra", "present_letterbox": False}
        self.assertEqual(specs.deck_offer(DECK, {**base, **changed}),
                         {"frame_rate": 30, "scene_msaa": "off", "world_shadow_detail": "console",
                          "present_letterbox": True})
        self.assertEqual(specs.deck_offer(LAPTOP, {**base, **changed}), {})

    def test_warnings_on_a_deck(self):
        def keys(**settings):
            return {w.key: w.fix for w in specs.warnings(DECK, {"frame_rate": 30, "resolution_scale": 1,
                                                                  "scene_msaa": "off", **settings})}
        self.assertEqual(keys(), {})
        self.assertEqual(keys(frame_rate=40), {})
        self.assertEqual(keys(frame_rate=60)["frame_rate"], {"frame_rate": 30})
        self.assertEqual(keys(scene_msaa="game")["scene_msaa"], {"scene_msaa": "off"})
        self.assertEqual(keys(scene_msaa="2x")["scene_msaa"], {"scene_msaa": "off"})
        self.assertNotIn("scene_msaa", {w.key for w in specs.warnings(LAPTOP, {"scene_msaa": "game"})})

    def test_banner_names_the_preset_and_returns_once_for_it(self):
        self.assertIn("Steam Deck", specs.banner_text(DECK))
        self.assertEqual(specs.banner_text(LAPTOP), "Some settings are heavy for this PC.")
        self.assertTrue(DECK.signature().endswith("|" + specs.DECK_OFFER))
        self.assertFalse(LAPTOP.signature().endswith(specs.DECK_OFFER))


class Flow(unittest.TestCase):
    def cfg(self, tmp, content=None):
        path = Path(tmp) / "launcher.json"
        if content is not None:
            path.write_text(json.dumps(content))
        return Config(path)

    def test_a_new_launcher_on_a_deck_gets_the_whole_preset(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected(gpu.DECK, "Steam Deck"))
            c.detect_specs(DECK)
            for key, value in gpu.DECK_SETTINGS.items():
                self.assertEqual(c.settings[key], value, key)
            self.assertFalse(c.specs_banner_due())

    def test_a_new_launcher_whose_card_was_not_recognised_still_gets_it(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected(gpu.AMD, "AMD graphics"))
            c.detect_specs(DECK)
            self.assertEqual((c.settings["frame_rate"], c.settings["scene_msaa"]), (30, "off"))

    def test_existing_settings_get_a_one_time_offer(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"settings": {"frame_rate": 30, "present_effect": "fsr", "scene_msaa": "game",
                                          "world_texture_detail": "high", "anisotropic_override": 5}})
            c.detect_specs(DECK)
            self.assertEqual(c.settings["scene_msaa"], "game")                    # nothing changes by itself
            self.assertTrue(c.specs_banner_due())
            self.assertIn("Steam Deck", c.specs_banner_text())
            changed = c.use_recommended()
            self.assertEqual(sorted(changed), ["scene_msaa", "world_texture_detail"])
            self.assertEqual(c.settings["anisotropic_override"], 5)               # not part of the preset
            self.assertFalse(c.specs_banner_due())

    def test_keep_mine_is_remembered_but_an_older_answer_is_not(self):
        with tempfile.TemporaryDirectory() as t:
            old_answer = specs.Specs.signature(DECK)[:-len("|" + specs.DECK_OFFER)]
            c = self.cfg(t, {"settings": {"scene_msaa": "game"}, "specs": {"dismissed": old_answer}})
            c.detect_specs(DECK)
            self.assertTrue(c.specs_banner_due())   # answered before the Deck had its preset: asked once more
            c.dismiss_specs_banner()
            c.save()
            self.assertFalse(Config(c.path).specs_banner_due())

    def test_40_fps_is_a_deck_choice_not_a_problem(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"settings": {**gpu.DECK_SETTINGS, "frame_rate": 40}})
            c.detect_specs(DECK)
            self.assertFalse(c.specs_banner_due())
            self.assertEqual(c.specs_warnings(), [])


class MatchDisplay(unittest.TestCase):
    def test_a_40_hz_screen_gets_40(self):
        self.assertEqual(display.resolve(0, rates=[40.0]), 40)
        self.assertEqual(display.resolve(0, rates=[39.98]), 40)
        self.assertEqual(display.resolve(0, rates=[45.0]), 30)
        self.assertEqual(display.resolve(0, rates=[50.0]), 30)

    def test_on_a_deck_a_rate_its_screen_divides(self):
        # 39.899, 59.810 and 79.968: what Qt reported inside gamescope (the Deck's compositor) run at 40, 60, 80 Hz
        for hz, want in [(40.0, 40), (60.0, 30), (59.94, 30), (80.0, 40), (90.0, 30), (120.0, 40), (144.0, 40),
                         (45.0, 30), (70.0, 40), (39.899, 40), (59.810, 30), (79.968, 40)]:
            with self.subTest(hz=hz):
                rate = display.resolve(0, DECK, rates=[hz])
                self.assertEqual(rate, want)
                self.assertFalse([w for w in specs.warnings(DECK, {"frame_rate": rate, "scene_msaa": "off"})
                                  if w.key == "frame_rate"])
        lines = []
        self.assertEqual(display.resolve(0, DECK, rates=[], log=lines.append), 30)   # unknown: as at 60 Hz
        self.assertTrue(any("30 fps" in line for line in lines))

    def test_other_pcs_keep_their_caps(self):
        low = Specs(cpu_name="Old", cpu_cores=2, cpu_threads=4, avx2=False, ram_gb=8, gpu_name="X", vram_gb=8)
        self.assertEqual(display.resolve(0, low, rates=[40.0]), 30)
        self.assertEqual(display.resolve(0, low, rates=[120.0]), 30)
        mid = Specs(cpu_name="Mid", cpu_cores=4, cpu_threads=8, avx2=True, ram_gb=16, gpu_name="X", vram_gb=8)
        self.assertEqual(display.resolve(0, mid, rates=[90.0]), 60)


class SettingsPage(unittest.TestCase):
    def test_the_banner_offers_the_deck_preset(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 is not installed")
        from forza_launcher.ui.settings_page import SettingsPage as Page
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as t:
            Path(t, "launcher.json").write_text(json.dumps({"settings": {"frame_rate": 30, "scene_msaa": "game"}}))
            c = Config(Path(t) / "launcher.json")
            c.advanced["data_root"] = t
            c.gpu.update(checked=True, vendor=gpu.DECK)
            c.detect_specs(DECK)
            window = mock.Mock()
            window.preview_text.return_value = "game"
            page = Page(c, window)
            page.show()
            app.processEvents()
            self.assertTrue(page.banner.isVisibleTo(page))
            self.assertIn("Steam Deck", page.banner_text.text())
            self.assertIn("one pass", page.rows["scene_msaa"].warning.text())
            page._use_recommended()
            page._save_timer.stop()
            self.assertEqual(c.settings["scene_msaa"], "off")
            self.assertFalse(page.banner.isVisibleTo(page))
            page.close()


class WithThePad(unittest.TestCase):
    """The Deck's offer with its controls: D-pad down from the Settings tab reaches "Use recommended", A takes it
    (the launcher's controller navigation, #129; a mocked controller as in test_gamepad.py)."""

    def test_use_recommended_with_the_pad(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QApplication, QPushButton
            from forza_launcher.gamepad import State
            from forza_launcher.ui.gamepad import Navigation
            from forza_launcher.ui.window import MainWindow
        except ImportError as e:
            self.skipTest(f"PySide6 or PySDL2 is not installed ({e})")
        app = QApplication.instance() or QApplication([])
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        for target, kw in (("PySide6.QtWidgets.QApplication.applicationState", {"return_value": Qt.ApplicationActive}),
                           ("forza_launcher.ui.pages.SetupPage.refresh", {}),
                           ("forza_launcher.ui.pages.SetupPage.offer_move", {}),
                           ("forza_launcher.paths.config_dir", {"return_value": Path(t.name)})):
            p = mock.patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        Path(t.name, "launcher.json").write_text(json.dumps({"settings": {"frame_rate": 30, "scene_msaa": "game"}}))
        c = Config(Path(t.name) / "launcher.json")
        c.gpu.update(checked=True, vendor=gpu.DECK)
        c.d["advanced"]["data_root"] = t.name
        c.d["check_updates"] = False
        c.detect_specs(DECK)
        win = MainWindow(c)
        self.addCleanup(win.close)
        backend = mock.Mock()
        backend.read.return_value = State(1)
        nav = Navigation(win, backend)
        self.addCleanup(nav.close)
        win.show()
        win.activateWindow()
        app.processEvents()
        self.addCleanup(win.gallery.shutdown)
        win.show_page(2)
        app.processEvents()
        self.assertTrue(win.settings.banner.isVisible())
        use = next(b for b in win.settings.banner.findChildren(QPushButton) if b.text() == "Use recommended")
        nav.focus(win.navs.button(2))
        for _ in range(40):
            nav.dispatch("down", win)
            app.processEvents()
            if app.focusWidget() is use:
                break
        self.assertIs(app.focusWidget(), use)
        nav.dispatch("accept", win)
        app.processEvents()
        win.settings._save_timer.stop()
        self.assertEqual(c.settings["scene_msaa"], "off")
        self.assertFalse(win.settings.banner.isVisible())


if __name__ == "__main__":
    unittest.main()
