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
lines. What is combined (fnf_map/COMBINED_MAP_PLAN.md has the measurements):

- The region: FH2's zones in the cells F&F has none in and in those round them FH2 rebuilt;
  F&F's everywhere else. Round the region, F&F's zones lose their instances and procedural
  geometry standing in it and see FH2's there as FH2's zone of the same cell does.
- The layout: F&F's tables (its instances whole when they fit), then FH2's after them. Shaders
  are shared by name. An FH2 texture that is one of F&F's byte for byte (4,749 of F&F's 10,233
  against FH2's) is F&F's record; the rest are records of their own with their ids moved
  (TEXTURE_ID_STEP). Moved, FH2's records never had their full mips streamed in.
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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fm4_pvs  # noqa: E402
import fm4_routes  # noqa: E402
import fh2ff_world as ff  # noqa: E402

NAME = ff.NAME
TEXTURE_ID_STEP = 0x2900       # FH2's texture ids move this far: past F&F's (to 10,403), and all
                               # under 21,000 as Colorado's are (to 20,519). Moved 0x10000 (to 75,800),
                               # FH1 corrupted its memory near F&F's edge (a list of the main loop, lr 82C12C10)
REC = ">6I"                    # a collision index record: number, cell, offset, size, 0, end
# Test switches (FH2FF_COMBINE_TEST, comma list): "share" the added textures that are one of the
# base's byte for byte use the base's record (faulted: see match_textures), "noseam" the base's
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
    """A bundle with only the textures record_map names, each under its record's new number;
    None when none is left. Moved by the ids' step instead, F&F's later textures (their records
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
        """FH2's texture records in the combined world: records of their own after F&F's, their
        ids moved by TEXTURE_ID_STEP (FH1 streams their full mips once their zones are live).
        With the "share" test switch, one that is F&F's byte for byte (the same files, or the same
        bundled data, and the same record: rectangle and flags; 4,749 of F&F's 10,233 are FH2's
        under another id) is F&F's record instead. That faulted as soon as F&F's docks went live
        (a pending stream block's list broken, sub_82DD9388): a texture's resident mips come from
        the bundles its zones name (nearly every texture with files has a bundle copy too), and
        F&F's records' copies are in F&F's bundles, which FH2's zones never name.
        Sets index ({FH2 record: combined record}), ids ({FH2 id: combined id}), own (FH2's
        records kept, in order) and twins (how many are F&F's)."""
        PA, PB = self.A.P, self.B.P
        if "share" not in TESTS:   # every added texture a record of its own (see the docstring)
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
    ways = []                      # ([("a" or "b", node)], world, its tags)
    for idx, tags in ways_of(net_a):
        keep = [not inside(pos_a[n][1], pos_a[n][3]) for n in idx]
        for run in runs(idx, keep):
            if len(run) > 1:
                ways.append(([("a", n) for n in run], "a", tags))
    pos_b = net_b["nodes"]
    for idx, tags in ways_of(net_b):
        ins = [inside(pos_b[n][1], pos_b[n][3]) for n in idx]
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
    out = bytearray(colA[:0x58])
    struct.pack_into(">I", out, 0x4C, len(ra))
    struct.pack_into(">I", out, 0x54, start)
    struct.pack_into(">I", out, 0x28, at)
    for r in ra:
        out += struct.pack(REC, *r)
    out += bytes(start - len(out))
    return bytes(out), new, taken


def combine(ff_disc, fh2_disc, out_disc, similar=SIMILAR):
    """ff_disc: the base world's game folder; fh2_disc: the added world's; similar: how alike
    (0 to 1) a cell's two zones must be for the base's to stay round the cells it lacks (0:
    only those cells are the added world's)."""
    ff_root = os.path.join(ff_disc, "media", "tracks", NAME)
    fh2_root = os.path.join(fh2_disc, "media", "tracks", NAME)
    out_root = os.path.join(out_disc, "media", "tracks", NAME)
    os.makedirs(os.path.join(out_root, "Ribbon_00"), exist_ok=True)
    C = Combined(ff_root, fh2_root, similar)
    try:
        return _combine(C, ff_disc, fh2_disc, out_disc, out_root)
    finally:
        C.close()


def _combine(C, ff_disc, fh2_disc, out_disc, out_root):
    A, B = C.A, C.B
    PA, PB = A.P, B.P
    rbA, rbB = os.path.join(C.roots[0], "Ribbon_00"), os.path.join(C.roots[1], "Ribbon_00")
    bz = {zone_uid(info): c for info, c in B.zones()}            # FH2's zones, read once
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
    col, col_new, col_taken = combine_collision(open(os.path.join(rbA, f"{NAME}_track_00.col"), "rb").read(),
                                                open(os.path.join(rbB, f"{NAME}_track_00.col"), "rb").read(),
                                                a_files, b_take)
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
    # bundles: only FH2's own textures in them; a bundle of F&F's textures alone is left out
    own_map = {k: C.index[k] for k in C.own}
    bundle_data = {}
    for e in [e for e in needed if B.names[e].lower().endswith(".bundle")]:
        bundle_data[e] = rewrite_bundle(B.zip.read(B.info_of[e]), own_map)
        if bundle_data[e] is None:
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

    # every file's combined name; FH2's that F&F's stands for map to F&F's name
    names = list(A.names)
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
    for _, c in A.zones():
        for f in c["files"] + c.get("files2", []):
            a_res.setdefault((f[3], f[2]), (f[0], f[1]))

    # Resource numbers (a record's second field) in the combined world. Both worlds number a
    # zone's file resource as the zone (every type 1 record of F&F's 6,560 and FH2's 17,332),
    # its zones first, then the rest, no number naming two files. Here: the zones (F&F's and
    # FH2's region's) keep that, F&F's other resources move past the added zones, FH2's past
    # F&F's. Left with FH2's numbers past F&F's, FH2's zones' own resources were beyond the
    # zones and the game broke its stream list (sub_82DD9388) as soon as one went live (even
    # FH2 combined with itself).
    NBZ = len(C.b_zones)
    B_RES = C.RES + NBZ

    def a_record(f):
        own, res, kind, e = f
        return (own, own if kind == ZONE else res + NBZ, kind, e)

    def b_record(f):
        own, res, kind, e = f
        if e not in b_to:
            return None
        if kind == PGEO and f"nopgeo{B.kind(e)}" in TESTS:   # a test: one procedural type left out
            return None
        ne = b_to[e]
        if kind == ff.PRELOAD:
            # FH2's zones preload their neighbours' files (models, procedural geometry, collision,
            # shadows, soundscapes; their own id is the file's number): kept, renumbered. Left
            # out, FH1 corrupted its memory streaming them in (lr 82C12C10).
            return (ne, res + B_RES, kind, ne)
        if ne < len(A.names):    # F&F's file: F&F's numbers
            if kind == FIZ:
                own = int(names[ne].split(".")[0])
            # F&F's own record of this file of the same type. (By the file alone, FH2's base
            # gave a collision record the numbers of a preload record of the same file, whose
            # id is the file's entry: the game broke its stream list as the zone went live.)
            if (ne, kind) in a_res:
                return a_record((*a_res[(ne, kind)], kind, ne))
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
    if len(PA["instances"]) + len(b_used) <= 0xFFFF:
        a_used = set(range(len(PA["instances"])))
    else:
        a_used = set()
        for _, c in A.zones():
            a_used.update(v & 0x7FFFFFFF for v in c["instances"])
    a_inst = {v: k for k, v in enumerate(sorted(a_used))}
    a_whole = len(a_inst) == len(PA["instances"])
    b_inst = {v: len(a_inst) + k for k, v in enumerate(sorted(b_used))}
    ZM = C.NM                      # the added zones' models' shift
    ztex = C.tex
    if TESTS & {"aall", "amodels"}:   # a world combined with itself: the added zones use the base's
        b_inst, ZM, ztex = dict(a_inst), 0, (lambda t: t)
    if len(a_inst) + len(b_used) > 0xFFFF:
        raise ValueError(f"{len(a_inst) + len(b_used)} instances: FH1 takes 65,535")
    report = {"base instances in the region left out of zones round it": 0,
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

    def a_zone(c, uid):
        """F&F's zone: its instances renumbered; round the region, its own instances and
        procedural geometry standing in the region out and FH2's region (as FH2's zone of the
        cell sees it) in, with their models and textures; the region's zones its neighbours."""
        if az_cell.get(uid) in C.region:   # replaced: no cell is its, its numbers kept consistent
            return dict(c, instances=[(v & 0x80000000) | a_inst[v & 0x7FFFFFFF] for v in c["instances"]],
                        files=[a_record(f) for f in c["files"]],
                        **({"files2": [a_record(f) for f in c["files2"]]} if "files2" in c else {}))
        c = dict(c, files=a_neighbours([a_record(f) for f in c["files"]]),
                 **({"files2": a_neighbours([a_record(f) for f in c["files2"]])} if "files2" in c else {}))
        if "noseam" in TESTS:
            return dict(c, instances=[(v & 0x80000000) | a_inst[v & 0x7FFFFFFF] for v in c["instances"]])
        keep = region_part(PA, c, False)
        report["base instances in the region left out of zones round it"] += len(c["instances"]) - len(keep)
        out = dict(c)
        out["instances"] = [(c["instances"][k] & 0x80000000) | a_inst[c["instances"][k] & 0x7FFFFFFF] for k in keep]
        out["drawn"] = [c["drawn"][k] for k in keep]
        out["placements"] = [c["placements"][k] for k in keep]
        for key in ("files", "files2"):
            if key in c:
                out[key] = [f for f in c[key] if not (f[2] == PGEO and f[3] in a_pgeo_drop)]
                report["base procedural geometry entries left out"] += len(c[key]) - len(out[key])
        if uid in seen_from:
            bc, ks = seen_from[uid]
            report["region instances joining zones round it"] += len(ks)
            out["instances"] += [(bc["instances"][k] & 0x80000000) | b_inst[bc["instances"][k] & 0x7FFFFFFF] for k in ks]
            out["drawn"] += [bc["drawn"][k] for k in ks]
            out["placements"] += [bc["placements"][k] for k in ks]
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
        out["placements"] = [c["placements"][k] for k in keep]
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
    instances = [PA["instances"][v] for v in sorted(a_used)]
    for v in sorted(b_used):
        i = PB["instances"][v]
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
    rb = os.path.join(out_root, "Ribbon_00")
    wr = ff.TrackZipWriter(os.path.join(out_root, "bin.zip"))
    try:
        for k, n in enumerate(names):
            if k < len(A.names):
                info = A.info_of[k]
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
                    bi = B.info_of[B.entry[f"{col_taken[int(n.split('.')[0])]}.fiz"]]
                    wr.add_raw(info.filename, bi, B.zip.raw(bi))   # the region's collision
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
                wr.add(folder + n, bundle_data[e])
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
    # the game's other files the build reads: F&F's
    for rel in (("media", "db", "gamedb.slt"), ("media", "physics.zip"), ("media", "aiopenworld.zip"),
                ("media", "dynamicpost.zip")):
        src = os.path.join(ff_disc, *rel)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(out_disc, *rel)), exist_ok=True)
            shutil.copyfile(src, os.path.join(out_disc, *rel))
    xex = os.path.join(ff_disc, "default.xex")
    if os.path.isfile(xex):
        shutil.copyfile(xex, os.path.join(out_disc, "default.xex"))
    return {"zones": f"{len(PA['zones'])} base + {len(C.b_zones)} added", "files": len(names),
            "models": len(models), "textures": len(textures), "added textures that are the base's": C.twins,
            "bundles": f"{len(bundle_data)} named, {sum(1 for d in bundle_data.values() if d is None)} left out",
            "region": f"{len(C.region)} cells, {sum(1 for c in C.region if c in C.hexA)} of the base's replaced",
            "collision cells": struct.unpack_from(">I", col, 0x4C)[0],
            "shadow squares the region's": len(sh_take),
            "collision objects": f"{coll_a.count('<Obj')} in the base, {collobjs.count('<Obj')} combined",
            "road closures left out (objects, instances)": (len(gone), len(drop_b)), **report}


def main(argv):
    args = list(argv[1:])
    similar = SIMILAR
    if "--similar" in args:
        k = args.index("--similar")
        similar = float(args[k + 1])
        del args[k:k + 2]
    if len(args) != 3 or not 0 <= similar <= 1:
        print(__doc__)
        return 2
    for key, value in combine(*args, similar=similar).items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
