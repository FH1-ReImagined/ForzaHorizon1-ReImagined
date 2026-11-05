"""Import FM4 cars (#648): publishing, removal and the disc checks, with a stand-in converter
and synthetic files (no game file is read)."""
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import fm4cars, mods  # noqa: E402
from forza_launcher.config import Config  # noqa: E402


def xex(title):
    """A minimal XEX2 header with the execution info (title id) xexcheck.header_info reads."""
    head = bytearray(0x100)
    head[:4] = b"XEX2"
    struct.pack_into(">I", head, 0x14, 1)
    struct.pack_into(">II", head, 0x18, 0x40006, 0x40)
    struct.pack_into(">IIII", head, 0x40, 0x26FF1101, 8, 8, title)
    return bytes(head)


def package(path, title, content_type, name="", svod=False, size=0):
    """A synthetic Xbox 360 content package header (CON/LIVE/PIRS): what fm4cars and
    xcontent_extract read of it, nothing else."""
    raw = bytearray(0xA000)
    raw[:4] = b"LIVE" if svod else b"PIRS"
    struct.pack_into(">I", raw, 0x344, content_type)
    struct.pack_into(">I", raw, 0x360, title)
    raw[0x411:0x411 + 2 * len(name)] = name.encode("utf-16-be")
    if svod:
        raw[0x379 + 0x18] = 0x40  # enhanced GDF: the volume descriptor at 0x2000 of Data0000
        struct.pack_into(">I", raw, 0x39D, 1)
        struct.pack_into(">I", raw, 0x3A9, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(raw))
    if size:
        with open(path, "r+b") as f:
            f.truncate(size)
    return path


def volume(files, first_block):
    """XDVDFS sectors ({block: bytes}, root block, root size) holding files ({"a/b": bytes})."""
    tree = {}
    for path, data in files.items():
        node = tree
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = data
    sectors, free = {}, [first_block]

    def alloc(n):
        free[0] += max(1, n)
        return free[0] - max(1, n)

    def put_dir(node):
        names = sorted(node)
        offs, off = [], 0
        for n in names:
            offs.append(off)
            off += (14 + len(n) + 3) & ~3
        block, buf = alloc(1), bytearray(b"\xff" * 0x800)
        for i, n in enumerate(names):
            child = node[n]
            if isinstance(child, dict):
                (start, length), attr = put_dir(child), 0x10
            else:
                length, attr = len(child), 0x80
                start = alloc((length + 0x7FF) // 0x800)
                for k in range(0, length, 0x800):
                    sectors[start + k // 0x800] = child[k:k + 0x800]
            right = offs[i + 1] // 4 if i + 1 < len(names) else 0
            struct.pack_into("<HHIIBB", buf, offs[i], 0, right, start, length, attr, len(n))
            buf[offs[i] + 14:offs[i] + 14 + len(n)] = n.encode()
        sectors[block] = bytes(buf)
        return block, off
    root, size = put_dir(tree)
    return sectors, root, size


MEDIA = b"MICROSOFT*XBOX*MEDIA"


def iso(path, files):
    """A plain XDVDFS disc image (volume descriptor at sector 32)."""
    sectors, root, size = volume(files, 33)
    raw = bytearray((max(sectors) + 1) * 0x800)
    raw[0x10000:0x10000 + 28] = MEDIA + struct.pack("<II", root, size)
    for b, data in sectors.items():
        raw[b * 0x800:b * 0x800 + len(data)] = data
    path.write_bytes(bytes(raw))
    return path


def god(folder, title, files, name="FM4", file="ABCDEF0123456789"):
    """A Games on Demand copy: <title>/00007000/<package> and its <package>.data/Data0000."""
    from xcontent_extract import Svod
    pkg = package(folder / f"{title:08X}" / "00007000" / file, title, 0x7000, name, svod=True)
    sectors, root, size = volume(files, 1)
    layout = Svod.__new__(Svod)
    layout.enhanced_gdf, layout.start_block, layout.single_file = True, 0, False
    raw = bytearray(0x3000 + (max(sectors) + 3) * 0x800)
    raw[0x2000:0x2000 + 28] = MEDIA + struct.pack("<II", root, size)
    for b, data in sectors.items():
        _, addr = layout.block_to_offset(b)
        raw[addr:addr + len(data)] = data
    data = pkg.with_name(pkg.name + ".data")
    data.mkdir()
    (data / "Data0000").write_bytes(bytes(raw))
    return pkg


DISC1 = {"default.xex": xex(fm4cars.FM4_TITLE), "Media/db/gamedb.slt": b"db", "Media/cars/a.zip": b"PK"}
DISC2 = {"default.xex": xex(fm4cars.FM4_TITLE), "media/x.bin": b"x",
         "content/0000000000000000/4d530910/00000002/4d53091000000001": b"pack"}


class FakeConverter:
    runs = []

    def __init__(self, fm4, fh1, out, package=None):
        self.out = Path(out)
        self.package = Path(package)

    def candidates(self):
        return [(1119, "BUG_Veyron_09", None), (1500, "XYZ_Thing_10", "its make is not in FH1")]

    def run(self, ids, progress=None, cancelled=lambda: False):
        FakeConverter.runs.append(list(ids))
        (self.out / "media/cars/BUG_Veyron_09").mkdir(parents=True)
        (self.out / "media/cars/BUG_Veyron_09/carattribs.xml").write_text("<CarAttributes/>")
        (self.out / "mod.toml").write_text('name = "FM4 cars"\n')
        (self.package / "Media/DLCZips").mkdir(parents=True)
        (self.package / "Media/DLCZips/fm4cars1_pri_60.zip").write_bytes(b"PK")
        header = self.package.parent.parent / "Headers/00000002" / (self.package.name + ".header")
        header.parent.mkdir(parents=True)
        header.write_bytes(b"h")
        return {"cars": [(1119, "BUG_Veyron_09", 8, 872)], "skipped": [(1500, "XYZ_Thing_10: no model")]}


class _Setup:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.disc1 = t / "fm4"
        (self.disc1 / "Media/db").mkdir(parents=True)
        (self.disc1 / "default.xex").write_bytes(xex(fm4cars.FM4_TITLE))
        (self.disc1 / "Media/db/gamedb.slt").write_bytes(b"")
        game = t / "game/media"
        game.mkdir(parents=True)
        self.cfg.d.setdefault("setup", {})
        FakeConverter.runs = []
        self.patches = [patch.object(fm4cars, "_update_root", lambda cfg: None),
                        patch.object(Config, "game_files", lambda self_: t / "game")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def build(self, **kw):
        return fm4cars.build(self.cfg, self.disc1, None, converter=FakeConverter, **kw)


class Fm4ImportTests(_Setup, unittest.TestCase):

    def test_publishes_the_mod_enabled_last_and_removes_it(self):
        msg = self.build()
        self.assertIn("Added 1", msg)
        self.assertIn("1 could not be converted", msg)
        self.assertEqual(FakeConverter.runs, [[1119]])
        target = mods.mods_dir(self.cfg) / fm4cars.MOD
        self.assertTrue((target / "media/cars/BUG_Veyron_09/carattribs.xml").is_file())
        self.assertEqual(fm4cars.status(self.cfg)["cars"], ["BUG_Veyron_09"])
        self.assertEqual(self.cfg.d["mods"]["order"][-1], fm4cars.MOD)
        self.assertNotIn(fm4cars.MOD, self.cfg.d["mods"]["disabled"])
        self.assertFalse(fm4cars.stale(self.cfg))
        # nothing left behind in the mods folder but the mod
        self.assertEqual([p.name for p in mods.mods_dir(self.cfg).iterdir()], [fm4cars.MOD])
        package, header = fm4cars._package(self.cfg)
        self.assertTrue((package / "Media/DLCZips/fm4cars1_pri_60.zip").is_file())
        self.assertTrue(header.is_file())
        self.assertFalse(fm4cars.stale(self.cfg))  # its own package is not DLC
        fm4cars.remove(self.cfg)
        self.assertFalse(target.exists())
        self.assertFalse(package.exists())
        self.assertFalse(header.exists())
        self.assertIsNone(fm4cars.status(self.cfg))

    def test_a_second_import_replaces_the_first(self):
        self.build()
        self.build()
        self.assertEqual(len(FakeConverter.runs), 2)
        self.assertEqual([p.name for p in mods.mods_dir(self.cfg).iterdir()], [fm4cars.MOD])
        package, _ = fm4cars._package(self.cfg)
        self.assertEqual([p.name for p in package.parent.iterdir()], [package.name])

    def test_refuses_another_disc(self):
        (self.disc1 / "default.xex").write_bytes(xex(0x4D5309C9))
        with self.assertRaises(fm4cars.Fm4Error):
            self.build()
        (self.disc1 / "default.xex").unlink()
        with self.assertRaises(fm4cars.Fm4Error):
            self.build()

    def test_never_takes_over_a_players_folder(self):
        own = mods.mods_dir(self.cfg) / fm4cars.MOD
        own.mkdir(parents=True)
        (own / "mine.txt").write_text("keep")
        with self.assertRaises(fm4cars.Fm4Error):
            self.build()
        self.assertEqual((own / "mine.txt").read_text(), "keep")

    def test_cancel_keeps_the_previous_import(self):
        self.build()
        from forza_launcher.fetch import Cancelled

        class Cancelling(FakeConverter):
            def run(self, ids, progress=None, cancelled=lambda: False):
                import fm4_cars
                raise fm4_cars.Cancelled()
        with self.assertRaises(Cancelled):
            fm4cars.build(self.cfg, self.disc1, None, converter=Cancelling)
        self.assertEqual(fm4cars.status(self.cfg)["cars"], ["BUG_Veyron_09"])

    def test_package_name_matches_the_tool(self):
        self.assertEqual(fm4cars.PACKAGE, fm4cars._tools().PACK_NAME)

    def test_stale_after_dlc_changes(self):
        self.build()
        d = Path(self.cfg.data_home()) / "0000000000000000/4D5309C9/00000002/PACK"
        d.mkdir(parents=True)
        self.assertTrue(fm4cars.stale(self.cfg))


class Fm4ChoiceTests(unittest.TestCase):
    """What the player chose for a disc: accepted, or named in plain words (#681)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        fm4cars.paths.ensure_tools_importable()

    def prepare(self, disc1, disc2=None):
        work = self.t / "work"
        shutil.rmtree(work, ignore_errors=True)
        return fm4cars.prepare(disc1, disc2, work, lambda n, total, text: None, lambda: False)

    def refused(self, disc1, disc2=None):
        with self.assertRaises(fm4cars.Fm4Error) as e:
            self.prepare(disc1, disc2)
        return str(e.exception)

    def test_another_games_package_renamed_to_iso_is_named(self):
        pkg = package(self.t / "4D5309C900003061.iso", 0x4D5309C9, 0x2, "Some Car Pack")
        msg = self.refused(pkg)
        self.assertIn("is a Forza Horizon content package (DLC) (\"Some Car Pack\")", msg)
        self.assertIn("Choose your FM4 Disc 1", msg)
        package(pkg, 0x4D5309C9, 0xB0000)
        self.assertIn("a Forza Horizon title update", self.refused(pkg))
        package(pkg, 0x12345678, 0x7000, svod=True)
        self.assertIn("a Games on Demand package of another game (title id 12345678)", self.refused(pkg))

    def test_an_fm4_car_pack_as_disc_1_points_to_disc_2(self):
        pkg = package(self.t / "4d53091000000001", fm4cars.FM4_TITLE, 0x2, "Car Pack")
        self.assertIn("one of Forza Motorsport 4's Disc 2 car packs", self.refused(pkg))

    def test_an_unknown_file_with_a_content_name_says_so(self):
        f = self.t / "4D5309C900003061.iso"
        f.write_bytes(b"\0" * 0x20000)
        msg = self.refused(f)
        self.assertIn("is not an Xbox 360 disc image, Games on Demand package or content package", msg)
        self.assertIn("a Forza Horizon content package has (4D5309C9", msg)
        f = self.t / "notes.txt"
        f.write_bytes(b"hello")
        self.assertNotIn("Its name", self.refused(f))

    def test_an_xex_is_named_and_fm4s_own_uses_its_folder(self):
        f = self.t / "default.xex"
        f.write_bytes(xex(0x4D5309C9))
        self.assertIn("is a Forza Horizon program file (XEX)", self.refused(f))
        for path, data in DISC1.items():
            (self.t / path).parent.mkdir(parents=True, exist_ok=True)
            (self.t / path).write_bytes(data)
        self.assertEqual(self.prepare(f), (self.t, None))

    def test_a_disc_image_of_another_game_is_named_before_the_copy(self):
        img = iso(self.t / "fh1.iso", {**DISC1, "default.xex": xex(0x4D5309C9)})
        msg = self.refused(img)
        self.assertIn("This is a Forza Horizon disc, not Forza Motorsport 4's Disc 1", msg)
        self.assertFalse((self.t / "work/disc1/Media").exists())

    def test_fm4_disc_1_image(self):
        disc1, disc2 = self.prepare(iso(self.t / "d1.iso", DISC1))
        self.assertEqual((disc1 / "Media/db/gamedb.slt").read_bytes(), b"db")
        self.assertEqual((disc1 / "Media/cars/a.zip").read_bytes(), b"PK")
        self.assertIsNone(disc2)

    def test_disc_2_chosen_as_disc_1(self):
        for chosen in (iso(self.t / "d2.iso", DISC2), god(self.t / "god", fm4cars.FM4_TITLE, DISC2)):
            self.assertIn("This is Forza Motorsport 4's Disc 2", self.refused(chosen))
        folder = self.t / "d2"
        for path, data in DISC2.items():
            (folder / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / path).write_bytes(data)
        self.assertIn("This is Forza Motorsport 4's Disc 2", self.refused(folder))

    def test_disc_1_chosen_as_disc_2(self):
        d1 = iso(self.t / "d1.iso", DISC1)
        self.assertIn("has no Forza Motorsport 4 car packs: is it Disc 1?", self.refused(d1, d1))

    def test_fm4_games_on_demand_copy_however_it_is_picked(self):
        pkg = god(self.t / "Content/0000000000000000", fm4cars.FM4_TITLE, DISC1)
        data = pkg.with_name(pkg.name + ".data")
        for chosen in (pkg, data, data / "Data0000", pkg.parent, pkg.parent.parent, pkg.parent.parent.parent):
            disc1, _ = self.prepare(chosen)
            self.assertEqual((disc1 / "Media/db/gamedb.slt").read_bytes(), b"db", chosen)
        shutil.rmtree(data)
        self.assertIn("folder with its data is not next to it", self.refused(pkg))

    def test_a_two_disc_games_on_demand_copy_uses_disc_1(self):
        god(self.t / "god", fm4cars.FM4_TITLE, DISC2, file="0000000000000001")
        pkg = god(self.t / "god", fm4cars.FM4_TITLE, DISC1, file="0000000000000002")
        disc1, _ = self.prepare(pkg.parent)
        self.assertEqual((disc1 / "Media/db/gamedb.slt").read_bytes(), b"db")

    def test_another_games_on_demand_copy_is_named(self):
        pkg = god(self.t / "god", 0x4D5309C9, {**DISC1, "default.xex": xex(0x4D5309C9)}, "Forza Horizon")
        self.assertIn("is a Forza Horizon Games on Demand package (\"Forza Horizon\")", self.refused(pkg))

    def test_car_packs_are_found_by_their_header(self):
        folder = self.t / "packs"
        package(folder / "a/hashname", fm4cars.FM4_TITLE, 0x2, size=2 << 20)
        package(folder / "a/fh1pack", 0x4D5309C9, 0x2, size=2 << 20)
        package(folder / "a/small", fm4cars.FM4_TITLE, 0x2)
        self.assertEqual([p.name for p in fm4cars._car_packs(folder)], ["hashname"])


try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Fm4CardTests(_Setup, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_card_reports_and_removing_the_mod_removes_the_package(self):
        from forza_launcher.ui.mods_page import ModsPage
        self.build()
        page = ModsPage(self.cfg, window=None)
        try:
            self.assertIn("1 FM4 car(s) imported", page.fm4_message.text())
            self.assertTrue(page.fm4_remove.isEnabled())
            page.remove_folder(fm4cars.MOD)
            package, header = fm4cars._package(self.cfg)
            self.assertFalse(package.exists())
            self.assertFalse(header.exists())
            self.assertFalse(page.fm4_remove.isEnabled())
        finally:
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
