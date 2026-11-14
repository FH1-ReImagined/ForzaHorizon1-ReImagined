#!/usr/bin/env python3
"""One world from Forza Horizon 2 Presents Fast & Furious's and Forza Horizon 2's (#821).

F&F's world is FH2's southern Europe cut to an area round Nice and edited throughout; FH2's is
the whole map. Both are media/tracks/Anthem on the same hex grid (F&F's is FH2's first 66 of 104
columns). F&F's docks are its own: 24 cells FH2 has no zone in, and 37 round them where FH2 has
an older dock area of its own (8 to 30% of their placements in common, F&F with two to three
times the models; elsewhere the two worlds are 50 to 90% the same). This writes a third world
folder in their own format (layout version 51), which tools/fh2ff_world.py then builds as it
builds either:

  tools/fh2ff_combine.py BASE_DISC ADDED_DISC OUT_DISC [--similar 0.4]
  tools/fh2ff_world.py build OUT_DISC FH1_DISC UPDATE_MEDIA_ZIP OUT

With FH2 as the base and F&F added: FH2's world with F&F's docks (61 cells, changed_region). With
F&F as the base: F&F's world with FH2's zones round it (those faulted in FH1 so far). Below, "F&F"
stands for the base (A) and "FH2" for the added world (B), as first written. --similar 0 takes
only the cells the base has no zone in.

OUT_DISC gets media/tracks/Anthem (the combined world), and the base's database, physics and AI
lines. What is combined (docs/fh2ff-world.md has the measurements):

- The region: FH2's zones in the cells F&F has none in and in those round them FH2 rebuilt;
  F&F's everywhere else. Round the region, F&F's zones lose their instances and procedural
  geometry standing in it and see FH2's there as FH2's zone of the same cell does.
- The layout: F&F's tables (its instances whole when they fit), then FH2's after them. Shaders
  are shared by name. An FH2 texture that is byte for byte one of F&F's (5,072 of F&F's 10,233
  against FH2's) is F&F's record; the rest are records of their own, their ids moved
  (TEXTURE_ID_STEP).
- FH2's files take numbers and ids after F&F's where their name holds one: model files
  (aout.NNNNN.rmb.bin), procedural geometry (__r00gNNNNN.pgeo), textures of its own (_0x<id>.bix,
  _B.bix, .bin), bundles (_0x1000NNNN.bundle, with only its own textures), zones (__R00ZNNNNN.pvsz,
  anthem_00_NNNNN.soundscape) and collision cells (N.fiz, by the collision index's records).
  Collision cells and shadow squares (.sh, named by place) the region's zones name are FH2's.
- FH2's procedural geometry names textures by record number, other procedural geometry by number (a
  scene's animated objects) and, in Models files, layout instances by number: all moved the same
  way (pgeo_parts). Left alone, F&F's docks drew with FH2's textures and without their placed
  models.
- A zone's file records (own id, resource number, type, file) are renumbered the same way; FH2's
  resource numbers move past F&F's, and a file both worlds name is F&F's, with F&F's numbers.
- The hex grid, the zone lookup and speeds, the collision index, the road network (merge_nav),
  CollObjs.xml (merge_collobjs: FH2's road closures in the region left out, with their
  instances) and SmashableEffects.xml hold both worlds'. The rest of the world folder (lighting,
  ribbon XML, races) is F&F's.

What it reads and writes is the player's own: never share the output.
"""
import hashlib
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fm4_col  # noqa: E402
import fm4_pvs  # noqa: E402
import fm4_routes  # noqa: E402
import fh2ff_world as ff  # noqa: E402

NAME = ff.NAME
TEXTURE_ID_STEP = 0x2900       # FH2's texture ids move this far: past F&F's (to 10,403), and all
                               # under 21,000 as Colorado's are (to 20,519). Moved 0x10000 (to 75,800),
                               # FH1 corrupted its memory near F&F's edge (a list of the main loop, lr 82C12C10)
REC = ">6I"                    # a collision index record: number, cell, offset, size, 0, end
# Test switches (FH2FF_COMBINE_TEST, comma list): "noshare" every added texture a record of its
# own, "noprune" the base's files of retired zones kept, "noseam" the base's
# zones round the region as they are, "nosh" the base's shadow squares kept, "nocol" the base's
# collision files kept, "noobj" the base's CollObjs.xml and SmashableEffects.xml as they are
TESTS = set(filter(None, os.environ.get("FH2FF_COMBINE_TEST", "").split(",")))
FIZ, ZONE, PGEO, SHADOW, SOUNDSCAPE, BUNDLE = 0, 1, 2, 3, 4, 7


# ---- the layout ----

def write_layout_51(P):
    """fm4_pvs.write_fh1 for version 51 (FH2's: a placement's route numbers are u16)."""
    w = fm4_pvs.Writer()

    def placement(e):
        for v in e["lod"]:
            w.u16(v)
        w.floats(e["pos"])
        for v in e["rot"]:
            w.u16(v)
        w.floats(e["v"])
        if e["extra"] is None:
            w.u8(0)
            return
        x, routes, tail = e["extra"]
        w.u8(1)
        w.u32(x)
        w.u8(len(routes))
        for r in routes:
            w.u16(r)
        w.raw(tail)
    real = fm4_pvs.write_placement
    fm4_pvs.write_placement = lambda _w, e: placement(e)   # write_cell's placements, as 51
    try:
        w.raw(b"FPVS"); w.u32(fm4_pvs.FH2_VERSION); w.u32(P["hdr"][0]); w.u32(P["hdr"][1])
        w.u16(P["ribbon"]); w.u32(P["pgeo"]); w.u8(P["g1"]); w.u8(P["g2"])
        w.u32(P["resources"]); w.u32(P["rmodels"]); w.vec(P["list48"], w.u32)
        w.u8(P["g3"]); w.u16(P["a2"])
        w.u32(len(P["cells"]))
        for c in P["cells"]:
            fm4_pvs.write_cell(w, c, P["g2"])
        w.vec(P["textures"], lambda t: (w.u32(t[0]), w.u32(t[1]), w.floats(t[2:6]), w.u32(t[6])))
        w.vec(P["shaders"], w.string)
        w.vec(P["instances"], lambda i: fm4_pvs.write_fh1_instance(w, i))
        w.vec(P["models"], lambda m: (w.vec(m["textures"], w.u32), w.vec(m["shaders"], w.u32),
                                      w.floats(m["matrix"]), w.floats(m["extent"])))
        w.vec(P["always"], w.u32)
        w.vec(P["models_a"], w.u16)
        w.vec(P["models_b"], w.u16)
        for e in P["always_placements"]:
            placement(e)
        w.vec(P["dynamic"], lambda i: fm4_pvs.write_fh1_instance(w, i))
        for e in P["dynamic_placements"]:
            placement(e)
        w.string(P["prefix"])
        w.u8(P["b184"]); w.u8(P["b216"])
        w.vec(P["names"], w.string)
        w.vec(P["pairs"], w.raw)
        w.u8(P["b228"])
        w.vec(P["zones"], lambda z: [w.u32(v) for v in z])
    finally:
        fm4_pvs.write_placement = real
    return bytes(w.b)


def write_cell_51(c, g2):
    w = fm4_pvs.Writer()
    real = fm4_pvs.write_placement

    def placement(_w, e):
        for v in e["lod"]:
            w.u16(v)
        w.floats(e["pos"])
        for v in e["rot"]:
            w.u16(v)
        w.floats(e["v"])
        if e["extra"] is None:
            w.u8(0)
            return
        x, routes, tail = e["extra"]
        w.u8(1); w.u32(x); w.u8(len(routes))
        for r in routes:
            w.u16(r)
        w.raw(tail)
    fm4_pvs.write_placement = placement
    try:
        fm4_pvs.write_cell(w, c, g2)
    finally:
        fm4_pvs.write_placement = real
    return bytes(w.b)


def hex_cells(data):
    """{(column, row): zone} of a hex file, and its header."""
    ver, side, ox, oz, zones, w, h = struct.unpack_from(">ifffiii", data, 4)
    cells = struct.unpack_from(f">{w * h}i", data, 32)
    return {(k % w, k // w): v for k, v in enumerate(cells) if v >= 0}, (ver, side, ox, oz, w, h)


MODELS = 7                     # procedural geometry type 7: placed copies of layout models


def pgeo_parts(d):
    """Where a procedural geometry file (FH2's layout, before fh2ff_world converts it) keeps the
    references that name the rest of its world: {"tex": (offset, count), "geo": ..., "inst": ...},
    or None for a file of another kind. From the FH2 prototype's PDB (proceduralGeometry::
    CProceduralGeometry: CGeometryData's m_NumTextures +0x40, m_NumGeometries +0x44, m_NumModels
    +0x54; CTextureRef = (texture record number, settings), CGeometryRef = (file number, name), CModelRef =
    64 bytes, its m_Name size at +32 and three (instance count, list) LODs at +36), laid out as
    read from both worlds' files (checked on all 4,465 of F&F's and 11,219 of FH2's):
    - the texture references follow the name (4-byte aligned), the geometry references them;
    - in a Models file (type 7) the name is followed (16-byte aligned) by its model references,
      every reference's instance lists (u32 layout instance numbers, the references in order),
      their names, and then (4-byte aligned) the texture and geometry references.
    A Models file's instances give the models it places; its textures, like a scene's or the
    vegetation's, are texture record numbers: in both worlds all are under the record count,
    which the ids pass (the records' ids only name their files)."""
    if d[:4] != b"OEGP":
        return None
    kind = struct.unpack_from(">I", d, 0x30)[0]
    nlen, ntex, ngeo = struct.unpack_from(">3I", d, 0x3C)
    nmod = struct.unpack_from(">I", d, 0x54)[0]
    base = 0x60 + (ff.DYN_FH2 if struct.unpack_from(">I", d, 0x58)[0] else 0)
    inst = (0, 0)
    if kind == MODELS and nmod:
        at = (base + nlen + 15) & ~15
        refs = [struct.unpack_from(">7I", d, at + 64 * k + 32) for k in range(nmod)]
        at += 64 * nmod
        count = sum(r[1] + r[3] + r[5] for r in refs)
        inst = (at, count)
        at = (at + 4 * count + sum(r[0] for r in refs) + 3) & ~3
    else:
        at = base + ((nlen + 3) & ~3)
    if at + 8 * (ntex + ngeo) > len(d) or inst[0] + 4 * inst[1] > len(d):
        raise ValueError("a procedural geometry file shorter than its references")
    return {"tex": (at, ntex), "geo": (at + 8 * ntex, ngeo), "inst": inst}


def read_refs(d, part, step=8):
    at, n = part
    return [struct.unpack_from(">I", d, at + step * k)[0] for k in range(n)]


def rewrite_pgeo(d, tex_map=None, geo_shift=0, inst_map=None):
    """A procedural geometry file with its texture records renumbered by tex_map, its geometry
    references (file numbers) by geo_shift and its Models instances renumbered by inst_map; the
    file itself when nothing changes. A scene's geometry references moved alone left its
    textures pointing at the other world's of the same id; so did vegetation's and Models'."""
    p = pgeo_parts(d)
    if p is None:
        return d
    out = bytearray(d)
    for (at, n), f, step in ((p["tex"], tex_map, 8), (p["geo"], lambda v: v + geo_shift, 8),
                             (p["inst"], (lambda v: inst_map[v]) if inst_map is not None else None, 4)):
        if f is None:
            continue
        for k in range(n):
            o = at + step * k
            struct.pack_into(">I", out, o, f(struct.unpack_from(">I", d, o)[0]))
    return d if out == d else bytes(out)


def bundle_entries(d):
    """A texture bundle's textures: [(texture record number, offset of the entry, its length)].
    The numbers are the layout's texture records', not their ids (in both worlds every entry is
    under the record count, which the ids pass: F&F's to 10,403 for 10,233 records)."""
    out, n, p = [], struct.unpack_from(">I", d, 0)[0], 4
    for _ in range(n):
        size = struct.unpack_from(">I", d, p + 24)[0]
        out.append((struct.unpack_from(">I", d, p)[0], p, 28 + size))
        p += 28 + size
    if p != len(d):
        raise ValueError("a bundle that does not end with its last texture")
    return out


def rewrite_bundle(d, record_map):
    """A bundle with the textures record_map names, each under its record's new number; None
    when none is left. Moved by the ids' step instead, F&F's later textures (their records
    past 9,977) were registered past the end of FH1's streaming resource table, which has a
    12-byte entry a texture record: it wrote over what follows, and the game faulted in its
    stream list (sub_82DD9388) as soon as the docks' bundles loaded."""
    kept = [(record_map[t], p, n) for t, p, n in bundle_entries(d) if t in record_map]
    if not kept:
        return None
    out = bytearray(struct.pack(">I", len(kept)))
    for t, p, n in kept:
        out += struct.pack(">I", t) + d[p + 4:p + n]
    return bytes(out)


def texture_data(w):
    """{texture id: (kind, signature, where)} of a world's textures: kind "f" for those with
    files of their own (the signature their suffixes and sizes, where {suffix: entry}), else
    "b" for a bundle's (the signature the entry's header, where the digest of its data: each
    bundle is read once; a bundle names its textures by record number)."""
    files = {}
    for e, n in enumerate(w.names):
        m = re.fullmatch(r"_0x([0-9a-f]{8})(.*)", n.lower())
        if m and m.group(2) != ".bundle":
            files.setdefault(int(m.group(1), 16), {})[m.group(2)] = e
    out = {t: ("f", tuple(sorted((s, w.info_of[e].file_size) for s, e in f.items())), f)
           for t, f in files.items()}
    for e, n in enumerate(w.names):
        if n.lower().endswith(".bundle"):
            d = w.zip.read(w.info_of[e])
            for k, p, length in bundle_entries(d):
                t = w.P["textures"][k][0] if k < len(w.P["textures"]) else None
                if t is not None and t not in out:
                    out[t] = ("b", d[p + 4:p + 28], hashlib.md5(d[p + 4:p + length]).digest())
    return out


def bundle_numbers(ids_a, ids_b):
    """{added world's bundle id: its id in the combined world}: a bundle id is 0x10000000 + 2k + 1,
    k the bundle's number, dense in both worlds; the added world's carry on after the base's."""
    for ids in (ids_a, ids_b):
        if sorted(ids) != [0x10000001 + 2 * k for k in range(len(ids))]:
            raise ValueError("bundle ids that are not 0x10000001, 0x10000003, ... one after another")
    return {b: b + 2 * len(ids_a) for b in ids_b}


SIMILAR = 0.4      # a cell whose two zones share fewer of their placements than this is the added world's
SMALL = 150.0      # a model at most this big (its larger half-size) stands where its centre is;
                   # bigger ones (terrain pieces) belong to no region and stay as they are


def zone_uid(info):
    return int(re.fullmatch(r"__r\d\dz(\d+)\.pvsz", info.filename.replace("\\", "/").split("/")[-1].lower()).group(1))


def placed_at(P, v, placement):
    """Where an instance stands (x, z) and its model's larger half-size: a static instance's
    placement is at the origin and its model's matrix holds its place (z the other way round),
    a dynamic one's placement holds it."""
    m = P["models"][P["instances"][v & 0x7FFFFFFF]["model"]]
    mt, ex = m["matrix"], m["extent"]
    return placement["pos"][0] + mt[9], placement["pos"][2] - mt[11], max(ex[0], ex[2])


def zone_prints(w, cells):
    """{cell: the set of its zone's placements (rounded place, height and size; models over
    400 m, the far terrain, left out)}."""
    cell_of = {uid: cell for cell, uid in cells.items()}
    P, out = w.P, {}
    for info, c in w.zones():
        cell = cell_of.get(zone_uid(info))
        if cell is None:
            continue
        s = set()
        for v, pl in zip(c["instances"], c["placements"]):
            m = P["models"][P["instances"][v & 0x7FFFFFFF]["model"]]
            mt, ex = m["matrix"], m["extent"]
            if max(ex) <= 400:
                s.add((round(pl["pos"][0] + mt[9]), round(pl["pos"][1] + mt[10]), round(pl["pos"][2] - mt[11]),
                       round(ex[0]), round(ex[2])))
        out[cell] = s
    return out


def hex_neighbours(c, r):
    """The six cells round (c, r): flat-topped columns, odd ones half a cell lower."""
    side = (r - 1, r) if c % 2 == 0 else (r, r + 1)
    return [(c, r - 1), (c, r + 1)] + [(c + dc, rr) for dc in (-1, 1) for rr in side]


def changed_region(cells_a, cells_b, prints_a, prints_b, similar=SIMILAR):
    """The cells the added world's zones take: those the base has no zone in, and the cells
    round them (cell by cell, as far as it goes) whose two zones are less alike than similar.
    F&F's docks are 24 cells FH2 has none in and 37 FH2 has its own older docks in (8 to 30%
    of their placements in common, F&F with two to three times the models), next to cells
    where the two worlds are 50 to 90% the same."""
    region = {cell for cell in cells_b if cell not in cells_a}
    todo = list(region)
    while todo:
        for n in hex_neighbours(*todo.pop()):
            if n in region or n not in cells_a or n not in cells_b:
                continue
            a, b = prints_a.get(n, set()), prints_b.get(n, set())
            if len(a & b) < similar * max(1, len(a | b)):
                region.add(n)
                todo.append(n)
    return region


class Combined:
    """The two worlds read (ff.World) and the combined one's numbering."""

    def __init__(self, ff_root, fh2_root, similar=SIMILAR):
        self.A, self.B = ff.World(ff_root), ff.World(fh2_root)
        self.roots = (ff_root, fh2_root)
        progress("worlds read")
        rb = lambda root, n: open(os.path.join(root, "Ribbon_00", n), "rb").read()  # noqa: E731
        self.hexA, self.gridA = hex_cells(rb(ff_root, f"{NAME}_00.hex"))
        self.hexB, self.gridB = hex_cells(rb(fh2_root, f"{NAME}_00.hex"))
        if self.gridA[1:4] != self.gridB[1:4]:
            raise ValueError("the two worlds' hex grids differ")
        for key in ("g1", "g2", "g3", "a2", "ribbon", "prefix", "b184", "b216", "b228"):
            if self.A.P[key] != self.B.P[key]:
                raise ValueError(f"the two worlds' layouts differ in {key}: {self.A.P[key]!r}, {self.B.P[key]!r}")
        PA, PB = self.A.P, self.B.P
        self.NM = len(PA["models"])
        self.NPG = PA["pgeo"]
        self.RES = PA["resources"]
        self.grid = fm4_pvs.HexGrid.__new__(fm4_pvs.HexGrid)
        self.grid.r, self.grid.ox, self.grid.oz = self.gridA[1], self.gridA[2], self.gridA[3]
        self.grid.w, self.grid.h = max(self.gridA[4], self.gridB[4]), max(self.gridA[5], self.gridB[5])
        # FH2's zones in its region (the cells F&F has none in, and those round them FH2 rebuilt),
        # numbered after F&F's
        forced = os.environ.get("FH2FF_COMBINE_REGION")   # a test: "c0-c1,r0-r1", the added world's there
        if forced:
            (c0, c1), (r0, r1) = (tuple(int(v) for v in part.split("-")) for part in forced.split(","))
            self.region = {cell for cell in self.hexB if c0 <= cell[0] <= c1 and r0 <= cell[1] <= r1}
        elif similar == "all":
            self.region = set(self.hexB)
        elif similar:
            self.region = changed_region(self.hexA, self.hexB, zone_prints(self.A, self.hexA),
                                         zone_prints(self.B, self.hexB), similar)
        else:
            self.region = {cell for cell in self.hexB if cell not in self.hexA}
        self.b_zones = sorted(uid for cell, uid in self.hexB.items() if cell in self.region)
        self.zone_new = {uid: len(PA["zones"]) + k for k, uid in enumerate(self.b_zones)}
        progress(f"region: {len(self.region)} cells, {sum(1 for c in self.region if c in self.hexA)} of them "
                 f"the base's own zones replaced")
        self.match_textures()
        progress(f"textures matched: {self.twins} of the added world's {len(PB['textures'])} are the base's")
        # bundles: their ids are 0x10000000 + 2k + 1, k their number (dense in both worlds: F&F's
        # 88, FH2's 173), so FH2's carry on after F&F's. Moved 0x8000 instead, FH2's first was
        # number 16,384 of 261.
        self.bundle_new = bundle_numbers([int(n[3:11], 16) for n in self.A.names if n.lower().endswith(".bundle")],
                                         [int(n[3:11], 16) for n in self.B.names if n.lower().endswith(".bundle")])
        if max(t[0] for t in PA["textures"]) >= TEXTURE_ID_STEP:
            raise ValueError("the base's texture ids reach the added world's moved ones")

    def match_textures(self):
        """FH2's texture records in the combined world. One that is F&F's byte for byte (the
        same files, or the same bundled data, and the same record: rectangle and flags; 5,072
        of F&F's 10,233 are FH2's under another id) is F&F's record; the rest are records of
        their own after F&F's, their ids moved by TEXTURE_ID_STEP. ("noshare", a test: all of
        them their own.)
        Sets index ({FH2 record: combined record}), ids ({FH2 id: combined id}), own (FH2's
        records kept, in order) and twins (how many are F&F's)."""
        PA, PB = self.A.P, self.B.P
        if "noshare" in TESTS:     # a test: every added texture a record of its own
            n = len(PA["textures"])
            self.index = {k: n + k for k in range(len(PB["textures"]))}
            self.ids = {t[0]: t[0] + TEXTURE_ID_STEP for t in PB["textures"]}
            self.own = list(range(len(PB["textures"])))
            self.own_ids = set(self.ids)
            self.twins = 0
            return
        da, db = texture_data(self.A), texture_data(self.B)
        rec_a = {}
        for k, t in enumerate(PA["textures"]):
            rec_a.setdefault(t[0], k)
        by_sig = {}
        for tid, (kind, sig, _) in da.items():
            if tid in rec_a:
                by_sig.setdefault((kind, sig), []).append(tid)
        cache = {}

        def digest(w, data, tid):
            key = (id(w), tid)
            if key not in cache:
                kind, _, where = data[tid]
                cache[key] = (tuple(hashlib.md5(w.zip.read(w.info_of[where[s]])).digest() for s in sorted(where))
                              if kind == "f" else where)
            return cache[key]

        self.index, self.ids, self.own = {}, {}, []
        for k, t in enumerate(PB["textures"]):
            twin = None
            if t[0] in db:
                kind, sig, _ = db[t[0]]
                for a_id in by_sig.get((kind, sig), ()):
                    if PA["textures"][rec_a[a_id]][2:] == t[2:] and digest(self.A, da, a_id) == digest(self.B, db, t[0]):
                        twin = a_id
                        break
            if twin is not None:
                self.index[k] = rec_a[twin]
                self.ids[t[0]] = twin
            else:
                self.index[k] = len(PA["textures"]) + len(self.own)
                self.ids[t[0]] = t[0] + TEXTURE_ID_STEP
                self.own.append(k)
        self.own_ids = {PB["textures"][k][0] for k in self.own}
        self.twins = len(PB["textures"]) - len(self.own)

    def tex(self, ref):
        """An FH2 texture reference (a record number, flags in its top byte) in the combined world."""
        return (ref & 0xFF000000) | self.index.get(ref & 0xFFFFFF, ref & 0xFFFFFF)

    def in_region(self, x, z):
        """Whether (x, z) is in a cell whose zone is the added world's (off the grid: no)."""
        try:
            return self.grid.cell_of(x, z) in self.region
        except TypeError:          # cell_of finds no cell off the grid
            return False

    def close(self):
        self.A.zip.close()
        self.B.zip.close()
        if getattr(self, "spool", None) is not None:
            self.spool.close()

    # -- names: FH2's file name in the combined world, or None when F&F's is used --
    def b_name(self, name, col_new, shadows_A):
        low = name.lower()
        m = re.fullmatch(r"(aout)\.(\d+)\.rmb\.bin", low)
        if m:
            return f"{m.group(1)}.{int(m.group(2)) + self.NM:05d}.rmb.bin"
        m = re.fullmatch(r"__r(\d\d)g(\d+)\.pgeo", low)
        if m:
            return f"__R{m.group(1)}G{int(m.group(2)) + self.NPG:05d}.pgeo"
        m = re.fullmatch(r"__r(\d\d)z(\d+)\.pvsz", low)
        if m:
            uid = int(m.group(2))
            return f"__R{m.group(1)}Z{self.zone_new[uid]:05d}.pvsz" if uid in self.zone_new else None
        m = re.fullmatch(rf"{NAME.lower()}_(\d\d)_(\d+)\.soundscape", low)
        if m:
            uid = int(m.group(2))
            return f"{NAME.lower()}_{m.group(1)}_{self.zone_new[uid]:05d}.soundscape" if uid in self.zone_new else None
        m = re.fullmatch(r"(\d+)\.fiz", low)
        if m:
            k = int(m.group(1))
            return f"{col_new[k]}.fiz" if k in col_new else None
        if low.endswith(".sh"):
            return None if low in shadows_A else name
        m = re.fullmatch(r"_0x([0-9a-f]{8})(.*)", low)
        if m:
            v, rest = int(m.group(1), 16), name[11:]
            if low.endswith(".bundle"):
                return f"_0x{self.bundle_new[v]:08X}{rest}"
            if v in self.ids:
                # FH2's texture of its own: its files under the moved id; one of F&F's: F&F's
                return f"_0x{self.ids[v]:08X}{rest}" if v in self.own_ids else None
        return name


_COLLOBJ = re.compile(r'(\t*)<Obj(\d+) PhysicsType="([^"]*)" GraphicsName="#\d+">(\s*<Pos x="([-\d.e]+)" '
                      r'y="([-\d.e]+)" z="([-\d.e]+)".*?)</Obj\2>', re.S)


def merge_collobjs(xml_a, xml_b, inside):
    """CollObjs.xml (physics objects, tied to the zones' instances by position): F&F's objects
    except where inside(x, z) is true (FH2's region), and FH2's objects there, all numbered
    again in that order (each object's GraphicsName is "#" and its number). FH2's road closures
    there (fh2ff_world.CLOSURES) that F&F has not at the same place (one of that type within a
    metre) are left out, as F&F's own world leaves its own out where FH2 has none. Returns (the
    file, the positions of the closures left out)."""
    at_a = {}
    for m in _COLLOBJ.finditer(xml_a):
        at_a.setdefault((m.group(3).lower(), round(float(m.group(5))), round(float(m.group(7)))), []).append(
            (float(m.group(5)), float(m.group(7))))

    def in_a(t, x, z):
        return any(abs(ox - x) < 1.0 and abs(oz - z) < 1.0 for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                   for ox, oz in at_a.get((t.lower(), round(x) + dx, round(z) + dz), ()))
    for name, xml in (("the base's", xml_a), ("the added world's", xml_b)):
        if len(re.findall(r"<Obj\d+[ >]", xml)) != sum(1 for _ in _COLLOBJ.finditer(xml)):
            raise ValueError(f"{name} CollObjs.xml holds an object in a form not read: it would be left out")
    kept, closures = [], []
    for m in _COLLOBJ.finditer(xml_a):
        if not inside(float(m.group(5)), float(m.group(7))):
            kept.append(m)
    for m in _COLLOBJ.finditer(xml_b):
        x, y, z = float(m.group(5)), float(m.group(6)), float(m.group(7))
        if not inside(x, z):
            continue
        if ff.CLOSURES.search(m.group(3)) and not in_a(m.group(3), x, z):
            closures.append((x, y, z))
            continue
        kept.append(m)
    body = "".join(f'{m.group(1)}<Obj{k} PhysicsType="{m.group(3)}" GraphicsName="#{k}">{m.group(4)}</Obj{k}>\n'
                   for k, m in enumerate(kept))
    first = _COLLOBJ.search(xml_a)
    start = first.start() if first else xml_a.rindex("</CollObjs>")
    return xml_a[:start] + body + xml_a[xml_a.rindex("</CollObjs>"):], closures


def merge_nav(net_a, net_b, inside, join=1.5):
    """The road network (fm4_routes.read_nav's dict) with F&F's ways where inside(x, z) is false
    and FH2's where it is true: F&F's ways cut at the region's edge (their parts outside kept),
    FH2's parts inside, each continued to its first node outside, which is F&F's node there when
    one is within join metres (F&F's docks roads meet FH2's at 3 such places, all within a
    metre). Nodes no way uses are left out; FH2's nodes take ids after F&F's; tag names and
    values are F&F's, then FH2's others; memberships are laid out again node by node, ways in
    order (as both worlds' files are)."""
    def ways_of(net):
        for count, first, tc, tf in net["ways"]:
            yield net["node_list"][first:first + count], net["tags"][tf:tf + tc]

    def runs(idx, keep):
        out, run = [], []
        for n, k in zip(idx, keep):
            if k:
                run.append(n)
            elif run:
                out.append(run)
                run = []
        if run:
            out.append(run)
        return out
    pos_a = net_a["nodes"]
    near = {}
    for k, nd in enumerate(pos_a):
        near.setdefault((round(nd[1] / 4), round(nd[3] / 4)), []).append(k)

    def a_node_at(x, z):
        best = None
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for k in near.get((round(x / 4) + dx, round(z / 4) + dz), ()):
                    d = math.hypot(pos_a[k][1] - x, pos_a[k][3] - z)
                    if d < join and (best is None or d < best[0]):
                        best = (d, k)
        return best and best[1]
    pos_b = net_b["nodes"]
    near_b = {}
    for nd in pos_b:
        near_b.setdefault((round(nd[1] / 4), round(nd[3] / 4)), []).append(nd)

    def b_has(x, z):
        return any(math.hypot(nd[1] - x, nd[3] - z) < join for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                   for nd in near_b.get((round(x / 4) + dx, round(z / 4) + dz), ()))
    ways = []                      # ([("a" or "b", node)], world, its tags)
    for idx, tags in ways_of(net_a):
        # F&F's road stays where FH2 has the same road (a node of FH2's on it): its types and
        # traffic. FH2's own marks its roads near its edge closed and empty, and traffic stood
        # still where they met F&F's.
        keep = [not inside(pos_a[n][1], pos_a[n][3]) or b_has(pos_a[n][1], pos_a[n][3]) for n in idx]
        for run in runs(idx, keep):
            if len(run) > 1:
                ways.append(([("a", n) for n in run], "a", tags))
    for idx, tags in ways_of(net_b):
        # FH2's roads in the region that F&F has not
        ins = [inside(pos_b[n][1], pos_b[n][3]) and a_node_at(pos_b[n][1], pos_b[n][3]) is None for n in idx]
        if not any(ins):
            continue
        grow = [ins[k] or (k > 0 and ins[k - 1]) or (k + 1 < len(ins) and ins[k + 1]) for k in range(len(idx))]
        for run in runs(list(range(len(idx))), grow):
            nodes = []
            for k in run:
                n = idx[k]
                a = None if ins[k] else a_node_at(pos_b[n][1], pos_b[n][3])
                nodes.append(("a", a) if a is not None else ("b", n))
            if len(nodes) > 1:
                ways.append((nodes, "b", tags))
    # strings: both tables sorted, as both worlds' are (the game searches them as sorted ones:
    # fh2ff_world.fix_tags)
    keys = sorted(set(net_a["keys"]) | set(net_b["keys"]))
    values = sorted(set(net_a["values"]) | set(net_b["values"]))
    key_at, value_at = {s: k for k, s in enumerate(keys)}, {s: k for k, s in enumerate(values)}
    tag_map = {"a": ([key_at[s] for s in net_a["keys"]], [value_at[s] for s in net_a["values"]]),
               "b": ([key_at[s] for s in net_b["keys"]], [value_at[s] for s in net_b["values"]])}

    def tags_in(world, tags):
        k_of, v_of = tag_map[world]
        return [(k_of[t], v_of[v]) for t, v in tags]
    # nodes: F&F's used ones in their order, then FH2's
    used = {n for nodes, _, _ in ways for n in nodes}
    order = [("a", k) for k in range(len(pos_a)) if ("a", k) in used] + \
            sorted((n for n in used if n[0] == "b"), key=lambda n: n[1])
    new_of = {n: k for k, n in enumerate(order)}
    next_id = max((nd[0] for nd in pos_a), default=-1) + 1
    tags_out, node_tags = [], []
    for world, k in order:
        net = net_a if world == "a" else net_b
        nd = net["nodes"][k]
        own = net["tags"][nd[7]:nd[7] + nd[6]]
        node_tags.append((len(tags_out) if own else 0, len(own)))     # untagged: 0, as both worlds'
        tags_out += tags_in(world, own)
    way_rows, node_list, members = [], [], [[] for _ in order]
    for w, (nodes, world, tags) in enumerate(ways):
        first_tag = len(tags_out)
        tags_out += tags_in(world, tags)
        way_rows.append((len(nodes), len(node_list), len(tags), first_tag))
        for i, n in enumerate(nodes):
            node_list.append(new_of[n])
            members[new_of[n]].append((w, i))
    node_rows, memberships = [], []
    for k, (world, n) in enumerate(order):
        nd = (net_a if world == "a" else net_b)["nodes"][n]
        node_id = nd[0] if world == "a" else next_id + n
        node_rows.append((node_id, nd[1], nd[2], nd[3], len(members[k]), len(memberships),
                          node_tags[k][1], node_tags[k][0]))
        memberships += members[k]
    return {"nodes": node_rows, "ways": way_rows, "node_list": node_list, "memberships": memberships,
            "tags": tags_out, "keys": keys, "values": values}


def merge_smashable_effects(xml_a, xml_b):
    """SmashableEffects.xml with FH2's pairs for the smashables F&F names none for."""
    pair = re.compile(r'[ \t]*<FileEffectPair smashableName="([^"]*)"[^>]*/>\r?\n?')
    have = {m.group(1).lower() for m in pair.finditer(xml_a)}
    extra = [m.group(0) if m.group(0).endswith("\n") else m.group(0) + "\n"
             for m in pair.finditer(xml_b) if m.group(1).lower() not in have]
    end = xml_a.rindex("</Track>")
    return xml_a[:end] + "".join(extra) + xml_a[end:]


CELL_HEAD = 0x90               # a collision cell's header (tools/fm4_col.py has the layout)
CELL_IDS = 0x3C                # in it: a list of u16 ids, its count at +0x7A
CELL_ID_COUNT = 0x7A
ID_BITS = 0x7FFF               # a triangle's flag bits that select ids of its cell's list (bit k: id k)


def cell_ids(c):
    """A collision cell's id list. A triangle whose flags have 0x8000 is always there; one with
    low bits is there for the ids those bits select (in both games: a race's route number, its
    barriers). Each cell has its own list: 11% of F&F's cells have one, of 1 to 7 ids."""
    n = struct.unpack_from(">H", c, CELL_ID_COUNT)[0]
    if n > 15:
        raise ValueError("a collision cell with more than 15 ids")
    return struct.unpack_from(f">{n}H", c, CELL_IDS)


def read_fiz(d):
    """A collision cell file (.fiz: a 0x20 header with the sizes of its two parts, FH1's cell,
    then a second part, "OWBB") as (bounds (x0, z0, x1, z1), [(position, packed normal)] its
    vertices, [(flags, three vertex numbers, normal, word)] its triangles)."""
    if d[:4] != b"fiz ":
        raise ValueError("not a collision cell file")
    c = d[0x20:]
    nv, nt, hs, toff = struct.unpack_from(">4I", c, 0)
    if hs != CELL_HEAD or toff != hs + 16 * nv:
        raise ValueError("a collision cell not in FH1's layout")
    verts = [(struct.unpack_from(">3f", c, hs + 16 * i), struct.unpack_from(">I", c, hs + 16 * i + 12)[0]) for i in range(nv)]
    tris = []
    for i in range(nt):
        fl, a, b, e, nx, ny, nz, word = struct.unpack_from(">4I3fI", c, toff + 32 * i)
        tris.append((fl, tuple((v - hs) // 16 for v in (a, b, e)), (nx, ny, nz), word))
    return struct.unpack_from(">4f", c, 0x18), verts, tris


def retile_fiz(files, dx, dz):
    """files[0], a collision cell of a world whose 100 m grid starts (dx, dz) from the combined
    world's, cut again for the combined grid's cell of the same number: the triangles of it
    and of its neighbours (files[1:]) that reach the new square (the old one moved by -dx, -dz;
    a cell holds its 100 m and 8 m round it), their vertices, and the 12 x 12 squares' lists
    made again. The cells' id lists are not the same (cell_ids): the new cell's is the ids its
    triangles select, and each triangle's bits are set again for it. Its second part stays. F&F's grid starts 12.72 m north of FH2's: with F&F's
    cells as they were, the car fell through the road in a strip at every row's edge."""
    first = files[0]
    c = first[0x20:]
    size1, size2 = struct.unpack_from(">II", first, 8)
    gx, gz = struct.unpack_from(">II", c, 0x10)
    x0, z0, x1, z1 = struct.unpack_from(">4f", c, 0x18)
    x0, z0, x1, z1 = x0 - dx, z0 - dz, x1 - dx, z1 - dz
    side_x, side_z = struct.unpack_from(">2f", c, 0x28)
    verts, vert_at, tris, seen = [], {}, [], set()
    for d in files:
        _, vs, ts = read_fiz(d)
        own = cell_ids(d[0x20:])
        for fl, corners, normal, word in ts:
            pts = [vs[k][0] for k in corners]
            if (fl & ID_BITS) >> len(own):
                raise ValueError("a collision triangle selects an id its cell's list has not")
            ids = frozenset(own[k] for k in range(len(own)) if fl >> k & 1)
            fl = (fl & ~ID_BITS, ids)
            key = (fl, tuple(sorted((round(q[0], 2), round(q[1], 2), round(q[2], 2)) for q in pts)))
            if key in seen or not fm4_col.overlaps([(q[0], q[2]) for q in pts], x0, z0, x1, z1):
                continue
            seen.add(key)
            row = []
            for k in corners:
                if vs[k] not in vert_at:
                    vert_at[vs[k]] = len(verts)
                    verts.append(vs[k])
                row.append(vert_at[vs[k]])
            tris.append((fl, row, normal, word, [(q[0], q[2]) for q in pts]))
    lists = [[] for _ in range(gx * gz)]
    for i, t in enumerate(tris):
        tri2 = t[4]
        c0 = max(0, int((min(q[0] for q in tri2) - x0) // side_x))
        c1 = min(gx - 1, int((max(q[0] for q in tri2) - x0) // side_x))
        r0 = max(0, int((min(q[1] for q in tri2) - z0) // side_z))
        r1 = min(gz - 1, int((max(q[1] for q in tri2) - z0) // side_z))
        for r in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                bx, bz = x0 + cc * side_x, z0 + r * side_z
                if fm4_col.overlaps(tri2, bx, bz, bx + side_x, bz + side_z):
                    lists[r * gx + cc].append(i)
    if len(tris) > 0xFFFF:
        raise ValueError("a collision cell of more than 65,535 triangles")
    id_list = sorted(set().union(*(t[0][1] for t in tris)))     # as the games' own lists: ascending
    if len(id_list) > 15:
        raise ValueError("a collision cell cut again selects more than 15 ids")
    bit = {v: 1 << k for k, v in enumerate(id_list)}
    vbase = CELL_HEAD
    tbase = vbase + 16 * len(verts)
    gbase = tbase + 32 * len(tris)
    blob, offs = bytearray(), []
    for lst in lists:
        offs.append(gbase + 4 * gx * gz + len(blob) if lst else 0)
        if lst:
            blob += struct.pack(f">H{len(lst)}H", len(lst), *lst)
    out = bytearray(c[:CELL_HEAD])
    struct.pack_into(">4I", out, 0, len(verts), len(tris), vbase, tbase)
    struct.pack_into(">4f", out, 0x18, x0, z0, x1, z1)
    struct.pack_into(">I", out, 0x38, gbase)
    out[CELL_IDS:CELL_ID_COUNT] = bytes(CELL_ID_COUNT - CELL_IDS)
    struct.pack_into(f">{len(id_list)}H", out, CELL_IDS, *id_list)
    struct.pack_into(">H", out, CELL_ID_COUNT, len(id_list))
    for pos, word in verts:
        out += struct.pack(">3fI", *pos, word)
    for (fl, ids), row, normal, word, _ in tris:
        out += struct.pack(">4I3fI", fl | sum(bit[v] for v in ids), *[vbase + 16 * v for v in row], *normal, word)
    out += struct.pack(f">{gx * gz}I", *offs) + blob
    out += bytes(-len(out) % 0x800)
    head = bytearray(first[:0x20])
    struct.pack_into(">I", head, 8, len(out))
    return bytes(head) + bytes(out) + first[0x20 + size1:0x20 + size1 + size2]


_START = time.monotonic()


def progress(what):
    print(f"[{time.monotonic() - _START:6.0f} s] {what}", file=sys.stderr, flush=True)


def combine_collision(colA, colB, a_files, b_take=()):
    """The collision index with F&F's records (its numbering kept), FH2's cells taking a place F&F
    lists without a file (F&F's index covers almost all of FH2's grid, its files only its own
    area) and the cells F&F does not list numbered on. FH2's records in b_take (the cells its
    added zones name) take F&F's place even where F&F has a file: FH2's base has its own
    collision under F&F's docks (135 of their 162 cells), and kept, the docks had none. Every
    record's place in the stream is laid out again in order. Returns (the index, {FH2 record: its
    number in the index}, {F&F record whose file is FH2's: FH2 record})."""
    nA, nB = struct.unpack_from(">I", colA, 0x4C)[0], struct.unpack_from(">I", colB, 0x4C)[0]
    ra = [list(struct.unpack_from(REC, colA, 0x58 + 24 * k)) for k in range(nA)]
    rb = [list(struct.unpack_from(REC, colB, 0x58 + 24 * k)) for k in range(nB)]
    at_cell = {r[1]: r[0] for r in ra}
    new, taken = {}, {}
    for r in rb:
        k = at_cell.get(r[1])
        if k is not None:
            if k in a_files:
                if r[0] in b_take:       # the added zones' cell: FH2's file under F&F's number
                    new[r[0]] = k
                    taken[k] = r[0]
                    ra[k][3] = r[3]
                continue                 # F&F's own cell
            new[r[0]] = k
            ra[k][3] = r[3]
            continue
        k = len(ra)
        new[r[0]] = k
        ra.append([k, r[1], 0, r[3], 0, 0])
    start = (0x58 + 24 * len(ra) + 0x7FF) & ~0x7FF
    at = start
    for r in ra:
        r[2], r[5] = at, at + r[3]
        at += r[3]
    ra[-1][5] = 0                  # the last record's, as in both games' own index
    out = bytearray(colA[:0x58])
    struct.pack_into(">I", out, 0x4C, len(ra))
    struct.pack_into(">I", out, 0x54, start)
    struct.pack_into(">I", out, 0x28, at)
    for r in ra:
        out += struct.pack(REC, *r)
    out += bytes(start - len(out))
    return bytes(out), new, taken


def link_target(instance, number, count):
    """The number of the instance this one is linked to in a layout's table of `count`, or None.

    An object with models for several distances is an instance for each, linked in a ring: the
    near one to the far one (+2), each other to the one before it (-1); many instances link to
    one far back in the table (a shared parent). In the FH2 prototype's PDB it is
    PVS::CPVSModelInstance::m_LODParentPVSModelInstanceOffset, a signed 16-bit offset in the
    table (tools/fm4_pvs.py reads it as "u16"); 38,000 of FH2's 51,182 instances have one. An
    offset of more than 32,767 is stored without its high bits (FH2's own has 760 such): the
    target is then the one 65,536 further back or on."""
    v = instance["u16"]
    if not v:
        return None
    v -= 0x10000 if v & 0x8000 else 0
    for t in (number + v, number + v - 0x10000, number + v + 0x10000):
        if 0 <= t < count:
            return t
    return None


def close_links(used, table, add):
    """Every instance a used one links to, and so on, added (add(number)): an instance left out
    of the table would leave its partners linked to another object."""
    todo = list(used)
    while todo:
        t = link_target(table[todo[-1]], todo.pop(), len(table))
        if t is not None and t not in used:
            add(t)
            todo.append(t)


def relinked(instance, number, new, count):
    """The instance with its link for the table numbered again (new: {old number: new}; count:
    the old table's length), stored as the games store it: its low 16 bits."""
    t = link_target(instance, number, count)
    if t is None:
        return instance
    return dict(instance, u16=(new[t] - new[number]) & 0xFFFF)


NO_PHYSICS = 0xFFFFFFFF         # a placement's physics key when it has none
PHYS_VERSION = 13               # PhysicsDefinitionVersion::Version14 of the FH2 prototype's PDB
PHYS_HEAD = 0x95                # a record's fixed part, before its collision shapes
PHYS_CHILDREN, PHYS_UNIQUE, PHYS_HASH = 0x70, 0x8C, 0x90
PHYS_SHAPES = {0: 16, 1: 80}    # a sphere's and a box's bytes after the shape's 6-byte head


def read_physics(d):
    """A world's PhysicsDefinitions.bin as ([(name hash, [its records' bytes])] its definitions,
    [key -> definition], [a key's model numbers]).

    The breakable objects (signs, lamp posts, fences): a zone's placement of one names a key,
    and the game makes the object from the key's definition as the car comes near. The file
    (TU4 sub_82E00038; CPhysicsDefinitionList, CCollObjPhysicsDefinition and CCollisionShape in
    the FH2 prototype's PDB, the fields' order from the labels the game's reader passes):
    - a count of definitions, then their records one after another. A record: version (13),
      type (0, a collidable object), 0x8D bytes of fixed fields (the number of child records
      at +0x70, the record's number in the file at +0x8C, the object's name hash at +0x90, its
      SmashableObjectTypes.xml type at +0x94), a count of collision shapes and the shapes (a
      type, 2 bytes of flags; a sphere 16 bytes, a box 80, a convex hull its size and that
      many). A definition is a record and its children (the object's parts), which follow it
      with its name hash;
    - a count of keys ("global indices") and a u16 definition number for each: two keys of one
      definition are one object in two liveries;
    - for each key, a layout model number for each child of its definition (one when it has
      none)."""
    try:
        return _read_physics(d)
    except struct.error:
        raise ValueError("PhysicsDefinitions.bin: cut short") from None


def _read_physics(d):
    n = struct.unpack_from(">I", d, 0)[0]
    p, defs = 4, []
    while len(defs) < n or (defs and len(defs[-1][1]) <= struct.unpack_from(">I", defs[-1][1][0], PHYS_CHILDREN)[0]):
        if p + PHYS_HEAD + 4 > len(d) or struct.unpack_from(">II", d, p) != (PHYS_VERSION, 0):
            raise ValueError(f"PhysicsDefinitions.bin: no version {PHYS_VERSION} object record at {p:#x}")
        q = p + PHYS_HEAD + 4
        for _ in range(struct.unpack_from(">I", d, p + PHYS_HEAD)[0]):
            kind = struct.unpack_from(">I", d, q)[0]
            q += 6
            if kind == 2:
                q += 4 + struct.unpack_from(">I", d, q)[0]
            elif kind in PHYS_SHAPES:
                q += PHYS_SHAPES[kind]
            else:
                raise ValueError(f"PhysicsDefinitions.bin: a collision shape of type {kind} at {q - 6:#x}")
        name = struct.unpack_from(">I", d, p + PHYS_HASH)[0]
        if defs and len(defs[-1][1]) <= struct.unpack_from(">I", defs[-1][1][0], PHYS_CHILDREN)[0]:
            if name != defs[-1][0]:
                raise ValueError(f"PhysicsDefinitions.bin: a child record at {p:#x} with another name than its parent's")
            defs[-1][1].append(d[p:q])
        else:
            defs.append((name, [d[p:q]]))
        p = q
    count = struct.unpack_from(">I", d, p)[0]
    keys = list(struct.unpack_from(f">{count}H", d, p + 4))
    p += 4 + 2 * count
    if any(k >= n for k in keys):
        raise ValueError("PhysicsDefinitions.bin: a key of a definition the file has not")
    models = []
    for k in keys:
        parts = max(len(defs[k][1]) - 1, 1)
        models.append(list(struct.unpack_from(f">{parts}I", d, p)))
        p += 4 * parts
    if p != len(d):
        raise ValueError(f"PhysicsDefinitions.bin: read {p} of {len(d)} bytes")
    return defs, keys, models


def write_physics(defs, keys, models):
    """read_physics's parts as the file, the records numbered again in their order."""
    out, number = [struct.pack(">I", len(defs))], 0
    for _, records in defs:
        for r in records:
            out.append(r[:PHYS_UNIQUE] + struct.pack(">I", number) + r[PHYS_UNIQUE + 4:])
            number += 1
    out.append(struct.pack(f">I{len(keys)}H", len(keys), *keys))
    out += [struct.pack(f">{len(m)}I", *m) for m in models]
    return b"".join(out)


def merge_physics(da, db, model_shift):
    """The base world's PhysicsDefinitions.bin with the added world's keys after its own:
    (the file, the number an added world's key moves by). Each world numbers its keys for
    itself (FH2 has 145, F&F 157, in another order), so with the base's file alone an added
    zone's object took another object's definition, or one past the end, and the game stopped
    on it. An added key keeps its own models (moved by model_shift) and takes the base's
    definition of its object (the same name hash and as many parts), or its own, put after the
    base's: F&F has 18 that FH2 has not. The game takes the records from a pool of a fixed size
    (both files whole were too many: it stopped reading them), so no definition is in twice."""
    defs_a, keys_a, models_a = read_physics(da)
    defs_b, keys_b, models_b = read_physics(db)
    defs = list(defs_a)
    at = {name: k for k, (name, _) in enumerate(defs_a)}
    place = []
    for name, records in defs_b:
        if name in at and len(defs_a[at[name]][1]) == len(records):
            place.append(at[name])
        else:
            place.append(len(defs))
            defs.append((name, records))
    keys = keys_a + [place[k] for k in keys_b]
    if len(keys) >= 0xFFFF:
        raise ValueError("more physics keys than 16 bits number")
    models = models_a + [[m + model_shift for m in ms] for ms in models_b]
    return write_physics(defs, keys, models), len(keys_a)


def merge_smashable_types(xml_a, xml_b):
    """SmashableObjectTypes.xml: a physics record names its type by its place in the list, so the
    longer list must start with the shorter one (F&F's is FH2's with one more). Returns the
    longer."""
    types = [re.findall(r'<Object\s+type="([^"]*)"', x) for x in (xml_a, xml_b)]
    short, long_ = sorted(types, key=len)
    if long_[:len(short)] != short:
        raise ValueError("the two worlds' SmashableObjectTypes.xml list their types in another order")
    return xml_a if types[0] is long_ else xml_b


def find_ci(root, *parts):
    """root/parts with each part found whatever its case (the games' folders are named in
    several: Media, media); None when one is missing."""
    path = root
    for part in parts:
        if not os.path.isdir(path):
            return None
        at = {n.lower(): n for n in os.listdir(path)}
        if part.lower() not in at:
            return None
        path = os.path.join(path, at[part.lower()])
    return path


# the base's other files the build reads (fh2ff_world.py): its database, physics and
# post-processing must be there; its AI lines are taken when they are
OTHER_FILES = ((("media", "db", "gamedb.slt"), True), (("media", "physics.zip"), True),
               (("media", "dynamicpost.zip"), True), (("media", "aiopenworld.zip"), False))
OUTPUT_MARKER = ".fh2ff-combined"             # written into OUT_DISC: the folder is this tool's
OUTPUT_ITEMS = ("media", "default.xex", OUTPUT_MARKER)   # all this writes into OUT_DISC


def output_checked(ff_disc, fh2_disc, out_disc):
    """OUT_DISC's real path, refused when it is one of the inputs, in one or round one (it would
    be written over the files being read), or holds anything but an earlier output (a folder
    with this tool's marker file: it is taken away when the new one is complete, so a game's
    folder of the same two entries must not pass for one)."""
    out = os.path.realpath(out_disc)
    for src in (ff_disc, fh2_disc):
        real = os.path.realpath(src)
        try:
            common = os.path.normcase(os.path.commonpath([real, out]))
        except ValueError:         # on another drive: neither holds the other
            continue
        if common in (os.path.normcase(real), os.path.normcase(out)):
            raise ValueError(f"OUT_DISC ({out_disc}) is, is in or holds an input folder ({src})")
    if os.path.isdir(out):
        have = os.listdir(out)
        other = [n for n in have if n.lower() not in OUTPUT_ITEMS]
        if other:
            raise ValueError(f"OUT_DISC ({out_disc}) holds other files ({other[0]}): give an empty or new folder")
        if have and OUTPUT_MARKER not in have:
            raise ValueError(f"OUT_DISC ({out_disc}) holds files and no {OUTPUT_MARKER} of an earlier run: "
                             "give an empty or new folder")
    elif os.path.exists(out):
        raise ValueError(f"OUT_DISC ({out_disc}) is a file")
    return out


def combine(ff_disc, fh2_disc, out_disc, similar=SIMILAR):
    """ff_disc: the base world's game folder; fh2_disc: the added world's; similar: how alike
    (0 to 1) a cell's two zones must be for the base's to stay round the cells it lacks (0:
    only those cells are the added world's; "all": every cell the added world has).
    Written into a folder beside OUT_DISC and put in its place when complete: a run that fails
    leaves an earlier output as it was."""
    roots = []
    for disc in (ff_disc, fh2_disc):
        root = find_ci(disc, "media", "tracks", NAME)
        if root is None or find_ci(root, "Ribbon_00") is None or find_ci(root, "bin.zip") is None:
            raise ValueError(f"{disc} holds no media/tracks/{NAME} with its bin.zip and Ribbon_00")
        roots.append(root)
    others = []
    for rel, needed in OTHER_FILES:
        src = find_ci(ff_disc, *rel)
        if src is None and needed:
            raise ValueError(f"{ff_disc} holds no {'/'.join(rel)}")
        if src is None:
            print(f"note: no {'/'.join(rel)} in {ff_disc}: left out", file=sys.stderr)
        else:
            others.append((rel, src))
    out = output_checked(ff_disc, fh2_disc, out_disc)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".fh2ff-combine-", dir=os.path.dirname(out) or ".")
    try:
        out_root = os.path.join(stage, "media", "tracks", NAME)
        os.makedirs(os.path.join(out_root, "Ribbon_00"))
        C = Combined(roots[0], roots[1], similar)
        try:
            report = _combine(C, ff_disc, fh2_disc, stage, out_root)
        finally:
            C.close()
        for rel, src in others:
            os.makedirs(os.path.dirname(os.path.join(stage, *rel)), exist_ok=True)
            shutil.copyfile(src, os.path.join(stage, *rel))
        xex = find_ci(ff_disc, "default.xex")
        if xex and os.path.isfile(xex):
            shutil.copyfile(xex, os.path.join(stage, "default.xex"))
        with open(os.path.join(stage, OUTPUT_MARKER), "w", encoding="utf-8") as f:
            f.write("Written by tools/fh2ff_combine.py: a later run with this folder as OUT_DISC replaces it.\n")
        old = None
        if os.path.isdir(out):
            old = out + ".old-" + os.path.basename(stage)[-8:]
            os.rename(out, old)
        try:
            os.rename(stage, out)
        except OSError:
            if old:
                os.rename(old, out)
            raise
        if old:
            shutil.rmtree(old, ignore_errors=True)
        return report
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _combine(C, ff_disc, fh2_disc, out_disc, out_root):
    A, B = C.A, C.B
    PA, PB = A.P, B.P
    rbA, rbB = os.path.join(C.roots[0], "Ribbon_00"), os.path.join(C.roots[1], "Ribbon_00")
    bz = {zone_uid(info): c for info, c in B.zones()}            # FH2's zones, read once
    # the breakable objects: the added world's keys after the base's, in one file
    physics, key_shift = None, 0
    phys = [find_ci(r, "PhysicsDefinitions.bin") for r in C.roots]
    if all(phys) and "nophys" not in TESTS:
        with open(phys[0], "rb") as fa, open(phys[1], "rb") as fb:
            physics, key_shift = merge_physics(fa.read(), fb.read(), 0 if TESTS & {"aall", "amodels"} else C.NM)
    elif any(phys):
        raise ValueError("only one of the two worlds has a PhysicsDefinitions.bin")

    def b_placement(e):
        """An added zone's placement with its physics key in the combined file."""
        if not key_shift or not e["extra"] or e["extra"][0] == NO_PHYSICS:
            return e
        return dict(e, extra=(e["extra"][0] + key_shift,) + tuple(e["extra"][1:]))
    az_cell = {uid: cell for cell, uid in C.hexA.items()}
    a_files = {int(n.split(".")[0]) for n in A.names if re.fullmatch(r"\d+\.fiz", n.lower())}
    b_take = set()                 # the collision records the region's zones name
    sh_take = set()                # and their shadow squares (named by place)
    for uid in C.b_zones:
        for f in bz[uid]["files"] + bz[uid].get("files2", []):
            if f[2] == FIZ:
                b_take.add(f[0])
            elif f[2] == SHADOW:
                sh_take.add(B.names[f[3]].lower())
    if "nocol" in TESTS:
        b_take = set()
    col_a = open(os.path.join(rbA, f"{NAME}_track_00.col"), "rb").read()
    col_b = bytearray(open(os.path.join(rbB, f"{NAME}_track_00.col"), "rb").read())
    # FH2's collision grid against F&F's (the index's header: columns, rows, the origin): its
    # cells the region's zones name are cut again for F&F's grid when the origins differ
    if col_a[:8] != col_b[:8] or col_a[0x18:0x28] != col_b[0x18:0x28] or col_a[0x2C:0x4C] != col_b[0x2C:0x4C]:
        raise ValueError("the two worlds' collision grids differ in size")
    cols = struct.unpack_from(">I", col_a, 0)[0]
    grid_dx = struct.unpack_from(">f", col_b, 8)[0] - struct.unpack_from(">f", col_a, 8)[0]
    grid_dz = struct.unpack_from(">f", col_b, 12)[0] - struct.unpack_from(">f", col_a, 12)[0]
    retiled = {}                   # FH2 collision record -> its file, cut for F&F's grid: where
    #                                it is in `spool` (a file beside the output, not memory)
    spool = C.spool = tempfile.TemporaryFile(dir=out_disc)     # closed with C, before the folder moves

    def cut_cell(num):
        at, size = retiled[num]
        spool.seek(at)
        return spool.read(size)
    if (abs(grid_dx) > 0.01 or abs(grid_dz) > 0.01) and "noretile" not in TESTS:
        if abs(grid_dx) >= 100 or abs(grid_dz) >= 100:
            raise ValueError("the two worlds' collision grids are a cell or more apart")
        nb = struct.unpack_from(">I", col_b, 0x4C)[0]
        b_recs = [struct.unpack_from(REC, col_b, 0x58 + 24 * k) for k in range(nb)]
        at_cell = {r[1]: r[0] for r in b_recs}

        def cell_file(cell):
            e = B.entry.get(f"{at_cell[cell]}.fiz") if cell in at_cell else None
            return B.zip.read(B.info_of[e]) if e is not None else None
        for k, r in enumerate(b_recs):
            if r[0] not in b_take:
                continue
            first = cell_file(r[1])
            if first is None:
                continue
            near = [cell_file(r[1] + dc + cols * dr) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if dc or dr]
            cut = retile_fiz([first] + [d for d in near if d], grid_dx, grid_dz)
            spool.seek(0, os.SEEK_END)
            retiled[r[0]] = (spool.tell(), len(cut))
            spool.write(cut)
            struct.pack_into(">I", col_b, 0x58 + 24 * k + 12, struct.unpack_from(">I", cut, 8)[0])
        progress(f"{len(retiled)} collision cells cut again for the base's grid ({grid_dx:.2f}, {grid_dz:.2f} m off)")
    col, col_new, col_taken = combine_collision(col_a, bytes(col_b), a_files, b_take)
    shadows_A = {n.lower() for n in A.names if n.lower().endswith(".sh")}
    sh_take &= shadows_A           # a square F&F has too: FH2's in F&F's name
    if "nosh" in TESTS:
        sh_take = set()

    # the region's collision objects: FH2's, its road closures left out with their instances
    # (tied to them by position: within a metre, 3 m in height, as fh2ff_world's)
    with open(os.path.join(rbA, "CollObjs.xml"), encoding="utf-8", errors="replace") as f:
        coll_a = f.read()
    with open(os.path.join(rbB, "CollObjs.xml"), encoding="utf-8", errors="replace") as f:
        coll_b = f.read()
    collobjs, gone = merge_collobjs(coll_a, coll_b, C.in_region) if "noobj" not in TESTS else (coll_a, [])
    near = {}
    for x, y, z in gone:
        near.setdefault((round(x), round(z)), []).append((x, y, z))
    drop_b = set()
    for uid, c in bz.items():
        for v, e in zip(c["instances"], c["placements"]):
            if e["extra"]:
                x, y, z = e["pos"]
                if any(abs(ox - x) < 1.0 and abs(oz - z) < 1.0 and abs(oy - y) < 3.0
                       for dx in (-1, 0, 1) for dz in (-1, 0, 1)
                       for ox, oy, oz in near.get((round(x) + dx, round(z) + dz), ())):
                    drop_b.add(v & 0x7FFFFFFF)

    def region_part(P, c, inside):
        """The positions in zone c's instance list of the instances standing in the region
        (inside True) or out of it (False); models over SMALL stand nowhere and go with False."""
        out = []
        for k, (v, pl) in enumerate(zip(c["instances"], c["placements"])):
            x, z, size = placed_at(P, v, pl)
            if (size <= SMALL and C.in_region(x, z)) == inside:
                out.append(k)
        return out

    # FH2's files the region's zones use: their file records, their models' and textures' files,
    # and what their procedural geometry names: textures by record, other procedural geometry (the
    # animated objects a scene plays) by number, and (Models) layout instances, whose models and
    # textures come too. F&F's zones round the region see FH2's region as FH2's zone of the
    # same cell sees it: those instances join them, and F&F's own instances standing in the
    # region leave them.
    needed = set()
    tex_files = {}
    for e, n in enumerate(B.names):
        m = re.fullmatch(r"_0x([0-9a-f]{8}).*", n.lower())
        if m:
            tex_files.setdefault(int(m.group(1), 16), []).append(e)
    b_used = set()                 # FH2's instances the combined world holds
    b_textures = set()             # FH2's texture records the combined world uses

    def use_instance(v):
        b_used.add(v)
        i = PB["instances"][v]
        if i["model"] in B.model_entry:                        # the model an instance draws (the
            needed.add(B.model_entry[i["model"]])              # zone's model list may not hold it)
        b_textures.update(PB["models"][i["model"]]["textures"])
        if i["u8"] != 0xFFFFFFFF:
            b_textures.add(i["idx"])                           # an instance's own lightmap

    for uid in C.b_zones:
        c = bz[uid]
        for f in c["files"] + c.get("files2", []):
            needed.add(f[3])
        b_textures.update(c["textures"] + c.get("textures2", []))
        for mdl in c["models"] + c.get("models2", []):
            if mdl in B.model_entry:
                needed.add(B.model_entry[mdl])
            b_textures.update(PB["models"][mdl]["textures"])  # a model's own, the zone's list or not
        for v in c["instances"]:
            use_instance(v & 0x7FFFFFFF)
    seen_from = {}                 # F&F zone -> (FH2's zone of its cell, the positions joining it)
    for cell, uid in C.hexA.items():
        if cell in C.region or cell not in C.hexB or "noseam" in TESTS:
            continue
        c = bz[C.hexB[cell]]
        ks = [k for k in region_part(PB, c, True) if (c["instances"][k] & 0x7FFFFFFF) not in drop_b]
        if ks:
            seen_from[uid] = (c, ks)
            for k in ks:
                use_instance(c["instances"][k] & 0x7FFFFFFF)
    pgeo_tex = set()               # texture records FH2's procedural geometry names
    todo = [e for e in needed if B.names[e].lower().endswith(".pgeo")]
    seen = set(todo)
    while todo:
        d = B.zip.read(B.info_of[todo.pop()])
        p = pgeo_parts(d)
        if p is None:
            continue
        pgeo_tex.update(read_refs(d, p["tex"]))
        for g in read_refs(d, p["geo"]):
            e = B.entry.get(f"__r00g{g:05d}.pgeo")
            if e is not None and e not in seen:
                seen.add(e)
                needed.add(e)
                todo.append(e)
        for v in read_refs(d, p["inst"], 4):
            use_instance(v)
    for t in b_textures:
        t &= 0xFFFFFF              # a texture reference's top byte holds flags
        if t < len(PB["textures"]) and PB["textures"][t][0] in C.own_ids:
            needed.update(tex_files.get(PB["textures"][t][0], ()))
    for k in pgeo_tex:
        if k < len(PB["textures"]) and PB["textures"][k][0] in C.own_ids:
            needed.update(tex_files.get(PB["textures"][k][0], ()))
    # bundles: every texture kept, under its combined record (F&F's record for one that is
    # F&F's). With those left out, F&F's bundles had to supply them, and where F&F's zones are
    # retired nothing loads those: the road drew black and so many textures streamed one by one
    # that the next zone's collision came late (the car fell through at each zone's edge).
    own_map = dict(C.index)
    bundles = [e for e in needed if B.names[e].lower().endswith(".bundle")]
    for e in bundles:              # (rewritten again when written: none is held meanwhile)
        if not any(t in own_map for t, _, _ in bundle_entries(B.zip.read(B.info_of[e]))):
            needed.discard(e)
    for e, n in enumerate(B.names):
        low = n.lower()
        if low.endswith(".fxobj"):
            needed.add(e)
        m = re.fullmatch(rf"(?:__r\d\dz|{NAME.lower()}_\d\d_)(\d+)\.(?:pvsz|soundscape)", low)
        if m and int(m.group(1)) in C.zone_new:
            needed.add(e)          # a region zone's own file and soundscape

    # F&F's procedural geometry standing in the region (its own older docks' vegetation and
    # placed models: the file's bounds' centre) leaves F&F's zones round it
    region_pgeo = set()
    for info, c in A.zones():
        if az_cell.get(zone_uid(info)) in C.region:
            region_pgeo.update(f[3] for f in c["files"] + c.get("files2", []) if f[2] == PGEO)
    a_pgeo_drop = set()
    for e in region_pgeo:
        d = A.zip.read(A.info_of[e])
        if d[:4] == b"OEGP":
            lo, hi = struct.unpack_from(">3f", d, 0x10), struct.unpack_from(">3f", d, 0x20)
            if C.in_region((lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2):
                a_pgeo_drop.add(e)

    # F&F's zones in the region are retired (no cell is theirs: written empty). The model and
    # procedural geometry files only they used are left out (with every cell of FH2's taken,
    # the two worlds' files are past the archive's 65,535), and F&F's entries numbered again.
    az = {zone_uid(info): c for info, c in A.zones()}
    retired = {uid for cell, uid in C.hexA.items() if cell in C.region}
    a_ref, a_pg_inst = set(), set()
    for uid, c in az.items():
        if uid in retired:
            continue
        a_ref.update(f[3] for f in c["files"] + c.get("files2", []))
        for m in list(c["models"]) + list(c.get("models2", [])) + [PA["instances"][v & 0x7FFFFFFF]["model"] for v in c["instances"]]:
            if m in A.model_entry:
                a_ref.add(A.model_entry[m])
    prune = bool(retired) and "noprune" not in TESTS
    # every procedural geometry file that is written: the referenced ones, or all when none is left out
    todo = [e for e in (a_ref if prune else range(len(A.names))) if A.names[e].lower().endswith(".pgeo")]
    while todo:
        d = A.zip.read(A.info_of[todo.pop()])
        pp = pgeo_parts(d)
        if pp is None:
            continue
        for g in read_refs(d, pp["geo"]):
            e = A.entry.get(f"__r00g{g:05d}.pgeo")
            if e is not None and e not in a_ref:
                a_ref.add(e)
                todo.append(e)
        for v in read_refs(d, pp["inst"], 4):
            a_pg_inst.add(v)
            m = PA["instances"][v]["model"]
            if m in A.model_entry:
                a_ref.add(A.model_entry[m])
    a_old = [e for e, n in enumerate(A.names)
             if not prune or e in a_ref or not n.lower().endswith((".rmb.bin", ".pgeo"))]
    a_new = {e: k for k, e in enumerate(a_old)}
    NA = len(a_old)
    report_pruned = len(A.names) - NA
    # every file's combined name; FH2's that F&F's stands for map to F&F's name
    names = [A.names[e] for e in a_old]
    a_index = {n.lower(): k for k, n in enumerate(names)}
    b_to = {}                      # FH2 entry -> combined entry
    b_member = {}                  # combined entry (an FH2 file) -> FH2 entry
    for e, n in enumerate(B.names):
        if e not in needed:
            continue
        low = n.lower()
        # tests for a world combined with itself: the added zones name the base's own file of
        # the same name for these kinds ("apgeo", "abundles")
        if (("apgeo" in TESTS and low.endswith(".pgeo")) or ("abundles" in TESTS and low.endswith(".bundle"))
                or ("amodels" in TESTS and (low.endswith(".rmb.bin") or low.startswith("_0x") and not low.endswith(".bundle")))
                or ("aall" in TESTS and not low.endswith((".pvsz", ".soundscape")))) and low in a_index:
            b_to[e] = a_index[low]
            continue
        new = C.b_name(n, col_new, shadows_A)
        if new is None:
            if low.endswith((".sh",)) and low in a_index:
                b_to[e] = a_index[low]
            elif low.endswith(".fiz"):
                pass           # a cell F&F has: the zone's record goes to F&F's (below)
            continue
        if low.endswith(".fiz") and int(new.split(".")[0]) in col_taken:
            b_to[e] = a_index[new.lower()]         # FH2's file stored under F&F's name (below)
            continue
        if new.lower() in a_index:
            if low.endswith(".fxobj") or "/" in n or not new.lower().startswith(("_0x", "aout", "__r")):
                b_to[e] = a_index[new.lower()]     # a shader (or other shared name): F&F's
                continue
            raise ValueError(f"FH2's {n} as {new} meets an F&F file")
        b_to[e] = len(names)
        b_member[len(names)] = e
        a_index[new.lower()] = len(names)
        names.append(new)

    # FH2's collision cells F&F has: its records to F&F's file of the same cell
    nA = struct.unpack_from(">I", col, 0x4C)[0]
    cell_of_A = {}
    for k in range(nA):
        num, cell = struct.unpack_from(">2I", col, 0x58 + 24 * k)
        cell_of_A[cell] = num
    colB = open(os.path.join(rbB, f"{NAME}_track_00.col"), "rb").read()
    for k in range(struct.unpack_from(">I", colB, 0x4C)[0]):
        num, cell = struct.unpack_from(">2I", colB, 0x58 + 24 * k)
        if num not in col_new and cell in cell_of_A:
            n = f"{cell_of_A[cell]}.fiz"
            e = B.entry.get(f"{num}.fiz")
            if e is not None and n.lower() in a_index:
                b_to[e] = a_index[n.lower()]

    # F&F's own record ids and resource numbers of its files (for FH2's records that name them)
    a_res = {}
    for uid, c in az.items():
        if uid in retired:
            continue
        for f in c["files"] + c.get("files2", []):
            if f[3] in a_new:
                a_res.setdefault((a_new[f[3]], f[2]), (f[0], f[1]))
    OWN9 = len(A.names) + len(B.names)     # ids of FH2's preload records: past F&F's (its entries)

    a_self = {}
    for cell, uid in C.hexA.items():
        if cell not in C.region:
            for f in az[uid]["files"] + az[uid].get("files2", []):
                if f[2] in (ZONE, SOUNDSCAPE) and f[0] == uid:
                    a_self[(cell, f[2])] = f
    b_cell = {uid: cell for cell, uid in C.hexB.items()}

    # Resource numbers (a record's second field) in the combined world. Both worlds number a
    # zone's file resource as the zone (every type 1 record of F&F's 6,560 and FH2's 17,332),
    # its zones first, then the rest, no number naming two files. Here: the zones (F&F's and
    # FH2's region's) keep that, F&F's other resources move past the added zones, FH2's past
    # F&F's. Left with FH2's numbers past F&F's, FH2's zones' own resources were beyond the
    # zones and the game broke its stream list (sub_82DD9388) as soon as one went live (even
    # FH2 combined with itself).
    NBZ = len(C.b_zones)
    B_RES = C.RES + NBZ

    def a_record(f, new=False):
        """F&F's record in the combined world (None: its file is left out); new: its entry is
        the combined one already."""
        own, res, kind, e = f
        if not new:
            e = a_new.get(e)
        return None if e is None else (own, own if kind == ZONE else res + NBZ, kind, e)

    def b_record(f):
        own, res, kind, e = f
        if kind in (ZONE, SOUNDSCAPE) and own not in C.zone_new:
            # a neighbour outside the region: F&F's zone of that cell (FH2's zones name 4 to 7
            # neighbours; F&F's own name only themselves)
            g = a_self.get((b_cell.get(own), kind))
            return a_record(g) if g else None
        if e not in b_to:
            return None
        if kind == PGEO and f"nopgeo{B.kind(e)}" in TESTS:   # a test: one procedural type left out
            return None
        ne = b_to[e]
        if kind == ff.PRELOAD:
            # FH2's zones preload their neighbours' files (models, procedural geometry, collision,
            # shadows, soundscapes; their own id is the file's number): kept, renumbered. Left
            # out, FH1 corrupted its memory streaming them in (lr 82C12C10).
            if (ne, kind) in a_res:
                return a_record((*a_res[(ne, kind)], kind, ne), new=True)
            return (OWN9 + ne, res + B_RES, kind, ne)
        if ne < NA:    # F&F's file: F&F's numbers
            if kind == FIZ:
                own = int(names[ne].split(".")[0])
            # F&F's own record of this file of the same type. (By the file alone, FH2's base
            # gave a collision record the numbers of a preload record of the same file, whose
            # id is the file's entry: the game broke its stream list as the zone went live.)
            if (ne, kind) in a_res:
                return a_record((*a_res[(ne, kind)], kind, ne), new=True)
            return (own, res + B_RES, kind, ne)
        if kind in (ZONE, SOUNDSCAPE):
            own = C.zone_new.get(own, own)
        elif kind == PGEO:
            own += C.NPG
        elif kind == BUNDLE:
            own = C.bundle_new.get(own, own)
        elif kind == FIZ:
            own = col_new.get(own, own)
        return (own, own if kind == ZONE else res + B_RES, kind, ne)

    # the instances: F&F's, then the FH2 instances the region's zones, the zones round it and
    # their procedural geometry use. FH1 drove no world past 65,535 instances (Colorado has
    # 62,173): F&F's whole table and FH2's (97,807) faulted. When F&F's whole table and FH2's
    # used ones fit, F&F's stays as it is; else only those F&F's zones use are kept (26,482 of
    # its 46,625), renumbered, and its Models files (procedural geometry naming instances by
    # number) with them.
    # (the instances the kept ones are linked to stay with them: close_links)
    before = len(b_used)
    close_links(b_used, PB["instances"], use_instance)
    linked = len(b_used) - before
    if len(PA["instances"]) + len(b_used) <= 0xFFFF:
        a_used = set(range(len(PA["instances"])))
    else:
        a_used = set(a_pg_inst)
        for uid, c in az.items():
            if uid not in retired:
                a_used.update(v & 0x7FFFFFFF for v in c["instances"])
        close_links(a_used, PA["instances"], a_used.add)
    a_inst = {v: k for k, v in enumerate(sorted(a_used))}
    a_whole = len(a_inst) == len(PA["instances"])
    b_inst = {v: len(a_inst) + k for k, v in enumerate(sorted(b_used))}
    ZM = C.NM                      # the added zones' models' shift
    ztex = C.tex
    if TESTS & {"aall", "amodels"}:   # a world combined with itself: the added zones use the base's
        b_inst, ZM, ztex = dict(a_inst), 0, (lambda t: t)
    if len(a_inst) + len(b_used) > 0xFFFF:
        raise ValueError(f"{len(a_inst) + len(b_used)} instances: FH1 takes 65,535")
    report = {"added instances kept for the ones linked to them": linked,
              "base instances in the region left out of zones round it": 0,
              "region instances joining zones round it": 0, "base procedural geometry entries left out": 0}

    # the zones a zone names (its own and its neighbours', type 1, with their soundscapes, type 4;
    # FH2's zones name 4 to 7): where a cell is FH2's now, FH2's zone of it
    b_self = {}
    for cell, uid in C.hexB.items():
        if uid in C.zone_new:
            for f in bz[uid]["files"] + bz[uid].get("files2", []):
                if f[2] in (ZONE, SOUNDSCAPE) and f[0] == uid:
                    b_self[(cell, f[2])] = f
    a_region_cell = {uid: cell for cell, uid in C.hexA.items() if cell in C.region}

    def a_neighbours(files):
        out = []
        for f in files:
            if f[2] in (ZONE, SOUNDSCAPE) and f[0] in a_region_cell:
                g = b_self.get((a_region_cell[f[0]], f[2]))
                f = b_record(g) if g else None
                report["base zones' neighbour entries now the region's"] += 1
            if f:
                out.append(f)
        return out
    report["base zones' neighbour entries now the region's"] = 0

    a_pgeo_new = {a_new[e] for e in a_pgeo_drop if e in a_new}

    def a_zone(c, uid):
        """F&F's zone: its instances renumbered; round the region, its own instances and
        procedural geometry standing in the region out and FH2's region (as FH2's zone of the
        cell sees it) in, with their models and textures; the region's zones its neighbours."""
        if uid in retired:
            # replaced: the hex gives its cell to FH2's zone, but its own record in the hex still
            # says that cell. Empty but for the collision cells of the zone in its place:
            # written with none, the car fell through at some zones' edges.
            out = {k: [] for k in c}
            out["files"] = [r for r in (b_record(f) for f in bz[C.hexB[az_cell[uid]]]["files"] if f[2] == FIZ) if r]
            return out

        def mine(files):
            return a_neighbours([r for r in (a_record(f) for f in files) if r])
        c = dict(c, files=mine(c["files"]), **({"files2": mine(c["files2"])} if "files2" in c else {}))
        # a zone in a cell FH2 has no zone in keeps F&F's own view of the region (FH2's zones
        # see it from their own cells only): it changes to FH2's at the region's edge
        if "noseam" in TESTS or az_cell.get(uid) not in C.hexB:
            return dict(c, instances=[(v & 0x80000000) | a_inst[v & 0x7FFFFFFF] for v in c["instances"]])
        keep = region_part(PA, c, False)
        report["base instances in the region left out of zones round it"] += len(c["instances"]) - len(keep)
        out = dict(c)
        out["instances"] = [(c["instances"][k] & 0x80000000) | a_inst[c["instances"][k] & 0x7FFFFFFF] for k in keep]
        out["drawn"] = [c["drawn"][k] for k in keep]
        out["placements"] = [c["placements"][k] for k in keep]
        for key in ("files", "files2"):
            if key in c:
                out[key] = [f for f in c[key] if not (f[2] == PGEO and f[3] in a_pgeo_new)]
                report["base procedural geometry entries left out"] += len(c[key]) - len(out[key])
        if uid in seen_from:
            bc, ks = seen_from[uid]
            report["region instances joining zones round it"] += len(ks)
            out["instances"] += [(bc["instances"][k] & 0x80000000) | b_inst[bc["instances"][k] & 0x7FFFFFFF] for k in ks]
            out["drawn"] += [bc["drawn"][k] for k in ks]
            out["placements"] += [b_placement(bc["placements"][k]) for k in ks]
            models, textures = list(out["models"]), list(out["textures"])
            have_m, have_t = set(models), set(textures)
            for k in ks:
                m = PB["instances"][bc["instances"][k] & 0x7FFFFFFF]["model"]
                if m + C.NM not in have_m:
                    have_m.add(m + C.NM)
                    models.append(m + C.NM)
                for t in PB["models"][m]["textures"]:
                    t = C.tex(t) & 0xFFFFFF
                    if t not in have_t:
                        have_t.add(t)
                        textures.append(t)
            out["models"] = models
            out["textures"] = textures
            out["texture_flags"] = list(c["texture_flags"]) + [0] * (len(textures) - len(c["textures"]))
        return out

    def b_zone(c):
        out = dict(c)
        keep = [k for k, v in enumerate(c["instances"]) if (v & 0x7FFFFFFF) not in drop_b]
        out["instances"] = [(c["instances"][k] & 0x80000000) | b_inst[c["instances"][k] & 0x7FFFFFFF] for k in keep]
        out["drawn"] = [c["drawn"][k] for k in keep]
        out["placements"] = [b_placement(c["placements"][k]) for k in keep]
        out["models"] = [m + ZM for m in c["models"]]
        out["textures"] = [ztex(t) for t in c["textures"]]
        out["files"] = [r for r in (b_record(f) for f in c["files"]) if r]
        if "files2" in c:
            out["models2"] = [m + ZM for m in c["models2"]]
            out["textures2"] = [ztex(t) for t in c["textures2"]]
            out["files2"] = [r for r in (b_record(f) for f in c["files2"]) if r]
        return out

    progress(f"{len(needed)} of the added world's files needed")
    # the layout: F&F's tables, FH2's after them
    shader_at = {s.lower(): k for k, s in enumerate(PA["shaders"])}
    shaders = list(PA["shaders"])
    smap = []
    for s in PB["shaders"]:
        if s.lower() not in shader_at:
            shader_at[s.lower()] = len(shaders)
            shaders.append(s)
        smap.append(shader_at[s.lower()])
    textures = list(PA["textures"]) + [(C.ids[PB["textures"][k][0]], C.index[k]) + tuple(PB["textures"][k][2:])
                                       for k in C.own]
    models = list(PA["models"]) + [dict(m, textures=[C.tex(t) for t in m["textures"]],
                                         shaders=[smap[s] for s in m["shaders"]]) for m in PB["models"]]
    if len(models) > 0xFFFF:       # an instance's and a zone's model numbers are 16 bits
        raise ValueError(f"{len(models)} models: the layout takes 65,535")
    if len(PA["names"]) + len(PB["names"]) >= 0xFFFF:
        raise ValueError("more named instances than the layout's 16-bit name numbers take")
    for key in ("cells", "always", "dynamic", "models_a", "models_b"):
        if PB[key] or (PA[key] and not a_whole):   # they name instances and models by number
            raise ValueError(f"the layout's {key} list is not empty: not renumbered here")
    instances = [relinked(PA["instances"][v], v, a_inst, len(PA["instances"])) for v in sorted(a_used)]
    b_order = {v: k for k, v in enumerate(sorted(b_used))}
    for v in sorted(b_used):
        i = relinked(PB["instances"][v], v, b_order, len(PB["instances"]))
        j = dict(i, model=i["model"] + C.NM)
        if i["u8"] != 0xFFFFFFFF:
            j["idx"] = C.tex(i["idx"])      # the instance's own lightmap record
        if i["u12"] != 0xFFFF:
            j["u12"] = i["u12"] + len(PA["names"])   # a named instance (the barn finds): its name
        instances.append(j)
    zones_table = list(PA["zones"]) + [PB["zones"][uid] for uid in C.b_zones]
    P = dict(PA, textures=textures, shaders=shaders, models=models, instances=instances,
             zones=zones_table, names=list(PA["names"]) + list(PB["names"]), pgeo=C.NPG + PB["pgeo"],
             resources=B_RES + PB["resources"], rmodels=PA["rmodels"] + PB["rmodels"])

    # the archive
    a_zone_files = {}
    for info, c in A.zones():
        base = info.filename.replace("\\", "/").split("/")[-1].lower()
        a_zone_files[a_index[base]] = write_cell_51(a_zone(c, zone_uid(info)), PA["g2"])
    b_zone_files = {a_index[f"__r00z{C.zone_new[uid]:05d}.pvsz"]: write_cell_51(b_zone(bz[uid]), PB["g2"])
                    for uid in C.b_zones}
    if len(names) > 0xFFFF:
        raise ValueError(f"{len(names)} files: the archive takes 65,535")
    rb = os.path.join(out_root, "Ribbon_00")
    wr = ff.TrackZipWriter(os.path.join(out_root, "bin.zip"))
    try:
        for k, n in enumerate(names):
            if k < NA:
                info = A.info_of[a_old[k]]
                if os.environ.get("FH2FF_COMBINE_SHADERS") == "fh2" and n.lower().endswith(".fxobj")                         and n.lower() in B.entry:                      # a test: FH2's shader files
                    bi = B.info_of[B.entry[n.lower()]]
                    wr.add_raw(info.filename, bi, B.zip.raw(bi))
                    continue
                if k in a_zone_files:
                    wr.add(info.filename, a_zone_files[k])
                elif not a_whole and n.lower().endswith(".pgeo"):
                    d = A.zip.read(info)          # F&F's Models files: its instances renumbered
                    d2 = rewrite_pgeo(d, inst_map=a_inst)
                    if d2 is d:
                        wr.add_raw(info.filename, info, A.zip.raw(info))
                    else:
                        wr.add(info.filename, d2)
                elif n.lower().endswith(".fiz") and int(n.split(".")[0]) in col_taken:
                    num = col_taken[int(n.split('.')[0])]
                    bi = B.info_of[B.entry[f"{num}.fiz"]]
                    if num in retiled:
                        wr.add(info.filename, cut_cell(num))       # the region's collision
                    else:
                        wr.add_raw(info.filename, bi, B.zip.raw(bi))
                elif n.lower() in sh_take:
                    bi = B.info_of[B.entry[n.lower()]]
                    wr.add_raw(info.filename, bi, B.zip.raw(bi))   # the region's shadow squares
                else:
                    wr.add_raw(info.filename, info, A.zip.raw(info))
                continue
            e = b_member[k]
            info = B.info_of[e]
            folder = info.filename.replace("\\", "/").rsplit("/", 1)[0] + "/" if "/" in info.filename.replace("\\", "/") else ""
            if k in b_zone_files:
                wr.add(folder + n, b_zone_files[k])
            elif n.lower().endswith(".bundle"):
                wr.add(folder + n, rewrite_bundle(B.zip.read(info), own_map))
            elif B.names[e].lower().endswith(".fiz") and int(B.names[e].split(".")[0]) in retiled:
                wr.add(folder + n, cut_cell(int(B.names[e].split(".")[0])))
            elif n.lower().endswith(".pgeo"):
                d = B.zip.read(info)
                d2 = rewrite_pgeo(d, lambda v: C.index.get(v, v), C.NPG, b_inst)
                if d2 is d:
                    wr.add_raw(folder + n, info, B.zip.raw(info))
                else:
                    wr.add(folder + n, d2)
            else:
                wr.add_raw(folder + n, info, B.zip.raw(info))
    finally:
        wr.close()

    progress("archive written")
    # the ribbon: layout, hex, file map, zone lookup, collision index; the rest F&F's
    for f in os.listdir(rbA):
        if f.lower().startswith("trackroute") or f.lower().endswith((".xml",)):
            shutil.copyfile(os.path.join(rbA, f), os.path.join(rb, f))
    with open(os.path.join(rb, "CollObjs.xml"), "w", encoding="utf-8", newline="") as f:
        f.write(collobjs)
    smash_a, smash_b = os.path.join(rbA, "SmashableEffects.xml"), os.path.join(rbB, "SmashableEffects.xml")
    if os.path.isfile(smash_a) and os.path.isfile(smash_b) and "noobj" not in TESTS:
        with open(smash_a, encoding="utf-8-sig", errors="replace") as f:
            sa = f.read()
        with open(smash_b, encoding="utf-8-sig", errors="replace") as f:
            sb = f.read()
        with open(os.path.join(rb, "SmashableEffects.xml"), "w", encoding="utf-8-sig", newline="") as f:
            f.write(merge_smashable_effects(sa, sb))
    open(os.path.join(rb, f"{NAME}_00.pvs"), "wb").write(write_layout_51(P))
    open(os.path.join(rb, "FilenameMap_00.dat"), "wb").write(fm4_pvs.write_filename_map(names))
    look = [(a_index[f"__r00z{uid:05d}.pvsz"], uid) for uid in range(len(zones_table))]
    open(os.path.join(rb, "PVSZLookup_00.dat"), "wb").write(b"".join(struct.pack(">ii", e, u) for e, u in look))
    open(os.path.join(rb, f"{NAME}_track_00.col"), "wb").write(col)
    hexA = open(os.path.join(rbA, f"{NAME}_00.hex"), "rb").read()
    ver, side, ox, oz, nzA, w, h = struct.unpack_from(">ifffiii", hexA, 4)
    gw, gh = max(C.gridA[4], C.gridB[4]), max(C.gridA[5], C.gridB[5])
    cells = [-1] * (gw * gh)
    for (cx, cy), uid in C.hexA.items():
        cells[cy * gw + cx] = uid
    for (cx, cy), uid in C.hexB.items():
        if uid in C.zone_new:
            cells[cy * gw + cx] = C.zone_new[uid]
    nz = len(zones_table)
    # after the cells: an 8-byte record a zone, then FH1's byte a zone (F&F's 102 as 101)
    a_rest = ff.convert_hex(hexA)[32 + 4 * w * h:]
    hexB = open(os.path.join(rbB, f"{NAME}_00.hex"), "rb").read()
    b_rest = ff.convert_hex(hexB)[32 + 4 * C.gridB[4] * C.gridB[5]:]
    nzB = len(PB["zones"])
    if len(a_rest) != 9 * nzA or len(b_rest) != 9 * nzB:
        raise ValueError("a hex file's per-zone data is not 8 + 1 bytes a zone")
    extra = (a_rest[:8 * nzA] + b"".join(b_rest[8 * u:8 * u + 8] for u in C.b_zones)
             + a_rest[8 * nzA:] + bytes(b_rest[8 * nzB + u] for u in C.b_zones))
    hexO = (b"HEXY" + struct.pack(">ifffiii", 101, side, ox, oz, nz, gw, gh)
            + struct.pack(f">{gw * gh}i", *cells) + bytes(extra))
    open(os.path.join(rb, f"{NAME}_00.hex"), "wb").write(hexO)

    # the world folder's other files: F&F's, the zone speeds combined
    for f in os.listdir(C.roots[0]):
        p = os.path.join(C.roots[0], f)
        if os.path.isfile(p) and f.lower() not in ("bin.zip", "pvszonespeeds.dat"):
            shutil.copyfile(p, os.path.join(out_root, f))
    if physics is not None:
        with open(os.path.join(out_root, os.path.basename(phys[0])), "wb") as f:
            f.write(physics)
        merged = read_physics(physics)
        report["physics"] = (f"{len(merged[0])} definitions of {sum(len(r) for _, r in merged[0])} records, "
                             f"{len(merged[1])} keys (the added world's from {key_shift})")
    smash = [find_ci(r, "SmashableObjectTypes.xml") for r in C.roots]
    if all(smash):
        with open(smash[0], encoding="utf-8-sig", errors="replace") as fa, \
                open(smash[1], encoding="utf-8-sig", errors="replace") as fb:
            types = merge_smashable_types(fa.read(), fb.read())
        with open(os.path.join(out_root, os.path.basename(smash[0])), "w", encoding="utf-8", newline="") as f:
            f.write(types)
    # the road network: F&F's, with FH2's roads in the region
    with open(os.path.join(C.roots[0], f"{NAME}.nav"), "rb") as f:
        net_a = fm4_routes.read_nav(f.read())
    with open(os.path.join(C.roots[1], f"{NAME}.nav"), "rb") as f:
        net_b = fm4_routes.read_nav(f.read())
    net = merge_nav(net_a, net_b, C.in_region)
    with open(os.path.join(out_root, f"{NAME}.nav"), "wb") as f:
        f.write(fm4_routes.write_nav(net))
    report["road network ways"] = f"{len(net_a['ways'])} in the base, {len(net['ways'])} combined"
    sA = open(os.path.join(C.roots[0], "PVSZoneSpeeds.dat"), "rb").read()
    sB = open(os.path.join(C.roots[1], "PVSZoneSpeeds.dat"), "rb").read()
    per = len(sA) // nzA
    open(os.path.join(out_root, "PVSZoneSpeeds.dat"), "wb").write(
        sA + b"".join(sB[uid * per:(uid + 1) * per] for uid in C.b_zones))
    return {"zones": f"{len(PA['zones'])} base + {len(C.b_zones)} added", "files": len(names),
            "models": len(models), "textures": len(textures), "added textures that are the base's": C.twins,
            "bundles": f"{len(bundles)} named, {sum(1 for e in bundles if e not in needed)} left out",
            "base files left out (only retired zones used them)": report_pruned,
            "instances": len(instances),
            "region": f"{len(C.region)} cells, {sum(1 for c in C.region if c in C.hexA)} of the base's replaced",
            "collision cells": struct.unpack_from(">I", col, 0x4C)[0],
            "shadow squares the region's": len(sh_take),
            "collision objects": f"{coll_a.count('<Obj')} in the base, {collobjs.count('<Obj')} combined",
            "road closures left out (objects, instances)": (len(gone), len(drop_b)), **report}


def main(argv):
    args = list(argv[1:])
    similar = SIMILAR
    if "--all" in args:               # the added world's zone in every cell it has one
        args.remove("--all")
        similar = "all"
    if "--similar" in args:
        k = args.index("--similar")
        similar = float(args[k + 1])
        del args[k:k + 2]
    if len(args) != 3 or len(argv) - 1 - len(args) > 2 or not (similar == "all" or 0 <= similar <= 1):
        print(__doc__)             # --all and --similar are one choice: not both
        return 2
    for key, value in combine(*args, similar=similar).items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
