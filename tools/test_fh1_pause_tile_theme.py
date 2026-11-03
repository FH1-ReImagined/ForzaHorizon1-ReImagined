import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import fh1_pause_tile_theme as theme
from stage_horizon2_pause_scene import append_compressed, compressed_payload


class TileTheme(unittest.TestCase):
    def fixture(self, root):
        overlay = root / "overlay"
        overlay.mkdir()
        (overlay / "default.xex").write_bytes(b"synthetic executable")
        (overlay / "scene-provenance.json").write_text(json.dumps({"offline_cars_reload_flow": True}))
        with zipfile.ZipFile(overlay / "media.zip", "w") as archive:
            archive.writestr(theme.TARGET.upper(), b"old panel")
            archive.writestr("ui/textures/ui4/anthem/buttongrad.xds", b"keep gradient")
            archive.writestr("scene", b"keep scene")
        donor = root / "Horizon.zip"
        info = zipfile.ZipInfo(theme.SOURCE)
        info.compress_type, info.CRC, info.file_size = 21, 0x12345678, 131124
        with zipfile.ZipFile(donor, "w") as archive:
            append_compressed(archive, theme.SOURCE, info, b"synthetic XMem payload")
        return overlay, donor

    def test_native_payload_codec_and_sources_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay, donor = self.fixture(root)
            before = (overlay / "media.zip").read_bytes(), donor.read_bytes()
            output = root / "new"
            report = theme.stage_theme(overlay, donor, output)
            self.assertEqual(before, ((overlay / "media.zip").read_bytes(), donor.read_bytes()))
            self.assertEqual((output / "default.xex").read_bytes(), b"synthetic executable")
            with zipfile.ZipFile(output / "media.zip") as archive:
                matches = [i for i in archive.infolist() if i.filename.lower() == theme.TARGET]
                self.assertEqual(len(matches), 1)
                info = matches[0]
                self.assertEqual((info.compress_type, info.CRC, info.file_size), (21, 0x12345678, 131124))
                self.assertEqual(compressed_payload(archive, info), b"synthetic XMem payload")
                self.assertEqual(archive.read("ui/textures/ui4/anthem/buttongrad.xds"), b"keep gradient")
                self.assertEqual(archive.read("scene"), b"keep scene")
            provenance = json.loads((output / "scene-provenance.json").read_text())
            self.assertTrue(provenance["offline_cars_reload_flow"])
            self.assertEqual(provenance["fh1_tile_theme"], report)
            self.assertEqual(provenance["payload_offsets"][theme.TARGET], report["payload_offsets"][theme.TARGET])

    def test_refuses_overwrite_or_nested_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            overlay, donor = self.fixture(root)
            with self.assertRaises(ValueError):
                theme.stage_theme(overlay, donor, overlay / "new")
            output = root / "existing"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                theme.stage_theme(overlay, donor, output)


if __name__ == "__main__":
    unittest.main()
