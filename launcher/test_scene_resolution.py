from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, crash_report, schema
from forza_launcher.config import Config


class SceneResolutionTests(unittest.TestCase):
    """Settings > Graphics > Picture > 3D scene resolution (forza-recomp/src/hooks/scene_resolution.cpp)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root)

    def setting(self):
        return next(s for s in schema.S if s.key == "scene_resolution")

    def test_option_defaults_to_the_game_and_offers_540(self):
        s = self.setting()
        self.assertEqual((s.label, s.group, s.section), ("3D scene resolution", "Display", "Picture"))
        self.assertEqual(s.default, "720")
        self.assertEqual([v for v, _ in s.choices], ["720", "540"])
        self.assertTrue(s.restart)
        self.assertIn("scene_resolution", crash_report.SETTING_KEYS)

    def test_it_sits_next_to_render_scale(self):
        keys = [s.key for s in schema.S]
        self.assertEqual(keys.index("scene_resolution"), keys.index("resolution_scale") + 1)

    def test_720_writes_nothing_and_lower_values_reach_forza_toml(self):
        self.assertNotIn("scene_resolution", config.render_toml(self.cfg))
        self.cfg.settings.update(scene_resolution="540")
        self.assertEqual(config.parse_toml(config.render_toml(self.cfg))["scene_resolution"], "540")

    def test_unknown_values_fall_back_to_720(self):
        for bad in ("360", "480", "1080", "540p", None):
            with self.subTest(bad=bad):
                self.assertEqual(schema.coerce(self.setting(), bad), "720")


if __name__ == "__main__":
    unittest.main()
