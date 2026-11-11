"""The in-game Graphics menu offers the launcher's frame rates (#783)."""
from pathlib import Path
import json
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import ingame, schema
from forza_launcher.config import Config

HEADER = Path(__file__).resolve().parents[1] / "forza-recomp/src/platform/frame_rate_choices.h"
MENU = Path(__file__).resolve().parents[1] / "forza-recomp/src/hooks/graphics_menu.cpp"


class FrameRatePresetTests(unittest.TestCase):
    def test_game_menu_lists_the_launcher_rates(self):
        found = re.search(r"kFrameRatePresets\{([^}]*)\}", HEADER.read_text(encoding="utf-8"))
        self.assertIsNotNone(found)
        game = tuple(int(v) for v in re.findall(r"\d+", found.group(1)))
        self.assertEqual(game, schema.FRAME_RATE_PRESETS)
        self.assertIn(75, game)

    def test_game_menu_builds_its_list_from_the_shared_rates(self):
        menu = MENU.read_text(encoding="utf-8")
        self.assertIn("FrameRateChoices(", menu)
        self.assertNotIn('{"90", "90 FPS"}', menu)    # no second hand-written list

    def test_every_preset_is_a_rate_the_launcher_keeps(self):
        setting = schema.BY_KEY["frame_rate"]
        self.assertEqual(list(schema.FRAME_RATE_PRESETS), sorted(set(schema.FRAME_RATE_PRESETS)))
        for rate in schema.FRAME_RATE_PRESETS:
            with self.subTest(rate=rate):
                self.assertEqual(schema.coerce(setting, rate), rate)

    def test_in_game_saves_of_every_preset_are_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(Path(tmp) / "launcher.json")
            cfg.advanced["data_root"] = tmp
            for rate in (*schema.FRAME_RATE_PRESETS, 72):
                with self.subTest(rate=rate):
                    (Path(tmp) / ingame.FILENAME).write_text(json.dumps(
                        {"version": 1, "settings": {"frame_rate": str(rate)}}), encoding="utf-8")
                    self.assertTrue(ingame.import_pending(cfg, lambda _: None))
                    self.assertEqual(Config(cfg.path).settings["frame_rate"], rate)


if __name__ == "__main__":
    unittest.main()
