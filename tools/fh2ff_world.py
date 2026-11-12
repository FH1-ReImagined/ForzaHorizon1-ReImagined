#!/usr/bin/env python3
"""Forza Horizon 2 Presents Fast & Furious (Xbox 360): its world, media/tracks/Anthem, as a world
FH1 drives in free roam, from the player's own copies of the games (#821).

A first, command-line step: it writes a track folder for the circuit mount (--fm4_track_dir, as
FM4's circuits use it) and a mod. The launcher's import comes later. What it reads and writes is
the player's own and stays on their PC: never share the output.

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
                             place on a road) and autopilot.txt (a line along that road for the
                             dev autopilot)
    OUT/mods/fh2ff-anthem/   the mod: the database (Environments 70 = Anthem, free roam's Tracks
                             row 317 on it), Colorado's game modes as Anthem's, the lighting
                             templates and, with --fh2, the map's background
  tools/fh2ff_world.py check FF_DISC
      reads F&F's layout, every zone and every procedural geometry file and converts them in
      memory: the converted zones must read back whole

To run it (dev), with the game's own launch arguments and environment:
  --fm4_track_dir=OUT/fm4-tracks/anthem --forza_mods_dir=OUT/mods --forza_mods=fh2ff-anthem
  FORZA_INSECURE_FILES='zipmanifest.xml|\\tracks\\anthem\\'
  FORZA_TRACK_SPAWN=<the line in spawn.txt>  FORZA_TRACK_NO_TRAFFIC=1
  FORZA_AUTOPILOT=<autopilot.txt>,1.0 FORZA_AUTOPILOT_DELAY=6  (drives it)

docs/fh2ff-world.md has what is converted and why, and what is not done yet.
"""
import collections
from contextlib import ExitStack
import math
import os
from pathlib import Path, PureWindowsPath
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
# Types left out of the zones: AnimatedScene still faults after the load when converted (its own
# data differs from FH1's; the PDB's CProceduralAnimatedScene structs are the way to convert it).
PGEO_DROP = {int(k) for k in os.environ.get("FH2FF_PGEO_DROP", str(ANIMATED_SCENE)).split(",") if k}

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

def data_offset(info, fh):
    """A member's data offset: FH1's and FH2's track archives have no local headers, the 0x1123
    extra field of the central directory gives it (as fm4_track_mod.member_data reads it)."""
    ex, p = info.extra, 0
    while p + 4 <= len(ex):
        tag, ln = struct.unpack("<HH", ex[p:p + 4])
        if tag == 0x1123 and ln >= 4:
            return struct.unpack("<I", ex[p + 4:p + 8])[0]
        p += 4 + ln
    fh.seek(info.header_offset)
    h = fh.read(30)
    n, m = struct.unpack("<HH", h[26:30])
    return info.header_offset + 30 + n + m


class Archive:
    def __init__(self, path):
        self.path = path
        self.zf = zipfile.ZipFile(path)
        self.infos = self.zf.infolist()
        self.fh = open(path, "rb")
        self.tmp = tempfile.mkdtemp(prefix="fh2ff-")
        self.by_base = collections.defaultdict(list)
        for i in self.infos:
            self.by_base[i.filename.replace("\\", "/").split("/")[-1].lower()].append(i)

    def read(self, info):
        return fm4_track_mod.member_data(self.path, self.zf, info, self.fh, self.tmp)

    def raw(self, info):
        self.fh.seek(data_offset(info, self.fh))
        return self.fh.read(info.compress_size)

    def close(self):
        self.fh.close()
        self.zf.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


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
    Unchanged without the block."""
    if d[:4] != b"OEGP" or not struct.unpack_from(">I", d, 0x58)[0]:
        return d
    if struct.unpack_from(">I", d, 0x48)[0] != len(d):
        raise ValueError("a procedural geometry file whose m_FileSize is not its size")
    dyn = d[0x60:0x60 + DYN_FH2]
    n = min(dyn[36], 19)
    ids = struct.unpack_from(f">{n}H", dyn, 38)
    new = dyn[:36] + bytes([n]) + bytes(routes.get(r, 0) for r in ids) + bytes(19 - n)
    name_len = struct.unpack_from(">I", d, 0x3C)[0]
    old_name = 0x60 + DYN_FH2
    old_rest = (old_name + name_len + 15) & ~15
    new_rest = (0x60 + DYN_FH1 + name_len + 15) & ~15
    out = bytearray(d[:0x60] + new + d[old_name:old_name + name_len])
    out += bytes(new_rest - len(out))
    out += d[old_rest:]
    struct.pack_into(">I", out, 0x48, len(out))
    return bytes(out)


def closures(ff_xml, fh2_xml):
    """F&F's road closures in its Ribbon_00/CollObjs.xml: the CLOSURES objects FH2 has not at the
    same place (to the metre). Returns (the file with each of them moved to y = -5000, under the
    world, every object kept in its place in the list; their (x, y, z))."""
    def objs(s):
        return [(m.start(4), m.end(4), m.group(2), float(m.group(3)), float(m.group(4)), float(m.group(5)))
                for m in re.finditer(r'<Obj(\d+) PhysicsType="([^"]+)" GraphicsName="[^"]*">\s*'
                                     r'<Pos x="([-\d.e]+)" y="([-\d.e]+)" z="([-\d.e]+)"', s)]
    fh2_at = {(t.lower(), round(x), round(z)) for _, _, t, x, y, z in objs(fh2_xml)}
    gone = [o for o in objs(ff_xml) if CLOSURES.search(o[2]) and (o[2].lower(), round(o[3]), round(o[5])) not in fh2_at]
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
        self.report = collections.Counter()
        self.drop = set()
        self._zone_data = None
        self._kinds = {}
        self._routes = None

    def zones(self):
        if self._zone_data is None:
            self._zone_data = [(i, self.zip.read(i)) for i in self.zone_infos]
        for info, data in self._zone_data:
            r = fm4_pvs.Reader(data)
            c = fm4_pvs.read_cell(r, self.P["g2"], self.P["version"])
            if r.p != len(data):
                raise ValueError(f"{info.filename}: read {r.p} of {len(data)} bytes")
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
            if len(ids) > 254:
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
    line = best[1]
    a, b = line[0], line[1]
    fx, fz = b[0] - a[0], b[2] - a[2]
    n = math.hypot(fx, fz) or 1.0
    return (f"{a[0]:.1f},{a[1] + 0.5:.1f},{a[2]:.1f},{fx / n:.3f},{fz / n:.3f}",
            "".join(f"{x:.2f} {y:.2f} {z:.2f} 20.0\n" for x, y, z in line), best[0])


def fix_tags(net):
    """FH1's names for two of F&F's tag names. FH1's map looks "oneway" up in the table and reads
    through a null pointer when it is not there (TU4 sub_82D98D20)."""
    renamed = {"oneway_forward": "oneway", "traffic_density_name": "traffic_density"}
    net["keys"] = [renamed.get(k, k) for k in net["keys"]]
    if net["keys"] != sorted(set(net["keys"])):   # the game searches the table as a sorted one
        raise ValueError("the road network's tag names are not sorted after renaming")


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
                if new not in vals:
                    vals.append(new)
                net["tags"][ft + j] = (k, vals.index(new))
                changed[new + (" (FH2's)" if votes else " (no FH2 road there)")] += 1
    return dict(changed)


def fh1_tile_name(member):
    """FH2's map tile name (Map/background/high-<row>-<col>.xds) as FH1's (high-<col>-<row>.xds),
    or None for another member."""
    m = re.fullmatch(r"(?i)map/background/(high|low)-(\d+)-(\d+)\.xds", member.replace("\\", "/"))
    return f"{m.group(1).lower()}-{m.group(3)}-{m.group(2)}.xds" if m else None


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
                   part.rstrip(" .") != part or PureWindowsPath(part).is_reserved()
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
    lock.mkdir()
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
        routes = w.route_map()
        for d, _, files in os.walk(src):
            rel = os.path.relpath(d, src)
            for f in files:
                if rel == "." and f.lower() == "bin.zip":
                    continue
                dst = os.path.join(out_track, rel, f)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(os.path.join(d, f), dst)
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
        # lighting: Colorado's, the time of day held as FM4's circuits get it. With F&F's own the
        # scene is black and the car blown out at any hour.
        for f in ("ColorGradingLookup.dds", "ColorGradingLookup_Night.dds", "TrackSettings.xml",
                  os.path.join("Ribbon_00", "PostProcessingZones_Safe.xml")):
            shutil.copyfile(os.path.join(colorado, f), os.path.join(out_track, f))
        fm4_pvs.write_time_of_day(fh1_disc, rb)
        copy_member(os.path.join(fh1_disc, "media", "dynamicpost.zip"), "tracks/colorado/",
                    os.path.join(mod, "media", "dynamicpost", "tracks", NAME.lower()), tmp)
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
        row = dict(zip(cols, db.execute("SELECT * FROM Environments WHERE MediaName='Colorado' ORDER BY Id LIMIT 1").fetchone()))
        row.update(Id=ENVIRONMENT, MediaName=NAME, ShowOnMap=0)
        db.execute(f"INSERT OR REPLACE INTO Environments ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [row[c] for c in cols])
        db.execute("UPDATE Tracks SET MediaName=?, EnvironmentId=?, RibbonIndex=0 WHERE id=?", (NAME, ENVIRONMENT, FREE_ROAM))
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
    w = World(os.path.join(ff_disc, "media", "tracks", NAME))
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
    w.zip.close()
    return whole == len(zones)


def main(argv):
    args = list(argv[1:])
    fh2 = None
    if "--fh2" in args:
        k = args.index("--fh2")
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
