"""Cockpit camera config reaches the runtime without changing stock defaults."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import schema
from forza_launcher.config import Config, render_toml


class CockpitFovTests(unittest.TestCase):
    def test_schema_range_and_units(self):
        setting = schema.BY_KEY["forza_cockpit_fov"]
        self.assertEqual(setting.default, 0)
        self.assertIn("vertical degrees", setting.label)
        self.assertEqual([value for value, _ in setting.choices], [0] + list(range(40, 91)))
        for value in (0, 40, 62, 90):
            self.assertEqual(schema.coerce(setting, str(value)), value)
        for value in (-1, 1, 39, 91, 62.5, "nan", "inf"):
            self.assertEqual(schema.coerce(setting, value), 0)

    def test_toml_restart_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(Path(directory) / "launcher.json")
            cfg.advanced["data_root"] = directory
            cfg.settings["frame_rate"] = 30  # Avoid Match display's hardware query.
            self.assertNotIn("forza_cockpit_fov =", render_toml(cfg))
            cfg.settings["forza_cockpit_fov"] = 75
            cfg.save()
            restarted = Config(cfg.path)
            self.assertIn("forza_cockpit_fov = 75\n", render_toml(restarted))
            restarted.settings["forza_cockpit_fov"] = 0
            restarted.save()
            self.assertNotIn("forza_cockpit_fov =", render_toml(Config(cfg.path)))

    def test_per_camera_bases_are_independent_and_keep_stock_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Config(Path(directory) / "launcher.json")
            cfg.settings["frame_rate"] = 30
            for key, _, _ in schema.CAMERA_FOVS:
                self.assertEqual(cfg.settings[key], 0)
                self.assertIn("base FOV", schema.BY_KEY[key].label)
                self.assertIn("speed widening", schema.BY_KEY[key].help)
            cfg.settings["forza_chase_near_fov"] = 80
            cfg.settings["forza_hood_fov"] = 70
            cfg.save()
            restored = Config(cfg.path)
            self.assertEqual(restored.settings["forza_chase_near_fov"], 80)
            self.assertEqual(restored.settings["forza_hood_fov"], 70)
            for key in ("forza_chase_far_fov", "forza_cockpit_fov", "forza_bumper_fov"):
                self.assertEqual(restored.settings[key], 0)
                self.assertNotIn(key + " =", render_toml(restored))

    def test_existing_cockpit_values_remain_degrees_not_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher.json"
            path.write_text(json.dumps({"settings": {"forza_cockpit_fov": 90, "frame_rate": 30}}))
            cfg = Config(path)
            self.assertEqual(cfg.settings["forza_cockpit_fov"], 90)
            self.assertIn("forza_cockpit_fov = 90\n", render_toml(cfg))
            self.assertEqual(cfg.settings["forza_chase_near_fov"], 0)


if __name__ == "__main__":
    unittest.main()
