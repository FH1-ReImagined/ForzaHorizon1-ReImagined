"""The radio "Station changes" setting (#401) reaches the game's forza.toml."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, schema


class RadioStationSwitchTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def test_game_by_default_and_not_written(self):
        setting = schema.BY_KEY["radio_station_switch"]
        self.assertEqual((setting.kind, setting.default), ("choice", "game"))
        self.assertEqual((setting.group, setting.section), ("Audio", "Radio"))
        self.assertEqual([value for value, _ in setting.choices], ["game", "fast", "instant"])
        cfg = self.config()
        self.assertEqual(cfg.settings["radio_station_switch"], "game")
        self.assertNotIn("radio_station_switch", config.render_toml(cfg))

    def test_fast_is_written_for_the_game(self):
        cfg = self.config()
        cfg.settings["radio_station_switch"] = "fast"
        self.assertIn('radio_station_switch = "fast"', config.render_toml(cfg).splitlines())

    def test_instant_is_written_for_the_game(self):
        cfg = self.config()
        cfg.settings["radio_station_switch"] = "instant"
        self.assertIn('radio_station_switch = "instant"', config.render_toml(cfg).splitlines())

    def test_the_game_defines_the_cvar_with_the_same_default(self):
        hook = Path(__file__).resolve().parents[1] / "forza-recomp/src/hooks/radio_station.cpp"
        self.assertIn('REXCVAR_DEFINE_STRING(radio_station_switch, "game", "Audio"', hook.read_text())

    def test_shown_on_every_platform(self):
        self.assertTrue(schema.BY_KEY["radio_station_switch"].applies())


if __name__ == "__main__":
    unittest.main()
