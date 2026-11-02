"""The racing wheel's force feedback strengths (#256) and rumble style (#273) match the SDK's cvars and
sit with the others."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import schema
from forza_launcher.config import Config, import_toml, render_toml

PATCHES = Path(__file__).resolve().parent.parent / "patches" / "rexglue-sdk"
PATCH = PATCHES / "0101-wheel-game-force-feedback.patch"
RUMBLE_PATCH = PATCHES / "0129-wheel-fine-rumble.patch"  # softer rumble defaults, the style
KEYS = ("wheel_ffb_spring_strength", "wheel_rumble_engine_strength", "wheel_rumble_road_strength")


def added(patch):
    return "\n".join(line[1:] for line in patch.read_text().splitlines() if line.startswith("+"))


def sdk_int_cvars():
    """name -> (default, low, high) of the integer cvars patch 0101 adds, with the defaults patch
    0129 changes."""
    found = {}
    for m in re.finditer(r"REXCVAR_DEFINE_INT32\((\w+), (\d+),(.*?);", added(PATCH), re.S):
        name, value, rest = m.groups()
        bounds = re.search(r"\.range\((\d+), (\d+)\)", rest)
        found[name] = (int(value), int(bounds.group(1)), int(bounds.group(2)))
    for m in re.finditer(r"REXCVAR_DEFINE_INT32\((\w+), (\d+),", added(RUMBLE_PATCH)):
        name, value = m.groups()
        found[name] = (int(value),) + found[name][1:]
    return found


class WheelForceFeedbackSettings(unittest.TestCase):
    def config(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return Config(Path(folder.name) / "launcher.json")

    def test_settings_match_the_sdk_cvars(self):
        cvars = sdk_int_cvars()
        for key in KEYS:
            with self.subTest(key=key):
                self.assertIn(key, cvars)
                setting = schema.BY_KEY[key]
                default, lo, hi = cvars[key]
                self.assertEqual((setting.default, setting.lo, setting.hi), (default, lo, hi))
        # The centring as before; the engine and road rumble softer (#273).
        self.assertEqual([cvars[k][0] for k in KEYS], [100, 50, 80])

    def test_rumble_style_matches_the_sdk(self):
        m = re.search(r'REXCVAR_DEFINE_STRING\(wheel_rumble_style, "(\w+)",.*?\.allowed\(\{([^}]*)\}\)',
                      added(RUMBLE_PATCH), re.S)
        setting = schema.BY_KEY["wheel_rumble_style"]
        self.assertEqual(setting.default, m.group(1))
        self.assertEqual(setting.default, "fine")
        self.assertEqual(sorted(v for v, _ in setting.choices), sorted(re.findall(r'"(\w+)"', m.group(2))))
        self.assertTrue(setting.restart)
        self.assertEqual(setting.section, "Racing wheel")

    def test_strengths_follow_the_overall_strength(self):
        keys = [s.key for s in schema.for_group("Controls")]
        # The minimum force (#303) comes right after the overall strength.
        self.assertEqual(keys[keys.index("wheel_ffb_strength") + 1], "wheel_ffb_min_strength")
        start = keys.index("wheel_ffb_min_strength") + 1
        self.assertEqual(tuple(keys[start:start + len(KEYS)]), KEYS)
        self.assertEqual({schema.BY_KEY[k].section for k in KEYS}, {"Racing wheel"})

    def test_defaults_stay_out_of_the_game_configuration_and_values_roundtrip(self):
        cfg = self.config()
        text = render_toml(cfg)
        for key in KEYS:
            self.assertNotIn(key, text)
        expected = dict(wheel_ffb_spring_strength=40, wheel_rumble_engine_strength=25,
                        wheel_rumble_road_strength=90)
        cfg.settings.update(expected)
        text = render_toml(cfg)
        imported = self.config()
        import_toml(imported, text)
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertIn(f"{key} = {value}", text)
                self.assertEqual(imported.settings[key], value)
        self.assertEqual(schema.coerce(schema.BY_KEY["wheel_ffb_spring_strength"], 150), 100)
        self.assertEqual(schema.coerce(schema.BY_KEY["wheel_rumble_road_strength"], -5), 0)


class WheelMinimumForce(unittest.TestCase):
    """#303 (patch 0131): the minimum steering force, a percentage after the overall strength,
    off (0) by default."""
    def test_setting_matches_the_sdk_cvar(self):
        text = added(PATCHES / "0131-wheel-minimum-force.patch")
        m = re.search(r"REXCVAR_DEFINE_INT32\(wheel_ffb_min_strength, (\d+),.*?\.range\((\d+), (\d+)\)",
                      text, re.S)
        self.assertIsNotNone(m)
        setting = schema.BY_KEY["wheel_ffb_min_strength"]
        self.assertEqual((setting.default, setting.lo, setting.hi),
                         tuple(int(v) for v in m.groups()))
        self.assertEqual(setting.default, 0)
        self.assertEqual((setting.kind, setting.section), ("int", "Racing wheel"))

    def test_roundtrip(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cfg = Config(Path(folder.name) / "launcher.json")
        self.assertNotIn("wheel_ffb_min_strength", render_toml(cfg))
        cfg.settings["wheel_ffb_min_strength"] = 20
        text = render_toml(cfg)
        self.assertIn("wheel_ffb_min_strength = 20", text)
        imported = Config(Path(folder.name) / "other.json")
        import_toml(imported, text)
        self.assertEqual(imported.settings["wheel_ffb_min_strength"], 20)
        self.assertEqual(schema.coerce(schema.BY_KEY["wheel_ffb_min_strength"], 150), 100)


class WheelVibrationRate(unittest.TestCase):
    """#467 (ReIGlue 0403 follow-up): the vibration's updates per second, 60 by default, 15 to 120,
    next to the other rumble settings."""
    def test_setting(self):
        setting = schema.BY_KEY["wheel_vibration_rate"]
        self.assertEqual((setting.kind, setting.default, setting.lo, setting.hi), ("int", 60, 15, 120))
        self.assertEqual((setting.group, setting.section), ("Controls", "Racing wheel"))
        self.assertEqual(setting.label, "Wheel vibration updates per second")
        self.assertIn("Lower it if your wheel's vibration feels too strong.", setting.help)
        self.assertFalse(setting.restart)  # read on every update
        keys = [s.key for s in schema.for_group("Controls")]
        # After the rumble strengths, before the rumble style.
        self.assertEqual(keys[keys.index("wheel_rumble_road_strength") + 1], "wheel_vibration_rate")
        self.assertEqual(keys[keys.index("wheel_vibration_rate") + 1], "wheel_rumble_style")

    def test_roundtrip_and_clamp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cfg = Config(Path(folder.name) / "launcher.json")
        self.assertNotIn("wheel_vibration_rate", render_toml(cfg))  # the default stays out
        cfg.settings["wheel_vibration_rate"] = 30
        text = render_toml(cfg)
        self.assertIn("wheel_vibration_rate = 30", text)
        imported = Config(Path(folder.name) / "other.json")
        import_toml(imported, text)
        self.assertEqual(imported.settings["wheel_vibration_rate"], 30)
        setting = schema.BY_KEY["wheel_vibration_rate"]
        self.assertEqual(schema.coerce(setting, 500), 120)
        self.assertEqual(schema.coerce(setting, 1), 15)


if __name__ == "__main__":
    unittest.main()
