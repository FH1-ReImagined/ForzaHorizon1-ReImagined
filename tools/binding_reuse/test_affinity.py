"""CPU-only regression fixtures for constrained benchmark runs (requires psutil)."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench_vulkan_binding_reuse import audit_affinity


class ProcessFixture:
    def __init__(self, mask):
        self.mask = list(mask)
        self.writes = []

    def cpu_affinity(self, mask=None):
        if mask is not None:
            self.writes.append(list(mask))
            self.mask = list(mask)
        return list(self.mask)


class AffinityTests(unittest.TestCase):
    def test_startup_sdk_reset_is_corrected_before_lock(self):
        selected = [0, 2, 4, 6]
        proc = ProcessFixture(selected)
        proc.mask = list(range(32))  # EnableAffinityConfiguration's reset.
        self.assertEqual(audit_affinity(proc, selected, False), selected)
        self.assertEqual(proc.writes, [selected])
        self.assertEqual(audit_affinity(proc, selected, True), selected)
        self.assertEqual(len(proc.writes), 1)

    def test_scored_escape_is_rejected_without_reassertion(self):
        proc = ProcessFixture(range(32))
        with self.assertRaisesRegex(RuntimeError, "CPU affinity changed"):
            audit_affinity(proc, [0, 2, 4, 6], True)
        self.assertEqual(proc.writes, [])

    def test_matching_scored_mask_is_read_only(self):
        proc = ProcessFixture([6, 4, 2, 0])
        self.assertEqual(audit_affinity(proc, [0, 2, 4, 6], True), [0, 2, 4, 6])
        self.assertEqual(proc.writes, [])

    def test_unconstrained_runs_do_not_query_affinity(self):
        self.assertIsNone(audit_affinity(None, None, True))


if __name__ == "__main__":
    unittest.main()
