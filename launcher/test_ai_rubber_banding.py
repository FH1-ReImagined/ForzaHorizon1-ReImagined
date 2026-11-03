"""The Gameplay "Rubber banding" setting (#433) reaches the game's forza.toml and crash reports."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, crash_report, schema


class AiRubberBandingTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def test_game_by_default_and_not_written(self):
        setting = schema.BY_KEY["ai_rubber_banding"]
        self.assertEqual((setting.kind, setting.default), ("choice", "game"))
        self.assertEqual((setting.group, setting.section), ("Gameplay", "CPU drivers"))
        self.assertEqual([value for value, _ in setting.choices], ["game", "off"])
        cfg = self.config()
        self.assertEqual(cfg.settings["ai_rubber_banding"], "game")
        self.assertNotIn("ai_rubber_banding", config.render_toml(cfg))

    def test_off_is_written_for_the_game(self):
        cfg = self.config()
        cfg.settings["ai_rubber_banding"] = "off"
        self.assertIn('ai_rubber_banding = "off"', config.render_toml(cfg).splitlines())

    def test_the_game_defines_the_cvar_with_the_same_default(self):
        root = Path(__file__).resolve().parents[1] / "forza-recomp/src"
        hook = (root / "hooks/ai_catchup.cpp").read_text()
        self.assertIn('REXCVAR_DEFINE_STRING(ai_rubber_banding, "game", "Gameplay"', hook)
        # The game turns it off only for the launcher's own spelling.
        self.assertIn('value == "off"', (root / "platform/ai_catchup.h").read_text())

    def test_has_its_own_tab_and_says_online_races_are_unchanged(self):
        self.assertIn("Gameplay", schema.GROUPS)
        self.assertTrue(schema.BY_KEY["ai_rubber_banding"].applies())
        self.assertIn("online races", schema.BY_KEY["ai_rubber_banding"].help)

    def test_crash_reports_name_it(self):
        self.assertIn("ai_rubber_banding", crash_report.SETTING_KEYS)


if __name__ == "__main__":
    unittest.main()
