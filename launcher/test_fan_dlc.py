"""The installed DLC is checked for packages that are not the game's own, the fan-made unicorn cars pack
(offer 1975) is named on Setup and Play with a button that moves it out (#66, #76, #115, #771), and the
install-time warning names it too."""
import errno
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import build_env, datafolder, fan_dlc, game, installer
from forza_launcher.fm4cars import PACKAGE as FM4_CARS
from test_xcontent import build as build_stfs

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None

install_dlc = installer.install_dlc
OFFICIAL = sorted(install_dlc.KNOWN_NAMES)[:3]
# A stand-in for the pack, from what the players' logs show of it: offer 1975, its display name in the
# header, Media/DLCZips/stringtables.xml and no zipmount.xml, no zips. (Not the pack itself.)
FAN = "F1975000000000000000000000000000000000004D"
FAN_DISPLAY = "Forza Horizon - Unicorn cars unlock (by Madmatoster)"
UNKNOWN = "DEADBEEF000000000000000000000000000000004D"


def dlc_root(home):
    return Path(home) / "0000000000000000" / "4D5309C9"


def add_package(home, name, display=None, offer=None, media="Media", files=()):
    """An installed package folder (with its header when display is given)."""
    pkg = dlc_root(home) / "00000002" / name
    (pkg / media).mkdir(parents=True)
    if offer is not None:
        (pkg / media / f"{offer}.puboffer").write_bytes(b"")
    for rel, data in files:
        (pkg / rel).parent.mkdir(parents=True, exist_ok=True)
        (pkg / rel).write_bytes(data)
    if display is not None:
        h = dlc_root(home) / "Headers" / "00000002" / (name + ".header")
        h.parent.mkdir(parents=True, exist_ok=True)
        h.write_bytes(install_dlc.header_bytes(name, display, install_dlc.TITLE_ID))
    return pkg


def add_fan(home, name=FAN, media="Media", offer_file="1975.puboffer"):
    pkg = add_package(home, name, FAN_DISPLAY, None, media,
                      [(f"{media}/DLCZips/stringtables.xml", b"<StringTables/>")])
    (pkg / media / offer_file).write_bytes(b"")
    return pkg


def add_official(home):
    for i, name in enumerate(OFFICIAL):
        add_package(home, name, install_dlc.KNOWN_NAMES[name], 1001 + i, files=[("Media/DLCZips/zipmount.xml", b"<x/>")])


class Detection(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = Path(t.name) / "data"

    def test_official_packages_only(self):
        add_official(self.home)
        self.assertEqual(fan_dlc.unrecognized(self.home), [])
        self.assertEqual(fan_dlc.fan_packages(self.home), [])

    def test_the_offer_1975_pack_is_named_first(self):
        add_official(self.home)
        add_package(self.home, UNKNOWN, "Some other pack", 2999)
        add_fan(self.home)
        got = fan_dlc.unrecognized(self.home)
        self.assertEqual([p.name for p in got], [FAN, UNKNOWN])
        self.assertTrue(got[0].fan)
        self.assertFalse(got[1].fan)
        self.assertEqual(got[0].display, FAN_DISPLAY)
        self.assertEqual(got[0].offers, {"1975"})
        text = fan_dlc.fan_text(fan_dlc.fan_packages(self.home))
        self.assertIn(FAN_DISPLAY, text)
        self.assertIn("first Single Player load never finishes", text)
        self.assertIn("fan-made", text)
        self.assertIn("not one of the game's DLC", text)
        self.assertIn(fan_dlc.MOVE_BUTTON, text)
        self.assertIn("nothing is deleted", text)
        self.assertIn("Library > Saves > Edit profile… > Unicorn cars > Add missing unicorns", text)
        other = fan_dlc.unknown_text([p for p in got if not p.fan])
        self.assertIn("Some other pack", other)
        self.assertIn(UNKNOWN, other)
        self.assertNotIn(FAN, other)

    def test_any_case_of_media_and_offer_file(self):   # a copy made on Windows
        add_fan(self.home, media="media", offer_file="1975.PUBOFFER")
        self.assertEqual([p.name for p in fan_dlc.fan_packages(self.home)], [FAN])

    def test_offer_names_are_read_as_the_game_log_reads_them(self):   # atoi, dlc_log.cpp
        for stem, want in (("1975", "1975"), ("01975", "1975"), ("1975x", "1975"), ("+1975", "1975"),
                           ("19750", "19750"), ("x1975", "x1975"), ("", "")):
            self.assertEqual(fan_dlc.offer_number(stem), want, stem)
        add_fan(self.home, offer_file="01975.puboffer")
        self.assertEqual([p.name for p in fan_dlc.fan_packages(self.home)], [FAN])
        self.assertTrue(fan_dlc.is_fan_entries(["Media/01975.puboffer"]))
        self.assertTrue(fan_dlc.is_fan_entries(["/media/1975.PUBOFFER"]))
        self.assertFalse(fan_dlc.is_fan_entries(["Media/19750.puboffer", "Media/x/1975.puboffer"]))

    def test_a_header_named_in_another_case_gives_the_name(self):   # a copy made on Windows
        add_fan(self.home)
        h = dlc_root(self.home) / "Headers" / "00000002" / (FAN + ".header")
        h.rename(h.with_name(FAN.lower() + ".HEADER"))
        (got,) = fan_dlc.fan_packages(self.home)
        self.assertEqual(got.display, FAN_DISPLAY)

    def test_offer_1975_under_an_official_name_is_still_the_pack(self):
        add_fan(self.home, name=OFFICIAL[0].lower())
        self.assertEqual([p.name for p in fan_dlc.fan_packages(self.home)], [OFFICIAL[0].lower()])

    def test_our_own_packages_are_not_unrecognized(self):
        add_official(self.home)
        add_package(self.home, FM4_CARS, "Forza Motorsport 4 cars", 64800)
        self.assertEqual(fan_dlc.unrecognized(self.home), [])
        add_package(self.home, FM4_CARS.lower() + "X", None, 64800)   # not our folder name: named
        self.assertEqual(len(fan_dlc.unrecognized(self.home)), 1)

    def test_empty_or_missing_dlc_folder(self):
        self.assertEqual(fan_dlc.unrecognized(self.home), [])          # no data folder at all
        (dlc_root(self.home) / "00000002").mkdir(parents=True)
        self.assertEqual(fan_dlc.unrecognized(self.home), [])
        (dlc_root(self.home) / "00000002" / "stray.txt").write_text("not a package")
        (dlc_root(self.home) / "00000002" / (FAN + ".part")).mkdir()   # an unfinished copy
        self.assertEqual(fan_dlc.unrecognized(self.home), [])

    def test_unreadable_header_still_finds_the_pack(self):
        add_fan(self.home)
        h = dlc_root(self.home) / "Headers" / "00000002" / (FAN + ".header")
        h.write_bytes(b"\0" * 10)   # cut short
        (got,) = fan_dlc.fan_packages(self.home)
        self.assertEqual(got.display, FAN)
        self.assertEqual(got.label(), FAN)
        h.unlink()
        h.mkdir()                   # not a file at all
        (got,) = fan_dlc.fan_packages(self.home)
        self.assertEqual(got.display, FAN)

    def test_unreadable_package_folder_is_named_without_offers(self):
        pkg = add_package(self.home, UNKNOWN, "Odd pack", 1975)
        real = os.scandir

        def scandir(p="."):
            if Path(p) == pkg:
                raise PermissionError("denied")
            return real(p)
        with patch.object(fan_dlc.os, "scandir", side_effect=scandir):
            (got,) = fan_dlc.unrecognized(self.home)
        self.assertEqual((got.name, got.fan), (UNKNOWN, False))


class MoveOut(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = Path(t.name) / "data"
        add_official(self.home)
        add_fan(self.home)
        self.src = dlc_root(self.home) / "00000002" / FAN
        self.src_hdr = dlc_root(self.home) / "Headers" / "00000002" / (FAN + ".header")
        self.header = self.src_hdr.read_bytes()

    def assert_in_place(self):
        self.assertTrue((self.src / "Media" / "1975.puboffer").is_file())
        self.assertEqual(self.src_hdr.read_bytes(), self.header)
        self.assertEqual([p.name for p in fan_dlc.fan_packages(self.home)], [FAN])

    def test_moved_out_next_to_the_installed_dlc_and_back(self):
        root = fan_dlc.move_out(self.home, FAN)
        self.assertEqual(root, dlc_root(self.home) / fan_dlc.MOVED)
        self.assertFalse(self.src.exists())
        self.assertFalse(self.src_hdr.exists())
        self.assertEqual((root / "00000002" / FAN / "Media/DLCZips/stringtables.xml").read_bytes(), b"<StringTables/>")
        self.assertEqual((root / "Headers" / "00000002" / (FAN + ".header")).read_bytes(), self.header)
        self.assertEqual(fan_dlc.unrecognized(self.home), [])
        self.assertEqual(datafolder.dlc_packages(self.home), set(OFFICIAL))   # the game no longer sees it
        text = fan_dlc.moved_text([fan_dlc.Package(FAN, FAN_DISPLAY, frozenset({"1975"}))], root, self.home)
        self.assertIn(str(root), text)
        self.assertIn("nothing was deleted", text)
        self.assertIn("Install DLC", text)
        # Setup > Install DLC with that folder puts it back, header and all.
        installer._DLC_CACHE.clear()
        self.assertEqual(installer.install_dlc_folder(str(root), self.home, lambda *_: None, log=lambda _l: None), 1)
        self.assert_in_place()

    def test_a_name_clash_at_the_destination_uses_a_new_folder(self):
        taken = dlc_root(self.home) / fan_dlc.MOVED / "00000002" / FAN
        taken.mkdir(parents=True)
        (taken / "keep.bin").write_bytes(b"older copy")
        root = fan_dlc.move_out(self.home, FAN)
        self.assertEqual(root.name, fan_dlc.MOVED + " (2)")
        self.assertEqual((taken / "keep.bin").read_bytes(), b"older copy")
        self.assertTrue((root / "00000002" / FAN / "Media" / "1975.puboffer").is_file())
        # a header of that name there counts as a clash too
        add_fan(self.home)
        hdr = dlc_root(self.home) / (fan_dlc.MOVED + " (3)") / "Headers" / "00000002" / (FAN + ".header")
        hdr.parent.mkdir(parents=True)
        hdr.write_bytes(b"older header")
        self.assertEqual(fan_dlc.move_out(self.home, FAN).name, fan_dlc.MOVED + " (4)")
        self.assertEqual(hdr.read_bytes(), b"older header")

    def test_a_folder_that_cannot_move_leaves_everything_in_place(self):
        with patch.object(fan_dlc.os, "rename", side_effect=PermissionError("in use")):
            with self.assertRaises(fan_dlc.MoveError) as e:
                fan_dlc.move_out(self.home, FAN)
        self.assertIn("Nothing was moved", str(e.exception))
        self.assertIn("in use", str(e.exception))
        self.assert_in_place()
        self.assertFalse((dlc_root(self.home) / fan_dlc.MOVED).exists())   # no empty folders left

    def test_another_drive_says_to_move_it_by_hand(self):   # 00000002 is a link to another drive
        with patch.object(fan_dlc.os, "rename", side_effect=OSError(errno.EXDEV, "Invalid cross-device link")):
            with self.assertRaises(fan_dlc.MoveError) as e:
                fan_dlc.move_out(self.home, FAN)
        msg = str(e.exception)
        self.assertIn("another drive", msg)
        self.assertIn("by hand", msg)
        self.assertNotIn("close the game", msg)
        self.assert_in_place()
        self.assertFalse((dlc_root(self.home) / fan_dlc.MOVED).exists())

    def test_a_header_named_in_another_case_moves_with_it(self):
        other = self.src_hdr.with_name(FAN.lower() + ".HEADER")
        self.src_hdr.rename(other)
        if self.src_hdr.exists():
            self.skipTest("file names here are not case-sensitive")
        root = fan_dlc.move_out(self.home, FAN)
        self.assertFalse(other.exists())
        self.assertEqual((root / "Headers" / "00000002" / other.name).read_bytes(), self.header)

    def test_a_folder_not_named_in_hex_is_restored_by_hand(self):
        # The installer names a folder after the file it came from; Install DLC skips other names.
        name = "Unicorn cars unlock"
        add_fan(self.home, name=name)
        root = fan_dlc.move_out(self.home, name)
        text = fan_dlc.moved_text([fan_dlc.Package(name, FAN_DISPLAY, frozenset({"1975"}))], root, self.home)
        self.assertNotIn("press Install DLC", text)
        self.assertIn("Install DLC does not take a folder of that name", text)
        self.assertIn(f"move {name} back into", text)
        hex_text = fan_dlc.moved_text([fan_dlc.Package(FAN, FAN_DISPLAY, frozenset({"1975"}))], root, self.home)
        self.assertIn("press Install DLC", hex_text)
        installer._DLC_CACHE.clear()
        self.addCleanup(installer._DLC_CACHE.clear)
        with self.assertRaises(Exception):   # what the text no longer offers
            installer.install_dlc_folder(str(root), self.home, lambda *_: None, log=lambda _l: None)

    def test_a_header_that_cannot_move_puts_the_folder_back(self):
        real = os.rename
        calls = []

        def rename(a, b):
            calls.append((a, b))
            if str(a).endswith(".header"):
                raise PermissionError("in use")
            return real(a, b)
        with patch.object(fan_dlc.os, "rename", side_effect=rename):
            with self.assertRaises(fan_dlc.MoveError) as e:
                fan_dlc.move_out(self.home, FAN)
        self.assertIn("Nothing was moved", str(e.exception))
        self.assertEqual(len(calls), 3)   # the folder out, the header (fails), the folder back
        self.assert_in_place()
        self.assertFalse((dlc_root(self.home) / fan_dlc.MOVED).exists())

    def test_a_failed_put_back_says_where_everything_is(self):
        real = os.rename
        calls = []

        def rename(a, b):
            calls.append(a)
            if len(calls) > 1:
                raise PermissionError("in use")
            return real(a, b)
        with patch.object(fan_dlc.os, "rename", side_effect=rename):
            with self.assertRaises(fan_dlc.MoveError) as e:
                fan_dlc.move_out(self.home, FAN)
        msg = str(e.exception)
        self.assertIn("could not be put back", msg)
        self.assertIn(str(dlc_root(self.home) / fan_dlc.MOVED / "00000002"), msg)
        self.assertTrue((dlc_root(self.home) / fan_dlc.MOVED / "00000002" / FAN).is_dir())
        self.assertTrue(self.src_hdr.is_file())

    def test_a_package_without_header_moves(self):
        self.src_hdr.unlink()
        root = fan_dlc.move_out(self.home, FAN)
        self.assertTrue((root / "00000002" / FAN).is_dir())
        self.assertFalse((root / "Headers").exists())

    def test_a_package_that_is_gone(self):
        with self.assertRaises(fan_dlc.MoveError):
            fan_dlc.move_out(self.home, UNKNOWN)
        self.assert_in_place()


XUID = "E000000000000001"


class XeniaCopy(unittest.TestCase):
    """A Xenia save's root (xenia-<XUID>) whose 0000000000000000 is a copy of the installed DLC: the
    launcher mirrors it when it cannot link it (game.share_dlc)."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = Path(t.name) / "data"
        self.root = self.home / f"xenia-{XUID}"
        self.root.mkdir(parents=True)
        add_official(self.home)
        add_fan(self.home)

    def share(self, link=False):
        if link:
            return game.share_dlc(self.home, self.root, log=lambda _l: None)
        with patch.object(game, "_link_dir"):   # no link can be made
            return game.share_dlc(self.home, self.root, log=lambda _l: None)

    def test_the_copy_loses_the_pack_too_and_the_next_play_keeps_it_out(self):
        self.assertEqual(self.share(), "mirrored")
        self.assertIn(FAN, datafolder.dlc_packages(self.root))
        self.assertEqual(fan_dlc.mirrors(self.home), [self.root])
        root = fan_dlc.move_out(self.home, FAN)
        for r in (self.home, self.root):
            self.assertEqual(datafolder.dlc_packages(r), set(OFFICIAL), r)
        self.assertTrue((root / "00000002" / FAN).is_dir())
        self.assertTrue((self.root / datafolder.DLC_DIR / fan_dlc.MOVED / "00000002" / FAN).is_dir())
        self.assertEqual(self.share(), "mirrored")   # the next Play
        for r in (self.home, self.root):
            self.assertEqual(datafolder.dlc_packages(r), set(OFFICIAL), r)
        self.assertEqual(fan_dlc.fan_packages(self.home), [])

    def test_a_copy_that_cannot_move_leaves_the_installed_pack(self):
        self.share()
        real = os.rename

        def rename(a, b):
            if Path(a).is_relative_to(self.root):
                raise PermissionError("in use")
            return real(a, b)
        with patch.object(fan_dlc.os, "rename", side_effect=rename):
            with self.assertRaises(fan_dlc.MoveError) as e:
                fan_dlc.move_out(self.home, FAN)
        self.assertIn(f"xenia-{XUID}", str(e.exception))
        self.assertIn(FAN, datafolder.dlc_packages(self.home))   # the notice stays
        self.assertIn(FAN, datafolder.dlc_packages(self.root))

    def test_a_linked_root_is_not_a_copy(self):
        if self.share(link=True) != "linked":
            self.skipTest("no links here")
        self.assertEqual(fan_dlc.mirrors(self.home), [])
        fan_dlc.move_out(self.home, FAN)
        self.assertEqual(datafolder.dlc_packages(self.root), set(OFFICIAL))
        self.assertEqual(self.share(link=True), "linked")
        self.assertEqual(datafolder.dlc_packages(self.home), set(OFFICIAL))


class CarryDlc(unittest.TestCase):
    """datafolder.carry_dlc (a Xenia copy into the installed DLC; on Windows, Documents\\forza into the
    launcher's folder when it has no DLC) leaves out a package that was moved out."""

    def test_a_moved_out_package_is_not_copied_back(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        old, new = Path(t.name) / "Documents-forza", Path(t.name) / "data"
        add_official(old)
        add_fan(old)
        add_fan(new)
        fan_dlc.move_out(new, FAN)   # the player's only package, moved out: no DLC installed
        self.assertEqual(datafolder.dlc_packages(new), set())
        logs = []
        self.assertEqual(datafolder.carry_dlc(old, new, logs.append), len(OFFICIAL))
        self.assertEqual(datafolder.dlc_packages(new), set(OFFICIAL))
        self.assertFalse((dlc_root(new) / "Headers" / "00000002" / (FAN + ".header")).exists())
        self.assertTrue(any("left out" in line and FAN in line for line in logs), logs)
        # in a "DLC moved out (2)" folder, or in another case, too
        (dlc_root(new) / fan_dlc.MOVED).rename(dlc_root(new) / (fan_dlc.MOVED + " (2)"))
        self.assertEqual(datafolder.dlc_moved_out(new), {FAN.upper()})
        add_fan(old, name=UNKNOWN.lower())
        add_fan(new, name=UNKNOWN)
        fan_dlc.move_out(new, UNKNOWN)
        self.assertEqual(datafolder.carry_dlc(old, new), 0)
        self.assertEqual(datafolder.dlc_packages(new), set(OFFICIAL))


class InstallTime(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        installer._DLC_CACHE.clear()
        self.addCleanup(installer._DLC_CACHE.clear)

    def check(self, warning, label):
        self.assertTrue(warning.startswith("Unrecognized DLC packages:\n- " + label), warning)
        self.assertIn("first Single Player load never finishes", warning)
        self.assertIn("fan-made", warning)
        self.assertIn("Add missing unicorns", warning)
        self.assertIn("You can still install them", warning)

    def test_warning_names_the_pack_first(self):
        found = [(UNKNOWN, "Other"), (FAN, FAN_DISPLAY)]
        self.check(installer.dlc_warning(found, {FAN.lower()}), f"{FAN_DISPLAY} ({FAN})")
        self.assertNotIn("fan-made", installer.dlc_warning(found))
        self.assertEqual(installer.dlc_warning([(OFFICIAL[0], "x")], ()), "")

    def test_extracted_pack_is_named_and_still_installed(self):
        source = self.root / "content"
        add_official(source)
        add_fan(source)
        found, _ = installer.describe_dlc_folder(str(source))
        _key, got = installer.dlc_snapshot(str(source))
        self.assertEqual(got[2], {FAN})
        warning = installer.dlc_warning(found, got[2])
        self.check(warning, f"{FAN_DISPLAY} ({FAN})")
        logs = []
        data = self.root / "data"
        n = installer.install_dlc_folder(str(source), data, lambda *_: None, log=logs.append)
        self.assertEqual(n, len(OFFICIAL) + 1)
        self.assertIn(warning, logs)
        self.assertEqual([p.name for p in fan_dlc.fan_packages(data)], [FAN])

    def test_stfs_pack_is_named(self):
        source = self.root / "input"
        source.mkdir()
        data = bytearray(build_stfs(b"", name="Media/1975.puboffer"))
        struct.pack_into(">I", data, 0x344, install_dlc.MARKETPLACE)
        struct.pack_into(">I", data, 0x360, install_dlc.TITLE_ID)
        text = FAN_DISPLAY.encode("utf-16-be")
        data[0x411:0x411 + len(text)] = text
        (source / FAN).write_bytes(data)
        (source / UNKNOWN).write_bytes(bytearray(build_stfs(b"x")))
        found, _ = installer.describe_dlc_folder(str(source))
        self.assertEqual(installer.dlc_snapshot(str(source))[1][2], {FAN})
        self.check(installer.dlc_warning(found, {FAN}), f"{FAN_DISPLAY} ({FAN})")


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Notices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.window import MainWindow
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.home = self.root / "data"
        installer._DLC_CACHE.clear()
        self.addCleanup(installer._DLC_CACHE.clear)
        build_env.forget_tools()
        self.addCleanup(build_env.forget_tools)
        for p in (patch("forza_launcher.paths.config_dir", return_value=self.root),
                  patch.object(build_env, "_detect_tools", return_value=build_env.Tools())):
            p.start()
            self.addCleanup(p.stop)
        add_official(self.home)
        add_package(self.home, FM4_CARS, "Forza Motorsport 4 cars", 64800)
        cfg = Config(self.root / "launcher.json")
        cfg.gpu["checked"] = True
        cfg.loaded = True
        cfg.d["check_updates"] = False
        cfg.advanced["data_root"] = str(self.home)
        self.win = MainWindow(cfg)
        self.addCleanup(self.win.close)
        self.settle()

    def settle(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not self.win.setup._probes:
                return
            time.sleep(.005)

    def refresh(self):
        self.win.play.refresh()
        self.win.setup.refresh()
        self.settle()

    def test_official_and_our_own_packages_show_nothing(self):
        self.refresh()
        self.assertTrue(self.win.play.fan_dlc.isHidden())
        self.assertTrue(self.win.setup.fan_dlc.isHidden())
        self.assertTrue(self.win.setup.dlcinstalled.isHidden())

    def test_the_pack_is_named_on_play_and_setup_and_moved_out(self):
        add_fan(self.home)
        add_package(self.home, UNKNOWN, "Some other pack", 2999)
        self.refresh()
        play, setup = self.win.play, self.win.setup
        for notice in (play.fan_dlc, setup.fan_dlc):
            self.assertFalse(notice.isHidden())
            self.assertIn(FAN_DISPLAY, notice.text.text())
            self.assertIn("first Single Player load never finishes", notice.text.text())
            self.assertEqual(notice.move.text(), fan_dlc.MOVE_BUTTON)
        self.assertIn("fan-made pack", play.r_dlc.detail.text())
        self.assertFalse(setup.dlcinstalled.isHidden())
        self.assertIn("Some other pack", setup.dlcinstalled.text())
        self.assertNotIn(FAN_DISPLAY, setup.dlcinstalled.text())
        with patch.object(self.win, "running", return_value=True), \
                patch.object(QMessageBox, "information") as info:
            play.fan_dlc.move.click()
            info.assert_called_once()
        self.assertTrue((dlc_root(self.home) / "00000002" / FAN).is_dir())   # not while the game runs
        play.fan_dlc.move.click()
        self.assertFalse((dlc_root(self.home) / "00000002" / FAN).exists())
        self.assertTrue((dlc_root(self.home) / fan_dlc.MOVED / "00000002" / FAN).is_dir())
        self.assertTrue(play.fan_dlc.isHidden())
        self.assertTrue(setup.fan_dlc.isHidden())
        self.assertIn(str(dlc_root(self.home) / fan_dlc.MOVED), play.warn.text() + play.msg.text())

    def test_not_while_a_setup_task_changes_the_data_folder(self):
        add_fan(self.home)
        self.refresh()
        notice = self.win.play.fan_dlc
        with patch.object(self.win.setup, "busy", return_value=True), \
                patch.object(QMessageBox, "information") as info:
            notice.move.click()
        info.assert_called_once()
        self.assertIn("Wait for the task", info.call_args[0][2])
        self.win._busy = True
        self.addCleanup(setattr, self.win, "_busy", False)
        with patch.object(QMessageBox, "information") as info:
            notice.move.click()
        info.assert_called_once()
        self.assertTrue((dlc_root(self.home) / "00000002" / FAN).is_dir())
        self.assertFalse(notice.isHidden())

    def test_the_installed_dlc_is_read_again_at_the_click(self):
        add_fan(self.home)
        self.refresh()
        gone = self.root / "elsewhere"
        (dlc_root(self.home) / "00000002" / FAN).rename(gone)   # moved by hand after the notice showed
        with patch.object(QMessageBox, "warning") as warn:
            self.win.play.fan_dlc.move.click()
        warn.assert_not_called()
        self.assertTrue(gone.is_dir())
        self.assertFalse((dlc_root(self.home) / fan_dlc.MOVED).exists())
        self.assertTrue(self.win.play.fan_dlc.isHidden())

    def test_a_failed_move_is_shown_and_nothing_moves(self):
        add_fan(self.home)
        self.refresh()
        with patch.object(fan_dlc.os, "rename", side_effect=PermissionError("in use")), \
                patch.object(QMessageBox, "warning") as warn:
            self.win.setup.fan_dlc.move.click()
        warn.assert_called_once()
        self.assertIn("Nothing was moved", warn.call_args[0][2])
        self.assertTrue((dlc_root(self.home) / "00000002" / FAN).is_dir())
        self.assertFalse(self.win.setup.fan_dlc.isHidden())
        self.assertEqual(self.win.setup.msg2.text(), "")


if __name__ == "__main__":
    unittest.main()
