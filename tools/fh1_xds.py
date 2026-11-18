#!/usr/bin/env python3
"""DXT5 textures as Forza Horizon (2012) stores a car's (.xds): Xbox 360 layout, with mip levels.

Measured on FH1's car textures (MAZ_RX7_97's nodamage, damage, lights; 512 to 2048 texels):
  header   13 x u32 (big-endian): the D3D resource (3, 1, 0, 0, 0, 0xFFFF0000, 0xFFFF0000), then the
           texture fetch constant: 0xFE | pitch/32 << 22 | tiled << 31; format 0x35 (DXT4_5 as
           16_16_16_16) | endian 1 << 6 (8-in-16); width - 1 | height - 1 << 13; 0xD10; the last
           mip level << 6; 0xA00 (2-D, packed mips) | the mips' address in 4 KiB pages << 12
  blocks   16-byte DXT5 blocks with every 16-bit word byte-swapped, tiled (XGAddress2DTiledOffset,
           a block as a 16-byte texel)
  levels   the largest level; then each smaller one as its own surface at least 32 x 32 blocks,
           until the shorter side is 16 texels or less: those levels share one 32 x 32-block
           surface (Xenia's GetPackedMipOffset; the offsets matched on FH1's lights.xds). The
           file's size is the header plus these surfaces.
Standard library only.
"""
import struct

FORMAT_DXT5 = 0x35
RESOURCE = (3, 1, 0, 0, 0, 0xFFFF0000, 0xFFFF0000)


def tiled_offset(x, y, width, log_bpp):
    """Byte offset of texel (x, y) in a tiled surface `width` texels wide (2**log_bpp bytes each)."""
    aligned = (width + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << (log_bpp + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << log_bpp
    offset = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + log_bpp)) + ((y & 1) << 4)
    return (((offset & ~511) << 3) + ((offset & 448) << 2) + (offset & 63) + ((y & 16) << 7)
            + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6))


def packed_mip_offset(width, height, mip):
    """(x, y) texels of a mip level within the shared surface of the small levels, or None."""
    lw, lh = (width - 1).bit_length(), (height - 1).bit_length()
    ls = min(lw, lh)
    if ls > 4 + mip:
        return None
    base = ls - 4 if ls > 4 else 0
    p = mip - base
    if p < 3:
        return (0, 16 >> p) if lw > lh else (16 >> p, 0)
    if lw > lh:
        return ((1 << (lw - base)) >> (p - 2), 0)
    return (0, (1 << (lh - base)) >> (p - 2))


def _swap16(b):
    out = bytearray(len(b))
    out[0::2], out[1::2] = b[1::2], b[0::2]
    return bytes(out)


def _align32(n):
    return (n + 31) & ~31


def level_blocks(width, height, mip):
    return max(1, ((width >> mip) + 3) // 4), max(1, ((height >> mip) + 3) // 4)


def _layout(width, height, levels):
    """[(surface offset, pitch in blocks, x, y blocks)] per level, the mips' offset, the total size."""
    bw, bh = level_blocks(width, height, 0)
    base = _align32(bw) * _align32(bh) * 16
    out = [(0, _align32(bw), 0, 0)]
    at, packed = base, None
    for i in range(1, levels):
        po = packed_mip_offset(width, height, i)
        if po is None:
            w, h = level_blocks(width, height, i)
            out.append((at, _align32(w), 0, 0))
            at += _align32(w) * _align32(h) * 16
        else:
            if packed is None:
                packed, at = at, at + 32 * 32 * 16
            out.append((packed, 32, po[0] // 4, po[1] // 4))
    return out, base, at


def write_dxt5(levels, width, height):
    """The .xds of a DXT5 texture: levels[i] is mip i's blocks, PC order (little-endian), row by row."""
    if width & (width - 1) or height & (height - 1) or min(width, height) < 64:
        raise ValueError("%dx%d: sides must be powers of two, 64 or more" % (width, height))
    if not 2 <= len(levels) <= max(width, height).bit_length():
        raise ValueError("%d mip levels" % len(levels))
    layout, base, size = _layout(width, height, len(levels))
    data = bytearray(size)
    for i, (blocks, (at, pitch, ox, oy)) in enumerate(zip(levels, layout)):
        w, h = level_blocks(width, height, i)
        if len(blocks) != w * h * 16:
            raise ValueError("mip %d: %d bytes, not %d" % (i, len(blocks), w * h * 16))
        sw = _swap16(blocks)
        for by in range(h):
            for bx in range(w):
                o = at + tiled_offset(bx + ox, by + oy, pitch, 4)
                s = (by * w + bx) * 16
                data[o:o + 16] = sw[s:s + 16]
    pitch = _align32(level_blocks(width, height, 0)[0]) * 4
    fetch = (0xFE | (pitch // 32) << 22 | 1 << 31, FORMAT_DXT5 | 1 << 6, (width - 1) | (height - 1) << 13,
             0xD10, (len(levels) - 1) << 6, 0xA00 | (base // 4096) << 12)
    return struct.pack(">13I", *(RESOURCE + fetch)) + bytes(data)


def read_dxt5(xds):
    """(width, height, [mip blocks, PC order]) of a DXT5 .xds."""
    f0, f1, f2, f3, f4, f5 = struct.unpack_from(">6I", xds, 28)
    if f1 & 0x3F not in (0x14, FORMAT_DXT5) or not f0 >> 31:
        raise ValueError("not a tiled DXT5 texture")
    width, height = (f2 & 0x1FFF) + 1, ((f2 >> 13) & 0x1FFF) + 1
    n = ((f4 >> 6) & 0xF) + 1
    layout, _, size = _layout(width, height, n)
    body = xds[52:]
    if len(body) < size:
        raise ValueError("truncated texture")
    levels = []
    for i, (at, pitch, ox, oy) in enumerate(layout):
        w, h = level_blocks(width, height, i)
        out = bytearray()
        for by in range(h):
            for bx in range(w):
                o = at + tiled_offset(bx + ox, by + oy, pitch, 4)
                out += body[o:o + 16]
        levels.append(_swap16(bytes(out)))
    return width, height, levels


# ----------------------------------------------------------------------------- DXT5 blocks

def _rgb565(c):
    return (c >> 11) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31


def decode_block(b):
    """16 (r, g, b, a) texels, row by row, of a DXT5 block (PC order)."""
    a0, a1 = b[0], b[1]
    if a0 > a1:
        alphas = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
    else:
        alphas = [a0, a1] + [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)] + [0, 255]
    abits = int.from_bytes(b[2:8], "little")
    c0, c1 = struct.unpack_from("<HH", b, 8)
    p0, p1 = _rgb565(c0), _rgb565(c1)
    cols = [p0, p1, tuple((2 * x + y) // 3 for x, y in zip(p0, p1)), tuple((x + 2 * y) // 3 for x, y in zip(p0, p1))]
    cbits = struct.unpack_from("<I", b, 12)[0]
    return [cols[(cbits >> (2 * i)) & 3] + (alphas[(abits >> (3 * i)) & 7],) for i in range(16)]


def _to565(c):
    r, g, b = (max(0, min(255, int(round(v)))) for v in c)
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | (b * 31 + 127) // 255


def encode_block(px):
    """A DXT5 block (PC order) for 16 (r, g, b, a) texels: colour endpoints the two farthest apart
    of the texels darkest and brightest in some channel, alpha from its range."""
    alist = [p[3] for p in px]
    amax, amin = max(alist), min(alist)
    if amax == amin:
        ab = bytes([amax, amin]) + bytes(6)  # a0 == a1: every index 0 reads a0
    else:
        idx = 0
        for i, a in enumerate(alist):  # 8-alpha mode: index 0 = amax, 1 = amin, 2..7 between
            t = round((amax - a) * 7 / (amax - amin))
            k = 0 if t == 0 else 1 if t == 7 else t + 1
            idx |= k << (3 * i)
        ab = bytes([amax, amin]) + idx.to_bytes(6, "little")
    cand = {min(px, key=lambda p: p[k])[:3] for k in range(3)} | {max(px, key=lambda p: p[k])[:3] for k in range(3)}
    cand = sorted(cand)
    lo, hi = max(((a, b) for i, a in enumerate(cand) for b in cand[i:]),
                 key=lambda ab: sum((x - y) ** 2 for x, y in zip(*ab)))
    c0, c1 = _to565(hi), _to565(lo)
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:
        return ab + struct.pack("<HHI", c0, c1, 0)
    pal = [_rgb565(c0), _rgb565(c1)]
    pal += [tuple((2 * x + y) / 3 for x, y in zip(pal[0], pal[1])), tuple((x + 2 * y) / 3 for x, y in zip(pal[0], pal[1]))]
    bits = 0
    for i, p in enumerate(px):
        d = [sum((p[k] - q[k]) ** 2 for k in range(3)) for q in pal]
        bits |= d.index(min(d)) << (2 * i)
    return ab + struct.pack("<HHI", c0, c1, bits)


def decode_level(blocks, w, h):
    """RGBA texels (a flat list of tuples, row by row) of a level w x h blocks."""
    out = [None] * (w * 4 * h * 4)
    for by in range(h):
        for bx in range(w):
            px = decode_block(blocks[(by * w + bx) * 16:(by * w + bx + 1) * 16])
            for i, p in enumerate(px):
                out[(by * 4 + i // 4) * w * 4 + bx * 4 + i % 4] = p
    return out


def mips_from(blocks, width, height):
    """[level 0 blocks, then each smaller level encoded from a 2 x 2 average of the one above]."""
    levels = [blocks]
    px, w, h = decode_level(blocks, width // 4, height // 4), width, height
    while max(w, h) > 1:
        nw, nh = max(1, w // 2), max(1, h // 2)
        nxt = []
        for y in range(nh):
            for x in range(nw):
                q = [px[min(2 * y + dy, h - 1) * w + min(2 * x + dx, w - 1)] for dy in (0, 1) for dx in (0, 1)]
                nxt.append(tuple((sum(c[k] for c in q) + 2) // 4 for k in range(4)))
        px, w, h = nxt, nw, nh
        bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
        out = bytearray()
        for by in range(bh):
            for bx in range(bw):
                out += encode_block([px[min(by * 4 + i // 4, h - 1) * w + min(bx * 4 + i % 4, w - 1)] for i in range(16)])
        levels.append(bytes(out))
    return levels
