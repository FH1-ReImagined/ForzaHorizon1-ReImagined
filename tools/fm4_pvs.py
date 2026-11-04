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
  FORZA_TRACK_SPAWN=x,y,z[,fx,fz] (Suzuka's main straight: 478,604.5,14,0.57,-0.82)
  FORZA_FM4_COL=1 (the collision read whole from the converted .col files)
  FORZA_FM4_ZONE_POOL=4 (more live instance objects: the hex zones share few instances)
and FM4_DISC=<FM4's disc folder> when converting, for the surface numbers (tools/fm4_col.py).
Status: Suzuka renders (buildings, stands, crowds, trees, grass, fences, flags, the road)
and drives (collision: tools/fm4_col.py); FM4's terrain and road-shoulder blend shaders still
draw dark or not at all under FH1.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_col  # noqa: E402

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
                     "extent": m["extent"][:3]} for m in F["models"]],
         # FM4's always-drawn instances and the two model lists are its sky domes (one per
         # time of day); FH1 draws its own sky, and its loader waits forever on them.
         "always": [], "models_a": [], "models_b": [],
         "dynamic": [fh1_instance_of(i) for i in F["dynamic"]],
         "prefix": F["prefix"], "b184": F["b160"], "b216": F["b196"], "names": F["names"],
         "pairs": [], "b228": 1}
    P["always_placements"] = []
    P["dynamic_placements"] = [placement_of(i) for i in F["dynamic"]]
    # Every instance of FM4's cells, placed in FH1's hex zones by where it is: its own
    # position, or for a mesh placed by its model (FM4's instances at 0,0,0) the model's.
    used = sorted({i for c in F["cells"] for i in c["instances"]})
    def where(i):
        pos = insts[i]["pos"]
        if any(pos):
            return pos[0], pos[2]
        m = F["models"][insts[i]["model"]]["matrix"]
        return m[12], m[14]
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
    return P, zones, grid


# Zone size: FH1's Colorado has 100 m hexes; its loader streams the camera's zone and the six
# around it through a 23 MB buffer, which 250 m zones of Suzuka overflow.
HEX_RADIUS = float(os.environ.get("FM4_PVS_HEX", "100"))


class HexGrid:
    """FH1's zone grid (a <track>_NN.hex file, HEXY version 101): flat-topped hexes of a
    radius, in columns 1.5 radii apart, odd columns half a hex lower; the centre of (col, row)
    is origin + (2 + 1.5 col, sqrt3/2 + sqrt3 row + sqrt3/2 (col odd)) radii (fitted to
    Colorado's zones and their contents). Every cell of the grid is a zone here."""

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
        return (self.ox + (2 + 1.5 * c) * self.r, self.oz + h / 2 + h * r + (h / 2 if c & 1 else 0))

    def cell_of(self, x, z):
        c0 = int((x - self.ox - 2 * self.r) / (1.5 * self.r))
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
    out_dir = os.path.join(loose, "media", "tracks", track)
    os.makedirs(out_dir, exist_ok=True)
    start, size, count = write_track_zip(members, os.path.join(out_dir, "bin.zip"))
    import shutil
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
