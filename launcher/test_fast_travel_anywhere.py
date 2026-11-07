"""The Gameplay "Fast Travel Anywhere" setting (#424) reaches the game's forza.toml and crash reports."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, crash_report, schema


class FastTravelAnywhereTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def test_on_by_default_and_not_written(self):
        setting = schema.BY_KEY["fast_travel_anywhere"]
        self.assertEqual((setting.kind, setting.default), ("bool", True))
        self.assertEqual((setting.label, setting.group, setting.section),
                         ("Fast Travel Anywhere", "Gameplay", "Map"))
        cfg = self.config()
        self.assertIs(cfg.settings["fast_travel_anywhere"], True)
        self.assertNotIn("fast_travel_anywhere", config.render_toml(cfg))

    def test_off_is_written_for_the_game(self):
        cfg = self.config()
        cfg.settings["fast_travel_anywhere"] = False
        self.assertIn("fast_travel_anywhere = false", config.render_toml(cfg).splitlines())

    def test_the_game_defines_the_cvar_with_the_same_default(self):
        root = Path(__file__).resolve().parents[1] / "forza-recomp/src"
        hook = (root / "hooks/fast_travel_anywhere.cpp").read_text()
        self.assertIn('REXCVAR_DEFINE_BOOL(fast_travel_anywhere, true, "Gameplay"', hook)

    def test_says_it_gives_no_tokens(self):
        help_text = schema.BY_KEY["fast_travel_anywhere"].help
        self.assertIn("Gives no Tokens", help_text)
        self.assertIn("Treasure Map", help_text)

    def test_crash_reports_name_it(self):
        self.assertIn("fast_travel_anywhere", crash_report.SETTING_KEYS)


if __name__ == "__main__":
    unittest.main()
