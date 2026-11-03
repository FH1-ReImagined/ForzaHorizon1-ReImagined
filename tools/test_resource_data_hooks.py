"""Validate hook auditing with synthetic instructions and vtables, no game data."""
import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_resource_data_hooks import verify


class ResourceHookAuditTests(unittest.TestCase):
    def fixture(self, zero=11, saved=31):
        base, load_step, function = 0x10000000, 0x10003000, 0x10000200
        image = bytearray(1024)
        struct.pack_into(">I", image, 64 + 16 * 4, function)
        struct.pack_into(">I", image, 64 + 22 * 4, load_step)
        words = [0x7C000378 | (3 << 21) | (saved << 16) | (3 << 11),
                 0x80630040, 0x38000000 | (zero << 21), 0x2B030000,
                 0x90000040 | (zero << 21) | (saved << 16), 0x419A0010]
        struct.pack_into(">6I", image, function - base, *words)
        hook = dict(address=function + 4, jump_address=function + 20,
                    name=f"ForzaUnloadResourceDataR{zero}", registers=["r3"])
        return base, image, load_step, {"midasm_hook": [hook]}

    def test_each_zero_register_variant(self):
        for zero, saved in ((10, 11), (11, 31), (29, 31), (30, 31)):
            with self.subTest(zero=zero):
                self.assertEqual(verify(*self.fixture(zero, saved)), 1)

    def test_missing_unload_hook_fails(self):
        base, image, load, hints = self.fixture()
        hints["midasm_hook"] = []
        with self.assertRaisesRegex(ValueError, "Missing or incompatible"):
            verify(base, image, load, hints)

    def test_wrong_register_or_resume_before_clear_fails(self):
        for field, value in (("name", "ForzaUnloadResourceDataR10"),
                             ("registers", ["r11"]), ("jump_address", 0x10000210)):
            with self.subTest(field=field):
                base, image, load, hints = self.fixture()
                hints["midasm_hook"][0][field] = value
                with self.assertRaisesRegex(ValueError, "Missing or incompatible"):
                    verify(base, image, load, hints)

    def test_changed_guest_instructions_fail(self):
        base, image, load, hints = self.fixture()
        struct.pack_into(">I", image, 0x200 + 12, 0x2B030001)  # compare with 1, not 0
        with self.assertRaisesRegex(ValueError, "Unexpected unload instructions"):
            verify(base, image, load, hints)

    def test_duplicate_or_unverified_hook_fails(self):
        for duplicate in (True, False):
            base, image, load, hints = self.fixture()
            extra = copy.deepcopy(hints["midasm_hook"][0])
            if not duplicate:
                extra["address"] += 128
            hints["midasm_hook"].append(extra)
            with self.assertRaises(ValueError):
                verify(base, image, load, hints)

    def test_wrong_image_without_unloads_fails(self):
        base, image, load, hints = self.fixture()
        with self.assertRaisesRegex(ValueError, "No supported resource unloads"):
            verify(base, image, load + 4, hints)


if __name__ == "__main__":
    unittest.main()
