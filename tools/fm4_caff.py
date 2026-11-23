"""FM4's CAFF textures as the .bix pairs FH1 streams (tools/fm4_pvs.py, caff_textures).

A texture of an FM4 circuit whose own file is a CAFF file (_0x<id>.bin) is streamed from it by
FM4; the static pack holds only a small copy. FH1 streams a texture from _0x<id>.bix (a 28-byte
header, then the mip chain) and _0x<id>_B.bix (the base level), the layout of FM4's other
streamed textures. Both hold Xenos tiled GPU data as the CAFF's .gpu block does.

FH1 draws a streamed texture from its mip chain alone until (and whenever) the base is not in
memory: a texture of half the size with one level fewer (TU4 Bix::InitD3DTextureFromParts).
So a texture needs at least two levels: with one, the mips-only texture is asked for with 0
levels, which the SDK reads as every level, and the GPU reads memory that holds no mips (the
Alps' road drew as scrambled blocks when its texture was cut to one level). Many of FM4's
CAFF textures have one level, so their mips are made here: decoded (DXT1, DXT5), sized to
powers of two, halved down to 128 texels, encoded again and tiled. Levels of 128 texels and
more each way are whole 32 x 32 block tiles, one after another, which is how FM4's own .bix
files hold them; the smaller levels share a packed tail whose layout is not modelled here, so
the chain stops at 128.

Plain Python: an import makes a few of these per circuit (the Alps: about 8 s for a
2048 x 1024 texture)."""
import struct

# Bytes per 4 x 4 block of the formats handled (the format's low 6 bits).
BLOCK_BYTES = {0x12: 8, 0x13: 16, 0x14: 16, 0x31: 16, 0x3B: 8}
DXT1, DXT5 = 0x12, 0x14
ENDIAN_8IN16 = 1
SMALLEST_MIP = 128
# The largest base level written: a texture over it is halved first (an 8192 x 2048 sky dome:
# 8 MB as DXT1, 16 MB as DXT5, to 4096 x 1024). The game's streamed texture data on a converted
# track comes from 8 MB chunks, 64 MB in all (forza-recomp/src/hooks/fm4_stream_textures.h): a
# file larger than a chunk cannot be placed there.
MAX_BASE_BYTES = 4 << 20


def _al(v, a):
    return (v + a - 1) // a * a


def caff_texture(data):
    """A CAFF file's one 2D texture as (format, width, height, levels, its .gpu data), or None:
    not a CAFF texture, more than one asset, a cube map or volume, the mips not contiguous
    after the base level, or a format not in BLOCK_BYTES."""
    u32 = lambda p: struct.unpack_from(">I", data, p)[0]
    try:
        assets, sections = u32(24), u32(28)
        header_size = u32(72)
        blocks, at = {}, header_size
        for k in range(data[77]):
            p = 80 + 40 * k
            name = data[p:p + 11].split(b"\0")[0]
            blocks[name] = (at, u32(p + 16))
            at += u32(p + 16)
        if assets != 1 or sections < 1 or set(blocks) != {b".data", b".gpu"}:
            return None
        a = blocks[b".data"][0]
        if data[a:a + 8] != b"texture\0":
            return None
        fmt, kind = u32(a + 24), u32(a + 28)
        w, h = struct.unpack_from(">HH", data, a + 36)
        base, mips, levels = u32(a + 40), u32(a + 44), data[a + 48]
        gpu_at, gpu_size = blocks[b".gpu"]
    except (struct.error, IndexError):
        return None
    if kind != 0 or base != 0 or mips != 0xFFFFFFFF or fmt & 0x3F not in BLOCK_BYTES or not w or not h:
        return None
    gpu = data[gpu_at:gpu_at + gpu_size]
    return (fmt, w, h, levels, gpu) if len(gpu) == gpu_size else None


def base_size(fmt, w, h):
    """A tiled base level's bytes: whole 32 x 32 block tiles, 4 KB aligned."""
    return _al(_al((w + 3) // 4, 32) * _al((h + 3) // 4, 32) * BLOCK_BYTES[fmt & 0x3F], 4096)


def tiled_offset(x, y, width_blocks, bpb):
    """Byte offset of block (x, y) in a tiled 2D surface width_blocks wide (the Xenos layout,
    the XDK's XGAddress2DTiledOffset)."""
    aligned = (width_blocks + 31) & ~31
    log_bpp = (bpb >> 2) + ((bpb >> 1) >> (bpb >> 2))
    macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << (log_bpp + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << log_bpp
    off = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + log_bpp)) + ((y & 1) << 4)
    return ((((off & ~511) << 3) + ((off & 448) << 2) + (off & 63) + ((y & 16) << 7)
             + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> log_bpp) * bpb


def _swap16(b):
    b = bytearray(b)
    b[0::2], b[1::2] = b[1::2], b[0::2]
    return b


# ---- DXT blocks (little endian, as a PC reads them) ----

def _rgb565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _colour_palette(c0, c1, four):
    a, b = _rgb565(c0), _rgb565(c1)
    if four:
        return [a + (255,), b + (255,), tuple((2 * x + y) // 3 for x, y in zip(a, b)) + (255,),
                tuple((x + 2 * y) // 3 for x, y in zip(a, b)) + (255,)]
    return [a + (255,), b + (255,), tuple((x + y) // 2 for x, y in zip(a, b)) + (255,), (0, 0, 0, 0)]


def decode_colour(blk, dxt1):
    """16 (r, g, b, a) of a colour block; in DXT1's 3-colour mode index 3 is transparent."""
    c0, c1, idx = struct.unpack_from("<HHI", blk)
    pal = _colour_palette(c0, c1, c0 > c1 or not dxt1)
    return [pal[(idx >> (2 * i)) & 3] for i in range(16)]


def _alpha_palette(a0, a1):
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


def decode_alpha(blk):
    bits = int.from_bytes(blk[2:8], "little")
    pal = _alpha_palette(blk[0], blk[1])
    return [pal[(bits >> (3 * i)) & 7] for i in range(16)]


def _to565(r, g, b):
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def encode_colour(px, dxt1):
    """A colour block for 16 (r, g, b, a): endpoints at the ends of the colours' range along
    the channel with the widest range (each channel's direction by its sign against that one);
    DXT1 takes its 3-colour mode with index 3 transparent when a texel's alpha is below 128."""
    cut = dxt1 and any(p[3] < 128 for p in px)
    solid = [p for p in px if p[3] >= 128] if cut else px
    if not solid:
        return struct.pack("<HHI", 0, 0, 0xFFFFFFFF)
    lo = [min(p[k] for p in solid) for k in range(3)]
    hi = [max(p[k] for p in solid) for k in range(3)]
    axis = max(range(3), key=lambda k: hi[k] - lo[k])
    mean = [sum(p[k] for p in solid) / len(solid) for k in range(3)]
    e0, e1 = list(hi), list(lo)
    for k in range(3):
        if k != axis and sum((p[k] - mean[k]) * (p[axis] - mean[axis]) for p in solid) < 0:
            e0[k], e1[k] = lo[k], hi[k]
    c0, c1 = _to565(*e0), _to565(*e1)
    if cut:
        if c0 > c1:
            c0, c1 = c1, c0
    elif c0 < c1:
        c0, c1 = c1, c0
    elif c0 == c1:
        return struct.pack("<HHI", c0, c1, 0)
    a, b = _rgb565(c0), _rgb565(c1)
    d = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    dd = d[0] * d[0] + d[1] * d[1] + d[2] * d[2] or 1
    # each texel's place along a -> b, rounded to the palette's steps: 4-colour order a, 2/3 a,
    # 1/3 a, b is index 0, 2, 3, 1; 3-colour a, middle, b is 0, 2, 1
    order, steps = ((0, 2, 1), 2) if cut else ((0, 2, 3, 1), 3)
    idx = 0
    for i, p in enumerate(px):
        if cut and p[3] < 128:
            k = 3
        else:
            t = ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1] + (p[2] - a[2]) * d[2]) * steps / dd
            k = order[min(steps, max(0, int(t + 0.5)))]
        idx |= k << (2 * i)
    return struct.pack("<HHI", c0, c1, idx)


def encode_alpha(al):
    a0, a1 = max(al), min(al)
    if a0 == a1:
        return bytes([a0, a1]) + bytes(6)
    # 8-alpha mode, a0 > a1: index 0 is a0, 1 is a1, 2..7 step from a0 towards a1
    order = (0, 2, 3, 4, 5, 6, 7, 1)
    span = a0 - a1
    bits = 0
    for i, a in enumerate(al):
        bits |= order[min(7, max(0, int((a0 - a) * 7 / span + 0.5)))] << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


# ---- whole levels: four channel planes (bytearrays, row-major) ----

def block_offsets(wb, hb, bpb):
    """tiled_offset of every block, row by row. The offset within a 32 x 32 block tile repeats
    from tile to tile, and the tiles follow each other in bytes in a fixed step (checked
    against tiled_offset in the tests)."""
    inner = [tiled_offset(x, y, 32, bpb) for y in range(32) for x in range(32)]
    tile = 32 * 32 * bpb
    across = ((wb + 31) & ~31) >> 5
    return [((by >> 5) * across + (bx >> 5)) * tile + inner[((by & 31) << 5) | (bx & 31)]
            for by in range(hb) for bx in range(wb)]


def decode_level(fmt, w, h, tiled):
    """The tiled level's texels as [r, g, b, a] planes of w * h."""
    bpb = BLOCK_BYTES[fmt & 0x3F]
    dxt1 = fmt & 0x3F == DXT1
    wb, hb = (w + 3) // 4, (h + 3) // 4
    data = _swap16(tiled)
    planes = [bytearray(w * h) for _ in range(4)]
    R, G, B, A = planes
    offs = block_offsets(wb, hb, bpb)
    unpack = struct.unpack_from
    full = w % 4 == 0 and h % 4 == 0
    k = 0
    for by in range(hb):
        for bx in range(wb):
            o = offs[k]
            k += 1
            co = o if dxt1 else o + 8
            c0, c1, idx = unpack("<HHI", data, co)
            pal = _colour_palette(c0, c1, c0 > c1 or not dxt1)
            ids = [(idx >> (2 * i)) & 3 for i in range(16)]
            if dxt1:
                al = [pal[i][3] for i in ids]
            else:
                bits = int.from_bytes(data[o + 2:o + 8], "little")
                apal = _alpha_palette(data[o], data[o + 1])
                al = [apal[(bits >> (3 * i)) & 7] for i in range(16)]
            if full:
                for row in range(4):
                    at = (by * 4 + row) * w + bx * 4
                    r = ids[row * 4:row * 4 + 4]
                    R[at:at + 4] = bytes(pal[i][0] for i in r)
                    G[at:at + 4] = bytes(pal[i][1] for i in r)
                    B[at:at + 4] = bytes(pal[i][2] for i in r)
                    A[at:at + 4] = bytes(al[row * 4:row * 4 + 4])
            else:
                for i in range(16):
                    x, y = bx * 4 + (i & 3), by * 4 + (i >> 2)
                    if x < w and y < h:
                        at = y * w + x
                        p = pal[ids[i]]
                        R[at], G[at], B[at], A[at] = p[0], p[1], p[2], al[i]
    return planes


def decode_half(fmt, w, h, tiled):
    """The tiled level (sizes a multiple of 8) decoded straight to its next mip: planes of
    w/2 * h/2, each texel the mean of a 2 x 2 group (as halve(decode_level(...)) gives, at a
    quarter of the writes)."""
    bpb = BLOCK_BYTES[fmt & 0x3F]
    dxt1 = fmt & 0x3F == DXT1
    wb, hb, w2 = w // 4, h // 4, w // 2
    data = _swap16(tiled)
    planes = [bytearray(w2 * (h // 2)) for _ in range(4)]
    R, G, B, A = planes
    offs = block_offsets(wb, hb, bpb)
    unpack = struct.unpack_from
    k = 0
    for by in range(hb):
        for bx in range(wb):
            o = offs[k]
            k += 1
            c0, c1, idx = unpack("<HHI", data, o if dxt1 else o + 8)
            pal = _colour_palette(c0, c1, c0 > c1 or not dxt1)
            ids = [(idx >> (2 * i)) & 3 for i in range(16)]
            if dxt1:
                al = [pal[i][3] for i in ids]
            else:
                bits = int.from_bytes(data[o + 2:o + 8], "little")
                apal = _alpha_palette(data[o], data[o + 1])
                al = [apal[(bits >> (3 * i)) & 7] for i in range(16)]
            for sy in range(2):
                at = (by * 2 + sy) * w2 + bx * 2
                for sx in range(2):
                    q = sy * 8 + sx * 2
                    p0, p1, p2, p3 = pal[ids[q]], pal[ids[q + 1]], pal[ids[q + 4]], pal[ids[q + 5]]
                    R[at + sx] = (p0[0] + p1[0] + p2[0] + p3[0] + 2) >> 2
                    G[at + sx] = (p0[1] + p1[1] + p2[1] + p3[1] + 2) >> 2
                    B[at + sx] = (p0[2] + p1[2] + p2[2] + p3[2] + 2) >> 2
                    A[at + sx] = (al[q] + al[q + 1] + al[q + 4] + al[q + 5] + 2) >> 2
    return planes


def encode_level(fmt, w, h, planes):
    """A level (power-of-two size, 32 blocks or more each way) encoded and tiled: w/4 * h/4 blocks."""
    bpb = BLOCK_BYTES[fmt & 0x3F]
    dxt1 = fmt & 0x3F == DXT1
    wb, hb = w // 4, h // 4
    out = bytearray(wb * hb * bpb)
    R, G, B, A = planes
    offs = block_offsets(wb, hb, bpb)
    k = 0
    for by in range(hb):
        rows = [(by * 4 + r) * w for r in range(4)]
        for bx in range(wb):
            x = bx * 4
            px = []
            for r in rows:
                at = r + x
                px += zip(R[at:at + 4], G[at:at + 4], B[at:at + 4], A[at:at + 4])
            blk = encode_colour(px, True) if dxt1 else encode_alpha([p[3] for p in px]) + encode_colour(px, False)
            o = offs[k]
            k += 1
            out[o:o + bpb] = blk
    return bytes(_swap16(out))


def resize(planes, w, h, w2, h2):
    """Bilinear resize of the planes (each axis on its own)."""
    if w2 != w:
        planes = [_resize_rows(p, w, h, w2) for p in planes]
    if h2 != h:
        planes = [_resize_cols(p, w2, h, h2) for p in planes]
    return planes


def _weights(n, n2):
    out = []
    for i in range(n2):
        s = (i + 0.5) * n / n2 - 0.5
        a = max(0, min(n - 1, int(s // 1)))
        b = min(n - 1, a + 1)
        f = min(1.0, max(0.0, s - a))
        out.append((a, b, f))
    return out


def _resize_rows(p, w, h, w2):
    out = bytearray(w2 * h)
    ws = _weights(w, w2)
    for y in range(h):
        row = p[y * w:(y + 1) * w]
        base = y * w2
        for x, (a, b, f) in enumerate(ws):
            out[base + x] = int(row[a] + (row[b] - row[a]) * f + 0.5)
    return out


def _resize_cols(p, w, h, h2):
    out = bytearray(w * h2)
    for y, (a, b, f) in enumerate(_weights(h, h2)):
        ra, rb = p[a * w:(a + 1) * w], p[b * w:(b + 1) * w]
        out[y * w:(y + 1) * w] = bytes(int(u + (v - u) * f + 0.5) for u, v in zip(ra, rb))
    return out


def halve(planes, w, h):
    """A 2 x 2 box filter: the next mip level's planes."""
    w2, h2 = w // 2, h // 2
    out = []
    for p in planes:
        q = bytearray(w2 * h2)
        for y in range(h2):
            r0, r1 = 2 * y * w, (2 * y + 1) * w
            for x in range(w2):
                c = 2 * x
                q[y * w2 + x] = (p[r0 + c] + p[r0 + c + 1] + p[r1 + c] + p[r1 + c + 1] + 2) >> 2
        out.append(q)
    return out


def _pow2(v):
    """The power of two nearest v (by its logarithm)."""
    lo = 1 << (v.bit_length() - 1)
    return lo if v * v <= lo * lo * 2 else lo * 2


def bix_of_caff(tex):
    """The (.bix, _B.bix) contents for caff_texture's result, or None.

    A texture with its own mips (contiguous after the base) is split as it is, when its base's
    size is certain (power-of-two sizes of 128 texels or more each way). A one-level DXT1 or
    DXT5 texture gets mips made (see the module's notes), sized to powers of two first and
    halved while its base is over MAX_BASE_BYTES; it needs a mip of 128 texels or more each way.
    Anything else: None (left to the pack's copy)."""
    fmt, w, h, levels, gpu = tex
    pow2 = not (w & (w - 1)) and not (h & (h - 1))
    if levels > 1:
        if not pow2 or w < SMALLEST_MIP or h < SMALLEST_MIP:
            return None
        base = base_size(fmt, w, h)
        if base >= len(gpu):
            return None
        return b"BIX1" + struct.pack(">6I", w, h, levels, fmt, len(gpu), base) + gpu[base:], gpu[:base]
    if fmt & 0x3F not in (DXT1, DXT5) or (fmt >> 6) & 3 != ENDIAN_8IN16 or len(gpu) != base_size(fmt, w, h):
        return None
    w2, h2 = (w, h) if pow2 else (_pow2(w), _pow2(h))
    shrink = 0
    while base_size(fmt, w2 >> shrink, h2 >> shrink) > MAX_BASE_BYTES:
        shrink += 1
    if min(w2, h2) >> shrink < 2 * SMALLEST_MIP:
        return None
    if not pow2:
        planes = resize(decode_level(fmt, w, h, gpu), w, h, w2, h2)
        for _ in range(shrink):
            planes = halve(planes, w2, h2)
            w2, h2 = w2 // 2, h2 // 2
        base = encode_level(fmt, w2, h2, planes)
        planes = halve(planes, w2, h2)
    elif shrink:
        planes = decode_half(fmt, w, h, gpu)
        w2, h2 = w // 2, h // 2
        for _ in range(shrink - 1):
            planes = halve(planes, w2, h2)
            w2, h2 = w2 // 2, h2 // 2
        base = encode_level(fmt, w2, h2, planes)
        planes = halve(planes, w2, h2)
    else:
        base = gpu
        planes = decode_half(fmt, w, h, gpu)
    mips = []
    lw, lh = w2 // 2, h2 // 2
    while True:
        mips.append(encode_level(fmt, lw, lh, planes))
        if min(lw, lh) // 2 < SMALLEST_MIP:
            break
        planes = halve(planes, lw, lh)
        lw, lh = lw // 2, lh // 2
    chain = b"".join(mips)
    head = b"BIX1" + struct.pack(">6I", w2, h2, 1 + len(mips), fmt, len(base) + len(chain), len(base))
    return head + chain, bytes(base)
