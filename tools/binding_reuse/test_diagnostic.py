"""Current-main relative-clock reports must never become scored CPU/FPS results."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_vulkan_binding_reuse import diagnostic_summary


class DiagnosticTests(unittest.TestCase):
    def test_current_main_report_is_correctness_only(self):
        # Current benchmark.cpp's report has no wall-clock epoch. Keep raw FPS
        # available in the private input, but never return it as a scored result.
        report = {"valid": True, "reason": "completed", "source": "guest_vdswap",
                  "warmup_seconds": 15, "frames": 812, "seconds": 59.9866,
                  "average_fps": 13.5363, "low_1_percent_fps": 9.47807,
                  "p99_ms": 97.636, "worst_ms": 124.42,
                  "stalls_over_50ms": 812, "stalls_over_100ms": 6}
        audit = [{"focused": False, "cpu_affinity": [0, 2, 4, 6]}] * 3
        result = diagnostic_summary("candidate-warmup", audit, report)
        self.assertFalse(result["qualified_for_performance"])
        self.assertTrue(result["ai_race_completed"])
        self.assertEqual(result["focused_fraction"], 0)
        self.assertEqual(result["audited_cpu_affinities"], [[0, 2, 4, 6]])
        self.assertTrue(set(result).isdisjoint({"average_fps", "guest", "present",
                                              "cpu_ms_per_guest_frame", "ai_race"}))

    def test_incomplete_race_cannot_qualify_as_correctness_run(self):
        for report in (None, {"valid": False, "reason": "stalled"},
                       {"valid": True, "reason": "started"}):
            with self.assertRaisesRegex(RuntimeError, "completed valid AI race"):
                diagnostic_summary("candidate-warmup", [], report)

    def test_explicit_foreground_requirement_still_rejects_background_diagnostic(self):
        for audit in ([{"focused": False}], []):
            with self.assertRaisesRegex(RuntimeError, "not entirely in the foreground"):
                diagnostic_summary("candidate-warmup", audit,
                                   {"valid": True, "reason": "completed"}, True)

    def test_verified_foreground_diagnostic_is_still_unqualified_for_performance(self):
        result = diagnostic_summary("candidate-warmup", [{"focused": True}],
                                    {"valid": True, "reason": "completed"}, True)
        self.assertEqual(result["focused_fraction"], 1)
        self.assertFalse(result["qualified_for_performance"])


if __name__ == "__main__":
    unittest.main()
