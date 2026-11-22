#!/usr/bin/env python3
"""Build another game's car geometry into a Forza Horizon (2012) car's .carbin models (#930).

A plain-Python port of the geometry builder in Forza Imported DLC Maker (builder/fh1_build.py and
builder/fh6_geometry.py, warshack4874). An FH1 car is the donor: its header (light list, exhaust
emitters, the per-LOD draw lists), its section list and order, and each section's pivot are kept.
A section the new car has a part for gets that part's geometry; an upgrade variant with no part of
its own (bumperFb) gets its stock part's (bumperFa); a section named in `keep` (the wheel) stays the
donor's; any other section is emptied (one zero-area triangle where the donor drew it). The draw
lists are rebuilt for the new meshes.

Geometry is given as Pieces in FH1's model space: x to the car's right (its left is -x), y up, z
to the rear (the front is -z), triangles wound so that cross(b - a, c - a) points along the
vertex normals (orient() makes sure). Each mesh is encoded as FH1's are (the DLC Maker measured
its encoding on 99 vertex pools of a shipped car, positions within 0.04 mm):

  position : local = pos - section.offset; v = local / |bmax|; xyz = v / |v| (SNORM16), w = |v|
  bounds   : bmin = -bmax (symmetric half extents)
  frame    : an SNORM16 quaternion whose rotation has the normal, the tangent (+dP/du of uv1) and
             their cross product as columns (fh1_carbin.quat_normal reads the normal back)
  uv       : UNORM16 within the mesh's own uv rectangle; the mesh's uvxf maps them back
             (u offset, u scale, v offset, v scale, for uv0 then uv1)
  SH0      : 4 x s8 per vertex (the baked lighting), a linear fit on the normal from the donor's
             own vertices (Forza Imported DLC Maker fits them as unsigned bytes)
  indices  : triangle strips (type 6), 32-bit, restart 0x00FFFFFF

Every other mesh field is copied from the donor's first mesh (they are the same in all of a
shipped car's meshes). Standard library only.
"""
import copy
import math
import random
import re
import struct
from collections import Counter
from dataclasses import dataclass, replace

import fh1_carbin as fc

RESTART = fc.RESTART32
VERTEX = struct.Struct(">4h2H2H4h4b")
VARIANT = re.compile(r"^(?P<root>.+?)(?P<var>[b-z]|race)$")


@dataclass
class Piece:
    section: str   # the donor section it goes into
    material: str  # an FH1 shader technique the donor's file uses
    lod: int       # 0 in the _lod0 and _cockpit models, 1-5 in the main one
    pos: list      # [(x, y, z)] FH1 model space
    nrm: list      # [(x, y, z)] unit normals
    uv0: list      # [(u, v)] in the car's texture atlases (nodamage, lights...)
    uv1: list      # [(u, v)] the detail mapping (the tangent frame follows it)
    tris: list     # [(a, b, c)] indices into pos


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def unit(a, fallback=(1.0, 0.0, 0.0)):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else fallback


def tangents(pos, nrm, uv, tris):
    """Per-vertex unit tangents (+dP/du), perpendicular to the normals."""
    acc = [[0.0, 0.0, 0.0] for _ in pos]
    for a, b, c in tris:
        e1, e2 = _sub(pos[b], pos[a]), _sub(pos[c], pos[a])
        d1u, d1v = uv[b][0] - uv[a][0], uv[b][1] - uv[a][1]
        d2u, d2v = uv[c][0] - uv[a][0], uv[c][1] - uv[a][1]
        r = d1u * d2v - d2u * d1v
        r = 1.0 if abs(r) < 1e-12 else 1.0 / r
        s = ((e1[0] * d2v - e2[0] * d1v) * r, (e1[1] * d2v - e2[1] * d1v) * r, (e1[2] * d2v - e2[2] * d1v) * r)
        for k in (a, b, c):
            t = acc[k]
            t[0] += s[0]
            t[1] += s[1]
            t[2] += s[2]
    out = []
    for t, n in zip(acc, nrm):
        d = _dot(n, t)
        t = (t[0] - n[0] * d, t[1] - n[1] * d, t[2] - n[2] * d)
        if _dot(t, t) < 1e-16:  # no uv gradient: any direction perpendicular to the normal
            alt = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
            d = _dot(n, alt)
            t = (alt[0] - n[0] * d, alt[1] - n[1] * d, alt[2] - n[2] * d)
        out.append(unit(t))
    return out


def frame_quaternion(n, t):
    """(x, y, z, w) of the rotation with columns n, t, n x t (t made perpendicular to n first)."""
    n = unit(n)
    d = _dot(n, t)
    t = unit((t[0] - n[0] * d, t[1] - n[1] * d, t[2] - n[2] * d), (0.0, 1.0, 0.0) if abs(n[1]) < 0.9 else (1.0, 0.0, 0.0))
    b = _cross(n, t)
    m00, m01, m02 = n[0], t[0], b[0]
    m10, m11, m12 = n[1], t[1], b[1]
    m20, m21, m22 = n[2], t[2], b[2]
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(max(tr + 1.0, 1e-12)) * 2
        q = ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(max(1.0 + m00 - m11 - m22, 1e-12)) * 2
        q = (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    elif m11 > m22:
        s = math.sqrt(max(1.0 + m11 - m00 - m22, 1e-12)) * 2
        q = ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    else:
        s = math.sqrt(max(1.0 + m22 - m00 - m11, 1e-12)) * 2
        q = ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
    k = math.sqrt(sum(c * c for c in q)) or 1.0
    return (q[0] / k, q[1] / k, q[2] / k, q[3] / k)


def _solve(a, b):
    """x with a x = b (a: n x n, b: n x m), Gaussian elimination with partial pivoting."""
    n, m = len(a), len(b[0])
    a = [list(r) + list(rb) for r, rb in zip(a, b)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        a[col], a[piv] = a[piv], a[col]
        if abs(a[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                f = a[r][col] / a[col][col]
                if f:
                    a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [[a[r][n + j] / a[r][r] if abs(a[r][r]) >= 1e-12 else 0.0 for j in range(m)] for r in range(n)]


class SHModel:
    """A vertex's SH0, its baked lighting (4 x s8: a constant and three linear terms, which on FH1's
    cars follow the normal's z, x and y), as a linear function of its normal, fitted on the donor's
    own vertices (least squares, by the normal equations). The bytes are signed: on the RX-7, a fit
    read as unsigned misses by 75-89 per byte, signed by 30-38."""

    SECTION_MIN = 200    # vertices a section needs for a fit of its own (by_section)

    def __init__(self, carbins, by_section=False, coef=None):
        """by_section: each section also gets a fit on the donor's section of that name (section()),
        for sections with SECTION_MIN vertices: one fit over the whole car mixes the dark interior,
        glass and underside into the paint's (FM1's MX-5 body averaged 80 where the '94 Miata's own
        body has 94, and drew darker in the same paint)."""
        self.sections = {}
        if coef is not None:
            self.coef = coef
            return
        total = ([[0.0] * 4 for _ in range(4)], [[0.0] * 4 for _ in range(4)], [0])
        per = {}
        for c in carbins:
            for s in c.sections:
                acc = per.setdefault(getattr(s, "name", ""), ([[0.0] * 4 for _ in range(4)], [[0.0] * 4 for _ in range(4)], [0]))
                for pool in (s.lod, s.lod0):
                    if pool.count < 16 or pool.stride != 28:
                        continue
                    for qx, qy, qz, qw, b0, b1, b2, b3 in struct.iter_unpack(">16x4h4b", pool.data):
                        n = fc.quat_normal((qx, qy, qz, qw))
                        row = (1.0, n[0], n[1], n[2])
                        ys = (b0, b1, b2, b3)
                        for ata, aty, cnt in (total, acc):
                            cnt[0] += 1
                            for i in range(4):
                                ri = row[i]
                                ai, yi = ata[i], aty[i]
                                for j in range(4):
                                    ai[j] += ri * row[j]
                                    yi[j] += ri * ys[j]
        ata, aty, _ = total
        self.coef = _solve(ata, aty) if ata[0][0] else [[80.0, 0.0, 0.0, 0.0], [0.0] * 4, [0.0] * 4, [0.0] * 4]
        if by_section:
            for name, (a, y, cnt) in per.items():
                if cnt[0] >= self.SECTION_MIN:
                    try:
                        self.sections[name] = SHModel(None, coef=_solve(a, y))
                    except (ZeroDivisionError, ValueError):
                        pass

    def section(self, name):
        """The fit for a section of this name (by_section), else the whole car's."""
        return self.sections.get(name, self)

    def __call__(self, n):
        c = self.coef
        return tuple(max(-127, min(127, int(round(c[0][k] + c[1][k] * n[0] + c[2][k] * n[1] + c[3][k] * n[2]))))
                     for k in range(4))


def _s16(x):
    return int(round(max(-1.0, min(1.0, x)) * 32767))


def _u16(x):
    return int(round(max(0.0, min(1.0, x)) * 65535))


def encode_vertices(pos, nrm, uv0, uv1, offset, radius, sh, tris):
    """28-byte vertices; uv0 and uv1 already within 0..1 of the mesh's uv rectangle."""
    tan = tangents(pos, nrm, uv1, tris)
    inv = 1.0 / max(radius, 1e-9)
    out = bytearray()
    for p, n, a, b, t in zip(pos, nrm, uv0, uv1, tan):
        v = ((p[0] - offset[0]) * inv, (p[1] - offset[1]) * inv, (p[2] - offset[2]) * inv)
        w = math.sqrt(_dot(v, v))
        xyz = (v[0] / w, v[1] / w, v[2] / w) if w > 1e-9 else (1.0, 0.0, 0.0)
        q = frame_quaternion(n, t)
        out += VERTEX.pack(_s16(xyz[0]), _s16(xyz[1]), _s16(xyz[2]), _s16(min(w, 1.0)),
                           _u16(a[0]), _u16(a[1]), _u16(b[0]), _u16(b[1]),
                           _s16(q[0]), _s16(q[1]), _s16(q[2]), _s16(q[3]), *sh(n))
    return bytes(out)


def to_strip(tris):
    """Triangle list -> one strip with restarts, joining each triangle to the next one when it
    continues the strip with the right winding."""
    out = []
    i, n = 0, len(tris)
    while i < n:
        strip = list(tris[i])
        i += 1
        while i < n:
            p, q = strip[-2], strip[-1]
            t = tris[i]
            odd = len(strip) % 2 == 1  # the next strip triangle (p, q, r) is wound (q, p, r)
            nxt = None
            for k in range(3):
                x0, x1, x2 = t[k], t[(k + 1) % 3], t[(k + 2) % 3]
                if ((x1, x0) if odd else (x0, x1)) == (p, q):
                    nxt = x2
                    break
            if nxt is None:
                break
            strip.append(nxt)
            i += 1
        out += strip + [RESTART]
    return out[:-1]


def orient(pos, nrm, tris):
    """Each triangle wound to agree with its vertex normals."""
    out = []
    for a, b, c in tris:
        fn = _cross(_sub(pos[b], pos[a]), _sub(pos[c], pos[a]))
        vn = (nrm[a][0] + nrm[b][0] + nrm[c][0], nrm[a][1] + nrm[b][1] + nrm[c][1], nrm[a][2] + nrm[b][2] + nrm[c][2])
        out.append((c, b, a) if _dot(fn, vn) < 0 else (a, b, c))
    return out


def materials(c):
    return {sb.name for s in c.sections for sb in s.subs}


def transparent_materials(carbins):
    """Materials the donor draws in the first (transparent) lists more often than in the second."""
    t, o = Counter(), Counter()
    for c in carbins:
        _, arrays, _ = fc.draw_lists(c.header)
        half = len(arrays) // 2
        for ai, a in enumerate(arrays):
            for e in a:
                s, k = e >> 16, e & 0xFFFF
                if s < len(c.sections) and k < len(c.sections[s].subs):
                    (t if ai < half else o)[c.sections[s].subs[k].name] += 1
    return {m for m in t if t[m] > o[m]}


def regenerate_draw_lists(new, old, transparent, untouched):
    """The per-LOD lists for the new meshes. An untouched section keeps the donor's entries; a
    rebuilt one is drawn at the LODs the donor drew it at."""
    _, arrays, _ = fc.draw_lists(old.header)
    half = len(arrays) // 2

    def valid(e):
        return (e >> 16) < len(old.sections) and (e & 0xFFFF) < len(old.sections[e >> 16].subs)
    out = []
    for ai, a in enumerate(arrays):
        pair = arrays[ai % half] + arrays[half + ai % half]
        lods = Counter(old.sections[e >> 16].subs[e & 0xFFFF].lod for e in pair if valid(e))
        lod = lods.most_common(1)[0][0] if lods else (0 if half == 1 else ai % half + 1)
        want_t = ai < half
        lst = [e for e in a if valid(e) and old.sections[e >> 16].name in untouched]
        for si in sorted({e >> 16 for e in pair if valid(e)}):
            if new.sections[si].name in untouched:
                continue
            for k, sb in enumerate(new.sections[si].subs):
                if sb.lod == lod and (sb.name in transparent) == want_t:
                    lst.append((si << 16) | k)
        out.append(lst)
    new.header = fc.set_draw_lists(new.header, out)


def stock_section(name, names):
    """The stock section an upgrade variant stands in for (bumperFb -> bumperFa), or None."""
    m = VARIANT.match(name)
    return m.group("root") + "a" if m and m.group("root") + "a" in names else None


def _empty_piece(sec, lod):
    """A zero-area triangle at the section's pivot, in a material the donor's section has."""
    o = tuple(sec.offset)
    mat = sec.subs[0].name if sec.subs else "black"
    return Piece(sec.name, mat, lod, [o, o, o], [(0.0, 1.0, 0.0)] * 3, [(0.0, 0.0)] * 3, [(0.0, 0.0)] * 3, [(0, 1, 2)])


def _rect(uvs):
    lo = (min(u for u, _ in uvs), min(v for _, v in uvs))
    hi = (max(u for u, _ in uvs), max(v for _, v in uvs))
    return lo, (hi[0] - lo[0], hi[1] - lo[1])


def _encode_pool(mine, offset, radius, sh, tpl, rng):
    """(Pool, meshes) of pieces: one mesh per LOD and material, their vertices in one pool."""
    vbytes, subs, base = [], [], 0
    groups = {}
    for p in mine:
        groups.setdefault((p.lod, p.material), []).append(p)
    for (lod, mat), ps in sorted(groups.items()):
        used_all = [sorted({k for t in p.tris for k in t}) for p in ps]
        (u0lo, u0rng) = _rect([p.uv0[k] for p, used in zip(ps, used_all) for k in used])
        (u1lo, u1rng) = _rect([p.uv1[k] for p, used in zip(ps, used_all) for k in used])
        div0 = tuple(r if r > 1e-6 else 1.0 for r in u0rng)
        div1 = tuple(r if r > 1e-6 else 1.0 for r in u1rng)
        idx = []
        for p, used in zip(ps, used_all):
            remap = {k: j for j, k in enumerate(used)}
            pos = [p.pos[k] for k in used]
            nrm = [p.nrm[k] for k in used]
            tris = orient(pos, nrm, [(remap[a], remap[b], remap[c]) for a, b, c in p.tris])
            uv0 = [((p.uv0[k][0] - u0lo[0]) / div0[0], (p.uv0[k][1] - u0lo[1]) / div0[1]) for k in used]
            uv1 = [((p.uv1[k][0] - u1lo[0]) / div1[0], (p.uv1[k][1] - u1lo[1]) / div1[1]) for k in used]
            vbytes.append(encode_vertices(pos, nrm, uv0, uv1, offset, radius, sh, tris))
            idx += [(a + base, b + base, c + base) for a, b, c in tris]
            base += len(used)
        sb = copy.deepcopy(tpl)
        sb.name, sb.lod, sb.index_type, sb.index_size = mat, lod, 6, 4
        sb.uvxf = [u0lo[0], u0rng[0], u0lo[1], u0rng[1], u1lo[0], u1rng[0], u1lo[1], u1rng[1]]
        sb.indices = to_strip(idx)
        subs.append(sb)
    return fc.Pool(base, 28, [1, rng.randrange(1, 0xFFFFFFFF)], b"".join(vbytes)), subs


def section_pieces(sec, names=None):
    """The donor section's own meshes as Pieces (the model's positions, normals and uvs), for
    keeping some of them in a rebuilt section: names, the materials to take (all when None)."""
    out = []
    for sb in sec.subs:
        if names is not None and sb.name not in names:
            continue
        pool = sec.lod0 if sb.lod == 0 else sec.lod
        if not pool.count:
            continue
        pos = fc.world(sec, pool)
        verts = fc.vertices(pool)
        restart = RESTART if sb.index_size == 4 else 0xFFFF
        tris = fc.strip_to_list(sb.indices, restart) if sb.index_type == 6 else \
            [tuple(sb.indices[i:i + 3]) for i in range(0, len(sb.indices) - 2, 3)]
        tris = [t for t in tris if max(t) < pool.count]
        f = sb.uvxf
        nrm = [fc.quat_normal(v[3]) for v in verts]
        uv0 = [(v[1][0] / 65535 * f[1] + f[0], v[1][1] / 65535 * f[3] + f[2]) for v in verts]
        uv1 = [(v[2][0] / 65535 * f[5] + f[4], v[2][1] / 65535 * f[7] + f[6]) for v in verts]
        out.append(Piece(sec.name, sb.name, sb.lod, pos, nrm, uv0, uv1, tris))
    return out


def build(donor, pieces, pool_name, sh, seed=1, keep=(), transparent=None):
    """-> (new Carbin, report). pool_name: "lod" (the main model, LOD 1-5), "lod0" (_lod0 and
    _cockpit) or "auto" (a wheel model: each piece in its LOD's pool). Every piece's section must
    be one of the donor's."""
    rng = random.Random(seed)
    sh_all = sh                     # (a by_section SHModel gives each section its own fit)
    out = copy.deepcopy(donor)
    names = {s.name for s in donor.sections}
    by_sec = {}
    for p in pieces:
        if p.section not in names:
            raise ValueError("no section %r in the donor" % p.section)
        by_sec.setdefault(p.section, []).append(p)
    report = {"filled": [], "from_stock": [], "kept": [], "emptied": []}
    for sec in donor.sections:  # upgrade variants with nothing of their own: the stock part
        stock = stock_section(sec.name, names)
        if sec.name not in by_sec and stock in by_sec:
            by_sec[sec.name] = [replace(p, section=sec.name) for p in by_sec[stock]]
            report["from_stock"].append(sec.name)
    tpl = next(sb for s in donor.sections for sb in s.subs)
    kept = set()
    for i, sec in enumerate(out.sections):
        lods = sorted({sb.lod for sb in sec.subs}) or [0 if pool_name == "lod0" else 1]
        mine = [p for p in by_sec.get(sec.name, []) if p.lod in lods]
        if sec.name in keep and not mine:
            out.sections[i] = copy.deepcopy(donor.sections[i])
            kept.add(sec.name)
            report["kept"].append(sec.name)
            continue
        if not mine:
            mine = [_empty_piece(sec, lod) for lod in lods]
            report["emptied"].append(sec.name)
        elif sec.name not in report["from_stock"]:
            report["filled"].append(sec.name)
        offset = tuple(sec.offset)
        ext = [0.0, 0.0, 0.0]
        for p in mine:
            for t in p.tris:
                for k in t:
                    d = _sub(p.pos[k], offset)
                    for a in range(3):
                        ext[a] = max(ext[a], abs(d[a]))
        bmax = [max(e, 1e-3) * 1.0005 for e in ext]
        sec.bmin, sec.bmax = [-b for b in bmax], bmax
        radius = math.sqrt(_dot(bmax, bmax))
        sh = sh_all.section(sec.name) if hasattr(sh_all, "section") else sh_all
        empty = fc.Pool(0, 0, [0], b"")
        if pool_name == "auto":  # a wheel: LOD 0 in its own pool, LOD 1-5 in the other
            hi = [p for p in mine if p.lod > 0]
            lo = [p for p in mine if p.lod == 0]
            pool1, subs1 = _encode_pool(hi, offset, radius, sh, tpl, rng) if hi else (empty, [])
            pool0, subs0 = _encode_pool(lo, offset, radius, sh, tpl, rng) if lo else (empty, [])
            sec.lod, sec.lod0, sec.subs = pool1, pool0, subs1 + subs0
        else:
            pool, subs = _encode_pool(mine, offset, radius, sh, tpl, rng)
            if pool_name == "lod":
                sec.lod, sec.lod0 = pool, (sec.lod0 if sec.lod0.count == 0 else empty)
            else:
                sec.lod, sec.lod0 = (sec.lod if sec.lod.count == 0 else empty), pool
            sec.subs = subs
        sec.perms = struct.pack(">I", 0)
        sec.cnt2 = struct.pack(">I", 0)
        sec.streams = [fc.Stream(3, 0, 0, [0], b""), fc.Stream(3, 0, 0, [0], b"")]  # no damage morphs
        sec.tail5 = sec.tail5[:4] + b"\x00"
        sec.fraw = None
    if transparent is None:
        transparent = transparent_materials([donor])
    regenerate_draw_lists(out, donor, transparent, kept)
    return out, report
