import struct
import unittest

import fh1_pause_header as header


def text_record(name, font, size, width):
    return (struct.pack(">I", len(name)) + name + bytes(24) + font +
            struct.pack(">I", size) + bytes(29) + struct.pack(">f", width) +
            b"preserved-record-tail")


def source_fbf():
    return b"".join(text_record(*record) for record in (
        (b"TEXT_CAR", b"horizon_e", 15, 222.0),
        (b"TEXT_CR", b"horizon_e", 14, 9.0),
        (b"TEXT_PI", b"horizon_d", 15, 24.0),
        (b"Next_Level", b"horizon_d", 14, 44.0),
        (b"Points", b"horizon_d", 14, 74.0)))


def source_bgf():
    contract = (b"WRISTBAND\0" + bytes(98) +
                b"\0\0\0\x09horizon_d" + bytes(30))
    return (b"\0\0\0\x09horizon_e-unrelated" +
            b"\0\0\0\x0aPLAYER_BAR" + contract * 3 +
            b"\0\0\0\x09horizon_e" * 3 + b"\0\0\0\x04onUp" +
            b"\0\0\0\x09horizon_e-unrelated")


class HeaderStyleTests(unittest.TestCase):
    def test_native_text_styles_preserve_offsets_and_other_records(self):
        unrelated = text_record(b"OTHER", b"horizon_e", 18, 40.0)
        source = source_fbf() + unrelated
        result = header.patch_fbf(source)
        self.assertEqual(len(result), len(source))
        self.assertTrue(result.endswith(unrelated))
        for record in (
                (b"TEXT_CAR", b"horizon_d", 14, 238.0),
                (b"TEXT_CR", b"horizon_d", 14, 9.0),
                (b"TEXT_PI", b"horizon_d", 16, 37.0),
                (b"Next_Level", b"horizon_d", 14, 62.0),
                (b"Points", b"horizon_d", 14, 99.0)):
            self.assertIn(text_record(*record), result)

    def test_ambiguous_or_unexpected_records_rejected(self):
        source = source_fbf()
        credit = text_record(b"TEXT_CR", b"horizon_e", 14, 9.0)
        for invalid in (
                source.replace(credit, b""), source + credit,
                source.replace(b"horizon_e", b"otherfont", 1),
                source.replace(struct.pack(">f", 24), struct.pack(">f", 25))):
            with self.assertRaises(ValueError):
                header.patch_fbf(invalid)

    def test_live_fonts_are_limited_to_header(self):
        source = source_bgf()
        result = header.patch_bgf(source)
        self.assertEqual(len(source), len(result))
        self.assertEqual(result.count(b"horizon_b"), 3)
        self.assertEqual(result.count(b"horizon_d"), 3)
        self.assertEqual(result.count(b"horizon_e-unrelated"), 2)

    def test_live_font_patch_rejects_unknown_layout(self):
        source = source_bgf()
        for invalid in (
                source.replace(b"\x09horizon_d", b"\x09otherfont", 1),
                source.replace(b"PLAYER_BAR", b"PLAYER_FOO"),
                source.replace(b"onUp", b"onXX"),
                source.replace(b"\x09horizon_e", b"\x09otherfont", 2)):
            with self.assertRaises(ValueError):
                header.patch_bgf(invalid)


if __name__ == "__main__":
    unittest.main()
