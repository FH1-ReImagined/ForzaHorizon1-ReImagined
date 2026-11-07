"""Align current-main relative CSV events with a separately emitted clock epoch."""
from pathlib import Path
import csv
import io
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_vulkan_binding_reuse import select_clock_samples


class FrameClockTests(unittest.TestCase):
    def test_current_csv_and_marker_select_only_cpu_window(self):
        rows = list(csv.DictReader(io.StringIO(
            "scene,src,t,ms\n"
            "candidate,guest,99.9999,16.0\n"
            "candidate,present,100.0000,15.0\n"
            "candidate,guest,115.0000,16.0\n"
            "candidate,present,130.0000,15.0\n"
            "candidate,guest,130.0001,16.0\n")))
        epoch = "1792880000.125000000\n"
        begin = (1792880100.125, {}, 1000.0)
        end = (1792880130.125, {}, 1030.0)
        selected, duration = select_clock_samples(rows, epoch, begin, end)
        self.assertEqual([row["t"] for row in selected],
                         ["100.0000", "115.0000", "130.0000"])
        self.assertEqual(duration, 30)

    def test_wall_clock_jump_is_not_used_for_alignment(self):
        with self.assertRaisesRegex(RuntimeError, "Wall clock changed"):
            select_clock_samples([], "1000", (1001, {}, 10), (1032, {}, 40))

    def test_invalid_epoch_cannot_qualify_samples(self):
        for marker in ("nan", "inf", "-inf"):
            with self.assertRaisesRegex(RuntimeError, "must be finite"):
                select_clock_samples([], marker, (1001, {}, 10), (1031, {}, 40))


if __name__ == "__main__":
    unittest.main()
