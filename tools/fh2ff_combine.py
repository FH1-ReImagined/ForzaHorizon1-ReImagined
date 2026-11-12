#!/usr/bin/env python3
"""One world from Forza Horizon 2 Presents Fast & Furious's and Forza Horizon 2's (#821).

F&F's world is FH2's southern Europe cut to an area round Nice and edited throughout; FH2's is the
whole map. Both are media/tracks/Anthem on the same hex grid (F&F's is FH2's first 66 of 104
columns). This writes a third world folder in their own format (layout version 51): F&F's zones
in every cell F&F has one, FH2's in the rest, which tools/fh2ff_world.py then builds as it builds
either:

  tools/fh2ff_combine.py FF_DISC FH2_DISC OUT_DISC
  tools/fh2ff_world.py build OUT_DISC FH1_DISC UPDATE_MEDIA_ZIP OUT --fh2 FH2_DISC

OUT_DISC gets media/tracks/Anthem (the combined world), and F&F's database, physics and AI lines.
What is combined (fnf_map/COMBINED_MAP_PLAN.md has the measurements):

- The layout: F&F's tables, then FH2's after them. FH2's models' texture records, instances'
  models and lightmap records, and its zones' instance, model and texture numbers move by F&F's
  counts; shaders are shared by name.
- FH2's files take numbers and ids after F&F's where their name holds one: model files
  (aout.NNNNN.rmb.bin), procedural geometry (__r00gNNNNN.pgeo, and an animated scene's references
  to them), textures (_0x<id>.bix, _B.bix, .bin: the texture record's id), bundles
  (_0x1000NNNN.bundle), zones (__R00ZNNNNN.pvsz, anthem_00_NNNNN.soundscape) and collision cells
  (N.fiz, numbered by the collision index's records). Shadow squares (.sh, named by place) are
  F&F's where F&F has the square.
- A zone's file records (own id, resource number, type, file) are renumbered the same way; FH2's
  resource numbers move past F&F's, and a file both worlds name (a collision cell or shadow
  square F&F has) is F&F's, with F&F's numbers.
- The hex grid, the zone lookup and speeds, and the collision index hold both worlds' zones and
  cells. The rest of the world folder (lighting, road network, ribbon XML) is F&F's for now.

What it reads and writes is the player's own: never share the output.
"""
import os
import re
import shutil
import struct
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fm4_pvs  # noqa: E402
import fh2ff_world as ff  # noqa: E402

NAME = ff.NAME
TEXTURE_ID_STEP = 0x2900       # FH2's texture ids move this far: past F&F's (to 10,403), and all
                               # under 21,000 as Colorado's are (to 20,519). Moved 0x10000 (to 75,800),
                               # FH1 corrupted its memory near F&F's edge (a list of the main loop, lr 82C12C10)
BUNDLE_ID_STEP = 0x8000        # and its bundle ids (0x1000NNNN) this far
REC = ">6I"                    # a collision index record: number, cell, offset, size, 0, end
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


def scene_refs(d, shift):
    """An animated scene (procedural type 5) with its geometry references (procedural geometry
    file numbers) moved by shift; other files as they are."""
    if d[:4] != b"OEGP" or struct.unpack_from(">I", d, 0x30)[0] != ff.ANIMATED_SCENE:
        return d
    nlen, ntex, ngeo = struct.unpack_from(">3I", d, 0x3C)
    at = 0x60 + (ff.DYN_FH2 if struct.unpack_from(">I", d, 0x58)[0] else 0) + ((nlen + 3) & ~3) + 8 * ntex
    out = bytearray(d)
    for k in range(ngeo):
        struct.pack_into(">I", out, at + 8 * k, struct.unpack_from(">I", d, at + 8 * k)[0] + shift)
    return bytes(out)


def shift_bundle(d, step):
    """A texture bundle (a count, then a texture a record: its texture id, width, height, levels,
    format, -1, size, the data) with every texture id moved by step: they name the world's texture
    records, whose ids FH2's move. Left as they were, FH2's bundled textures were registered under
    F&F's ids."""
    out = bytearray(d)
    n, p = struct.unpack_from(">I", d, 0)[0], 4
    for _ in range(n):
        struct.pack_into(">I", out, p, struct.unpack_from(">I", d, p)[0] + step)
        p += 28 + struct.unpack_from(">I", d, p + 24)[0]
    if p != len(d):
        raise ValueError("a bundle that does not end with its last texture")
    return bytes(out)


class Combined:
    """The two worlds read (ff.World) and the combined one's numbering."""

    def __init__(self, ff_root, fh2_root):
        self.A, self.B = ff.World(ff_root), ff.World(fh2_root)
        self.roots = (ff_root, fh2_root)
        rb = lambda root, n: open(os.path.join(root, "Ribbon_00", n), "rb").read()  # noqa: E731
        self.hexA, self.gridA = hex_cells(rb(ff_root, f"{NAME}_00.hex"))
        self.hexB, self.gridB = hex_cells(rb(fh2_root, f"{NAME}_00.hex"))
        if self.gridA[1:4] != self.gridB[1:4]:
            raise ValueError("the two worlds' hex grids differ")
        PA, PB = self.A.P, self.B.P
        self.NT, self.NM, self.NI = len(PA["textures"]), len(PA["models"]), len(PA["instances"])
        self.NPG = PA["pgeo"]
        self.RES = PA["resources"]
        # FH2's zones in the cells F&F has none in, numbered after F&F's
        taken = set(self.hexA)
        self.b_zones = sorted({uid for cell, uid in self.hexB.items() if cell not in taken})
        self.zone_new = {uid: len(PA["zones"]) + k for k, uid in enumerate(self.b_zones)}
        # textures and bundles: FH2's ids moved
        self.tex_ids_B = {t[0] for t in PB["textures"]}
        bundles_A = {int(n[3:11], 16) for n in self.A.names if n.lower().endswith(".bundle")}
        self.bundle_new = {}
        for n in self.B.names:
            if n.lower().endswith(".bundle"):
                b = int(n[3:11], 16)
                self.bundle_new[b] = b + BUNDLE_ID_STEP
        if bundles_A & set(self.bundle_new.values()):
            raise ValueError("FH2's moved bundle ids meet F&F's")
        if max(t[0] for t in PA["textures"]) >= TEXTURE_ID_STEP:
            raise ValueError("F&F's texture ids reach FH2's moved ones")

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
            if v in self.tex_ids_B:
                return f"_0x{v + TEXTURE_ID_STEP:08X}{rest}"
        return name


def combine_collision(colA, colB, a_files):
    """The collision index with F&F's records (its numbering kept), FH2's cells taking a place F&F
    lists without a file (F&F's index covers almost all of FH2's grid, its files only its own
    area) and the cells F&F does not list numbered on. Every record's place in the stream is laid
    out again in order. Returns (the index, {FH2 record: its number in the index})."""
    nA, nB = struct.unpack_from(">I", colA, 0x4C)[0], struct.unpack_from(">I", colB, 0x4C)[0]
    ra = [list(struct.unpack_from(REC, colA, 0x58 + 24 * k)) for k in range(nA)]
    rb = [list(struct.unpack_from(REC, colB, 0x58 + 24 * k)) for k in range(nB)]
    at_cell = {r[1]: r[0] for r in ra}
    new = {}
    for r in rb:
        k = at_cell.get(r[1])
        if k is not None:
            if k in a_files:
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
    return bytes(out), new


def combine(ff_disc, fh2_disc, out_disc):
    ff_root = os.path.join(ff_disc, "media", "tracks", NAME)
    fh2_root = os.path.join(fh2_disc, "media", "tracks", NAME)
    out_root = os.path.join(out_disc, "media", "tracks", NAME)
    os.makedirs(os.path.join(out_root, "Ribbon_00"), exist_ok=True)
    C = Combined(ff_root, fh2_root)
    try:
        return _combine(C, ff_disc, fh2_disc, out_disc, out_root)
    finally:
        C.close()


def _combine(C, ff_disc, fh2_disc, out_disc, out_root):
    A, B = C.A, C.B
    PA, PB = A.P, B.P
    rbA, rbB = os.path.join(C.roots[0], "Ribbon_00"), os.path.join(C.roots[1], "Ribbon_00")
    a_files = {int(n.split(".")[0]) for n in A.names if re.fullmatch(r"\d+\.fiz", n.lower())}
    col, col_new = combine_collision(open(os.path.join(rbA, f"{NAME}_track_00.col"), "rb").read(),
                                     open(os.path.join(rbB, f"{NAME}_track_00.col"), "rb").read(), a_files)
    shadows_A = {n.lower() for n in A.names if n.lower().endswith(".sh")}

    # FH2's files the added zones use: their file records, their models' and textures' files,
    # and the animated objects their scenes play (the rest are FH2's own zones in F&F's area)
    needed = set()
    tex_files = {}
    for e, n in enumerate(B.names):
        m = re.fullmatch(r"_0x([0-9a-f]{8}).*", n.lower())
        if m:
            tex_files.setdefault(int(m.group(1), 16), []).append(e)
    for info, c in B.zones():
        base = info.filename.replace("\\", "/").split("/")[-1].lower()
        if int(re.fullmatch(r"__r\d\dz(\d+)\.pvsz", base).group(1)) not in C.zone_new:
            continue
        for f in c["files"] + c.get("files2", []):
            needed.add(f[3])
        textures = set(c["textures"] + c.get("textures2", []))
        for mdl in c["models"] + c.get("models2", []):
            if mdl in B.model_entry:
                needed.add(B.model_entry[mdl])
            textures.update(PB["models"][mdl]["textures"])   # a model's own, the zone's list or not
        for v in c["instances"]:
            i = PB["instances"][v & 0x7FFFFFFF]
            if i["model"] in B.model_entry:                    # the model an instance draws (the
                needed.add(B.model_entry[i["model"]])          # zone's model list may not hold it)
                textures.update(PB["models"][i["model"]]["textures"])
            if i["u8"] != 0xFFFFFFFF:
                textures.add(i["idx"])                         # an instance's own lightmap
        for t in textures:
            if t < len(PB["textures"]):
                needed.update(tex_files.get(PB["textures"][t][0], ()))
    for e in list(needed):
        n = B.names[e].lower()
        if n.endswith(".pgeo"):
            d = B.zip.read(B.info_of[e])
            if d[:4] == b"OEGP" and struct.unpack_from(">I", d, 0x30)[0] == ff.ANIMATED_SCENE:
                nlen, ntex, ngeo = struct.unpack_from(">3I", d, 0x3C)
                at = 0x60 + (ff.DYN_FH2 if struct.unpack_from(">I", d, 0x58)[0] else 0) + ((nlen + 3) & ~3) + 8 * ntex
                for k in range(ngeo):
                    g = B.entry.get(f"__r00g{struct.unpack_from('>I', d, at + 8 * k)[0]:05d}.pgeo")
                    if g is not None:
                        needed.add(g)
    for e, n in enumerate(B.names):
        low = n.lower()
        if low.endswith(".fxobj"):
            needed.add(e)
        m = re.fullmatch(rf"(?:__r\d\dz|{NAME.lower()}_\d\d_)(\d+)\.(?:pvsz|soundscape)", low)
        if m and int(m.group(1)) in C.zone_new:
            needed.add(e)          # an added zone's own file and soundscape

    # every file's combined name; FH2's that F&F's stands for map to F&F's name
    names = list(A.names)
    a_index = {n.lower(): k for k, n in enumerate(names)}
    b_to = {}                      # FH2 entry -> combined entry
    b_member = {}                  # combined entry (an FH2 file) -> FH2 entry
    for e, n in enumerate(B.names):
        if e not in needed:
            continue
        new = C.b_name(n, col_new, shadows_A)
        low = n.lower()
        if new is None:
            if low.endswith((".sh",)) and low in a_index:
                b_to[e] = a_index[low]
            elif low.endswith(".fiz"):
                pass           # a cell F&F has: the zone's record goes to F&F's (below)
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
    a_ids = {}
    for _, c in A.zones():
        for f in c["files"] + c.get("files2", []):
            a_ids.setdefault(f[3], (f[0], f[1]))

    def b_record(f):
        own, res, kind, e = f
        if e not in b_to:
            return None
        ne = b_to[e]
        if kind == ff.PRELOAD:
            # FH2's zones preload their neighbours' files (models, procedural geometry, collision,
            # shadows, soundscapes; their own id is the file's number): kept, renumbered. Left
            # out, FH1 corrupted its memory streaming them in (lr 82C12C10).
            return (ne, res + C.RES, kind, ne)
        if ne < len(A.names):    # F&F's file: F&F's numbers
            if kind == FIZ:
                own = int(names[ne].split(".")[0])
            if ne in a_ids:
                own, res = a_ids[ne]
                return (own, res, kind, ne)
            return (own, res + C.RES, kind, ne)
        if kind in (ZONE, SOUNDSCAPE):
            own = C.zone_new.get(own, own)
        elif kind == PGEO:
            own += C.NPG
        elif kind == BUNDLE:
            own = C.bundle_new.get(own, own)
        elif kind == FIZ:
            own = col_new.get(own, own)
        return (own, res + C.RES, kind, ne)

    # the instances: only those the zones use (F&F's zones use 26,482 of its 46,625), F&F's then
    # FH2's. Both tables whole are 97,807 and FH1 drove neither world past 65,535 (Colorado has
    # 62,173): with them every FH2 zone faulted soon after it went live.
    a_used, b_used = set(), set()
    for _, c in A.zones():
        a_used.update(v & 0x7FFFFFFF for v in c["instances"])
    for info, c in B.zones():
        base = info.filename.replace("\\", "/").split("/")[-1].lower()
        if int(re.fullmatch(r"__r\d\dz(\d+)\.pvsz", base).group(1)) in C.zone_new:
            b_used.update(v & 0x7FFFFFFF for v in c["instances"])
    a_inst = {v: k for k, v in enumerate(sorted(a_used))}
    b_inst = {v: len(a_inst) + k for k, v in enumerate(sorted(b_used))}

    def a_zone(c):
        return dict(c, instances=[(v & 0x80000000) | a_inst[v & 0x7FFFFFFF] for v in c["instances"]])

    def b_zone(c):
        out = dict(c)
        out["instances"] = [(v & 0x80000000) | b_inst[v & 0x7FFFFFFF] for v in c["instances"]]
        out["models"] = [m + C.NM for m in c["models"]]
        out["textures"] = [t + C.NT for t in c["textures"]]
        out["files"] = [r for r in (b_record(f) for f in c["files"]) if r]
        if "files2" in c:
            out["models2"] = [m + C.NM for m in c["models2"]]
            out["textures2"] = [t + C.NT for t in c["textures2"]]
            out["files2"] = [r for r in (b_record(f) for f in c["files2"]) if r]
        return out

    # the layout: F&F's tables, FH2's after them
    shader_at = {s.lower(): k for k, s in enumerate(PA["shaders"])}
    shaders = list(PA["shaders"])
    smap = []
    for s in PB["shaders"]:
        if s.lower() not in shader_at:
            shader_at[s.lower()] = len(shaders)
            shaders.append(s)
        smap.append(shader_at[s.lower()])
    textures = list(PA["textures"]) + [(t[0] + TEXTURE_ID_STEP, t[1] + C.NT) + tuple(t[2:]) for t in PB["textures"]]
    models = list(PA["models"]) + [dict(m, textures=[t + C.NT for t in m["textures"]],
                                         shaders=[smap[s] for s in m["shaders"]]) for m in PB["models"]]
    instances = [PA["instances"][v] for v in sorted(a_used)]
    for v in sorted(b_used):
        i = PB["instances"][v]
        j = dict(i, model=i["model"] + C.NM)
        if i["u8"] != 0xFFFFFFFF:
            j["idx"] = i["idx"] + C.NT      # the instance's own lightmap record
        instances.append(j)
    zones_table = list(PA["zones"]) + [PB["zones"][uid] for uid in C.b_zones]
    P = dict(PA, textures=textures, shaders=shaders, models=models, instances=instances,
             zones=zones_table, names=PB["names"], pgeo=C.NPG + PB["pgeo"],
             resources=C.RES + PB["resources"], rmodels=PA["rmodels"] + PB["rmodels"])

    # the archive
    zone_member = {}   # combined entry -> new zone bytes
    a_entry_of = {i.filename.replace("\\", "/").split("/")[-1].lower(): k for k, i in enumerate(A.info_of)}
    a_zone_files = {}
    for info, c in A.zones():
        base = info.filename.replace("\\", "/").split("/")[-1].lower()
        a_zone_files[a_index[base]] = write_cell_51(a_zone(c), PA["g2"])
    b_zone_files = {}
    for info, c in B.zones():
        base = info.filename.replace("\\", "/").split("/")[-1].lower()
        uid = int(re.fullmatch(r"__r\d\dz(\d+)\.pvsz", base).group(1))
        if uid in C.zone_new:
            b_zone_files[a_index[f"__r00z{C.zone_new[uid]:05d}.pvsz"]] = write_cell_51(b_zone(c), PB["g2"])
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
                else:
                    wr.add_raw(info.filename, info, A.zip.raw(info))
                continue
            e = b_member[k]
            info = B.info_of[e]
            folder = info.filename.replace("\\", "/").rsplit("/", 1)[0] + "/" if "/" in info.filename.replace("\\", "/") else ""
            if k in b_zone_files:
                wr.add(folder + n, b_zone_files[k])
            elif n.lower().endswith(".bundle"):
                wr.add(folder + n, shift_bundle(B.zip.read(info), TEXTURE_ID_STEP))
            elif n.lower().endswith(".pgeo"):
                d = B.zip.read(info)
                d2 = scene_refs(d, C.NPG)
                if d2 is d:
                    wr.add_raw(folder + n, info, B.zip.raw(info))
                else:
                    wr.add(folder + n, d2)
            else:
                wr.add_raw(folder + n, info, B.zip.raw(info))
    finally:
        wr.close()

    # the ribbon: layout, hex, file map, zone lookup, collision index; the rest F&F's
    for f in os.listdir(rbA):
        if f.lower().startswith("trackroute") or f.lower().endswith((".xml",)):
            shutil.copyfile(os.path.join(rbA, f), os.path.join(rb, f))
    open(os.path.join(rb, f"{NAME}_00.pvs"), "wb").write(write_layout_51(P))
    open(os.path.join(rb, "FilenameMap_00.dat"), "wb").write(fm4_pvs.write_filename_map(names))
    look = [(a_index[f"__r00z{uid:05d}.pvsz"], uid) for uid in range(len(zones_table))]
    open(os.path.join(rb, "PVSZLookup_00.dat"), "wb").write(b"".join(struct.pack(">ii", e, u) for e, u in look))
    open(os.path.join(rb, f"{NAME}_track_00.col"), "wb").write(col)
    hexA = open(os.path.join(rbA, f"{NAME}_00.hex"), "rb").read()
    ver, side, ox, oz, nzA, w, h = struct.unpack_from(">ifffiii", hexA, 4)
    gw, gh = C.gridB[4], C.gridB[5]
    cells = [-1] * (gw * gh)
    for (cx, cy), uid in C.hexA.items():
        cells[cy * gw + cx] = uid
    for (cx, cy), uid in C.hexB.items():
        if (cx, cy) not in C.hexA:
            cells[cy * gw + cx] = C.zone_new[uid]
    nz = len(zones_table)
    # after the cells: an 8-byte record a zone, then FH1's byte a zone (F&F's 102 as 101)
    a_rest = ff.convert_hex(hexA)[32 + 4 * w * h:]
    hexB = open(os.path.join(rbB, f"{NAME}_00.hex"), "rb").read()
    b_rest = ff.convert_hex(hexB)[32 + 4 * gw * gh:]
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
    return {"zones": f"{len(PA['zones'])} F&F + {len(C.b_zones)} FH2", "files": len(names),
            "models": len(models), "textures": len(textures), "collision cells": struct.unpack_from(">I", col, 0x4C)[0]}


def main(argv):
    if len(argv) != 4:
        print(__doc__)
        return 2
    print(combine(*argv[1:]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
