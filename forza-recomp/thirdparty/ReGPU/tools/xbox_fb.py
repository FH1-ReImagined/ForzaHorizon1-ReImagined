"""Decode an XBDM screenshot (tools/xbdm.py screenshot): xbox_fb.py in.raw out.png [width height pitch_px] [--linear]

The framebuffer is Xenos 2D-tiled 32-bit pixels (as untile8.py with 4 bytes per texel); bytes are
B, G, R, A (checked against a console screenshot). Writes an 8-bit RGB PNG without third-party modules.
"""
import struct
import sys
import zlib


def outer(y, width, l):
    macro = ((y >> 5) * (width >> 5)) << (l + 7)
    micro = ((y & 6) << 2) << l
    return macro + ((micro & ~0xF) << 1) + (micro & 0xF) + ((y & 8) << (3 + l)) + ((y & 1) << 4)


def inner(x, y, base, l):
    macro = (x >> 5) << (l + 7)
    micro = (x & 7) << l
    o = base + (macro + ((micro & ~0xF) << 1) + (micro & 0xF))
    return ((o & ~0x1FF) << 3) + ((o & 0x1C0) << 2) + (o & 0x3F) + ((y & 16) << 7) + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)


def png(path, w, h, rows):
    raw = b"".join(b"\0" + r for r in rows)
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
                          chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def main(argv):
    linear = "--linear" in argv
    argv = [a for a in argv if a != "--linear"]
    src, dst = argv[0], argv[1]
    w, h, pitch = (int(argv[2]), int(argv[3]), int(argv[4])) if len(argv) >= 5 else (1280, 720, 1280)
    data = open(src, "rb").read()
    rows = []
    for y in range(h):
        base = outer(y, pitch, 2)
        r = bytearray()
        for x in range(w):
            o = (y * pitch + x) * 4 if linear else inner(x, y, base, 2)
            b, g, red, a = data[o:o + 4] if o + 4 <= len(data) else (0, 0, 0, 0)
            r += bytes((red, g, b))
        rows.append(bytes(r))
    png(dst, w, h, rows)


if __name__ == "__main__":
    main(sys.argv[1:])
