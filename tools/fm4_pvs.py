#!/usr/bin/env python3
"""Forza Motorsport 4 track visibility (PVS version 27) to Forza Horizon 1's (version 50) and
the streaming files FH1 needs with it, from the player's own FM4 files (#648).

Usage:
  tools/fm4_pvs.py convert MOD_DIR FH1_DISC LOOSE_DIR
      MOD_DIR    a mod made by tools/fm4_track_mod.py: its layouts' PVS files are converted
      FH1_DISC   FH1's disc folder (its media/zipmanifest.xml)
      LOOSE_DIR  the loose files to lay over the game folder: the track's archive
                 media/tracks/<track>/bin.zip (FH1 streams a track's zones and models from
                 its own archive, which a mod cannot add) and media/zipmanifest.xml with it
  tools/fm4_pvs.py check FILE.pvs|FILE.pvsz ...   parse FH1 (v50) or FM4 (v27) files, and
                                                  write v50 ones back: must be the same bytes

Both games serialize the PVS with the same engine code; FH1 added fields and moved the world
into streamed zones. The layouts below are from FH1 TU4's reader (sub_82CF2968 and the
functions it calls) and FM4's (sub_8288EAE8 in the disc's default.xex), checked by reading
every PVS of both games back to its last byte.

  FM4 (one file per layout)                   FH1
  header FPVS, 27, u32, u32                   FPVS, 50, u32, u32
  u16 ribbon, u16 a2                          u16 ribbon, u32 pgeo count, bool g1, bool g2,
                                              u32 resource ids, u32 models, u32 list, bool,
                                              u16 a2
  cells (visibility: instances, models,       cells (none in the PVS: one per streamed zone,
    textures seen from it)                      bin\\__RnnZnnnnn.pvsz, with each instance's
                                                placement)
  textures (28 bytes each)                    the same
  shader names                                the same
  instances (80 bytes: model, flags, a        instances (18 bytes); the placement went into
    placement)                                  the zones
  models (textures, shaders, a 4x4            models (the same, a 4x3 matrix and extent)
    matrix, extent)
  always-drawn instances, two model lists     the same, u32 instead of u16, placements after
  dynamic instances                           the same, placements after
  model file prefix ("Suzukaout")             the same
                                              zone table (instances, 0, models per zone)

The track's files move from the mod's bin/ folder into an archive of the layout FH1's track
archives have: stored members, no local headers, the data offset in extra field 0x1123.

A converted layout is split into FH1's 100 m hex zones by where each instance stands
(<track>_NN.hex, a zone file each with its instances, models, textures and the model files to
stream, the filename index FilenameMap_NN.dat and the zone lookup PVSZLookup_NN.dat).

To run it (dev): the loose files over a copy of the game folder, the mod from
tools/fm4_track_mod.py, and
  FORZA_INSECURE_FILES='zipmanifest.xml|\\tracks\\<track>\\'
  FORZA_FM4_STX='<Track>|LOOSE_DIR/<track>_stx_numbers.bin'
  FORZA_TRACK_SPAWN=x,y,z[,fx,fz] (Suzuka's grid: 605.6,598.5,-134.1,0.61,-0.79, 15 m behind
    the start line, FM4's first AI waypoint, media/AI/Tracks/suzuka_00.geo)
  FORZA_FM4_COL=1 (the collision read whole from the converted .col files)
  FORZA_FM4_ZONE_POOL=4 (more live instance objects: the hex zones share few instances)
and FM4_DISC=<FM4's disc folder> when converting, for the surface numbers (tools/fm4_col.py).
Status: Suzuka renders (buildings, stands, crowds, trees, grass, fences, flags, the road,
FM4's horizon landscape; tools/fm4_shaders.py makes the inputs FH1 does not give FM4's
shaders neutral) and drives (collision: tools/fm4_col.py). FM4 has no ground where its sky
dome's lower half stood in, so from above much of the infield is empty.
"""
import math
import re
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_col  # noqa: E402
import fm4_shaders  # noqa: E402

FH1_VERSION = 50
# Forza Horizon 2's version (its Fast & Furious standalone, tools/fh2ff_world.py): the same
# layout, but a placement's route numbers are u16 (FH1's are u8). Read, never written.
FH2_VERSION = 51


class Reader:
    def __init__(self, data):
        self.d, self.p = data, 0

    def take(self, n):
        b = self.d[self.p:self.p + n]
        if len(b) < n:
            raise EOFError(f"wanted {n} bytes at {self.p} of {len(self.d)}")
        self.p += n
        return b

    def u8(self): return self.take(1)[0]
    def u16(self): return struct.unpack(">H", self.take(2))[0]
    def u32(self): return struct.unpack(">I", self.take(4))[0]
    def f32(self): return struct.unpack(">f", self.take(4))[0]
    def floats(self, n): return list(struct.unpack(f">{n}f", self.take(4 * n)))
    def vec(self, fn): return [fn() for _ in range(self.u32())]
    def string(self): return self.take(self.u32()).decode("latin1")


class Writer:
    def __init__(self): self.b = bytearray()
    def u8(self, v): self.b += bytes([v & 0xFF])
    def u16(self, v): self.b += struct.pack(">H", v & 0xFFFF)
    def u32(self, v): self.b += struct.pack(">I", v & 0xFFFFFFFF)
    def f32(self, v): self.b += struct.pack(">f", v)
    def floats(self, vs): self.b += struct.pack(f">{len(vs)}f", *vs)
    def raw(self, v): self.b += v

    def vec(self, items, fn):
        self.u32(len(items))
        for x in items:
            fn(x)

    def string(self, s):
        b = s.encode("latin1")
        self.u32(len(b))
        self.b += b


# ---- FM4, version 27 (sub_8288EAE8) ----

def read_fm4(data):
    r = Reader(data)
    if r.take(4) != b"FPVS":
        raise ValueError("not a PVS file")
    ver = r.u32()
    if ver != 27:
        raise ValueError(f"FM4 PVS version {ver}: only 27 is read")
    P = {"hdr": (r.u32(), r.u32()), "ribbon": r.u16(), "a2": r.u16()}
    P["cells"] = [{"instances": r.vec(r.u16), "models": r.vec(r.u16),
                   "textures": r.vec(r.u32), "texture_flags": r.vec(r.u8)}
                  for _ in range(r.u32())]
    P["textures"] = r.vec(lambda: (r.u32(), r.u32(), r.f32(), r.f32(), r.f32(), r.f32(), r.u32()))
    P["shaders"] = r.vec(r.string)

    def instance():  # sub_82883E38, 80 bytes in memory
        return {"model": r.u16(), "flags": r.u32(), "idx": r.u32(), "u12": r.u32(),
                "v16": r.floats(4), "u32": r.u16(), "lod": (r.u16(), r.u16(), r.u16()),
                "pos": r.floats(3), "rot": [r.u16() for _ in range(9)]}
    P["instances"] = r.vec(instance)

    def model():  # sub_8288A500, 112 bytes
        return {"textures": r.vec(r.u32), "shaders": r.vec(r.u32), "matrix": r.floats(16),
                "extent": r.floats(4)}
    P["models"] = r.vec(model)
    P["always"] = r.vec(r.u16)
    P["models_a"] = r.vec(r.u16)
    P["models_b"] = r.vec(r.u16)
    P["dynamic"] = r.vec(instance)
    P["prefix"] = r.string()
    P["b160"], P["b196"] = r.u8(), r.u8()
    P["names"] = r.vec(r.string)  # version 21 and later
    P["cell_lists"] = r.vec(lambda: r.vec(r.u16))  # version 23 and later
    if r.p != len(data):
        raise ValueError(f"FM4 PVS: read {r.p} of {len(data)} bytes")
    return P


# ---- FH1, version 50 (sub_82CF2968) ----

def read_placement(r, ver=FH1_VERSION):  # sub_82CEDD48
    e = {"lod": (r.u16(), r.u16(), r.u16()), "pos": r.floats(3),
         "rot": [r.u16() for _ in range(9)], "v": r.floats(4), "extra": None}
    if r.u8():
        x = r.u32()
        n = r.u8()
        # FH1: the route numbers as bytes; version 51: a tuple of u16 (map them to bytes before
        # writing, write_placement refuses them)
        routes = r.take(n) if ver < FH2_VERSION else tuple(r.u16() for _ in range(n))
        e["extra"] = (x, routes, r.take(32))  # sub_82CECFC0
    return e


def write_placement(w, e):
    for v in e["lod"]:
        w.u16(v)
    w.floats(e["pos"])
    for v in e["rot"]:
        w.u16(v)
    w.floats(e["v"])
    if e["extra"] is None:
        w.u8(0)
    else:
        x, lst, tail = e["extra"]
        if not isinstance(lst, (bytes, bytearray)):
            raise ValueError("a placement with 16-bit route numbers (version 51): map them to bytes first")
        w.u8(1)
        w.u32(x)
        w.u8(len(lst))
        w.raw(lst)
        w.raw(tail)


def read_rec16(r):  # sub_82CF0F48 with g1: stored as +12, +0, +4, +8
    return r.u32(), r.u32(), r.u32(), r.u32()


def read_cell(r, g2, ver=FH1_VERSION, placement=read_placement):
    # sub_82CF2168: a zone (.pvsz) or a cell of the PVS. placement: what reads one placement
    # (a world's every zone held as dicts is gigabytes: fh2ff_world keeps their bytes)
    c = {"instances": r.vec(r.u32)}
    if g2:
        c["models2"] = r.vec(r.u16)
        c["files2"] = r.vec(lambda: read_rec16(r))
        c["textures2"] = r.vec(r.u32)
        c["texture_flags2"] = r.vec(r.u8)
    c["models"] = r.vec(r.u16)
    c["textures"] = r.vec(r.u32)
    c["texture_flags"] = r.vec(r.u8)
    c["files"] = r.vec(lambda: read_rec16(r))
    c["drawn"] = r.vec(r.u8)
    c["placements"] = [placement(r, ver) for _ in range(r.u32())]
    return c


def write_cell(w, c, g2):
    w.vec(c["instances"], w.u32)
    if g2:
        w.vec(c["models2"], w.u16)
        w.vec(c["files2"], lambda x: [w.u32(v) for v in x])
        w.vec(c["textures2"], w.u32)
        w.vec(c["texture_flags2"], w.u8)
    w.vec(c["models"], w.u16)
    w.vec(c["textures"], w.u32)
    w.vec(c["texture_flags"], w.u8)
    w.vec(c["files"], lambda x: [w.u32(v) for v in x])
    w.vec(c["drawn"], w.u8)
    w.u32(len(c["placements"]))
    for e in c["placements"]:
        write_placement(w, e)


def read_fh1_instance(r):  # sub_82CEE8E0: +14, +16 (v50), +0, +4, +8, +12
    return {"model": r.u16(), "u16": r.u16(), "flags": r.u32(), "idx": r.u32(),
            "u8": r.u32(), "u12": r.u16()}


def write_fh1_instance(w, i):
    w.u16(i["model"]); w.u16(i["u16"]); w.u32(i["flags"]); w.u32(i["idx"])
    w.u32(i["u8"]); w.u16(i["u12"])


def read_fh1(data):
    r = Reader(data)
    if r.take(4) != b"FPVS":
        raise ValueError("not a PVS file")
    ver = r.u32()
    if ver not in (FH1_VERSION, FH2_VERSION):
        raise ValueError(f"FH1 PVS version {ver}: only 50 (and FH2's 51) are read")
    P = {"version": ver, "hdr": (r.u32(), r.u32()), "ribbon": r.u16(), "pgeo": r.u32(), "g1": r.u8(),
         "g2": r.u8(), "resources": r.u32(), "rmodels": r.u32(), "list48": r.vec(r.u32),
         "g3": r.u8(), "a2": r.u16()}
    if not P["g1"]:
        raise ValueError("g1 = 0 (the other 16-byte record layout) is not read")
    P["cells"] = [read_cell(r, P["g2"], ver) for _ in range(r.u32())]
    P["textures"] = r.vec(lambda: (r.u32(), r.u32(), r.f32(), r.f32(), r.f32(), r.f32(), r.u32()))
    P["shaders"] = r.vec(r.string)
    P["instances"] = r.vec(lambda: read_fh1_instance(r))
    P["models"] = r.vec(lambda: {"textures": r.vec(r.u32), "shaders": r.vec(r.u32),
                                 "matrix": r.floats(12), "extent": r.floats(3)})
    P["always"] = r.vec(r.u32)
    P["models_a"] = r.vec(r.u16)
    P["models_b"] = r.vec(r.u16)
    P["always_placements"] = [read_placement(r, ver) for i in P["always"] if i != 0xFFFFFFFF]
    P["dynamic"] = r.vec(lambda: read_fh1_instance(r))
    P["dynamic_placements"] = [read_placement(r, ver) for _ in P["dynamic"]]
    P["prefix"] = r.string()
    P["b184"], P["b216"] = r.u8(), r.u8()
    P["names"] = r.vec(r.string)
    P["pairs"] = r.vec(lambda: r.take(8))  # sub_82CF0218; none in the files seen
    P["b228"] = r.u8()
    P["zones"] = r.vec(lambda: (r.u32(), r.u32(), r.u32()))
    if r.p != len(data):
        raise ValueError(f"FH1 PVS: read {r.p} of {len(data)} bytes")
    return P


def write_fh1(P):
    w = Writer()
    w.raw(b"FPVS"); w.u32(FH1_VERSION); w.u32(P["hdr"][0]); w.u32(P["hdr"][1])
    w.u16(P["ribbon"]); w.u32(P["pgeo"]); w.u8(P["g1"]); w.u8(P["g2"])
    w.u32(P["resources"]); w.u32(P["rmodels"]); w.vec(P["list48"], w.u32)
    w.u8(P["g3"]); w.u16(P["a2"])
    w.u32(len(P["cells"]))
    for c in P["cells"]:
        write_cell(w, c, P["g2"])
    w.vec(P["textures"], lambda t: (w.u32(t[0]), w.u32(t[1]), w.floats(t[2:6]), w.u32(t[6])))
    w.vec(P["shaders"], w.string)
    w.vec(P["instances"], lambda i: write_fh1_instance(w, i))
    w.vec(P["models"], lambda m: (w.vec(m["textures"], w.u32), w.vec(m["shaders"], w.u32),
                                  w.floats(m["matrix"]), w.floats(m["extent"])))
    w.vec(P["always"], w.u32)
    w.vec(P["models_a"], w.u16)
    w.vec(P["models_b"], w.u16)
    for e in P["always_placements"]:
        write_placement(w, e)
    w.vec(P["dynamic"], lambda i: write_fh1_instance(w, i))
    for e in P["dynamic_placements"]:
        write_placement(w, e)
    w.string(P["prefix"])
    w.u8(P["b184"]); w.u8(P["b216"])
    w.vec(P["names"], w.string)
    w.vec(P["pairs"], w.raw)
    w.u8(P["b228"])
    w.vec(P["zones"], lambda z: [w.u32(v) for v in z])
    return bytes(w.b)


# ---- FM4 to FH1 ----

def half(v):
    return struct.unpack(">e", struct.pack(">H", v))[0]


def placement_of(inst):
    return {"lod": inst["lod"], "pos": inst["pos"], "rot": inst["rot"], "v": inst["v16"],
            "extra": None}


LIGHTMAPS = os.environ.get("FM4_PVS_LIGHTMAPS", "1") == "1"
# FM4's instance flags (0x0FFFxxxx) in FH1's (0x083Fxxxx mostly): see fh1_flags.
# FM4_PVS_FLAGS=keep keeps FM4's, a number gives every instance that value;
# FM4_PVS_FLAGMASK the FM4 bits kept (a test).
FLAGS = os.environ.get("FM4_PVS_FLAGS", "map")
FLAG_LOW_MASK = int(os.environ.get("FM4_PVS_FLAGMASK", "0xFFFF"), 0)


def fh1_flags(f):
    """FM4's instance flags in FH1's layout. Bits 16-21 are set in every instance of both
    games. FM4 also sets bits 22-27 in all of its instances; FH1 never sets 22, 23 and 26,
    sets 24 and 25 in 2% (roads) and 27 in 77% of Colorado's, and with FM4's high bits it
    draws nothing, so the high half is FH1's most common, 0x083F. FM4's bit 28 (60% of its
    blend surfaces: road edges and lines) makes FH1's GPU read past its buffers (a fault in
    the command processor while the track loads), so it is dropped. The low 16 bits are
    kept: the same bits are in use in both games (2, 3, 4, 9, 11-13 together, 14), and
    FM4's bit 5 (every road piece; FH1 has none) is harmless."""
    return 0x083F0000 | (f & FLAG_LOW_MASK)


def fh1_instance_of(inst):
    # idx/u12: an instance's own texture (FM4's baked lightmaps) and the model's texture slot
    # it replaces (FM4_PVS_LIGHTMAPS=0 drops them).
    idx, slot = (inst["idx"], inst["u12"]) if LIGHTMAPS else (0, 0xFFFFFFFF)
    flags = (inst["flags"] if FLAGS == "keep" else fh1_flags(inst["flags"]) if FLAGS == "map"
             else int(FLAGS, 0))
    return {"model": inst["model"], "u16": 0xFFFF, "flags": flags, "idx": idx,
            "u8": slot, "u12": inst["u32"]}


# FM4_PVS_SHADERS=part:shader,... replaces the track shaders whose name holds part (a test).
SHADER_MAP = [tuple(x.split(":", 1)) for x in os.environ.get("FM4_PVS_SHADERS", "").split(",")
              if ":" in x]


def shader_of(name):
    for part, repl in SHADER_MAP:
        if part in name.split("\\")[-1]:
            return name.rsplit("\\", 1)[0] + "\\" + repl
    return name


# FM4_PVS_PROJLM=1 keeps FM4's flag 0x10 (a test): see fh1_texture_of.
PROJECTED_LIGHTMAP = os.environ.get("FM4_PVS_PROJLM", "0") == "1"


# FM4_PVS_EXTENT=n: every model's extent n m in each axis (a culling test).
EXTENT = os.environ.get("FM4_PVS_EXTENT")


def extent_of(m):
    return [float(EXTENT)] * 3 if EXTENT else m["extent"][:3]


def fh1_texture_of(t):
    """FM4 packs many textures into one file (an atlas: the record's file index and UV
    rectangle); FH1's records have flag 0x100 and never FM4's 0x8. FH1 takes the first
    record with a flag in 0xF0 as the track's projected lightmap (sub_82E23130 loads it at
    once into its own slot). FM4 sets 0x10 on one texture, shared by every road piece; kept,
    the road draws flat dark grey, so the flag is dropped."""
    drop = 0x8 if PROJECTED_LIGHTMAP else 0xF8
    return t[:6] + ((t[6] & ~drop) | 0x100,)


def convert(F, files, fits=None):
    """FM4's PVS (read_fm4) to FH1's PVS, its zones (what each holds: ZONE_REACH) and the
    grid. fits(zones, grid), when given, says whether zones fit the game's streaming pool;
    when the neighbours' reach does not, a shorter one is tried (NEIGHBOUR_NEAR), then the
    zones' own instances alone."""
    insts = F["instances"]
    P = {"hdr": (F["hdr"][0], 0), "ribbon": F["ribbon"], "pgeo": 0, "g1": 1, "g2": 0,
         # The size of FH1's resource table: zone file records and texture files take ids in
         # it; too small, the loader writes past it (into the texture table).
         "resources": len(files) + len(F["textures"]) + 64, "rmodels": len(F["models"]), "list48": [], "g3": 0, "a2": 0x32,
         "cells": [], "textures": [fh1_texture_of(t) for t in F["textures"]],
         "shaders": [shader_of(sh) for sh in F["shaders"]],
         "instances": [fh1_instance_of(i) for i in insts],
         "models": [{"textures": m["textures"], "shaders": m["shaders"],
                     "matrix": [m["matrix"][r * 4 + c] for r in range(4) for c in range(3)],
                     "extent": extent_of(m)} for m in F["models"]],
         # FM4's always-drawn instances and the two model lists are its sky domes (one per
         # time of day); FH1's loader waits forever on them there. The first dome goes into
         # the zones instead.
         "always": [], "models_a": [], "models_b": [],
         "dynamic": [fh1_instance_of(i) for i in F["dynamic"]],
         "prefix": F["prefix"], "b184": F["b160"], "b216": F["b196"], "names": F["names"],
         "pairs": [], "b228": 1}
    P["always_placements"] = []
    P["dynamic_placements"] = [placement_of(i) for i in F["dynamic"]]
    # Every instance of FM4's cells, placed in FH1's hex zones by where it is: its own
    # position, or for a mesh placed by its model (FM4's instances at 0,0,0) the model's.
    used = sorted({i for c in F["cells"] for i in c["instances"]})
    # A mesh placed by its model (the road, the terrain) has its vertices and its model's
    # matrix in the mirror of the world (z negated), and its placement turns it round
    # (FM4's rotation 1, 1, -1, which FH1 applies too). Its zones are those of where it is
    # drawn: the model's centre through that rotation. Zoned by the centre as stored, the
    # road near the camera stayed in the zones of its mirror and was never loaded.
    def where(i):
        pos = insts[i]["pos"]
        if any(pos):
            return pos[0], pos[2]
        m = F["models"][insts[i]["model"]]["matrix"]
        rot = [half(v) for v in insts[i]["rot"]]
        c = m[12:15]
        return (sum(c[k] * rot[3 * k] for k in range(3)),
                sum(c[k] * rot[3 * k + 2] for k in range(3)))
    spots = {i: where(i) for i in used}
    grid = HexGrid([spots[i] for i in used], HEX_RADIUS)
    # An instance goes into every zone its model's extent reaches (FH1 counts the zones that
    # hold an instance): a 400 m piece of road shows from all along it, not only from the
    # hex of its centre.
    members = {cell: [] for cell in grid.cells}
    for i in used:
        e = F["models"][insts[i]["model"]]["extent"]
        reach = (e[0] ** 2 + e[2] ** 2) ** 0.5
        for cell in grid.cells_near(*spots[i], reach):
            members[cell].append(i)
    # And everything FM4 sees from there: FM4's cells are visibility sets (650 to 870 of
    # Suzuka's 3349 instances each, in order round the lap) without a position. A zone takes
    # the cells that hold the most of the instances standing near its centre (VISIBLE_NEAR
    # m, twice that if none). By position alone some far pieces (the tree line past the
    # Esses, ground beyond a crest) were in no live zone.
    if VISIBLE:
        sets = [set(c["instances"]) for c in F["cells"]]
        for cell in grid.cells:
            cx, cz = grid.centre(*cell)
            for r in (VISIBLE_NEAR, 2 * VISIBLE_NEAR):
                near = [i for i in used
                        if (spots[i][0] - cx) ** 2 + (spots[i][1] - cz) ** 2 < r * r]
                if near:
                    break
            if not near:
                continue
            # The cell holding the most of them (the smaller first on a tie); of what it
            # sees, only the large pieces (VISIBLE_SIZE m and over: ground, road, rows of
            # trees). With everything over 25 m (zones of up to 445 instances, 524 textures)
            # the road under the car drew flat black, ground near it went missing and kerbs
            # and verges drew cyan, green or blue, differently from run to run.
            ranked = sorted(range(len(sets)),
                            key=lambda k: (-sum(i in sets[k] for i in near), len(sets[k])))
            seen = set(members[cell])
            for k in ranked[:VISIBLE_CELLS]:
                for i in sorted(sets[k] - seen):
                    if max(F["models"][insts[i]["model"]]["extent"][:3]) < VISIBLE_SIZE:
                        continue
                    members[cell].append(i)
                    seen.add(i)
    # The sky dome (FM4's first always-drawn instance) as an object of every zone: FH1 keeps
    # its own sky above, the dome adds FM4's landscape round the horizon. Its model's extent
    # is FM4's 0; FH1 gets the dome's size instead.
    everywhere = []
    sky = [i for i in F["always"] if i not in (0xFFFF, 0xFFFFFFFF)][:1] if SKY else []
    for i in sky:
        P["models"][insts[i]["model"]]["extent"] = [SKY_EXTENT] * 3
        everywhere.append(i)
    # A ground under everything (see GROUND): one flat terrain piece of FM4's own, placed
    # again stretched GROUND_SCALE times across, below the lowest ground, in every zone.
    if GROUND:
        g = ground_instance(F, used)
        if g is not None:
            if GROUND_LAYER in ("A", "B", "copy"):
                g["model"] = ground_model_copy(F, P, files, g["model"], g["reach"])
            insts = insts + [g]
            P["instances"].append(fh1_instance_of(g))
            everywhere.append(len(insts) - 1)
    # What each zone holds (ZONE_REACH): FH1 draws the camera's zone alone, so a zone holds
    # what is seen from it, its own instances and its six neighbours'. Where that needs more
    # streaming blocks than the game has, the neighbours' instances within NEIGHBOUR_NEAR m
    # of the zone's centre (their extent's reach added, as cells_near), else its own alone.
    def reach_of(i):
        e = F["models"][insts[i]["model"]]["extent"]
        return (e[0] ** 2 + e[2] ** 2) ** 0.5
    zones = None
    for near in ([None] + list(NEIGHBOUR_NEAR) if ZONE_REACH == "neighbours" else []):
        zones = zone_records(F, insts, grid, with_neighbours(grid, members, near, spots, reach_of),
                             everywhere)
        if fits is None or fits(zones, grid):
            P["zone_reach"] = ("their own and their neighbours' instances" if near is None else
                               f"their own and their neighbours' instances within {near:.0f} m "
                               f"(all of them: over the game's {MAX_STREAM_BLOCKS} streaming blocks)")
            break
        zones = None
    if zones is None:
        P["zone_reach"] = "their own instances" + (
            f" (with their neighbours': over the game's {MAX_STREAM_BLOCKS} streaming blocks)"
            if ZONE_REACH == "neighbours" else "")
        zones = zone_records(F, insts, grid, members, everywhere)
    P["zones"] = [(len(z["instances"]), 0, len(z["models"])) for z in zones]
    # FH1 sizes its pools of live instance objects and placements from the largest zone in
    # this table: 3 x (its instances + 128) + the dynamic ones (sub_82E296C0), for Colorado's
    # zones that each hold everything visible from them. Up to 14 of these hex zones are live
    # at once and they share few instances, so the pools ran dry past Degner (1540 objects
    # and 1133 placements in use where they held 1079). A last table entry no hex points at,
    # never streamed, makes them POOL_ZONES times the largest zone.
    if POOL_ZONES:
        biggest = max(z[0] for z in P["zones"])
        P["zones"].append((POOL_ZONES * biggest, 0, 0))
    return P, zones, grid


def with_neighbours(grid, members, near=None, spots=None, reach=None):
    """Each cell's members, then those of its six neighbours it does not hold yet: all of them,
    or (near, m) those whose spot is within near + reach(i) of the cell's centre."""
    out = {}
    for cell in grid.cells:
        zi, seen = list(members[cell]), set(members[cell])
        cx, cz = grid.centre(*cell)
        for n in grid.neighbours(*cell):
            for i in members[n]:
                if i in seen:
                    continue
                if near is not None:
                    x, z = spots[i]
                    if (x - cx) ** 2 + (z - cz) ** 2 > (near + reach(i)) ** 2:
                        continue
                seen.add(i)
                zi.append(i)
        out[cell] = zi
    return out


def zone_records(F, insts, grid, members, everywhere=()):
    """The zones of the grid's cells: each cell's members, then the instances in every zone."""
    zones = []
    for cell in grid.cells:
        zi = list(members[cell]) + list(everywhere)
        models = sorted({insts[i]["model"] for i in zi})
        tex = sorted({t & 0xFFFFFF for m in models for t in F["models"][m]["textures"]} |
                     ({insts[i]["idx"] & 0xFFFFFF for i in zi if insts[i]["u12"] != 0xFFFFFFFF}
                      if LIGHTMAPS else set()))
        zones.append({"instances": [0x80000000 | i for i in zi], "models": models,
                      "textures": tex, "texture_flags": [ZONE_TEXTURE_FLAG] * len(tex), "files": [],
                      "drawn": [1] * len(zi), "placements": [placement_of(insts[i]) for i in zi]})
    return zones


# FM4_PVS_GROUND=0 leaves out the ground under the circuit. FM4 has none: where its sky
# dome's lower half stood in, FH1 showed holes past crests and edges (the dome's horizon band
# or grey). The ground is FM4's flattest grass piece (terr_blnd, 50 m or more across), placed
# again GROUND_SCALE times wider and GROUND_BELOW m under the lowest ground, centred on the
# circuit; FM4_PVS_GROUND_MODEL=n picks the model instead.
GROUND = os.environ.get("FM4_PVS_GROUND", "1") == "1"
GROUND_SCALE = float(os.environ.get("FM4_PVS_GROUND_SCALE", "15"))
GROUND_BELOW = float(os.environ.get("FM4_PVS_GROUND_BELOW", "4"))
# The ground's layers: a terrain piece blends its two layers (Blend_A, tinted by the
# material's c0, and Blend_B) by the green channel of a mask texture of its own, and
# stretched 15 times the ground took one corner of that mask everywhere: pale sand and
# patches where it fills the holes past the circuit's edges. The ground is drawn instead with
# a copy of its model (<prefix>.NNNNN.rmb.bin, NNNNN the next model number) whose blend shader
# is a copy too (its name's last letter made "g": terr_blnd_spec_g) with the mask read as 0
# (FM4_PVS_GROUND_LAYER=A) or 1 (B): one layer everywhere. "off" keeps the piece as it is.
# (Pointing the mask at another texture, Suzuka's 621, left a hole at the grid's verge.)
GROUND_LAYER = os.environ.get("FM4_PVS_GROUND_LAYER", "off")
GROUND_COPIES = {}  # new file in the track's bin folder: (the file it is a copy of, renames)


# FH1 streams a zone's model files into a pool of 1028-byte blocks (22440 of them, 23 MB),
# and holds the camera's zone and the six around it. A converted circuit's zones hold more
# than Colorado's: the pool runs dry. The game (forza-recomp/src/hooks/fm4_track.cpp) adds
# blocks when the track starts streaming, as many as media/tracks/<track>/fm4_stream_blocks.txt
# asks for: the largest union of a zone's and its six neighbours' model files (in KB, the
# blocks' payload), STREAM_MARGIN times over. For Suzuka the union came within 2% of the
# blocks in use measured on a lap (24454 for 24037 at the 120 m visible set, 34225 for 34518
# at 25 m).
STREAM_MARGIN = float(os.environ.get("FM4_PVS_STREAM_MARGIN", "1.25"))
# The most blocks the game links in (kMaxBlocks in fm4_track.cpp: 64 MB in all).
MAX_STREAM_BLOCKS = 65536
# A test (FM4_PVS_ZONE_TEXTURES=1): a zone's file list holds its textures' own files too, as
# Colorado's do (a streamed record's object holds the archive index of its _0x<id>.bix as its
# resource id). It changed nothing at Sunset Infield (FH1 requests a streamed texture itself):
# the textures left on the pack's resident mips were short of memory instead
# (hooks/fm4_track.cpp, sub_825CB038). Listing them also needs about three times the pool.
ZONE_TEXTURES = os.environ.get("FM4_PVS_ZONE_TEXTURES", "0") == "1"
# The flag byte each zone gives its textures (Colorado's zones hold 0 for 88% of them, 1 for
# the rest). With 0 FH1 streamed only part of a zone's textures in: at Sunset Infield's spot 2,
# 112 of them were asked for every frame and never loaded (flat grass, black billboards, the
# road on the pack's resident mips); with 1 every one loads (699 textures, 1 left). 1 for all:
# FM4's zones hold what is seen from them. FM4_PVS_ZONE_TEXTURE_FLAG=0 as before.
ZONE_TEXTURE_FLAG = int(os.environ.get("FM4_PVS_ZONE_TEXTURE_FLAG", "1"))
# Which of a texture's files: "mips" its _0x<id>.bix alone (mip 1 down), "all" with its
# _0x<id>_B.bix (the base level) too.
ZONE_TEXTURE_FILES = os.environ.get("FM4_PVS_ZONE_TEXTURE_FILES", "mips")


def texture_files(own):
    """A streamed texture's own files a zone lists (ZONE_TEXTURE_FILES)."""
    return ((f"_0x{own:08X}.bix", f"_0x{own:08X}_B.bix") if ZONE_TEXTURE_FILES == "all"
            else (f"_0x{own:08X}.bix",))


def atlas_mask_shaders(F):
    """The blend shaders (upper-case names) most of whose models have a texture record from
    an FM4 atlas (flag 0x8) that is not a lightmap (0x4): fm4_shaders.ATLAS_MASK_SHADERS. The
    patch is the shader's, shared by every model drawn with it: on a shader most of whose
    models have a plain mask it would take the blend from more models than it gives back
    (Suzuka: 1 of 66 RDEDG, 9 of 34 SHLDR_BLND_SKIDSPEC; Laguna Seca: 58 of 104
    ROAD_BLND_TRILIN_2, 58 of 98 RDLINE)."""
    count = {}
    for M in F["models"]:
        names = {F["shaders"][s].split("\\")[-1].upper() for s in M["shaders"]}
        atlas = any(F["textures"][t & 0xFFFFFF][6] & 0xC == 0x8 for t in M["textures"])
        for n in names:
            if "BLND" in n:
                c = count.setdefault(n, [0, 0])
                c[atlas] += 1
    return {n for n, (plain, atlas) in count.items() if atlas > plain}


# A PM4 type-0 write of pixel-shader constants (registers 0x4400 + 4 * n): header word
# (count - 1) << 16 | register, count a whole number of registers.
_PS_WRITE = re.compile(rb"(?=\x00([\x00-\xff])([\x44-\x47])([\x00-\xff]))", re.S)


def writes_ps_constant(data, reg):
    """Whether a model file holds a PM4 write of pixel constant reg (any alignment)."""
    for m in _PS_WRITE.finditer(data):
        cnt = m.group(1)[0] + 1
        low = (m.group(2)[0] << 8) | m.group(3)[0]
        if low & 3 or cnt % 4:
            continue
        first = (low - 0x4400) // 4
        if first % 4:  # FM4's constant blocks start on 4-register boundaries (c0, c116, c120)
            continue
        if first <= reg < first + cnt // 4:
            return True
    return False


def lightmap_uv_models(F, bin_dir):
    """The models of F whose shaders read Light_Map_uvOS at c116 and whose files write no
    pixel constant c116 (see <track>_stx_lmuv.bin)."""
    shader_files = {}
    sdir = os.path.join(bin_dir, "shaders", "track")
    if os.path.isdir(sdir):
        shader_files = {n.upper(): os.path.join(sdir, n) for n in os.listdir(sdir)}
    reads = {}

    def reads_lmuv(name):
        if name not in reads:
            p = shader_files.get(name.upper() + ".FXOBJ")
            reads[name] = bool(p) and any(
                floats.get("Light_Map_uvOS", (0,))[0] == 116
                for _, _, _, floats, _ in fm4_shaders.blobs(open(p, "rb").read()))
        return reads[name]
    out = []
    for mi, M in enumerate(F["models"]):
        names = [F["shaders"][s].split("\\")[-1] for s in M["shaders"]]
        if not any(reads_lmuv(n) for n in names):
            continue
        p = os.path.join(bin_dir, f"{F['prefix']}.{mi:05d}.rmb.bin")
        if os.path.exists(p) and not writes_ps_constant(open(p, "rb").read(), 116):
            out.append(mi)
    return out


# Tests, both off: they do not fix the five leftovers (docs/festival_track_spike.md, "The
# shader copies"). The draws of the models lightmap_uv_models lists have no texture at their
# lightmap sampler (s7), so whatever rectangle they read, they read another draw's texture.
#
# FM4_PVS_LMUV_IDENTITY=1: models whose instances' lightmaps all have a file of their own (a
# streamed tile: the tile alone once in) and whose command buffers never set Light_Map_uvOS are
# drawn with copies of their shaders that read it as the identity
# (fm4_shaders.lightmap_uv_identity). The copy's name is the shader's with its last letter "l"
# (or the next letter no shader has; the same length: the model files name their shaders).
# FM4_PVS_LMUV_ALL=1 gives every listed model these copies.
LMUV_IDENTITY = os.environ.get("FM4_PVS_LMUV_IDENTITY", "0") == "1"
LMUV_COPIES = {}  # copy in the track's bin folder: the shader file it is a copy of


# FM4_PVS_LMUV_RECT=1: the listed models with an instance lightmap that is an atlas tile:
# their command buffers' write of c120..c127 (Diffuse_Texture_uvOS at c120, the rest unread by
# their shaders) gets the instance's rectangle in a free register of it, the lowest no fixup
# sets either (offset u, v, scale u, v; the identity for a lightmap with a file of its own,
# FM4_PVS_LMUV_OWN=rect the record's rectangle there too), and their shaders' copies read the
# lightmap's offset and scale from there (fm4_shaders.lightmap_uv_register). A model whose
# instances have different rectangles is drawn by one copy of its model file per rectangle
# (new model numbers, the same in every ribbon), each instance by its own.
LMUV_RECT = os.environ.get("FM4_PVS_LMUV_RECT", "0") == "1"
# The listed models' draws have no texture at their lightmap sampler (s7): their materials map
# sampler 7 to no slot and their command buffers write no fetch constant 7 (FM4 binds the
# instance's lightmap itself). With the rectangles above, each one's map sends a fetch constant
# its command buffers do write but no shader of the model samples to the lightmap's slot, its
# shader copies fetch the lightmap from there, and each instance's copy of the model has its own
# lightmap record in that slot (Hockenheim 7 and 8: the flat red run-off; Tsukuba 9: the white
# haze). Models whose command buffers write only fetch constants their shaders sample (the road
# shaders with a normal map at Infineon, Indianapolis, Top Gear) get a fetch constant of their
# own added to every pass's command buffer instead (insert_fetch_constant; FM4_PVS_LMUV_INSERT=0
# leaves them). FM4_PVS_LMUV_BIND=0 leaves them all.
LMUV_BIND = os.environ.get("FM4_PVS_LMUV_BIND", "1") == "1"
LMUV_INSERT = os.environ.get("FM4_PVS_LMUV_INSERT", "1") == "1"
INSERTED = set()  # models given a fetch constant by insert_fetch_constant
# A model whose instances name more lightmap records than this is left: each record takes a copy
# of the model file, and the copies are streamed as files of their own (with every one bound,
# Laguna Seca and Catalunya asked for 107000 and 104000 streaming blocks, over the game's cap
# of 65536, and Catalunya's pool ran dry at spot 5). FM4_PVS_LMUV_BIND_MAX=n.
LMUV_BIND_MAX = int(os.environ.get("FM4_PVS_LMUV_BIND_MAX", "4"))
LMUV_RECT = LMUV_RECT or LMUV_BIND
RECT_COPIES = {}  # shader copy in the track's bin folder: (the shader file, the register)
IDENTITY_RECT = (0.0, 0.0, 1.0, 1.0)
OWN_RECT = os.environ.get("FM4_PVS_LMUV_OWN", "identity") == "rect"


def _rect_copy(data, reg, fc):
    data = fm4_shaders.lightmap_uv_register(data, reg)[0]
    return fm4_shaders.lightmap_fetch_constant(data, fc)[0] if fc is not None else data


def _c120_writes(data):
    """(offset of the data, registers) of each PM4 write of PS constants from c120."""
    out = []
    for m in _PS_WRITE.finditer(data):
        cnt = m.group(1)[0] + 1
        low = (m.group(2)[0] << 8) | m.group(3)[0]
        if not low & 3 and not cnt % 4 and (low - 0x4400) // 4 == 120:
            out.append((m.start() + 4, cnt // 4))
    return out


# A model's material holds its sampler map before the shader names: u32 1, u32 8, then per
# sampler register 0 to 7 the model texture slot it reads (-1 none, -2 not used), then u32 1,
# u32 the shader count (Hockenheim's road model 257: 0 1 3 4 -2 -2 -2 2, its lightmap at slot 2;
# the listed 269: the same with -1 for sampler 7, though slot 2 holds its instance's lightmap).
# FM4_PVS_LMUV_MAP=slot (a test) points a listed model's sampler 7 at the slot holding its
# instances' lightmap record; =count at the slot after the model's own (where FH1's table keeps
# the instance's lightmap).
LMUV_MAP = os.environ.get("FM4_PVS_LMUV_MAP", "")
_SHADER_NAME = re.compile(rb"\x00\x00\x00[\x01-\x7f]shaders\\track\\", re.I)


def lightmap_sampler_map(F, files, bin_dir):
    """Point the listed models' sampler 7 at their lightmap (see LMUV_MAP); the number done."""
    if LMUV_MAP not in ("slot", "count"):
        return 0
    T = F["textures"]
    records = {}
    for inst in F["instances"]:
        if inst["u12"] != 0xFFFFFFFF:
            records.setdefault(inst["model"], set()).add(inst["idx"] & 0xFFFFFF)
    done = 0
    for m in lightmap_uv_models(F, bin_dir):
        p = os.path.join(bin_dir, f"{F['prefix']}.{m:05d}.rmb.bin")
        tex = [t & 0xFFFFFF for t in F["models"][m]["textures"]]
        recs = records.get(m, set())
        if LMUV_MAP == "slot":
            slots = {k for k, t in enumerate(tex) if t in recs}
            if len(slots) != 1:
                continue
            value = slots.pop()
        else:
            value = len(tex)
        data = bytearray(open(p, "rb").read())
        hit = _SHADER_NAME.search(bytes(data))
        if not hit:
            continue
        i = hit.start()
        head = [struct.unpack_from(">i", data, i - 4 * k)[0] for k in range(12, 0, -1)]
        if head[0] != 1 or head[1] != 8 or head[-2] != 1 or head[9] != -1:
            continue  # u32 1, u32 8, map[0..7], u32 1, u32 count; sampler 7 (head[9]) unmapped
        struct.pack_into(">i", data, i - 4 * 3, value)
        open(p, "wb").write(bytes(data))
        done += 1
    return done


def lightmap_binding(F, m, data, srcs, shader_blobs, recs):
    """(the fetch constant, the model file's data) for LMUV_BIND, or (None, data): sampler 7's
    map entry stays -1 and fetch constant fc, written by the model's command buffers (a type-3
    fixup (fc << 16) | 3) but sampled by none of its shaders, takes the lightmap's slot: the
    slot holding the instances' lightmap record when they share one, else the slot after the
    model's own (FH1's table keeps the instance's lightmap there)."""
    hit = _SHADER_NAME.search(data)
    if not hit:
        return None, data
    i = hit.start()
    head = [struct.unpack_from(">i", data, i - 4 * k)[0] for k in range(12, 0, -1)]
    if head[0] != 1 or head[1] != 8 or head[-2] != 1 or head[9] != -1:
        return None, data
    sampled = set()
    for p in srcs:
        for _, _, sm, _, _ in shader_blobs(p):
            sampled.update(sm.values())
    tex = [t & 0xFFFFFF for t in F["models"][m]["textures"]]
    slots = [k for k, t in enumerate(tex) if t in recs]
    if len(slots) != 1:
        return None, data
    value = slots[0]
    for fc in range(6, -1, -1):
        if fc in sampled or head[2 + fc] < 0 or struct.pack(">I", (fc << 16) | 3) not in data:
            continue
        out = bytearray(data)
        struct.pack_into(">i", out, i - 4 * (10 - fc), value)  # map[fc]: head[2 + fc]
        return fc, bytes(out)
    if not LMUV_INSERT:
        return None, data
    # No spare one: add one. The lightmap's own fetch constant, or one no shader samples, that
    # the sampler map leaves unmapped and no command buffer writes (the map covers 0 to 7).
    lm = set()
    for p in srcs:
        for _, _, sm, _, _ in shader_blobs(p):
            if "Light_MapSampler" in sm:
                lm.add(sm["Light_MapSampler"])
    for fc in range(7, -1, -1):
        if (fc in sampled and fc not in lm) or head[2 + fc] != -1:
            continue
        out = insert_fetch_constant(data, fc)
        if out is None:
            return None, data
        INSERTED.add(m)
        out = bytearray(out)
        struct.pack_into(">i", out, i - 4 * (10 - fc), value)  # the name is before the groups
        return fc, bytes(out)
    return None, data


def lightmap_rect_copies(Fs, files, bin_dir):
    """Give the models described above their rectangles (in every ribbon's F, before the
    zones are made); the models done (their numbers and their copies')."""
    RECT_COPIES.clear()
    INSERTED.clear()
    if not LMUV_RECT or not Fs or len({len(F["models"]) for F in Fs}) != 1:
        return set()
    F0 = Fs[0]
    listed = set(lightmap_uv_models(F0, bin_dir))
    own = {int(p.split("_0x")[1][:8], 16) for p in files if "_0x" in p and p.lower().endswith(".bix")}
    shader_files = {p.upper(): p for p in files if p.lower().endswith(".fxobj")}
    original = {n.lower() for F in Fs for n in F["shaders"]}
    blobs_of = {}

    def shader_blobs(p):
        if p not in blobs_of:
            blobs_of[p] = fm4_shaders.blobs(open(os.path.join(bin_dir, p), "rb").read())
        return blobs_of[p]

    def rect_of(F, inst):
        T, r = F["textures"], inst["idx"] & 0xFFFFFF
        if inst["u12"] == 0xFFFFFFFF or r >= len(T):
            return None
        if T[r][0] in own and not OWN_RECT:
            return IDENTITY_RECT
        su, sv, ou, ov = T[r][2:6]
        return (ou, ov, su, sv)
    rects, records = {}, {}
    for F in Fs:
        for inst in F["instances"]:
            if inst["model"] in listed:
                rects.setdefault(inst["model"], set()).add(rect_of(F, inst))
                if inst["u12"] != 0xFFFFFFFF:
                    records.setdefault(inst["model"], set()).add(inst["idx"] & 0xFFFFFF)
    variant_of, taken = {}, set()
    number = {}  # (model, rectangle, or with LMUV_BIND the lightmap record): its model number
    slot_records = {}  # with LMUV_BIND: model: (its lightmap slot, the record put there)
    new_models = []  # (copy's number, model, lightmap slot, record) with LMUV_BIND
    done = set()
    next_number = len(F0["models"])
    def key_of(F, inst):
        if LMUV_BIND:
            return inst["idx"] & 0xFFFFFF if inst["u12"] != 0xFFFFFFFF else None
        return rect_of(F, inst)

    def rect_of_record(F, r):
        return rect_of(F, {"idx": r, "u12": 0})
    for m in sorted(rects):
        rs = rects[m]
        if None in rs or (rs == {IDENTITY_RECT} and not LMUV_BIND):
            continue  # no lightmap, or whole ones only (lightmap_uv_copies)
        if LMUV_BIND and len(records.get(m, ())) > LMUV_BIND_MAX:
            continue  # too many copies to stream (LMUV_BIND_MAX)
        mfile = f"{F0['prefix']}.{m:05d}.rmb.bin"
        if mfile not in files:
            continue
        data = open(os.path.join(bin_dir, mfile), "rb").read()
        writes = _c120_writes(data)
        names = [F0["shaders"][si] for si in F0["models"][m]["shaders"]]
        srcs = [shader_files.get(f"SHADERS/TRACK/{n.split(chr(92))[-1].upper()}.FXOBJ") for n in names]
        if not writes or not all(srcs):
            continue
        used = set()
        for p in srcs:
            for _, _, _, fl, li in shader_blobs(p):
                for r, k in fl.values():
                    used.update(range(r, r + k))
                used.update(li)
        # and none a type-8 fixup sets at load (word (120 << 16) | (register - 120) << 10 |
        # slot << 4 | 8: a texture's uvOS; 0x780408 sets c121); the lowest first (the
        # trace showed c122 and c123 arrive as written; with c127 Hockenheim 7 stayed flat)
        for f in re.finditer(rb"(?=\x00\x78([\x00-\xff])([\x00-\xff]))", data):
            if f.group(2)[0] & 0xF == 8:
                used.add(120 + (f.group(1)[0] >> 2))
        free = [r for r in range(121, 128) if r not in used and all(n > r - 120 for _, n in writes)]
        lm = [p for p in srcs if any(fl.get("Light_Map_uvOS", (0,))[0] == 116 for _, _, _, fl, _ in shader_blobs(p))]
        if not free or not lm:
            continue
        reg = free[0]
        if any(fm4_shaders.lightmap_uv_register(open(os.path.join(bin_dir, p), "rb").read(), reg)[2]
               for p in lm):
            continue  # a read of c116 other than the lightmap coordinate's mad
        fc = None
        new_data = data
        if LMUV_BIND:
            fc, new_data = lightmap_binding(F0, m, data, srcs, shader_blobs, records.get(m, set()))
            if fc is None:
                continue
        new_names = list(names)
        for k, (n, p) in enumerate(zip(names, srcs)):
            if p not in lm:
                continue
            key = (n.lower(), reg, fc)
            if key not in variant_of:
                variant_of[key] = None
                for letter in "lmnpqrtuvwxyz":
                    v = n[:-1] + letter
                    vf = f"SHADERS/TRACK/{v.split(chr(92))[-1].upper()}.FXOBJ"
                    if v.lower() not in original and v.lower() not in taken and vf not in shader_files:
                        variant_of[key] = v
                        taken.add(v.lower())
                        dst = f"shaders/track/{v.split(chr(92))[-1].upper()}.fxobj"
                        RECT_COPIES[dst] = (p, reg, fc)
                        open(os.path.join(bin_dir, dst), "wb").write(open(os.path.join(bin_dir, p), "rb").read())
                        files.append(dst)
                        break
            v = variant_of[key]
            if v is None:
                break
            new_names[k] = v
            old, new = (n + ".fx\0").encode(), (v + ".fx\0").encode()
            if old not in new_data:
                old, new = old.lower(), new.lower()
            new_data = new_data.replace(old, new)
        else:
            keys = sorted(records[m]) if LMUV_BIND else sorted(rs)
            slot = None
            if LMUV_BIND:
                tex = [t & 0xFFFFFF for t in F0["models"][m]["textures"]]
                slot = [k for k, t in enumerate(tex) if t in records[m]][0]
            for j, key in enumerate(keys):
                rect = rect_of_record(F0, key) if LMUV_BIND else key
                out = bytearray(new_data)
                for off, _ in _c120_writes(new_data):  # new_data's: an added fetch constant moves them
                    struct.pack_into(">4f", out, off + 16 * (reg - 120), *rect)
                n = m if j == 0 else next_number
                if j:
                    next_number += 1
                    files.append(f"{F0['prefix']}.{n:05d}.rmb.bin")
                    new_models.append((n, m, slot, key))
                elif LMUV_BIND:
                    slot_records[m] = (slot, key)
                open(os.path.join(bin_dir, f"{F0['prefix']}.{n:05d}.rmb.bin"), "wb").write(bytes(out))
                number[(m, key)] = n
                done.add(n)
            done.add(m)
            for F in Fs:
                for v in new_names:
                    if v not in F["shaders"]:
                        F["shaders"].append(v)
                F["models"][m] = dict(F["models"][m], shaders=[F["shaders"].index(v) for v in new_names])
    def with_record(M, slot, rec):
        if slot is None:
            return dict(M)
        tex = list(M["textures"])
        tex[slot] = (tex[slot] & ~0xFFFFFF) | rec
        return dict(M, textures=tex)
    for F in Fs:
        for m, (slot, rec) in slot_records.items():
            F["models"][m] = with_record(F["models"][m], slot, rec)
        for n, m, slot, rec in new_models:
            assert len(F["models"]) == n
            F["models"].append(with_record(F["models"][m], slot, rec))
        for inst in F["instances"]:
            if inst["model"] in rects and (inst["model"], key_of(F, inst)) in number:
                inst["model"] = number[(inst["model"], key_of(F, inst))]
    return done


def lightmap_uv_copies(converted, files, bin_dir, skip=()):
    """Give the models described above their shader copies (in every ribbon, in their model
    files and in the files list); the number of models changed."""
    LMUV_COPIES.clear()
    if not LMUV_IDENTITY or not converted:
        return 0
    listed = set(lightmap_uv_models(converted[0][2], bin_dir))
    own = {int(p.split("_0x")[1][:8], 16) for p in files if "_0x" in p and p.lower().endswith(".bix")}
    original = {n.lower() for c in converted for n in c[2]["shaders"]}
    kinds = {}
    for c in converted:
        F = c[2]
        T = F["textures"]
        for inst in F["instances"]:
            m = inst["model"]
            if m not in listed:
                continue
            r = inst["idx"] & 0xFFFFFF
            if inst["u12"] == 0xFFFFFFFF or r >= len(T):
                kinds.setdefault(m, set()).add("none")
            else:
                kinds.setdefault(m, set()).add("own" if T[r][0] in own else "tile")
    models = sorted(m for m, k in kinds.items() if m not in skip and
                    (k == {"own"} or os.environ.get("FM4_PVS_LMUV_ALL")))
    shader_files = {p.upper(): p for p in files if p.lower().endswith(".fxobj")}
    reads = {}

    def reads_lmuv(p):
        if p not in reads:
            reads[p] = any(fl.get("Light_Map_uvOS", (0,))[0] == 116 for _, _, _, fl, _ in
                           fm4_shaders.blobs(open(os.path.join(bin_dir, p), "rb").read()))
        return reads[p]
    changed = 0
    variant_of, taken = {}, set()  # shader name (lower case): its copy's; the copies' names
    for m in models:
        mfile = f"{converted[0][2]['prefix']}.{m:05d}.rmb.bin"
        if mfile not in files:
            continue
        data = open(os.path.join(bin_dir, mfile), "rb").read()
        new_data = data
        for c in converted:
            F, P = c[2], c[3]
            if m >= len(F["models"]):
                continue
            shaders = list(F["models"][m]["shaders"])
            for k, si in enumerate(shaders):
                name = F["shaders"][si]
                base = name.split("\\")[-1]
                src = shader_files.get(f"SHADERS/TRACK/{base.upper()}.FXOBJ")
                if not src or not reads_lmuv(src) or name.lower() in taken:
                    continue
                if name.lower() not in variant_of:
                    # the first last letter giving a name no shader has
                    for letter in "lmnpqrtuvwxyz":
                        v = name[:-1] + letter
                        if (v.lower() not in original and v.lower() not in taken and
                                f"SHADERS/TRACK/{v.split(chr(92))[-1].upper()}.FXOBJ" not in shader_files):
                            variant_of[name.lower()] = v
                            taken.add(v.lower())
                            break
                    else:
                        variant_of[name.lower()] = None
                variant = variant_of[name.lower()]
                if variant is None:
                    continue
                dst = f"shaders/track/{variant.split(chr(92))[-1].upper()}.fxobj"
                if variant not in F["shaders"]:
                    F["shaders"].append(variant)
                    P["shaders"].append(shader_of(variant))
                shaders[k] = F["shaders"].index(variant)
                old = (name + ".fx\0").encode()
                new = (variant + ".fx\0").encode()
                if old not in new_data:
                    old, new = old.lower(), new.lower()
                new_data = new_data.replace(old, new)
                if dst not in LMUV_COPIES:
                    LMUV_COPIES[dst] = src
                    open(os.path.join(bin_dir, dst), "wb").write(open(os.path.join(bin_dir, src), "rb").read())
                    if dst not in files:
                        files.append(dst)
            F["models"][m] = dict(F["models"][m], shaders=shaders)
            P["models"][m] = dict(P["models"][m], shaders=list(shaders))
        if new_data != data:
            open(os.path.join(bin_dir, mfile), "wb").write(new_data)
            changed += 1
    return changed


def fits_stream_pool(need):
    """Whether need blocks (stream_blocks) and their margin fit the game's streaming pool."""
    return int(need * STREAM_MARGIN + 0.5) <= MAX_STREAM_BLOCKS


def stream_blocks(F, cells, grid, bin_dir):
    """Blocks the zones of one ribbon need at most (see STREAM_MARGIN), without the margin."""
    size = {}
    def file_size(m):
        if m not in size:
            name = m if isinstance(m, str) else f"{F['prefix']}.{m:05d}.rmb.bin"
            p = os.path.join(bin_dir, name)
            if os.path.exists(p):
                size[m] = os.path.getsize(p)
            elif name in GROUND_COPIES:
                # The ground's model copy (see GROUND_LAYER), in the zones before its file is
                # written: the size of the file it is a copy of (the renames keep the length).
                size[m] = file_size(GROUND_COPIES[name][0])
            else:
                size[m] = 0
        return size[m]

    def zone_files(cell):  # the models' files and, with ZONE_TEXTURES, the textures' own files
        out = set(cell["models"])
        for t in (cell["textures"] if ZONE_TEXTURES else ()):
            own = F["textures"][t][0]
            out.update(texture_files(own))
        return out
    index = {c: k for k, c in enumerate(grid.cells)}
    best = 0
    for cell, k in index.items():
        models = zone_files(cells[k])
        for c in grid.neighbours(*cell):
            if c in index:
                models.update(zone_files(cells[index[c]]))
        best = max(best, sum(file_size(m) for m in models))
    return (best + 1023) // 1024


def ground_model_copy(F, P, files, model, reach):
    """The number of a copy of model drawn with one blend layer (see GROUND_LAYER), or model
    itself if that cannot be done."""
    M = F["models"][model]
    new = len(F["models"])
    src, dst = f"{F['prefix']}.{model:05d}.rmb.bin", f"{F['prefix']}.{new:05d}.rmb.bin"
    if src not in files or GROUND_COPIES.get(dst, (src,))[0] != src:
        return model
    shaders, renames = list(M["shaders"]), []
    for k, si in enumerate(shaders if GROUND_LAYER != "copy" else []):
        name = F["shaders"][si]
        base = name.split("\\")[-1]
        if "terr_blnd" not in base.lower() or base[-1].lower() == "g":
            continue
        variant = name[:-1] + "g"
        fx_src = f"shaders/track/{base.upper()}.fxobj"
        fx_dst = f"shaders/track/{variant.split(chr(92))[-1].upper()}.fxobj"
        if fx_src not in files:
            continue
        if variant not in F["shaders"]:
            F["shaders"].append(variant)
            P["shaders"].append(shader_of(variant))
        shaders[k] = F["shaders"].index(variant)
        renames.append((base.lower().encode(), variant.split(chr(92))[-1].lower().encode()))
        GROUND_COPIES[fx_dst] = (fx_src, [])
        if fx_dst not in files:
            files.append(fx_dst)
    if not renames and GROUND_LAYER != "copy":
        return model
    F["models"].append(dict(M, shaders=shaders))
    # FH1 culls an instance by a sphere of its model's extent round its origin, and the
    # ground's origin is 8 to 10 km off the circuit (the 15x scale moves the piece's own offset,
    # its vertices sit hundreds of metres from their origin, with it): the ground was culled
    # everywhere its model was not drawn for its own piece anyway (holes at the grid and behind
    # the fence at spot 9). The copy, the ground's alone, gets an extent that reaches from its
    # origin over the whole circuit.
    P["models"].append(dict(P["models"][model], shaders=shaders, extent=[reach] * 3))
    P["rmodels"] = len(P["models"])
    if dst not in files:
        files.append(dst)
    GROUND_COPIES[dst] = (src, renames)
    return new


def write_ground_copies(bin_dir):
    """The files of GROUND_COPIES, in the track's bin folder."""
    for dst, (src, renames) in GROUND_COPIES.items():
        with open(os.path.join(bin_dir, src), "rb") as f:
            data = f.read()
        for a, b in renames:  # the shader names in the model's material table
            data = data.replace(a, b)
        os.makedirs(os.path.dirname(os.path.join(bin_dir, dst)), exist_ok=True)
        with open(os.path.join(bin_dir, dst), "wb") as f:
            f.write(data)


def ground_instance(F, used):
    """A new FM4-style instance (see read_fm4) of a terrain model, or None."""
    insts = F["instances"]
    by_model = {}
    for i in used:
        if not any(insts[i]["pos"]):
            by_model.setdefault(insts[i]["model"], i)
    pick = os.environ.get("FM4_PVS_GROUND_MODEL")
    if pick:
        model = int(pick)
    else:
        best = None
        for m, i in by_model.items():
            M = F["models"][m]
            if not any("terr_blnd" in F["shaders"][s] for s in M["shaders"]):
                continue
            ex, ey, ez = M["extent"][:3]
            if min(ex, ez) < 25:
                continue
            flat = ey / max(ex, ez)
            if best is None or flat < best[0]:
                best = (flat, m)
        if best is None:
            return None
        model = best[1]
    if model not in by_model:
        return None
    src = insts[by_model[model]]
    # Lowest ground and the circuit's centre (drawn positions, x and z as placed).
    low, xs, zs = None, [], []
    for m, i in by_model.items():
        M = F["models"][m]["matrix"]
        y0 = M[13] - F["models"][m]["extent"][1]
        if any("terr_blnd" in F["shaders"][s] or "road" in F["shaders"][s]
               for s in F["models"][m]["shaders"]):
            low = y0 if low is None else min(low, y0)
            xs.append(M[12]); zs.append(-M[14])
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    if os.environ.get("FM4_PVS_GROUND_AT"):  # a test: the ground's centre at x,z instead
        cx, cz = (float(v) for v in os.environ["FM4_PVS_GROUND_AT"].split(","))
    M = F["models"][model]["matrix"]
    k = GROUND_SCALE
    # Drawn at (k x, y, -k z) + pos (FM4's 1, 1, -1 turn, stretched): its centre to the
    # circuit's, its lowest point GROUND_BELOW m under the lowest ground.
    pos = [cx - k * M[12], low - GROUND_BELOW - (M[13] - F["models"][model]["extent"][1]),
           cz + k * M[14]]
    h = lambda v: struct.unpack(">H", struct.pack(">e", v))[0]
    ext = F["models"][model]["extent"]
    reach = math.hypot(pos[0] - cx, pos[2] - cz) + k * max(ext[0], ext[2]) + 1000.0
    return {"model": model, "flags": src["flags"], "idx": 0, "u12": 0xFFFFFFFF, "reach": reach,
            "v16": [0.0, 0.0, 0.0, 0.0], "u32": src["u32"], "lod": src["lod"], "pos": pos,
            "rot": [h(k), 0, 0, 0, h(1.0), 0, 0, 0, h(-k)]}


# FM4_PVS_POOL_ZONES=n: the zone pools' size, in largest zones (0: FH1's own sizing).
POOL_ZONES = int(os.environ.get("FM4_PVS_POOL_ZONES", "4"))
# FM4_PVS_VISIBLE=0 zones instances by position only (see convert); FM4_PVS_VISIBLE_NEAR the
# radius (m) of the instances that pick a zone's FM4 cells, _CELLS how many cells, _SIZE the
# smallest extent (m) taken from them: 25, so stands and walls seen from far away are drawn
# (120 before, while bigger zones still showed stale-constant colours; a lap at 25 m held 30
# fps with 4% more draws and needed 10 MB more streaming blocks (fm4_stream_blocks.txt,
# STREAM_MARGIN) than at 120 m).
VISIBLE = os.environ.get("FM4_PVS_VISIBLE", "1") == "1"
VISIBLE_NEAR = float(os.environ.get("FM4_PVS_VISIBLE_NEAR", "40"))
VISIBLE_CELLS = int(os.environ.get("FM4_PVS_VISIBLE_CELLS", "1"))
VISIBLE_SIZE = float(os.environ.get("FM4_PVS_VISIBLE_SIZE", "25"))
# FM4_PVS_ZONE_REACH: what a zone holds. FH1 draws the camera's zone alone; Colorado's zones
# each list what is seen from them (an instance is in 16.5 of them on average), where a hex
# zone of its own instances (and the large pieces of FM4's visible set) showed small pieces
# only 120 m ahead at the median, and stretches of Le Mans that filled in at 200 km/h and
# over as the car got there (#824). "neighbours" (the default): a zone holds its own and its
# six neighbours' instances, everything within 200 m of its centre and more. Should that need
# more streaming blocks than the game has (MAX_STREAM_BLOCKS with STREAM_MARGIN), a layout's
# zones take the neighbours' instances within NEIGHBOUR_NEAR m of their centre, else their own
# alone. "own": their own alone, as before.
ZONE_REACH = os.environ.get("FM4_PVS_ZONE_REACH", "neighbours")
if ZONE_REACH not in ("neighbours", "own"):
    raise SystemExit(f"FM4_PVS_ZONE_REACH={ZONE_REACH}: neighbours or own")
# The shorter reaches tried in turn when the neighbours' whole hexes need too many blocks (m
# from the zone's centre: its own hex ends 87 to 100 m from it, the six around 200 to 273 m).
NEIGHBOUR_NEAR = (200.0, 150.0)
# FM4_PVS_SKY=0 leaves FM4's sky dome out (see convert).
SKY = os.environ.get("FM4_PVS_SKY", "1") == "1"
SKY_EXTENT = 4200.0


# Zone size: FH1's Colorado has 100 m hexes; its loader streams the camera's zone and the six
# around it through a 23 MB buffer, which 250 m zones of Suzuka overflow.
HEX_RADIUS = float(os.environ.get("FM4_PVS_HEX", "100"))


class HexGrid:
    """FH1's zone grid (a <track>_NN.hex file, HEXY version 101): flat-topped hexes of a
    radius, in columns 1.5 radii apart, odd columns half a hex lower; the centre of (col, row)
    is origin + (1 + 1.5 col, sqrt3/2 + sqrt3 row + sqrt3/2 (col odd)) radii (TU4
    sub_8320EA30 and sub_8320EAA8; an x of 2 radii, fitted to Colorado's zones, put every
    zone's contents 100 m off the zone FH1 draws from). Every cell of the grid is a zone here."""

    def __init__(self, points, radius):
        self.r = radius
        xs, zs = [p[0] for p in points], [p[1] for p in points]
        h = 3 ** 0.5 * radius
        self.ox = min(xs) - 2.75 * radius
        self.oz = min(zs) - h
        self.w = int((max(xs) - self.ox) / (1.5 * radius)) + 2
        self.h = int((max(zs) - self.oz) / h) + 2
        self.cells = [(c, r) for c in range(self.w) for r in range(self.h)]

    def centre(self, c, r):
        h = 3 ** 0.5 * self.r
        return (self.ox + (1 + 1.5 * c) * self.r, self.oz + h / 2 + h * r + (h / 2 if c & 1 else 0))

    def cell_of(self, x, z):
        c0 = int((x - self.ox - self.r) / (1.5 * self.r))
        r0 = int((z - self.oz) / (3 ** 0.5 * self.r))
        best = None
        for c in range(c0 - 1, c0 + 2):
            for r in range(r0 - 1, r0 + 2):
                if 0 <= c < self.w and 0 <= r < self.h:
                    cx, cz = self.centre(c, r)
                    d = (cx - x) ** 2 + (cz - z) ** 2
                    if best is None or d < best[0]:
                        best = (d, (c, r))
        return best[1]

    def cells_near(self, x, z, reach):
        """The cell of (x, z) and every cell whose centre is within reach + one radius."""
        out = {self.cell_of(x, z)}
        lim = (reach + self.r) ** 2
        for cell in self.cells:
            cx, cz = self.centre(*cell)
            if (cx - x) ** 2 + (cz - z) ** 2 <= lim:
                out.add(cell)
        return sorted(out)

    def neighbours(self, c, r):
        """The cells around (c, r), up to six: odd columns sit half a hex lower, so the side
        neighbours of an odd column's cell are in rows r and r + 1, of an even one's r - 1 and r."""
        side = (0, 1) if c % 2 else (-1, 0)
        near = [(c, r - 1), (c, r + 1)] + [(c + dc, r + dr) for dc in (-1, 1) for dr in side]
        return [(nc, nr) for nc, nr in near if 0 <= nc < self.w and 0 <= nr < self.h]

    def write(self):
        zone = {cell: k for k, cell in enumerate(self.cells)}
        out = b"HEXY" + struct.pack(">IfffIII", 101, self.r, self.ox, self.oz, len(self.cells),
                                    self.w, self.h)
        out += struct.pack(f">{self.w * self.h}i",
                           *[zone[(c, r)] for r in range(self.h) for c in range(self.w)])
        out += b"".join(struct.pack(">II", c, r) for c, r in self.cells)
        return out + b"\x3f" * len(self.cells)


def write_filename_map(names):
    head = 4 * len(names)
    offs, blob = [], bytearray()
    for n in names:
        offs.append(head + len(blob))
        blob += n.encode("latin1") + b"\0"
    return struct.pack(f">{len(names)}I", *offs) + bytes(blob)


def write_track_zip(members, out):
    """members: (name in the archive, bytes). Returns the zipmanifest.xml dirstart, dirsize
    (central directory and end record) and direntries."""
    import zlib
    with open(out, "wb") as f:
        offs = []
        for _, data in members:
            offs.append(f.tell())
            f.write(data)
        cd_start = f.tell()
        cd = bytearray()
        for (name, data), off in zip(members, offs):
            n = name.encode("latin1")
            extra = struct.pack("<HHI", 0x1123, 4, off)
            cd += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 10, 10, 0, 0, 0, 0,
                              zlib.crc32(data) & 0xFFFFFFFF, len(data), len(data), len(n),
                              len(extra), 0, 0, 0, 0, off) + n + extra
        f.write(cd)
        f.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(members), len(members),
                            len(cd), cd_start, 0))
    return cd_start, len(cd) + 22, len(members)


def archive_order(paths):
    """FH1's order of a track archive's files, which the zone tables and the filename index
    (FilenameMap_NN.dat) use as file numbers: by CRC-32 of the lower-case path in the
    archive (checked against the game's own UI levels)."""
    import zlib
    return sorted(paths, key=lambda p: zlib.crc32(p.lower().encode("latin1")))


def surfaces(fh1_disc):
    """FM4 surface number -> FH1's (tools/fm4_col.py), from both games' surfaceTypes.xml:
    FM4's in FM4_DISC/Media/physics (unset: FM4's numbers kept), FH1's in its physics.zip."""
    if not os.environ.get("FM4_DISC"):
        return None
    import tempfile
    import carbin_dump
    fm4 = open(os.path.join(os.environ["FM4_DISC"], "Media", "physics", "surfaceTypes.xml"),
               encoding="utf-8", errors="replace").read()
    with tempfile.TemporaryDirectory() as tmp:
        fh1 = carbin_dump.unxmem_member(os.path.join(fh1_disc, "media", "physics.zip"),
                                        "surfaceTypes.xml", tmp).decode("utf-8", "replace")
    return fm4_col.surface_map(fm4, fh1)


# FH1 lights a track from its ribbons' time-of-day curves (Tracks\<track>\Ribbon_NN\
# TimeOfDay.xml, TimeOfDayA.xml, TimeOfDayB.xml, TimeOfDayNeutral.xml: sun, sky, clouds, haze
# and the clock's speed, keyed by minutes after midnight); FM4's circuits have none, and the
# sky and lighting came out dark blue, as at night. The converter copies Colorado's, each
# curve held at FM4_PVS_TOD minutes (default 780, 13:00; "off" copies nothing, "keep" copies
# them as they are), so the circuit stays at that time of day whatever the clock says.
TOD = os.environ.get("FM4_PVS_TOD", "780")
TOD_FILES = ("TimeOfDay.xml", "TimeOfDayA.xml", "TimeOfDayB.xml", "TimeOfDayNeutral.xml")


def hold_time_of_day(xml, minutes):
    """Every <Name numKeys=...> curve of a TOD file as one key: its value at minutes."""
    import re

    def held(m):
        name, body = m.group(1), m.group(2)
        keys = [dict(re.findall(r'(\w+)="([^"]*)"', k)) for k in re.findall(r"<Key ([^>]*)>", body)]
        if not keys:
            return m.group(0)
        ts = [float(k["time"]) for k in keys]
        attrs = [a for a in keys[0] if a != "time"]
        if minutes <= ts[0]:
            vals = {a: float(keys[0][a]) for a in attrs}
        elif minutes >= ts[-1]:
            vals = {a: float(keys[-1][a]) for a in attrs}
        else:
            j = next(i for i in range(1, len(ts)) if ts[i] >= minutes)
            f = (minutes - ts[j - 1]) / ((ts[j] - ts[j - 1]) or 1.0)
            vals = {a: float(keys[j - 1][a]) + f * (float(keys[j][a]) - float(keys[j - 1][a]))
                    for a in attrs}
        key = " ".join(f'{a}="{vals[a]:.6f}"' for a in attrs)
        return (f' <{name} numKeys="2">\n  <Key time="0.000000" {key}></Key>\n'
                f'  <Key time="1439.000000" {key}></Key>\n </{name}>')
    return re.sub(r' *<(\w+) numKeys="\d+">(.*?)</\1>', held, xml, flags=re.S)


def write_time_of_day(fh1_disc, ribbon_dir):
    if TOD == "off":
        return
    src = os.path.join(fh1_disc, "media", "tracks", "colorado", "Ribbon_00")
    for name in TOD_FILES:
        path = os.path.join(src, name)
        if not os.path.exists(path):
            continue
        xml = open(path, encoding="utf-8", errors="replace").read()
        if TOD != "keep" and "<TOD" in xml:
            xml = hold_time_of_day(xml, float(TOD))
        open(os.path.join(ribbon_dir, name), "w", encoding="utf-8").write(xml)


# FM4's <track>/TrackSettings.xml (version 59) lacks what FH1's renderer reads in its
# version 64 (Brightness, Contrast, ExposureBias, LightHemiTop/Bottom, WorldClear, the bloom
# block, height fog): read as it is, the circuit drew far too dark. FM4_PVS_TRACKSETTINGS=
# merge (the default) writes Colorado's with FM4's values for the elements in
# TRACK_SETTINGS_FM4 (what FM4's own shaders take: sun gain, lightmap and road constants)
# and FM4's fog; "fh1" Colorado's as it is; "fm4" leaves FM4's.
TRACK_SETTINGS = os.environ.get("FM4_PVS_TRACKSETTINGS", "merge")
TRACK_SETTINGS_FM4 = [x for x in os.environ.get(
    "FM4_PVS_TRACKSETTINGS_FM4", "SkyboxHeightOffset,sunGainScale,TrackLightMapTransition,"
    "TrackLightMapColor,RoadSpecular,TrackLightMapParams,wholeTrackLightmapRadius,Fog").split(",") if x]


def merge_track_settings(fh1_xml, fm4_xml):
    import re
    out = fh1_xml
    for name in TRACK_SETTINGS_FM4:
        pat = re.compile(r"<%s\b[^>]*?(/>|>.*?</%s>)" % (name, name), re.S)
        a, b = pat.search(out), pat.search(fm4_xml)
        if a and b:
            out = out[:a.start()] + b.group(0) + out[a.end():]
    return out


def write_track_settings(fh1_disc, root):
    if TRACK_SETTINGS == "fm4":
        return
    src = os.path.join(fh1_disc, "media", "tracks", "colorado", "TrackSettings.xml")
    if not os.path.exists(src):
        return
    fh1 = open(src, encoding="utf-8", errors="replace").read()
    for name in os.listdir(root):
        if name.lower().startswith("tracksettings") and name.lower().endswith(".xml"):
            path = os.path.join(root, name)
            fm4 = open(path, encoding="utf-8", errors="replace").read()
            xml = fh1 if TRACK_SETTINGS == "fh1" else merge_track_settings(fh1, fm4)
            open(path, "w", encoding="utf-8").write(xml)


# A model's materials (after its shader names) are an 8-word header and 21 pass groups:
# u32 2, u32 n, n 8-byte records (u16 register, u8 pass, u8 size, u8 kind, 0, u16 data
# offset: the constant blocks, samplers and uvOS constants the draw sets), u32 m, m 8-byte
# entries, 3 sizes (0 for an empty group). Both games use the same layout, but FH1 keeps its
# main pass in group 0 (57 of 60 Colorado models; the others hold 12 and 18) and FM4 keeps
# group 0 empty and its first populated one at 3 (or 9, 6...). FH1 then set none of FM4's
# material constants and its pixel shaders read another draw's: flat blue, green or magenta
# road and walls, different every run. FM4_PVS_PASS_GROUPS=1 (a test, off by default) swaps
# an empty group 0 with the first populated group; the groups between are empty (sizes 0),
# so the data the sizes count stays where it was, and the file keeps its size. FH1 then
# runs FM4's fixups (it faulted on type 4 until those were dropped) but the blend surfaces
# still drew flat purple (types 5 kept) or black (5 dropped): the constants are not set
# that way either.
PASS_GROUPS = os.environ.get("FM4_PVS_PASS_GROUPS", "0") == "1"
PASS_GROUP_DROP = {int(x, 0) for x in os.environ.get("FM4_PVS_PASS_GROUP_DROP", "4,5,6").split(",") if x}


def pass_groups(d, p):
    out = []
    while p + 8 <= len(d) and len(out) < 21:
        t, n = struct.unpack_from(">II", d, p)
        if t != 2 or n > 4096:
            return None
        q = p + 8 + 8 * n
        if q + 4 > len(d):
            return None
        m = struct.unpack_from(">I", d, q)[0]
        q += 4 + 8 * m + 12
        if m > 4096 or q > len(d):
            return None
        out.append((p, q, n, struct.unpack_from(">3I", d, q - 12)))
        p = q
    return out if len(out) == 21 else None


# A pass group's data, after the 21 groups (sizes: the group's three):
#   area A, each populated group's sizes[0] bytes in turn: D3D's precompiled command buffer
#     header (0xC8 bytes: state masks; +0x68 sizes[0] - 8; +0x70 the end of the patch table,
#     0xC8 + 4 * its words; +0x78 sizes[1] - 8; +0x7C the patch table's words; +0x90 0xC8, the
#     table's place; +0xC0 0x81000000 | (sizes[1] - 8) / 4), the patch table, zeros up to the
#     8-aligned end of the table + 8, then 11223344 55667789;
#   area B, each group's command buffer (sizes[1] bytes, PM4) at the next 32-byte boundary
#     from the area's start (the .fx header's word 4: the area's size, word 3 area A's);
#   area C, sizes[2] bytes each (word 5): vertex/index buffer fixups, two words each.
#     TU4 sub_824AA8D0 and sub_824AABD8 take the base dword from bits 6-25 of
#     the first word, then add the vertex slot or the index-address offset.
# The .fx header's word 7 is 8 times the groups' records. A record's second word for a fixup
# (type 3, a texture: 0x47000000 | index; kinds 0x41-0x47) indexes the patch table (TU4
# sub_824AAA28, the table at +0x90): from that word on, each word (0x84.., 0xC4..: one; 0x88..,
# 0xC8..: one and a value word after it) is a place in the command buffer (bits 6-25 the dword,
# bit 30 another place follows), where sub_824AAAC0 writes the texture's six fetch constant
# dwords for type 3, keeping the template's sampler bits. FH1 finds a record by its first word
# (sub_8248B570: (register << 4) | type; a texture's (fc << 16) | 3) and does nothing for a
# fetch constant without one.
FC7_TEMPLATE = (0x80404802, 0xFFFFF086, 0x00000000, 0x00A80C14, 0x003E8003, 0x00000218)  # clamped, Hockenheim 257's


def _pm4_packets(buf):
    """(offset, header) of each PM4 packet of a command buffer, or None if it does not walk."""
    out, o = [], 0
    while o + 4 <= len(buf):
        w = struct.unpack_from(">I", buf, o)[0]
        t = w >> 30
        if t == 0:
            n = 1 + ((w >> 16) & 0x3FFF) + 1
        elif t == 2:
            n = 1
        elif t == 3:
            n = 1 + ((w >> 16) & 0x3FFF) + 1
        else:
            n = 2  # type 1: two registers
        out.append((o, w))
        o += 4 * n
    return out if o == len(buf) else None


def insert_fetch_constant(data, fc, template=FC7_TEMPLATE):
    """The model file with fetch constant fc written by every pass that writes fetch constant 0
    (a type-0 packet after that one, a type-3 fixup record (fc << 16) | 3 and its patch place),
    or None if a pass's data is not laid out as above."""
    k = data.rfind(b".fx")
    if k < 0:
        return None
    hp = k + 3
    head = list(struct.unpack_from(">8I", data, hp))
    if head[0] != 1:
        return None
    g = pass_groups(data, hp + 32)
    if not g:
        return None
    A = g[-1][1]
    pop = [x for x in g if any(x[3])]
    if sum(x[3][0] for x in pop) != head[3] or sum(x[3][2] for x in pop) != head[5]:
        return None
    B = A + head[3]
    C = B + head[4]
    offs, o = [], 0
    for x in pop:
        o = (o + 31) & ~31
        offs.append(o)
        o += x[3][1]
    if o != head[4] or C + head[5] > len(data):
        return None
    reg = 0x4800 + 6 * fc
    key = struct.pack(">I", (fc << 16) | 3)
    blocks, bufs, recs_of, sizes_of = [], [], {}, {}
    area_c = bytearray(data[C:C + head[5]])
    a, c = A, 0
    added = 0
    for x, bo in zip(pop, offs):
        s0, s1, s2 = x[3]
        blk = bytearray(data[a:a + s0])
        buf = bytearray(data[B + bo:B + bo + s1])
        a += s0
        fixup_start = c
        c += s2
        n = x[2]
        recs = [data[x[0] + 8 + 8 * r:x[0] + 16 + 8 * r] for r in range(n)]
        has0 = struct.pack(">I", 3) in [r[:4] for r in recs]
        if not has0 or key in [r[:4] for r in recs]:
            blocks.append(bytes(blk)); bufs.append(bytes(buf))
            continue
        W = lambda o: struct.unpack_from(">I", blk, o)[0]
        count, tend = W(0x7C), W(0x70)
        mark = ((tend + 7) & ~7) + 8
        if (W(0x90) != 0xC8 or tend != 0xC8 + 4 * count or mark + 8 != s0 or W(0x68) != s0 - 8
                or W(0x78) != s1 - 8 or W(0xC0) != 0x81000000 | ((s1 - 8) // 4)
                or blk[mark:] != bytes.fromhex("1122334455667789") or any(blk[tend:mark])):
            return None
        pk = _pm4_packets(bytes(buf))
        if pk is None:
            return None
        # one write of fetch constants from 0 per draw (a buffer may hold several draws)
        at = [o for o, w in pk if w >> 30 == 0 and (w & 0xFFFF) == 0x4800]
        if not at:
            return None
        ins = []
        for o in at:
            cnt = ((struct.unpack_from(">I", buf, o)[0] >> 16) & 0x3FFF) + 1
            if cnt % 6 or 0x4800 + cnt > reg:
                return None
            ins.append(o + 4 + 4 * cnt)
        # Area C has its own offsets into the same PM4 buffer. Inserting only in
        # area B would leave these writes targeting the old vertex/index slots.
        if s2 % 8:
            return None
        for off in range(fixup_start, c, 8):
            word = struct.unpack_from(">I", area_c, off)[0]
            if (word & 0xFC000000) not in (0x08000000, 0x48000000):
                return None
            target = ((word >> 6) & 0xFFFFF) * 4
            last = target + 4 * max(2, (word & 0x3F) + 1)
            if last >= s1 or any(target < o <= last for o in ins):
                return None
            shift = 7 * sum(o <= target for o in ins)
            if target // 4 + shift > 0xFFFFF:
                return None
            struct.pack_into(">I", area_c, off, word + (shift << 6))
        # the patch places after an insertion move with it
        table = [W(0xC8 + 4 * t) for t in range(count)]
        t = 0
        while t < count:
            e = table[t]
            kind = (e >> 26) & 3
            if not e >> 31 or kind not in (1, 2):
                return None
            table[t] = e + ((7 * sum(1 for o in ins if ((e >> 6) & 0xFFFFF) * 4 >= o)) << 6)
            t += 2 if kind == 2 else 1
        # the new places, one per draw: bit 30 on all but the last (another place follows)
        for j, o in enumerate(ins):
            more = 0x40000000 if j + 1 < len(ins) else 0
            table.append(0x84000000 | more | (((o + 28 * j + 4) // 4) << 6))
        packet = struct.pack(">7I", (5 << 16) | reg, *template)
        for o in reversed(ins):
            buf = buf[:o] + packet + buf[o:]
        s1 += len(packet) * len(ins)
        tend += 4 * len(ins)
        mark = ((tend + 7) & ~7) + 8
        new = bytearray(blk[:0xC8]) + b"".join(struct.pack(">I", e) for e in table)
        new += bytes(mark - len(new)) + bytes.fromhex("1122334455667789")
        s0 = len(new)
        for off, v in ((0x68, s0 - 8), (0x70, tend), (0x78, s1 - 8), (0x7C, count + len(ins)),
                       (0xC0, 0x81000000 | ((s1 - 8) // 4))):
            struct.pack_into(">I", new, off, v)
        last3 = max(r for r in range(n) if struct.unpack_from(">I", recs[r])[0] & 0xF == 3)
        recs.insert(last3 + 1, key + struct.pack(">I", 0x47000000 | count))
        recs_of[x[0]] = recs
        sizes_of[x[0]] = (s0, s1, s2)
        blocks.append(bytes(new)); bufs.append(bytes(buf))
        added += 1
    if not added:
        return None
    groups = b""
    for x in g:
        if x[0] not in recs_of:
            groups += data[x[0]:x[1]]
            continue
        recs = recs_of[x[0]]
        body = data[x[0] + 8 + 8 * x[2]:x[1] - 12]  # u32 m and its entries
        groups += struct.pack(">II", 2, len(recs)) + b"".join(recs) + body + struct.pack(">3I", *sizes_of[x[0]])
    area_b = bytearray()
    for b in bufs:
        area_b += bytes(((len(area_b) + 31) & ~31) - len(area_b)) + b
    head[3] = sum(len(b) for b in blocks)
    head[4] = len(area_b)
    head[7] += 8 * added
    return (data[:hp] + struct.pack(">8I", *head) + data[hp + 32:g[0][0]] + groups +
            b"".join(blocks) + bytes(area_b) + bytes(area_c) + data[C + head[5]:])


# FM4_PVS_FIXUP_DROP=4,5,6: those command-buffer fixup types become 0 (skipped) in every
# pass group (a test; see PASS_GROUPS for the record layout).
FIXUP_DROP = {int(x, 0) for x in os.environ.get("FM4_PVS_FIXUP_DROP", "").split(",") if x}


def drop_fixups(data):
    k = data.rfind(b".fx")
    if k < 0 or not FIXUP_DROP:
        return None
    g = pass_groups(data, k + 3 + 32)
    if not g:
        return None
    out = bytearray(data)
    hit = 0
    for p0, p1, n, sizes in g:
        for i in range(n):
            o = p0 + 8 + 8 * i
            w = struct.unpack_from(">I", out, o)[0]
            if (w & 0xF) in FIXUP_DROP:
                struct.pack_into(">I", out, o, w & ~0xF)
                hit += 1
    return bytes(out) if hit else None


def fix_pass_groups(data):
    """The model file with its main pass group first (see PASS_GROUPS), or None if unchanged."""
    k = data.rfind(b".fx")
    if k < 0:
        return None
    p = k + 3
    if p + 32 > len(data) or struct.unpack_from(">I", data, p)[0] != 1:
        return None
    g = pass_groups(data, p + 32)
    if not g or g[0][2]:
        return None
    first = next((i for i, x in enumerate(g) if x[2]), None)
    if first is None or any(any(x[3]) for x in g[:first]):
        return None
    a0, a1 = g[0][0], g[0][1]
    b0, b1 = g[first][0], g[first][1]
    moved = bytearray(data[b0:b1])
    # The records are command-buffer fixups (TU4 sub_82D19210, switch on the first word's low
    # 4 bits): FH1's main pass uses types 1, 2, 3, 8 and 9 and gives no data for the others;
    # FM4's 4 (a viewport fixup in FH1's numbering) faulted on a null pointer. Types in
    # PASS_GROUP_DROP become 0, which the switch skips.
    for i in range(g[first][2]):
        o = 8 + 8 * i
        w = struct.unpack_from(">I", moved, o)[0]
        if (w & 0xF) in PASS_GROUP_DROP:
            struct.pack_into(">I", moved, o, w & ~0xF)
    out = data[:a0] + bytes(moved) + data[a1:b0] + data[a0:a1] + data[b1:]
    return out


# FM4 draws its road lines, road edges and other overlays (decals) in a pass of their own:
# such a model has its command buffers in the third group of a pass's three alone (opaque,
# transparent, decal): 11, 14 and 17, and with them 8 where its instances may show in mirrors
# (their flag bit 4 clear) and 5 where in cube maps (bit 9 clear). Its instances carry flag
# bit 28. With that bit the load faults (fh1_flags tells of it), so it is dropped, and then
# nothing drew them: an instance is drawn with its opaque or its transparent groups, which
# these models leave empty. (FH1's own decal models fill groups 2, 5, 14 and 20, and their
# instances carry bit 28 without bit 27: FH1's real decal pass may take FM4's buffers with
# those; not tried.)
# The main pass's decal group (14) takes the place of the group the model's instances are
# flagged for (DECAL_SLOT; the empty groups before it move up, the data stays where it is and
# the file keeps its size), so FH1 draws the model in that pass with the buffer's own state:
# FM4's polygon offset and, for the painted lines and edges, its cover by alpha (alpha to
# mask, no blending; depth state below), for the blended road detail FM4's blending. In the
# other group's place the load faults as it does with bit 28 (Suzuka's lines as transparent,
# Infineon's road detail as opaque): the GPU reads at an address nothing has patched
# (physical 0x20000000, the end of memory). So the buffer's own blend state has to agree
# with the flag, set before each of its draws (it does on every decal model of FM4's Disc 1):
# a model where it does not, or where the scan cannot tell, is left undrawn rather than
# risked. The mirror and cube map groups stay where they are, unused.
# FM4_PVS_DECALS=0 leaves the decals undrawn, as before.
DECALS = os.environ.get("FM4_PVS_DECALS", "1") == "1"
DECAL_GROUPS = frozenset(range(2, 21, 3))
# An instance's flag bits 2 (opaque) and 1 (transparent): the group that takes the decal
# buffer. FM4_PVS_DECAL_BLENDED=0 leaves the transparent ones undrawn (a test).
DECAL_SLOT = {0x4: 12, 0x2: 13}
if os.environ.get("FM4_PVS_DECAL_BLENDED", "1") != "1":
    del DECAL_SLOT[0x2]
NO_BLEND = 0x00010001  # RB_BLENDCONTROL0 (register 0x2201): source one, destination zero
PM4_DRAWS = frozenset((0x22, 0x36))  # type-3 opcodes (bits 8-14): DRAW_INDX, DRAW_INDX_2
# RB_DEPTHCONTROL (register 0x2200) of a decal buffer drawn in the opaque pass, the bits
# cleared and the bits set: FM4's 0x082082E3 tests the depth without writing it, a state for
# a pass that comes after the road. In FH1's opaque pass the road may come after its lines,
# and a line drawn first is painted over by it (whole stretches of Suzuka without lines, the
# rest with). With the depth written (bit 2: 0x082082E7, the state of FM4's own
# rdedg_*_zwrite decals) a line stands whichever is drawn first: its polygon offset puts it
# in front of the road under it. The stencil function stays FM4's EQUAL: passing the stencil
# always (0x087087E3) did not bring a line back, and beside the depth write it changed
# nothing (Suzuka, the same drive each way). The buffers drawn in the transparent pass keep
# FM4's state. FM4_PVS_DECAL_DEPTH (a test): 0 keeps FM4's state in the opaque pass too,
# "stencil" only passes the stencil always, "both" does that and writes the depth (the
# road's own 0x087087E7).
DECAL_DEPTHS = {"1": (0, 0x00000004), "0": (0, 0), "stencil": (0x00700700, 0x00700700),
                "both": (0x00700700, 0x00700704)}
DECAL_DEPTH = DECAL_DEPTHS.get(os.environ.get("FM4_PVS_DECAL_DEPTH", "1"), DECAL_DEPTHS["1"])


def _pm4_type3_registers(buf, at, w):
    """The registers the type-3 packet at offset at of buf writes, as the command processor
    reads it: SET_CONSTANT and LOAD_ALU_CONSTANT (from memory) of registers (the kind word's
    bits 16-23 4: from 0x2000 + its low 11 bits), SET_CONSTANT2 and SET_SHADER_CONSTANTS (from
    the word's low 16 bits), REG_RMW and COND_WRITE to a register; every register for an
    INDIRECT_BUFFER (a buffer not read here) or one of these too short for its words. An
    empty range for the others."""
    op, n = (w >> 8) & 0x7F, (w >> 16) & 0x3FFF       # n + 1 data words
    d = struct.unpack_from(f">{n + 1}I", buf, at + 4)
    every = range(0x10000)
    if op == 0x2D:                                     # SET_CONSTANT: kind, values
        kind, size = d[0], n
    elif op == 0x2F:                                   # LOAD_ALU_CONSTANT: address, kind, size
        if n < 2:
            return every
        kind, size = d[1], d[2] & 0xFFF
    elif op in (0x55, 0x56):                           # SET_CONSTANT2, SET_SHADER_CONSTANTS
        return range(d[0] & 0xFFFF, (d[0] & 0xFFFF) + n)
    elif op == 0x21:                                   # REG_RMW: register, and, or
        return range(d[0] & 0x1FFF, (d[0] & 0x1FFF) + 1)
    elif op == 0x45:                                   # COND_WRITE: ..., register, value
        if n < 5:
            return every
        return range(0) if d[0] & 0x100 else range(d[4], d[4] + 1)  # bit 8: to memory
    elif op in (0x37, 0x3F):                           # INDIRECT_BUFFER_PFD, INDIRECT_BUFFER
        return every
    else:
        return range(0)
    first = 0x2000 + (kind & 0x7FF)
    return range(first, first + size) if (kind >> 16) & 0xFF == 4 else range(0)


def decal_model(data):
    """(the .fx header's place, the header, the 21 pass groups) of a decal model's file: its
    groups parse, group 14 is filled, and no group but DECAL_GROUPS; None for any other."""
    k = data.rfind(b".fx")
    if k < 0:
        return None
    p = k + 3
    if p + 32 > len(data):
        return None
    head = struct.unpack_from(">8I", data, p)
    if head[0] != 1:
        return None
    g = pass_groups(data, p + 32)
    if not g:
        return None
    filled = [i for i, x in enumerate(g) if x[2] or any(x[3])]
    if 14 not in filled or not set(filled) <= DECAL_GROUPS:
        return None
    return p, head, g


def decal_group_to(data, slot=12):
    """The model file of a decal model with its main pass's decal group (14) in the place of
    group slot (12 opaque, 13 transparent), and for 12 that group's buffer writing the depth
    (DECAL_DEPTH). None for any other model, and for a decal model whose buffer is not the
    pass's: an RB_BLENDCONTROL0 write of it blends in the opaque pass or does not in the
    transparent one, a draw comes before any (it would take the state the buffer drawn before
    it left), or RB_BLENDCONTROL0 or RB_DEPTHCONTROL is written in a way the scan does not
    read (a type-0 packet to one register, a type-3 packet). Also None for either slot when
    the buffer has no RB_DEPTHCONTROL write, although only slot 12 patches it, and when the
    header or the buffer is not laid out as expected."""
    m = decal_model(data)
    if m is None:
        return None
    p, head, g = m
    # the areas after the groups (the layout above insert_fetch_constant, and its checks)
    pop = [x for x in g if any(x[3])]
    if sum(x[3][0] for x in pop) != head[3] or sum(x[3][2] for x in pop) != head[5]:
        return None
    B = g[-1][1] + head[3]
    o, start = 0, None
    for x in pop:
        o = (o + 31) & ~31
        if x is g[14]:
            start = B + o
        o += x[3][1]
    if start is None or o != head[4] or B + head[4] + head[5] > len(data):
        return None
    buf = bytearray(data[start:start + g[14][3][1]])
    packets = _pm4_packets(bytes(buf))
    if packets is None:
        return None
    depth_at, blends = [], set()
    for at, w in packets:
        reg, count = w & 0x7FFF, ((w >> 16) & 0x3FFF) + 1
        if w >> 30 == 3:
            if (w >> 8) & 0x7F in PM4_DRAWS:
                if not blends:
                    return None
            else:
                regs = _pm4_type3_registers(buf, at, w)
                if 0x2200 in regs or 0x2201 in regs:
                    return None
            continue
        if w >> 30:                                    # type 1 (registers below 0x800), type 2
            continue
        if w & 0x8000:                                 # every value to register reg
            if reg in (0x2200, 0x2201):
                return None
            continue
        if reg <= 0x2200 < reg + count:
            depth_at.append(at + 4 + 4 * (0x2200 - reg))
        if reg <= 0x2201 < reg + count:
            blends.add(struct.unpack_from(">I", buf, at + 4 + 4 * (0x2201 - reg))[0] != NO_BLEND)
    # every draw of the buffer has to be of the pass's kind, by a blend write before it: none
    # blends in the opaque one, all blend in the transparent one
    if not depth_at or blends != {slot == 13}:
        return None
    if slot == 12:
        for q in depth_at:
            struct.pack_into(">I", buf, q, struct.unpack_from(">I", buf, q)[0] & ~DECAL_DEPTH[0] | DECAL_DEPTH[1])
    data = data[:start] + bytes(buf) + data[start + len(buf):]
    a0, b0, b1 = g[slot][0], g[14][0], g[14][1]
    return data[:a0] + data[b0:b1] + data[a0:b0] + data[b1:]


def draw_decals(converted, bin_dir):
    """Has FH1 draw the decal models (see DECALS): each one's file in the track's bin folder
    gets its decal group in the place its instances are flagged for (over every ribbon of its
    prefix). A decal model (decal_model) flagged both ways or neither, or that decal_group_to
    does not move for its flag, is left undrawn. The numbers of models drawn and left."""
    if not DECALS or FLAGS != "map":
        return 0, 0
    asked = {}
    for _, _, _, P, _, _, _ in converted:
        for inst in P["instances"] + P["dynamic"]:
            asked.setdefault((P["prefix"], inst["model"]), set()).add(inst["flags"] & 0x6)
    done = left = 0
    for (prefix, m), bits in sorted(asked.items()):
        path = os.path.join(bin_dir, f"{prefix}.{m:05d}.rmb.bin")
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        slot = DECAL_SLOT.get(next(iter(bits))) if len(bits) == 1 else None
        out = decal_group_to(data, slot) if slot else None
        if out is not None:
            with open(path, "wb") as f:
                f.write(out)
            done += 1
        elif decal_model(data) is not None:
            left += 1
    return done, left


def convert_mod(mod, fh1_disc, loose):
    tracks = os.path.join(mod, "media", "tracks")
    (track,) = [d for d in os.listdir(tracks) if os.path.isdir(os.path.join(tracks, d))]
    root = os.path.join(tracks, track)
    bin_dir = os.path.join(root, "bin")
    # <track>.stx.bin, the always-resident textures, is a CAFF archive whose symbol layout FH1
    # reads differently: FH1 ends it with a list of texture record numbers, FM4's holds one
    # texture per texture file (the records' file numbers) and no list. The game gets the
    # list from LOOSE_DIR/<track>_stx_numbers.bin through FORZA_FM4_STX=<track>|<that file>
    # (forza-recomp/src/hooks/fm4_track.cpp). FM4_PVS_STX=0 leaves the pack out instead.
    keep_stx = os.environ.get("FM4_PVS_STX", "1") == "1"
    stand_in = os.environ.get("FM4_PVS_STX_FILE")  # dev test: another track's stx.bin in its place
    keep_stx = keep_stx or bool(stand_in)
    files = [os.path.relpath(os.path.join(dp, n), bin_dir).replace(os.sep, "/")
             for dp, _, fs in os.walk(bin_dir) for n in fs
             if not n.endswith(".pvsz") and (keep_stx or not n.lower().endswith(".stx.bin"))]
    write_track_settings(fh1_disc, root)
    if FIXUP_DROP:
        dropped = 0
        for n in os.listdir(bin_dir):
            if n.lower().endswith(".rmb.bin"):
                path = os.path.join(bin_dir, n)
                out = drop_fixups(open(path, "rb").read())
                if out is not None:
                    open(path, "wb").write(out)
                    dropped += 1
        print(f"{track}: fixups {sorted(FIXUP_DROP)} dropped in {dropped} models")
    if PASS_GROUPS:
        fixed = 0
        for n in os.listdir(bin_dir):
            if n.lower().endswith(".rmb.bin"):
                path = os.path.join(bin_dir, n)
                out = fix_pass_groups(open(path, "rb").read())
                if out is not None:
                    open(path, "wb").write(out)
                    fixed += 1
        print(f"{track}: main pass group first in {fixed} models")
    ribbons = []
    for ribbon in sorted(d for d in os.listdir(root) if d.lower().startswith("ribbon_")):
        pvs = [f for f in os.listdir(os.path.join(root, ribbon)) if f.lower().endswith(".pvs")]
        if len(pvs) != 1:
            continue
        path = os.path.join(root, ribbon, pvs[0])
        data = open(path, "rb").read()
        if data[4:8] != struct.pack(">I", 27):
            print(f"{path}: not FM4's version 27, left alone")
            continue
        ribbons.append((ribbon, path, data, None))
        write_time_of_day(fh1_disc, os.path.join(root, ribbon))
        # The ribbon's collision in FH1's cell layout, still read whole from the .col file
        # (tools/fm4_col.py).
        for f in os.listdir(os.path.join(root, ribbon)):
            col = os.path.join(root, ribbon, f)
            if f.lower().endswith(".col"):
                cdata = open(col, "rb").read()
                if struct.unpack_from(">I", cdata, 0x2C)[0] == 2:
                    open(col, "wb").write(fm4_col.convert_col(cdata, surfaces(fh1_disc)))
    converted = []
    Fs = [read_fm4(data) for _, _, data, _ in ribbons]
    n_models = len(Fs[0]["models"]) if Fs else 0
    n_map = lightmap_sampler_map(Fs[0], files, bin_dir) if Fs else 0
    if n_map:
        print(f"{track}: {n_map} models' sampler 7 pointed at their lightmap ({LMUV_MAP})")
    rect_done = lightmap_rect_copies(Fs, files, bin_dir)
    if rect_done:
        print(f"{track}: {len(rect_done)} models drawn with their instance lightmap's rectangle "
              f"({len(Fs[0]['models']) - n_models} model copies, {len(RECT_COPIES)} shaders; "
              f"{len(INSERTED)} with a fetch constant added)")
    for (ribbon, path, data, _), F in zip(ribbons, Fs):
        P, cells, grid = convert(F, files, lambda cells, grid, F=F: fits_stream_pool(
            stream_blocks(F, cells, grid, bin_dir)))
        nn = ribbon[-2:]
        converted.append((ribbon, path, F, P, cells, grid,
                          [f"__R{int(nn):02d}Z{k:05d}.pvsz" for k in range(len(cells))]))
    n_lmuv = lightmap_uv_copies(converted, files, bin_dir, rect_done)
    if n_lmuv:
        print(f"{track}: {n_lmuv} models drawn with lightmap-identity shader copies ({len(LMUV_COPIES)} shaders)")
    write_ground_copies(bin_dir)
    n_decals, n_left = draw_decals(converted, bin_dir)
    if n_decals or n_left:
        print(f"{track}: {n_decals} decal models (road lines, road edges) drawn in FH1's opaque and transparent passes"
              + (f"; {n_left} left undrawn (flagged both ways or neither, or their buffer's state or layout"
                 " is not as their flag needs)" if n_left else ""))
    need = max(stream_blocks(c[2], c[4], c[5], bin_dir) for c in converted) if converted else 0
    order = archive_order(files + [zn for c in converted for zn in c[6]])
    index = {p: i for i, p in enumerate(order)}
    names = [p.split("/")[-1] for p in order]
    zones = {}
    for ribbon, path, F, P, cells, grid, zone_names in converted:
        nn = ribbon[-2:]
        for k, (cell, zn) in enumerate(zip(cells, zone_names)):
            # The zone file itself (as FH1's levels list theirs), then the files to stream
            # with the zone: its models' <prefix>.NNNNN.rmb.bin (type 9, as Colorado's;
            # resource number = file number).
            cell["files"] = [(k, k, 1, index[zn])]
            for m in cell["models"]:
                f = f"{P['prefix']}.{m:05d}.rmb.bin"
                if f in index:
                    cell["files"].append((index[f], index[f], 9, index[f]))
            # And its textures' own files (see ZONE_TEXTURES): _0x<id>.bix and _0x<id>_B.bix.
            listed = set()
            for t in (cell["textures"] if ZONE_TEXTURES else ()):
                own = F["textures"][t][0]
                for f in texture_files(own):
                    if f in index and f not in listed:
                        listed.add(f)
                        cell["files"].append((index[f], index[f], 9, index[f]))
            w = Writer()
            write_cell(w, cell, P["g2"])
            zones[zn] = bytes(w.b)
        open(path, "wb").write(write_fh1(P))
        open(os.path.join(root, ribbon, f"{track}_{nn}.hex"), "wb").write(grid.write())
        open(os.path.join(root, ribbon, f"FilenameMap_{nn}.dat"), "wb").write(
            write_filename_map(names))
        open(os.path.join(root, ribbon, f"PVSZLookup_{nn}.dat"), "wb").write(
            b"".join(struct.pack(">II", index[zn], k) for k, zn in enumerate(zone_names)))
        print(f"{ribbon}: {len(P['instances'])} instances, {len(P['models'])} models, "
              f"{len(P['textures'])} textures; {len(cells)} zones ({grid.w}x{grid.h} hexes of "
              f"{grid.r:.0f} m) of {P['zone_reach']}, "
              f"{sum(len(c['instances']) for c in cells) / len(cells):.0f} "
              f"instances each on average, the largest {max(len(c['instances']) for c in cells)} "
              f"instances, {max(len(c['models']) for c in cells)} models, "
              f"{max(len(c['textures']) for c in cells)} textures")
    # The static pack's texture numbers: pack entry k is texture file k; its record is the
    # first texture record of that file.
    # A file shared by records with a texture file of their own (flag 0x4: streamed) and atlas
    # tiles (0x8 without 0x4: drawn from the pack) gives its entry to the first tile: the pack's
    # texture is registered under that record's number, which only a pack-drawn record's object
    # looks up (a streamed record's object holds its own file's resource id instead). An entry
    # given to a streamed record (tried: Laguna Seca's file 857 to a lightmap) leaves the pack
    # texture where no object finds it.
    first = {}
    for i, t in enumerate(F["textures"]):
        k = first.get(t[1])
        if k is None or (F["textures"][k][6] & 0x4 and not t[6] & 0x4):
            first[t[1]] = i
    numbers = [first.get(k, 0xFFFFFFFF) for k in range(max(first) + 1)]
    # A test (FM4_PVS_PACK_IDS=1): pack entry k is registered under the id len(archive) + k, past
    # every archive file's (the streaming resource table holds both; a record number can equal a
    # model file's index), and every record with no file of its own takes its entry's id
    # (<track>_stx_files.bin then holds the id, the game sets the record object's resource id).
    pack_ids = os.environ.get("FM4_PVS_PACK_IDS") == "1"
    own_files = {int(p.split("_0x")[1][:8], 16) for p in order if "_0x" in p and p.lower().endswith(".bix")}
    own_tile_files = {int(p.split("_0x")[1][:8], 16) for p in order
                      if "_0x" in p and p.lower().endswith(".bix") and "_b.bix" not in p.lower()}
    if pack_ids:
        numbers = [len(order) + k if v != 0xFFFFFFFF else 0xFFFFFFFF for k, v in enumerate(numbers)]
    os.makedirs(loose, exist_ok=True)
    with open(os.path.join(loose, f"{track}_stx_numbers.bin"), "wb") as f:
        f.write(struct.pack(f">{len(numbers)}I", *numbers))
    # FM4's atlas tiles: a record from an atlas (flag 0x8) with no texture file of its own
    # (no 0x4: nothing streamed for it) is drawn from its atlas, the pack entry of its file.
    # FH1 ties one record to each pack entry (the numbers above), so every other tile of that
    # atlas had a texture object that never got its texture: a 32 x 32 stand-in (Laguna Seca's
    # road lightmaps and masks, flag 9; drawn black). <track>_stx_files.bin, a u32 per record,
    # gives such a record the pack entry it is drawn from (-1 for the others); the game
    # (hooks/fm4_track.cpp) points those records at their atlas's texture object.
    if pack_ids:
        files_of = [numbers[t[1]] if t[1] < len(numbers) and t[0] not in own_files else 0xFFFFFFFF
                    for t in F["textures"]]
    else:
        files_of = [t[1] if (t[6] & 0xC) == 0x8 and numbers[t[1]] != i else 0xFFFFFFFF
                    for i, t in enumerate(F["textures"])]
    with open(os.path.join(loose, f"{track}_stx_files.bin"), "wb") as f:
        f.write(struct.pack(f">{len(files_of)}I", *files_of))
    # <track>_stx_tiles.bin: for the game to fill a model's texture slots that are atlas tiles
    # (hooks/fm4_track.cpp, FillAtlasTiles): u32 model count, then per model, in the model
    # numbers the game uses (a model texture table's +4 points at that model's record): its
    # slot count (0: no tile) and per slot its pack entry (-1: not a tile) and UV rectangle
    # (4 floats as u32: scale u, v, offset u, v; the game checks them against the table's).
    # A streamed tile (0xC: a file of its own with the tile alone, the atlas in the pack until
    # it streams in) has its entry with the top bit set: filled only while it holds nothing.
    # The models are ribbon 0's (the layout free roam loads); a rectangle alone cannot name
    # the atlas (the atlases share one grid: 22% of Sunset Infield's tables were matched to
    # another model's atlases that way).
    F0 = converted[0][2]
    tile_entry = [(t[1] | (0x80000000 if t[6] & 0x4 or t[0] in own_tile_files else 0))
                  if t[6] & 0x8 and t[1] < len(numbers) and numbers[t[1]] != 0xFFFFFFFF
                  else 0xFFFFFFFF for t in F0["textures"]]
    words = [len(F0["models"])]
    for M in F0["models"]:
        ents = [tile_entry[t & 0xFFFFFF] for t in M["textures"]]
        if all(e == 0xFFFFFFFF for e in ents):
            words.append(0)
            continue
        words.append(len(ents))
        for t, e in zip(M["textures"], ents):
            r = F0["textures"][t & 0xFFFFFF]
            words += [e] + [struct.unpack(">I", struct.pack(">f", v))[0] for v in r[2:6]]
    with open(os.path.join(loose, f"{track}_stx_tiles.bin"), "wb") as f:
        f.write(struct.pack(f">{len(words)}I", *words))
    # <track>_stx_lmuv.bin: the models (ribbon 0's numbers) whose pixel shaders read
    # Light_Map_uvOS (PS c116) but whose command buffers never write it: FM4 sets their
    # instance lightmap's rectangle there at run time, FH1 does not (its shaders read their
    # lightmap with no uvOS). No game code reads it now (writing c116 from a hook had no
    # effect); the converter draws those models with their rectangles itself
    # (lightmap_rect_copies, lightmap_uv_copies). u32 count, then per model its number and
    # texture slot count.
    lmuv = lightmap_uv_models(F0, bin_dir)
    words = [len(lmuv)]
    for m in lmuv:  # the model and its texture slot count (the table holds one more: the
        words += [m, len(F0["models"][m]["textures"])]  # instance's own lightmap)
    with open(os.path.join(loose, f"{track}_stx_lmuv.bin"), "wb") as f:
        f.write(struct.pack(f">{len(words)}I", *words))
    print(f"{track}: {len(lmuv)} models get their lightmap rectangle from the game")
    print(f"{track}: {sum(v != 0xFFFFFFFF for v in files_of)} atlas tiles drawn from their atlas")
    members = [(p, zones[p] if p in zones else open(stand_in, "rb").read()
                if p.lower().endswith(".stx.bin") and stand_in
                else open(os.path.join(bin_dir, p), "rb").read()) for p in order]
    # FM4's track shaders with the inputs FH1 does not give them made neutral
    # (tools/fm4_shaders.py; FM4_PVS_SHADER_PATCHES=0 leaves them as they are).
    if os.environ.get("FM4_PVS_SHADER_PATCHES", "1") == "1":
        fm4_shaders.ATLAS_MASK_SHADERS.clear()
        # The game gives atlas tiles their atlas (FillAtlasTiles, <track>_stx_tiles.bin), so the
        # masks are drawn as FM4 draws them; FM4_PVS_ATLAS_MASK=1 patches them to 1 as before.
        for c in (converted if os.environ.get("FM4_PVS_ATLAS_MASK", "0") == "1" else []):
            fm4_shaders.ATLAS_MASK_SHADERS.update(atlas_mask_shaders(c[2]))
        if fm4_shaders.ATLAS_MASK_SHADERS:
            print(f"{track}: blend masks from an atlas in {sorted(fm4_shaders.ATLAS_MASK_SHADERS)}")
        members = [(p, fm4_shaders.patch_fxobj(p, d)[0] if p.lower().endswith(".fxobj") else d)
                   for p, d in members]
    members = [(p, fm4_shaders.lightmap_uv_identity(d)[0] if p in LMUV_COPIES else
                _rect_copy(d, *RECT_COPIES[p][1:]) if p in RECT_COPIES else d)
               for p, d in members]
    # The ground's blend shader copies: the mask read as the one layer (see GROUND_LAYER).
    members = [(p, fm4_shaders.one_layer(d, GROUND_LAYER == "B")
                if p in GROUND_COPIES and p.lower().endswith(".fxobj") else d) for p, d in members]
    out_dir = os.path.join(loose, "media", "tracks", track)
    os.makedirs(out_dir, exist_ok=True)
    if need:
        blocks = min(int(need * STREAM_MARGIN + 0.5), MAX_STREAM_BLOCKS)
        with open(os.path.join(out_dir, "fm4_stream_blocks.txt"), "w") as f:
            f.write(f"{blocks}\n")
        print(f"{track}: streaming blocks {need} at most, {blocks} asked for (fm4_stream_blocks.txt)")
        if not fits_stream_pool(need):
            print(f"{track}: warning: that is more than the game's {MAX_STREAM_BLOCKS} with the "
                  f"margin ({STREAM_MARGIN}); the pool may run dry")
    start, size, count = write_track_zip(members, os.path.join(out_dir, "bin.zip"))
    shutil.rmtree(bin_dir)
    manifest = open(os.path.join(fh1_disc, "media", "zipmanifest.xml"), "rb").read().decode("utf-8-sig")
    line = (f'<Zip version="1" path="game:\\media\\tracks\\{track.lower()}\\bin.zip" priority="80" '
            f'dirstart="{start}" dirsize="{size}" direntries="{count}" />')
    manifest = manifest.replace("</ZipFiles>", line + "\r\n</ZipFiles>")
    with open(os.path.join(loose, "media", "zipmanifest.xml"), "wb") as f:
        f.write(b"\xef\xbb\xbf" + manifest.encode("utf-8"))
    print(f"{out_dir}/bin.zip: {count} files; zipmanifest.xml: {line}")


def check(paths):
    bad = 0
    for p in paths:
        data = open(p, "rb").read()
        try:
            if p.lower().endswith(".pvsz"):
                for g2 in (1, 0):
                    try:
                        r = Reader(data)
                        c = read_cell(r, g2)
                        if r.p == len(data):
                            break
                    except EOFError:
                        continue
                w = Writer()
                write_cell(w, c, g2)
                ok = bytes(w.b) == data
            elif data[4:8] == struct.pack(">I", 27):
                read_fm4(data)
                ok = True
            else:
                ok = write_fh1(read_fh1(data)) == data
        except Exception as e:  # noqa: BLE001 (report and go on)
            print(f"{p}: {e}")
            ok = False
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {p}")
    return 1 if bad else 0


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "check":
        sys.exit(check(sys.argv[2:]))
    if len(sys.argv) == 5 and sys.argv[1] == "convert":
        sys.exit(convert_mod(sys.argv[2], sys.argv[3], sys.argv[4]))
    sys.exit(__doc__)
