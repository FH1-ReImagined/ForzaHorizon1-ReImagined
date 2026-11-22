#!/usr/bin/env python3
"""Forza Motorsport (2005, original Xbox) track textures as Forza Motorsport 4's (#930), for tools/fm1_tracks.py.
Plain Python (no numpy, no PIL).

Textures: FM1's DXT1 / DXT5 textures (NV2A descriptors, plain little-endian blocks, mip levels one after another) ->
FM4's BIX1 pairs: header (w, h, levels, Xenos format word, total, main) + the mip chain, and the top level in _B.
Xenos layout: 16-bit words byte-swapped (8in16), blocks tiled (XGAddress2DTiledOffset, a block as one texel), each
level its own surface at least 32 x 32 blocks until the shorter side is 16 texels or less: those levels share one
packed 32 x 32-block surface (Xenia's GetPackedMipOffset). Format words: DXT1 0x1A200152, DXT5 0x1A200154 (FM4's /
flat_dxt1's: 0x100 tiled, 1 << 6 endian 8in16).
"""
import struct

DXT1, DXT5 = 0x1A200152, 0x1A200154
NV2A_DXT = {0x0C: DXT1, 0x0F: DXT5}   # FM1's DXT1, DXT5 (0x0E DXT3 not seen on tracks)


def tiled_offset(x, y, width, log_bpp):
    """byte offset of texel (x, y) in a tiled surface `width` texels wide (2**log_bpp bytes each)"""
    aligned = (width + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << (log_bpp + 7)
    micro = ((x & 7) + ((y & 6) << 2)) << log_bpp
    offset = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << (3 + log_bpp)) + ((y & 1) << 4)
    return (((offset & ~511) << 3) + ((offset & 448) << 2) + (offset & 63) + ((y & 16) << 7)
            + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6))


def packed_mip_offset(width, height, mip):
    """(x, y) texels of a mip level within the shared surface of the small levels, or None"""
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


def level_blocks(width, height, mip):
    return max(1, ((width >> mip) + 3) // 4), max(1, ((height >> mip) + 3) // 4)


def _a32(n):
    return (n + 31) & ~31


def _swap16(b):
    out = bytearray(len(b))
    out[0::2], out[1::2] = b[1::2], b[0::2]
    return bytes(out)


def to_bix(levels, width, height, fmt):
    """(bix, bix_B) of a DXT texture: levels[i] = mip i's blocks, PC order, row by row"""
    bb = 8 if fmt == DXT1 else 16
    log_bpp = 3 if bb == 8 else 4
    bw, bh = level_blocks(width, height, 0)
    main_size = _a32(bw) * _a32(bh) * bb
    layout, at, packed = [(0, _a32(bw), 0, 0)], main_size, None
    for i in range(1, len(levels)):
        po = packed_mip_offset(width, height, i)
        if po is None:
            w, h = level_blocks(width, height, i)
            layout.append((at, _a32(w), 0, 0))
            at += _a32(w) * _a32(h) * bb
        else:
            if packed is None:
                packed, at = at, at + 32 * 32 * bb
            layout.append((packed, 32, po[0] // 4, po[1] // 4))
    data = bytearray(at)
    end = main_size
    for i, (blocks, (base, pitch, ox, oy)) in enumerate(zip(levels, layout)):
        w, h = level_blocks(width, height, i)
        if len(blocks) < w * h * bb:
            raise ValueError("mip %d: %d bytes, not %d" % (i, len(blocks), w * h * bb))
        sw = _swap16(blocks[:w * h * bb])
        for by in range(h):
            for bx in range(w):
                o = base + tiled_offset(bx + ox, by + oy, pitch, log_bpp)
                s = (by * w + bx) * bb
                data[o:o + bb] = sw[s:s + bb]
                end = max(end, o + bb)
    if packed is not None:  # the packed tail is one 4 KB page in FM4's / FH1's square .bix files (caff.mip_chain_size);
        # a non-square texture's small levels can sit further in (Xenos packs them along the longer side): to its last page
        data = data[:packed + max(4096, (end - packed + 4095) & ~4095)]
    top, mips = bytes(data[:main_size]), bytes(data[main_size:])
    hdr = b"BIX1" + struct.pack(">6I", width, height, len(levels), fmt, main_size + len(mips), main_size)
    return hdr + mips, top


def _decode_block(fmt, blk):
    """16 RGBA texels of one DXT1 / DXT5 block (PC layout), row by row"""
    if fmt == DXT5:
        a0, a1 = blk[0], blk[1]
        bits = int.from_bytes(blk[2:8], "little")
        if a0 > a1:
            pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
        else:
            pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
        alpha = [pal[(bits >> (3 * i)) & 7] for i in range(16)]
        blk = blk[8:]
    else:
        alpha = None
    c0, c1, idx = struct.unpack_from("<HHI", blk)
    def rgb(c):
        r, g, b = c >> 11, (c >> 5) & 63, c & 31
        return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)
    p0, p1 = rgb(c0), rgb(c1)
    if c0 > c1 or fmt == DXT5:
        pal = [p0, p1, tuple((2 * a + b) // 3 for a, b in zip(p0, p1)), tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))]
        a3 = 255
    else:
        pal = [p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1)), (0, 0, 0)]
        a3 = 0
    out = []
    for i in range(16):
        k = (idx >> (2 * i)) & 3
        out.append(pal[k] + ((alpha[i] if alpha else (a3 if k == 3 else 255)),))
    return out


def _encode_block(fmt, px):
    """one DXT1 / DXT5 block of 16 RGBA texels: endpoints the two colours furthest apart (exact for blocks of up to two
    colours, as nearest-scaled small textures mostly are)"""
    def to565(c):
        return (c[0] >> 3) << 11 | (c[1] >> 2) << 5 | c[2] >> 3
    def rgb(c):
        r, g, b = c >> 11, (c >> 5) & 63, c & 31
        return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)
    cols = list({p[:3] for p in px})
    best, pair = -1, (cols[0], cols[0])
    for i in range(len(cols)):
        for j in range(i, len(cols)):
            dd = sum((a - b) ** 2 for a, b in zip(cols[i], cols[j]))
            if dd > best:
                best, pair = dd, (cols[i], cols[j])
    c0, c1 = to565(pair[0]), to565(pair[1])
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:
        idx = 0
    else:
        p0, p1 = rgb(c0), rgb(c1)
        pal = [p0, p1, tuple((2 * a + b) // 3 for a, b in zip(p0, p1)), tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))]
        idx = 0
        for i, p in enumerate(px):
            k = min(range(4), key=lambda k: sum((a - b) ** 2 for a, b in zip(pal[k], p[:3])))
            idx |= k << (2 * i)
    colour = struct.pack("<HHI", c0, c1, idx)
    if fmt != DXT5:
        return colour
    al = [p[3] for p in px]
    a0, a1 = max(al), min(al)
    if a0 == a1:
        return bytes([a0, a1]) + bytes(6) + colour
    pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    bits = 0
    for i, a in enumerate(al):
        bits |= min(range(8), key=lambda k: abs(pal[k] - a)) << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, "little") + colour


def upscale_small_levels(fmt, w, h, levels, min_side=32):
    """(w, h, levels) of a small DXT texture scaled up as upscale_small, with a full mip chain (FM4's .bix files
    always have one; a short chain read empty blocks from far away): new level j is the top level scaled by k >> j
    while that is 2 or more, then the source's own levels"""
    k = 1
    while min(w, h) * k < min_side:
        k *= 2
    if k == 1:
        return w, h, levels
    nw, nh = w * k, h * k
    if max(nw, nh) > 8 * min(nw, nh):
        # past 8:1 (Road America's 64 x 4 -> 512 x 32, Silverstone's 128 x 8): the long side scaled less, so the
        # shape stays one whose mip layout is checked on FM4's files (up to 8:1; uv is 0..1, so it looks the same)
        if nw > nh:
            nw = max(w, nh * 8)
        else:
            nh = max(h, nw * 8)
    # every level from the top one (nearest up, mean down). The source's own levels ran out before 1 x 1 on
    # non-square ones and the last was repeated at the wrong size: far levels read garbage (Road America's
    # rumblestrip lightmaps, chain-link fence tops)
    return nw, nh, resize_levels(fmt, w, h, levels[0], nw, nh)


def resize_levels(fmt, w, h, top, nw, nh):
    """a DXT texture's top level resampled to nw x nh (each new texel the mean of the source texels it covers, or
    the nearest one when scaling up), with a full mip chain built the same way: -> levels"""
    bb = 8 if fmt == DXT1 else 16
    bw, bh = level_blocks(w, h, 0)
    img = [[None] * w for _ in range(h)]
    for by in range(bh):
        for bx in range(bw):
            px = _decode_block(fmt, top[(by * bw + bx) * bb:(by * bw + bx + 1) * bb])
            for i, p in enumerate(px):
                x, y = bx * 4 + i % 4, by * 4 + i // 4
                if x < w and y < h:
                    img[y][x] = p
    out = []
    lw, lh = nw, nh
    while True:
        def texel(x, y):
            x0, x1 = x * w // lw, max(x * w // lw + 1, (x + 1) * w // lw)
            y0, y1 = y * h // lh, max(y * h // lh + 1, (y + 1) * h // lh)
            ps = [img[yy][xx] for yy in range(y0, y1) for xx in range(x0, x1)]
            return tuple(sum(p[c] for p in ps) // len(ps) for c in range(len(ps[0])))
        grid = [[texel(x, y) for x in range(lw)] for y in range(lh)]
        blocks = bytearray()
        for by in range((lh + 3) // 4):
            for bx in range((lw + 3) // 4):
                blocks += _encode_block(fmt, [grid[min(lh - 1, by * 4 + i // 4)][min(lw - 1, bx * 4 + i % 4)]
                                              for i in range(16)])
        out.append(bytes(blocks))
        if lw == 1 and lh == 1:
            return out
        lw, lh = max(1, lw // 2), max(1, lh // 2)





def over_colour(levels, fmt, rgb):
    """DXT5 levels with their colour drawn over a flat colour by their own alpha, per block (the block's mean alpha
    moves both colour endpoints towards rgb; indices kept), alpha made opaque: FM1's see-through water (Tsukuba's
    pond, alpha 115-183 all over) drawn solid over a sky-like colour."""
    if fmt != DXT5:
        return levels
    def unpack(c):
        r, g, b = c >> 11, (c >> 5) & 63, c & 31
        return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)
    def pack(c):
        return (min(255, c[0]) >> 3) << 11 | (min(255, c[1]) >> 2) << 5 | min(255, c[2]) >> 3
    out = []
    for blocks in levels:
        new = bytearray(blocks)
        for o in range(0, len(blocks) - 15, 16):
            a0, a1 = blocks[o], blocks[o + 1]
            bits = int.from_bytes(blocks[o + 2:o + 8], "little")
            if a0 > a1:
                pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
            else:
                pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
            a = sum(pal[(bits >> (3 * i)) & 7] for i in range(16)) / (16 * 255)
            c0, c1 = struct.unpack_from("<HH", blocks, o + 8)
            n0, n1 = (pack(tuple(int(x * a + y * (1 - a) + 0.5) for x, y in zip(unpack(c), rgb))) for c in (c0, c1))
            new[o:o + 8] = bytes([255, 255]) + bytes(6)
            struct.pack_into("<HH", new, o + 8, n0, n1)
        out.append(bytes(new))
    return out


def grey_curve(src, dst):
    """a monotone grey mapping (a function 0..255 -> 0..255) through (0, 0), the pairs of src / dst quantiles (both
    sorted) and (255, 255): FM1's lightmap brightness laid onto FM4's for the same circuit"""
    pts = [(0.0, 0.0)]
    for a, b in zip(src, dst):
        if a > pts[-1][0] and b >= pts[-1][1] and a < 255:
            pts.append((float(a), float(b)))
    pts.append((255.0, 255.0))

    def f(g):
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if g <= x1:
                return y0 + (y1 - y0) * (g - x0) / (x1 - x0)
        return 255.0
    return f


def map_grey(levels, fmt, f):
    """DXT1 / DXT5 levels with each colour endpoint's grey g made f(g), its colour cast (channel minus grey) scaled by
    g / f(g) when lifted: scaling Sunset's bluish dark lightmaps turned the infield blue, so a lifted texel comes
    out nearly neutral. Indices and alpha are kept; a block whose endpoints change order gets them swapped and its
    indices remapped (it keeps its colour mode)"""
    bb, co = (8, 0) if fmt == DXT1 else (16, 8)

    def unpack(c):
        r, g, b = c >> 11, (c >> 5) & 63, c & 31
        return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)

    def mapped(c):
        p = unpack(c)
        g = grey(p)
        n = f(g)
        k = min(1.0, g / n) if n > 0 else 1.0       # a lifted texel's cast shrinks as much as it is lifted
        q = [max(0, min(255, int(n + (x - g) * k + 0.5))) for x in p]
        return (q[0] >> 3) << 11 | (q[1] >> 2) << 5 | q[2] >> 3
    cache = {}
    out = []
    for blocks in levels:
        new = bytearray(blocks)
        for o in range(co, len(blocks) - bb + co + 1, bb):
            c0, c1, idx = struct.unpack_from("<HHI", blocks, o)
            four = c0 > c1 or fmt == DXT5
            n0 = cache.setdefault(c0, mapped(c0))
            n1 = cache.setdefault(c1, mapped(c1))
            if four and fmt == DXT1 and n0 <= n1:
                if n0 == n1:
                    idx = 0
                else:
                    n0, n1, idx = n1, n0, idx ^ 0x55555555
            elif not four and n0 > n1:          # DXT1 3-colour (+ clear) block: keep c0 <= c1
                n0, n1 = n1, n0
                idx = sum((({0: 1, 1: 0}.get((idx >> (2 * i)) & 3, (idx >> (2 * i)) & 3)) << (2 * i)) for i in range(16))
            struct.pack_into("<HHI", new, o, n0, n1, idx)
        out.append(bytes(new))
    return out


class Atlases:
    """Lightmaps of one size packed side by side into side x side DXT1 pages, their blocks copied as they are (mip
    levels too, while a lightmap's level is 4 texels or more; the page's smaller levels are made from the last one
    copied). FM1 gives every mesh a lightmap of its own (Tokyo 1,242, mostly 64 x 64 or less) where FM4 packs them;
    the game loads a circuit's models and textures through one list of 6,144 (fm1_tracks.MAX_LOAD), and with its
    lightmaps in atlases a lit mesh can be joined with its neighbours too. add() -> (page, (u0, v0, du, dv)): the
    lightmap's place in its page, for its uv"""

    def __init__(self, side=512, most=128):
        self.side, self.most = side, most
        self.pages = []          # {"size": (w, h), "cells": [levels]}

    def add(self, w, h, levels):
        s = self.side
        if max(w, h) > self.most or min(w, h) < 4 or w & (w - 1) or h & (h - 1):
            return None
        page = next((p for p in reversed(self.pages) if p["size"] == (w, h)
                     and len(p["cells"]) < (s // w) * (s // h)), None)
        if page is None:
            page = {"size": (w, h), "cells": []}
            self.pages.append(page)
        k = len(page["cells"])
        page["cells"].append(levels)
        col, row = k % (s // w), k // (s // w)
        return self.pages.index(page), (col * w / s, row * h / s, w / s, h / s)

    def page_levels(self, i):
        """the DXT1 levels of page i (side x side, full chain)"""
        s, page = self.side, self.pages[i]
        w, h = page["size"]
        cols = s // w
        grey = struct.pack("<HHI", 0x8410, 0x8410, 0)
        last = 0
        while (min(w, h) >> (last + 1)) >= 4 and all(len(c) > last + 1 for c in page["cells"]):
            last += 1
        levels = []
        for k in range(last + 1):
            pb = (s >> k) // 4
            out = bytearray(grey * (pb * pb))
            cw, ch = (w >> k) // 4, (h >> k) // 4
            for n, cell in enumerate(page["cells"]):
                x0, y0 = (n % cols) * cw, (n // cols) * ch
                src = cell[k]
                for by in range(ch):
                    o = ((y0 + by) * pb + x0) * 8
                    out[o:o + cw * 8] = src[by * cw * 8:(by + 1) * cw * 8]
            levels.append(bytes(out))
        ls = s >> last
        if ls > 1:
            levels += resize_levels(DXT1, ls, ls, levels[-1], ls // 2, ls // 2)
        return levels


def bix_mean_grey(data):
    """mean grey of an FM4 .bix's level 1 (the file holds its header and the levels below the top), or None"""
    if data[:4] != b"BIX1":
        return None
    w, h, n, fmt = struct.unpack_from(">4I", data, 4)
    fmt = {0x52: DXT1, 0x54: DXT5}.get(fmt & 0xFF)
    if n < 2 or fmt is None:
        return None
    bb = 8 if fmt == DXT1 else 16
    mips = data[28:]
    po = packed_mip_offset(w, h, 1)
    bw, bh = level_blocks(w, h, 1)
    pitch, ox, oy = (32, po[0] // 4, po[1] // 4) if po else (_a32(bw), 0, 0)
    total = count = 0
    for by in range(bh):
        for bx in range(bw):
            o = tiled_offset(bx + ox, by + oy, pitch, 3 if bb == 8 else 4)
            blk = _swap16(mips[o:o + bb])
            if len(blk) < bb:
                continue
            for p in _decode_block(fmt, blk):
                total += grey(p)
                count += 1
    return total / count if count else None


def _alpha_palette(a0, a1):
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


def _scaled_alpha_blocks(blocks, s):
    """DXT5 blocks with both alpha endpoints multiplied by s (clamped to 255), indices kept"""
    new = bytearray(blocks)
    for o in range(0, len(blocks) - 15, 16):
        new[o] = min(255, int(blocks[o] * s + 0.5))
        new[o + 1] = min(255, int(blocks[o + 1] * s + 0.5))
    return bytes(new)


def alpha_coverage(blocks, ref=128):
    """share of a DXT5 level's texels with alpha >= ref"""
    hit = n = 0
    for o in range(0, len(blocks) - 15, 16):
        pal = _alpha_palette(blocks[o], blocks[o + 1])
        bits = int.from_bytes(blocks[o + 2:o + 8], "little")
        hit += sum(1 for i in range(16) if pal[(bits >> (3 * i)) & 7] >= ref)
        n += 16
    return hit / n if n else 0.0


def keep_alpha_coverage(levels, fmt, ref=128, most=8.0):
    """DXT5 levels whose smaller levels keep the top level's share of alpha >= ref: each level's alpha endpoints are
    scaled up (by at most `most`) until it does. Averaging a cut-out's alpha down the chain thins it (FM1's oak
    leaves: 14 % solid at the top, 5 % three levels down), so trees faded into pale see-through cards from afar"""
    if fmt != DXT5 or len(levels) < 2:
        return levels
    want = alpha_coverage(levels[0], ref)
    if want <= 0.0:
        return levels
    out = [levels[0]]
    for blocks in levels[1:]:
        if alpha_coverage(blocks, ref) >= want:
            out.append(blocks)
            continue
        lo, hi = 1.0, most
        if alpha_coverage(_scaled_alpha_blocks(blocks, hi), ref) < want:
            lo = hi
        else:
            for _ in range(12):
                mid = (lo + hi) / 2
                if alpha_coverage(_scaled_alpha_blocks(blocks, mid), ref) >= want:
                    hi = mid
                else:
                    lo = mid
            lo = hi
        out.append(_scaled_alpha_blocks(blocks, lo))
    return out


def hard_alpha(levels, fmt, ref=128):
    """DXT5 levels with every texel's alpha made 255 (>= ref) or 0: the game draws alpha-tested track models with
    their alpha as a dither (alpha to coverage), so FM1's soft-edged leaves (a third of their texels in between)
    drew as a speckled see-through screen with each card's rectangle showing; FM4's own leaves are hard already"""
    if fmt != DXT5:
        return levels
    out = []
    for blocks in levels:
        new = bytearray(blocks)
        for o in range(0, len(blocks) - 15, 16):
            pal = _alpha_palette(blocks[o], blocks[o + 1])
            bits = int.from_bytes(blocks[o + 2:o + 8], "little")
            solid = [pal[(bits >> (3 * i)) & 7] >= ref for i in range(16)]
            if all(solid):
                new[o:o + 8] = bytes([255, 255]) + bytes(6)
            elif not any(solid):
                new[o:o + 8] = bytes([0, 0]) + bytes(6)
            else:       # 8-alpha mode: index 0 = 255, index 1 = 0
                nb = sum(1 << (3 * i) for i in range(16) if not solid[i])
                new[o:o + 8] = bytes([255, 0]) + nb.to_bytes(6, "little")
        out.append(bytes(new))
    return out


def scale_colour(levels, fmt, k):
    """DXT1 / DXT5 levels with their colour multiplied by k (<= 1), on the blocks: both endpoints scaled, indices and
    alpha kept. Scaling keeps the endpoints' order (or makes them equal: a DXT1 block that was in 4-colour mode then
    gets every index 0, all four colours being one anyway, so it does not turn into the 3-colour + clear mode)."""
    bb, co = (8, 0) if fmt == DXT1 else (16, 8)
    def scale(c):
        r, g, b = c >> 11, (c >> 5) & 63, c & 31
        return int(r * k + 0.5) << 11 | int(g * k + 0.5) << 5 | int(b * k + 0.5)
    out = []
    for blocks in levels:
        new = bytearray(blocks)
        for o in range(0, len(blocks) - bb + 1, bb):
            c0, c1 = struct.unpack_from("<HH", blocks, o + co)
            n0, n1 = scale(c0), scale(c1)
            struct.pack_into("<HH", new, o + co, n0, n1)
            if fmt == DXT1 and c0 > c1 and n0 == n1:
                new[o + 4:o + 8] = bytes(4)
        out.append(bytes(new))
    return out




def nv2a_levels(desc, data):
    """(Xenos format word, width, height, [mip level blocks]) of an FM1 DXT texture, or None for other formats. The
    20-byte NV2A descriptor: u32 common, data, lock, format (8-15 colour format, 16-19 mip levels, 20-23 log2 w,
    24-27 log2 h), size"""
    _, _, _, f, size = struct.unpack_from("<5I", desc)
    cf = (f >> 8) & 0xFF
    if cf not in NV2A_DXT or size or (f >> 4) & 0xF == 3 or f & 4:
        return None
    w, h = 1 << ((f >> 20) & 0xF), 1 << ((f >> 24) & 0xF)
    bb = 8 if cf == 0x0C else 16
    levels, at = [], 0
    for i in range(max(1, (f >> 16) & 0xF)):
        bw, bh = level_blocks(w, h, i)
        n = bw * bh * bb
        if at + n > len(data):
            break
        levels.append(data[at:at + n])
        at += n
    return NV2A_DXT[cf], w, h, levels


# ---- reading FM1 textures: every track texture is DXT1 / DXT5 (2D) or a DXT1 volume (instanced trees' light)

def grey(p):
    """a texel's luminance, as PIL's convert("L"): ITU-R 601-2, rounded"""
    return (p[0] * 19595 + p[1] * 38470 + p[2] * 7471 + 0x8000) >> 16


def top_texels(desc, data):
    """(width, height, [RGBA texel] row by row) of a DXT texture's top level, or None for other formats"""
    got = nv2a_levels(desc, data)
    if not got or not got[3]:
        return None
    fmt, w, h, levels = got
    bb = 8 if fmt == DXT1 else 16
    bw, bh = level_blocks(w, h, 0)
    px = [None] * (w * h)
    top = levels[0]
    for by in range(bh):
        for bx in range(bw):
            for i, p in enumerate(_decode_block(fmt, top[(by * bw + bx) * bb:(by * bw + bx + 1) * bb])):
                x, y = bx * 4 + i % 4, by * 4 + i // 4
                if x < w and y < h:
                    px[y * w + x] = p
    return w, h, px


def cutout_share(desc, data):
    """share of a DXT texture's top-level texels with alpha < 128 (0 for other formats). Only the alpha is read"""
    got = nv2a_levels(desc, data)
    if not got or not got[3]:
        return 0.0
    fmt, w, h, levels = got
    top, holes, n = levels[0], 0, 0
    if fmt == DXT5:
        for o in range(0, len(top) - 15, 16):
            a0, a1 = top[o], top[o + 1]
            bits = int.from_bytes(top[o + 2:o + 8], "little")
            if a0 > a1:
                pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
            else:
                pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
            holes += sum(1 for i in range(16) if pal[(bits >> (3 * i)) & 7] < 128)
            n += 16
    else:
        for o in range(0, len(top) - 7, 8):
            c0, c1, idx = struct.unpack_from("<HHI", top, o)
            if c0 <= c1:
                holes += sum(1 for i in range(16) if (idx >> (2 * i)) & 3 == 3)
            n += 16
    return holes / n if n else 0.0


def volume_mean_grey(data, slices_bytes=512):
    """mean luminance of an instanced tree's DXT1 16 x 16 x 4 volume lightmap: its first 512 bytes are the four
    slices (read as one 16 x 64 image; the rest pads with grey 129)"""
    if len(data) < slices_bytes:
        return None
    total = 0
    for o in range(0, slices_bytes, 8):
        total += sum(grey(p) for p in _decode_block(DXT1, data[o:o + 8]))
    return total / (slices_bytes // 8 * 16)


def dxt5_to_dxt1(blocks):
    """DXT5 blocks (16 bytes: alpha 8, colour 8; little-endian) -> DXT1 (the colour 8). A DXT5 colour block is always
    read with 4 colours; DXT1 reads c0 <= c1 as 3 colours + transparent black, so such blocks get their endpoints swapped
    and their indices remapped (0<->1, 2<->3), and c0 == c1 blocks index 0 throughout."""
    out = bytearray(len(blocks) // 2)
    for k in range(0, len(blocks) // 16):
        c0, c1, idx = struct.unpack_from("<HHI", blocks, 16 * k + 8)
        if c0 < c1:
            c0, c1 = c1, c0
            idx ^= 0x55555555                      # 0<->1, 2<->3 in every 2-bit index
        elif c0 == c1:
            idx = 0
        struct.pack_into("<HHI", out, 8 * k, c0, c1, idx)
    return bytes(out)


# ---- textures made here

FLAT_MAIN, FLAT_MIPS = 8192, 20480     # a 128 x 128 DXT1 tiled top level, its 8-level chain (Xenos padding, 4 KB tail)


def flat_dxt1(rgb):
    """128 x 128 DXT1 (8in16, tiled) of one colour, all mip levels: -> (bix, bix_B)"""
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    c565 = (r >> 3) << 11 | (g >> 2) << 5 | (b >> 3)
    block = struct.pack(">HHHH", c565, c565, 0, 0)
    hdr = b"BIX1" + struct.pack(">6I", 128, 128, 8, DXT1, FLAT_MAIN + FLAT_MIPS, FLAT_MAIN)
    return hdr + block * (FLAT_MIPS // 8), block * (FLAT_MAIN // 8)


def grey_palette(levels=32):
    """(bix, bix_B): a 128 x 32 DXT1 lightmap of `levels` greys, column block k = grey (k + 0.5) * 256 / levels, with
    a full mip chain (instanced trees' light: each tree's uv points at its column)"""
    grey_of = [int((x // 4 + 0.5) * 256 / levels) for x in range(128)]
    out, w, h = [], 128, 32
    while True:
        k = 128 // w
        row = [sum(grey_of[x * k:(x + 1) * k]) // k for x in range(w)]
        blocks = bytearray()
        for _ in range(max(1, (h + 3) // 4)):
            for bx in range(max(1, (w + 3) // 4)):
                v = sum(row[bx * 4:bx * 4 + 4]) // len(row[bx * 4:bx * 4 + 4])
                c = (v >> 3) << 11 | (v >> 2) << 5 | (v >> 3)
                blocks += struct.pack("<HHI", c, c, 0)
        out.append(bytes(blocks))
        if w == 1 and h == 1:
            break
        w, h = max(1, w // 2), max(1, h // 2)
    return to_bix(out, 128, 32, DXT1)
