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
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_col  # noqa: E402
import fm4_shaders  # noqa: E402

FH1_VERSION = 50


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

def read_placement(r):  # sub_82CEDD48
    e = {"lod": (r.u16(), r.u16(), r.u16()), "pos": r.floats(3),
         "rot": [r.u16() for _ in range(9)], "v": r.floats(4), "extra": None}
    if r.u8():
        x = r.u32()
        n = r.u8()
        e["extra"] = (x, r.take(n), r.take(32))  # sub_82CECFC0
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
        w.u8(1)
        w.u32(x)
        w.u8(len(lst))
        w.raw(lst)
        w.raw(tail)


def read_rec16(r):  # sub_82CF0F48 with g1: stored as +12, +0, +4, +8
    return r.u32(), r.u32(), r.u32(), r.u32()


def read_cell(r, g2):  # sub_82CF2168: a zone (.pvsz) or a cell of the PVS
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
    c["placements"] = [read_placement(r) for _ in range(r.u32())]
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
    if ver != FH1_VERSION:
        raise ValueError(f"FH1 PVS version {ver}: only 50 is read")
    P = {"hdr": (r.u32(), r.u32()), "ribbon": r.u16(), "pgeo": r.u32(), "g1": r.u8(),
         "g2": r.u8(), "resources": r.u32(), "rmodels": r.u32(), "list48": r.vec(r.u32),
         "g3": r.u8(), "a2": r.u16()}
    if not P["g1"]:
        raise ValueError("g1 = 0 (the other 16-byte record layout) is not read")
    P["cells"] = [read_cell(r, P["g2"]) for _ in range(r.u32())]
    P["textures"] = r.vec(lambda: (r.u32(), r.u32(), r.f32(), r.f32(), r.f32(), r.f32(), r.u32()))
    P["shaders"] = r.vec(r.string)
    P["instances"] = r.vec(lambda: read_fh1_instance(r))
    P["models"] = r.vec(lambda: {"textures": r.vec(r.u32), "shaders": r.vec(r.u32),
                                 "matrix": r.floats(12), "extent": r.floats(3)})
    P["always"] = r.vec(r.u32)
    P["models_a"] = r.vec(r.u16)
    P["models_b"] = r.vec(r.u16)
    P["always_placements"] = [read_placement(r) for i in P["always"] if i != 0xFFFFFFFF]
    P["dynamic"] = r.vec(lambda: read_fh1_instance(r))
    P["dynamic_placements"] = [read_placement(r) for _ in P["dynamic"]]
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


def convert(F, files):
    """FM4's PVS (read_fm4) to FH1's PVS, its one zone and the zone's file list."""
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
    sky = [i for i in F["always"] if i not in (0xFFFF, 0xFFFFFFFF)][:1] if SKY else []
    for i in sky:
        P["models"][insts[i]["model"]]["extent"] = [SKY_EXTENT] * 3
        for cell in grid.cells:
            members[cell].append(i)
    # A ground under everything (see GROUND): one flat terrain piece of FM4's own, placed
    # again stretched GROUND_SCALE times across, below the lowest ground, in every zone.
    if GROUND:
        g = ground_instance(F, used)
        if g is not None:
            if GROUND_LAYER in ("A", "B"):
                g["model"] = ground_model_copy(F, P, files, g["model"], g["reach"])
            insts = insts + [g]
            P["instances"].append(fh1_instance_of(g))
            for cell in grid.cells:
                members[cell].append(len(insts) - 1)
    zones = []
    for cell in grid.cells:
        zi = members[cell]
        models = sorted({insts[i]["model"] for i in zi})
        tex = sorted({t & 0xFFFFFF for m in models for t in F["models"][m]["textures"]} |
                     ({insts[i]["idx"] & 0xFFFFFF for i in zi if insts[i]["u12"] != 0xFFFFFFFF}
                      if LIGHTMAPS else set()))
        zones.append({"instances": [0x80000000 | i for i in zi], "models": models,
                      "textures": tex, "texture_flags": [0] * len(tex), "files": [],
                      "drawn": [1] * len(zi), "placements": [placement_of(insts[i]) for i in zi]})
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


def stream_blocks(F, cells, grid, bin_dir):
    """Blocks the zones of one ribbon need at most (see STREAM_MARGIN), without the margin."""
    size = {}
    def file_size(m):
        if m not in size:
            p = os.path.join(bin_dir, f"{F['prefix']}.{m:05d}.rmb.bin")
            size[m] = os.path.getsize(p) if os.path.exists(p) else 0
        return size[m]
    index = {c: k for k, c in enumerate(grid.cells)}
    best = 0
    for (col, row), k in index.items():
        # Odd columns sit half a hex lower (HexGrid): their side neighbours are rows row, row+1.
        side = (0, 1) if col % 2 else (-1, 0)
        near = [(col, row - 1), (col, row + 1)] + [(col + dc, row + dr) for dc in (-1, 1) for dr in side]
        models = set(cells[k]["models"])
        for c in near:
            if c in index:
                models.update(cells[index[c]]["models"])
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
    for k, si in enumerate(shaders):
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
    if not renames:
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
    for ribbon, path, data, _ in ribbons:
        F = read_fm4(data)
        P, cells, grid = convert(F, files)
        nn = ribbon[-2:]
        converted.append((ribbon, path, F, P, cells, grid,
                          [f"__R{int(nn):02d}Z{k:05d}.pvsz" for k in range(len(cells))]))
    for dst, (src, renames) in GROUND_COPIES.items():
        data = open(os.path.join(bin_dir, src), "rb").read()
        for a, b in renames:  # the shader names in the model's material table
            data = data.replace(a, b)
        os.makedirs(os.path.dirname(os.path.join(bin_dir, dst)), exist_ok=True)
        open(os.path.join(bin_dir, dst), "wb").write(data)
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
              f"{grid.r:.0f} m), the largest {max(len(c['instances']) for c in cells)} "
              f"instances, {max(len(c['models']) for c in cells)} models, "
              f"{max(len(c['textures']) for c in cells)} textures")
    # The static pack's texture numbers: pack entry k is texture file k; its record is the
    # first texture record of that file.
    first = {}
    for i, t in enumerate(F["textures"]):
        first.setdefault(t[1], i)
    numbers = [first.get(k, 0xFFFFFFFF) for k in range(max(first) + 1)]
    os.makedirs(loose, exist_ok=True)
    with open(os.path.join(loose, f"{track}_stx_numbers.bin"), "wb") as f:
        f.write(struct.pack(f">{len(numbers)}I", *numbers))
    members = [(p, zones[p] if p in zones else open(stand_in, "rb").read()
                if p.lower().endswith(".stx.bin") and stand_in
                else open(os.path.join(bin_dir, p), "rb").read()) for p in order]
    # FM4's track shaders with the inputs FH1 does not give them made neutral
    # (tools/fm4_shaders.py; FM4_PVS_SHADER_PATCHES=0 leaves them as they are).
    if os.environ.get("FM4_PVS_SHADER_PATCHES", "1") == "1":
        fm4_shaders.ATLAS_MASK_SHADERS.clear()
        for c in converted:
            fm4_shaders.ATLAS_MASK_SHADERS.update(atlas_mask_shaders(c[2]))
        if fm4_shaders.ATLAS_MASK_SHADERS:
            print(f"{track}: blend masks from an atlas in {sorted(fm4_shaders.ATLAS_MASK_SHADERS)}")
        members = [(p, fm4_shaders.patch_fxobj(p, d)[0] if p.lower().endswith(".fxobj") else d)
                   for p, d in members]
    # The ground's blend shader copies: the mask read as the one layer (see GROUND_LAYER).
    members = [(p, fm4_shaders.one_layer(d, GROUND_LAYER == "B")
                if p in GROUND_COPIES and p.lower().endswith(".fxobj") else d) for p, d in members]
    out_dir = os.path.join(loose, "media", "tracks", track)
    os.makedirs(out_dir, exist_ok=True)
    if need:
        blocks = int(need * STREAM_MARGIN + 0.5)
        with open(os.path.join(out_dir, "fm4_stream_blocks.txt"), "w") as f:
            f.write(f"{blocks}\n")
        print(f"{track}: streaming blocks {need} at most, {blocks} asked for (fm4_stream_blocks.txt)")
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
