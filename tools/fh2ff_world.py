#!/usr/bin/env python3
"""Forza Horizon 2 Presents Fast & Furious (Xbox 360): its world, media/tracks/Anthem, as a world
FH1 drives in free roam, from the player's own copies of the games (#821). Forza Horizon 2's own
world (the same folder, the whole map) is built the same way: its disc folder as FF_DISC.

It writes a track folder for the circuit mount (--fm4_track_dir, as FM4's circuits use it) and a
mod; the launcher's import runs it (launcher/forza_launcher/fh2ffworld.py). What it reads and
writes is the player's own and stays on their PC: never share the output.

Usage:
  tools/fh2ff_world.py build FF_DISC FH1_DISC UPDATE_MEDIA_ZIP OUT [--fh2 FH2_DISC]
      FF_DISC           F&F's disc folder (its media/tracks/Anthem)
      FH1_DISC          FH1's disc folder (media/zipmanifest.xml, gamemodes.zip, Colorado's audio
                        and lighting, the map profiles)
      UPDATE_MEDIA_ZIP  the title update's media.zip (its gamedb.slt and game modes)
      FH2_DISC          Forza Horizon 2's disc folder, optional. With it, F&F's closed roads take
                        FH2's road types (they show on the map), the road closures F&F placed are
                        taken out, and the full map shows FH2's map art
    OUT/fm4-tracks/anthem/   the track folder: media/tracks/Anthem (the world converted),
                             media/zipmanifest.xml, media/audio/tracks/Anthem, spawn.txt (a
                             place on a road), autopilot.txt (a line along that road for the
                             dev autopilot) and media/tracks/Anthem/fm4_stream_blocks.txt (the
                             game's streaming pool)
    OUT/mods/fh2ff-anthem/   the mod: the database (Environments 70 = Anthem, free roam's Tracks
                             row 317 on it), Colorado's game modes as Anthem's, the lighting
                             templates and, with --fh2, the map's background
  tools/fh2ff_world.py check FF_DISC
      reads F&F's layout, every zone and every procedural geometry file and converts them in
      memory: the converted zones must read back whole

To run it (dev), with the game's own launch arguments and environment:
  --fm4_track_dir=OUT/fm4-tracks/anthem --forza_mods_dir=OUT/mods --forza_mods=fh2ff-anthem
  FORZA_INSECURE_FILES='zipmanifest.xml|\\tracks\\anthem\\'
  FORZA_TRACK_SPAWN=<the line in spawn.txt>
  FORZA_FM4_COL=0 FORZA_TRACK_NO_TRAFFIC=0  (fm4_stream_blocks.txt would turn a circuit's on: the
                             world's collision streams and traffic drives its roads)
  FORZA_AUTOPILOT=<autopilot.txt>,1.0 FORZA_AUTOPILOT_DELAY=6  (drives it)

docs/fh2ff-world.md has what is converted and why, and what is not done yet.
"""
import bisect
import collections
from contextlib import ExitStack
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import struct
import sys
import tempfile
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import fm4_pvs  # noqa: E402
import fm4_routes  # noqa: E402
import fm4_track_mod  # noqa: E402

NAME = "Anthem"            # the world's folder and media name in both FH2 games
ENVIRONMENT = 70           # F&F's own Environments id for Anthem; FH1 has no row 70
FREE_ROAM = 317            # FH1's free-roam Tracks row
MOD = "fh2ff-anthem"

# A zone's additional files (fm4_pvs.read_rec16: user, unique id, type, volume entry): the types
PRELOAD = 9                # a file to stream in with the zone; FH1's zones list their models so
GEOMETRY = 2               # a procedural geometry file (.pgeo)
SOUND_BANKS = (5, 6)       # FMOD event and bank files of FH2's audio project: not given to FH1

# Procedural geometry (.pgeo, "OEGP"): proceduralGeometry::CProceduralGeometry::CGeometryData, 0x60
# bytes (the FH2 prototype's PDB): +0x30 m_Type (0 Points, 1 Billboards, 2 Vegetation,
# 3 Characters, 4 AnimatedObject, 5 AnimatedScene, 6 LightGlows, 7 Models, 8 LightMaps), +0x3C
# m_Name, +0x40 m_NumTextures, +0x44 m_NumGeometries, +0x48 m_FileSize, +0x4C
# m_SecondaryDataBufferSize, +0x50 m_TemporaryBufferSize, +0x54 m_NumModels, +0x58
# m_Dynamic3DData (non-zero: the block follows the header). Pointer fields hold the size of what
# they point to, and the loader lays the parts out one after another, each 16-byte aligned (TU4
# sub_82EBAC70). PVS::CPVSModelInstanceDynamic3DData is FH2's 76 bytes (u32 physics definition,
# char[32] gameplay id, u8 route count, a pad byte, u16 routes[19]) and FH1's 56 (u8 routes[19],
# no pad): FH1 read F&F's 20 bytes off and faulted.
DYN_FH2, DYN_FH1 = 76, 56
ANIMATED_SCENE = 5
# Procedural types left out of the zones (a test switch, FH2FF_PGEO_DROP=5,...): none now. Animated
# scenes faulted until convert_pgeo kept their references with their name.
PGEO_DROP = {int(k) for k in os.environ.get("FH2FF_PGEO_DROP", "").split(",") if k}

# The collision object types F&F places to close roads (CollObjs.xml PhysicsType), taken out where
# FH2 has no such object at the same place.
CLOSURES = re.compile(r"(?i)barrier_crowd_fence|trafficcone|polereflector|bar_metal_mesh|"
                      r"sign_routebarree|bollards_intersection|barrier_intersection")

# A zone's soundscape names reverb and ambience templates and sound banks by F&F's own numbers;
# with Colorado's audio set they are other things, so every soundscape is written silent.
SILENT_TILE = b"""<?xml version="1.0" encoding="utf-8"?>
<AudioTile eventfilecrc="0" soundbankfilecrc="0">
  <TrackAudio soundbank="">
    <Track3D/>
    <Track2D/>
    <SharedAudio soundbank="">
      <Shared3D/>
      <Shared2D/>
    </SharedAudio>
    <SharedAudioDynamic soundbank="">
      <Shared3D/>
      <Shared2D/>
    </SharedAudioDynamic>
  </TrackAudio>
  <ReverbZones/>
  <AmbienceZones/>
  <Trees/>
  <Crowds/>
</AudioTile>
"""


# ---- archives ----

class Archive:
    """A track archive opened for reading; close() (or a with block) closes it and removes the
    folder member_data decompresses into."""

    def __init__(self, path):
        self.path = path
        self.zf = zipfile.ZipFile(path)
        # One of each member: FH2's archive (as FH1's Colorado) stores a file again near each zone
        # that streams it, 220,842 members for 50,956 files; F&F's once. The copies are the same
        # file (an archive where they are not is refused); the first is kept.
        self.infos, first = [], {}
        for i in self.zf.infolist():
            key = i.filename.replace("\\", "/").lower()
            if key not in first:
                first[key] = i
                self.infos.append(i)
            elif (i.CRC, i.file_size, i.compress_size) != (first[key].CRC, first[key].file_size, first[key].compress_size):
                raise ValueError(f"{path}: two different files named {i.filename}")
        self.copies = len(self.zf.infolist()) - len(self.infos)
        self.fh = open(path, "rb")
        self.tmp = tempfile.mkdtemp(prefix="fh2ff-")
        self.by_base = collections.defaultdict(list)
        for i in self.infos:
            self.by_base[i.filename.replace("\\", "/").split("/")[-1].lower()].append(i)

    def read(self, info):
        return fm4_track_mod.member_data(self.path, self.zf, info, self.fh, self.tmp)

    def raw(self, info):
        self.fh.seek(fm4_track_mod.data_offset(info, self.fh))
        return self.fh.read(info.compress_size)

    def close(self):
        self.fh.close()
        self.zf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class TrackZipWriter:
    """FH1's track archive layout (fm4_pvs.write_track_zip's): no local headers, the data offset
    in extra field 0x1123. Members are stored, or copied as they are (LZX, method 21)."""

    def __init__(self, path):
        self.f = open(path, "wb")
        self.dir = []

    def _entry(self, name, method, crc, csize, usize, blob):
        self.dir.append((name.replace("\\", "/"), method, crc, csize, usize, self.f.tell()))
        self.f.write(blob)

    def add(self, name, data):
        self._entry(name, 0, zlib.crc32(data) & 0xFFFFFFFF, len(data), len(data), data)

    def add_raw(self, name, info, raw):
        self._entry(name, info.compress_type, info.CRC, info.compress_size, info.file_size, raw)

    def close(self):
        """The zipmanifest.xml dirstart, dirsize (central directory and end record), direntries."""
        start = self.f.tell()
        cd = bytearray()
        for name, method, crc, csize, usize, off in self.dir:
            n = name.encode("latin1")
            extra = struct.pack("<HHI", 0x1123, 4, off)
            cd += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 10, 10, 0, method, 0, 0, crc, csize,
                              usize, len(n), len(extra), 0, 0, 0, 0, off) + n + extra
        self.f.write(cd)
        self.f.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(self.dir), len(self.dir),
                                 len(cd), start, 0))
        self.f.close()
        return start, len(cd) + 22, len(self.dir)


# ---- the world's files ----

def read_filename_map(data):
    if not data:
        return []
    n = struct.unpack_from(">I", data, 0)[0] // 4
    return [data[o:data.index(b"\0", o)].decode("latin1") for o in struct.unpack_from(f">{n}I", data, 0)]


def fh1_names(paths):
    """FH1 numbers a track archive's files in its order (fm4_pvs.archive_order: by CRC-32 of the
    lower-case path); its FilenameMap holds the base names, lower case for members in a folder (as
    Colorado's). F&F's map is in FH2's own order, and with it FH1 read a texture as the first zone.
    Returns (the paths in FH1's order, the FilenameMap bytes)."""
    order = fm4_pvs.archive_order([p.replace("\\", "/") for p in paths])
    names = [p.split("/")[-1].lower() if "/" in p else p for p in order]
    return order, fm4_pvs.write_filename_map(names)


def convert_hex(data):
    """HEXY 102 as 101: the header's version, the grid, the zones' cells, then FH1's byte a zone
    (F&F's second per-zone array; its first, values 0 to 2, FH1 has not)."""
    if data[:4] != b"HEXY":
        raise ValueError("not a hex file")
    ver, side, lx, lz, zones, cx, cz = struct.unpack_from(">ifffiii", data, 4)
    if ver == 101:
        return data
    if ver != 102:
        raise ValueError(f"hex version {ver}")
    at = 32 + 4 * cx * cz + 8 * zones
    if len(data) != at + 2 * zones:
        raise ValueError("hex version 102: not two bytes a zone after the cells")
    return data[:4] + struct.pack(">i", 101) + data[8:at] + data[at + zones:]


def pgeo_kind(d):
    return struct.unpack_from(">I", d, 0x30)[0] if d[:4] == b"OEGP" else -1


def pgeo_routes(d):
    if d[:4] != b"OEGP" or not struct.unpack_from(">I", d, 0x58)[0]:
        return []
    n = min(d[0x60 + 36], 19)
    return list(struct.unpack_from(f">{n}H", d, 0x60 + 38))


def convert_pgeo(d, routes):
    """F&F's procedural geometry file as FH1's: the dynamic 3D data block made FH1's size (route
    numbers mapped to bytes), the rest moved to keep its 16-byte alignment, m_FileSize set.
    Unchanged without the block.

    An animated scene's name is followed directly (4-byte aligned) by its texture and geometry
    references and its 28-byte CAnimatedSceneData, as in Colorado's scenes; only its object
    instances start 16-byte aligned. Those move with the name (moving them to the next 16 bytes
    lost the geometry reference: the scene drew, and faulted on, object 0)."""
    if d[:4] != b"OEGP" or not struct.unpack_from(">I", d, 0x58)[0]:
        return d
    if struct.unpack_from(">I", d, 0x48)[0] != len(d):
        raise ValueError("a procedural geometry file whose m_FileSize is not its size")
    dyn = d[0x60:0x60 + DYN_FH2]
    n = min(dyn[36], 19)
    ids = struct.unpack_from(f">{n}H", dyn, 38)
    new = dyn[:36] + bytes([n]) + bytes(routes.get(r, 0) for r in ids) + bytes(19 - n)
    name_len, ntex, ngeo = struct.unpack_from(">3I", d, 0x3C)
    old_name = 0x60 + DYN_FH2
    head = name_len
    if struct.unpack_from(">I", d, 0x30)[0] == ANIMATED_SCENE:
        head = ((name_len + 3) & ~3) + 8 * ntex + 8 * ngeo + 28
    old_rest = (old_name + head + 15) & ~15
    new_rest = (0x60 + DYN_FH1 + head + 15) & ~15
    out = bytearray(d[:0x60] + new + d[old_name:old_name + name_len])
    if head > name_len:   # a scene: the name's padding zeroed, its references and data after it
        out += bytes(((name_len + 3) & ~3) - name_len) + d[old_name + ((name_len + 3) & ~3):old_name + head]
    out += bytes(new_rest - len(out))
    out += d[old_rest:]
    struct.pack_into(">I", out, 0x48, len(out))
    return bytes(out)


def closures(ff_xml, fh2_xml):
    """F&F's road closures in its Ribbon_00/CollObjs.xml: the CLOSURES objects FH2 has not at the
    same place (an object of the same type within a metre). Returns (the file with each of them moved to y = -5000, under the
    world, every object kept in its place in the list; their (x, y, z))."""
    def objs(s):
        return [(m.start(4), m.end(4), m.group(2), float(m.group(3)), float(m.group(4)), float(m.group(5)))
                for m in re.finditer(r'<Obj(\d+) PhysicsType="([^"]+)" GraphicsName="[^"]*">\s*'
                                     r'<Pos x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"', s)]
    fh2_at = collections.defaultdict(list)
    for _, _, t, x, y, z in objs(fh2_xml):
        fh2_at[(t.lower(), round(x), round(z))].append((x, z))

    def in_fh2(t, x, z):
        return any(math.hypot(ox - x, oz - z) < 1.0 for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                   for ox, oz in fh2_at.get((t.lower(), round(x) + dx, round(z) + dz), ()))
    gone = [o for o in objs(ff_xml) if CLOSURES.search(o[2]) and not in_fh2(o[2], o[3], o[5])]
    out, at = [], 0
    for a, b, *_ in sorted(gone):
        out.append(ff_xml[at:a])
        out.append("-5000.000000")
        at = b
    out.append(ff_xml[at:])
    return "".join(out), [(x, y, z) for _, _, _, x, y, z in gone]


class World:
    """F&F's world folder (media/tracks/Anthem), read with tools/fm4_pvs.py's FH1 reader (its
    layout is version 51)."""

    def __init__(self, root):
        self.root = root
        rb = os.path.join(root, "Ribbon_00")
        with open(os.path.join(rb, f"{NAME}_00.pvs"), "rb") as f:
            self.P = fm4_pvs.read_fh1(f.read())
        if self.P["version"] != fm4_pvs.FH2_VERSION:
            raise ValueError(f"layout version {self.P['version']}: {fm4_pvs.FH2_VERSION} expected")
        self.zip = Archive(os.path.join(root, "bin.zip"))
        try:
            with open(os.path.join(rb, "FilenameMap_00.dat"), "rb") as f:
                self.names = read_filename_map(f.read())
            self.entry = {n.lower(): i for i, n in enumerate(self.names)}
            if len(self.names) != len(self.zip.infos) or any(len(v) != 1 for v in self.zip.by_base.values()):
                raise ValueError("the archive's members and the filename map are not one to one by name")
            self.info_of = [self.zip.by_base[n.lower()][0] for n in self.names]
            order, self.fh1_map = fh1_names([i.filename for i in self.zip.infos])
            place = {p.lower(): k for k, p in enumerate(order)}
            self.vol = [place[i.filename.replace("\\", "/").lower()] for i in self.info_of]  # F&F's entry -> FH1's
            prefix = self.P["prefix"].lower()
            self.model_entry = {}
            for m in range(len(self.P["models"])):
                e = self.entry.get(f"{prefix}.{m:05d}.rmb.bin")
                if e is not None:
                    self.model_entry[m] = e
            self.zone_infos = sorted((i for i in self.zip.infos if i.filename.lower().endswith(".pvsz")),
                                     key=lambda i: i.filename.lower())
        except BaseException:
            self.zip.close()
            raise
        self.report = collections.Counter()
        self.drop = set()
        self._zone_data = None
        self._kinds = {}
        self._routes = None
        self.zone_models, self.uid_models = {}, {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.zip.close()

    def stream_blocks(self, hex_data):
        """FH1's streaming blocks (1 KB of file each) the largest union of a hex cell's zone's
        model files and its six neighbours' takes (tools/fm4_pvs.py's measure, after convert())."""
        _, _, _, _, _, gw, gh = struct.unpack_from(">ifffiii", hex_data, 4)
        cells = struct.unpack_from(f">{gw * gh}i", hex_data, 32)
        size = {}
        best = 0
        for r in range(gh):
            for col in range(gw):
                if cells[r * gw + col] < 0:
                    continue
                side = (0, 1) if col % 2 else (-1, 0)   # odd columns sit half a hex lower
                near = [(col, r), (col, r - 1), (col, r + 1)] + [(col + dc, r + dr) for dc in (-1, 1) for dr in side]
                models = set()
                for c, rr in near:
                    if 0 <= c < gw and 0 <= rr < gh and cells[rr * gw + c] >= 0:
                        models |= {m for m in self.uid_models.get(cells[rr * gw + c], ()) if m in self.model_entry}
                for m in models - size.keys():
                    size[m] = self.info_of[self.model_entry[m]].file_size
                best = max(best, sum(size[m] for m in models))
        return (best + 1023) // 1024

    def zones(self):
        if self._zone_data is None:
            self._zone_data = [(i, self.zip.read(i)) for i in self.zone_infos]
        for info, data in self._zone_data:
            r = fm4_pvs.Reader(data)
            c = fm4_pvs.read_cell(r, self.P["g2"], self.P["version"])
            if r.p != len(data):
                raise ValueError(f"{info.filename}: read {r.p} of {len(data)} bytes")
            if not len(c["instances"]) == len(c["drawn"]) == len(c["placements"]):
                raise ValueError(f"{info.filename}: {len(c['instances'])} instances, {len(c['drawn'])} "
                                 f"drawn flags and {len(c['placements'])} placements: not one to one")
            yield info, c

    def kind(self, entry):
        if entry not in self._kinds:
            self._kinds[entry] = pgeo_kind(self.zip.read(self.info_of[entry]))
        return self._kinds[entry]

    def route_map(self):
        """{F&F's route number: FH1's (a byte)}: 0 stays 0, the others in order from 1 (the zones'
        instances' routes and the procedural geometry's)."""
        if self._routes is None:
            used = set()
            for _, c in self.zones():
                for e in c["placements"]:
                    if e["extra"]:
                        used |= set(e["extra"][1])
            for i in self.zip.infos:
                if i.filename.lower().endswith(".pgeo"):
                    used |= set(pgeo_routes(self.zip.read(i)))
            ids = sorted(used - {0})
            if len(ids) > 255:
                raise ValueError(f"{len(ids)} route numbers: more than a byte holds")
            self._routes = {0: 0}
            self._routes.update({r: k + 1 for k, r in enumerate(ids)})
        return self._routes

    def remove_closures(self, fh2_world):
        """closures() on this world's CollObjs.xml, and the zones' dynamic instances standing where
        a removed object stood (a CollObjs object and its instance are tied by position only)
        left out of the zones. Returns the new CollObjs.xml."""
        with open(os.path.join(self.root, "Ribbon_00", "CollObjs.xml"), encoding="utf-8", errors="replace") as f:
            mine = f.read()
        with open(os.path.join(fh2_world, "Ribbon_00", "CollObjs.xml"), encoding="utf-8", errors="replace") as f:
            theirs = f.read()
        xml, gone = closures(mine, theirs)
        grid = collections.defaultdict(list)
        for x, y, z in gone:
            grid[(round(x), round(z))].append((x, y, z))
        for _, c in self.zones():
            for v, e in zip(c["instances"], c["placements"]):
                if not e["extra"]:
                    continue
                x, y, z = e["pos"]
                if any(abs(ox - x) < 1.0 and abs(oz - z) < 1.0 and abs(oy - y) < 3.0
                       for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                       for ox, oy, oz in grid.get((round(x) + dx, round(z) + dz), ())):
                    self.drop.add(v & 0x7FFFFFFF)
        self.report["road closure objects taken out"] = len(gone)
        self.report["road closure instances left out of the zones"] = len(self.drop)
        return xml

    def convert_zone(self, c, base, routes):
        """The zone as FH1's: route numbers as bytes; the instances of models with no file (and of
        removed closures) gone; F&F's sound banks and the procedural geometry types of PGEO_DROP
        out of its file lists; every file at its FH1 number; a preload entry for each visible
        model's file (unique id base + its F&F number)."""
        inst = self.P["instances"]
        keep = [k for k, v in enumerate(c["instances"])
                if inst[v & 0x7FFFFFFF]["model"] in self.model_entry and (v & 0x7FFFFFFF) not in self.drop]
        self.report["instances taken out (no model file, or a closure)"] += len(c["instances"]) - len(keep)
        out = dict(c)
        out["instances"] = [c["instances"][k] for k in keep]
        out["drawn"] = [c["drawn"][k] for k in keep]
        out["placements"] = []
        for k in keep:
            e = c["placements"][k]
            if e["extra"]:
                x, ids, tail = e["extra"]
                e = dict(e, extra=(x, bytes(routes[r] for r in ids), tail))
            out["placements"].append(e)

        def wanted(f):
            if f[2] in SOUND_BANKS:
                return False
            return not (f[2] == GEOMETRY and self.kind(f[3]) in PGEO_DROP)

        def moved(f):
            return (f[0], f[1], f[2], self.vol[f[3]])
        models = [m for m in c["models"] if m in self.model_entry]
        own = [moved(f) for f in c["files"] if wanted(f)]
        self.report["file entries left out (sound banks, procedural types left out)"] += len(c["files"]) - len(own)
        out["models"] = models
        out["files"] = own + [(self.vol[self.model_entry[m]], base + self.model_entry[m], PRELOAD,
                               self.vol[self.model_entry[m]]) for m in models]
        self.report["preload entries added"] += len(models)
        if "files2" in c:
            out["files2"] = [moved(f) for f in c["files2"] if wanted(f)]
            out["models2"] = [m for m in c["models2"] if m in self.model_entry]
        return out

    def convert(self):
        """(the layout, {zone member: bytes}, the hex file, the FilenameMap, the PVSZLookup), all
        FH1's version 50 forms."""
        P = self.P
        base = P["resources"]
        routes = self.route_map()
        zones, sizes = {}, {}
        for info, c in self.zones():
            out = self.convert_zone(c, base, routes)
            w = fm4_pvs.Writer()
            fm4_pvs.write_cell(w, out, P["g2"])
            zones[info.filename] = bytes(w.b)
            sizes[info.filename.replace("\\", "/").split("/")[-1].lower()] = (len(out["instances"]), len(out["models"]))
            self.zone_models[info.filename.replace("\\", "/").split("/")[-1].lower()] = set(out["models"])
        rb = os.path.join(self.root, "Ribbon_00")
        with open(os.path.join(rb, "PVSZLookup_00.dat"), "rb") as f:
            lk = f.read()
        look = [struct.unpack_from(">ii", lk, 8 * i) for i in range(len(lk) // 8)]
        table = list(P["zones"])
        for entry, uid in look:
            n = self.names[entry].lower()
            if uid < len(table) and n in sizes:
                old = table[uid]
                table[uid] = (sizes[n][0], old[1], sizes[n][1])
        layout = fm4_pvs.write_fh1(dict(P, resources=base + len(self.names) + 64, zones=table))
        lookup = b"".join(struct.pack(">ii", self.vol[entry], uid) for entry, uid in look)
        self.uid_models = {uid: self.zone_models[self.names[entry].lower()] for entry, uid in look
                           if self.names[entry].lower() in self.zone_models}
        with open(os.path.join(rb, f"{NAME}_00.hex"), "rb") as f:
            hexd = convert_hex(f.read())
        return layout, zones, hexd, self.fh1_map, lookup


# ---- the road network, the spawn, the map ----

def zone_cells(hex_data):
    """A function (x, z) -> True when the hex cell there and the cells around it have zones (the
    grid of fm4_pvs.HexGrid). Outside every zone FH1 draws from the nearest one, which sees the
    place only from afar: a spawn there had the road under the car invisible."""
    ver, side, ox, oz, zones, w, h = struct.unpack_from(">ifffiii", hex_data, 4)
    cells = struct.unpack_from(f">{w * h}i", hex_data, 32)
    grid = fm4_pvs.HexGrid.__new__(fm4_pvs.HexGrid)
    grid.r, grid.ox, grid.oz, grid.w, grid.h = side, ox, oz, w, h

    def used(c, r):
        return 0 <= c < w and 0 <= r < h and cells[r * w + c] >= 0

    def inside(x, z):
        if not (ox <= x <= ox + (1.5 * w + 1) * side and oz <= z <= oz + 3 ** 0.5 * side * (h + 1)):
            return False                      # off the grid (cell_of finds no cell there)
        c, r = grid.cell_of(x, z)
        return all(used(c + dc, r + dr) for dc in (-1, 0, 1) for dr in (-1, 0, 1))
    return inside


def spawn_of(net, inside):
    """(x,y,z,fx,fz at the start of the longest stretch of road whose cells all have zones, an
    autopilot line along that stretch: "x y z speed" a row)."""
    keys, vals = net["keys"], net["values"]
    best = None
    for count, first, tc, ft in net["ways"]:
        tags = {keys[net["tags"][ft + j][0]] for j in range(tc)}
        if "road_type" not in tags:
            continue
        run = []
        for i in net["node_list"][first:first + count] + [None]:
            p = net["nodes"][i][1:4] if i is not None else None
            if p is not None and inside(p[0], p[2]):
                run.append(p)
                continue
            if len(run) > 1:
                length = sum(math.dist(run[k], run[k + 1]) for k in range(len(run) - 1))
                if best is None or length > best[0]:
                    best = (length, run)
            run = []
    if best is None:
        raise ValueError("no road inside the zones")
    line = keep_right(best[1])
    a, b = line[0], line[1]
    fx, fz = b[0] - a[0], b[2] - a[2]
    n = math.hypot(fx, fz) or 1.0
    return (f"{a[0]:.1f},{a[1] + 0.5:.1f},{a[2]:.1f},{fx / n:.3f},{fz / n:.3f}",
            "".join(f"{x:.2f} {y:.2f} {z:.2f} 20.0\n" for x, y, z in line), best[0])


# ---- races and playground arenas (data only: not yet tried in a lobby) ----

ROUTE_KINDS = (0, 2, 7)   # Tracks.RibbonConfiguration: circuit, point to point, playground arena
ROUTE_BASE = 5000         # the world's route rows' Tracks ids (FH1's own end at 1111)
_NAMED = re.compile(r"<NamedTransform name='(?P<name>[^']*)'(?P<attrs>[^>]*)>(?P<sp1>\s*)"
                    r"<Transform (?P<t>[^>]*?)\s*/>(?P<sp2>\s*)</NamedTransform>")


def _facing_unit(t):
    """A Transform's attributes with its facing a unit vector (F&F's are 2 to 4 long; FH1's 1)."""
    f = {k: float(v) for k, v in re.findall(r"facing\.([xyz])='([^']*)'", t)}
    n = math.sqrt(sum(v * v for v in f.values())) if len(f) == 3 else 0.0
    if not n or abs(n - 1) < 1e-3:
        return t
    return re.sub(r"facing\.([xyz])='[^']*'", lambda m: f"facing.{m.group(1)}='{f[m.group(1)] / n:.6g}'", t)


def route_xml(text):
    """An F&F/FH2 TrackRouteNNN.xml in FH1's terms: facings made unit vectors, an arena's edge
    (Arena_NN) named as FH1's (route_boundary_NN), and the places after a race FH1 looks for
    (post_race_location_NN, a race's: an arena has none) from F&F's end positions, else its start
    places. Other names stay."""
    blocks = list(_NAMED.finditer(text))
    if not blocks:
        return text
    out, at, places, arena_file = [], 0, {}, False
    for m in blocks:
        name, t = m.group("name"), _facing_unit(m.group("t"))
        attrs = m.group("attrs")
        arena = re.fullmatch(r"Arena_(\d+)", name)
        arena_file = arena_file or name.startswith("route_boundary")
        if arena:
            arena_file = True
            name, attrs = f"route_boundary_{int(arena.group(1)):02d}", ""
        for kind in ("end_position", "start_location"):
            k = re.fullmatch(kind + r"_?(\d+)", name)
            if k:
                places.setdefault(kind, []).append(t)
        out.append(text[at:m.start()])
        out.append(f"<NamedTransform name='{name}'{attrs}>{m.group('sp1')}<Transform {t}/>{m.group('sp2')}</NamedTransform>")
        at = m.end()
    out.append(text[at:])
    text = "".join(out)
    have = any(m.group("name").startswith("post_race_location") for m in blocks)
    src = places.get("end_position") or places.get("start_location")
    if not have and not arena_file and src and "</NamedTransforms>" in text:
        nl = "\r\n" if "\r\n" in text else "\n"
        add = "".join(f"\t\t<NamedTransform name='post_race_location_{k:02d}'>{nl}\t\t\t<Transform {t}/>{nl}"
                      f"\t\t</NamedTransform>{nl}" for k, t in enumerate(src))
        i = text.rindex("</NamedTransforms>")
        line = text.rfind("\n", 0, i) + 1
        text = text[:line] + add + text[line:]
    return text


def route_file(ribbon_dir, route_id):
    """The ribbon's TrackRouteNNN.xml for a route id (FH1 names it %03d), any case, or None."""
    want = f"trackroute{route_id:03d}.xml"
    return next((os.path.join(ribbon_dir, n) for n in os.listdir(ribbon_dir) if n.lower() == want), None)


def world_routes(src_db, ribbon_dir):
    """The world's own races and arenas: its Tracks rows (MediaName Anthem, a ROUTE_KINDS
    configuration) whose route file the ribbon holds, as {column: value}, by id."""
    con = sqlite3.connect(f"file:{Path(src_db).as_posix()}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(Tracks)")]
        rows = [dict(zip(cols, r)) for r in con.execute(
            f"SELECT * FROM Tracks WHERE MediaName LIKE ? AND RibbonConfiguration IN ({','.join('?' * len(ROUTE_KINDS))}) "
            "ORDER BY id", (NAME, *ROUTE_KINDS))]
    finally:
        con.close()
    return [r for r in rows if isinstance(r.get("RouteId"), int) and r["RouteId"] > 0
            and route_file(ribbon_dir, r["RouteId"])]


def add_world_routes(db, rows):
    """The rows put into FH1's Tracks as the world's (ids from ROUTE_BASE, Environments 70, its
    developer name as the shown one: F&F's string ids are not in FH1's tables). Only the columns
    FH1's table has. Returns {RibbonConfiguration: count}."""
    have = [r[1] for r in db.execute("PRAGMA table_info(Tracks)")]
    used = {r[0] for r in db.execute("SELECT id FROM Tracks")}
    counts = collections.Counter()
    next_id = ROUTE_BASE
    for row in rows:
        while next_id in used:
            next_id += 1
        r = dict(row, id=next_id, MediaName=NAME, EnvironmentId=ENVIRONMENT, RibbonIndex=0,
                 DisplayName=row.get("DevName") or f"Route {row['RouteId']}",
                 ShortDisplayName=row.get("DevName") or f"Route {row['RouteId']}")
        keys = [c for c in have if c in r]
        db.execute(f"INSERT INTO Tracks ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [r[c] for c in keys])
        used.add(next_id)
        counts[row["RibbonConfiguration"]] += 1
    return dict(counts)


NONE = 0xDEADBEEF


def owt2_line(d):
    """An FH2 AI line (OWTM version 2) as FH1's (version 1, fm4_routes.owt's points): ([(position,
    the vector across the road, the racing line)], looped).

    Version 2: the magic, 2 and 8 bytes; the segment count at 0x10; from 0x20 a 32-byte entry a
    segment (its first waypoint, the next segment, the other one at a branch, DEADBEEF for none,
    its waypoint count, its successors, 1, CAFEFEED); then 48-byte waypoints (position, the vector
    across, then -0.5, 0.5, the racing line, 0) and the magic again. FH1's line is one: the main
    chain from segment 0, looped when it comes back to it."""
    if d[:4] != b"OWTM" or struct.unpack_from(">I", d, 4)[0] != 2:
        raise ValueError("not an OWTM version 2 file")
    n = struct.unpack_from(">I", d, 0x10)[0]
    base = 0x20 + 32 * n
    total = (len(d) - base - 16) // 48
    if not n or total <= 0 or base + 48 * total + 16 != len(d):
        raise ValueError("an OWTM version 2 file of an unexpected size")
    segs = [struct.unpack_from(">8I", d, 0x20 + 32 * k) for k in range(n)]
    pts, seen, k, looped = [], set(), 0, False
    while k != NONE and k < n and k not in seen:
        seen.add(k)
        first, nxt, count = segs[k][0], segs[k][1], segs[k][4]
        if first + count > total:
            raise ValueError("an OWTM version 2 segment past the waypoints")
        for i in range(first, first + count):
            w = struct.unpack_from(">12f", d, base + 48 * i)
            pts.append((w[0:3], w[4:7], w[10]))
        looped = nxt == 0
        k = nxt
    return pts, looped


def ai_lines(zip_path, route_ids, out_dir):
    """The world's AI lines for its routes (aiopenworld.zip's <world>/Ribbon_00/RouteNNNN.owt) as
    FH1's route_NNN.owt in out_dir (the mod's aiopenworld folder, laid over FH1's archive). A
    line that does not read is left out. Returns the counts."""
    counts = collections.Counter()
    if not os.path.isfile(zip_path):
        return dict(counts)
    with Archive(zip_path) as z:
        for info in z.infos:
            m = re.fullmatch(r"(?i)[^/]+/ribbon_00/route(\d+)\.owt", info.filename.replace("\\", "/"))
            if not m or int(m.group(1)) not in route_ids:
                continue
            try:
                pts, looped = owt2_line(z.read(info))
            except ValueError:
                counts["not read"] += 1
                continue
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, f"route_{int(m.group(1)):03d}.owt"), "wb") as f:
                f.write(fm4_routes.owt(pts, looped))
            counts["looped" if looped else "point to point"] += 1
    return dict(counts)


STREAM_CAP = 65536   # the game's cap on streaming blocks (hooks/fm4_track.cpp kMaxBlocks)
LANE = 2.0   # metres right of a road's centre line: the middle of its right-hand lane


def keep_right(line, metres=LANE):
    """The line moved `metres` to the right of its way (right of a heading (fx, fz) is (fz, -fx),
    as the game's teleports place a car): the road network's nodes are its centre line, where
    oncoming traffic met the car head on."""
    out = []
    for k, p in enumerate(line):
        a, b = line[max(k - 1, 0)], line[min(k + 1, len(line) - 1)]
        fx, fz = b[0] - a[0], b[2] - a[2]
        n = math.hypot(fx, fz) or 1.0
        out.append((p[0] + fz / n * metres, p[1], p[2] - fx / n * metres))
    return out


def fix_tags(net):
    """FH1's names for two of F&F's tag names. FH1's map looks "oneway" up in the table and reads
    through a null pointer when it is not there (TU4 sub_82D98D20)."""
    renamed = {"oneway_forward": "oneway", "traffic_density_name": "traffic_density"}
    net["keys"] = [renamed.get(k, k) for k in net["keys"]]
    if net["keys"] != sorted(set(net["keys"])):   # the game searches the table as a sorted one
        raise ValueError("the road network's tag names are not sorted after renaming")
    # FH1's traffic asks for names F&F's table has not (the same unchecked lookup: a fault at
    # sub_82D9C8E0 once traffic spawned): every name of Colorado's table is in it, unused.
    table = sorted(set(net["keys"]) | set(COLORADO_TAG_NAMES))
    at = {k: i for i, k in enumerate(table)}
    net["tags"] = [(at[net["keys"][k]], v) for k, v in net["tags"]]
    net["keys"] = table
    density_ids(net)


# The tag names of Colorado's road network (colorado.nav, FH1's disc).
COLORADO_TAG_NAMES = ("ai_disabled", "allowedfordemo", "area_type", "deadend", "discovery_zone", "display_name",
                      "first_time_flow_disabled", "grid_control", "iefestivaldrive", "ignore_turn_flags", "oneway",
                      "road_type", "sat_nav_junction", "text", "traffic_density", "traffic_disabled", "uturn")


# F&F's traffic densities by name, as FH1's ids in its AIOpenWorld.xml (freeroam, traffic): 1
# empty, 2 B-road, 3 A-road and 5 a denser one, 4 freeway, 8 the festival's (none), 12 dirt, 14
# B-road denser (Red Rock's). F&F's own list has the same ids; its road network names them.
TRAFFIC_DENSITY = {"none": "1", "b": "2", "open_world": "2", "a": "3", "freeway": "4", "a_high": "5",
                   "a_large": "5", "festival": "8", "dirt": "12", "b_high": "14"}


def density_ids(net):
    """FH1 reads a road's traffic_density as a number (Colorado's are 2 to 17); F&F's are names,
    and with them no traffic came. Each becomes FH1's id (an unknown name: 1, empty). The value
    table is rebuilt sorted, each string once, as the game searches it."""
    keys, vals = net["keys"], net["values"]
    if "traffic_density" not in keys:
        return {}
    td = keys.index("traffic_density")
    new = [vals[v] if k != td else TRAFFIC_DENSITY.get(vals[v], vals[v] if vals[v].isdigit() else "1")
           for k, v in net["tags"]]
    table = sorted(set(new) | {s for s in vals if s not in TRAFFIC_DENSITY})
    at = {s: i for i, s in enumerate(table)}
    changed = collections.Counter(f"{vals[v]}>{new[i]}" for i, (k, v) in enumerate(net["tags"])
                                  if k == td and vals[v] != new[i])
    net["tags"] = [(k, at[s]) for (k, _), s in zip(net["tags"], new)]
    net["values"] = table
    return dict(changed)


def retype_blocked(net, fh2_net):
    """F&F closes the roads out of its area with road_type=blocked (FH2: none). FH1's map draws a,
    b, dirt and freeway only, so those roads were missing from it. Each takes the road type FH2's
    network has on the same nodes (to the metre), else "b". Returns the counts."""
    ok, ov = fh2_net["keys"], fh2_net["values"]
    at = {}
    for count, first, tc, ft in fh2_net["ways"]:
        t = {ok[fh2_net["tags"][ft + j][0]]: ov[fh2_net["tags"][ft + j][1]] for j in range(tc)}
        if t.get("road_type") in ("a", "b", "dirt", "freeway"):
            for i in fh2_net["node_list"][first:first + count]:
                x, y, z = fh2_net["nodes"][i][1:4]
                at[(round(x), round(z))] = t["road_type"]
    keys, vals = net["keys"], net["values"]
    if "road_type" not in keys or "blocked" not in vals:
        return {}
    rt, blocked = keys.index("road_type"), vals.index("blocked")
    changed = collections.Counter()
    for count, first, tc, ft in net["ways"]:
        for j in range(tc):
            k, v = net["tags"][ft + j]
            if k == rt and v == blocked:
                votes = collections.Counter()
                for i in net["node_list"][first:first + count]:
                    x, y, z = net["nodes"][i][1:4]
                    for dx in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            t = at.get((round(x) + dx, round(z) + dz))
                            if t:
                                votes[t] += 1
                new = votes.most_common(1)[0][0] if votes else "b"
                if new not in vals:        # the game searches the table as a sorted one
                    at = bisect.bisect(vals, new)
                    vals.insert(at, new)
                    net["tags"] = [(tk, tv + (tv >= at)) for tk, tv in net["tags"]]
                net["tags"][ft + j] = (k, vals.index(new))
                changed[new + (" (FH2's)" if votes else " (no FH2 road there)")] += 1
    return dict(changed)


def fh1_tile_name(member):
    """FH2's map tile name (Map/background/high-<row>-<col>.xds) as FH1's (high-<col>-<row>.xds),
    or None for another member."""
    m = re.fullmatch(r"(?i)map/background/(high|low)-(\d+)-(\d+)\.xds", member.replace("\\", "/"))
    return f"{m.group(1).lower()}-{m.group(3)}-{m.group(2)}.xds" if m else None


def surface_names(physics_zip, tmp):
    """The surface list of a game's physics.zip surfaceTypes.xml (a triangle's surface is its
    number in it)."""
    with zipfile.ZipFile(physics_zip) as z, open(physics_zip, "rb") as fh:
        info = next((i for i in z.infolist() if i.filename.replace("\\", "/").lower() == "surfacetypes.xml"), None)
        if info is None:
            raise ValueError(f"{physics_zip} holds no surfaceTypes.xml")
        xml = fm4_track_mod.member_data(physics_zip, z, info, fh, tmp).decode("utf-8", "replace")
    return re.findall(r'<SurfaceType value="([^"]+)"', xml.split("</SurfaceTypesList>")[0])


def surface_map(ff_names, fh1_names):
    """{F&F's surface number: FH1's} for each surface whose number differs. F&F's first 68 are
    FH1's; its four more (RallyGrass, Dirt2, Dirt3, RallyGravel) are past the end of FH1's
    table: each becomes FH1's surface of its name without "Rally" and a trailing number."""
    at = {n.lower(): i for i, n in enumerate(fh1_names)}
    out = {}
    for i, n in enumerate(ff_names):
        j = at.get(n.lower())
        if j is None:
            j = at.get(re.sub(r"\d+$", "", re.sub(r"(?i)^rally", "", n)).lower())
        if j is None:
            raise ValueError(f"F&F's surface {n} ({i}) has no FH1 counterpart")
        if j != i:
            out[i] = j
    return out


def remap_fiz(d, surfaces):
    """A collision cell file (.fiz: a 0x20 header, then FH1's cell, see fm4_col.py) with each
    triangle's surface (flags 0xTTMM8000, MM) remapped. Unchanged if none is."""
    if d[:4] != b"fiz " or len(d) < 0x30:
        return d
    nv, nt, hs, toff = struct.unpack_from(">4I", d, 0x20)
    if hs != 0x90 or toff != hs + 16 * nv or 0x20 + toff + 32 * nt > len(d):
        raise ValueError("a collision cell not in FH1's layout")
    out = None
    for k in range(nt):
        at = 0x20 + toff + 32 * k
        m = d[at + 1]
        if m in surfaces:
            if out is None:
                out = bytearray(d)
            out[at + 1] = surfaces[m]
    return d if out is None else bytes(out)


TRACK_SETTINGS_66 = ("CarGlowScale", "Particles", "TintColor", "ToneMapHighBumperExposure", "ToneMapHoodExposure")


def track_settings_64(xml):
    """F&F's TrackSettings.xml (version 66) as FH1's version 64: the five elements FH1's has not
    taken out. FH1 given version 66 lit the world black with the car blown out white at any hour;
    as 64, F&F's own lighting files draw it right (and so did Colorado's settings with them)."""
    if '<TrackSettings Version="64">' in xml:
        return xml
    if '<TrackSettings Version="66">' not in xml:
        raise ValueError("TrackSettings.xml: version 66 expected")
    xml = xml.replace('<TrackSettings Version="66">', '<TrackSettings Version="64">')
    for tag in TRACK_SETTINGS_66:
        xml = re.sub(r"[ \t]*<%s\b[^>]*/>[ \t]*\r?\n" % tag, "", xml)
        xml = re.sub(r"[ \t]*<%s\b[^>]*>.*?</%s>[ \t]*\r?\n" % (tag, tag), "", xml, flags=re.S)
    left = [tag for tag in TRACK_SETTINGS_66 if re.search(r"<%s\b" % tag, xml)]
    if left:    # laid out another way than on its own lines: FH1 would light the world black
        raise ValueError(f"TrackSettings.xml: could not take out {', '.join(left)}")
    return xml


def hold_time_of_day(ribbon_dir):
    """The ribbon's own time-of-day curves held at fm4_pvs.TOD's minutes (13:00), as FM4's circuits'."""
    for name in fm4_pvs.TOD_FILES:
        path = os.path.join(ribbon_dir, name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            xml = f.read()
        if fm4_pvs.TOD not in ("off", "keep") and "<TOD" in xml:
            with open(path, "w", encoding="utf-8") as f:
                f.write(fm4_pvs.hold_time_of_day(xml, float(fm4_pvs.TOD)))


def missing_grading_maps(ff_post, fh1_post, out_dir, tmp):
    """The colour grading maps F&F's post-processing names that FH1's dynamicpost.zip has not
    (amalfi.dds), as mod files."""
    with zipfile.ZipFile(fh1_post) as z:
        have = {i.filename.replace("\\", "/").lower() for i in z.infolist()}
    n = 0
    dst_dir = os.path.join(out_dir, "colourgradingmaps")
    with zipfile.ZipFile(ff_post) as z, open(ff_post, "rb") as fh:
        for i in z.infolist():
            name = i.filename.replace("\\", "/").lower()
            base = name[len("colourgradingmaps/"):]
            if (not name.startswith("colourgradingmaps/") or not base.endswith(".dds") or name in have
                    or not re.fullmatch(r"[a-z0-9_ ]+\.dds", base) or reserved_name(base)):
                continue
            os.makedirs(dst_dir, exist_ok=True)
            with open(os.path.join(dst_dir, base), "wb") as f:
                f.write(fm4_track_mod.member_data(ff_post, z, i, fh, tmp))
            n += 1
    return n


def map_background(fh1_disc, fh2_disc, mod, tmp):
    """The full map's background: FH2's art (the same world coordinates as F&F's; F&F's own tiles
    are FH2's western ones darkened outside its area, its others a developer map), under FH1's
    names, and FH1's map profile with FH2's background size and offset."""
    src = os.path.join(fh2_disc, "media", "ui", "textures", "Horizon.zip")
    z = zipfile.ZipFile(src)
    dst = os.path.join(mod, "media", "ui", "textures", "horizon", "map", "background")
    os.makedirs(dst, exist_ok=True)
    n = 0
    with open(src, "rb") as fh:
        for info in z.infolist():
            name = fh1_tile_name(info.filename)
            if name:
                with open(os.path.join(dst, name), "wb") as f:
                    f.write(fm4_track_mod.member_data(src, z, info, fh, tmp))
                n += 1
    with open(os.path.join(fh1_disc, "media", "ui", "MapProfileFullscreen.xml"), "rb") as f:
        prof = f.read().decode("utf-8-sig")
    a = prof.index('<Group name="background" >')
    b = prof.index("</Group>", a)
    group = prof[a:b].replace('<Size value="11500" />', '<Size value="12000" />')
    group = group.replace('<Offset x="-9583.3" y="0" z="-6900" />', '<Offset x="-7000" y="0" z="-11000" />')
    if group.count('"12000"') != 2 or group.count('"-11000"') != 2:
        raise ValueError("FH1's map profile: the background group is not the one expected")
    os.makedirs(os.path.join(mod, "media", "ui"), exist_ok=True)
    with open(os.path.join(mod, "media", "ui", "MapProfileFullscreen.xml"), "wb") as f:
        f.write((prof[:a] + group + prof[b:]).encode("utf-8"))
    return n


# ---- build ----

RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def reserved_name(part):
    """A Windows device name (CON, NUL, COM1...), with or without an extension. PurePath's
    is_reserved() did this; Python 3.15 removed it."""
    return part.split(".")[0].rstrip(" ").lower() in RESERVED


def copy_member(zip_path, prefix, out_dir, tmp, lower=True):
    """Every member under prefix (case ignored) of a disc archive, into out_dir."""
    root = Path(out_dir).resolve()
    n = 0
    with zipfile.ZipFile(zip_path) as z, open(zip_path, "rb") as fh:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or not name.lower().startswith(prefix.lower()):
                continue
            rel = name[len(prefix):]
            parts = (rel.lower() if lower else rel).split("/")
            if any(part in ("", ".", "..") or ":" in part or
                   part.rstrip(" .") != part or reserved_name(part)
                   for part in parts):
                raise ValueError(f"Unsafe archive member: {info.filename!r}")
            dst = root.joinpath(*parts)
            if not dst.resolve().is_relative_to(root):
                raise ValueError(f"Unsafe archive member: {info.filename!r}")
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(fm4_track_mod.member_data(zip_path, z, info, fh, tmp))
            n += 1
    return n


def build(ff_disc, fh1_disc, update_zip, out, fh2_disc=None):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".fh2ff-build-", dir=out) as work:
        stage = Path(work)
        _build_into(ff_disc, fh1_disc, update_zip, stage, fh2_disc)
        paths = (Path("fm4-tracks") / NAME.lower(), Path("mods") / MOD)
        for path in paths:
            if not (stage / path).is_dir():
                raise ValueError(f"Conversion did not produce {path}")
        _publish(stage, out, paths)
    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    print(f"written: {out} ({total / 1e6:.0f} MB)")


def _publish(stage, out, paths):
    # Serialize replacement of the pair. Keep backups outside the staging directory so
    # they survive if a filesystem error also prevents rollback.
    lock = out / ".fh2ff-publish"
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError(f"{lock} is there: an earlier build was interrupted while it put its output in "
                         "place and could not put the old one back. What it kept of the old output is in "
                         "that folder; move it out (or delete it) and build again.") from None
    backups, installed = [], []
    try:
        for path in paths:
            dst = out / path
            if dst.parent.is_symlink() or dst.parent.resolve() != dst.parent:
                raise ValueError(f"Unsafe output directory: {dst.parent}")
            dst.parent.mkdir(exist_ok=True)
            if os.path.lexists(dst):
                backup = lock / path.parent.name
                dst.rename(backup)
                backups.append((backup, dst))
        for path in paths:
            dst = out / path
            (stage / path).rename(dst)
            installed.append((dst, stage / path))
    except BaseException:
        try:
            for dst, source in reversed(installed):
                dst.rename(source)
            for backup, dst in reversed(backups):
                backup.rename(dst)
            lock.rmdir()
        except OSError as error:
            raise RuntimeError(f"Could not restore the previous import; recovery files are in {lock}") from error
        raise
    shutil.rmtree(lock)


def _build_into(ff_disc, fh1_disc, update_zip, out, fh2_disc):
    src = os.path.join(ff_disc, "media", "tracks", NAME)
    fh2_world = os.path.join(fh2_disc, "media", "tracks", NAME) if fh2_disc else None
    track_dir = os.path.join(out, "fm4-tracks", NAME.lower())
    mod = os.path.join(out, "mods", MOD)
    out_track = os.path.join(track_dir, "media", "tracks", NAME)
    tmp = tempfile.mkdtemp(prefix="fh2ff-")
    resources = ExitStack()
    try:
        w = World(src)
        resources.callback(w.zip.close)
        collobjs = w.remove_closures(fh2_world) if fh2_world else None
        layout, zones, hexd, fmap, lookup = w.convert()
        surfaces = surface_map(surface_names(os.path.join(ff_disc, "media", "physics.zip"), tmp),
                               surface_names(os.path.join(fh1_disc, "media", "physics.zip"), tmp))
        print("collision surfaces FH1 lacks:", surfaces)
        routes = w.route_map()
        for d, _, files in os.walk(src):
            rel = os.path.relpath(d, src)
            for f in files:
                if rel == "." and f.lower() == "bin.zip":
                    continue
                dst = os.path.join(out_track, rel, f)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(d, f), dst)
        # FH1's streaming pool (22,440 blocks) ran dry in FH2's world at its first place: the game
        # links in as many as a track's fm4_stream_blocks.txt asks for (hooks/fm4_track.cpp),
        # up to 65,536. F&F's world measures 52,089 and drove on the game's pool; FH2's 50,234.
        need = w.stream_blocks(hexd)
        blocks = min(STREAM_CAP, int(need * fm4_pvs.STREAM_MARGIN + 0.5))
        with open(os.path.join(out_track, "fm4_stream_blocks.txt"), "w") as f:
            f.write(f"{blocks}\n")
        print(f"streaming blocks: {need} at most, {blocks} asked for")
        rb = os.path.join(out_track, "Ribbon_00")
        for name, data in ((f"{NAME}_00.pvs", layout), (f"{NAME}_00.hex", hexd),
                           ("FilenameMap_00.dat", fmap), ("PVSZLookup_00.dat", lookup)):
            with open(os.path.join(rb, name), "wb") as f:
                f.write(data)
        if collobjs is not None:
            with open(os.path.join(rb, "CollObjs.xml"), "w", encoding="utf-8", newline="") as f:
                f.write(collobjs)
        # the archive: the zones converted, the procedural geometry with dynamic data converted, the
        # soundscapes silent, every other member copied as it is
        wr = TrackZipWriter(os.path.join(out_track, "bin.zip"))
        resources.callback(wr.f.close)
        for info in w.zip.infos:
            low = info.filename.lower()
            if info.filename in zones:
                wr.add(info.filename, zones[info.filename])
            elif low.endswith(".pgeo"):
                d = w.zip.read(info)
                c = convert_pgeo(d, routes)
                if c is d:
                    wr.add_raw(info.filename, info, w.zip.raw(info))
                else:
                    wr.add(info.filename, c)
                    w.report["procedural geometry files with dynamic data converted"] += 1
            elif low.endswith(".fiz") and surfaces:
                d = w.zip.read(info)
                c = remap_fiz(d, surfaces)
                if c is d:
                    wr.add_raw(info.filename, info, w.zip.raw(info))
                else:
                    wr.add(info.filename, c)
                    w.report["collision cells with surfaces FH1 lacks, remapped"] += 1
            elif low.endswith(".soundscape"):
                wr.add(info.filename, SILENT_TILE)
                w.report["soundscapes written silent"] += 1
            else:
                wr.add_raw(info.filename, info, w.zip.raw(info))
        start, size, count = wr.close()
        for k, v in sorted(w.report.items()):
            print(f"  {v:7d}  {k}")
        with open(os.path.join(fh1_disc, "media", "zipmanifest.xml"), "rb") as f:
            manifest = f.read().decode("utf-8-sig")
        line = (f'<Zip version="1" path="game:\\media\\tracks\\{NAME.lower()}\\bin.zip" priority="80" '
                f'dirstart="{start}" dirsize="{size}" direntries="{count}" />')
        with open(os.path.join(track_dir, "media", "zipmanifest.xml"), "wb") as f:
            f.write(b"\xef\xbb\xbf" + manifest.replace("</ZipFiles>", line + "\r\n</ZipFiles>").encode("utf-8"))
        # the road network
        nav_path = os.path.join(out_track, NAME + ".nav")
        with open(nav_path, "rb") as f:
            net = fm4_routes.read_nav(f.read())
        fix_tags(net)
        if fh2_world:
            with open(os.path.join(fh2_world, NAME + ".nav"), "rb") as f:
                print("closed roads retyped:", retype_blocked(net, fm4_routes.read_nav(f.read())))
        with open(nav_path, "wb") as f:
            f.write(fm4_routes.write_nav(net))
        spawn, line, length = spawn_of(net, zone_cells(hexd))
        with open(os.path.join(track_dir, "spawn.txt"), "w") as f:
            f.write(spawn + "\n")
        with open(os.path.join(track_dir, "autopilot.txt"), "w") as f:
            f.write(line)
        print(f"spawn {spawn}; autopilot line {length:.0f} m")
        # audio: Colorado's, whole, as FM4's circuits get it (the world's own banks are FH2's FMOD
        # project: a ribbon file naming a bank that is not there ends the load)
        colorado = os.path.join(fh1_disc, "media", "tracks", "colorado")
        for kind in ("ambience", "reverb", "soundbank_lookup"):
            shutil.copyfile(os.path.join(colorado, f"colorado_{kind}.xml"), os.path.join(out_track, f"{NAME}_{kind}.xml"))
        audio_src = os.path.join(fh1_disc, "media", "audio", "tracks", "colorado")
        audio_dst = os.path.join(track_dir, "media", "audio", "tracks", NAME)
        os.makedirs(os.path.join(audio_dst, "ribbon_00"))
        for n in os.listdir(audio_src):
            if os.path.isfile(os.path.join(audio_src, n)):
                shutil.copyfile(os.path.join(audio_src, n), os.path.join(audio_dst, n))
        shutil.copyfile(os.path.join(audio_src, "Ribbon_00", "ColoradoRibbon_00.xml"),
                        os.path.join(audio_dst, "ribbon_00", f"{NAME}ribbon_00.xml".lower()))
        # lighting: F&F's own (grading lookups, post-processing zones and templates, time of day
        # held as FM4's circuits get it), with its track settings as FH1's version
        ts = os.path.join(out_track, "TrackSettings.xml")
        with open(ts, "rb") as f:
            settings = f.read().decode("utf-8", "replace")
        with open(ts, "w", encoding="utf-8", newline="") as f:
            f.write(track_settings_64(settings))
        hold_time_of_day(rb)
        ff_post = os.path.join(ff_disc, "media", "dynamicpost.zip")
        copy_member(ff_post, f"tracks/{NAME.lower()}/", os.path.join(mod, "media", "dynamicpost", "tracks", NAME.lower()), tmp)
        print("grading maps FH1 lacks:", missing_grading_maps(ff_post, os.path.join(fh1_disc, "media", "dynamicpost.zip"),
                                                               os.path.join(mod, "media", "dynamicpost"), tmp))
        # two files every FH1 ribbon has and this one lacks
        ribbon = os.path.join(colorado, "Ribbon_00")
        have = {n.lower() for n in os.listdir(rb)}
        for theirs, ours in (("Colorado_track_00.crowd", f"{NAME}_track_00.crowd"), ("PreRaceCams.xml", "PreRaceCams.xml")):
            found = next((n for n in os.listdir(ribbon) if n.lower() == theirs.lower()), None)
            if found and ours.lower() not in have:
                shutil.copyfile(os.path.join(ribbon, found), os.path.join(rb, ours))
        # the database and the game modes
        zf, info = fm4_track_mod.find_member(update_zip, "db/gamedb.slt")
        resources.callback(zf.close)
        with open(update_zip, "rb") as fh:
            data = fm4_track_mod.member_data(update_zip, zf, info, fh, tmp)
        db_path = os.path.join(mod, "media", "db", "gamedb.slt")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with open(db_path, "wb") as f:
            f.write(data)
        db = sqlite3.connect(db_path)
        resources.callback(db.close)
        cols = [r[1] for r in db.execute("PRAGMA table_info(Environments)")]
        colorado_row = db.execute("SELECT * FROM Environments WHERE MediaName='Colorado' ORDER BY Id LIMIT 1").fetchone()
        if colorado_row is None:
            raise ValueError(f"{update_zip}: its gamedb.slt has no Colorado environment (not FH1's title update?)")
        row = dict(zip(cols, colorado_row))
        row.update(Id=ENVIRONMENT, MediaName=NAME, ShowOnMap=0)
        db.execute(f"INSERT OR REPLACE INTO Environments ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [row[c] for c in cols])
        db.execute("UPDATE Tracks SET MediaName=?, EnvironmentId=?, RibbonIndex=0 WHERE id=?", (NAME, ENVIRONMENT, FREE_ROAM))
        # the world's own races and playground arenas: their rows and route files in FH1's terms
        routes = world_routes(os.path.join(ff_disc, "media", "db", "gamedb.slt"), rb)
        for r in routes:
            path = route_file(rb, r["RouteId"])
            with open(path, encoding="utf-8-sig", newline="") as f:
                text = f.read()
            new = route_xml(text)
            if new != text:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
        print("races and arenas (Tracks rows by configuration):", add_world_routes(db, routes))
        print("AI lines:", ai_lines(os.path.join(ff_disc, "media", "aiopenworld.zip"), {r["RouteId"] for r in routes},
                                    os.path.join(mod, "media", "aiopenworld", NAME.lower(), "ribbon_00")))
        db.commit()
        gamemodes = os.path.join(mod, "media", "gamemodes", NAME.lower())
        count = copy_member(os.path.join(fh1_disc, "media", "gamemodes.zip"), "colorado/", gamemodes, tmp)
        count += copy_member(update_zip, "gamemodes/colorado/", gamemodes, tmp)
        print(f"{count} game mode files from Colorado")
        if fh2_disc:
            print("map background tiles:", map_background(fh1_disc, fh2_disc, mod, tmp))
        with open(os.path.join(mod, "mod.toml"), "w") as f:
            f.write('name = "Fast & Furious world (from your own copies)"\n'
                    'description = "Made from your own copies of the games: never share it."\n')
    finally:
        try:
            resources.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check(ff_disc):
    with World(os.path.join(ff_disc, "media", "tracks", NAME)) as w:
        return _check(w)


def _check(w):
    P = w.P
    print(f"{NAME}: layout version {P['version']}, {len(P['instances'])} instances, {len(P['models'])} models "
          f"({len(w.model_entry)} with a file), {len(P['textures'])} textures, {len(w.zone_infos)} zones, {len(w.names)} files")
    routes = w.route_map()
    layout, zones, hexd, fmap, lookup = w.convert()
    back = fm4_pvs.read_fh1(layout)
    whole = 0
    for name, data in zones.items():
        r = fm4_pvs.Reader(data)
        fm4_pvs.read_cell(r, back["g2"])
        whole += r.p == len(data)
    pgeo = 0
    for i in w.zip.infos:
        if i.filename.lower().endswith(".pgeo"):
            d = w.zip.read(i)
            c = convert_pgeo(d, routes)
            pgeo += c is not d
    for k, v in sorted(w.report.items()):
        print(f"  {v:7d}  {k}")
    print(f"layout: version {back['version']}, {len(zones)} zones of which {whole} read back whole; "
          f"{len(routes) - 1} route numbers; {pgeo} procedural geometry files with dynamic data")
    return whole == len(zones)


def main(argv):
    args = list(argv[1:])
    fh2 = None
    if "--fh2" in args:
        k = args.index("--fh2")
        if k + 1 >= len(args):
            print(__doc__)
            return 2
        fh2 = args[k + 1]
        del args[k:k + 2]
    if len(args) == 5 and args[0] == "build":
        build(*args[1:], fh2_disc=fh2)
        return 0
    if len(args) == 2 and args[0] == "check":
        return 0 if check(args[1]) else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
