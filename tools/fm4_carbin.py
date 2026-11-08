#!/usr/bin/env python3
"""Convert Forza Motorsport 4 .carbin car models to the Forza Horizon (2012) layout.

The two games use the same serializer; FH1's files are a few versions newer
(docs/modding/carbin-format.md has FH1's layout, docs/modding/fm4-cars.md the
differences). What changes, field by field:

| Group                | FM4                         | FH1                                        |
|----------------------|-----------------------------|--------------------------------------------|
| root (car models)    | 2: CCarModelData, no lights | 5: the same 0x150 bytes, then a light list |
| CBaseCarModelData    | version 16                  | version 17 (same fields)                   |
| VertexBuffer         | version 2, no flags         | version 3: flags (bit 0 key, bit 1 no data)|
| vertex               | 32 bytes, SH0 4 x s16       | 28 bytes, SH0 4 x s8                       |
| IndexBuffer          | version 3, no flags         | version 4: flags                           |
| morph (damage) entry | a unit direction and a size | the offset itself (FMT_10_11_11, #747)     |

Positions, UVs and the tangent frame are byte-identical between the games for a
car both ship (the Alfa Romeo 8C); FH1's SH0 is FM4's rounded to 8 bits. Part
models (root 1) and wheels (root 3) only need the buffer versions.

The damage morphs (4-byte entries, one per vertex) are packed the same way in both games
but mean different things. FH1's entry is the vertex's dent offset (x, y, z). FM4's holds a
unit direction in x and z, with its y left out (sqrt(1 - x^2 - z^2), never negative), and
the dent's signed size in the y field. Read as offsets, FM4's directions moved a vertex
by up to 2 m as soon as that spot of the car took any damage (with Cosmetic Damage on):
the long spikes of #747. They are converted to FH1's offsets (size x direction): for the
cars both games ship, 95 % of the converted entries are within 2 steps of FH1's own (FH1
re-baked the rest). Wheel models' 16-byte morphs are the same in both games.

`convert(data, key_seed)` returns the FH1 file; `strip(data)` returns the
`stripped_` companion of an FH1 file (every buffer without its data, as the
game's own `stripped_*.carbin` files are). Standard library only.
"""
import struct
import zlib

TAIL = bytes.fromhex("000000010000000000000001000000000000000100000000")


class CarbinError(ValueError):
    pass


class _R:
    def __init__(self, data):
        self.d, self.o = data, 0

    def u32(self):
        if self.o + 4 > len(self.d):
            raise CarbinError("truncated at 0x%x" % self.o)
        v = struct.unpack_from(">I", self.d, self.o)[0]
        self.o += 4
        return v

    def take(self, n):
        if n < 0 or self.o + n > len(self.d):
            raise CarbinError("truncated at 0x%x (+%d)" % (self.o, n))
        b = self.d[self.o:self.o + n]
        self.o += n
        return b


class _Converter:
    """Walks an FM4 (or FH1) file and writes the FH1 version of it."""

    def __init__(self, data, key_seed, strip=False, lights=b"\0\0\0\0"):
        self.r = _R(data)
        self.out = bytearray()
        self.seed = key_seed
        self.strip = strip
        self.lights = lights
        self.nkey = 0
        self.vertex_buffers = 0

    def copy(self, n):
        self.out += self.r.take(n)

    def u32(self, value=None):
        v = self.r.u32()
        self.out += struct.pack(">I", v if value is None else value)
        return v

    def key(self):
        # Buffers with the same key share one cached copy in the game, so a key must not be
        # used by any other buffer of the game. FH1's own keys are not a checksum of the data
        # (docs/modding/carbin-format.md); any unused 32-bit value works.
        self.nkey += 1
        k = zlib.crc32(b"%s/%d" % (self.seed, self.nkey)) & 0xFFFFFFFF
        return k or 1

    # ------------------------------------------------------------------ groups

    def vertex_buffer(self, vertex=False, morph=False):
        version = self.r.u32()
        if version not in (2, 3):
            raise CarbinError("vertex buffer version %d at 0x%x" % (version, self.r.o - 4))
        count, stride = self.r.u32(), self.r.u32()
        flags, key = 0, None
        if version == 3:
            flags = self.r.u32()
            if flags & 1:
                key = self.r.u32()
        has_data = not flags & 2
        data = self.r.take(count * stride) if has_data else b""
        out_stride = stride
        if vertex and stride == 32:
            out_stride = 28
            if has_data:
                data = repack_vertices(data, count)
        elif vertex and stride not in (0, 28):
            raise CarbinError("vertex stride %d at 0x%x" % (stride, self.r.o))
        elif morph and version == 2 and stride == 4 and has_data:
            data = fm4_morphs(data, count)  # FM4's (version 2): directions and sizes
        if count and key is None:
            key = self.key()
        out_flags = 1 if count else 0
        if self.strip and count:
            out_flags |= 2
        self.out += struct.pack(">IIII", 3, count, out_stride, out_flags)
        if out_flags & 1:
            self.out += struct.pack(">I", key)
        if not out_flags & 2:
            self.out += data

    def index_buffer(self):
        version = self.r.u32()
        if version not in (3, 4):
            raise CarbinError("index buffer version %d at 0x%x" % (version, self.r.o - 4))
        flags = 0
        if version == 4:
            flags = self.r.u32()
            if flags & 1:
                self.r.u32()
        count, fmt = self.r.u32(), self.r.u32()
        size = 4 if fmt == 4 else 2
        data = self.r.take(count * size) if not flags & 2 else b""
        # FH1's full files have no index keys; the stripped files flag the data as absent.
        self.out += struct.pack(">IIII", 4, 2 if self.strip else 0, count, fmt)
        if not self.strip:
            self.out += data

    def mesh(self):
        v = self.u32()  # CCarMaterialData
        if v > 1:
            self.copy(1)
        if v > 2:
            self.copy(64)
        mv = self.u32()  # CMesh
        n = self.u32()
        if n > 4096:
            raise CarbinError("bad material name length at 0x%x" % (self.r.o - 4))
        self.copy(n)
        self.copy(12)  # LOD, primitive, MaterialIndex
        self.u32()  # UnpackingData version
        self.copy(48)
        self.index_buffer()
        if mv > 1:
            self.u32()

    def section(self):
        v = self.u32()
        if v != 5:
            raise CarbinError("section version %d at 0x%x" % (v, self.r.o - 4))
        self.copy(4 * 14)  # center, bounds, f32, vec3, u32... (see carbin-format.md)
        self.u32()  # GlassParticles version
        self.u32()
        n = self.u32()
        self.copy(16 * n)
        self.u32()
        n = self.u32()
        self.copy(2 * n)
        self.copy(1)
        self.u32()
        self.u32()  # model part version
        n = self.u32()
        self.copy(n)  # name
        self.vertex_buffer(vertex=True)
        self.u32()  # mesh array version
        for _ in range(self.u32()):
            self.mesh()
        self.vertex_buffer(vertex=True)  # LOD0
        self.u32()  # MorphTargets
        self.copy(1)
        self.vertex_buffer(morph=True)
        self.vertex_buffer(morph=True)  # LOD0

    def base(self):
        v = self.r.u32()
        if v not in (16, 17):
            raise CarbinError("CBaseCarModelData version %d" % v)
        self.out += struct.pack(">I", 17)
        self.u32()  # ShaderVersion
        self.u32()  # Emitters version
        n = self.u32()
        for _ in range(n):
            if self.u32() != 2:
                raise CarbinError("emitter version at 0x%x" % (self.r.o - 4))
            self.copy(136)
        dv = self.u32()
        self.copy({1: 0x60, 2: 0xd0, 3: 0x100}[dv])
        self.copy(8)  # StartLOD, EndLOD
        self.copy(15 * 36 + 8)
        self.u32()
        self.u32()  # BoneMappingTable
        for _ in range(self.u32()):
            n = self.u32()
            self.copy(n)

    def run(self):
        root = self.r.u32()
        if root == 2:  # FM4 car model: the same CCarModelData without the light list
            self.out += struct.pack(">I", 5)
            self.copy(0x150)
            self.out += self.lights
        elif root == 5:
            self.out += struct.pack(">I", 5)
            self.copy(0x150)
            n = self.u32()
            self.copy(32 * n)
        elif root == 3:  # wheel model: 0x44 bytes of wheel data
            self.out += struct.pack(">I", 3)
            self.copy(0x44)
        elif root == 1:  # part model (calipers, rotors)
            self.out += struct.pack(">I", 1)
        else:
            raise CarbinError("unknown root version %d" % root)
        self.base()
        self.copy(8)  # draw list header
        n = self.u32()
        for _ in range(2 * n):
            c = self.u32()
            self.copy(4 * c)
        self.u32()  # SubModel array version
        for _ in range(self.u32()):
            self.section()
        rest = self.r.d[self.r.o:]
        if rest != TAIL:
            raise CarbinError("unexpected data after the sections (0x%x bytes)" % len(rest))
        self.out += rest
        return bytes(self.out)


def repack_vertices(data, count):
    """32-byte FM4 vertices to FH1's 28 bytes: SH0 4 x s16 -> 4 x s8 (snorm, rounded)."""
    out = bytearray(28 * count)
    for i in range(count):
        src = 32 * i
        dst = 28 * i
        out[dst:dst + 24] = data[src:src + 24]
        for k, v in enumerate(struct.unpack_from(">4h", data, src + 24)):
            q = int(round(max(-1.0, v / 32767.0) * 127.0))
            out[dst + 24 + k] = q & 0xFF
    return bytes(out)


def _morph_fields(v):
    """The signed x (bits 0-10), y (11-21) and z (22-31) fields of a morph entry."""
    x, y, z = v & 0x7FF, (v >> 11) & 0x7FF, (v >> 22) & 0x3FF
    return x - (x >> 10 << 11), y - (y >> 10 << 11), z - (z >> 9 << 10)


def fm4_morph_offset(v):
    """An FM4 morph entry as the offset FH1's entries hold (x, y, z, each -1..1): FM4's x and
    z fields are a unit direction (y = sqrt(1 - x^2 - z^2), never negative), its y field the
    signed size of the dent."""
    x, y, z = _morph_fields(v)
    dx, size, dz = max(-1.0, x / 1023.0), max(-1.0, y / 1023.0), max(-1.0, z / 511.0)
    flat = dx * dx + dz * dz
    if flat > 1.0:
        dx, dz, dy = dx / flat ** 0.5, dz / flat ** 0.5, 0.0
    else:
        dy = (1.0 - flat) ** 0.5
    return size * dx, size * dy, size * dz


def morph_entry(x, y, z):
    """FH1's morph entry (FMT_10_11_11, signed normalized) of the offset x, y, z (-1..1)."""
    def q(v, scale, mask):
        return int(round(max(-1.0, min(1.0, v)) * scale)) & mask
    return q(x, 1023, 0x7FF) | q(y, 1023, 0x7FF) << 11 | q(z, 511, 0x3FF) << 22


def fm4_morphs(data, count):
    """FM4's 4-byte morph entries as FH1's (see the module notes, #747)."""
    vals = struct.unpack(">%dI" % count, data)
    return struct.pack(">%dI" % count, *[morph_entry(*fm4_morph_offset(v)) for v in vals])


def convert(data, key_seed, lights=b"\0\0\0\0"):
    """An FM4 .carbin as FH1 reads it. key_seed (bytes) makes the buffer keys of this file
    unique: use the car's and the file's names. lights: FH1's light list (u32 count +
    32-byte records); FM4 has none, an empty list is valid (FH1's cockpit models have none)."""
    if isinstance(key_seed, str):
        key_seed = key_seed.encode()
    return _Converter(data, key_seed, lights=lights).run()


def set_lights(data, lights):
    """An FH1 car model (root 5) with its light list replaced by lights (u32 count + 32-byte
    records)."""
    if struct.unpack_from(">I", data, 0)[0] != 5:
        raise CarbinError("not a car model")
    n = struct.unpack_from(">I", data, 0x154)[0]
    return data[:0x154] + lights + data[0x158 + 32 * n:]


def strip(data):
    """The stripped_ companion of an FH1 .carbin: the same file, every buffer flagged as not in
    the file (vertex and morph buffers keep their keys, index buffers have none)."""
    return _Converter(data, b"", strip=True).run()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        sys.exit("usage: fm4_carbin.py FM4.carbin OUT.carbin")
    with open(sys.argv[1], "rb") as f:
        src = f.read()
    with open(sys.argv[2], "wb") as f:
        f.write(convert(src, sys.argv[2]))
