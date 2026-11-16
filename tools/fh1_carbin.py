#!/usr/bin/env python3
"""Lossless reader and writer for Forza Horizon (2012, Xbox 360) .carbin car models, at the level of
sections, vertex pools and meshes: parse() -> build() gives back the same bytes, and a section's
geometry can be replaced (fm1_cars.py builds FM1 cars into an FH1 car's models this way, #930).

From Forza Imported DLC Maker's fh1_carbin.py (warshack4874), without numpy; its layout follows
rustybusgaming's fh1_rewrite (crates/fh1-formats/src/carbin.rs). docs/modding/carbin-format.md
names the same fields. Every byte the layout does not name is kept as it is. Big-endian.

Section:  u32 marker (5|2) | f32 offset[3] | f32 bmin[3] | f32 bmax[3] | raw28
          | u32 perm_count, perm_count*16 | raw4 | u32 cnt2, cnt2*2 | raw12
          | u8 name_len, name | u32 version | Pool(lod) | raw4 | u32 sub_count, Sub*
          | raw4 | Pool(lod0) | tail
Pool:     u32 count | u32 stride | bone block (version >= 3: u32 n, [u32 id if n]) | count*stride
Sub:      raw5 | f32 uvxf[8] | raw36 | u32 name_len, name | u32 lod | u32 index_type (4 list, 6 strip)
          | raw56 | raw4|8 | u32 count | u32 size | indices | raw4
Tail:     raw5 | 2 x Stream(u32 version, u32 count, u32 stride, bone block, data)
Vertex (28 bytes): i16 pos[4] (a direction xyz and its length w, SNORM, in the section's bounds)
          | u16 uv0[2] | u16 uv1[2] | i16 tangent-frame quaternion[4] | s8[4] (SH0, the baked lighting)
"""
import copy
import math
import struct
from dataclasses import dataclass, field

BE_U32 = struct.Struct(">I")
FLT_MAX = 3.4028234663852886e38
RESTART16, RESTART32 = 0xFFFF, 0x00FFFFFF


class CarbinError(ValueError):
    pass


class _R:
    def __init__(self, d, p=0):
        self.d, self.p = d, p

    def raw(self, n):
        if self.p + n > len(self.d):
            raise CarbinError("truncated at %#x (+%d)" % (self.p, n))
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def u8(self):
        return self.raw(1)[0]

    def u32(self):
        return BE_U32.unpack(self.raw(4))[0]

    def i32(self):
        return struct.unpack(">i", self.raw(4))[0]

    def f32s(self, n):
        return list(struct.unpack(">%df" % n, self.raw(4 * n)))


@dataclass
class Pool:
    count: int
    stride: int
    bone: list          # [] or [n] or [n, id]
    data: bytes


@dataclass
class Stream:
    version: int
    count: int
    stride: int
    bone: list
    data: bytes


@dataclass
class Sub:
    raw5: bytes
    uvxf: list          # (u0 offset, u0 scale, v0 offset, v0 scale, the same for uv1)
    raw36: bytes
    name: str           # the material (a shader technique name)
    lod: int
    index_type: int     # 4 triangle list, 6 strip
    raw56: bytes
    rawver: bytes
    index_size: int     # 2 or 4
    indices: list       # as stored
    raw4: bytes


@dataclass
class Section:
    marker: int
    offset: list
    bmin: list
    bmax: list
    raw28: bytes
    perms: bytes        # u32 count + count * 16
    raw4a: bytes
    cnt2: bytes         # u32 count + count * 2
    raw12: bytes
    name: str
    version: int
    lod: Pool           # the LOD1-5 vertices
    raw4b: bytes
    subs: list
    raw4c: bytes
    lod0: Pool          # the LOD0 vertices
    tail5: bytes
    streams: list
    trailer: bytes = b""
    fraw: bytes = field(default=None, repr=False)


@dataclass
class Carbin:
    header: bytes       # everything before the section count
    sections: list
    footer: bytes
    type_id: int = 0


def _bone(r, version):
    if version < 3:
        return []
    n = r.u32()
    return [n, r.u32()] if n else [n]


def _pool(r, version):
    count = max(r.i32(), 0)
    stride = r.u32()
    bone = _bone(r, version)
    if count and stride not in (28, 32):
        raise CarbinError("odd vertex stride %d" % stride)
    return Pool(count, stride, bone, r.raw(count * stride))


def _sub(r, version):
    raw5 = r.raw(5)
    uvxf = r.f32s(8)
    raw36 = r.raw(36)
    ln = r.u32()
    if ln > 256:
        raise CarbinError("mesh name too long")
    name = r.raw(ln).decode("latin-1")
    lod = r.u32()
    itype = r.u32()
    if itype not in (4, 6):
        raise CarbinError("index type %d" % itype)
    raw56 = r.raw(56)
    rawver = r.raw(8 if version >= 3 else 4)
    count = r.u32()
    size = r.u32()
    if size not in (2, 4):
        raise CarbinError("index size %d" % size)
    idx = list(struct.unpack(">%d%s" % (count, "H" if size == 2 else "I"), r.raw(count * size)))
    return Sub(raw5, uvxf, raw36, name, lod, itype, raw56, rawver, size, idx, r.raw(4))


def _looks_like_section(d, p):
    if p + 40 > len(d) or BE_U32.unpack_from(d, p)[0] not in (5, 2):
        return False
    f = struct.unpack_from(">9f", d, p + 4)
    return all(math.isfinite(x) for x in f) or f[3] == FLT_MAX


def _section(r):
    marker = r.u32()
    if marker not in (5, 2):
        raise CarbinError("bad marker %d at %#x" % (marker, r.p - 4))
    fraw = r.raw(36)
    vals = list(struct.unpack(">9f", fraw))
    raw28 = r.raw(28)
    pc = r.u32()
    if pc > 1000000:
        raise CarbinError("perm count")
    perms = BE_U32.pack(pc) + r.raw(pc * 16)
    raw4a = r.raw(4)
    c2 = r.u32()
    if c2 > 1000000:
        raise CarbinError("cnt2")
    cnt2 = BE_U32.pack(c2) + r.raw(c2 * 2)
    raw12 = r.raw(12)
    nb = r.raw(r.u8())
    if not nb or not all(32 < c < 127 for c in nb):
        raise CarbinError("section name not printable")
    version = r.u32()
    lod = _pool(r, version)
    raw4b = r.raw(4)
    nsub = r.u32()
    if nsub > 1000:
        raise CarbinError("mesh count")
    subs = [_sub(r, version) for _ in range(nsub)]
    raw4c = r.raw(4)
    lod0 = _pool(r, version)
    tail5 = r.raw(5)
    streams = []
    for _ in range(2):
        v, c, s = r.u32(), r.u32(), r.u32()
        bone = _bone(r, v)
        streams.append(Stream(v, c, s, bone, r.raw(c * s)))
    return Section(marker, vals[0:3], vals[3:6], vals[6:9], raw28, perms, raw4a, cnt2, raw12,
                   nb.decode("latin-1"), version, lod, raw4b, subs, raw4c, lod0, tail5, streams, b"", fraw)


def _sections(data, p, n):
    r = _R(data, p)
    secs = []
    for k in range(n):
        s = _section(r)
        if k + 1 < n:  # the bytes up to the next section (0 to 16 seen)
            for skip in (0, 4, 8, 12, 16):
                if _looks_like_section(data, r.p + skip):
                    s.trailer = r.raw(skip)
                    break
            else:
                raise CarbinError("no section after %s" % s.name)
        secs.append(s)
    return secs, r.p


def parse(data):
    type_id = BE_U32.unpack_from(data, 0)[0]
    if type_id not in (1, 2, 3, 5):
        raise CarbinError("unsupported carbin type %d" % type_id)
    try:  # the header read field by field: the section count follows the draw lists and a u32
        i = draw_lists(data)[2] + 4
        n = BE_U32.unpack_from(data, i)[0]
        if 1 <= n <= 255 and _looks_like_section(data, i + 4):
            secs, end = _sections(data, i + 4, n)
            return Carbin(data[:i], secs, data[end:], type_id)
    except (CarbinError, struct.error):
        pass
    start = 0x4DC if type_id == 5 and any(data[4:0x18]) else 0x8
    for i in range(start, min(len(data) - 48, start + 0x2000), 4):
        n = BE_U32.unpack_from(data, i)[0]
        if not 1 <= n <= 255 or not _looks_like_section(data, i + 4):
            continue
        try:
            secs, end = _sections(data, i + 4, n)
        except CarbinError:
            continue
        return Carbin(data[:i], secs, data[end:], type_id)
    raise CarbinError("no section table found")


def _w_bone(bone, stripped=False):
    if stripped and bone and bone[0]:  # stripped files set bit 1 ("no payload") on the flag word
        bone = [bone[0] | 2] + list(bone[1:])
    return b"".join(BE_U32.pack(x) for x in bone)


def _w_pool(p, stripped=False):
    return struct.pack(">iI", p.count, p.stride) + _w_bone(p.bone, stripped) + (b"" if stripped else p.data)


def _w_sub(s, stripped=False):
    nm = s.name.encode("latin-1")
    fmt = ">%d%s" % (len(s.indices), "H" if s.index_size == 2 else "I")
    idx = b"" if stripped else struct.pack(fmt, *s.indices)
    rawver = s.rawver
    if stripped and len(rawver) == 8:  # the index buffer's flag word: 0 -> 2
        rawver = rawver[:4] + BE_U32.pack(BE_U32.unpack(rawver[4:])[0] | 2)
    return (s.raw5 + struct.pack(">8f", *s.uvxf) + s.raw36 + BE_U32.pack(len(nm)) + nm
            + struct.pack(">II", s.lod, s.index_type) + s.raw56 + rawver
            + struct.pack(">II", len(s.indices), s.index_size) + idx + s.raw4)


def _w_section(s, stripped=False):
    nm = s.name.encode("latin-1")
    floats = struct.pack(">9f", *(list(s.offset) + list(s.bmin) + list(s.bmax)))
    if s.fraw is not None and struct.unpack(">9f", s.fraw) == tuple(list(s.offset) + list(s.bmin) + list(s.bmax)):
        floats = s.fraw  # unchanged: the exact bits (NaN, -0, sentinels)
    out = [BE_U32.pack(s.marker), floats, s.raw28, s.perms, s.raw4a, s.cnt2, s.raw12, bytes([len(nm)]), nm,
           BE_U32.pack(s.version), _w_pool(s.lod, stripped), s.raw4b, BE_U32.pack(len(s.subs))]
    out += [_w_sub(x, stripped) for x in s.subs]
    out += [s.raw4c, _w_pool(s.lod0, stripped), s.tail5]
    for st in s.streams:
        out.append(struct.pack(">III", st.version, st.count, st.stride) + _w_bone(st.bone, stripped)
                   + (b"" if stripped else st.data))
    out.append(s.trailer)
    return b"".join(out)


def build(c, stripped=False):
    """The file's bytes; stripped=True: the stripped_<name>.carbin companion (no payloads)."""
    return c.header + BE_U32.pack(len(c.sections)) + b"".join(_w_section(s, stripped) for s in c.sections) + c.footer


def clone(c):
    return copy.deepcopy(c)


DRIVER_CONTROLS = {1: 0x60, 2: 0xD0, 3: 0x100}  # bytes by version


def draw_lists(header):
    """A model header's per-LOD draw lists: (offset of the LOD count, arrays, end). The header
    (as tools/carbin_dump.py reads it): the root kind; a car model's 0x150 bytes and light list
    (u32 count, 32-byte records); CBaseCarModelData (exhaust emitters, driver controls, gauges,
    bone names); 8 bytes; a u32 LOD count and two arrays per LOD (u32 count, then u32 entries
    section << 16 | mesh): all the LODs' transparent arrays, then all their opaque ones."""
    kind = BE_U32.unpack_from(header, 0)[0]
    r = _R(header, 4)
    if kind == 5:
        r.p = 0x154
        r.raw(32 * r.u32())
    elif kind == 3:  # a wheel model: 0x44 bytes of wheel data
        r.raw(0x44)
    elif kind != 1:  # 1: a part model (calipers, rotors)
        raise CarbinError("root kind %d" % kind)
    v = r.u32()
    if v >= 9:
        r.u32()  # shader version
    r.u32()  # emitters version
    for _ in range(r.u32()):
        r.raw(140)
    dv = r.u32()
    if dv not in DRIVER_CONTROLS:
        raise CarbinError("driver controls version %d" % dv)
    r.raw(DRIVER_CONTROLS[dv])
    if v >= 9:
        r.raw(8)  # first and last LOD
    r.raw((15 if v >= 16 else 4) * 36)  # gauges
    if v >= 8:
        r.raw(8)
    if v > 13:
        r.u32()
    if v > 14:  # bone names
        r.u32()
        for _ in range(r.u32()):
            r.raw(r.u32())
    r.raw(8)
    d0, arrays = r.p, []
    for _ in range(r.u32() * 2):
        k = r.u32()
        arrays.append(list(struct.unpack(">%dI" % k, r.raw(4 * k))))
    return d0, arrays, r.p


def set_draw_lists(header, arrays):
    d0, _, end = draw_lists(header)
    blob = BE_U32.pack(len(arrays) // 2) + b"".join(struct.pack(">I%dI" % len(a), len(a), *a) for a in arrays)
    return header[:d0] + blob + header[end:]


def world(section, pool):
    """[(x, y, z)] model-space positions of a pool's vertices."""
    r = math.sqrt(sum(b * b for b in section.bmax))
    o = section.offset
    out = []
    for i in range(pool.count):
        x, y, z, w = struct.unpack_from(">4h", pool.data, i * pool.stride)
        s = w / 32767.0 * r / 32767.0
        out.append((x * s + o[0], y * s + o[1], z * s + o[2]))
    return out


def strip_to_list(idx, restart):
    """Triangles of a strip with restarts, in the strip's winding."""
    tris, run = [], []
    for v in idx:
        if v == restart or v >= 0xFFFFFF:
            run = []
            continue
        run.append(v)
        if len(run) >= 3:
            a, b, c = run[-3:]
            if a != b and b != c and a != c:
                tris.append((a, b, c) if len(run) % 2 else (b, a, c))
    return tris


def quat_normal(q):
    """The normal (the first row of the rotation) of a tangent-frame quaternion (x, y, z, w)."""
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y))


def vertices(pool):
    """[(pos4 i16, uv0 u16[2], uv1 u16[2], quat i16[4], sh s8[4])] of a pool."""
    out = []
    for i in range(pool.count):
        o = i * pool.stride
        v = struct.unpack_from(">4h2H2H4h4b", pool.data, o)
        out.append((v[0:4], v[4:6], v[6:8], v[8:12], v[12:16]))
    return out
