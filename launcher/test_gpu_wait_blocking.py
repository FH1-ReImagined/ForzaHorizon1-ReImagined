"""The gpu_wait_blocking setting: the game's default (src/hooks/gpu_wait.cpp, on everywhere since #640),
the launcher's Windows-only switch, which writes forza.toml only when turned off, and the one-time
migration of the off that launchers up to 0.2.6 saved as Windows' default."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import schema
from forza_launcher.config import Config, render_toml

HOOK = Path(__file__).resolve().parent.parent / "forza-recomp" / "src" / "hooks" / "gpu_wait.cpp"


def game_default():
    """The hook's default."""
    m = re.search(r"REXCVAR_DEFINE_BOOL\(gpu_wait_blocking, (\w+),", HOOK.read_text())
    assert m, "default not found in gpu_wait.cpp"
    return m.group(1) == "true"


class GpuWaitBlocking(unittest.TestCase):
    def test_game_default_is_on_everywhere(self):
        self.assertTrue(game_default())
        self.assertNotIn("FORZA_GPU_WAIT_BLOCKING_DEFAULT", HOOK.read_text())

    def test_launcher_switch_is_windows_only_and_matches_the_default(self):
        s = schema.BY_KEY["gpu_wait_blocking"]
        self.assertEqual((s.group, s.kind, s.platform), ("Advanced", "bool", "windows"))
        self.assertEqual(s.default, game_default())
        self.assertTrue(s.restart)

    def test_written_only_when_turned_off_on_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = Config(Path(folder) / "launcher.json")
            with patch("forza_launcher.schema.IS_WINDOWS", True):
                self.assertNotIn("gpu_wait_blocking", render_toml(cfg))
                cfg.settings["gpu_wait_blocking"] = False
                self.assertIn("gpu_wait_blocking = false", render_toml(cfg))
            with patch("forza_launcher.schema.IS_WINDOWS", False):
                self.assertNotIn("gpu_wait_blocking", render_toml(cfg))  # Linux: the game's default

    def test_the_old_default_off_is_turned_on_once(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "launcher.json"
            # a launcher.json of 0.2.6: every setting saved, this one at its old default
            path.write_text(json.dumps({"settings": {"gpu_wait_blocking": False, "frame_rate": 30},
                                        "migrated": {"discord_application_id": True}}))
            cfg = Config(path)
            self.assertTrue(cfg.settings["gpu_wait_blocking"])
            self.assertEqual(cfg.settings["frame_rate"], 30)
            # the player turns it off again: that stays
            cfg.settings["gpu_wait_blocking"] = False
            cfg.save()
            self.assertFalse(Config(path).settings["gpu_wait_blocking"])

    def test_a_new_launcher_json_needs_no_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = Config(Path(folder) / "launcher.json")
            self.assertTrue(cfg.settings["gpu_wait_blocking"])
            self.assertTrue(cfg.d["migrated"]["gpu_wait_blocking"])


if __name__ == "__main__":
    unittest.main()
