#!/usr/bin/env python3
"""Convert Forza Motorsport 4's cars that Forza Horizon (2012) lacks into an FH1 mod folder.

Reads the player's own FM4 files (disc 1 unpacked, and optionally the disc 2 car packs) and
their own FH1 files, and writes a mod folder (docs/modding.md) that adds the cars:

    <out>/mod.toml
    <out>/media/db/gamedb.slt                  the player's FH1 database with the cars' rows
    <out>/media/stringtables/<lang>/*.str      FH1's car (and make) names plus the new ones
    <out>/media/cars/<CAR>/...                 the car: converted models, its textures, a
                                               generated physicsdefinition.bin
    <out>/media/wheels/<WHEEL>/...             its stock wheel when FH1 has no files for it
    <out>/media/ui/textures/thumbnails/...     its picture for the car lists

With --package the car, wheel and picture files go into a DLC-style package folder instead
(zips under Media/DLCZips, FM4's compressed members copied as they are): the mod overlay
holds 4 GB at most, a full import is about 9 GB uncompressed and 5.6 GB in the package.

Nothing of either game is in this tool; it only reads the files the player points it at.
docs/modding/fm4-cars.md has the format notes and what was checked in the game.

Usage:
  fm4_cars.py list    --fm4 FM4_DISC1 [--fm4-content DIR] [--fm4-dlc DIR] --fh1 GAME [--update TU]
  fm4_cars.py convert --fm4 FM4_DISC1 [--fm4-content DIR] [--fm4-dlc DIR] --fh1 GAME [--update TU]
                      [--dlc DIR] --out MOD_DIR (--cars A,B,... | --all)
  fm4_cars.py check   --fh1 GAME [--update TU] --out MOD_DIR [--package PACKAGE_DIR]
                      (what an installed import lacks that the game reads for its cars)

FM4_DISC1 is the unpacked disc 1 (the folder with default.xex and Media); --fm4-content is a
folder holding the unpacked disc 2 car packs (each with Media/DLCZips/*_pri_65.zip); --fm4-dlc
one holding unpacked FM4 DLC packs (the same layout, their cars' rows in the pack's
media/db/patch/<n>_merge.slt). GAME is
FH1's game folder (with media), TU its title update folder (with media.zip); --dlc the
installed FH1 DLC folder (.../4D5309C9/00000002), so their car names are kept and their cars
are not added twice. Standard library only (LZX through tools/fxobj_shaders.py).
"""
import argparse
import itertools
import math
import os
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import car_ids  # noqa: E402 (the one id table for every imported car, #924)
import fm4_carbin  # noqa: E402
import fm4_physdef  # noqa: E402
from car_ids import twin_name  # noqa: E402,F401
from fxobj_shaders import read_zip_member  # noqa: E402

MOD_NAME = "FM4 cars"
PACK_NAME = "464D3443415253000000000000000000000000004D"  # the package folder ("FM4CARS")
PACK_STEM = "fm4cars"
PACK_PRIORITY = 60    # FH1's DLC car zips are _pri_65
PACK_OFFER = 64800    # a .puboffer no FH1 offer uses (theirs 1000-2800); no ContentOffers row
SKIP_FILES = {"versiondata.xml"}
# FH1's DLC cars (docs/dlc.md): not added from FM4 even when that DLC is not installed, so a
# DLC installed later never meets a second copy of its car.
FH1_DLC_CARS = {
    "RUF_CTR2_95", "FOR_F100_56", "FER_F142_10", "NIS_370Z_10", "VW_GolfR_10", "MER_SLSAMG_11",
    "AST_Virage_12", "FOR_MustangBoss302_PZA_13", "FOR_ShelbyGT500LCE_13", "FOR_TransitSSV_11",
    "BOW_NemesisEXR_12", "ALF_ZagatoTZ3Stradale_12", "BMW_M3GTR_HZN_05", "AST_Vanquish_13",
    "MER_CLKGTR_HZN_98", "AST_DBS_09", "AMC_JavelinAMX_71", "MCL_F1_93", "FER_F12_13",
    "FOR_GTX1_06", "HUM_H1AlphaOpenTop_06", "FOR_SVTRaptorHALO_12", "NIS_510_70", "DEV_GTX_10",
    "FOR_CountrySquire_66", "MAZ_RX3_73", "TOY_FJCruiser_12", "SUB_BRZ_13", "FER_512TR_HZN_91",
    "HON_CivicSi_13", "HON_HPDRallyFit_10", "HON_CivicSi_86", "MER_SLR_05", "PAG_ZondaCinque_09",
    "FOR_Mustang_95", "LEX_LFANurburgring_12", "LOT_ExigeS_12", "GMC_Vandura_83",
    "CAD_Escalade_12", "CHE_BelAir_57", "FOR_MustangBoss302_13", "AUD_LCESportQuattro_83",
    "VW_LCESciroccoR_11", "FOR_LCESVTRaptor_12", "MIT_LCELancerEvoXGSR_08",
    "DOD_LCEChargerSRT8_12", "LAM_AventadorJ_12", "ALF_8CSpider_10", "FER_599XXEvolution_12",
    "GUM_ApolloEnraged_12", "PEU_206RC_04", "JOS_JT1_10", "BMW_M135i_13",
    "FER_360Challenge_HZN_03", "NIS_R390_HZN_98", "AUD_RS4Avant_13", "MER_G65AMG_13",
    "SHE_Cobra427_65", "NIS_240SX_94", "JEE_Wrangler_12", "BMW_M3GTS_10",
    "LAM_GallardoLP570Spyder_12", "SHE_1000_12", "FOR_FocusSVT_03", "SUB_WRXSTi_05",
    "MIT_LancerEvoVIIIMR_04", "FOR_EscortRSCosworth_92", "LAN_037Stradale_82",
    "TOY_CelicaST185_90", "AUS_MiniCooperS_65", "TOY_Sprinter_85", "PEU_205T16_84",
    "LAN_DeltaS4_86", "AUD_RS4_06", "FOR_RS200Evolution_87", "LAM_LP7004_12", "ULT_GTR_12",
    "FER_FXX_05", "LAM_Miura_06", "PAG_Huayra_12", "LAM_SestoElemento_11", "RUF_Rt12R_11",
    "KOE_Agera_11", "AUD_R8GTSpyder_12",
}
# Model sections left out of the collision hull: glass, lights, mirrors, wheels and the
# optional race parts (bumperFrace, wingrace, cagerace) that a stock car does not have.
HULL_SKIP = re.compile(r"(?i)mirror|wheel|tire|glass|light|race|wing|exhaust|cage|antenna|caliper|rotor")


class ConvertError(Exception):
    pass


class Cancelled(Exception):
    pass


def log(*a):
    print(*a, flush=True)


# ============================================================================= sources

def find_ci(base, *parts):
    """base/parts..., matching each part without regard to case (the discs mix cases)."""
    path = base
    for part in parts:
        if not os.path.isdir(path):
            return None
        want = part.lower()
        hit = [n for n in os.listdir(path) if n.lower() == want]
        if not hit:
            return None
        path = os.path.join(path, hit[0])
    return path


class Member:
    """A file inside one of the game's zips."""

    def __init__(self, zf, path, info):
        self.zf, self.path, self.info = zf, path, info

    def read(self):
        data = read_zip_member(self.zf, self.path, self.info)
        if len(data) != self.info.file_size:
            raise ConvertError("could not read %s from %s" % (self.info.filename, self.path))
        return data

    def raw(self):
        """(method, the member's stored bytes, CRC-32, size): copied as they are into a pack."""
        i = self.info
        with open(self.path, "rb") as f:
            f.seek(i.header_offset)
            h = f.read(30)
            start = i.header_offset
            if h[:4] == b"PK\x03\x04":
                n, m = struct.unpack("<HH", h[26:30])
                start += 30 + n + m
            f.seek(start)
            data = f.read(i.compress_size)
        if len(data) != i.compress_size:
            raise ConvertError("could not read %s from %s" % (i.filename, self.path))
        return i.compress_type, data, i.CRC, i.file_size


class PackWriter:
    """Zips the way FH1's DLC packages hold them (Media/DLCZips/<name>_pri_<n>.zip, mounted at
    game:\\): members stored or copied in their LZX form (method 21) from FM4's archives, each
    central record with the 0x1123 extra field (the absolute offset of the member's data) the
    game's zip reader uses. A new zip starts before one would pass the reader's 4 GB or
    65,535-entry limits."""

    LIMIT = 0xF0000000

    def __init__(self, folder, stem, priority):
        self.folder, self.stem, self.priority = folder, stem, priority
        self.index = 0
        self.f = None
        self.names = set()
        self.zips = []
        self.loose = set()

    def _open(self):
        self.index += 1
        os.makedirs(self.folder, exist_ok=True)
        name = "%s%d_pri_%d.zip" % (self.stem, self.index, self.priority)
        self.f = open(os.path.join(self.folder, name), "wb")
        self.central = []
        self.zips.append(name)

    def add(self, rel, data=None, raw=None):
        """rel: the path under game:\\ ("media/cars/X/x.carbin"); data: bytes to store, or
        raw: Member.raw() of a member to copy."""
        rel = rel.replace("\\", "/")
        if rel.lower() in self.names:
            return
        self.names.add(rel.lower())
        if raw is None:
            method, payload, crc, size = 0, data, zlib.crc32(data) & 0xFFFFFFFF, len(data)
        else:
            method, payload, crc, size = raw
        name = rel.encode("ascii")
        if (self.f is None or self.f.tell() + 30 + len(name) + len(payload) + 64 * (len(self.central) + 1)
                > self.LIMIT or len(self.central) >= 65000):
            self.close()
            self._open()
        offset = self.f.tell()
        self.f.write(struct.pack("<IHHHHHIIIHH", 0x04034B50, 10, 0, method, 0, 0x21, crc,
                                 len(payload), size, len(name), 0) + name)
        data_at = self.f.tell()
        self.f.write(payload)
        self.central.append(struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 10, 10, 0, method, 0, 0x21,
                                        crc, len(payload), size, len(name), 8, 0, 0, 0, 0, offset)
                            + name + struct.pack("<HHI", 0x1123, 4, data_at))

    def add_loose(self, rel, src):
        """A file the game reads only as a file, an engine sound bank (rel: "Media/audio/cars/
        Engines/Soundbanks/LOD1/x.fsb"): copied from src into the folder of the current zip's
        name, where FM4's and FH1's DLC packs keep theirs (BANKS_DIR, #776). Once per name."""
        if rel.lower() in self.loose:
            return False
        if not self.zips:
            self._open()
        dest = os.path.join(self.folder, self.zips[-1][:-len(".zip")], *rel.split("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(src, "rb") as f, open(dest, "wb") as g:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                g.write(chunk)
        self.loose.add(rel.lower())
        return True

    def close(self):
        if self.f is None:
            return
        start = self.f.tell()
        cd = b"".join(self.central)
        self.f.write(cd)
        self.f.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(self.central), len(self.central),
                                 len(cd), start, 0))
        self.f.close()
        self.f = None


class _Zips:
    """The zips read so far, kept open. close() lets them go: Windows can delete or move neither
    a file nor its folder while a handle to it is open (#551)."""

    def __init__(self):
        self.open = {}

    def get(self, path):
        z = self.open.get(path)
        if z is None:
            z = self.open[path] = zipfile.ZipFile(path)
        return z

    def close(self, *roots):
        """Close the zips under any of the folders roots (all of them when none is given)."""
        roots = [os.path.normcase(os.path.abspath(r)) for r in roots if r]
        for path in list(self.open):
            p = os.path.normcase(os.path.abspath(path))
            if not roots or any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots):
                self.open.pop(path).close()


ZIPS = _Zips()


def zip_member(path, name):
    """The member `name` (any case) of a zip, read; None when absent."""
    if not path or not os.path.isfile(path):
        return None
    z = ZIPS.get(path)
    want = name.lower().replace("\\", "/")
    for info in z.infolist():
        if info.filename.lower() == want:
            return Member(z, path, info).read()
    return None


def logo_members(path, prefix=""):
    """Lowercase name ("ui4/logos/<size>/l_man_<make>_logo.xds") -> Member of the make logos in a
    UI textures zip, or in a pack's zip under prefix ("media/ui/textures/")."""
    z = ZIPS.get(path)
    out = {}
    for i in z.infolist():
        m = re.match(r"(?i)%s(ui4/logos/[^/]+/l_man_[^/]+\.xds)$" % re.escape(prefix), i.filename)
        if m:
            out[m.group(1).lower()] = Member(z, path, i)
    return out


# A pack's car zip: Disc 2's are 0001_pri_65.zip..., a DLC pack's its offer (27_pri_65.zip) or a
# hex name (5c00_pri_65.zip); other zips of a pack (tracks, cameras, string tables) hold no car.
PACK_ZIP = re.compile(r"(?i)[0-9a-f]+_pri_65\.zip$")
PACK_STRINGS = re.compile(r"(?i)stringtables_pri_\d+\.zip$")
PACK_CMT = re.compile(r"(?i)carmodeltuning_pri_\d+\.zip$")  # mounted at media/audio/cars/carmodeltuning
PATCH = re.compile(r"(?i)^media/db/patch/(\d+)_merge\.slt$")
PACK_AUDIO = {"cmt": "media/audio/cars/carmodeltuning/", "et": "media/audio/cars/engines/enginetuning/",
              "ht": "media/audio/cars/engines/harmonictuning/"}
# Engine sound banks (.fsb). The game reads a bank as a file, never from a zip: the disc's are loose
# files in this folder, and a DLC pack's are loose files in the folder of one of its zips' names
# (Media/DLCZips/<zip name>/Media/audio/cars/Engines/Soundbanks/LOD1/...), which the game looks in
# when that folder exists. A harmonic tuning file names its bank (<Soundbank name="...">); a bank
# no file has stops the game with its disc read error (#776).
BANKS_DIR = ("Media", "audio", "cars", "Engines", "Soundbanks")
# Named by FH1's own NA_*_HT.xml placeholder tunings (24 of its cars, the Prius and the Leaf
# among them), with no file in FH1 or FM4: the game never loads it.
PLACEHOLDER_BANKS = {"na.fsb"}
_BANK = re.compile(rb'<Soundbank\s+name="([^"]+)"')


def bank_names(data):
    """The sound banks a harmonic tuning file (bytes) names, with ".fsb" added where it has none
    (FH1's 6N1_NSX_EngAmb_HT.xml names its bank without it)."""
    out = []
    for m in _BANK.findall(data):
        name = m.decode("latin-1").strip()
        out.append(name if "." in name else name + ".fsb")
    return out


def loose_banks(folder):
    """{lower file name: (path, path under the Soundbanks folder)}: the .fsb files under folder
    (a Soundbanks folder: LOD1/, Burbles/), or {} when there is none."""
    out = {}
    if not folder or not os.path.isdir(folder):
        return out
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for f in sorted(files):
            if f.lower().endswith(".fsb"):
                path = os.path.join(root, f)
                out.setdefault(f.lower(), (path, os.path.relpath(path, folder).replace(os.sep, "/")))
    return out


def pack_folders(root):
    """The packages under root: each folder holding a Media/DLCZips with a car zip, sorted."""
    out = []
    if not root or not os.path.isdir(root):
        return out
    for folder, dirs, files in os.walk(root):
        dirs.sort()
        if os.path.basename(folder).lower() == "dlczips" and any(PACK_ZIP.match(f) for f in files):
            out.append(os.path.dirname(os.path.dirname(folder)))
            dirs[:] = []
    return sorted(out)


def pack_patches(pack):
    """[(number, zip path, zipfile.ZipInfo)] of the database patches (media/db/patch/N_merge.slt)
    in a package folder's zips: FM4's DLC packs carry their cars' rows in one; Disc 2's car packs
    have none (their rows are in Disc 1's database)."""
    dz = find_ci(pack, "Media", "DLCZips")
    out = []
    for f in sorted(os.listdir(dz)) if dz else []:
        if PACK_ZIP.match(f):
            path = os.path.join(dz, f)
            for i in ZIPS.get(path).infolist():
                m = PATCH.match(i.filename)
                if m:
                    out.append((int(m.group(1)), path, i))
    return out


def pack_cars(pack):
    """(MediaNames of the cars whose files a package folder has, Data_Car rows of its database
    patches as (Id, MediaName))."""
    dz = find_ci(pack, "Media", "DLCZips")
    names = set()
    for f in sorted(os.listdir(dz)) if dz else []:
        if PACK_ZIP.match(f):
            for i in ZIPS.get(os.path.join(dz, f)).infolist():
                m = re.match(r"(?i)media/cars/([^/]+)/", i.filename)
                if m and not m.group(1).lower().endswith("_slod"):
                    names.add(m.group(1))
    rows = []
    for _, path, info in pack_patches(pack):
        fd, tmp = tempfile.mkstemp(suffix=".slt")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(Member(ZIPS.get(path), path, info).read())
            db = sqlite3.connect("file:%s?mode=ro" % tmp, uri=True)
            try:
                if "Data_Car" in tables(db):
                    rows += db.execute("select Id, MediaName from Data_Car order by Id").fetchall()
            finally:
                db.close()
        except sqlite3.Error:
            pass
        finally:
            remove_file(tmp)
    return names, rows


def merge_patch(db, patch_path):
    """Merges a DLC pack's database patch into db (FM4's database, a copy): the patch's rows are
    added to the tables both have; a table whose first column is a unique key (Data_Engine,
    List_FamilyModel...) takes the patch's row over its own of the same key, as the game reads the
    patch over the disc's database; other tables (several rows a car) get the rows they lack."""
    db.execute("attach database ? as p", (patch_path,))
    try:
        mine = set(tables(db))
        for (t,) in db.execute("select name from p.sqlite_master where type='table'").fetchall():
            pcols = [r[1] for r in db.execute("pragma p.table_info([%s])" % t)]
            if t not in mine:
                db.execute("create table main.[%s] as select * from p.[%s]" % (t, t))
                continue
            cols = [c for c in columns(db, t) if c in pcols]
            if not cols:
                continue
            key = columns(db, t)[0]
            c = ",".join("[%s]" % x for x in cols)
            (n, distinct), = db.execute("select count(*), count(distinct [%s]) from main.[%s]" % (key, t)).fetchall()
            if key in pcols and n == distinct:
                db.execute("delete from main.[%s] where [%s] in (select [%s] from p.[%s])" % (t, key, key, t))
                db.execute("insert or replace into main.[%s] (%s) select %s from p.[%s]" % (t, c, c, t))
            else:
                db.execute("insert or ignore into main.[%s] (%s) select %s from p.[%s] except select %s "
                           "from main.[%s]" % (t, c, c, t, c, t))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.execute("detach database p")



def remove_file(path):
    """Deletes a file of ours; on Windows retried for a few seconds while another program (an
    antivirus scan, the search indexer) has it open. True when it is gone."""
    for wait in (0.1, 0.2, 0.4, 0.8, 1.5, None):
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return True
        except OSError as e:
            if wait is None or getattr(e, "winerror", None) not in (5, 32, 33):
                return False
            time.sleep(wait)
    return False


class FM4:
    """The player's FM4 files: disc 1, the disc 2 car packs (content) and the DLC packs (dlc);
    each a folder of unpacked packages, or a list of such folders. A DLC pack's cars have their rows in the
    pack's database patch, which goes into a copy of disc 1's database (db_path)."""

    def __init__(self, disc1, content=None, dlc=None):
        self.media = find_ci(disc1, "Media")
        if not self.media:
            raise ConvertError("no Media folder in %s: point at FM4's unpacked disc 1" % disc1)
        self.db_path = find_ci(self.media, "db", "gamedb.slt")
        if not self.db_path:
            raise ConvertError("no Media/db/gamedb.slt in %s" % disc1)
        def roots(r):
            return [] if not r else [r] if isinstance(r, (str, os.PathLike)) else [x for x in r if x]
        content_roots, dlc_roots = roots(content), roots(dlc)
        self.packs = []  # (zip path, string table zip path)
        self.pack_dirs = []  # the Media/DLCZips folders, in pack order
        self.dlc_packs = []  # the DLC packages' folders (those with a database patch)
        self.dlc_car_ids = set()  # the cars whose rows come from a DLC pack's patch
        self._tmp = []
        for root, is_dlc in [(r, False) for r in content_roots] + [(r, True) for r in dlc_roots]:
            for pack in pack_folders(str(root) if root else None):
                dz = find_ci(pack, "Media", "DLCZips")
                files = sorted(os.listdir(dz))
                st = [os.path.join(dz, g) for g in files if PACK_STRINGS.match(g)]
                self.pack_dirs.append(dz)
                for f in files:
                    if PACK_ZIP.match(f):
                        self.packs.append((os.path.join(dz, f), st[0] if st else None))
                if is_dlc and pack_patches(pack):
                    self.dlc_packs.append(pack)
        self.packs.sort()
        self._roots = (disc1, *content_roots, *dlc_roots)
        self._cars = self._thumbs = self._audio = self._wheels = None
        self._dbs = []  # every connection db() handed out: closed by close() before the delete
        if self.dlc_packs:
            try:
                self._merge_dlc()
            except BaseException:
                self.close()  # nobody else can: the caller never gets this object
                raise

    def _merge_dlc(self):
        """disc 1's database with the DLC packs' patches (in their number's order) merged in."""
        fd, merged = tempfile.mkstemp(suffix=".slt")
        self._tmp.append(merged)
        with os.fdopen(fd, "wb") as g, open(self.db_path, "rb") as f:
            while True:
                chunk = f.read(1 << 22)
                if not chunk:
                    break
                g.write(chunk)
        db = sqlite3.connect(merged)
        try:
            before = {r[0] for r in db.execute("select Id from Data_Car")}
            for _, path, info in sorted(p for pack in self.dlc_packs for p in pack_patches(pack)):
                fd, tmp = tempfile.mkstemp(suffix=".slt")
                self._tmp.append(tmp)
                with os.fdopen(fd, "wb") as f:
                    f.write(Member(ZIPS.get(path), path, info).read())
                try:
                    merge_patch(db, tmp)
                except sqlite3.Error as e:
                    raise ConvertError("the database patch of %s could not be read (%s)"
                                       % (os.path.basename(path), e))
            self.dlc_car_ids = {r[0] for r in db.execute("select Id from Data_Car")} - before
        finally:
            db.close()
        self.db_path = merged

    def db(self):
        db = sqlite3.connect("file:%s?mode=ro" % self.db_path, uri=True)
        self._dbs.append(db)
        return db

    def close(self):
        """Close FM4's zips and every connection to its database, then delete the merged copy:
        Windows deletes no file a connection still holds open, and the launcher deletes the
        unpacked discs next (#551, #707)."""
        self._cars = self._thumbs = self._audio = self._wheels = None
        for db in self._dbs:
            db.close()  # a second close (the converter's own) does nothing
        self._dbs = []
        ZIPS.close(*self._roots)
        self._tmp = [p for p in self._tmp if not remove_file(p)]

    def car_files(self):
        """MediaName (lower) -> (display MediaName, [(relative name, Member)])."""
        if self._cars is not None:
            return self._cars
        cars = {}
        disc = find_ci(self.media, "cars")
        for f in sorted(os.listdir(disc)) if disc else []:
            if f.lower().endswith(".zip"):
                path = os.path.join(disc, f)
                z = ZIPS.get(path)
                cars[f[:-4].lower()] = (f[:-4], [(i.filename, Member(z, path, i)) for i in z.infolist()
                                                  if not i.is_dir()])
        for path, _ in self.packs:
            z = ZIPS.get(path)
            for i in z.infolist():
                m = re.match(r"(?i)media/cars/([^/]+)/(.+)$", i.filename)
                if m and not i.is_dir() and not m.group(1).lower().endswith("_slod"):
                    cars.setdefault(m.group(1).lower(), (m.group(1), []))[1].append((m.group(2), Member(z, path, i)))
        self._cars = cars
        return cars

    def wheel_files(self, name):
        disc = find_ci(self.media, "wheels")
        z = find_ci(disc, name + ".zip") if disc else None
        if z:
            zf = ZIPS.get(z)
            return [(i.filename, Member(zf, z, i)) for i in zf.infolist() if not i.is_dir()]
        for path, _ in self.packs:
            zf = ZIPS.get(path)
            out = []
            for i in zf.infolist():
                m = re.match(r"(?i)media/wheels/([^/]+)/(.+)$", i.filename)
                if m and m.group(1).lower() == name.lower() and not i.is_dir():
                    out.append((m.group(2), Member(zf, path, i)))
            if out:
                return out
        return None

    def has_wheel(self, name):
        """True when disc 1 or a pack has the wheel's files."""
        if self._wheels is None:
            disc = find_ci(self.media, "wheels")
            self._wheels = {f[:-4].lower() for f in os.listdir(disc) if f.lower().endswith(".zip")} if disc else set()
            for path, _ in self.packs:
                for i in ZIPS.get(path).infolist():
                    m = re.match(r"(?i)media/wheels/([^/]+)/.", i.filename)
                    if m:
                        self._wheels.add(m.group(1).lower())
        return name.lower() in self._wheels

    def thumbnail(self, car_id):
        """The car's picture: disc 1's UI/Textures.zip, else a pack's media/UI/Textures (a DLC car's)."""
        z = find_ci(self.media, "UI", "Textures.zip")
        data = zip_member(z, "Thumbnails/Thumbnail_%d.xds" % car_id)
        if data is not None:
            return data
        if self._thumbs is None:
            self._thumbs = {}
            for path, _ in self.packs:
                z = ZIPS.get(path)
                for i in z.infolist():
                    m = re.match(r"(?i)media/ui/textures/thumbnails/thumbnail_(\d+)\.xds$", i.filename)
                    if m:
                        self._thumbs[int(m.group(1))] = Member(z, path, i)
        m = self._thumbs.get(car_id)
        return m.read() if m else None

    def pack_audio(self):
        """{"cmt" | "et" | "ht": {lower file name: (zip path, ZipInfo)}}: the sound tuning files the
        packs add (a DLC car's CarModelTuning, EngineTuning and harmonic tuning: in its car zip
        under media/audio/cars, and in a CarModelTuning_pri_N.zip), later packs over earlier ones."""
        if self._audio is None:
            self._audio = {k: {} for k in PACK_AUDIO}
            for dz in self.pack_dirs:
                for f in sorted(os.listdir(dz)):
                    path = os.path.join(dz, f)
                    if PACK_ZIP.match(f):
                        for i in ZIPS.get(path).infolist():
                            low = i.filename.lower()
                            for k, pre in PACK_AUDIO.items():
                                if low.startswith(pre) and "/" not in low[len(pre):]:
                                    self._audio[k][low[len(pre):]] = (path, i)
                    elif PACK_CMT.match(f):
                        for i in ZIPS.get(path).infolist():
                            if not i.is_dir():
                                self._audio["cmt"][i.filename.lower()] = (path, i)
        return self._audio

    def banks(self):
        """{lower file name: (path, path under Soundbanks)}: FM4's engine sound banks, disc 1's
        and those the packs hold loose next to their zips (BANKS_DIR), later packs over earlier
        ones (#776)."""
        if getattr(self, "_banks", None) is None:
            self._banks = loose_banks(find_ci(self.media, *BANKS_DIR[1:]))
            for dz in self.pack_dirs:
                for d in sorted(os.listdir(dz)):
                    if os.path.isdir(os.path.join(dz, d)):
                        self._banks.update(loose_banks(find_ci(os.path.join(dz, d), *BANKS_DIR)))
        return self._banks

    def logos(self):
        """Lowercase name (ui4/logos/...) -> Member: the make logos in disc 1's UI/Textures.zip,
        and the packs' own (media/UI/Textures/UI4/Logos/...), later packs over earlier ones: the
        DLC makes' logos (MG, Porsche, smart, Triumph, Lincoln) ship only in their packs (#772)."""
        z = find_ci(self.media, "UI", "Textures.zip")
        out = logo_members(z) if z else {}
        for path, _ in self.packs:
            out.update(logo_members(path, "media/ui/textures/"))
        return out

    def languages(self):
        st = find_ci(self.media, "StringTables")
        return {f[:-4].lower(): os.path.join(st, f) for f in os.listdir(st) if f.lower().endswith(".zip")} if st else {}

    def strings(self, table, lang):
        """The table as FM4 sees it: disc 1's, the car packs' copies over it."""
        langs = self.languages()
        data = zip_member(langs.get(lang) or langs.get("en"), table + ".str")
        t = StringTable(data)
        for _, st in self.packs:
            if st:
                extra = zip_member(st, "%s/%s.str" % (lang.upper() if lang in langs else "EN", table))
                if extra is None:
                    extra = zip_member(st, "%s/%s.str" % (lang, table))
                if extra:
                    t.merge(StringTable(extra))
        return t


class FH1:
    """The player's FH1 files: the game folder, the title update, the installed DLC."""

    def __init__(self, game, update=None, dlc=None):
        self.media = find_ci(game, "media")
        if not self.media:
            raise ConvertError("no media folder in %s" % game)
        self.update = os.path.join(update, "media.zip") if update and os.path.isfile(os.path.join(update, "media.zip")) else None
        self.dlc_zips = []  # (priority, path)
        # database patches kept next to a pack's zip, not in it (UDLC's
        # Media/DLCZips/729_pri_99/Media/db/patch/72900_merge.slt): the game reads those too (#924)
        self.dlc_loose_patches = []
        if dlc and os.path.isdir(dlc):
            for root, _, files in os.walk(dlc):
                if PACK_NAME.lower() in root.lower():
                    continue  # this tool's own package (an earlier import)
                low = root.replace("\\", "/").lower()
                for f in files:
                    m = re.match(r"(?i).+_pri_(\d+)\.zip$", f)
                    if m and "dlczips" in low:
                        self.dlc_zips.append((int(m.group(1)), os.path.join(root, f)))
                    elif f.lower().endswith(".slt") and "/dlczips/" in low and low.endswith("/db/patch"):
                        self.dlc_loose_patches.append(os.path.join(root, f))
        self.dlc_zips.sort()
        self.dlc_loose_patches.sort()
        self._tmp = []
        self._roots = (game, update, dlc)

    def close(self):
        ZIPS.close(*self._roots)
        for p in self._tmp:
            try:
                os.unlink(p)
            except OSError:
                pass

    def _temp(self, data, suffix=".slt"):
        fd, p = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        self._tmp.append(p)
        return p

    def db(self):
        data = zip_member(self.update, "db/gamedb.slt") if self.update else None
        path = self._temp(data) if data else find_ci(self.media, "db", "gamedb.slt")
        self.db_file = path
        return sqlite3.connect("file:%s?mode=ro" % path, uri=True)

    def dlc_patches(self):
        """Paths of the installed DLC packs' database patches: those in their zips (FH1's own DLC) and
        those next to them (fan packs such as UDLC)."""
        if not hasattr(self, "_patches"):
            self._patches = []
            for _, path in self.dlc_zips:
                z = ZIPS.get(path)
                for i in z.infolist():
                    if re.search(r"(?i)/db/patch/[^/]+\.slt$", i.filename):
                        self._patches.append(self._temp(Member(z, path, i).read()))
            self._patches += self.dlc_loose_patches
        return self._patches

    def dlc_car_names(self):
        """MediaNames and ids of the installed DLC packs' cars (their DB patches)."""
        names, ids = set(), set()
        for p in self.dlc_patches():
            try:
                c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
                for cid, name in c.execute("select Id, MediaName from Data_Car"):
                    ids.add(cid)
                    names.add(str(name).lower())
                c.close()
            except sqlite3.Error:
                pass
        return names, ids

    def logos(self):
        """Lowercase member names of the make logos FH1 has (ui/Textures.zip, UI4/Logos/...)."""
        z = find_ci(self.media, "ui", "Textures.zip")
        return set(logo_members(z)) if z else set()

    def has_car_files(self, name):
        return bool(find_ci(self.media, "cars", name + ".zip"))

    def has_wheel(self, name):
        if find_ci(self.media, "wheels", name + ".zip"):
            return True
        for _, path in self.dlc_zips:
            z = ZIPS.get(path)
            if any(i.filename.lower().startswith("media/wheels/%s/" % name.lower()) for i in z.infolist()):
                return True
        return False

    def car_zips(self):
        d = find_ci(self.media, "cars")
        return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(".zip")] if d else []

    def languages(self):
        st = find_ci(self.media, "stringtables")
        return {f[:-4].lower(): os.path.join(st, f) for f in os.listdir(st) if f.lower().endswith(".zip")} if st else {}

    def strings_raw(self, table, lang):
        """(bytes of the copy the game uses, StringTable merged as the game merges them)."""
        data = zip_member(self.update, "stringtables/%s/%s.str" % (lang, table.lower())) if self.update else None
        if data is None:
            data = zip_member(self.languages().get(lang), table + ".str")
        top = data
        merged = StringTable(data)
        for _, path in self.dlc_zips:
            if re.match(r"(?i)stringtables_pri_\d+\.zip$", os.path.basename(path)):
                extra = zip_member(path, "%s/%s.str" % (lang, table))
                if extra:
                    merged.merge(StringTable(extra))
                    top = extra
        return top, merged


# ============================================================================= string tables

class StringTable:
    """An LSB2 string table (tools/extract_map_points.py describes the layout). Sections: 1
    the texts (u16 key, u32 offset in UTF-16 units, sorted by key, ended by key 0xFFFF), 2
    the IDS_ names (u16 key, u16 index, u32 byte offset), 4 one u16 per entry in key order
    (not decoded; kept as it is). A database reference "_&N" names key N & 0xFFFF; both
    games give the same IDS_ name the same key."""

    def __init__(self, data=None):
        self.entries = {}  # key -> [text, ids name, index, sec4 value]
        if not data:
            return
        if data[:4] != b"LSB2":
            raise ConvertError("not an LSB2 string table")
        n = struct.unpack_from(">I", data, 16)[0]
        offs = struct.unpack_from(">%dI" % n, data, 20)
        s1 = offs[0]
        count = struct.unpack_from(">I", data, s1 + 4)[0]
        pool = s1 + 8 + 6 * (count + 1)
        keys = []
        for i in range(count):
            key, off = struct.unpack_from(">HI", data, s1 + 8 + 6 * i)
            p = e = pool + 2 * off
            while e + 1 < len(data) and data[e:e + 2] != b"\0\0":
                e += 2
            self.entries[key] = [data[p:e].decode("utf-16-be", "replace"), None, i, 0]
            keys.append(key)
        if n > 1 and offs[1]:
            s2 = offs[1]
            c2 = struct.unpack_from(">I", data, s2 + 4)[0]
            pool2 = s2 + 8 + 8 * c2
            for i in range(c2):
                key, idx, off = struct.unpack_from(">HHI", data, s2 + 8 + 8 * i)
                e = data.index(b"\0", pool2 + off)
                if key in self.entries:
                    self.entries[key][1] = data[pool2 + off:e].decode("latin-1")
                    self.entries[key][2] = idx
        if n > 3 and offs[3]:
            s4 = offs[3]
            c4 = struct.unpack_from(">I", data, s4 + 4)[0]
            if c4 == count:
                for i, key in enumerate(keys):
                    self.entries[key][3] = struct.unpack_from(">H", data, s4 + 8 + 2 * i)[0]

    def merge(self, other):
        self.entries.update({k: list(v) for k, v in other.entries.items()})

    def text(self, ref):
        if not isinstance(ref, str) or not re.match(r"_&\d+$", ref):
            return None
        e = self.entries.get(int(ref[2:]) & 0xFFFF)
        return e[0] if e else None

    def by_name(self, name):
        for k, e in self.entries.items():
            if e[1] == name:
                return k, e
        return None, None

    def encode(self):
        keys = sorted(self.entries)
        # Texts in key order, names in index order (as in the game's own files).
        order = sorted(keys, key=lambda k: self.entries[k][2])
        text_off, pool = {}, bytearray()
        for k in keys:
            text_off[k] = len(pool) // 2
            pool += self.entries[k][0].encode("utf-16-be") + b"\0\0"
        sec1 = bytearray(struct.pack(">II", 0, len(keys)))
        for k in keys:
            sec1 += struct.pack(">HI", k, text_off[k])
        sec1 += struct.pack(">HI", 0xFFFF, len(pool) // 2) + pool
        sec1 += bytes(-len(sec1) % 4)  # sections are 4-byte aligned
        struct.pack_into(">I", sec1, 0, len(sec1))
        name_off, names = {}, bytearray()
        named = [k for k in order if self.entries[k][1]]
        for k in named:
            name_off[k] = len(names)
            names += self.entries[k][1].encode("latin-1") + b"\0"
        sec2 = bytearray(struct.pack(">II", 0, len(named)))
        for k in sorted(named):
            sec2 += struct.pack(">HHI", k, self.entries[k][2], name_off[k])
        sec2 += names
        sec2 += bytes(-len(sec2) % 4)
        struct.pack_into(">I", sec2, 0, len(sec2))
        sec4 = b"".join(struct.pack(">H", self.entries[k][3]) for k in keys)
        sec4 += bytes(-len(sec4) % 4)
        sec4 = struct.pack(">II", 8 + len(sec4), len(keys)) + sec4
        s1 = 36
        s2 = s1 + len(sec1)
        s4 = s2 + len(sec2)
        head = b"LSB2" + struct.pack(">4I", 0x01000000, 0x02000000, 36, 4) + struct.pack(">4I", s1, s2, 0, s4)
        return head + bytes(sec1) + bytes(sec2) + sec4

    def add(self, name, text, sec4, key):
        """Add a string; key is the one the other game gave it, moved to a free key when this
        table uses it for another string. Returns the key."""
        k0, e = self.by_name(name)
        if e is not None:
            return k0
        while key in self.entries or key == 0xFFFF:
            key = (key + 1) & 0xFFFF
        self.entries[key] = [text, name, len(self.entries), sec4]
        return key


# ============================================================================= database

# String tables the new rows need, and the (table, columns) whose "_&N" references are in them.
STRING_TABLES = {
    "Data_Car": [("Data_Car", ("DisplayName", "ModelShort"))],
    "List_CarMake": [("List_CarMake", ("DisplayName", "Profile")), ("Data_Car", ("MakeName",))],
}

AUDIO_ZIPS = {"cmt": "audio/cars/CarModelTuning.zip", "et": "audio/cars/Engines/EngineTuning.zip",
              "ht": "audio/cars/Engines/HarmonicTuning.zip"}

# FH1 retuned the engine sound of the cars it sells (their EngineTuning, CarModelTuning and
# harmonic tuning files); the FM4 cars it does not sell kept FM4's tuning, and in the game they
# are about 2 LU quieter than FH1's cars (#712, docs/modding/fm4-cars.md). An imported car's
# GlobalCarVolume curve (the engine's level over rpm and throttle) is raised by this factor, at
# most to 1.0, the highest value FH1's own tuning uses.
ENGINE_GAIN = 1.6
ENGINE_VOLUME_MAX = 1.0
_GCV = re.compile(rb"(<GlobalCarVolume\b.*?</GlobalCarVolume>)", re.S)
_CURVE_Y = re.compile(rb'(\by[0-9]+=")([0-9.eE+-]+)(")')


_TUNING_NAME = re.compile(r"(?i)^(\d*)([a-z]*)\d*_(.*)_([a-z]+)_HT\.xml$")


def similar_tunings(name, candidates):
    """candidates (harmonic tuning file names), the most like name first: those of the same part
    (Exh, Int, EngAmb), then the same kind of engine (the first letter after the cylinder count:
    N, T, S, R for rotary...), the same count, the same letters, the longest common start, the
    shortest. 5T1_A200_1478_Exh_HT.xml: 5T1_A200_Exh_HT.xml first."""
    m = _TUNING_NAME.match(name)
    count, letters, _, part = m.groups() if m else ("", "", "", "")
    low = name.lower()

    def key(c):
        n = _TUNING_NAME.match(c)
        cc, cl, _, cp = n.groups() if n else ("", "", "", "")
        common = len(os.path.commonprefix([low, c.lower()]))
        return (cp.lower() != part.lower(), cl[:1].lower() != letters[:1].lower(), cc != count,
                cl.lower() != letters.lower(), -common, len(c), c.lower())
    return sorted((c for c in candidates if c.lower() != low), key=key)


def _read_file(path):
    with open(path, "rb") as f:
        return f.read()


def sound_fallbacks(fh1_media, package, mod=None):
    """An import made before #776 left out the engine sound banks FH1 lacks (of a few FM4 DLC
    cars: the 1986 Audi Sport quattro S1, the 1985 RX-7, the 787B), and the game stopped with its
    disc read error where it needed one, at every start once such a car was the current one.
    Without FM4's files at hand, each harmonic tuning file of the import (package: its folder;
    mod: the mod's folder, whose copies the game reads first) whose bank FH1, the package and the
    mod all lack gets FH1's most similar one (similar_tunings) whose banks FH1 has:
    {its name: (FH1's file name, FH1's data)}. None when FH1's banks are not found (fh1_media:
    FH1's media folder)."""
    fh_banks = set(loose_banks(find_ci(fh1_media, *BANKS_DIR[1:])))
    if not fh_banks:
        return None
    have, hts = set(), {}
    dz = find_ci(package, "Media", "DLCZips") if package else None
    try:
        for f in sorted(os.listdir(dz)) if dz else []:
            p = os.path.join(dz, f)
            if os.path.isdir(p):
                have |= set(loose_banks(find_ci(p, *BANKS_DIR)))
            elif f.lower().endswith(".zip"):
                z = ZIPS.get(p)
                for i in z.infolist():
                    low = i.filename.lower()
                    if low.startswith(PACK_AUDIO["ht"]) and low.endswith(".xml") and "/" not in low[len(PACK_AUDIO["ht"]):]:
                        hts[low[len(PACK_AUDIO["ht"]):]] = (i.filename[len(PACK_AUDIO["ht"]):], Member(z, p, i).read)
        ht_dir = find_ci(mod, "media", "audio", "cars", "engines", "harmonictuning") if mod else None
        for f in sorted(os.listdir(ht_dir)) if ht_dir else []:
            path = os.path.join(ht_dir, f)
            hts[f.lower()] = (f, lambda path=path: _read_file(path))
        if mod:
            have |= set(loose_banks(find_ci(mod, *BANKS_DIR)))
        ok = fh_banks | have | PLACEHOLDER_BANKS
        fh_zip = find_ci(fh1_media, "audio", "cars", "Engines", "HarmonicTuning.zip")
        fh_ht = {}
        if fh_zip:
            z = ZIPS.get(fh_zip)
            fh_ht = {i.filename: Member(z, fh_zip, i) for i in z.infolist() if not i.is_dir()}
        lower_fh = {n.lower() for n in fh_ht}
        out = {}
        for low, (name, read) in sorted(hts.items()):
            if low in lower_fh:
                continue  # FH1's own copy is the one the game reads
            if all(b.lower() in ok for b in bank_names(read())):
                continue
            for cand in similar_tunings(name, fh_ht):
                data = fh_ht[cand].read()
                banks = bank_names(data)
                if banks and all(b.lower() in fh_banks for b in banks):  # a real bank, no placeholder
                    out[name] = (cand, data)
                    break
        return out
    finally:
        ZIPS.close(*(r for r in (package, fh1_media) if r))


def check_import(fh1, mod, package=None):
    """What an installed import lacks that the game reads for one of its cars (#776): for every
    car of the mod's database that FH1's database lacks, its model, its CarModelTuning, its
    engines' rows and EngineTuning, the harmonic tuning those name and the sound banks that
    names, each looked up where the game reads it (the mod first, then FH1's title update and
    disc, then the package's zips; a bank only as a loose file: FH1's disc or the package's
    folders). fh1: an FH1; mod, package: the folders. [(car, problem)], [] when complete."""
    mod_files = {}
    for root, _, files in os.walk(os.path.join(mod, "media")):
        for f in files:
            path = os.path.join(root, f)
            mod_files[os.path.relpath(path, mod).replace(os.sep, "/").lower()] = lambda p=path: _read_file(p)
    pack_files, banks = {}, set(loose_banks(find_ci(fh1.media, *BANKS_DIR[1:]))) | PLACEHOLDER_BANKS
    dz = find_ci(package, "Media", "DLCZips") if package else None
    for f in sorted(os.listdir(dz)) if dz else []:
        p = os.path.join(dz, f)
        if os.path.isdir(p):
            banks |= set(loose_banks(find_ci(p, *BANKS_DIR)))
        elif f.lower().endswith(".zip"):
            z = ZIPS.get(p)
            for i in z.infolist():
                pack_files.setdefault(i.filename.lower(), Member(z, p, i).read)
    fh_files = {}
    for kind, rel in AUDIO_ZIPS.items():
        z = find_ci(fh1.media, *rel.split("/"))
        if z:
            for i in ZIPS.get(z).infolist():
                fh_files["media/%s/%s" % (rel[:-4].lower(), i.filename.lower())] = Member(ZIPS.get(z), z, i).read
    if fh1.update:
        z = ZIPS.get(fh1.update)
        for i in z.infolist():
            fh_files["media/" + i.filename.lower()] = Member(z, fh1.update, i).read

    def find(rel):
        rel = rel.lower()
        return mod_files.get(rel) or fh_files.get(rel) or pack_files.get(rel)
    db = sqlite3.connect("file:%s?mode=ro" % find_ci(mod, "media", "db", "gamedb.slt"), uri=True)
    fh1db = fh1.db()
    problems, done_ht = [], {}
    try:
        have = {r[0] for r in fh1db.execute("select Id from Data_Car")}
        has_motors = bool(db.execute("select 1 from sqlite_master where name='List_UpgradeMotor'").fetchone()
                          and db.execute("select 1 from sqlite_master where name='Data_Motor'").fetchone())
        for cid, name in db.execute("select Id, MediaName from Data_Car order by Id"):
            if cid in have:
                continue
            car = "%s (%d)" % (name, cid)
            if not find("media/cars/%s/%s.carbin" % (name, name)):
                problems.append((car, "no model media/cars/%s/%s.carbin" % (name, name)))
            if not find(PACK_AUDIO["cmt"] + name + "_CMT.xml"):
                problems.append((car, "no CarModelTuning %s_CMT.xml" % name))
            engines = [r[0] for r in db.execute(
                "select e.MediaName from List_UpgradeEngine u left join Data_Engine e on "
                "e.EngineID=u.EngineID where u.Ordinal=?", (cid,))]
            if not engines and has_motors:  # an electric car (the Tesla): its motor's tuning
                engines = [r[0] for r in db.execute(
                    "select m.MediaName from List_UpgradeMotor u left join Data_Motor m on "
                    "m.MotorID=u.MotorID where u.Ordinal=?", (cid,))]
            if not engines:
                problems.append((car, "no engine or motor rows (List_UpgradeEngine, List_UpgradeMotor)"))
            for engine in engines:
                if engine is None:
                    problems.append((car, "an engine or motor row names one its table lacks"))
                    continue
                read = find(PACK_AUDIO["et"] + engine + "_ET.xml")
                if not read:
                    problems.append((car, "no EngineTuning %s_ET.xml" % engine))
                    continue
                for ht in sorted(set(re.findall(r'"([^"]+_HT\.xml)"', read().decode("latin-1")))):
                    if ht.lower() not in done_ht:
                        hread = find(PACK_AUDIO["ht"] + ht)
                        done_ht[ht.lower()] = (["no harmonic tuning %s" % ht] if not hread else
                                               ["%s names the sound bank %s, which no file has" % (ht, b)
                                                for b in bank_names(hread()) if b.lower() not in banks])
                    problems += [(car, p) for p in done_ht[ht.lower()]]
    finally:
        db.close()
        fh1db.close()
        ZIPS.close(*(r for r in (package,) if r))
    return problems


# A traffic car's cut-down EngineTuning: its live (uncommented) exhaust, intake or engine ambient
# slot names a Traffic_*_HT.xml (a few low loops only) or an NA_*_HT.xml (the NA.fsb placeholder).
# FH1 keeps such a copy for some FM4 cars it cut as traffic cars (the 2006 Focus ST, the 2011 A4,
# Spark and Volt, the 2009 xD), their real slots commented out or gone (#840).
_COMMENT = re.compile(rb"<!--.*?-->", re.S)
_TRAFFIC_SLOT = re.compile(rb'(?i)"(?:Traffic|NA)_(?:[^"]*_)?(?:Exh|Int|EngAmb)_HT\.xml"')


def traffic_tuning(data):
    """True when an EngineTuning file (bytes) is a traffic car's placeholder (_TRAFFIC_SLOT)."""
    return bool(_TRAFFIC_SLOT.search(_COMMENT.sub(b"", data)))


def louder_engine(data, gain=ENGINE_GAIN):
    """An EngineTuning file (bytes) with its GlobalCarVolume curve's y values times gain (at
    most ENGINE_VOLUME_MAX); everything else is left as it is."""
    def curve(m):
        return _CURVE_Y.sub(lambda y: y.group(1) + b"%.6f" % min(ENGINE_VOLUME_MAX, float(y.group(2)) * gain)
                            + y.group(3), m.group(1))
    return _GCV.sub(curve, data, count=1)

# Tables keyed by the car's id, and the column holding it.
CAR_TABLES = [
    ("Data_Car", "Id"), ("CameraOverrides", "CarId"), ("CarExceptions", "CarID"),
    ("CarPartPositions", "Ordinal"), ("Combo_Colors", "Ordinal"), ("Combo_Engines", "Ordinal"),
    ("List_AntiSwayPhysics", "Ordinal"), ("List_SpringDamperPhysics", "Ordinal"),
    ("List_UpgradeAntiSwayFront", "Ordinal"), ("List_UpgradeAntiSwayRear", "Ordinal"),
    ("List_UpgradeBrakes", "Ordinal"), ("List_UpgradeCarBody", "Ordinal"),
    ("List_UpgradeDrivetrain", "Ordinal"), ("List_UpgradeEngine", "Ordinal"),
    ("List_UpgradeRearWing", "Ordinal"), ("List_UpgradeRimSizeFront", "Ordinal"),
    ("List_UpgradeRimSizeRear", "Ordinal"), ("List_UpgradeSpringDamper", "Ordinal"),
    ("List_UpgradeTireCompound", "Ordinal"), ("List_UpgradeMotor", "Ordinal"),
]
# Second level: (key family, column in the first-level rows that names it).
FAMILIES = {
    "engine": [("List_UpgradeEngine", "EngineID"), ("Combo_Engines", "EngineID")],
    "body": [("List_UpgradeCarBody", "CarBodyID")],
    "drivetrain": [("List_UpgradeDrivetrain", "DrivetrainID")],
    "motor": [("List_UpgradeMotor", "MotorID")],
}
FAMILY_TABLES = {
    "engine": [("Data_Engine", "EngineID")],
    "body": [("Data_CarBody", "Id")],
    "drivetrain": [("Data_Drivetrain", "DrivetrainID")],
    "motor": [("Data_Motor", "MotorID"), ("List_UpgradeMotorParts", "MotorID")],
}
FAMILY_PREFIX = {"engine": "List_UpgradeEngine", "body": "List_UpgradeCarBody",
                 "drivetrain": "List_UpgradeDrivetrain"}
# The column naming a part of each family in the rows of the part's own tables.
PART_COLUMN = {"engine": "EngineID", "body": "CarBodyID", "drivetrain": "DrivetrainID", "motor": "MotorID"}
# Third level: any column of the rows above with these names points into these tables.
REFS = [(re.compile(r"(?i)^TorqueCurve.*ID$"), "List_TorqueCurve", "TorqueCurveID"),
        (re.compile(r"(?i)^AeroPhysicsID$"), "List_AeroPhysics", "AeroPhysicsID")]

# Data_Car columns FH1 retuned for every car (docs/modding/fm4-cars.md): mapped from FM4's
# value with a fit over the cars both games have.
RETUNED = ["PerformanceIndex", "BodyAeroForwardDownforceFront", "BodyAeroForwardDownforceRear",
           "HandlingRating", "BrakingRating", "AccelerationRating", "LaunchRating", "SpeedRating",
           "SimTimeTo60MPH", "SimTimeTo100MPH", "SimTimeQuarterMile", "SimTopSpeed",
           "SimBrakeDistance60MPH", "SimBrakeDistance100MPH", "SimLatGees60MPH",
           "SimLatGees120MPH", "SimSpeedQuarterMile"]

# Rally parts (#924), as FH1's own cars have them (190 rows of each in the title update's database)
# and as Forza Imported DLC Maker gives FM4 cars: measured there on the Rally Expansion's 16 cars
# (its DLC offer 1600), matching UDLC's. Added with or without the Rally Expansion: a car's rows
# must not go when an import is made again without it (a part fitted in a save whose row is missing
# stops the car from loading), and FH1's own cars have them in the title update's database.
RALLY_TIRE = {"Level": 5, "TireCompoundID": 9, "Price": 10000}
RALLY_GEARBOX = {"FinalDriveRatio": 4.4, "NumGears": 7, "GearRatio1": 3.3, "GearRatio2": 2.05, "GearRatio3": 1.5,
                 "GearRatio4": 1.17, "GearRatio5": 0.97, "GearRatio6": 0.825, "GearRatio7": -1.0, "GearRatio8": -1.0}
RALLY_SPRING_PER_L1 = 0.649             # rally spring rate / the car's level-1 spring's (median, front and rear)
RALLY_SPRING_PER_L3 = (0.2914, 0.2354)  # front, rear: / the level-3 spring's, for a car with no level-1 row
RALLY_BUMP = 0.1489                     # damper bump rate / spring rate
RALLY_REBOUND = 0.0851                  # damper rebound rate / spring rate
RALLY_BUMPSTOP = 80.0
RALLY_RIDE = 1.166                      # rally ride height / the stock body's ride height (median)
RALLY_MASS = 0.3                        # the rally suspension's MassDiff / level 3's


def columns(db, table):
    return [r[1] for r in db.execute("pragma table_info([%s])" % table)]


def tables(db):
    return [r[0] for r in db.execute("select name from sqlite_master where type='table'")]


def _lstsq(X, y):
    """Least squares with the normal equations (small, well-conditioned problems)."""
    n = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) + (1e-9 if i == j else 0) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * t for r, t in zip(X, y)) for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(A[r][c]))
        A[c], A[p], b[c], b[p] = A[p], A[c], b[p], b[c]
        for r in range(n):
            if r != c and A[c][c]:
                f = A[r][c] / A[c][c]
                A[r] = [a - f * x for a, x in zip(A[r], A[c])]
                b[r] -= f * b[c]
    return [b[i] / A[i][i] if A[i][i] else 0.0 for i in range(n)]


class Retune:
    """FM4 -> FH1 mapping of the retuned Data_Car columns, fitted on the cars both games have
    (same id and MediaName): each column from its FM4 value (linear), the performance index
    from FM4's index and ratings. Downforce that maps to zero or less is FH1's "none" (front
    0, rear -1), as on FH1's cars."""

    def __init__(self, fm4db, fh1db):
        cols = [c for c in RETUNED if c in columns(fm4db, "Data_Car") and c in columns(fh1db, "Data_Car")]
        q = "select Id, MediaName, %s from Data_Car" % ",".join("[%s]" % c for c in cols)
        fm = {r[0]: r for r in fm4db.execute(q)}
        fh = {r[0]: r for r in fh1db.execute(q)}
        shared = [i for i in fm if i in fh and str(fm[i][1]).lower() == str(fh[i][1]).lower()]
        self.cols = cols
        self.models = {}
        ix = {c: 2 + k for k, c in enumerate(cols)}
        if len(shared) < 20:
            raise ConvertError("too few cars in both games (%d) to fit FH1's tuning" % len(shared))
        for c in cols:
            if c == "PerformanceIndex":
                feats = ["PerformanceIndex", "HandlingRating", "BrakingRating", "SpeedRating", "AccelerationRating"]
                feats = [f for f in feats if f in ix]
            else:
                feats = [c]
            rows = [i for i in shared if all(isinstance(fm[i][ix[f]], (int, float)) for f in feats)
                    and isinstance(fh[i][ix[c]], (int, float))]
            if c.startswith("BodyAeroForwardDownforce"):
                rows = [i for i in rows if fh[i][ix[c]] > 0]
            if len(rows) < 10:
                continue
            X = [[float(fm[i][ix[f]]) for f in feats] + [1.0] for i in rows]
            y = [float(fh[i][ix[c]]) for i in rows]
            self.models[c] = (feats, _lstsq(X, y))
        # CarClasses.MaxPerformanceIndex is each class's upper bound (E 0.459, D 0.539, ...)
        self.classes = sorted((r[1], r[0]) for r in fh1db.execute(
            "select Id, MaxPerformanceIndex from CarClasses where Id < 10"))

    def apply(self, row):
        """row: dict of an FM4 Data_Car row; changed in place."""
        src = dict(row)
        for c, (feats, coef) in self.models.items():
            vals = [src.get(f) for f in feats]
            if any(not isinstance(v, (int, float)) for v in vals):
                continue
            v = sum(a * float(x) for a, x in zip(coef, vals)) + coef[-1]
            if c == "BodyAeroForwardDownforceFront":
                v = max(0.0, round(v))
            elif c == "BodyAeroForwardDownforceRear":
                v = round(v) if v > 0 else -1.0
            elif c == "PerformanceIndex":
                v = min(0.999, max(0.01, v))
            row[c] = v
        pi = row.get("PerformanceIndex")
        cls = self.classes[-1][1]
        for maxpi, cid in self.classes:
            if pi <= maxpi:
                cls = cid
                break
        row["ClassID"] = cls


def _make_key(text):
    return re.sub(r"[^0-9a-z]", "", (text or "").casefold())


def _unrelated(a, b):
    """Two make names' keys (_make_key) for two makes: both known, and neither equal to the other
    nor its start (FM4's "GMC Truck" is FH1's "GMC")."""
    return bool(a and b) and not (a == b or a.startswith(b) or b.startswith(a))


def _make_keys(db, names):
    """{make id: _make_key of its English name} of a game's List_CarMake."""
    return {mid: _make_key(names.text(name))
            for mid, name in db.execute("select ID, DisplayName from List_CarMake order by ID")}


def make_merges(fm4db, fh1db, fm4_names, fh1_names):
    """{FM4 make id: FH1 make id} for the FM4 makes FH1 has under another id: the same English
    name. FM4's Eagle (87, the Talon's) is FH1's Eagle (102, the Speedster's); as a make of its
    own it was a second "Eagle" in the Autoshow (#710). That goes too for an FM4 make whose id
    FH1 uses for another make: FM4's Hennessey (102) is FH1's Hennessey (103), not FH1's 102,
    Eagle (#772). fm4_names and fh1_names are the two games' List_CarMake string tables."""
    fh_keys = _make_keys(fh1db, fh1_names)
    fh = {}
    for mid, key in fh_keys.items():
        if key:
            fh.setdefault(key, mid)
    out = {}
    for mid, key in _make_keys(fm4db, fm4_names).items():
        if mid in fh_keys and not _unrelated(key, fh_keys[mid]):
            continue  # FH1's own make under the same id
        hit = fh.get(key)
        if hit is not None and hit != mid:
            out[mid] = hit
    return out


def make_moves(fm4db, fh1db, fm4_names, fh1_names, merges):
    """{FM4 make id: new id} for the FM4 makes whose id FH1 uses for another make, when FH1 has no
    make of their name (make_merges takes those): FM4's Lincoln (105, the 1962 Continental's) is
    FH1's Viper, and the Continental showed as a "Viper Continental" (#772). The new ids follow the
    highest either game uses (FH1's DLC packs add none above its own)."""
    fh_keys = _make_keys(fh1db, fh1_names)
    fm4_keys = _make_keys(fm4db, fm4_names)
    new = max(list(fh_keys) + list(fm4_keys) + [0]) + 1
    out = {}
    for mid, key in fm4_keys.items():
        if mid in merges or mid not in fh_keys or not _unrelated(key, fh_keys[mid]):
            continue
        out[mid] = new
        new += 1
    return out


def made_from(v, key):
    """True for an id made from key, as the games key a car's or a part's rows: key x 1000 + n."""
    return isinstance(v, int) and not isinstance(v, bool) and key * 1000 <= v < (key + 1) * 1000


def electric_only(db, cid):
    """True for a car with electric motor rows and no engine row (FM4's Leaf and Tesla Roadster)."""
    try:
        motor = db.execute("select 1 from List_UpgradeMotor where Ordinal=? limit 1", (cid,)).fetchone()
        engine = db.execute("select 1 from List_UpgradeEngine where Ordinal=? limit 1", (cid,)).fetchone()
    except sqlite3.Error:  # a database without the motor tables
        return False
    return bool(motor) and not engine


def twin_file(rel, name, out):
    """A car's (or wheel's) file under its copy's name (twin_name): the models found by the car's
    name (<name>*.carbin at the top), the digital gauge (DigitalGauge/Dash_<name>.*) and a wheel's
    last_<name>*.xml. Other files keep their names: the gauge's .fbf names its textures."""
    if name == out:
        return rel
    folder, sep, base = rel.rpartition("/")
    low, key = base.lower(), name.lower()
    if not folder and low.endswith(".carbin") and low.startswith(key):
        base = out + base[len(name):]
    elif folder.lower() == "digitalgauge" and low.startswith("dash_" + key):
        base = base[:5] + out + base[5 + len(name):]
    elif not folder and low.startswith("last_" + key) and low.endswith(".xml"):
        base = base[:5] + out + base[5 + len(name):]
    return folder + sep + base


def table_cars(table, fm4db):
    """{FM4 car id: (its id in the id table, its MediaName there)} for the FM4 cars the table
    (car_ids.json, #924) numbers or names otherwise than FM4 does: the copies of FH1's traffic cars
    (#841: 55000 + id, VW_5_Beetle_04), the cars whose FM4 id FH1 or its DLC gives another car
    (55000 + id) and those FH2 has under another id (the SLK55 AMG is FH2's 51525)."""
    out = {}
    for sid, c in table.source_cars("FM4").items():
        row = fm4db.execute("select MediaName from Data_Car where Id=?", (sid,)).fetchone()
        if row and (c["id"] != sid or c["name"].lower() != str(row[0]).lower()):
            out[sid] = (c["id"], c["name"])
    return out


def table_parts(table):
    """{"engine" | "drivetrain" | "wheel": {FM4 id: (new id, new MediaName or None)}}: the FM4
    parts the id table gives ids of their own (#924; car_ids.py says which and why)."""
    return {kind: dict(table.parts.get(("FM4", kind), {})) for kind in car_ids.PART_KINDS}


def engine_clash(fm4db, fh1db, engine_id):
    """True when FH1's database has an engine of that id that is not FM4's (another MediaName)."""
    try:
        theirs = fh1db.execute("select MediaName from Data_Engine where EngineID=?", (engine_id,)).fetchone()
        ours = fm4db.execute("select MediaName from Data_Engine where EngineID=?", (engine_id,)).fetchone()
    except sqlite3.Error:
        return False
    return bool(theirs and ours and str(theirs[0]).lower() != str(ours[0]).lower())


class Database:
    """Builds the DB patch: the selected cars' rows from FM4's database in FH1's tables.
    merges: {FM4 make id: FH1 make id} (make_merges), the cars of those makes join FH1's;
    moves: {FM4 make id: new id} (make_moves), those makes and their cars take the new id."""

    def __init__(self, fm4db, fh1db, merges=None, moves=None, renumber=None, parts=None, released=None):
        self.fm4, self.fh1 = fm4db, fh1db
        # the cars (their ids in the import) the released import had: they keep FM4's level-4
        # tire (_drop_drag_tires)
        self.released = set(released or ())
        self.merges = dict(merges or {})
        self.moves = dict(moves or {})
        # the id table's (#924): {FM4 car id: (its id, its MediaName)} (table_cars) and
        # {kind: {FM4 part id: (new id, new MediaName or None)}} (table_parts)
        self.renumber = dict(renumber or {})
        self.parts = {kind: dict(v) for kind, v in (parts or {}).items()}
        # {FM4 engine MediaName: its MediaName in the import} for the engines the table renames
        self.part_names = {}
        self._copied = []  # (table, key) of the torque curve and aero rows copied under a new id
        self._next_key = {}  # table -> the next key after the highest either game uses (_fresh_key)
        # "_&N" references the rows take from FH1's own tables, not FM4's (strings() skips them)
        self.fh1_refs = set()
        # the moved makes' names: FM4's own strings although FH1 has its make of the same id
        # under the same key and IDS_ name (strings() adds them under a key of their own)
        self.own_refs = set()
        self.fh1_tables = set(tables(fh1db))
        self.fm4_tables = set(tables(fm4db))
        self.rows = {}  # table -> {key tuple: dict row}
        self.retune = Retune(fm4db, fh1db)
        self.defaults = {}
        fh_cols = columns(fh1db, "Data_Car")
        for c in fh_cols:
            if c not in columns(fm4db, "Data_Car"):
                vals = {}
                for (v,) in fh1db.execute("select [%s] from Data_Car" % c):
                    vals[v] = vals.get(v, 0) + 1
                self.defaults[c] = max(vals.items(), key=lambda kv: kv[1])[0] if vals else None
        self.defaults["IsSelectable"] = 1

    def _select(self, table, col, values):
        if table not in self.fm4_tables or table not in self.fh1_tables or not values:
            return []
        cols = columns(self.fm4, table)
        if col not in cols:
            return []
        out = []
        vals = sorted(values)
        for i in range(0, len(vals), 500):
            chunk = vals[i:i + 500]
            q = "select * from [%s] where [%s] in (%s)" % (table, col, ",".join("?" * len(chunk)))
            out += [dict(zip(cols, r)) for r in self.fm4.execute(q, chunk)]
        return out

    def _add(self, table, rows):
        if not rows:
            return
        pk = columns(self.fh1, table)[0]
        have = self.rows.setdefault(table, {})
        for r in rows:
            have[r.get(pk)] = r

    def collect(self, car_ids):
        car_ids = set(car_ids)
        first = {}
        for table, col in CAR_TABLES:
            rows = self._select(table, col, car_ids)
            if table == "List_UpgradeEngine":
                # an engine swap whose engine id FH1 gives another engine (the DLC's Porsche GT2 RS
                # engine is FH1's Camry's) and that the id table gives no id of its own: left out,
                # the car would get FH1's engine instead. A car whose stock engine is such an
                # engine is not converted at all (Converter.needs).
                moved = self.parts.get("engine", {})
                rows = [r for r in rows if r.get("IsStock") or r.get("EngineID") in moved
                        or not engine_clash(self.fm4, self.fh1, r.get("EngineID"))]
            first[table] = rows
            self._add(table, rows)
        level2 = []
        for fam, srcs in FAMILIES.items():
            keys = {r[c] for t, c in srcs for r in first.get(t, []) if r.get(c) is not None}
            for table, col in FAMILY_TABLES.get(fam, []):
                rows = self._select(table, col, keys)
                self._add(table, rows)
                level2 += rows
            prefix = FAMILY_PREFIX.get(fam)
            if prefix:
                for table in sorted(self.fm4_tables):
                    if table.startswith(prefix) and table != prefix:
                        col = [c for c in columns(self.fm4, table) if c.lower() == {
                            "engine": "engineid", "body": "carbodyid", "drivetrain": "drivetrainid"}[fam]]
                        if col:
                            rows = self._select(table, col[0], keys)
                            self._add(table, rows)
                            level2 += rows
        for r in first.get("Data_Car", []):
            to = self.merges.get(r.get("MakeID"))
            if to is not None:
                name = self.fh1.execute("select DisplayName from List_CarMake where ID=?", (to,)).fetchone()
                r["MakeID"] = to
                if name and "MakeName" in r:
                    r["MakeName"] = name[0]
                    self.fh1_refs.add(name[0])
            elif r.get("MakeID") in self.moves:
                r["MakeID"] = self.moves[r["MakeID"]]
                self.own_refs.add(r.get("MakeName"))
        # the stock wheels' rows: FH1's database has those of most FM4 cars, not of the DLC's
        # Porsche Expansion and some later packs (a car without its wheel row did not load)
        self._add("List_Wheels", self._select("List_Wheels", "ID", {
            r["StockWheelID"] for r in first.get("Data_Car", []) if r.get("StockWheelID") is not None}))
        makes = {r["MakeID"] for r in first.get("Data_Car", []) if r.get("MakeID") is not None}
        makes -= set(self.merges.values())  # FH1's own rows
        back = {new: old for old, new in self.moves.items()}
        make_rows = self._select("List_CarMake", "ID", {back.get(m, m) for m in makes})
        for row in make_rows:
            if row["ID"] in self.moves:
                row["ID"] = self.moves[row["ID"]]
                self.own_refs.update(row.get(c) for c in ("DisplayName", "Profile"))
        self._add("List_CarMake", make_rows)
        all_rows = [r for rows in first.values() for r in rows] + level2
        for pat, table, col in REFS:
            keys = {v for r in all_rows for c, v in r.items() if pat.match(c) and v is not None}
            self._add(table, self._select(table, col, keys))
        self._renumber_cars()
        self._move_parts()
        self._drop_unnamed()
        self._drop_drag_tires()

    def _drop_drag_tires(self):
        """FM4's level-4 tire is its drag tire (compound 9). FH1's own cars have none: their tires
        stop at level 3 but for the rally tire (level 5, compound 9; rally_parts), and Forza Imported
        DLC Maker leaves it out of every car too. It is left out only of the cars the released
        import never had: a car it had (self.released) keeps it, since a save can have it fitted,
        and a car whose fitted part's row is missing does not load (#924). A stock tire is kept (no
        FM4 car has one at level 4)."""
        tires = self.rows.get("List_UpgradeTireCompound", {})
        for key in [k for k, r in tires.items() if (r.get("Level") or 0) >= 4 and not r.get("IsStock")
                    and r.get("Ordinal") not in self.released]:
            del tires[key]

    def _drop_unnamed(self):
        """A torque curve or aero row copied under a new id (_renumber_cars, _move_part) goes from
        its old id when no row names it there any more: it would stay in the FM4 car's or part's
        range, where FH1 or its DLC keeps its own (the Cayman R's aero rows at 1541900, FH1's DLC
        car 1541's range)."""
        named = set()
        for t, rows in self.rows.items():
            for row in rows.values():
                for col, v in row.items():
                    for pat, rt, rc in REFS:
                        if pat.match(col) and not (t == rt and col == rc):
                            named.add((rt, v))
        for rt, old in self._copied:
            if (rt, old) not in named:
                self.rows.get(rt, {}).pop(old, None)

    def _fh1_keys(self, table):
        """FH1's keys (first column) of a table, read once."""
        cache = self.__dict__.setdefault("_fh1_key_cache", {})
        if table not in cache:
            cache[table] = set()
            if table in self.fh1_tables:
                pk = columns(self.fh1, table)[0]
                cache[table] = {v for (v,) in self.fh1.execute("select [%s] from [%s]" % (pk, table))}
        return cache[table]

    def rally_parts(self, cars):
        """Rally parts for the import's cars (their ids in the import, after collect; #924), as FH1's
        own cars and the Rally Expansion's have them, and as Forza Imported DLC Maker gives FM4 cars:
        - rally tires, level 5 and compound 9 (RALLY_TIRE, from the car's highest tire below
          level 4: a car the released import had keeps FM4's level-4 drag tire, _drop_drag_tires);
        - a level-4 rally suspension: the level-3 one with RALLY_MASS of its mass, its springs,
          dampers and ride height scaled from the car's own (the level-1 springs, the stock body's
          ride height) the way the Rally Expansion's are;
        - a level-4 rally gearbox (RALLY_GEARBOX) for each of their drivetrains that FH1 lacks (one
          FH1 has keeps FH1's rows, which have theirs).
        An electric car or a hybrid (a motor row) gets none, nor a part a car has at that level
        already; a part whose key FH1 or the import already uses is not added. Returns the counts
        ("cars": the cars that got tires or a suspension)."""
        rows = self.rows
        done = {"tires": 0, "suspension": 0, "gearbox": 0}
        got = set()

        def free(table, key):
            return key not in rows.get(table, {}) and key not in self._fh1_keys(table)

        def of(table, col, value):
            return [r for r in rows.get(table, {}).values() if r.get(col) == value]

        def level(r):
            return r.get("Level") or 0

        motor = {r.get("Ordinal") for r in rows.get("List_UpgradeMotor", {}).values()}
        mine = [c for c in cars if c not in motor]
        tires = rows.setdefault("List_UpgradeTireCompound", {})
        for car in mine:
            own = sorted(of("List_UpgradeTireCompound", "Ordinal", car), key=level)
            rest = [r for r in own if level(r) < 4]
            key = car * 1000 + RALLY_TIRE["Level"]
            if rest and level(own[-1]) <= 4 and not any(r.get("IsStock") for r in own if level(r) >= 4) \
                    and free("List_UpgradeTireCompound", key):
                tires[key] = dict(rest[-1], Id=key, IsStock=0, **RALLY_TIRE)
                done["tires"] += 1
                got.add(car)
        springs = rows.setdefault("List_UpgradeSpringDamper", {})
        physics = rows.setdefault("List_SpringDamperPhysics", {})
        for car in mine:
            own = of("List_UpgradeSpringDamper", "Ordinal", car)
            u3 = next((r for r in own if level(r) == 3), None)
            u1 = next((r for r in own if level(r) == 1), None)
            stock_body = next((r for r in of("List_UpgradeCarBody", "Ordinal", car) if r.get("IsStock")), None)
            body = rows.get("Data_CarBody", {}).get(stock_body.get("CarBodyID")) if stock_body else None
            ids = (car * 1000 + 4, car * 1000 + 104)
            if any(level(r) >= 4 for r in own) or not u3 or not body or not free("List_UpgradeSpringDamper", ids[0]) \
                    or not all(free("List_SpringDamperPhysics", i) for i in ids):
                continue
            stock_springs = next((r for r in own if r.get("IsStock")), None)
            new = []
            for side, (pid, col) in enumerate(zip(ids, ("FrontSpringDamperPhysicsID", "RearSpringDamperPhysicsID"))):
                p3 = physics.get(u3.get(col))
                p1 = physics.get(u1.get(col)) if u1 else None
                ride = body.get(("ModelFrontStockRideHeight", "ModelRearStockRideHeight")[side])
                if p3 is None or ride is None:
                    break
                spring = (p1["DefSpringRate"] * RALLY_SPRING_PER_L1 if p1 and p1.get("DefSpringRate")
                          else p3["DefSpringRate"] * RALLY_SPRING_PER_L3[side])
                lowest = p3.get("MinRideHeight") or 0
                ride = round(ride * RALLY_RIDE, 4)
                if ride < lowest:
                    # the body's ride height is out of line with the car's own suspension (the
                    # Cayman R's body says 0.06 m, its springs 0.15 m; 26 of FM4's cars, race cars
                    # such as the R8 LMS): from the stock suspension's instead. On FH1's own cars
                    # the rally ride height is 1.2 times either (the medians).
                    stock_ride = physics.get(stock_springs.get(col)) if stock_springs else None
                    if stock_ride and stock_ride.get("DefRideHeight"):
                        ride = round(stock_ride["DefRideHeight"] * RALLY_RIDE, 4)
                # never below the suspension's lowest, as in all of FH1's own rally rows
                ride = max(ride, lowest)
                new.append(dict(p3, SpringDamperPhysicsID=pid, DefRideHeight=ride,
                                MaxRideHeight=max(ride, p3.get("MinRideHeight") or 0), DefSpringRate=round(spring, 3),
                                DefDampenBumpRate=round(spring * RALLY_BUMP, 4),
                                DefDampenReboundRate=round(spring * RALLY_REBOUND, 4), BumpstopStiffness=RALLY_BUMPSTOP))
            else:
                for p in new:
                    physics[p["SpringDamperPhysicsID"]] = p
                springs[ids[0]] = dict(u3, Id=ids[0], Level=4, IsStock=0, MassDiff=(u3.get("MassDiff") or 0) * RALLY_MASS,
                                       FrontSpringDamperPhysicsID=ids[0], RearSpringDamperPhysicsID=ids[1])
                done["suspension"] += 1
                got.add(car)
        # the gearboxes: each drivetrain of those cars that FH1 lacks, but one a motor car uses
        uses = {}
        for r in rows.get("List_UpgradeDrivetrain", {}).values():
            uses.setdefault(r.get("DrivetrainID"), set()).add(r.get("Ordinal"))
        box = rows.get("List_UpgradeDrivetrainTransmission", {})

        def drivetrain(r):  # the column naming the drivetrain, however the table spells it
            return next((v for c, v in r.items() if c.lower() == "drivetrainid"), None)

        for d in sorted(d for d, users in uses.items() if d is not None and users & set(mine) and not users & motor):
            if d in self._fh1_keys("Data_Drivetrain"):
                continue
            own = [r for r in box.values() if drivetrain(r) == d]
            key = d * 1000 + 4
            if not own or any(level(r) >= 4 for r in own) or not free("List_UpgradeDrivetrainTransmission", key):
                continue
            box[key] = dict(max(own, key=level), Id=key, Level=4, IsStock=0, **RALLY_GEARBOX)
            done["gearbox"] += 1
        done["cars"] = len(got)
        return done

    def _keys(self, table, col):
        """Every value of col in table, in either game's database and in the rows collected."""
        out = {r.get(col) for r in self.rows.get(table, {}).values()}
        for db, have in ((self.fm4, self.fm4_tables), (self.fh1, self.fh1_tables)):
            if table in have and col in columns(db, table):
                out |= {v for (v,) in db.execute("select [%s] from [%s]" % (col, table))}
        return out

    def _fresh_key(self, table):
        """A key of table after the highest either game (or the rows collected) uses."""
        if table not in self._next_key:
            pk = columns(self.fh1, table)[0]
            self._next_key[table] = 1 + max([0] + [v for v in self._keys(table, pk)
                                                   if isinstance(v, int) and not isinstance(v, bool)])
        self._next_key[table] += 1
        return self._next_key[table] - 1

    def _renumber_cars(self):
        """The cars the id table renumbers (table_cars): the car id and every id made from it (car
        id x 1000 + n: its upgrade, physics, part, colour and body rows, and the references between
        them), its MediaName and thumbnail. A row of its own whose key is neither (its engine
        combos) takes a key after the highest either game uses. The engine, drivetrain and motor
        tables are left alone (an engine's rows can have the ids of a car's: engine 282's are
        282000 + n), and so are the torque curves and aero rows: a car's references to one in its
        range get a copy of it under the new id, the row staying for the cars sharing it."""
        if not self.renumber:
            return
        car_col = dict(CAR_TABLES)
        parts = {t for fam in ("engine", "drivetrain", "motor") for t in self._part_tables(fam)}
        refs = {rt for _, rt, _ in REFS}
        copies = []
        for table, rows in list(self.rows.items()):
            if table in parts or table in refs:
                continue
            pk = columns(self.fh1, table)[0]
            rekeyed = {}
            for key, row in rows.items():
                c = row.get(car_col[table]) if table in car_col else None
                if c not in self.renumber:
                    c = next((t for t in self.renumber for col, v in row.items()
                              if col.lower().endswith("id") and made_from(v, t)), None)
                if c is None:
                    rekeyed[key] = row
                    continue
                n, name = self.renumber[c]
                for col, v in list(row.items()):
                    if table in car_col and col == car_col[table] and v == c:
                        row[col] = n
                    elif col.lower().endswith("id") and made_from(v, c):
                        row[col] = n * 1000 + (v - c * 1000)
                        copies += [(rt, rc, v, row[col]) for pat, rt, rc in REFS if pat.match(col)]
                if table == "Data_Car":
                    row["MediaName"] = name
                    if isinstance(row.get("Thumbnail"), str):
                        row["Thumbnail"] = re.sub(r"(?i)(thumbnail_)%d(\.xds)$" % c, r"\g<1>%d\2" % n, row["Thumbnail"])
                    # every car's ModelId is its id; the game's car view (Restrictions_Data_Car) finds
                    # a car's parts and whether it is installed by it: FM4's 1541 is FH1's Explorer
                    if row.get("ModelId") == c:
                        row["ModelId"] = n
                if table in car_col and row.get(pk) == key and pk != car_col[table]:
                    row[pk] = self._fresh_key(table)  # a key not made from the car id: a new one
                rekeyed[row.get(pk)] = row
            self.rows[table] = rekeyed
        for rt, rc, old, new in copies:
            src = self.rows.get(rt, {}).get(old)
            if src is not None:
                self.rows[rt][new] = dict(src, **{rc: new})
                self._copied.append((rt, old))

    def _part_tables(self, fam):
        """The tables of a family's parts the import collected: Data_Engine and the
        List_UpgradeEngine<Part> tables, and so on."""
        prefix = FAMILY_PREFIX.get(fam)
        return [t for t, _ in FAMILY_TABLES.get(fam, [])] + \
            [t for t in self.rows if prefix and t.startswith(prefix) and t != prefix]

    def _move_parts(self):
        """The engines, drivetrains and stock wheels the id table gives ids of their own
        (table_parts, #924), for every car of the import that uses them: FM4's rows of the part
        under the new id (rows keyed id x 1000 + n too, and the torque curves in that range), the
        references to it, an engine's or wheel's MediaName as the table gives it (an engine whose
        name FH1 has gets "_FM4": the game finds its EngineTuning by that name)."""
        for fam in ("engine", "drivetrain"):
            for key, (new, name) in sorted(self.parts.get(fam, {}).items()):
                self._move_part(fam, key, new, name)
        wheels = self.rows.get("List_Wheels", {})
        for key, (new, name) in sorted(self.parts.get("wheel", {}).items()):
            row = wheels.pop(key, None)
            if row is None:
                continue
            wheels[new] = dict(row, ID=new, **({"MediaName": name} if name else {}))
            for car in self.rows.get("Data_Car", {}).values():
                if car.get("StockWheelID") == key:
                    car["StockWheelID"] = new

    def _move_part(self, fam, key, new, name):
        def move(v):
            return new * 1000 + (v - key * 1000)

        for t in self._part_tables(fam):
            rows = self.rows.get(t)
            if not rows:
                continue
            cols = columns(self.fh1, t)
            pk = cols[0]
            # the column naming the part, as this table spells it (List_UpgradeDrivetrainDriveline
            # has DrivetrainId; its rows stayed behind and the moved drivetrain had none)
            col = next((c for c in cols if c.lower() == PART_COLUMN[fam].lower()), PART_COLUMN[fam])
            out = {}
            for k, row in rows.items():
                if row.get(col) == key:
                    row[col] = new
                    if pk != col and made_from(row.get(pk), key):
                        row[pk] = move(row[pk])
                    if name and row.get("MediaName"):
                        self.part_names[row["MediaName"]] = name
                        row["MediaName"] = name
                    for pat, rt, rc in REFS:
                        for x, v in list(row.items()):
                            if pat.match(x) and made_from(v, key):
                                row[x] = move(v)
                                src = self.rows.get(rt, {}).get(v)
                                if src is not None:
                                    self.rows[rt][move(v)] = dict(src, **{rc: move(v)})
                                    self._copied.append((rt, v))
                out[row.get(pk)] = row
            self.rows[t] = out
        for t, c in FAMILIES[fam]:  # the cars' rows naming the part
            for row in self.rows.get(t, {}).values():
                if row.get(c) == key:
                    row[c] = new

    def finish(self, report):
        """Drop rows FH1 already has (same key); refuse rows whose key FH1 uses for something
        else. Returns {table: [rows]} in FH1's columns, with the retune applied to Data_Car."""
        out = {}
        for table, rows in self.rows.items():
            fcols = columns(self.fh1, table)
            pk = fcols[0]
            existing = {}
            keys = list(rows)
            for i in range(0, len(keys), 500):
                chunk = keys[i:i + 500]
                q = "select * from [%s] where [%s] in (%s)" % (table, pk, ",".join("?" * len(chunk)))
                for r in self.fh1.execute(q, chunk):
                    existing[r[0]] = dict(zip(fcols, r))
            kept = []
            for key, r in rows.items():
                if key in existing:
                    same = all(existing[key].get(c) == r.get(c) for c in fcols if c in r)
                    if not same and table not in ("List_TorqueCurve", "List_AeroPhysics", "List_Wheels"):
                        report.setdefault("conflicts", []).append((table, key))
                    continue
                row = {c: r.get(c, self.defaults.get(c) if table == "Data_Car" else None) for c in fcols}
                if table == "Data_Car":
                    self.retune.apply(row)
                    row["IsPurchased"] = 1
                    row["IsInstalled"] = 1
                    row["ContentId"] = 0
                kept.append(row)
            if kept:
                out[table] = kept
        return out


def write_full_db(source_path, fh1db, rows, path):
    """FH1's gamedb.slt with the new rows added. The game counts a car as installed only when
    it is in the database it loads (a car added by a DB patch, outside any DLC offer, is not
    in the game's list of installed content: IsCarInstalled answers 0 and the Autoshow does
    not offer it), so the cars go into a full copy of the player's database."""
    if os.path.exists(path):
        os.unlink(path)
    with open(source_path, "rb") as f, open(path, "wb") as g:
        g.write(f.read())
    db = sqlite3.connect(path)
    for table, data in sorted(rows.items()):
        cols = columns(fh1db, table)
        db.executemany("INSERT INTO [%s] VALUES (%s)" % (table, ",".join("?" * len(cols))),
                       [[r[c] for c in cols] for r in data])
    db.commit()
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    db.close()
    if str(mode).lower() == "wal":
        raise ConvertError("the game's database must not be in WAL mode")


# ============================================================================= car files

def model_points(data):
    """Body vertex positions of an FH1 .carbin (for the collision hull)."""
    import carbin_dump
    m = carbin_dump.parse(data)
    pts = []
    for s in m["sections"]:
        if HULL_SKIP.search(s["name"]):
            continue
        vb = s["vertex_buffer"]
        if not vb["count"] or not vb["has_data"]:
            continue
        c, h = s["center"], s["bounds_max"]
        step = max(1, vb["count"] // 400)
        for i in range(0, vb["count"], step):
            x, y, z = struct.unpack_from(">3h", data, vb["data_at"] + vb["stride"] * i)
            pts.append((c[0] + x / 32767.0 * h[0], c[1] + y / 32767.0 * h[1], c[2] + z / 32767.0 * h[2]))
    if len(pts) < 8:
        raise ConvertError("no body vertices for the collision hull")
    return pts


# The car's light list (CCarModelData, docs/modding/fm4-cars.md#the-light-list): FH1's sprites
# for the lamps, which FM4 does not have. Each lamp gets FH1's most common records of its kind.
LIGHT_MATERIALS = {
    "head": {"head_light", "head_light2", "hidhead", "xenonhead", "oldhead", "fullbeam"},
    "tail": {"tail_light", "tail_light2", "tail_light_on", "tail_light_lod0", "taillight2s",
             "taillightst"},
    "reverse": {"reverse_light", "reverse_light_lod0"},
}
# (byte 18, kind, function, rgb, size, glow, bytes 30-31) per record; function bits: 1 lights
# on, 2 brake, 4 a second brake sprite, 8 indicator, 16 reverse. glow is probably which of
# media/carlights/headlight_glow_<n>.xds; byte 18 and bytes 30-31 are not decoded.
HEAD_LAMP = {  # by the headlight kind FH1 gives cars of the car's age
    3: (0, 3, 1, (155, 140, 95), 19, 2, (30, 50)),    # before 2000
    1: (1, 1, 1, (160, 160, 140), 15, 4, (30, 50)),   # 2000-2009
    2: (0, 2, 1, (220, 220, 255), 19, 1, (30, 50)),   # 2010 on (xenon)
}
HEAD_FLARE = {3: (230, 230, 125), 1: (230, 230, 125), 2: (150, 150, 255)}
TAIL_LAMP = [
    (2, 4, 1, (100, 0, 0), 57, 7, (30, 50)),          # tail light (lights on)
    (0, 0, 2, (255, 0, 0), 32, 5, (30, 50)),          # brake
    (0, 0, 4, (255, 0, 0), 25, 5, (30, 50)),          # brake, second sprite
]
REVERSE_LAMP = (0, 0, 16, (128, 128, 128), 15, 6, (255, 255))
LIGHT_INSET = 0.03    # FH1's sprites sit 2-7 cm inside the lens's outer face
FLARE_AHEAD = 0.02    # the headlight's flare 2 cm ahead of it
LIGHT_MIDDLE = 0.2    # a lamp whose centre is this close to the middle (a third brake light)
LAMP_GAP = 0.04       # vertices this far apart belong to separate lamps
LAMPS_PER_SIDE = 2    # FH1's cars have one or two of each lamp a side
LAMP_RANK = {"own": 2, "lens": 1, "other": 0}  # where a lamp is found, the best used
# Lamps in these sections are not the stock car's: race and optional parts (bumperFb, hoodc,
# wingrace...), pop-up headlights' lowered position and the interior.
LIGHT_SKIP_SECTION = re.compile(r"^(bumper[FR]|hood|wing|trunk|(head|tail)light[LR])([b-z]|race)$|(?i:down$|^interior)")


def light_record(pos, front, side, rec):
    b18, kind, func, rgb, size, glow, tail = rec
    x, y, z = pos
    # The list's z axis points forward (the model's -z); the direction is s16 normalized.
    return struct.pack(">3f3h14B", x, y, -z, 0, 0, 32767 if front else -32767,
                       b18, kind, side, func, rgb[0], rgb[1], rgb[2], size, glow, 0, 0, 0, tail[0], tail[1])


def _lamp_clusters(pts):
    """The separate lamps among a side's lamp vertices: groups of vertices LAMP_GAP apart at most
    (a grid's connected cells), the LAMPS_PER_SIDE biggest by face area; small bits (less than
    a fifth of the biggest) are left out."""
    cells = {}
    for p in pts:
        cells.setdefault(tuple(int(math.floor(v / LAMP_GAP)) for v in p), []).append(p)
    seen, groups = set(), []
    for start in cells:
        if start in seen:
            continue
        seen.add(start)
        todo, group = [start], []
        while todo:
            cell = todo.pop()
            group += cells[cell]
            for d in itertools.product((-1, 0, 1), repeat=3):
                n = (cell[0] + d[0], cell[1] + d[1], cell[2] + d[2])
                if n in cells and n not in seen:
                    seen.add(n)
                    todo.append(n)
        lo = [min(q[k] for q in group) for k in range(3)]
        hi = [max(q[k] for q in group) for k in range(3)]
        groups.append(((hi[0] - lo[0] + 0.01) * (hi[1] - lo[1] + 0.01), lo, hi))
    groups.sort(key=lambda g: -g[0])
    return [(lo, hi) for area, lo, hi in groups[:LAMPS_PER_SIDE] if area >= groups[0][0] / 5]


def car_lights(data, year):
    """The light list (u32 count + 32-byte records) for a converted FH1 .carbin: for each lamp
    on each side, a head lamp's headlight and flare, a tail lamp's tail light, brake and second
    brake sprite, a reverse lamp's sprite, at the lamp's centre. A lamp is the meshes of its
    shader technique in its own sections (headlightL, taillightR...), else its lens (the glassLHL,
    glassRTL... sections), else the technique's meshes in the stock parts (body, bumperFa,
    trunk...; not the race or other optional parts). A lamp in the middle (a third brake light)
    is left out, as in most of FH1's cars, and so are indicators (FH1 gives those only to its
    traffic cars)."""
    import carbin_dump
    m = carbin_dump.parse(data)
    pts = {}  # (lamp, side) -> {rank: vertices}; the best rank is used
    lod = m["base"].get("start_lod", 1)
    for s in m["sections"]:
        vb = s["vertex_buffer"]
        name = s["name"]
        name = name.decode("latin-1") if isinstance(name, bytes) else name
        if not vb["count"] or not vb["has_data"] or LIGHT_SKIP_SECTION.search(name):
            continue
        c, h = s["center"], s["bounds_max"]
        glass = re.match(r"(?i)glass[LR]([HT])L$", name)
        for me in s["meshes"]:
            mat = me["material"]
            mat = (mat.decode("latin-1") if isinstance(mat, bytes) else mat).lower()
            if glass:  # a lamp's lens
                lamp, rank = "head" if glass.group(1).upper() == "H" else "tail", LAMP_RANK["lens"]
            else:
                lamp = next((k for k, v in LIGHT_MATERIALS.items() if mat in v), None)
                rank = LAMP_RANK["own" if re.match(r"(?i)(head|tail)light[LR]", name) else "other"]
            ib = me["index_buffer"]
            if not lamp or me["lod"] != lod or not ib["has_data"] or not ib["count"]:
                continue
            fmt = ">%d%s" % (ib["count"], "I" if ib["index_size"] == 4 else "H")
            for i in set(struct.unpack_from(fmt, data, ib["data_at"])):
                if i >= vb["count"]:
                    continue
                x, y, z = struct.unpack_from(">3h", data, vb["data_at"] + vb["stride"] * i)
                p = (c[0] + x / 32767.0 * h[0], c[1] + y / 32767.0 * h[1], c[2] + z / 32767.0 * h[2])
                if (lamp == "head") == (p[2] < 0) and abs(p[0]) > 0.01:  # the model's front is -z
                    pts.setdefault((lamp, 1 if p[0] < 0 else 2), {}).setdefault(rank, []).append(p)
    kind = 3 if year < 2000 else 1 if year < 2010 else 2
    recs = []
    for (lamp, side), ranks in sorted(pts.items()):
        for lo, hi in _lamp_clusters(ranks[max(ranks)]):
            x, y = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
            if abs(x) < LIGHT_MIDDLE:
                continue
            if lamp == "head":
                z = lo[2] + LIGHT_INSET
                recs.append(light_record((x, y, z), True, side, HEAD_LAMP[kind]))
                flare = (HEAD_LAMP[kind][0], 4, 1, HEAD_FLARE[kind], 179, 7, (30, 70))
                recs.append(light_record((x, y, z - FLARE_AHEAD), True, side, flare))
            elif lamp == "tail":
                for rec in TAIL_LAMP:
                    recs.append(light_record((x, y, hi[2] - LIGHT_INSET), False, side, rec))
            else:
                recs.append(light_record((x, y, hi[2] - LIGHT_INSET / 3), False, side, REVERSE_LAMP))
    return struct.pack(">I", len(recs)) + b"".join(recs)


def carattribs_v21(fm4_xml, donor_xml):
    """FM4's CarAttribs.xml (version 16) as FH1's version 21: FH1's damage and shadow
    settings (from one of the player's FH1 cars, the same for FH1's cars) over FM4's file."""
    text = fm4_xml.decode("utf-8", "replace")
    donor = donor_xml.decode("utf-8", "replace") if donor_xml else ""
    text = re.sub(r'<CarAttributes Version="\d+"', '<CarAttributes Version="21"', text)

    def donor_elem(tag):
        m = re.search(r"<%s\b[^>]*/>" % tag, donor)
        return m.group(0) if m else None

    def donor_block(tag):
        m = re.search(r"<%s\b[^>]*>.*?</%s>" % (tag, tag), donor, re.S)
        return m.group(0) if m else None
    for tag in ("PartDropGlobalScalar", "DamageScrape", "General"):
        d = donor_elem(tag)
        if d:
            if tag == "General":
                oc = re.search(r'OpenCockpit="(\d)"', text)
                if oc:
                    d = re.sub(r'OpenCockpit="\d"', 'OpenCockpit="%s"' % oc.group(1), d)
            text = re.sub(r"<%s\b[^>]*/>" % tag, lambda _: d, text, count=1)
    ds = re.findall(r"<DamageScalars\b[^>]*/>", donor)
    if ds:
        olds = re.findall(r"<DamageScalars\b[^>]*/>", text)
        if len(olds) == len(ds):
            for o, n in zip(olds, ds):
                text = text.replace(o, n, 1)
    extra = []
    for tag in ("DropShadowFadeOut", "CockpitShadowDepthBias", "CockpitShadowSlopeScaleDepthBias"):
        if not re.search(r"<%s\b" % tag, text):
            d = donor_elem(tag) or donor_block(tag)
            if d:
                extra.append("  " + d)
    if extra:
        text = text.replace("</CarAttributes>", "\n".join(extra) + "\n</CarAttributes>")
    return text.encode("utf-8")


class Converter:
    """out: the mod folder (the database and the names). package: when given, the folder of a
    DLC-style package (.../4D5309C9/00000002/<name>) that gets the cars' files instead of the
    mod: the mod overlay holds at most 4 GB in all, and a full import is about 9 GB
    uncompressed (3 GB with FM4's textures kept compressed, as a pack's zips can)."""

    mod_version = None  # mod.toml's version; the launcher sets its own (#853), else source_version()

    def __init__(self, fm4, fh1, out, package=None):
        self.fm4, self.fh1, self.out = fm4, fh1, out
        self.fm4db, self.fh1db = fm4.db(), fh1.db()
        self.donors = None
        self.donor_attribs = None
        self.wheels_done = set()
        self.package = package
        self.pack = PackWriter(os.path.join(package, "Media", "DLCZips"), PACK_STEM, PACK_PRIORITY) if package else None

    def close(self):
        """Close the databases and a pack zip a failed run left open (#551): on Windows an open
        file can't be deleted, nor the folder holding it."""
        for db in (self.fm4db, self.fh1db):
            db.close()
        if self.pack and self.pack.f is not None:
            self.pack.f.close()
            self.pack.f = None

    def write(self, rel, data):
        path = os.path.join(self.out, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def put(self, rel, data=None, member=None):
        """A car file: into the pack when there is one (a member copied as it is), else into the
        mod (read and stored)."""
        if self.pack:
            self.pack.add(rel, data, member.raw() if data is None else None)
        else:
            self.write(rel, data if data is not None else member.read())

    def finish_package(self, report):
        if not self.pack:
            return
        self.pack.close()
        media = os.path.join(self.package, "Media")
        with open(os.path.join(media, "%d.puboffer" % PACK_OFFER), "wb") as f:
            f.write(b" \r\n")
        name = os.path.basename(os.path.normpath(self.package))
        headers = os.path.join(os.path.dirname(os.path.dirname(os.path.normpath(self.package))), "Headers", "00000002")
        os.makedirs(headers, exist_ok=True)
        import install_dlc
        with open(os.path.join(headers, name + ".header"), "wb") as f:
            f.write(install_dlc.header_bytes(name, "Forza Motorsport 4 cars", install_dlc.TITLE_ID))
        report["zips"] = list(self.pack.zips)

    def load_donors(self):
        if self.donors is not None:
            return
        self.donors = []
        for path in self.fh1.car_zips():
            z = ZIPS.get(path)
            for i in z.infolist():
                n = i.filename.lower()
                if n == "physicsdefinition.bin":
                    try:
                        self.donors.append(fm4_physdef.parse(Member(z, path, i).read()))
                    except (fm4_physdef.PhysdefError, struct.error):
                        pass
                elif n == "carattribs.xml" and self.donor_attribs is None and "alf_8c_08" in path.lower():
                    self.donor_attribs = Member(z, path, i).read()
        if self.donor_attribs is None:
            for path in self.fh1.car_zips():
                data = zip_member(path, "CarAttribs.xml")
                if data and b'Version="21"' in data:
                    self.donor_attribs = data
                    break
        if not self.donors:
            raise ConvertError("no physicsdefinition.bin in the FH1 car archives")

    def table(self):
        """The one id table for every imported car (car_ids.json, #924)."""
        if getattr(self, "_table", None) is None:
            self._table = car_ids.load()
        return self._table

    def renumbered(self):
        """table_cars for these files: {FM4 car id: (its id, its MediaName)} where the id table
        numbers or names a car otherwise than FM4."""
        if getattr(self, "_renumbered", None) is None:
            self._renumbered = table_cars(self.table(), self.fm4db)
        return self._renumbered

    def twins(self):
        """The FM4 copies of FH1's traffic cars (#841): {FM4 car id: (its id, its MediaName)}."""
        return {c: t for c, t in self.renumbered().items()
                if self.table().car(t[1]) and self.table().car(t[1]).get("twin_of")}

    def candidates(self):
        """[(id, MediaName, reason)] for every FM4 car the id table lists (#924: every FM4 car FH1
        and its official DLC lack, and copies of FH1's traffic cars), under FM4's id and name;
        reason (None for a car that can be converted) says why one is left out. FM4's versions of
        FH1's and its DLC's other cars are never imported."""
        dlc_names, dlc_ids = self.fh1.dlc_car_names()
        files = self.fm4.car_files()
        listed = self.table().source_cars("FM4")
        out = []
        for cid, name, make in self.fm4db.execute("select Id, MediaName, MakeID from Data_Car order by Id").fetchall():
            low = str(name).lower()
            entry = listed.get(cid)
            if entry is None:
                continue  # FH1's or its DLC's own car
            reason = None
            if entry["name"].lower() in dlc_names or entry["id"] in dlc_ids:
                reason = "an installed DLC pack has it"  # UDLC's FH2 cars, for one
            elif name in FH1_DLC_CARS or low in {n.lower() for n in FH1_DLC_CARS}:
                reason = "an FH1 DLC car"
            elif low not in files:
                reason = "no model in the FM4 files given"
            elif entry.get("twin_of") and electric_only(self.fm4db, cid):
                # FH1 finds a car's engine sound by its stock engine, and a car without one
                # loaded to a grey world in Forza Imported DLC Maker; FH1's own Leaf (the traffic
                # car) is dressed as a petrol car for that
                reason = "an electric car without an engine row, which FH1 needs (not done yet)"
            else:
                reason = self.needs(cid)
            out.append((cid, name, reason))
        return out

    def _have(self, table, col, value):
        """True when FM4's database (with the DLC patches) or FH1's has a row of table with col = value."""
        for db in (self.fm4db, self.fh1db):
            try:
                if db.execute("select 1 from [%s] where [%s]=? limit 1" % (table, col), (value,)).fetchone():
                    return True
            except sqlite3.Error:  # a table or column that database lacks
                pass
        return False

    def released(self, cid):
        """True when ReImagined's released FM4 import (before the id table, #924) could have had
        this car: it took every FM4 car under FM4's id whose id and MediaName FH1's database and
        the installed DLC packs' cars lack (so no copy of a traffic car, #841, nor the 2013 Viper
        pack's two cars whose ids an FH1 DLC car has) and whose stock engine's id is not another
        engine's in FH1 (the id table moves some of those engines now). Such a car keeps FM4's
        level-4 tire (Database._drop_drag_tires): a save can have it fitted."""
        if cid in self.twins():
            return False
        name = self.fm4db.execute("select MediaName from Data_Car where Id=?", (cid,)).fetchone()
        low = str(name[0] if name else "").lower()
        if self.fh1db.execute("select 1 from Data_Car where Id=? or lower(MediaName)=?", (cid, low)).fetchone():
            return False
        if "_dlc_cars" not in self.__dict__:
            self.__dict__["_dlc_cars"] = self.fh1.dlc_car_names()
        dlc_names, dlc_ids = self.__dict__["_dlc_cars"]
        if cid in dlc_ids or low in dlc_names:
            return False
        try:
            stock = self.fm4db.execute("select EngineID from List_UpgradeEngine where Ordinal=? and IsStock",
                                       (cid,)).fetchall()
        except sqlite3.Error:
            stock = []
        return not any(engine_clash(self.fm4db, self.fh1db, v) for (v,) in stock)

    def needs(self, cid):
        """Why a car can't be converted whole, in words, or None: its rows name something neither
        game's files have (its engine, body or drivetrain rows, its make or its stock wheel: a DLC
        car that relies on another pack's rows or files), or its stock engine's id is another
        engine's in FH1. Such a car is left out rather than added broken."""
        miss = "needs %s, which is not in the FM4 files given (is it in another DLC pack?)"
        db = self.fm4db
        if not hasattr(self, "_fm4_tables"):
            self._fm4_tables = set(tables(db))
        for upgrade, col, table, key, what in (
                ("List_UpgradeEngine", "EngineID", "Data_Engine", "EngineID", "its engine"),
                ("List_UpgradeCarBody", "CarBodyID", "Data_CarBody", "Id", "its body"),
                ("List_UpgradeDrivetrain", "DrivetrainID", "Data_Drivetrain", "DrivetrainID", "its drivetrain")):
            if upgrade not in self._fm4_tables or col not in columns(db, upgrade):
                continue
            q = "select distinct [%s] from [%s] where Ordinal=?" % (col, upgrade)
            for (v,) in db.execute(q, (cid,)).fetchall():
                if v is not None and not self._have(table, key, v):
                    return miss % ("%s (%s %s)" % (what, key, v))
        if "List_UpgradeEngine" in self._fm4_tables and "IsStock" in columns(db, "List_UpgradeEngine"):
            moved = self.table().parts.get(("FM4", "engine"), {})
            for (v,) in db.execute("select EngineID from List_UpgradeEngine where Ordinal=? and IsStock",
                                   (cid,)).fetchall():
                if v not in moved and engine_clash(db, self.fh1db, v):
                    return "its engine's id (EngineID %s) is another engine's in FH1" % v
        cols = columns(db, "Data_Car")
        row = dict(zip(cols, db.execute("select * from Data_Car where Id=?", (cid,)).fetchone()))
        if row.get("MakeID") is not None and "List_CarMake" in self._fm4_tables \
                and not self._have("List_CarMake", "ID", row["MakeID"]):
            return miss % ("its make (%s)" % row["MakeID"])
        wid = row.get("StockWheelID")
        if wid is not None and "List_Wheels" in self._fm4_tables:
            wheel = db.execute("select MediaName from List_Wheels where ID=?", (wid,)).fetchone()
            if not wheel:
                if not self._have("List_Wheels", "ID", wid):
                    return miss % ("its wheel (%s)" % wid)
            elif not (self.fh1.has_wheel(wheel[0]) or self.fm4.has_wheel(wheel[0])):
                return miss % ("the files of its wheel %s" % wheel[0])
        return None

    def convert_wheel(self, name, out_name=None):
        """FM4's wheel name as FH1's, unless FH1 has it; written as out_name (a traffic twin's own
        copy, twin_file: FH1's copy is the traffic car's, kept for it), else under its own."""
        out_name = out_name or name
        if out_name in self.wheels_done or (out_name == name and self.fh1.has_wheel(name)):
            return True
        files = self.fm4.wheel_files(name)
        if not files:
            return False
        for rel, m in files:
            low = rel.lower()
            if low in SKIP_FILES:
                continue
            out_rel = twin_file(rel, name, out_name)
            path = "media/wheels/%s/%s" % (out_name, out_rel)
            if low.endswith(".carbin"):
                self.put(path, fm4_carbin.convert(m.read(), "wheel/%s/%s" % (out_name, out_rel.lower())))
            else:
                self.put(path, member=m)
        self.wheels_done.add(out_name)
        return True

    def _audio_zip_names(self, game_media, rel):
        z = find_ci(game_media, *rel.split("/"))
        if not z:
            return {}
        return {i.filename.lower(): (z, i) for i in ZIPS.get(z).infolist()}

    def _audio_sources(self):
        if not hasattr(self, "_fh_audio"):
            self._fh_audio = {k: self._audio_zip_names(self.fh1.media, v) for k, v in AUDIO_ZIPS.items()}
            self._fm_audio = {k: self._audio_zip_names(self.fm4.media, v) for k, v in AUDIO_ZIPS.items()}
            for k, members in self.fm4.pack_audio().items():  # a DLC car's, over disc 1's
                self._fm_audio[k].update(members)
            if self.fh1.update:
                for i in ZIPS.get(self.fh1.update).infolist():
                    for k, v in AUDIO_ZIPS.items():
                        pre = v[:-4].lower() + "/"
                        if i.filename.lower().startswith(pre):
                            self._fh_audio[k][i.filename.lower()[len(pre):]] = (self.fh1.update, i)
            # FH1's own banks: the loose files of its disc (an FH1 DLC's may not stay installed)
            self._fh_banks = set(loose_banks(find_ci(self.fh1.media, *BANKS_DIR[1:])))
            self._fm_banks = self.fm4.banks()
        if not hasattr(self, "_fh1_engines"):
            # Engines an FH1 car uses (its own, or one it can swap to): their tuning is FH1's
            # and stays as it is; so do those of FH1's DLC cars, which may not be in this database.
            self._fh1_engines = {str(r[0]).lower() for r in self.fh1db.execute(
                "select distinct e.MediaName from List_UpgradeEngine u join Data_Engine e "
                "on e.EngineID=u.EngineID")} | {n.lower() for n in FH1_DLC_CARS}
            self._louder_done = set()

    def audio_plan(self, car_name, engine_names, source=None):
        """What the car's engine sound needs that FH1 lacks, as a list of (what, path, data or
        source file, name for the report), checked before anything is written: FH1 has every
        FM4 disc car's sound banks and harmonic tuning, and the car and engine tuning
        (CarModelTuning, EngineTuning) of most FM4 cars; what it lacks is taken from FM4's copy
        (its audio/cars archives, and a DLC pack's own tuning files and sound banks for its cars).
        The EngineTuning of an engine no FH1 car uses goes into the mod at FH1's level
        (louder_engine, #712): FH1's copy, else FM4's, and FM4's where FH1's is a traffic car's
        placeholder and FM4's is not (traffic_tuning, #840). A tuning or sound bank that no file
        given has raises ConvertError: the game stops with its disc read error where it misses
        one (#776). source: the car's MediaName in FM4's files when it is written as another
        (a traffic twin's CarModelTuning, read as FM4's and written under car_name, #841).
        engine_names: MediaNames, or (the name in the import, FM4's name) for a twin's own copy of
        an engine (Database.part_names: its EngineTuning is FM4's, under the new name)."""
        self._audio_sources()
        wanted = [("cmt", car_name + "_CMT.xml", (source or car_name) + "_CMT.xml")] + \
            [("et", out + "_ET.xml", fm + "_ET.xml")
             for out, fm in (e if isinstance(e, tuple) else (e, e) for e in engine_names)]
        plan, hts = [], []
        for kind, fname, fm_name in wanted:
            rel = "media/%s/%s" % (AUDIO_ZIPS[kind][:-4].lower(), fname)
            engine = fname[:-len("_ET.xml")].lower() if kind == "et" else None
            fh_src = self._fh_audio[kind].get(fname.lower())
            if kind == "et" and engine not in self._fh1_engines:
                # An engine only FM4's cars have: its tuning (FH1's copy, else FM4's) with
                # FH1's level (ENGINE_GAIN), in the mod, which outranks every archive.
                if engine in self._louder_done:
                    continue
                fm_src = self._fm_audio[kind].get(fm_name.lower())
                src = fh_src or fm_src
                if not src:
                    continue
                data = Member(ZIPS.get(src[0]), src[0], src[1]).read()
                if fh_src and fm_src and traffic_tuning(data):
                    # FH1's copy is a cut traffic car's (#840): FM4's own, unless it is one too
                    # (the electric Volt's), with FM4's harmonic tuning, all of it FH1's too
                    fm_data = Member(ZIPS.get(fm_src[0]), fm_src[0], fm_src[1]).read()
                    if not traffic_tuning(fm_data):
                        data, fh_src = fm_data, None
                plan.append(("louder", rel, louder_engine(data), None if fh_src else fname))
                if fh_src:
                    continue  # FH1's tuning names FH1's harmonic tuning and banks
            elif fh_src:
                continue
            else:
                src = self._fm_audio[kind].get(fm_name.lower())
                if not src:
                    continue
                data = Member(ZIPS.get(src[0]), src[0], src[1]).read()
                plan.append(("put", rel, data, fname))
            if kind == "et":
                hts += [h for h in sorted(set(re.findall(r'"([^"]+_HT\.xml)"', data.decode("latin-1"))))
                        if h not in hts]
        for ht in hts:
            if ht.lower() in self._fh_audio["ht"]:
                continue  # FH1's own, naming FH1's own banks
            hsrc = self._fm_audio["ht"].get(ht.lower())
            if not hsrc:
                raise ConvertError("its engine's sound tuning %s is in none of the files given" % ht)
            hdata = Member(ZIPS.get(hsrc[0]), hsrc[0], hsrc[1]).read()
            plan.append(("put", "media/audio/cars/engines/harmonictuning/" + ht, hdata, ht))
            for bank in bank_names(hdata):
                low = bank.lower()
                if low in self._fh_banks or low in PLACEHOLDER_BANKS:
                    continue
                bsrc = self._fm_banks.get(low)
                if not bsrc:
                    raise ConvertError("its engine sound bank %s is in none of the files given" % bank)
                if not self.pack:
                    raise ConvertError("its engine sound bank %s is not one of FH1's: the game reads it "
                                       "only from a package (--package)" % bank)
                plan.append(("bank", "/".join(BANKS_DIR) + "/" + bsrc[1], bsrc[0], os.path.basename(bsrc[0])))
        return plan

    def audio_apply(self, car_name, plan, report):
        """audio_plan's files written: the tunings into the mod or the package, the banks loose
        next to the package's current zip, the one holding this car's files."""
        added = []
        for what, rel, data, name in plan:
            if what == "louder":
                engine = rel.rsplit("/", 1)[1][:-len("_ET.xml")].lower()
                if engine in self._louder_done:
                    continue
                self.write(rel, data)
                self._louder_done.add(engine)
                report["louder"] = report.get("louder", 0) + 1
            elif what == "put":
                self.put(rel, data)
            elif not self.pack.add_loose(rel, data):
                continue  # a bank another car brought already
            else:
                report["banks"] = report.get("banks", 0) + 1
            if name:
                added.append(name)
        if added:
            report.setdefault("audio", []).append((car_name, added))

    def audio(self, car_name, engine_names, report):
        """The car's engine sound set-up (audio_plan), written."""
        self.audio_apply(car_name, self.audio_plan(car_name, engine_names), report)

    def convert_car(self, cid, name, out_id=None, out_name=None):
        """FM4's car cid (its files under name) as FH1's, written as out_id and out_name (the id
        table's when it renumbers or renames the car: table_cars, twin_file), else under its own."""
        out_id = cid if out_id is None else out_id
        out_name = out_name or name
        self.load_donors()
        disp, files = self.fm4.car_files()[name.lower()]
        names = {rel.lower(): (rel, m) for rel, m in files}
        main = names.get(name.lower() + ".carbin")
        if not main:
            raise ConvertError("%s: no %s.carbin" % (name, name))
        maxdata = names.get("physics/maxdata.xml")
        if not maxdata:
            raise ConvertError("%s: no physics/maxdata.xml" % name)
        base = "media/cars/%s/" % out_name
        main_fh1 = None
        for low, (rel, m) in sorted(names.items()):
            if low in SKIP_FILES:
                continue
            out_rel = twin_file(rel, name, out_name)
            if low.endswith(".carbin"):
                data = fm4_carbin.convert(m.read(), "car/%s/%s" % (out_name, out_rel.lower()))
                if low == name.lower() + ".carbin":
                    year = self.fm4db.execute("select Year from Data_Car where Id=?", (cid,)).fetchone()
                    data = fm4_carbin.set_lights(data, car_lights(data, year[0] if year else 2000))
                    main_fh1 = data
                self.put(base + "stripped_" + out_rel.lower(), fm4_carbin.strip(data))
                self.put(base + out_rel, data)
            elif low == "carattribs.xml":
                self.put(base + out_rel, carattribs_v21(m.read(), self.donor_attribs))
            else:
                self.put(base + out_rel, member=m)
        pts = model_points(main_fh1)
        phys, donor = fm4_physdef.build(maxdata[1].read(), pts, self.donors)
        self.put(base + "physicsdefinition.bin", phys)
        thumb = self.fm4.thumbnail(cid)
        if thumb:
            self.put("media/ui/textures/thumbnails/thumbnail_%d.xds" % out_id, thumb)
        return donor

    def strings(self, rows, report, fh1_refs=(), own_refs=()):
        """<table>.str for each of FH1's languages: FH1's strings (with the DLC's) plus the new
        rows'. Rewrites the rows' references when a key had to move (another string of FH1's
        table has it). fh1_refs: references to FH1's own strings, left as they are. own_refs:
        references whose FM4 string is added even when FH1 has one of the same key and IDS_ name
        (a moved make's name, Database.own_refs), under that name with _FM4 appended."""
        fm_langs = self.fm4.languages()
        langs = sorted(l for l in self.fh1.languages() if l not in ("dev", "loc"))
        own = {int(r[2:]) for r in own_refs if isinstance(r, str) and re.match(r"_&\d+$", r)}
        moved_total = 0
        for table_name, users in STRING_TABLES.items():
            refs = []
            for t, cols in users:
                for r in rows.get(t, []):
                    for c in cols:
                        ref = r.get(c)
                        if isinstance(ref, str) and re.match(r"_&\d+$", ref) and ref not in fh1_refs:
                            refs.append(int(ref[2:]))
            if not refs:
                continue
            en = self.fm4.strings(table_name, "en")
            moved = {}
            for lang in langs:
                _, table = self.fh1.strings_raw(table_name, lang)
                if not table.entries:
                    continue
                src = self.fm4.strings(table_name, lang) if lang in fm_langs else en
                added = False
                for n in dict.fromkeys(refs):
                    key = n & 0xFFFF
                    e = src.entries.get(key) or en.entries.get(key)
                    if not e:
                        continue
                    have = table.entries.get(moved.get(n, key))
                    if have is not None and have[1] == e[1] and n not in own:
                        continue  # FH1 has it
                    name = (e[1] or "IDS_FM4_%d" % key) + ("_FM4" if n in own else "")
                    new = table.add(name, e[0], e[3], moved.get(n, key))
                    added = True
                    if new != key:
                        moved[n] = new
                if added:
                    self.write("media/stringtables/%s/%s.str" % (lang, table_name.lower()), table.encode())
            for t, cols in users:
                for r in rows.get(t, []):
                    for c in cols:
                        ref = r.get(c)
                        if isinstance(ref, str) and re.match(r"_&\d+$", ref) and ref not in fh1_refs \
                                and int(ref[2:]) in moved:
                            n = int(ref[2:])
                            r[c] = "_&%d" % ((n & ~0xFFFF) | moved[n])
            moved_total += len(moved)
        report["moved_string_keys"] = moved_total

    def logos(self, makes, report):
        """The logos of the makes FH1 lacks, from FM4 (UI4/Logos/<size>/L_MAN_<make>_Logo.xds,
        the same format in both games): the Autoshow's manufacturer tile was black for Renault,
        whose logos FH1 does not ship (#710). Those FH1 has (Acura, Saab, ...) are kept."""
        stems = set()
        for r in makes:
            for c in ("IconPath", "IconPathLight", "IconPathBase"):
                m = re.search(r"(?i)(l_man_[^\\/]+)\.tga$", str(r.get(c) or ""))
                if m:
                    stems.add(m.group(1).lower())
        if not stems:
            return
        have = self.fh1.logos()
        added = []
        for low, member in sorted(self.fm4.logos().items()):
            if low.rsplit("/", 1)[1][:-4] in stems and low not in have:
                rel = member.info.filename[-len(low):]  # UI4/Logos/<size>/L_MAN_<make>_Logo.xds
                self.put("media/ui/textures/" + rel, member=member)
                added.append(rel)
        report["logos"] = added

    def run(self, car_ids, progress=None, cancelled=lambda: False):
        """Convert the cars into self.out. progress(done, total, text) is called before each
        car; cancelled() is polled between cars (raises Cancelled)."""
        report = {"cars": [], "skipped": []}
        cand = {cid: (name, reason) for cid, name, reason in self.candidates()}
        chosen = []
        for cid in car_ids:
            if cid not in cand:
                report["skipped"].append((cid, "FH1 has it or FM4 does not"))
                continue
            name, reason = cand[cid]
            if reason:
                report["skipped"].append((cid, "%s: %s" % (name, reason)))
                continue
            chosen.append((cid, name))
        fm4_names = self.fm4.strings("List_CarMake", "en")
        fh1_names = self.fh1.strings_raw("List_CarMake", "en")[1]
        merges = make_merges(self.fm4db, self.fh1db, fm4_names, fh1_names)
        ids = {cid for cid, _ in chosen}
        renumber = {c: t for c, t in self.renumbered().items() if c in ids}
        twins = {c for c in self.twins() if c in ids}
        parts = table_parts(self.table())
        released = {renumber.get(c, (c, n))[0] for c, n in chosen if self.released(c)}
        db = Database(self.fm4db, self.fh1db, merges,
                      make_moves(self.fm4db, self.fh1db, fm4_names, fh1_names, merges), renumber, parts,
                      released)
        db.collect([c for c, _ in chosen])
        # rally parts, as Forza Imported DLC Maker adds them (#924), with or without FH1's Rally
        # Expansion: a part fitted in a save must not lose its row on a later import without it
        report["rally"] = db.rally_parts([renumber.get(c, (c, n))[0] for c, n in chosen])
        rows = db.finish(report)
        wheel_names = {}  # wheel id in the rows -> (FM4's wheel name, its name in the import)
        for cid, _ in chosen:
            r = self.fm4db.execute("select w.ID, w.MediaName from Data_Car c join List_Wheels w on "
                                   "w.ID=c.StockWheelID where c.Id=?", (cid,)).fetchone()
            if r:
                new = parts["wheel"].get(r[0])
                wheel_names[new[0] if new else r[0]] = (r[1], new[1] if new and new[1] else r[1])
        done = []
        dlc = getattr(self.fm4, "dlc_car_ids", set())
        report["dlc_cars"] = []
        for k, (cid, name) in enumerate(chosen):
            if cancelled():
                raise Cancelled()
            out_id, out_name = renumber.get(cid, (cid, name))
            if progress:
                progress(k, len(chosen), out_name)
            else:
                log("converting %s (%d)" % (out_name, out_id))
            try:
                engines = [(db.part_names.get(r[0], r[0]), r[0]) for r in self.fm4db.execute(
                    "select e.MediaName from List_UpgradeEngine u join Data_Engine e on "
                    "e.EngineID=u.EngineID where u.Ordinal=?", (cid,))]
                # a car missing a sound file is left out first
                sound = self.audio_plan(out_name, engines, source=name)
                self.convert_car(cid, name, out_id, out_name)
                row = [r for r in rows["Data_Car"] if r["Id"] == out_id][0]
                self.audio_apply(out_name, sound, report)
                wn = wheel_names.get(row["StockWheelID"])
                if wn and not self.convert_wheel(*wn):
                    log("  %s: no files for its wheel %s" % (out_name, wn[0]))
                done.append(out_id)
                report["cars"].append((out_id, out_name, row["ClassID"], round(row["PerformanceIndex"] * 1000)))
                if cid in dlc:
                    report["dlc_cars"].append((out_id, out_name))
                if cid in twins:
                    report.setdefault("twins", []).append((cid, out_id, out_name))
            except (ConvertError, fm4_carbin.CarbinError, fm4_physdef.PhysdefError) as e:
                report["skipped"].append((cid, "%s: %s" % (name, e)))
        if not done:
            raise ConvertError("no car converted")
        keep = set(done)
        rows = {t: [r for r in rs if t not in dict(CAR_TABLES) or r.get(dict(CAR_TABLES)[t]) in keep]
                for t, rs in rows.items()}
        self.logos(rows.get("List_CarMake", []), report)
        self.finish_package(report)
        self.strings(rows, report, db.fh1_refs, db.own_refs)
        dbdir = os.path.join(self.out, "media", "db")
        os.makedirs(dbdir, exist_ok=True)
        write_full_db(self.fh1.db_file, self.fh1db, rows, os.path.join(dbdir, "gamedb.slt"))
        with open(os.path.join(self.out, "mod.toml"), "w", encoding="utf-8") as f:
            f.write(mod_manifest(len(done), bool(report["dlc_cars"]), self.mod_version or source_version()))
        return report


def _toml_string(text):
    return '"%s"' % str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def mod_manifest(cars, dlc, version):
    """The FM4 cars mod's mod.toml. version: what made the import (the launcher's version, and
    its commit when it runs from source, #853), shown on the Mods page; nothing reads it back."""
    return ('name = %s\nversion = %s\ndescription = "%d Forza Motorsport 4 cars, '
            'converted on this PC from your own FM4 discs%s"\n'
            % (_toml_string(MOD_NAME), _toml_string(version or "1"), cars, " and DLC" if dlc else ""))


def source_version():
    """This script's version when it runs on its own: "source <commit>" in a git checkout, else "1"."""
    try:
        out = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(__file__)), "rev-parse", "--short",
                              "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "1"
    commit = out.stdout.strip() if out.returncode == 0 else ""
    return "source " + commit if commit else "1"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["list", "convert", "check"])
    ap.add_argument("--fm4", help="FM4's unpacked disc 1 (list, convert)")
    ap.add_argument("--fm4-content")
    ap.add_argument("--fm4-dlc", help="a folder of unpacked FM4 DLC packages (each with Media/DLCZips)")
    ap.add_argument("--fh1", required=True)
    ap.add_argument("--update")
    ap.add_argument("--dlc")
    ap.add_argument("--out")
    ap.add_argument("--package", help="put the cars' files into this DLC-style package folder "
                    "(USER_DATA/0000000000000000/4D5309C9/00000002/%s) instead of the mod" % PACK_NAME)
    ap.add_argument("--cars", help="MediaNames or ids, comma-separated")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            if not a.out:
                ap.error("check needs --out (the mod folder) and --package when there is one")
            fh1 = FH1(a.fh1, a.update, a.dlc)
            problems = check_import(fh1, a.out, a.package)
            fh1.close()
            for car, what in problems:
                print("%s: %s" % (car, what))
            print("%d problem(s)" % len(problems))
            return 1 if problems else 0
        if not a.fm4:
            ap.error("--fm4 is required")
        fm4 = FM4(a.fm4, a.fm4_content, a.fm4_dlc)
        fh1 = FH1(a.fh1, a.update, a.dlc)
        if a.cmd == "list":
            c = Converter(fm4, fh1, None)
            moved = c.renumbered()  # the id table's id and name where they are not FM4's (#924)
            for cid, name, reason in c.candidates():
                print("%5d %-32s %s%s" % (cid, name, reason or "ok", " (as %d %s)" % moved[cid] if cid in moved else ""))
            return 0
        if not a.out:
            ap.error("--out is required")
        c = Converter(fm4, fh1, a.out, a.package)
        cand = c.candidates()
        if a.all:
            ids = [cid for cid, _, reason in cand if not reason]
        else:
            want = [w.strip().lower() for w in (a.cars or "").split(",") if w.strip()]
            ids = []
            for w in want:
                hit = [cid for cid, name, _ in cand if name.lower() == w or str(cid) == w]
                if not hit:
                    raise ConvertError("%s is not an FM4 car FH1 lacks" % w)
                ids += hit
        os.makedirs(a.out, exist_ok=True)
        rep = c.run(ids)
        for cid, name, cls, pi in rep["cars"]:
            print("added %5d %-28s class %d PI %d" % (cid, name, cls, pi))
        for cid, why in rep["skipped"]:
            print("skipped %5d %s" % (cid, why))
        if rep.get("dlc_cars"):
            print("from the FM4 DLC: %d" % len(rep["dlc_cars"]))
        if rep.get("rally"):
            print("rally parts for %(cars)d cars: tires for %(tires)d, suspension for %(suspension)d, gearboxes for "
                  "%(gearbox)d drivetrains" % rep["rally"])
        if rep.get("louder"):
            print("engine tunings at FH1's level: %d" % rep["louder"])
        for car, files in rep.get("audio", []):
            print("sound set-up from FM4 for %s: %s" % (car, ", ".join(files)))
        if rep.get("banks"):
            print("engine sound banks from FM4: %d" % rep["banks"])
        if rep.get("conflicts"):
            print("rows whose key FH1 uses differently (left out):", rep["conflicts"][:20])
        c.close()
        fh1.close()
        fm4.close()
        return 0
    except ConvertError as e:
        print("error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
