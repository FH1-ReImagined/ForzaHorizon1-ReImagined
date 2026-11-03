"""Untile Xenos 8-bit planes: untile8.py in.raw out.raw width height pitch

in.raw holds frames of pitch * align32(height) bytes in the Xenos 2D tiled
layout (as in src/gpu/texture.cpp); out.raw gets width x height linear frames.
"""
import sys


def outer(y, width, log2bpp=0):
    macro = ((y >> 5) * (width >> 5)) << (log2bpp + 7)
    micro = ((y & 6) << 2) << log2bpp
    return macro + ((micro & ~0xF) << 1) + (micro & 0xF) + ((y & 8) << (3 + log2bpp)) + ((y & 1) << 4)


def inner(x, y, base, log2bpp=0):
    macro = (x >> 5) << (log2bpp + 7)
    micro = (x & 7) << log2bpp
    offset = base + (macro + ((micro & ~0xF) << 1) + (micro & 0xF))
    return ((offset & ~0x1FF) << 3) + ((offset & 0x1C0) << 2) + (offset & 0x3F) + ((y & 16) << 7) + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)


src, dst, w, h, pitch = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
frame_bytes = pitch * ((h + 31) & ~31)
data = open(src, "rb").read()
offsets = []
for y in range(h):
    row = outer(y, pitch)
    offsets.append([inner(x, y, row) for x in range(w)])
with open(dst, "wb") as out:
    for f in range(len(data) // frame_bytes):
        frame = data[f * frame_bytes:(f + 1) * frame_bytes]
        out.write(bytes(frame[o] if o < len(frame) else 0 for row in offsets for o in row))
