import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

import fh2_pause_tile_depth as depth


def curve(target, first, last, prop=depth.Z_PROPERTY):
    return (struct.pack(">III", target, prop, 0x01000002) +
            struct.pack(">IfIfIf", 0, first, 0, first, 0, last) +
            struct.pack(">IfIfIf", 100, last, 0, -1, 0, -1))


def fixture():
    return b"header" + b"".join(
        curve(target, value, value - 20) + curve(target, value - 20, value)
        for target, value in depth.DEPTHS.items())


class TileDepth(unittest.TestCase):
    def test_only_whitelisted_depth_values_change(self):
        outside = curve(0x308, 0, -20)  # Untouched Grid02 target.
        opacity = curve(0xAE, 100, 0, 0x0191C315)
        source = fixture() + outside + opacity
        result = depth.patch_bgf(source)
        self.assertEqual(len(result), len(source))
        self.assertTrue(result.endswith(outside + opacity))
        mutable = set()
        for target, value in depth.DEPTHS.items():
            marker = struct.pack(">III", target, depth.Z_PROPERTY, 0x01000002)
            start = 0
            for _ in range(2):
                start = source.index(marker, start) + len(marker)
                for key in range(2):
                    # Every point/control time and target/property ID is kept.
                    for field in (4, 12, 20):
                        offset = start + key * 24 + field
                        self.assertEqual(struct.unpack_from(">f", result, offset)[0], value)
                        mutable.update(range(offset, offset + 4))
                start += 48
        self.assertTrue(all(a == b or i in mutable
                            for i, (a, b) in enumerate(zip(source, result))))

    def test_rejects_changed_or_missing_native_curves(self):
        source = fixture()
        marker = struct.pack(">III", 0xAE, depth.Z_PROPERTY, 0x01000002)
        start = source.index(marker) + len(marker)
        changed = bytearray(source)
        struct.pack_into(">I", changed, start + 24, 200)
        with self.assertRaises(ValueError):
            depth.patch_bgf(changed)
        with self.assertRaises(ValueError):
            depth.patch_bgf(source[:-1])
        with self.assertRaises(ValueError):
            depth.patch_bgf(depth.patch_bgf(source))

    def test_new_overlay_preserves_source_and_other_payloads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / "source", root / "output"
            source.mkdir()
            (source / "default.xex").write_bytes(b"unchanged executable")
            (source / "scene-provenance.json").write_text(json.dumps({"fh1_tile_theme": True}))
            with zipfile.ZipFile(source / "media.zip", "w") as archive:
                archive.writestr(depth.SCENE, fixture())
                archive.writestr("other", b"keep other payload")
            original = (source / "media.zip").read_bytes()
            depth.stage_depth(source, output)
            self.assertEqual((source / "media.zip").read_bytes(), original)
            self.assertEqual((output / "default.xex").read_bytes(), b"unchanged executable")
            with zipfile.ZipFile(output / "media.zip") as archive:
                self.assertEqual(archive.read(depth.SCENE), depth.patch_bgf(fixture()))
                self.assertEqual(archive.read("other"), b"keep other payload")
            self.assertTrue(json.loads((output / "scene-provenance.json").read_text())["fh1_tile_theme"])
            with self.assertRaises(FileExistsError):
                depth.stage_depth(source, output)
            with self.assertRaises(ValueError):
                depth.stage_depth(source, source / "nested")


if __name__ == "__main__":
    unittest.main()
