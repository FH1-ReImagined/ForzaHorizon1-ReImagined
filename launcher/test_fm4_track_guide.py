"""The Gameplay "Lap guide on circuits" setting (#877) reaches the game's forza.toml."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, schema


class LapGuideSettingTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def test_off_by_default_and_not_written(self):
        setting = schema.BY_KEY["fm4_track_guide"]
        self.assertEqual((setting.kind, setting.default), ("bool", False))
        self.assertEqual((setting.label, setting.group, setting.section),
                         ("Lap guide on circuits", "Gameplay", "Map"))
        self.assertTrue(setting.restart)
        cfg = self.config()
        self.assertIs(cfg.settings["fm4_track_guide"], False)
        self.assertNotIn("fm4_track_guide", config.render_toml(cfg))

    def test_on_is_written_for_the_game(self):
        cfg = self.config()
        cfg.settings["fm4_track_guide"] = True
        self.assertIn("fm4_track_guide = true", config.render_toml(cfg).splitlines())

    def test_the_game_defines_the_cvar_with_the_same_default(self):
        root = Path(__file__).resolve().parents[1] / "forza-recomp/src"
        hook = (root / "hooks/fm4_lap_timer.cpp").read_text()
        self.assertIn('REXCVAR_DEFINE_BOOL(fm4_track_guide, false, "Mods"', hook)

    def test_says_what_it_costs(self):
        help_text = schema.BY_KEY["fm4_track_guide"].help
        self.assertIn("no Set Route", help_text)
        self.assertIn("not of the lap", help_text)


if __name__ == "__main__":
    unittest.main()
