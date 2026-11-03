"""DLC step: packages in subfolders and inside zips (issue #410). Synthetic packages only."""
import io
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import dlcscan, installer  # noqa: E402
from test_dlc_warnings import extracted, packed  # noqa: E402

A, B, C = "DEADBEEF01234567", "CAFEBABE01234567", "FEEDFACE01234567"
DEST = "0000000000000000/4D5309C9/00000002"


def pkg_bytes(tmp: Path, name: str, display="Pack") -> bytes:
    return packed(tmp / "mk", name, display).read_bytes()


def make_zip(path: Path, entries: dict, links=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, data in entries.items():
            z.writestr(n, data)
        for n, target in links:
            i = zipfile.ZipInfo(n)
            i.create_system = 3
            i.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(i, target)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "src"
        self.src.mkdir()
        self.data = self.root / "data"
        installer._DLC_CACHE.clear()
        self.addCleanup(installer._DLC_CACHE.clear)
        self.logs = []

    def install(self, cancelled=lambda: False):
        return installer.install_dlc_folder(str(self.src), self.data, lambda *_: None, cancelled, self.logs.append)

    def installed(self):
        d = self.data / DEST
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def no_stage_left(self):
        self.assertEqual(list((self.data / "tmp").glob("dlc-*")) if (self.data / "tmp").exists() else [], [])


class Folders(Base):
    def test_nested_folders_are_scanned(self):
        packed(self.src / "season1" / "cars", A, "A")
        packed(self.src / "x" / "y" / "z", B, "B")
        extracted(self.src / "deep" / "more", C, "C")
        found, skipped = installer.describe_dlc_folder(str(self.src))
        self.assertEqual(sorted(n for n, _ in found), sorted([A, B, C]))
        self.assertEqual(self.install(), 3)
        self.assertEqual(self.installed(), sorted([A, B, C]))

    def test_depth_hidden_links_and_junk(self):
        deep = self.src
        for i in range(dlcscan.MAX_DEPTH + 2):
            deep = deep / f"d{i}"
        packed(deep, A, "A")
        packed(self.src / ".hidden", B, "B")
        packed(self.src / "ok", C, "C")
        (self.src / "ok" / "readme.txt").write_text("hi")
        outside = self.root / "outside"
        packed(outside, "ABCDEF0123456789", "out")
        try:
            os.symlink(outside, self.src / "link")
        except (OSError, NotImplementedError):
            pass
        found, skipped = installer.describe_dlc_folder(str(self.src))
        self.assertEqual([n for n, _ in found], [C])
        reasons = " ".join(r for _n, r in skipped)
        self.assertIn("hidden", reasons)
        self.assertIn("deep", reasons)
        self.assertNotIn("readme", " ".join(n for n, _ in skipped))   # junk files are ignored quietly
        self.assertEqual(self.install(), 1)

    def test_cache_notices_a_package_added_two_folders_deep(self):
        packed(self.src / "a", A, "A")
        found, _ = installer.describe_dlc_folder(str(self.src))
        self.assertEqual([n for n, _ in found], [A])
        packed(self.src / "b" / "c", B, "B")
        self.assertIsNone(installer.dlc_cached(str(self.src)))
        found, _ = installer.describe_dlc_folder(str(self.src))
        self.assertEqual(sorted(n for n, _ in found), sorted([A, B]))
        parent = self.src / "b" / "c"
        before = parent.stat()
        packed(parent, C, "C")
        # Reproduce coarse/preserved directory times instead of relying on sleep.
        os.utime(parent, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertIsNone(installer.dlc_cached(str(self.src)))
        found, _ = installer.describe_dlc_folder(str(self.src))
        self.assertEqual(len(found), 3)

    def test_duplicates_install_once(self):
        packed(self.src / "one", A, "A")
        packed(self.src / "two", A, "A")
        found, skipped = installer.describe_dlc_folder(str(self.src))
        self.assertEqual(len(found), 1)
        self.assertEqual(self.install(), 1)
        self.assertTrue(any("duplicate" in r for _n, r in skipped))

    def test_non_dlc_package_is_reported(self):
        (self.src / "tu").mkdir()
        data = bytearray(pkg_bytes(self.root, A))
        data[0x344:0x348] = (1).to_bytes(4, "big")      # not marketplace content
        (self.src / "tu" / B).write_bytes(bytes(data))
        _found, skipped = installer.describe_dlc_folder(str(self.src))
        self.assertIn((B, "a package, but not DLC"), skipped)
        with self.assertRaises(installer.InstallError):
            self.install()


class Zips(Base):
    def test_zip_with_nested_folders_installs_and_cleans_up(self):
        make_zip(self.src / "pack.zip", {"cars/a/" + A: pkg_bytes(self.root, A), "b\\" + B: pkg_bytes(self.root, B),
                                         "readme.txt": b"x"})
        found, _ = installer.describe_dlc_folder(str(self.src))
        self.assertEqual(sorted(n for n, _ in found), sorted([A, B]))
        self.assertEqual(self.install(), 2)
        self.assertEqual(self.installed(), sorted([A, B]))
        self.no_stage_left()

    def test_zip_in_subfolder_and_nested_zip(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as z:
            z.writestr("deep/" + C, pkg_bytes(self.root, C))
        make_zip(self.src / "sub" / "packs.zip", {A: pkg_bytes(self.root, A), "more.zip": inner.getvalue()}) \
            if (self.src / "sub").mkdir() is None else None
        self.assertEqual(self.install(), 2)
        self.assertEqual(self.installed(), sorted([A, C]))
        self.no_stage_left()

    def test_zip_slip_absolute_and_symlink_are_refused(self):
        make_zip(self.src / "slip.zip", {"../../evil/" + A: pkg_bytes(self.root, A)})
        make_zip(self.src / "abs.zip", {"/tmp/x/" + B: pkg_bytes(self.root, B)})
        make_zip(self.src / "link.zip", {C: pkg_bytes(self.root, C)}, links=[("l", "/etc")])
        found, skipped = installer.describe_dlc_folder(str(self.src))
        self.assertEqual(found, [])
        self.assertEqual(len([1 for _n, r in skipped if "zip refused" in r]), 3)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertFalse((self.root / "evil").exists())
        self.assertEqual(self.installed(), [])
        self.no_stage_left()

    def test_corrupt_zip_is_skipped_but_others_install(self):
        (self.src / "broken.zip").write_bytes(b"PK\x03\x04" + b"garbage" * 50)
        truncated = io.BytesIO()
        make_zip(self.root / "t.zip", {A: pkg_bytes(self.root, A)})
        (self.src / "cut.zip").write_bytes((self.root / "t.zip").read_bytes()[:-30])
        packed(self.src, B, "B")
        self.assertEqual(self.install(), 1)
        self.assertEqual(self.installed(), [B])
        self.assertTrue(any("damaged" in line for line in self.logs))
        self.no_stage_left()

    def test_non_dlc_zip(self):
        make_zip(self.src / "photos.zip", {"a.jpg": b"jpg", "b.txt": b"t"})
        found, skipped = installer.describe_dlc_folder(str(self.src))
        self.assertEqual(found, [])
        self.assertIn(("photos.zip", "zip holds no DLC packages"), skipped)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.no_stage_left()

    def test_duplicate_between_folder_and_zip(self):
        packed(self.src, A, "A")
        make_zip(self.src / "p.zip", {A: pkg_bytes(self.root, A)})
        self.assertEqual(self.install(), 1)
        self.no_stage_left()

    def test_cancel_cleans_up(self):
        make_zip(self.src / "p.zip", {A: pkg_bytes(self.root, A)})
        calls = [0]

        def cancel():
            calls[0] += 1
            return calls[0] > 1
        with self.assertRaises(installer.Cancelled):
            self.install(cancel)
        self.no_stage_left()
        self.assertEqual(self.installed(), [])

    def test_failed_install_cleans_up(self):
        make_zip(self.src / "p.zip", {A: pkg_bytes(self.root, A)})
        with patch.object(installer.install_dlc, "install", side_effect=SystemExit("boom")):
            with self.assertRaises(installer.InstallError):
                self.install()
        self.no_stage_left()

    def test_size_cap_and_free_space(self):
        make_zip(self.src / "p.zip", {A: pkg_bytes(self.root, A)})
        with patch.object(dlcscan, "MAX_UNPACKED", 100):
            with self.assertRaises(installer.InstallError):
                self.install()
        with patch.object(dlcscan.shutil, "disk_usage", return_value=type("U", (), {"free": 10})()):
            with self.assertRaises(installer.InstallError):
                self.install()
        self.assertTrue(any("free disk space" in line for line in self.logs))
        self.no_stage_left()


if __name__ == "__main__":
    unittest.main()
