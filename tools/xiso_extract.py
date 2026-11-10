#!/usr/bin/env python3
"""Read-only Xbox 360 disc image (ISO) lister/extractor (standard library only).

A disc image holds an XDVDFS volume (the same file system as inside a Games on Demand
package, see xcontent_extract.Xdvdfs) at one of the offsets the disc formats use:
0 (a plain XDVDFS image), 0x2080000 (XGD3), 0xFD90000 (XGD2), 0x18300000 (XGD1), and
0x2090000 (some XGD3 dumps). The volume descriptor ("MICROSOFT*XBOX*MEDIA", root sector,
root size) sits at sector 32 of the volume. The image is only ever opened for reading.

An image split into parts (game.iso.1, game.iso.001, game.001, game.part1.iso, game.1.iso...)
is read as one: choose any part, the others are found next to it (split_parts).

Usage:
  xiso_extract.py ISO info
  xiso_extract.py ISO list
  xiso_extract.py ISO extract OUTDIR [PATH ...]   (no PATH = everything)
"""
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xcontent_extract as xc  # noqa: E402

SECTOR = 0x800
# Where the XDVDFS volume starts in the image, by disc format (the first match wins).
VOLUME_OFFSETS = ((0x2080000, "XGD3"), (0xFD90000, "XGD2"), (0x18300000, "XGD1"),
                  (0x2090000, "XGD3"), (0, "plain XDVDFS"))


class IsoError(Exception):
    pass


# The ways a disc image is split into parts: (name pattern, how the parts' names are made).
# group "n" is the part number; a part name is base + number (+ ext), the number zero-padded
# like the chosen part's.
_IMAGE_EXT = r"(?:iso|xiso|img|bin)"
PART_PATTERNS = (
    re.compile(r"(?i)^(?P<base>.+\." + _IMAGE_EXT + r"\.(?:part)?)(?P<n>\d{1,3})(?P<ext>)$"),
    re.compile(r"(?i)^(?P<base>.+\.)(?P<n>\d{3})(?P<ext>)$"),
    re.compile(r"(?i)^(?P<base>.+\.part)(?P<n>\d{1,3})(?P<ext>\." + _IMAGE_EXT + r")?$"),
    re.compile(r"(?i)^(?P<base>.+\.)(?P<n>\d)(?P<ext>\." + _IMAGE_EXT + r")$"),
)


def part_number(path):
    """The part number in a split image part's name, or None."""
    name = os.path.basename(path)
    for pattern in PART_PATTERNS:
        m = pattern.match(name)
        if m:
            return int(m.group("n"))
    return None


def split_parts(path):
    """The parts of a split image `path` is one of, in order ([path] when it is not a part).
    Raises IsoError naming the first missing part."""
    folder, name = os.path.split(os.path.abspath(path))
    for pattern in PART_PATTERNS:
        m = pattern.match(name)
        if not m:
            continue
        base, ext = m.group("base").lower(), (m.group("ext") or "").lower()
        found = {}
        try:
            names = os.listdir(folder)
        except OSError:
            return [path]
        for other in names:
            o = pattern.match(other)
            if (o and o.group("base").lower() == base and (o.group("ext") or "").lower() == ext
                    and os.path.isfile(os.path.join(folder, other))):
                found[int(o.group("n"))] = os.path.join(folder, other)
        if len(found) < 2:
            continue
        first = min(found)
        if first > 1:
            raise IsoError(f"{name} is part {int(m.group('n'))} of a split disc image, but its "
                           f"first part is not next to it.")
        for n in range(first, max(found) + 1):
            if n not in found:
                raise IsoError(f"{name} is one part of a split disc image, but part {n} is not "
                               "next to it: put all the parts in one folder.")
        return [found[n] for n in sorted(found)]
    return [path]


class Parts:
    """The parts of a split image as one read-only file (seek, read, tell, close)."""

    def __init__(self, paths):
        self.parts, self.starts, pos = [], [], 0
        try:
            for p in paths:
                f = open(p, "rb")
                self.parts.append(f)
                self.starts.append(pos)
                pos += os.fstat(f.fileno()).st_size
        except OSError:
            self.close()
            raise
        self.size, self.pos = pos, 0

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n < 0:
            n = self.size - self.pos
        out = []
        while n > 0 and self.pos < self.size:
            i = max(k for k, s in enumerate(self.starts) if s <= self.pos)
            f = self.parts[i]
            f.seek(self.pos - self.starts[i])
            end = self.starts[i + 1] if i + 1 < len(self.parts) else self.size
            chunk = f.read(min(n, end - self.pos))
            if not chunk:
                break
            out.append(chunk)
            self.pos += len(chunk)
            n -= len(chunk)
        return b"".join(out)

    def close(self):
        for f in self.parts:
            f.close()


def _open(path, whole=False):
    """(file, size, parts) of the image: a split image's parts joined (split_parts), unless
    `whole` (the chosen file by itself)."""
    parts = [path] if whole else split_parts(path)
    if len(parts) == 1:
        f = open(parts[0], "rb")
        return f, os.fstat(f.fileno()).st_size, 1
    f = Parts(parts)
    return f, f.size, len(parts)


def find_volume(f):
    """(offset, format name) of the XDVDFS volume in an open image, or None."""
    for off, name in VOLUME_OFFSETS:
        f.seek(off + 32 * SECTOR)
        if f.read(len(xc.MEDIA_MAGIC)) == xc.MEDIA_MAGIC:
            return off, name
    return None


def is_iso(path):
    """True when the file holds an XDVDFS volume at a disc offset (cheap: a few small reads)."""
    try:
        f = _open(path)[0]
    except (OSError, IsoError):
        return False
    try:
        return find_volume(f) is not None
    finally:
        f.close()


class Iso(xc.Xdvdfs):
    BLOCK = SECTOR

    def __init__(self, path, whole=False):
        if not whole and len(split_parts(path)) > 1:
            # named like a part: a whole image by itself (two discs named game.1.iso and
            # game.2.iso) is read alone, else the parts are joined
            try:
                alone = Iso(path, whole=True)
                alone.entries()
                self.__dict__.update(alone.__dict__)
                return
            except IsoError:
                if "alone" in locals():
                    alone.close()
        try:
            f, self.size, self.parts = _open(path, whole)
        except OSError as e:
            raise IsoError(f"Cannot read {path}: {e}")
        self.files = [f]
        found = find_volume(f)
        if not found:
            f.close()
            raise IsoError("Not an Xbox 360 disc image: no game partition found at the "
                           "XGD1, XGD2 or XGD3 offset.")
        self.offset, self.format = found
        f.seek(self.offset + 32 * SECTOR + len(xc.MEDIA_MAGIC))
        self.root_block, self.root_size = struct.unpack("<II", f.read(8))

    def close(self):
        for f in self.files:
            f.close()

    def read_block(self, block, n=SECTOR):
        f = self.files[0]
        f.seek(self.offset + block * SECTOR)
        data = f.read(n)
        if len(data) < n:
            raise IsoError("The image is truncated (a directory lies past its end).")
        return data

    def runs_for(self, block, length):
        return [[0, self.offset + block * SECTOR, length]] if length else []

    def entries(self):
        out = super().entries()
        for e in out:
            if not e.is_dir and e.runs and e.runs[0][1] + e.size > self.size:
                more = (f" (it has {self.parts} parts: is one missing or incomplete?)" if self.parts > 1
                        else " (is it split into parts, or still downloading?)")
                raise IsoError(f"The image is truncated: {e.path} runs past its end{more}.")
        return out


def open_iso(path):
    return Iso(str(path))


def extract(iso, outdir, progress=None, cancelled=lambda: False, wanted=None):
    """Extract every file (or those under the `wanted` paths) to outdir. progress(done, total,
    name) is called before each file; cancelled() is polled between files. Returns the bytes
    written."""
    entries = iso.entries()
    if wanted:
        wanted = [p.replace("\\", "/").strip("/").lower() for p in wanted]
        entries = [e for e in entries if any(e.path.lower() == w or e.path.lower().startswith(w + "/")
                                             for w in wanted)]
    total = sum(e.size for e in entries if not e.is_dir)
    done = 0
    for e in entries:
        if cancelled():
            raise InterruptedError("cancelled")
        if progress:
            progress(done, total, e.path)
        xc.extract(iso, e, outdir)
        done += e.size
    if progress:
        progress(total, total, "")
    return total


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    path, cmd = argv[1], argv[2]
    try:
        iso = open_iso(path)
        if cmd == "info":
            print(f"format={iso.format} volume_offset=0x{iso.offset:X} root_sector={iso.root_block} "
                  f"root_size={iso.root_size} image_size={iso.size}")
            return
        if cmd == "list":
            for e in iso.entries():
                print(f"{'d' if e.is_dir else '-'} {e.size:12d} {e.path}")
            return
        if cmd == "extract" and len(argv) > 3:
            total = extract(iso, argv[3], wanted=argv[4:])
            print(f"extracted {total} bytes to {argv[3]}")
            return
    except IsoError as e:
        raise SystemExit(str(e))
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
