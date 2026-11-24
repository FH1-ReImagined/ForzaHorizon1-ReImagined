"""Advanced > Log > Detailed GPU reset report: ReIGlue 1100's gpu_device_lost_setup_breadcrumbs
(#913), off as in the SDK, written to forza.toml only when a player turns it on."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import schema
from forza_launcher.config import Config, render_toml


class DeviceLostSetupBreadcrumbs(unittest.TestCase):
    def test_setting(self):
        s = schema.BY_KEY["gpu_device_lost_setup_breadcrumbs"]
        self.assertEqual((s.label, s.group, s.section, s.kind), ("Detailed GPU reset report", "Advanced", "Log", "bool"))
        self.assertIs(s.default, False)
        self.assertTrue(s.restart)
        self.assertEqual(s.platform, "")

    def test_written_only_when_on(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = Config(Path(folder) / "launcher.json")
            self.assertNotIn("gpu_device_lost_setup_breadcrumbs", render_toml(cfg))
            cfg.settings["gpu_device_lost_setup_breadcrumbs"] = True
            self.assertIn("gpu_device_lost_setup_breadcrumbs = true", render_toml(cfg))


if __name__ == "__main__":
    unittest.main()
