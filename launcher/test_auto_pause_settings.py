"""Auto-pause stays opt-in and both options reach the native cvars."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher.config import Config, import_toml, render_toml

KEYS = ("forza_pause_on_focus_loss", "forza_pause_on_controller_disconnect")


class AutoPauseSettingsTests(unittest.TestCase):
    def test_defaults_and_independent_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "launcher.json"
            cfg = Config(path)
            for key in KEYS:
                self.assertFalse(cfg.settings[key])
                self.assertNotIn(key, render_toml(cfg))
            for enabled in KEYS:
                for key in KEYS:
                    cfg.settings[key] = key == enabled
                cfg.save()
                loaded = Config(path)
                text = render_toml(loaded)
                self.assertIn(enabled + " = true", text)
                imported = Config(Path(root) / "imported.json")
                import_toml(imported, text)
                for key in KEYS:
                    self.assertEqual(loaded.settings[key], key == enabled)
                    self.assertEqual(imported.settings[key], key == enabled)
