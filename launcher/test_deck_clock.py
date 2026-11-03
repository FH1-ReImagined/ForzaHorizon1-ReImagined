"""Synthetic read-only sysfs clock checks; never change clocks."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import deck_clock


class DeckClock(unittest.TestCase):
    def test_manual_auto_unknown_and_idle_samples(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(deck_clock.gpu, "steam_deck_reason", return_value="APU"):
            device = Path(temp) / "class/drm/card1/device"
            device.mkdir(parents=True)
            (device / "vendor").write_text("0x1002")
            (device / "device").write_text("0x1435")
            (device / "pp_dpm_sclk").write_text("0: 200Mhz *\n1: 1600Mhz\n")
            for mode in ("manual", "auto", "", "unknown"):
                (device / "power_dpm_force_performance_level").write_text(mode)
                before = {p.name: p.read_bytes() for p in device.iterdir()}
                result = deck_clock.check(temp, temp)
                self.assertEqual(result.observed_mhz, 200)
                self.assertEqual(bool(result.warning()), mode == "manual")
                self.assertNotIn("200", result.warning())  # idle reading is not a cap
                self.assertEqual(before, {p.name: p.read_bytes() for p in device.iterdir()})
            (device / "device").write_text("0x744c")  # external AMD GPU, not the APU
            self.assertEqual(deck_clock.check(temp, temp), deck_clock.Clock())

    def test_non_deck_or_unreadable_is_no_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(deck_clock.gpu, "steam_deck_reason", return_value=""):
                self.assertEqual(deck_clock.check(temp, temp), deck_clock.Clock())
            with patch.object(deck_clock.gpu, "steam_deck_reason", return_value="APU"):
                self.assertEqual(deck_clock.check(temp, temp), deck_clock.Clock())


if __name__ == "__main__":
    unittest.main()
