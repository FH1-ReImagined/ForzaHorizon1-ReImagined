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


def xex(title, media=0x26FF1101):
    """A minimal XEX2 header with the execution info (media id, title id) xexcheck.header_info reads."""
    head = bytearray(0x100)
    head[:4] = b"XEX2"
    struct.pack_into(">I", head, 0x14, 1)
    struct.pack_into(">II", head, 0x18, 0x40006, 0x40)
    struct.pack_into(">IIII", head, 0x40, media, 8, 8, title)
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


def iso(path, files, offset=0):
    """An XDVDFS disc image: the volume (descriptor at its sector 32) at `offset`, 0 for a
    rebuilt or trimmed image, a disc format's game partition offset for a full one (written
    sparse: the video partition before it is left empty)."""
    sectors, root, size = volume(files, 33)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.seek(offset + 0x10000)
        f.write(MEDIA + struct.pack("<II", root, size))
        for b, data in sorted(sectors.items()):
            f.seek(offset + b * 0x800)
            f.write(data.ljust(0x800, b"\0"))
    return path


def god(folder, title, files, name="FM4", file="ABCDEF0123456789", content_type=0x7000, flat=False):
    """A Games on Demand copy: <title>/00007000/<package> (or <package> in folder when flat)
    and its <package>.data/Data0000."""
    from xcontent_extract import Svod
    where = folder if flat else folder / f"{title:08X}" / f"{content_type:08X}"
    pkg = package(where / file, title, content_type, name, svod=True)
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
        self.assertIn("is not an Xbox 360 disc image, Games on Demand package, content package or archive", msg)
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


def pack_bytes(name="Car Pack", title=None, content_type=0x2):
    """A Disc 2 car pack's bytes (an FM4 PIRS package header, padded past the size car packs have)."""
    with tempfile.TemporaryDirectory() as t:
        p = package(Path(t) / "p", fm4cars.FM4_TITLE if title is None else title, content_type, name)
        return p.read_bytes().ljust(fm4cars.PACK_MIN + 0x1000, b"\0")


PACKS = "content/0000000000000000/4d530910/00000002/"
DISC2P = {"default.xex": xex(fm4cars.FM4_TITLE, 0x0F5C7AF9), "media/x.bin": b"x",
          PACKS + "4d53091000000001": pack_bytes("1950-2000 Model Year Car Pack"),
          PACKS + "4d53091000000002": pack_bytes("Autovista Car Pack")}


def write_tree(folder, files):
    for path, data in files.items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data)
    return folder


class Fm4FormTests(unittest.TestCase):
    """Every common way a player holds FM4: disc images (whole, trimmed, split), Games on Demand,
    extracted folders, archives, and the title updates and DLC that lie next to them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        fm4cars.paths.ensure_tools_importable()

    def prepare(self, disc1, disc2=None):
        work = self.t / "work"
        shutil.rmtree(work, ignore_errors=True)
        return fm4cars.prepare(disc1, disc2, work, lambda n, total, text: None, lambda: False)

    def disc1(self, chosen):
        disc1, _ = self.prepare(chosen)
        self.assertEqual(fm4cars._find_ci(disc1, "Media", "db", "gamedb.slt").read_bytes(), b"db", chosen)
        return disc1

    def disc2(self, chosen):
        """The names of the car packs Disc 2's choice gives, or the folder of unpacked ones."""
        work = self.t / "work2"
        shutil.rmtree(work, ignore_errors=True)
        found = fm4cars._choice(chosen, 2, work, {}, lambda n, total, text: None, lambda: False)
        packs, unpacked = fm4cars._disc2(found, work, lambda n, total, text: None, lambda: False)
        return sorted(p.name for p in packs) if packs else unpacked

    def refused(self, disc1, disc2=None):
        with self.assertRaises(fm4cars.Fm4Error) as e:
            self.prepare(disc1, disc2)
        return str(e.exception)

    # ---- 1. disc images

    def test_full_and_trimmed_images_at_every_disc_format_offset(self):
        for offset, name in ((0x2080000, "redump-xgd3.iso"), (0x2090000, "xgd3-alt.iso"),
                             (0xFD90000, "xgd2.iso"), (0x18300000, "xgd1.iso"), (0, "rebuilt.iso")):
            img = iso(self.t / name, DISC1, offset)
            self.disc1(img)
            img.unlink()

    def test_image_names_do_not_matter(self):
        for name in ("fm4.xiso", "fm4.img", "fm4.bin", "fm4.ISO", "fm4 no extension"):
            self.disc1(iso(self.t / name, DISC1))

    def split(self, img, names, size=0x9000):
        raw = img.read_bytes()
        img.unlink()
        parts = [raw[k:k + size] for k in range(0, len(raw), size)]
        self.assertGreater(len(parts), 2)
        out = []
        for n, part in enumerate(parts):
            out.append(self.t / names(n))
            out[-1].write_bytes(part)
        return out

    def test_split_images_by_any_part(self):
        for names in (lambda n: f"fm4.iso.{n + 1}", lambda n: f"fm4.iso.{n + 1:03d}", lambda n: f"fm4.{n + 1:03d}",
                      lambda n: f"fm4.part{n + 1}.iso", lambda n: f"fm4.{n + 1}.iso", lambda n: f"fm4.iso.part{n}"):
            parts = self.split(iso(self.t / "fm4.iso", DISC1), names)
            self.disc1(parts[0])
            self.disc1(parts[1])  # any part: the others are found next to it
            self.disc1(self.t)  # the folder of the parts
            for p in parts:
                p.unlink()

    def test_a_split_image_with_a_part_missing_says_which(self):
        parts = self.split(iso(self.t / "fm4.iso", DISC1), lambda n: f"fm4.iso.{n + 1}")
        parts[1].unlink()
        self.assertIn("part 2 is not next to it", self.refused(parts[0]))
        parts[0].unlink()
        self.assertIn("looks like part 3 of a split disc image, but its first part is not next to it",
                      self.refused(parts[2]))

    def test_a_whole_image_named_like_a_part_is_read_alone(self):
        d1 = iso(self.t / "fm4.1.iso", DISC1)
        d2 = iso(self.t / "fm4.2.iso", DISC2P)
        self.disc1(d1)
        self.assertEqual(self.disc2(d2), ["4d53091000000001", "4d53091000000002"])

    def test_a_truncated_image_says_so(self):
        img = iso(self.t / "fm4.iso", DISC1)
        with open(img, "r+b") as f:
            f.truncate(img.stat().st_size - 0x800)
        self.assertIn("is it split into parts, or still downloading?", self.refused(img))

    def test_the_layer_break_file_leads_to_its_image(self):
        iso(self.t / "fm4.iso", DISC1)
        (self.t / "fm4.dvd").write_text("LayerBreak=1913760\nfm4.iso\n")
        self.disc1(self.t / "fm4.dvd")

    # ---- 2. Games on Demand

    def test_games_on_demand_layouts(self):
        # the Xbox 360 drive's own layout, chosen at its root
        god(self.t / "usb/Content/0000000000000000", fm4cars.FM4_TITLE, DISC1)
        self.disc1(self.t / "usb")
        # a lone package next to its .data folder, from any converter, with a Thumbs.db in it
        pkg = god(self.t / "lone", fm4cars.FM4_TITLE, DISC1, file="F1E2D3C4B5A69788", flat=True)
        (pkg.with_name(pkg.name + ".data") / "Thumbs.db").write_bytes(b"x")
        for chosen in (self.t / "lone", pkg, pkg.with_name(pkg.name + ".data"),
                       pkg.with_name(pkg.name + ".data") / "Data0000"):
            self.disc1(chosen)
        # a hard drive install of the disc is the same kind of package
        self.disc1(god(self.t / "hdd", fm4cars.FM4_TITLE, DISC1, content_type=0x4000).parent)

    def test_games_on_demand_with_a_part_or_its_package_missing(self):
        pkg = god(self.t / "god", fm4cars.FM4_TITLE, DISC1, flat=True)
        data = pkg.with_name(pkg.name + ".data")
        (data / "Data0001").write_bytes(b"")  # the header says one part
        self.assertIn("should hold 1 data parts (Data0000 to Data0000) but holds 2", self.refused(pkg))
        (data / "Data0001").unlink()
        pkg.unlink()
        for chosen in (data, data / "Data0000", self.t / "god"):
            self.assertIn("but the package file (ABCDEF0123456789) is not next to it", self.refused(chosen))

    def test_games_on_demand_of_disc_2_and_both_discs_in_one_folder(self):
        god(self.t / "god", fm4cars.FM4_TITLE, DISC2P, file="0000000000000001")
        god(self.t / "god", fm4cars.FM4_TITLE, DISC1, file="0000000000000002")
        self.disc1(self.t / "god")
        self.assertEqual(self.disc2(self.t / "god"), ["4d53091000000001", "4d53091000000002"])
        self.assertEqual(self.disc2(self.t / "god/4D530910/00007000/0000000000000001"),
                         ["4d53091000000001", "4d53091000000002"])
        self.assertIn("has no Forza Motorsport 4 car packs: is it Disc 1?",
                      self.refused(self.t / "god", self.t / "god/4D530910/00007000/0000000000000002"))

    def test_disc_2_car_packs_installed_next_to_a_games_on_demand_disc_1(self):
        title = self.t / "Content/0000000000000000"
        god(title, fm4cars.FM4_TITLE, DISC1)
        write_tree(title / "4D530910/00000002", {"4d53091000000001": DISC2P[PACKS + "4d53091000000001"],
                                                 "4d53091000000003": DISC2P[PACKS + "4d53091000000002"]})
        package(title / "4D530910/000B0000/TU_10LL1ACS", fm4cars.FM4_TITLE, 0xB0000, "Title Update 1")
        self.disc1(self.t / "Content")
        self.assertEqual(self.disc2(self.t / "Content"), ["4d53091000000001", "4d53091000000003"])

    # ---- 3. extracted folders

    def test_extracted_folders(self):
        # extract-xiso output, its default.xex chosen, and other letter cases
        self.disc1(write_tree(self.t / "xiso", DISC1))
        self.disc1(self.t / "xiso/default.xex")
        self.disc1(write_tree(self.t / "upper", {"DEFAULT.XEX": DISC1["default.xex"], "MEDIA/DB/GAMEDB.SLT": b"db"}))

    def test_the_parent_of_both_discs_folders(self):
        write_tree(self.t / "FM4/Disc 1", DISC1)
        write_tree(self.t / "FM4/Disc 2", DISC2P)
        self.assertEqual(self.disc1(self.t / "FM4"), self.t / "FM4/Disc 1")
        self.assertEqual(self.disc2(self.t / "FM4"), ["4d53091000000001", "4d53091000000002"])
        self.assertIn("Disc 1, chosen again for Disc 2", self.refused(self.t / "FM4", self.t / "FM4/Disc 1"))
        self.assertIn("This is Forza Motorsport 4's Disc 2", self.refused(self.t / "FM4/Disc 2"))

    def test_xenias_content_folder(self):
        # Xenia installs packages unpacked: content/<profile>/<title>/<type>/<package>/
        title = self.t / "xenia/content/0000000000000000/4D530910"
        write_tree(title / "00007000/ABCDEF0123456789", DISC1)
        for n in (1, 2):
            write_tree(title / f"00000002/4d5309100000000{n}/Media/DLCZips", {f"{n}_pri_65.zip": b"PK"})
        self.assertEqual(self.disc1(self.t / "xenia"), title / "00007000/ABCDEF0123456789")
        self.assertEqual(self.disc2(self.t / "xenia"), title / "00000002")
        self.assertEqual(self.disc2(title / "00000002/4d53091000000001"), title / "00000002/4d53091000000001")

    def test_a_folder_with_nothing_of_fm4(self):
        write_tree(self.t / "empty", {"readme.txt": b"hi"})
        msg = self.refused(self.t / "empty")
        self.assertIn("empty holds no Forza Motorsport 4 Disc 1: no default.xex, disc image or Games on "
                      "Demand package was found in it or its subfolders", msg)
        with self.assertRaises(fm4cars.Fm4Error) as e:
            self.disc2(self.t / "empty")
        self.assertIn("holds no Forza Motorsport 4 Disc 2", str(e.exception))
        write_tree(self.t / "packs", {"a": DISC2P[PACKS + "4d53091000000001"]})
        self.assertIn("holds Forza Motorsport 4's Disc 2 car packs, not Disc 1", self.refused(self.t / "packs"))

    # ---- 4. archives

    def zip(self, name, files):
        import zipfile
        with zipfile.ZipFile(self.t / name, "w", zipfile.ZIP_DEFLATED) as z:
            for path, data in files.items():
                z.writestr(path, data)
        return self.t / name

    def test_zip_and_tar_archives_of_every_form(self):
        img = iso(self.t / "src/fm4.iso", DISC1).read_bytes()
        self.disc1(self.zip("iso.zip", {"Forza Motorsport 4 (Disc 1).iso": img}))
        self.disc1(self.zip("files.zip", {f"FM4/Disc1/{k}": v for k, v in DISC1.items()}))
        pkg = god(self.t / "src/god", fm4cars.FM4_TITLE, DISC1)
        gfiles = {str(f.relative_to(self.t / "src")): f.read_bytes() for f in (self.t / "src/god").rglob("*") if f.is_file()}
        self.disc1(self.zip("god.zip", gfiles))
        self.assertTrue(pkg.is_file())
        import tarfile
        with tarfile.open(self.t / "fm4.tar.gz", "w:gz") as t:
            t.add(self.t / "src/fm4.iso", "fm4.iso")
        self.disc1(self.t / "fm4.tar.gz")

    def test_one_archive_with_both_discs_is_unpacked_once(self):
        both = self.zip("both.zip", {"d1.iso": iso(self.t / "a.iso", DISC1).read_bytes(),
                                     "d2.iso": iso(self.t / "b.iso", DISC2P).read_bytes()})
        disc1, content = self.prepare(both, both)
        self.assertEqual((disc1 / "Media/db/gamedb.slt").read_bytes(), b"db")
        self.assertEqual(sorted(p.name for p in (self.t / "work").iterdir() if p.name.startswith("archive")),
                         ["archive1"])

    def test_7z_archive(self):
        exe = shutil.which("7zz") or shutil.which("7z") or shutil.which("7za")
        if not exe:
            self.skipTest("7-Zip is not installed")
        import subprocess
        iso(self.t / "src/fm4.iso", DISC1)
        subprocess.run([exe, "a", "-bso0", str(self.t / "fm4.7z"), str(self.t / "src/fm4.iso")], check=True)
        self.disc1(self.t / "fm4.7z")
        subprocess.run([exe, "a", "-bso0", "-mx0", "-v40k", str(self.t / "split.7z"), str(self.t / "src/fm4.iso")], check=True)
        self.disc1(self.t / "split.7z.002")  # any volume: the first is used
        (self.t / "split.7z.001").unlink()
        self.assertIn("its first part (split.7z.001) is not next to it", self.refused(self.t / "split.7z.002"))

    def test_links_an_extractor_makes_are_refused(self):
        if not fm4cars._tool_paths("tar") or sys.platform == "win32":
            self.skipTest("no 7-Zip or bsdtar here (on Windows they write links as plain files)")
        import io
        import tarfile
        for kind, extra in (("link", dict(type=tarfile.SYMTYPE, linkname="/etc/hostname")),
                            ("hard", dict(type=tarfile.LNKTYPE, linkname="fm4.iso"))):
            tar = self.t / f"{kind}.tar"
            with tarfile.open(tar, "w") as t:
                data = iso(self.t / "a.iso", DISC1).read_bytes()
                info = tarfile.TarInfo("fm4.iso")
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
                t.addfile(self._tarinfo("evil", extra))
            out = self.t / f"out-{kind}"
            try:  # the tool path, as for .7z and .rar
                fm4cars.unpack_archive(tar, "tar", True, out, lambda *a: None, lambda: False)
            except fm4cars.Fm4Error as e:  # the tool made the link: refused, nothing left
                self.assertIn(f"{kind}.tar holds an unsafe entry (", str(e))
                self.assertFalse(out.exists())
            else:  # the tool wrote it as a plain file (older p7zip) or left it out
                self.assertIsNone(fm4cars.unsafe_entry(out))

    def test_a_link_an_extractor_leaves_is_refused_and_deleted(self):
        (self.t / "fm4.7z").write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\0" * 32)
        out = self.t / "out"

        def tool(exe, p, out, progress, cancelled):
            (out / "fm4.iso").write_bytes(b"x")
            try:
                os.symlink("/", out / "root")
            except OSError:
                os.mkfifo(out / "root") if hasattr(os, "mkfifo") else self.skipTest("no links here")
            return None
        with patch.object(fm4cars, "_tool_paths", lambda kind: ["7z"]), \
                patch.object(fm4cars, "_tool_size", lambda exe, p: None), patch.object(fm4cars, "_run_tool", tool):
            with self.assertRaises(fm4cars.Fm4Error) as e:
                fm4cars.unpack_archive(self.t / "fm4.7z", "7z", False, out, lambda *a: None, lambda: False)
        self.assertIn("fm4.7z holds an unsafe entry (root: ", str(e.exception))
        self.assertFalse(out.exists())
        self.assertTrue(Path("/").exists())
        # nothing but plain files and folders passes
        (self.t / "ok").mkdir()
        (self.t / "ok/a").write_bytes(b"x")
        self.assertIsNone(fm4cars.unsafe_entry(self.t / "ok"))
        try:
            os.symlink(self.t / "a.iso", self.t / "ok/l")
        except OSError:
            return
        self.assertEqual(fm4cars.unsafe_entry(self.t / "ok"), ("l", "a link"))

    @staticmethod
    def _tarinfo(name, extra):
        import tarfile
        info = tarfile.TarInfo(name)
        for k, v in extra.items():
            setattr(info, k, v)
        return info

    def test_an_archive_without_a_program_to_unpack_it(self):
        (self.t / "fm4.rar").write_bytes(b"Rar!\x1a\x07\x01\x00" + b"\0" * 64)
        with patch.object(fm4cars, "_tool_paths", lambda kind: []):
            msg = self.refused(self.t / "fm4.rar")
        self.assertIn("fm4.rar is a RAR archive, and no program that unpacks it was found on this PC "
                      "(7-Zip or unrar). Extract it first", msg)
        msg = self.refused(self.t / "fm4.rar")  # a damaged one, with whatever this PC has
        self.assertTrue(msg.startswith("fm4.rar could not be unpacked (") or "no program that unpacks it" in msg, msg)

    def test_damaged_unsafe_or_too_big_archives(self):
        good = self.zip("fm4.zip", {"fm4.iso": iso(self.t / "a.iso", DISC1).read_bytes()})
        (self.t / "cut.zip").write_bytes(good.read_bytes()[:-30])
        self.assertIn("cut.zip", self.refused(self.t / "cut.zip"))
        self.assertIn("unsafe name", self.refused(self.zip("evil.zip", {"../evil.iso": b"x"})))
        with patch.object(fm4cars.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(1, 1, 1000)):
            self.assertIn("Unpacking fm4.zip needs 0.0 GB of free space", self.refused(good))
        write_tree(self.t / "holder", {"fm4.7z": b"7z\xbc\xaf\x27\x1c" + b"\0" * 32})
        self.assertIn("fm4.7z is an archive: choose that file itself", self.refused(self.t / "holder"))

    # ---- 5. title updates, DLC, demo and editions

    def test_title_updates_dlc_and_demo_are_named_or_left_alone(self):
        tu = package(self.t / "TU_10LL1ACS", fm4cars.FM4_TITLE, 0xB0000, "Title Update 3")
        self.assertIn("is a Forza Motorsport 4 title update (\"Title Update 3\"), not a disc: the import "
                      "does not need it", self.refused(tu))
        demo = package(self.t / "demo", fm4cars.FM4_TITLE, 0x80000, "Forza Motorsport 4 Demo")
        self.assertIn("is the Forza Motorsport 4 demo", self.refused(demo))
        # a disc image with a title update, DLC and a demo next to it, the folder chosen
        iso(self.t / "fm4.iso", DISC1)
        package(self.t / "dlc", fm4cars.FM4_TITLE, 0x2, "Speed Pack", size=2 << 20)
        self.disc1(self.t)

    def test_every_fm4_release_shares_the_title_id(self):
        # editions and regions differ by media id (the disc), not by title id
        for media in (0x26FF1101, 0x12345678):
            write_tree(self.t / f"m{media:X}", {**DISC1, "default.xex": xex(fm4cars.FM4_TITLE, media)})
            self.disc1(self.t / f"m{media:X}")


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
