"""The wide world hook list checked against synthetic instructions, no game data."""
from pathlib import Path
import struct
import sys
import tomllib
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wide_world_hooks as ww  # noqa: E402

BASE = 0x82000000
TEXT = 0x1000


def image(words):
    """A loaded image with one executable section holding words, and a data section after it
    holding the same words (never looked at)."""
    code = struct.pack(f">{len(words)}I", *words)
    img = bytearray(0x3000)
    pe = 0x80
    struct.pack_into("<I", img, 0x3C, pe)
    struct.pack_into("<H", img, pe + 6, 2)          # sections
    struct.pack_into("<H", img, pe + 20, 0)         # no optional header
    for k, (name, at, flags) in enumerate(((b".text", TEXT, 0x60000020), (b".data", 0x2000, 0xC0000040))):
        struct.pack_into("<8sII", img, pe + 24 + 40 * k, name, len(code), at)
        struct.pack_into("<I", img, pe + 24 + 40 * k + 36, flags)
        img[at:at + len(code)] = code
    return bytes(img)


def vmx128(to_fixed, to, source, bits):
    return (6 << 26 | (to & 31) << 21 | bits << 16 | (source & 31) << 11 | (0x230 if to_fixed else 0x2B0)
            | (to >> 5) << 2 | source >> 5)


def vmx(to_fixed, to, source, bits):
    return 4 << 26 | to << 21 | bits << 16 | source << 11 | (970 if to_fixed else 842)


WORDS = [0x60000000, vmx128(True, 63, 63, 18), vmx128(False, 127, 63, 18), vmx(True, 0, 0, 18),
         vmx(False, 1, 0, 18), vmx128(True, 5, 6, 17), vmx(False, 1, 0, 0), 0x4E800020]


class Conversions(unittest.TestCase):
    def test_finds_the_four_forms_with_18_bits_in_code_only(self):
        found = ww.conversions(BASE, image(WORDS))
        at = BASE + TEXT
        self.assertEqual(found, [(at + 4, ww.TO_FIXED, 63, 63), (at + 8, ww.TO_FLOAT, 127, 63),
                                 (at + 12, ww.TO_FIXED, 0, 0), (at + 16, ww.TO_FLOAT, 1, 0)])

    def test_written_hints_verify(self):
        img = image(WORDS)
        hints = tomllib.loads(ww.hints_text(ww.conversions(BASE, img)))
        self.assertEqual(ww.verify(BASE, img, hints), 4)
        hook = hints["midasm_hook"][1]
        self.assertEqual(hook["registers"], ["fpscr", "v127", "v63"])
        self.assertEqual(hook["jump_address_on_true"], hook["address"] + 4)
        in_place = hints["midasm_hook"][0]          # one register: one vector parameter
        self.assertEqual((in_place["name"], in_place["registers"]), (ww.TO_FIXED + "InPlace", ["fpscr", "v63"]))

    def test_a_missing_changed_or_stray_hook_fails(self):
        img = image(WORDS)
        good = tomllib.loads(ww.hints_text(ww.conversions(BASE, img)))["midasm_hook"]
        with self.assertRaisesRegex(ValueError, "Missing or different"):
            ww.verify(BASE, img, {"midasm_hook": good[1:]})
        for field, value in (("name", ww.TO_FIXED), ("registers", ["fpscr", "v62"]),
                             ("jump_address_on_true", good[0]["address"] + 8)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "Missing or different"):
                    ww.verify(BASE, img, {"midasm_hook": [dict(good[0], **{field: value})] + good[1:]})
        stray = dict(good[0], address=BASE + TEXT, jump_address_on_true=BASE + TEXT + 4)
        with self.assertRaisesRegex(ValueError, "no such conversion"):
            ww.verify(BASE, img, {"midasm_hook": good + [stray]})
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            ww.verify(BASE, img, {"midasm_hook": good + [good[0]]})

    def test_an_image_with_none_is_refused(self):
        with self.assertRaisesRegex(ValueError, "No fixed-point"):
            ww.verify(BASE, image([0x60000000]), {})

    def test_the_repository_hints_are_well_formed(self):
        path = Path(__file__).resolve().parents[1] / "forza-recomp" / "hints_tu4" / "wide_world.toml"
        hooks = tomllib.loads(path.read_text(encoding="utf-8"))["midasm_hook"]
        self.assertEqual(len(hooks), 83)
        self.assertEqual(len({h["address"] for h in hooks}), 83)
        for h in hooks:
            self.assertIn(h["name"].removesuffix("InPlace"), (ww.TO_FIXED, ww.TO_FLOAT))
            self.assertEqual(len(h["registers"]), 2 if h["name"].endswith("InPlace") else 3)
            self.assertEqual(h["jump_address_on_true"], h["address"] + 4)
            self.assertEqual(h["registers"][0], "fpscr")
        manifest = (path.parents[1] / "forza_manifest_tu4.toml").read_text(encoding="utf-8")
        self.assertIn("hints_tu4/wide_world.toml", manifest)


if __name__ == "__main__":
    unittest.main()
