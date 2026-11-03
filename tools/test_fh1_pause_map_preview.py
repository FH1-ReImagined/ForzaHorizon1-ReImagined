import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import fh1_pause_map_preview as preview
from stage_horizon2_pause_scene import append_compressed, compressed_payload, digest


class MapPreview(unittest.TestCase):
    def fixture(self, root):
        overlay = root / "overlay"
        overlay.mkdir()
        for name in preview.EXECUTABLES:
            (overlay / name).write_bytes(("synthetic " + name).encode())
        (overlay / "scene-provenance.json").write_text(json.dumps({
            "existing_feature": True, "payload_offsets": {"previous": 123}}))
        with zipfile.ZipFile(overlay / "media.zip", "w") as archive:
            for index, suffix in enumerate(("bgf", "bsg", "fbf")):
                name = f"{preview.SOURCE}.{suffix}".upper()
                info = zipfile.ZipInfo(name)
                info.compress_type = 21  # Native XMem; must copy without decoding.
                info.CRC = 0x12345678 + index
                info.file_size = 10000 + index
                append_compressed(archive, name, info, b"native compressed " + suffix.encode())
            archive.writestr("unrelated/texture", b"keep this texture", zipfile.ZIP_DEFLATED)
        return overlay

    def test_alias_preserves_native_codec_payloads_and_original_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay = self.fixture(root)
            before = {p.name: p.read_bytes() for p in overlay.iterdir()}
            output = root / "new"
            report = preview.stage(overlay, output)
            self.assertEqual(before, {p.name: p.read_bytes() for p in overlay.iterdir()})
            for name in preview.EXECUTABLES:
                self.assertEqual((output / name).read_bytes(), before[name])
            with zipfile.ZipFile(overlay / "media.zip") as original, \
                    zipfile.ZipFile(output / "media.zip") as staged:
                self.assertEqual(len(staged.infolist()), len(original.infolist()) + 3)
                for info in original.infolist():
                    copied = staged.getinfo(info.filename)
                    self.assertEqual(compressed_payload(staged, copied), compressed_payload(original, info))
                    self.assertEqual((copied.compress_type, copied.CRC, copied.file_size),
                                     (info.compress_type, info.CRC, info.file_size))
                for suffix in ("bgf", "bsg", "fbf"):
                    source = original.getinfo(f"{preview.SOURCE}.{suffix}".upper())
                    alias = staged.getinfo(f"{preview.TARGET}.{suffix}")
                    self.assertEqual(compressed_payload(staged, alias), compressed_payload(original, source))
                    self.assertEqual((alias.compress_type, alias.CRC, alias.file_size),
                                     (source.compress_type, source.CRC, source.file_size))
                self.assertEqual(staged.read("unrelated/texture"), b"keep this texture")
            self.assertTrue(report["existing_feature"])
            self.assertEqual(report["payload_offsets"]["previous"], 123)
            self.assertEqual(report["native_home_map_preview"]["source_archive_sha256"],
                             digest(overlay / "media.zip"))
            self.assertEqual(report["output_archive_sha256"], digest(output / "media.zip"))
            self.assertEqual(json.loads((output / "scene-provenance.json").read_text()), report)

    def test_rejects_existing_and_nested_outputs_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay = self.fixture(root)
            output = root / "existing"
            output.mkdir()
            sentinel = output / "untouched"
            sentinel.write_bytes(b"existing output")
            with self.assertRaises(FileExistsError):
                preview.stage(overlay, output)
            self.assertEqual(list(output.iterdir()), [sentinel])
            self.assertEqual(sentinel.read_bytes(), b"existing output")
            for invalid in (overlay, overlay / "nested"):
                with self.assertRaises(ValueError):
                    preview.stage(overlay, invalid)
            self.assertFalse((overlay / "nested").exists())

    def test_rejects_already_staged_alias_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay = self.fixture(root)
            staged = root / "staged"
            preview.stage(overlay, staged)
            output = root / "duplicate"
            with self.assertRaises(ValueError):
                preview.stage(staged, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
