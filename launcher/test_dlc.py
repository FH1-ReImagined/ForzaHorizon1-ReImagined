"""DLC and Title Update 4 reach the game after an update or a folder change (issues #65, #70):
python3 -m unittest launcher/test_dlc.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import datafolder as df, game, paths  # noqa: E402
from forza_launcher.config import Config  # noqa: E402

DLC = Path("0000000000000000") / "4D5309C9"
RALLY = "D5A1E8E7F8C1A1E0D0C0B0A090807060504D5309C9"


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def install(root: Path, name=RALLY, header=True, data=b"pkg"):
    touch(root / DLC / "00000002" / name / "media" / "dlczips" / "a.zip", data)
    if header:
        touch(root / DLC / "Headers" / "00000002" / (name + ".header"), b"h" * 0x344)


class CarryDlc(unittest.TestCase):
    def test_copies_missing_packages_and_headers_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "Documents" / "forza", Path(tmp) / "Local" / "ForzaReImagined"
            install(old, "A")
            install(old, "B", data=b"old")
            install(new, "B", header=False, data=b"new")
            self.assertEqual(df.carry_dlc(old, new), 1)
            self.assertEqual(df.dlc_packages(new), {"A", "B"})
            self.assertEqual((new / DLC / "00000002" / "B" / "media" / "dlczips" / "a.zip").read_bytes(), b"new")
            self.assertTrue((new / DLC / "Headers" / "00000002" / "B.header").is_file())   # header carried
            self.assertEqual(df.dlc_without_header(new), [])
            self.assertEqual(df.carry_dlc(old, new), 0)
            self.assertEqual(df.carry_dlc(new, new), 0)
            self.assertEqual(df.dlc_packages(old), {"A", "B"})                               # old untouched

    def test_failed_copy_leaves_no_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "old", Path(tmp) / "new"
            install(old)
            with mock.patch.object(df.shutil, "copytree", side_effect=OSError(28, "No space left on device")):
                self.assertEqual(df.carry_dlc(old, new, log=lambda _t: None), 0)
            self.assertEqual(df.dlc_packages(new), set())


class ShareDlc(unittest.TestCase):
    """The Xenia save's copy (home/xenia-XUID) sees home's DLC through home/xenia-XUID/0000000000000000."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.root = self.home / "xenia-E03000000FB1630A"
        self.root.mkdir(parents=True)
        install(self.home)
        self.log = []

    def tearDown(self):
        self.tmp.cleanup()

    def share(self):
        return game.share_dlc(self.home, self.root, self.log.append)

    def test_fresh_link(self):
        self.assertEqual(self.share(), "linked")
        self.assertEqual(df.dlc_packages(self.root), {RALLY})
        self.assertEqual(self.share(), "linked")

    def test_link_to_a_folder_that_moved_is_repaired(self):
        link = self.root / "0000000000000000"
        target = Path(self.tmp.name) / "Documents" / "forza" / "0000000000000000"
        # Use the launcher's directory-link fallback: Windows junctions need no
        # Developer Mode/admin privilege, unlike os.symlink.
        game._link_dir(link, target)
        self.assertTrue(game._is_link(link), "the fixture must be a broken directory link")
        self.assertEqual(df.dlc_packages(self.root), set())
        self.assertEqual(self.share(), "linked")
        self.assertEqual(df.dlc_packages(self.root), {RALLY})

    def test_real_folder_in_its_place(self):
        # A copy made by a folder move (the link followed) or by hand: its own packages go to home first.
        install(self.root, "OTHER")
        self.assertEqual(self.share(), "linked")
        self.assertEqual(df.dlc_packages(self.home), {RALLY, "OTHER"})
        self.assertEqual(df.dlc_packages(self.root), {RALLY, "OTHER"})
        self.assertTrue((self.root / "0000000000000000.old" / "4D5309C9" / "00000002" / "OTHER").is_dir())

    def test_no_link_possible_mirrors(self):
        # Windows without Developer Mode and without a junction (mklink failed): hard links instead.
        with mock.patch.object(game.os, "symlink", side_effect=OSError(1314, "privilege not held")), \
                mock.patch.object(game.subprocess, "run", return_value=mock.Mock(returncode=1)):
            self.assertEqual(self.share(), "mirrored")
            self.assertEqual(df.dlc_packages(self.root), {RALLY})
            self.assertEqual(df.dlc_without_header(self.root), [])
            install(self.home, "NEW")                          # installed later: mirrored at the next start
            self.assertEqual(self.share(), "mirrored")
            self.assertEqual(df.dlc_packages(self.root), {RALLY, "NEW"})
        self.assertFalse((self.root / "0000000000000000.old").exists())


class Warnings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.home = t / "home"
        self.cfg.advanced["data_root"] = str(self.home)
        (self.home / "runtime").mkdir(parents=True)
        self.install = game.GameInstall(self.home / "runtime", self.home / "runtime" / game.EXE)

    def tearDown(self):
        self.tmp.cleanup()

    def warnings(self, root=None):
        return game.content_warnings(self.cfg, self.install, self.home, root or self.home)

    def test_title_update_chosen_but_not_built(self):
        self.cfg["tu_package"] = str(Path(self.tmp.name) / "tu.zip")
        w = self.warnings()
        self.assertEqual(len(w), 1)
        self.assertIn("Title Update 4", w[0])
        (self.home / "runtime" / "update").mkdir()
        self.assertEqual(self.warnings(), [])

    def test_dlc_installed_but_not_in_the_games_folder(self):
        install(self.home)
        self.assertEqual(self.warnings(), [])
        w = self.warnings(self.home / "xenia-E03000000FB1630A")
        self.assertEqual(len(w), 1)
        self.assertIn("Marketplace", w[0])

    def test_dlc_chosen_but_none_installed(self):
        self.cfg["dlc_dir"] = str(Path(self.tmp.name) / "HorizonDLC")
        self.assertIn("install the DLC again", self.warnings()[0])

    def test_package_without_header(self):
        install(self.home, header=False)
        self.assertIn("no header", self.warnings()[0])

    def test_title_update_without_the_rally_expansion_pack(self):
        (self.home / "runtime" / "update").mkdir()
        install(self.home, "CARS")
        touch(self.home / DLC / "00000002" / "CARS" / "Media" / "1200.puboffer")
        w = self.warnings()
        self.assertEqual(len(w), 1)
        self.assertIn("Rally Expansion Pack", w[0])
        install(self.home)
        touch(self.home / DLC / "00000002" / RALLY / "Media" / "1600.puboffer")
        self.assertEqual(df.dlc_offers(self.home), {"1200", "1600"})
        self.assertEqual(self.warnings(), [])

    def test_launch_plan_links_the_xenia_copy_and_warns_nothing(self):
        xenia = Path(self.tmp.name) / "xenia" / "content"
        touch(xenia / "E03000000FB1630A" / "4D5309C9" / "00000001" / "save")
        self.cfg.d["profile"] = {"mode": "xenia", "xenia_content": str(xenia), "xenia_xuid": ""}
        self.cfg.online["mode"] = "offline"
        install(self.home)
        plan = game.build_plan(self.cfg, self.install, write=True, log=lambda _t: None)
        root = self.home / "xenia-E03000000FB1630A"
        self.assertIn(f"--user_data_root={root}", plan.argv)
        self.assertEqual(df.dlc_packages(root), {RALLY})
        self.assertEqual(plan.notes, [])


if __name__ == "__main__":
    unittest.main()


class LongPaths(unittest.TestCase):
    """#586: a DLC car pack's files nest past Windows' 260-character MAX_PATH."""

    def test_windows_drive_path(self):
        self.assertEqual(str(paths.long_path("E:\\Games\\ForzaReImagined", windows=True)),
                         "\\\\?\\E:\\Games\\ForzaReImagined")

    def test_windows_unc_path(self):
        self.assertEqual(str(paths.long_path("\\\\nas\\share\\forza", windows=True)),
                         "\\\\?\\UNC\\nas\\share\\forza")

    def test_already_long_and_other_systems_unchanged(self):
        self.assertEqual(str(paths.long_path("\\\\?\\E:\\x", windows=True)), "\\\\?\\E:\\x")
        self.assertEqual(paths.long_path("/home/a/b", windows=False), Path("/home/a/b"))

    def test_mirror_and_carry_use_the_long_form(self):
        seen = []
        real = paths.long_path
        with mock.patch.object(paths, "long_path", side_effect=lambda p, windows=None: seen.append(str(p)) or real(p)):
            with tempfile.TemporaryDirectory() as t:
                src, dst = Path(t) / "a", Path(t) / "b"
                (src / "x").mkdir(parents=True)
                (src / "x" / "f.bin").write_bytes(b"1")
                self.assertEqual(game._mirror(src, dst), 1)
        self.assertIn(str(src), seen)
        self.assertIn(str(dst), seen)
