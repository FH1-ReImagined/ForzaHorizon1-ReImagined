from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, crash_report, ingame, schema
from forza_launcher.config import Config


class MotionBlurTests(unittest.TestCase):
    """Settings > Graphics > Picture > Motion blur (#588)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root)

    def setting(self):
        return next(s for s in schema.S if s.key == "motion_blur")

    def test_motion_blur_setting_metadata(self):
        s = self.setting()
        self.assertEqual((s.label, s.group, s.section), ("Motion blur", "Display", "Picture"))
        self.assertIs(s.default, True)
        self.assertEqual(s.kind, "bool")
        self.assertFalse(s.restart)
        self.assertIn("motion_blur", crash_report.SETTING_KEYS)

    def test_default_writes_nothing_to_toml_and_false_renders_explicitly(self):
        text = config.render_toml(self.cfg)
        self.assertNotIn("motion_blur", text)

        self.cfg.settings["motion_blur"] = False
        rendered = config.render_toml(self.cfg)
        self.assertIn("motion_blur = false", rendered.splitlines())
        values = config.parse_toml(rendered)
        self.assertIs(values["motion_blur"], False)

    def test_coercion_handles_various_types(self):
        s = self.setting()
        self.assertIs(schema.coerce(s, True), True)
        self.assertIs(schema.coerce(s, 1), True)
        self.assertIs(schema.coerce(s, False), False)
        self.assertIs(schema.coerce(s, 0), False)

    def test_ingame_import_handles_motion_blur_choices(self):
        for raw, expected in (("false", False), ("true", True)):
            with self.subTest(raw=raw):
                req = {"version": 1, "settings": {"motion_blur": raw}}
                changes = ingame.translate(req, self.cfg.settings)
                self.assertIs(changes["motion_blur"], expected)

    def test_ingame_import_rejects_invalid_values(self):
        for bad in ("off", "on", "2", 123, None):
            with self.subTest(bad=bad):
                req = {"version": 1, "settings": {"motion_blur": bad}}
                with self.assertRaises(ValueError):
                    ingame.translate(req, self.cfg.settings)


if __name__ == "__main__":
    unittest.main()
