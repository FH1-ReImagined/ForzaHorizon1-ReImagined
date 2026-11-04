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
  fm4_cars.py list    --fm4 FM4_DISC1 [--fm4-content DIR] --fh1 GAME [--update TU]
  fm4_cars.py convert --fm4 FM4_DISC1 [--fm4-content DIR] --fh1 GAME [--update TU]
                      [--dlc DIR] --out MOD_DIR (--cars A,B,... | --all)

FM4_DISC1 is the unpacked disc 1 (the folder with default.xex and Media); --fm4-content is a
folder holding the unpacked disc 2 car packs (each with Media/DLCZips/*_pri_65.zip). GAME is
FH1's game folder (with media), TU its title update folder (with media.zip); --dlc the
installed FH1 DLC folder (.../4D5309C9/00000002), so their car names are kept and their cars
are not added twice. Standard library only (LZX through tools/fxobj_shaders.py).
"""
import argparse
import os
import re
import sqlite3
import struct
import sys
import tempfile
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_carbin  # noqa: E402
import fm4_physdef  # noqa: E402
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
    def __init__(self):
        self.open = {}

    def get(self, path):
        z = self.open.get(path)
        if z is None:
            z = self.open[path] = zipfile.ZipFile(path)
        return z


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


class FM4:
    """The player's FM4 files: disc 1 and the disc 2 car packs."""

    def __init__(self, disc1, content=None):
        self.media = find_ci(disc1, "Media")
        if not self.media:
            raise ConvertError("no Media folder in %s: point at FM4's unpacked disc 1" % disc1)
        self.db_path = find_ci(self.media, "db", "gamedb.slt")
        if not self.db_path:
            raise ConvertError("no Media/db/gamedb.slt in %s" % disc1)
        self.packs = []  # (zip path, string table zip path)
        if content:
            for root, dirs, files in os.walk(content):
                for f in files:
                    if re.match(r"(?i)\d+_pri_65\.zip$", f) and "dlczips" in root.lower():
                        st = [os.path.join(root, g) for g in files if re.match(r"(?i)stringtables_pri_\d+\.zip$", g)]
                        self.packs.append((os.path.join(root, f), st[0] if st else None))
        self.packs.sort()
        self._cars = None

    def db(self):
        return sqlite3.connect("file:%s?mode=ro" % self.db_path, uri=True)

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

    def thumbnail(self, car_id):
        z = find_ci(self.media, "UI", "Textures.zip")
        return zip_member(z, "Thumbnails/Thumbnail_%d.xds" % car_id)

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
        if dlc and os.path.isdir(dlc):
            for root, _, files in os.walk(dlc):
                if PACK_NAME.lower() in root.lower():
                    continue  # this tool's own package (an earlier import)
                for f in files:
                    m = re.match(r"(?i).+_pri_(\d+)\.zip$", f)
                    if m and "dlczips" in root.lower():
                        self.dlc_zips.append((int(m.group(1)), os.path.join(root, f)))
        self.dlc_zips.sort()
        self._tmp = []

    def close(self):
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

    def dlc_car_names(self):
        """MediaNames and ids of the installed DLC packs' cars (their DB patches)."""
        names, ids = set(), set()
        for _, path in self.dlc_zips:
            z = ZIPS.get(path)
            for i in z.infolist():
                if re.search(r"(?i)/db/patch/[^/]+\.slt$", i.filename):
                    p = self._temp(Member(z, path, i).read())
                    try:
                        c = sqlite3.connect("file:%s?mode=ro" % p, uri=True)
                        for cid, name in c.execute("select Id, MediaName from Data_Car"):
                            ids.add(cid)
                            names.add(str(name).lower())
                        c.close()
                    except sqlite3.Error:
                        pass
        return names, ids

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


class Database:
    """Builds the DB patch: the selected cars' rows from FM4's database in FH1's tables."""

    def __init__(self, fm4db, fh1db):
        self.fm4, self.fh1 = fm4db, fh1db
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
        pk = columns(self.fh1, table)[0]
        have = self.rows.setdefault(table, {})
        for r in rows:
            have[r.get(pk)] = r

    def collect(self, car_ids):
        car_ids = set(car_ids)
        first = {}
        for table, col in CAR_TABLES:
            rows = self._select(table, col, car_ids)
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
        makes = {r["MakeID"] for r in first.get("Data_Car", []) if r.get("MakeID") is not None}
        self._add("List_CarMake", self._select("List_CarMake", "ID", makes))
        all_rows = [r for rows in first.values() for r in rows] + level2
        for pat, table, col in REFS:
            keys = {v for r in all_rows for c, v in r.items() if pat.match(c) and v is not None}
            self._add(table, self._select(table, col, keys))

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

    def __init__(self, fm4, fh1, out, package=None):
        self.fm4, self.fh1, self.out = fm4, fh1, out
        self.fm4db, self.fh1db = fm4.db(), fh1.db()
        self.donors = None
        self.donor_attribs = None
        self.wheels_done = set()
        self.package = package
        self.pack = PackWriter(os.path.join(package, "Media", "DLCZips"), PACK_STEM, PACK_PRIORITY) if package else None

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

    def candidates(self):
        """[(id, MediaName, has files, reason)] for every FM4 car FH1 lacks."""
        fh_ids = {r[0] for r in self.fh1db.execute("select Id from Data_Car")}
        fh_names = {str(r[0]).lower() for r in self.fh1db.execute("select MediaName from Data_Car")}
        dlc_names, dlc_ids = self.fh1.dlc_car_names()
        files = self.fm4.car_files()
        out = []
        for cid, name, make in self.fm4db.execute("select Id, MediaName, MakeID from Data_Car order by Id"):
            low = str(name).lower()
            if cid in fh_ids or low in fh_names or cid in dlc_ids or low in dlc_names:
                continue
            reason = None
            if name in FH1_DLC_CARS or low in {n.lower() for n in FH1_DLC_CARS}:
                reason = "an FH1 DLC car"
            elif low not in files:
                reason = "no model in the FM4 files given"
            out.append((cid, name, reason))
        return out

    def convert_wheel(self, name):
        if name in self.wheels_done or self.fh1.has_wheel(name):
            return True
        files = self.fm4.wheel_files(name)
        if not files:
            return False
        for rel, m in files:
            low = rel.lower()
            if low in SKIP_FILES:
                continue
            if low.endswith(".carbin"):
                self.put("media/wheels/%s/%s" % (name, rel), fm4_carbin.convert(m.read(), "wheel/%s/%s" % (name, low)))
            else:
                self.put("media/wheels/%s/%s" % (name, rel), member=m)
        self.wheels_done.add(name)
        return True

    def _audio_zip_names(self, game_media, rel):
        z = find_ci(game_media, *rel.split("/"))
        if not z:
            return {}
        return {i.filename.lower(): (z, i) for i in ZIPS.get(z).infolist()}

    def audio(self, car_name, engine_names, report):
        """The car's engine sound set-up. FH1 has every FM4 sound bank and harmonic tuning
        file, and the car and engine tuning (CarModelTuning, EngineTuning) of most FM4 cars;
        what it lacks is taken from FM4's copy (its audio/cars archives)."""
        if not hasattr(self, "_fh_audio"):
            self._fh_audio = {k: self._audio_zip_names(self.fh1.media, v) for k, v in AUDIO_ZIPS.items()}
            self._fm_audio = {k: self._audio_zip_names(self.fm4.media, v) for k, v in AUDIO_ZIPS.items()}
            if self.fh1.update:
                for i in ZIPS.get(self.fh1.update).infolist():
                    for k, v in AUDIO_ZIPS.items():
                        pre = v[:-4].lower() + "/"
                        if i.filename.lower().startswith(pre):
                            self._fh_audio[k][i.filename.lower()[len(pre):]] = (self.fh1.update, i)
        wanted = [("cmt", car_name + "_CMT.xml")] + [("et", e + "_ET.xml") for e in engine_names]
        added = []
        for kind, fname in wanted:
            if fname.lower() in self._fh_audio[kind]:
                continue
            src = self._fm_audio[kind].get(fname.lower())
            if not src:
                continue
            data = Member(ZIPS.get(src[0]), src[0], src[1]).read()
            self.put("media/%s/%s" % (AUDIO_ZIPS[kind][:-4].lower(), fname), data)
            added.append(fname)
            if kind == "et":
                for ht in sorted(set(re.findall(r'"([^"]+_HT\.xml)"', data.decode("latin-1")))):
                    if ht.lower() in self._fh_audio["ht"]:
                        continue
                    hsrc = self._fm_audio["ht"].get(ht.lower())
                    if hsrc:
                        self.put("media/audio/cars/engines/harmonictuning/" + ht,
                                   Member(ZIPS.get(hsrc[0]), hsrc[0], hsrc[1]).read())
                        added.append(ht)
                    report.setdefault("audio_missing", []).append(ht) if not hsrc else None
        if added:
            report.setdefault("audio", []).append((car_name, added))

    def convert_car(self, cid, name):
        self.load_donors()
        disp, files = self.fm4.car_files()[name.lower()]
        names = {rel.lower(): (rel, m) for rel, m in files}
        main = names.get(name.lower() + ".carbin")
        if not main:
            raise ConvertError("%s: no %s.carbin" % (name, name))
        maxdata = names.get("physics/maxdata.xml")
        if not maxdata:
            raise ConvertError("%s: no physics/maxdata.xml" % name)
        base = "media/cars/%s/" % name
        main_fh1 = None
        for low, (rel, m) in sorted(names.items()):
            if low in SKIP_FILES:
                continue
            if low.endswith(".carbin"):
                data = fm4_carbin.convert(m.read(), "car/%s/%s" % (name, low))
                self.put(base + "stripped_" + low, fm4_carbin.strip(data))
                if low == name.lower() + ".carbin":
                    main_fh1 = data
                self.put(base + rel, data)
            elif low == "carattribs.xml":
                self.put(base + rel, carattribs_v21(m.read(), self.donor_attribs))
            else:
                self.put(base + rel, member=m)
        pts = model_points(main_fh1)
        phys, donor = fm4_physdef.build(maxdata[1].read(), pts, self.donors)
        self.put(base + "physicsdefinition.bin", phys)
        thumb = self.fm4.thumbnail(cid)
        if thumb:
            self.put("media/ui/textures/thumbnails/thumbnail_%d.xds" % cid, thumb)
        return donor

    def strings(self, rows, report):
        """<table>.str for each of FH1's languages: FH1's strings (with the DLC's) plus the new
        rows'. Rewrites the rows' references when a key had to move (another string of FH1's
        table has it)."""
        fm_langs = self.fm4.languages()
        langs = sorted(l for l in self.fh1.languages() if l not in ("dev", "loc"))
        moved_total = 0
        for table_name, users in STRING_TABLES.items():
            refs = []
            for t, cols in users:
                for r in rows.get(t, []):
                    for c in cols:
                        ref = r.get(c)
                        if isinstance(ref, str) and re.match(r"_&\d+$", ref):
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
                    if have is not None and have[1] == e[1]:
                        continue  # FH1 has it
                    new = table.add(e[1] or "IDS_FM4_%d" % key, e[0], e[3], moved.get(n, key))
                    added = True
                    if new != key:
                        moved[n] = new
                if added:
                    self.write("media/stringtables/%s/%s.str" % (lang, table_name.lower()), table.encode())
            for t, cols in users:
                for r in rows.get(t, []):
                    for c in cols:
                        ref = r.get(c)
                        if isinstance(ref, str) and re.match(r"_&\d+$", ref) and int(ref[2:]) in moved:
                            n = int(ref[2:])
                            r[c] = "_&%d" % ((n & ~0xFFFF) | moved[n])
            moved_total += len(moved)
        report["moved_string_keys"] = moved_total

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
        db = Database(self.fm4db, self.fh1db)
        db.collect([c for c, _ in chosen])
        rows = db.finish(report)
        wheels = {r["StockWheelID"] for r in rows.get("Data_Car", [])}
        wheel_names = {}
        for wid in wheels:
            r = self.fm4db.execute("select MediaName from List_Wheels where ID=?", (wid,)).fetchone()
            if r:
                wheel_names[wid] = r[0]
        done = []
        for k, (cid, name) in enumerate(chosen):
            if cancelled():
                raise Cancelled()
            if progress:
                progress(k, len(chosen), name)
            else:
                log("converting %s (%d)" % (name, cid))
            try:
                self.convert_car(cid, name)
                row = [r for r in rows["Data_Car"] if r["Id"] == cid][0]
                engines = [r[0] for r in self.fm4db.execute(
                    "select e.MediaName from List_UpgradeEngine u join Data_Engine e on "
                    "e.EngineID=u.EngineID where u.Ordinal=?", (cid,))]
                self.audio(name, engines, report)
                wn = wheel_names.get(row["StockWheelID"])
                if wn and not self.convert_wheel(wn):
                    log("  %s: no files for its wheel %s" % (name, wn))
                done.append(cid)
                report["cars"].append((cid, name, row["ClassID"], round(row["PerformanceIndex"] * 1000)))
            except (ConvertError, fm4_carbin.CarbinError, fm4_physdef.PhysdefError) as e:
                report["skipped"].append((cid, "%s: %s" % (name, e)))
        if not done:
            raise ConvertError("no car converted")
        keep = set(done)
        rows = {t: [r for r in rs if t not in dict(CAR_TABLES) or r.get(dict(CAR_TABLES)[t]) in keep]
                for t, rs in rows.items()}
        self.finish_package(report)
        self.strings(rows, report)
        dbdir = os.path.join(self.out, "media", "db")
        os.makedirs(dbdir, exist_ok=True)
        write_full_db(self.fh1.db_file, self.fh1db, rows, os.path.join(dbdir, "gamedb.slt"))
        with open(os.path.join(self.out, "mod.toml"), "w", encoding="utf-8") as f:
            f.write('name = "%s"\nversion = "1"\ndescription = "%d Forza Motorsport 4 cars, '
                    'converted on this PC from your own FM4 discs"\n' % (MOD_NAME, len(done)))
        return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["list", "convert"])
    ap.add_argument("--fm4", required=True)
    ap.add_argument("--fm4-content")
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
        fm4 = FM4(a.fm4, a.fm4_content)
        fh1 = FH1(a.fh1, a.update, a.dlc)
        if a.cmd == "list":
            c = Converter(fm4, fh1, None)
            for cid, name, reason in c.candidates():
                print("%5d %-32s %s" % (cid, name, reason or "ok"))
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
        for car, files in rep.get("audio", []):
            print("sound set-up from FM4 for %s: %s" % (car, ", ".join(files)))
        if rep.get("conflicts"):
            print("rows whose key FH1 uses differently (left out):", rep["conflicts"][:20])
        fh1.close()
        return 0
    except ConvertError as e:
        print("error:", e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
