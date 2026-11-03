import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import fh2_pause_progress_ring as ring


def bgf():
    return (struct.pack(">III", 0xCC12FB41, 0xA3, 0xF1) + b"\x01\x01" +
            b"unchanged scene" + struct.pack(">III", 0x804B9B4E, 0x135, 0x255) +
            b"\x07\x05\x02" + b"unchanged progress curves" +
            ring.OLD_SLIDE_TIMING + b"\x00\x00\x00\x00unchanged other slide")


def bsg():
    data = bytearray(0x30 + 271 * 96)
    struct.pack_into(">I", data, 0x24, 271)
    struct.pack_into(">III", data, 0x30 + 182 * 96, 149, 0x255, 0x01000000)
    struct.pack_into(">III", data, 0x30 + 87 * 96, 86, 0xF1, 0x01000000)
    return bytes(data)


class ProgressRing(unittest.TestCase):
    def test_changes_only_parent_indices_and_exact_old_slide_timing(self):
        timing = bgf().index(ring.OLD_SLIDE_TIMING)
        for source, patch, edits in (
                (bgf(), ring.patch_bgf, [(14 + len(b"unchanged scene") + 4, 0xAB),
                                       (timing + 13, 0x7FFFFFFF), (timing + 21, 0)]),
                (bsg(), ring.patch_bsg, [(0x30 + 182 * 96, 87)])):
            result = patch(source)
            self.assertEqual(len(result), len(source))
            expected = bytearray(source)
            for position, value in edits:
                struct.pack_into(">I", expected, position, value)
            self.assertEqual(result, bytes(expected))
        self.assertLess(ring.BGF_PARENT, ring.BGF_RING)
        self.assertLess(ring.BSG_PARENT, ring.BSG_RING)
        patched = ring.patch_bgf(bgf())
        self.assertEqual(struct.unpack_from(">i", patched, timing + 13)[0], 0x7FFFFFFF)
        self.assertEqual(struct.unpack_from(">i", patched, timing + 21)[0], 0)

    def test_rejects_wrong_or_already_moved_graph(self):
        with self.assertRaises(ValueError):
            ring.patch_bgf(ring.patch_bgf(bgf()))
        with self.assertRaises(ValueError):
            ring.patch_bsg(ring.patch_bsg(bsg()))
        invalid = bytearray(bsg())
        struct.pack_into(">I", invalid, 0x30 + 87 * 96 + 4, 0x999)
        with self.assertRaises(ValueError):
            ring.patch_bsg(invalid)
        with self.assertRaises(ValueError):
            ring.patch_bgf(bgf().replace(ring.OLD_SLIDE_TIMING, b"wrong timing"))
        with self.assertRaises(ValueError):
            ring.patch_bgf(bgf() + ring.OLD_SLIDE_TIMING)

    def test_fresh_overlay_preserves_source_and_other_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "source", Path(folder) / "output"
            source.mkdir()
            (source / "default.xex").write_bytes(b"unchanged executable")
            (source / "scene-provenance.json").write_text(json.dumps({"fh2_tile_focus_depth": True}))
            with zipfile.ZipFile(source / "media.zip", "w") as archive:
                archive.writestr(ring.SCENE + ".bgf", bgf())
                archive.writestr(ring.SCENE + ".bsg", bsg())
                archive.writestr("other", b"unchanged native texture")
            original = (source / "media.zip").read_bytes()
            ring.stage_ring(source, output)
            self.assertEqual((source / "media.zip").read_bytes(), original)
            self.assertEqual((output / "default.xex").read_bytes(), b"unchanged executable")
            with zipfile.ZipFile(output / "media.zip") as archive:
                self.assertEqual(archive.read(ring.SCENE + ".bgf"), ring.patch_bgf(bgf()))
                self.assertEqual(archive.read(ring.SCENE + ".bsg"), ring.patch_bsg(bsg()))
                self.assertEqual(archive.read("other"), b"unchanged native texture")
            self.assertTrue(json.loads((output / "scene-provenance.json").read_text())["fh2_tile_focus_depth"])
            with self.assertRaises(FileExistsError):
                ring.stage_ring(source, output)
            with self.assertRaises(ValueError):
                ring.stage_ring(source, source / "nested")


if __name__ == "__main__":
    unittest.main()
