"""The Gameplay "Skip startup videos" setting reaches the game's forza.toml and crash reports."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, crash_report, schema


class SkipStartupMoviesTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def test_off_by_default_and_not_written(self):
        setting = schema.BY_KEY["skip_startup_movies"]
        self.assertEqual((setting.kind, setting.default), ("bool", False))
        self.assertEqual((setting.label, setting.group, setting.section),
                         ("Skip startup videos", "Gameplay", "Startup"))
        self.assertTrue(setting.restart)
        cfg = self.config()
        self.assertIs(cfg.settings["skip_startup_movies"], False)
        self.assertNotIn("skip_startup_movies", config.render_toml(cfg))

    def test_on_is_written_for_the_game(self):
        cfg = self.config()
        cfg.settings["skip_startup_movies"] = True
        self.assertIn("skip_startup_movies = true", config.render_toml(cfg).splitlines())

    def test_the_game_defines_the_cvar_with_the_same_default(self):
        root = Path(__file__).resolve().parents[1] / "forza-recomp/src"
        loader = (root / "kernel/xam_loader.cpp").read_text()
        self.assertIn('REXCVAR_DEFINE_BOOL(skip_startup_movies, false, "Gameplay"', loader)

    def test_crash_reports_name_it(self):
        self.assertIn("skip_startup_movies", crash_report.SETTING_KEYS)


if __name__ == "__main__":
    unittest.main()
