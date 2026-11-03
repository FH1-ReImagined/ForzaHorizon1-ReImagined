import struct
import unittest

import fh2_pause_tabs as tabs


def bgf():
    record = (struct.pack(">III", 0xE252B3F0, 0x90, 0xD8) + b"\x01\x01" +
              struct.pack(">IIIIf", 5, 0x083BF5F8, 5000, 0x1A7C230D, -400))
    curve = struct.pack(">III", 0x91, 0x027C230D, 0x01000006)
    for key in range(6):
        value = -400 if key == 0 else -0.5 if key == 5 else -key * 60
        curve += struct.pack(">IfIfIf", key * 1000, value, 0, value + 1, 0, value - 1)
    return record + b"unrelated" + curve + b"separator" + curve + b"tail"


class ShoulderAnimation(unittest.TestCase):
    def test_only_values_change_and_curve_becomes_constant(self):
        source = bgf()
        result = tabs.patch_bgf(source)
        self.assertEqual(len(source), len(result))
        expected = bytearray(source)
        struct.pack_into(">f", expected, 30, -0.5)
        marker = struct.pack(">III", 0x91, 0x027C230D, 0x01000006)
        start = 0
        for _ in range(2):
            start = source.index(marker, start) + 12
            for key in range(6):
                for offset in (4, 12, 20):
                    struct.pack_into(">f", expected, start + key * 24 + offset, -0.5)
            start += 144
        self.assertEqual(result, bytes(expected))

    def test_bsg_only_group_rb_default_changes(self):
        marker = struct.pack(">III", 0x4A, 0xD8, 0x01000000)
        source = b"prefix" + marker + struct.pack(">f", -400) + b"tail"
        self.assertEqual(tabs.patch_bsg(source),
                         b"prefix" + marker + struct.pack(">f", -0.5) + b"tail")
        for bad in (source + source, source.replace(marker, bytes(12)),
                    source.replace(struct.pack(">f", -400), struct.pack(">f", -399))):
            with self.assertRaises(ValueError):
                tabs.patch_bsg(bad)

    def test_missing_ambiguous_truncated_or_changed_curves_rejected(self):
        source = bgf()
        marker = struct.pack(">III", 0x91, 0x027C230D, 0x01000006)
        for bad in (source + source, source[:-8], source.replace(marker, bytes(12), 1),
                    source.replace(struct.pack(">I", 2000), struct.pack(">I", 2001), 1)):
            with self.assertRaises(ValueError):
                tabs.patch_bgf(bad)


if __name__ == "__main__":
    unittest.main()
