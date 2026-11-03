#!/usr/bin/env python3
"""Read-only Xbox 360 XContent package lister/extractor (standard library only).

Handles the two container layouts Forza Horizon ships in:
  - SVOD: Games on Demand (content type 0x7000), a header file plus a `<name>.data/`
    folder of DataNNNN fragments holding an XDVDFS image interleaved with hash blocks;
  - STFS: single-file CON/LIVE/PIRS packages (DLC, title updates, saves).

Ported from the ReXGlue SDK's StfsContainerDevice (itself from Xenia). The source
package is only ever opened for reading.

Usage:
  xcontent_extract.py PACKAGE info
  xcontent_extract.py PACKAGE list
  xcontent_extract.py PACKAGE extract OUTDIR [PATH ...]   (no PATH = everything)
"""

# Derived from the ReXGlue SDK's StfsContainerDevice, which comes from Xenia:
# Copyright (c) 2026, Tom Clay; Copyright (c) 2022, Ben Vanik and Xenia project contributors.
# Those parts are under the BSD 3-Clause License, whose notice follows; the rest of this file
# is under this repository's licence (see LICENSE and NOTICE.md).
#
# Redistribution and use in source and binary forms, with or without modification, are
# permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this list of
#   conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice, this list of
#   conditions and the following disclaimer in the documentation and/or other materials
#   provided with the distribution.
# * Neither the name of the copyright holder nor the names of its contributors may be used to
#   endorse or promote products derived from this software without specific prior written
#   permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
# GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.
import hashlib
import os
import struct
import sys

MEDIA_MAGIC = b"MICROSOFT*XBOX*MEDIA"


def u24le(b, off):
    return b[off] | (b[off + 1] << 8) | (b[off + 2] << 16)


class Header:
    def __init__(self, path):
        with open(path, "rb") as f:
            raw = f.read(0x971A)
        self.magic = raw[0:4]
        if self.magic not in (b"CON ", b"LIVE", b"PIRS"):
            raise SystemExit(f"{path}: not an XContent package (magic {self.magic!r})")
        (self.header_size, self.content_type, self.metadata_version) = struct.unpack_from(">III", raw, 0x340)
        (self.content_size,) = struct.unpack_from(">Q", raw, 0x34C)
        (self.media_id, self.version, self.base_version, self.title_id) = struct.unpack_from(">IIII", raw, 0x354)
        self.descriptor = raw[0x379:0x379 + 0x24]
        (self.data_file_count,) = struct.unpack_from(">I", raw, 0x39D)
        (self.data_file_size,) = struct.unpack_from(">Q", raw, 0x3A1)
        (self.volume_type,) = struct.unpack_from(">I", raw, 0x3A9)
        self.display_name = raw[0x411:0x411 + 0x100].decode("utf-16-be", "replace").split("\0")[0]
        self.title_name = raw[0x1691:0x1691 + 0x80].decode("utf-16-be", "replace").split("\0")[0]


class Entry:
    __slots__ = ("path", "is_dir", "size", "runs", "blocks")

    def __init__(self, path, is_dir, size, runs, blocks=None):
        self.path, self.is_dir, self.size, self.runs = path, is_dir, size, runs
        # STFS only: the data block numbers of the file, in order (None where there is no hash table)
        self.blocks = blocks


class Xdvdfs:
    """The XDVDFS directory tree. A subclass provides BLOCK, read_block(block, n) and
    runs_for(block, length) -> [[file index, byte offset, byte count], ...]; `root_block` and
    `root_size` come from the volume descriptor ("MICROSOFT*XBOX*MEDIA" + <II>)."""

    BLOCK = 0x800

    def entries(self):
        out = []
        self._walk_dir(self.root_block, self.root_size, "", out)
        return out

    def _walk_dir(self, block, size, prefix, out):
        # Directory entries are addressed by 4-byte ordinals; one directory can span
        # several sectors, each mapped on its own (hash blocks may sit in between).
        nsectors = max(1, (size + self.BLOCK - 1) // self.BLOCK)
        data = b"".join(self.read_block(block + i) for i in range(nsectors))
        stack, seen = [0], set()
        items = []
        while stack:
            ordinal = stack.pop()
            off = ordinal * 4
            if off in seen or off + 14 > len(data):
                continue
            seen.add(off)
            left, right, start, length, attr, nlen = struct.unpack_from("<HHIIBB", data, off)
            if left == 0xFFFF and right == 0xFFFF:
                continue
            name = data[off + 14:off + 14 + nlen].decode("latin-1")
            if left:
                stack.append(left)
            if right:
                stack.append(right)
            items.append((name, start, length, attr))
        for name, start, length, attr in sorted(items, key=lambda x: x[0].lower()):
            path = prefix + name
            if attr & 0x10:
                out.append(Entry(path, True, 0, []))
                if length:
                    self._walk_dir(start, length, path + "/", out)
            else:
                out.append(Entry(path, False, length, self.runs_for(start, length)))


class Svod(Xdvdfs):
    """GOD/SVOD: XDVDFS sectors (0x800) spread over DataNNNN fragments with hash tables."""

    BLOCK = 0x800
    HASH_BLOCK = 0x1000
    BLOCKS_PER_L0 = 0x198
    L0_PER_L1 = 0xA1C4
    BLOCKS_PER_FILE = 0x14388
    MAX_FILE_SIZE = 0xA290000

    def __init__(self, path, hdr):
        self.hdr = hdr
        data_dir = path + ".data"
        names = sorted(os.listdir(data_dir))
        if len(names) != hdr.data_file_count:
            raise SystemExit(f"expected {hdr.data_file_count} data fragments, found {len(names)}")
        self.files = [open(os.path.join(data_dir, n), "rb") for n in names]
        d = hdr.descriptor
        self.enhanced_gdf = bool(d[0x18] & 0x40)
        self.start_block = u24le(d, 0x1C)
        f0 = self.files[0]
        self.single_file = False
        if self.enhanced_gdf:
            magic_off = 0x2000
        else:
            f0.seek(0x12000)
            if f0.read(len(MEDIA_MAGIC)) == MEDIA_MAGIC:
                magic_off = 0x12000
            else:
                f0.seek(0xD000)
                if f0.read(len(MEDIA_MAGIC)) != MEDIA_MAGIC:
                    raise SystemExit("SVOD magic block not found")
                magic_off = 0xD000
                self.single_file = True
        f0.seek(magic_off)
        if f0.read(len(MEDIA_MAGIC)) != MEDIA_MAGIC:
            raise SystemExit(f"SVOD magic missing at 0x{magic_off:X}")
        self.root_block, self.root_size = struct.unpack("<II", f0.read(8))

    def block_to_offset(self, block):
        true_block = block - self.start_block * 2
        if self.enhanced_gdf:
            true_block += 2  # EGDF starts 0x1000 bytes earlier
        file_block = true_block % self.BLOCKS_PER_FILE
        file_index = true_block // self.BLOCKS_PER_FILE
        l0 = file_block // self.BLOCKS_PER_L0 + 1
        l1 = l0 // self.L0_PER_L1 + 1
        offset = (l0 + l1) * self.HASH_BLOCK
        if self.single_file:
            offset += 0xB000
        addr = file_block * self.BLOCK + offset
        if addr >= self.MAX_FILE_SIZE:
            file_index += 1
            addr = addr % self.MAX_FILE_SIZE + 0x2000
        return file_index, addr

    def read_block(self, block, n=BLOCK):
        fi, addr = self.block_to_offset(block)
        f = self.files[fi]
        f.seek(addr)
        return f.read(n)

    def runs_for(self, block, length):
        runs = []
        remaining = (length + self.BLOCK - 1) // self.BLOCK
        while remaining:
            fi, addr = self.block_to_offset(block)
            if runs and runs[-1][0] == fi and runs[-1][1] + runs[-1][2] == addr:
                runs[-1][2] += self.BLOCK
            else:
                runs.append([fi, addr, self.BLOCK])
            block += 1
            remaining -= 1
        return runs


class Stfs:
    """Single-file STFS package (CON/LIVE/PIRS)."""

    BLOCK = 0x1000
    PER_LEVEL = (170, 28900, 4913000)
    END_OF_CHAIN = 0xFFFFFF

    def __init__(self, path, hdr):
        self.hdr = hdr
        self.files = [open(path, "rb")]
        d = hdr.descriptor
        self.flags = d[2]
        self.read_only = bool(self.flags & 1)
        self.root_active = bool(self.flags & 2)
        self.table_count = struct.unpack_from("<H", d, 3)[0]
        self.table_block = u24le(d, 5)
        self.total_blocks, self.free_blocks = struct.unpack_from(">II", d, 0x1C)
        self.per_table = 1 if self.read_only else 2
        self.step0 = self.PER_LEVEL[0] + self.per_table
        self.step1 = self.PER_LEVEL[1] + (self.PER_LEVEL[0] + 1) * self.per_table
        self.base = (hdr.header_size + self.BLOCK - 1) // self.BLOCK * self.BLOCK
        self.tables = {}

    def block_to_offset(self, index):
        base, block = self.PER_LEVEL[0], index
        for _ in range(3):
            block += ((index + base) // base) * self.per_table
            if index < base:
                break
            base *= self.PER_LEVEL[0]
        return self.base + (block << 12)

    def hash_block_number(self, index, level):
        if level == 0:
            if index < self.PER_LEVEL[0]:
                return 0
            block = (index // self.PER_LEVEL[0]) * self.step0
            block += ((index // self.PER_LEVEL[1]) + 1) * self.per_table
            if index < self.PER_LEVEL[1]:
                return block
            return block + self.per_table
        if level == 1:
            if index < self.PER_LEVEL[1]:
                return self.step0
            return (index // self.PER_LEVEL[1]) * self.step1 + self.per_table
        return self.step1

    def _table(self, offset):
        if offset not in self.tables:
            f = self.files[0]
            f.seek(offset)
            self.tables[offset] = f.read(0x1000)
        return self.tables[offset]

    def block_hash_info(self, index):
        return struct.unpack_from(">I", self.block_record(index), 0x14)[0]

    def block_sha1(self, index):
        """The SHA-1 the hash table holds for data block `index`."""
        return self.block_record(index)[:20]

    def block_record(self, index):
        secondary = self.BLOCK if self.root_active else 0
        off0 = self.base + (self.hash_block_number(index, 0) << 12)
        if self.read_only:
            secondary = 0
        else:
            if self.total_blocks > self.PER_LEVEL[0]:
                off1 = self.base + (self.hash_block_number(index, 1) << 12)
                if self.total_blocks > self.PER_LEVEL[1]:
                    off2 = self.base + (self.hash_block_number(index, 2) << 12)
                    t2 = self._table(off2 + secondary)
                    rec = (index // self.PER_LEVEL[1]) % self.PER_LEVEL[0]
                    info = struct.unpack_from(">I", t2, rec * 0x18 + 0x14)[0]
                    secondary = self.BLOCK if info & 0x40000000 else 0
                t1 = self._table(off1 + secondary)
                rec = (index // self.PER_LEVEL[0]) % self.PER_LEVEL[0]
                info = struct.unpack_from(">I", t1, rec * 0x18 + 0x14)[0]
                secondary = self.BLOCK if info & 0x40000000 else 0
        t0 = self._table(off0 + secondary)
        rec = index % self.PER_LEVEL[0]
        return t0[rec * 0x18:rec * 0x18 + 0x18]

    def entries(self):
        f = self.files[0]
        raw = []
        block = self.table_block
        for _ in range(self.table_count):
            f.seek(self.block_to_offset(block))
            data = f.read(self.BLOCK)
            for i in range(0x40):
                e = data[i * 0x40:(i + 1) * 0x40]
                if e[0] == 0:
                    break
                raw.append(e)
            block = self.block_hash_info(block) & 0xFFFFFF
            if block == self.END_OF_CHAIN:
                break
        paths, out = [], []
        for e in raw:
            nflags = e[0x28]
            name = e[:nflags & 0x3F].decode("latin-1")
            is_dir = bool(nflags & 0x80)
            start = u24le(e, 0x2F)
            (dir_index,) = struct.unpack_from(">H", e, 0x32)
            (length,) = struct.unpack_from(">I", e, 0x34)
            parent = "" if dir_index == 0xFFFF else paths[dir_index] + "/"
            path = parent + name
            paths.append(path)
            if is_dir:
                out.append(Entry(path, True, 0, []))
                continue
            runs, remaining, b, blocks = [], length, start, []
            while remaining and b != self.END_OF_CHAIN:
                n = min(self.BLOCK, remaining)
                addr = self.block_to_offset(b)
                if runs and runs[-1][1] + runs[-1][2] == addr:
                    runs[-1][2] += n
                else:
                    runs.append([0, addr, n])
                remaining -= n
                blocks.append(b)
                b = self.block_hash_info(b) & 0xFFFFFF
            if remaining:
                raise SystemExit(f"{path}: block chain ends {remaining} bytes early")
            out.append(Entry(path, False, length, runs, blocks))
        return out


def open_package(path):
    hdr = Header(path)
    if hdr.volume_type == 1:
        return Svod(path, hdr)
    return Stfs(path, hdr)


def extraction_path(outdir, path):
    """Keep package-controlled paths under the extraction root, including existing links."""
    parts = path.split("/")
    # Packages use forward-slash relative paths. Reject Windows drive/stream syntax
    # on every host too, so a package cannot extract differently after being copied.
    if any(not p or p in (".", "..") or any(c in p for c in "\\:\0") for p in parts):
        raise SystemExit(f"unsafe package path: {path!r}")
    root = os.path.realpath(outdir)
    dest = os.path.realpath(os.path.join(root, *parts))
    try:
        inside = os.path.normcase(os.path.commonpath((root, dest))) == os.path.normcase(root)
    except ValueError:  # a Windows directory link can resolve onto another drive
        inside = False
    if not inside:
        raise SystemExit(f"unsafe package path: {path!r}")
    return dest


def extract(pkg, entry, outdir):
    """Extract one entry. Returns the list of file block numbers (4 KB each, counted from the start
    of the file) whose data does not match the package's own SHA-1 hash table: a damaged package
    (a bad download or archive copy) extracts with those blocks wrong, which no reader can repair."""
    dest = extraction_path(outdir, entry.path)
    if entry.is_dir:
        os.makedirs(dest, exist_ok=True)
        return []
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    remaining = entry.size
    bad, fb = [], 0
    check = entry.blocks is not None and hasattr(pkg, "block_sha1")
    with open(dest, "wb") as out:
        for fi, addr, n in entry.runs:
            if not remaining:
                break
            f = pkg.files[fi]
            f.seek(addr)
            take = min(n, remaining)
            while take:
                chunk = f.read(min(take, 1 << 22))
                if not chunk:
                    raise SystemExit(f"{entry.path}: short read")
                out.write(chunk)
                if check:
                    # runs start on a block boundary and chunks are whole blocks (but the last)
                    for i in range(0, len(chunk), pkg.BLOCK):
                        blk = chunk[i:i + pkg.BLOCK]
                        if len(blk) < pkg.BLOCK:
                            # STFS hashes the full stored block, including padding beyond
                            # the file's declared end. Read that padding for verification
                            # only; it must not be appended to the extracted file.
                            blk += f.read(pkg.BLOCK - len(blk))
                            if len(blk) != pkg.BLOCK:
                                raise SystemExit(f"{entry.path}: short read")
                        if hashlib.sha1(blk).digest() != pkg.block_sha1(entry.blocks[fb]):
                            bad.append(fb)
                        fb += 1
                take -= len(chunk)
                remaining -= len(chunk)
    return bad


def main(argv):
    if len(argv) < 3:
        raise SystemExit(__doc__)
    path, cmd = argv[1], argv[2]
    pkg = open_package(path)
    h = pkg.hdr
    if cmd == "info":
        kind = "SVOD" if h.volume_type == 1 else "STFS"
        print(f"magic={h.magic.decode()} content_type=0x{h.content_type:X} volume={kind} "
              f"title_id={h.title_id:08X} media_id={h.media_id:08X} version=0x{h.version:X} "
              f"base_version=0x{h.base_version:X} fragments={h.data_file_count}")
        print(f"display_name={h.display_name!r} title_name={h.title_name!r}")
        return
    entries = pkg.entries()
    if cmd == "list":
        for e in entries:
            print(f"{'d' if e.is_dir else '-'} {e.size:12d} {e.path}")
        return
    if cmd == "extract":
        outdir = argv[3]
        wanted = [p.replace("\\", "/").strip("/").lower() for p in argv[4:]]
        total = 0
        for e in entries:
            low = e.path.lower()
            if wanted and not any(low == w or low.startswith(w + "/") for w in wanted):
                continue
            bad = extract(pkg, e, outdir)
            if bad:
                print(f"WARNING: {e.path}: {len(bad)} block(s) do not match the package's hashes "
                      f"(first {bad[0]}): the package is damaged", file=sys.stderr)
            total += e.size
        print(f"extracted {total} bytes to {outdir}")
        return
    raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
