"""Preparation safety checks with synthetic data; no game files are needed."""
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import struct
import zipfile

import extract_horizon2_navigation as navigation
import prepare_horizon2_ui as prepare
import stage_horizon2_pause_scene as scene


class Preparation(unittest.TestCase):
    def package(self, title=prepare.TITLE_ID):
        data = {member: member.encode() for member in prepare.MEMBERS}
        entries, files = [], []
        for name, value in data.items():
            entries.append(SimpleNamespace(path=name, is_dir=False, size=len(value),
                                           runs=[(len(files), 0, len(value))]))
            files.append(io.BytesIO(value))
        return SimpleNamespace(hdr=SimpleNamespace(title_id=title, media_id=0x7C7779D1),
                               files=files, entries=lambda: entries), data

    def test_stage_verified_copy_and_close_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "god/package"
            source.parent.mkdir()
            source.write_bytes(b"source header")
            package, data = self.package()
            digest = hashlib.sha256(data["default.xex"]).hexdigest()
            with patch.object(prepare.xcontent_extract, "open_package", return_value=package), \
                 patch.object(prepare, "EXECUTABLE_SHA256", digest):
                result = prepare.prepare(source, root / "private")
            self.assertEqual(source.read_bytes(), b"source header")
            self.assertTrue(all(f.closed for f in package.files))
            for name, value in data.items():
                self.assertEqual((root / "private" / name).read_bytes(), value)
                self.assertEqual(result["files"][name]["sha256"], hashlib.sha256(value).hexdigest())
            self.assertTrue((root / "private/forza_manifest.toml").exists())

    def test_wrong_title_and_unverified_build_write_nothing(self):
        for title in (0x4D5309C9, prepare.TITLE_ID):
            with self.subTest(title=title), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                package, _ = self.package(title)
                with patch.object(prepare.xcontent_extract, "open_package", return_value=package):
                    with self.assertRaises(ValueError):
                        prepare.prepare(root / "god/package", root / "out")
                self.assertFalse((root / "out").exists())
                self.assertTrue(all(f.closed for f in package.files))

    def test_refuse_input_directory_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for output in (root / "god", root / "god/new-output"):
                with self.assertRaises(ValueError):
                    prepare.prepare(root / "god/package", output)
            output = root / "out"
            output.mkdir()
            (output / "keep").write_text("preserve")
            with self.assertRaises(FileExistsError):
                prepare.prepare(root / "god/package", output)
            self.assertEqual((output / "keep").read_text(), "preserve")

    def test_truncated_member_is_rejected(self):
        entry = SimpleNamespace(path="test", size=8, runs=[(0, 0, 8)])
        with self.assertRaisesRegex(ValueError, "Short read"):
            prepare.read_entry(SimpleNamespace(files=[io.BytesIO(b"short")]), entry)


class NavigationExtraction(unittest.TestCase):
    def fixture(self, root, suffix=""):
        generated = root / "generated"
        generated.mkdir()
        (root / "default.xex").write_bytes(b"synthetic")
        # Test-only functions, not derived from game code.
        (generated / "horizon2_ui_recomp.0.cpp").write_text("\n".join(
            f"DEFINE_REX_FUNC({name}) {{\n REX_FUNC_PROLOGUE();\n {suffix}\n}}"
            for name in navigation.FUNCTIONS))
        return generated

    def test_rename_only_and_record_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated = self.fixture(root)
            with patch.object(navigation, "EXECUTABLE_SHA256", hashlib.sha256(b"synthetic").hexdigest()):
                report = navigation.extract(generated, root / "private")
            code = (root / "private/pause_navigation.inc").read_text()
            for original, renamed in navigation.FUNCTIONS.items():
                self.assertIn(f"DEFINE_REX_FUNC({renamed})", code)
                self.assertNotIn(f"DEFINE_REX_FUNC({original})", code)
                self.assertIn(original, report["functions"])

    def test_unknown_call_or_macro_is_not_silently_stubbed(self):
        for suffix in ("sub_12345678(ctx,base);", "REX_UNREVIEWED();"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                generated = self.fixture(root, suffix)
                with patch.object(navigation, "EXECUTABLE_SHA256", hashlib.sha256(b"synthetic").hexdigest()):
                    with self.assertRaises(ValueError):
                        navigation.extract(generated, root / "private")
                self.assertFalse((root / "private").exists())


class SceneArchive(unittest.TestCase):
    def test_offsets_codecs_and_source_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("keep", b"untouched")
                archive.writestr("scene", b"old")
            before = source.read_bytes()
            donor = root / "donor.zip"
            for codec in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                with self.subTest(codec=codec):
                    with zipfile.ZipFile(donor, "w") as archive:
                        archive.writestr("source-scene", b"synthetic scene" * 20, compress_type=codec)
                    with zipfile.ZipFile(donor) as archive:
                        info = archive.getinfo("source-scene")
                        payload = scene.compressed_payload(archive, info)
                    destination = root / f"private-{codec}.zip"
                    offsets = scene.patch_archive(source, destination, {"scene": (info, payload)})
                    with zipfile.ZipFile(destination) as archive:
                        self.assertEqual(archive.namelist().count("scene"), 1)
                        self.assertEqual(archive.read("keep"), b"untouched")
                        self.assertEqual(archive.read("scene"), b"synthetic scene" * 20)
                        result = archive.getinfo("scene")
                        self.assertEqual(result.compress_type, codec)
                        self.assertEqual(result.CRC, info.CRC)
                        self.assertEqual(struct.unpack("<HHI", result.extra), (0x1123, 4, offsets["scene"]))
                        self.assertEqual(scene.compressed_payload(archive, result), payload)
                    self.assertEqual(source.read_bytes(), before)
                    data = destination.read_bytes()
                    self.assertEqual(data[offsets["scene"]:offsets["scene"] + len(payload)], payload)
                    with self.assertRaises(FileExistsError):
                        scene.patch_archive(source, destination, {})

    def test_method_21_keeps_original_payload_and_headers(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.zip"
            info = zipfile.ZipInfo("donor")
            info.compress_type, info.file_size, info.CRC = 21, 10000, 0x12345678
            payload = b"synthetic compressed stream"
            with zipfile.ZipFile(path, "w") as archive:
                offset = scene.append_compressed(archive, "scene", info, payload)
            with zipfile.ZipFile(path) as archive:
                result = archive.getinfo("scene")
                self.assertEqual((result.compress_type, result.file_size, result.CRC), (21, 10000, 0x12345678))
                self.assertEqual(scene.compressed_payload(archive, result), payload)
            data = path.read_bytes()
            self.assertEqual(struct.unpack_from("<H", data, 8)[0], 21)
            self.assertEqual(data[offset:offset + len(payload)], payload)


if __name__ == "__main__":
    unittest.main()
