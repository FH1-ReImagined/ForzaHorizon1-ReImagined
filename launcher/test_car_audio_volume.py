"""The Audio > Car sounds settings (engine and turbo/supercharger volume, #730) reach the
game's forza.toml and match the cvars the game defines."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, schema
from forza_launcher.ui import settings_page

KEYS = ("car_engine_volume", "car_turbo_volume")
HOOK = Path(__file__).resolve().parents[1] / "forza-recomp/src/hooks/car_audio_volume.cpp"


class CarAudioVolumeTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def test_percent_rows_in_audio_car_sounds(self):
        for key in KEYS:
            s = schema.BY_KEY[key]
            self.assertEqual((s.kind, s.default, s.lo, s.hi), ("int", 100, 0, 200), key)
            self.assertEqual((s.group, s.section), ("Audio", "Car sounds"), key)
            self.assertTrue(s.applies(), key)
            self.assertEqual(settings_page.tab_of(s), "Audio", key)

    def test_car_sounds_follow_output_on_the_audio_tab(self):
        sections = [section for section, _ in settings_page.tab_settings("Audio")]
        self.assertLess(sections.index("Output"), sections.index("Car sounds"))
        self.assertLess(sections.index("Car sounds"), sections.index("Radio"))
        rows = dict(settings_page.tab_settings("Audio"))["Car sounds"]
        self.assertEqual([s.key for s in rows], list(KEYS))

    def test_default_is_not_written(self):
        cfg = self.config()
        text = config.render_toml(cfg)
        for key in KEYS:
            self.assertEqual(cfg.settings[key], 100)
            self.assertNotIn(key, text)

    def test_a_change_is_written_for_the_game(self):
        cfg = self.config()
        cfg.settings["car_engine_volume"] = 60
        cfg.settings["car_turbo_volume"] = 150
        lines = config.render_toml(cfg).splitlines()
        self.assertIn("car_engine_volume = 60", lines)
        self.assertIn("car_turbo_volume = 150", lines)

    def test_the_game_defines_the_cvars_with_the_same_default(self):
        text = HOOK.read_text()
        for key in KEYS:
            self.assertIn(f'REXCVAR_DEFINE_INT32({key}, 100, "Audio"', text)


if __name__ == "__main__":
    unittest.main()
