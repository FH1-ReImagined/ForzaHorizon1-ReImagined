#!/usr/bin/env python3
"""Find the Xenos shader microcode in the game's own files and key it like the SDK.

Phase 1 of the shader prebuild (#226, docs/shader_prebuild.md). The game ships its
compiled shaders as Xbox 360 D3D shader blobs inside:
  - media/shaders/**/*.fxobj (cars, driver hands, effects),
  - shaders/track/*.fxobj inside media/tracks/*/bin.zip (LZX, zip method 21),
  - the title update's media.zip (newer car .fxobj files),
  - the executable image (engine shaders; tools/xex_image.py writes it).
Every blob starts with 0x102A1101 (vertex) or 0x102A1100 (pixel); its microcode is
found through the blob's own header, so the effect format around it need not be parsed.
The SDK keys a shader by XXH3-64 of the big-endian microcode words (tools/xxh3.py).

Pixel shaders run as stored in the files. Vertex shaders do not: when the game draws,
the console's D3D runtime binds a vertex shader to the vertex layout and the pixel
shader, writing the layout into its vertex fetch instructions, renumbering exports to
the pixel shader's inputs and turning unread exports into no-ops. derive_binding()
finds those fields; apply_binding() redoes them from the blob (see pipeline_list.py).

Nothing here writes game data anywhere: the output is hashes, sizes and file names.

Usage:
  fxobj_shaders.py scan SOURCE...                     summary per source
  fxobj_shaders.py list SOURCE... [--json OUT]        hashes, types, sizes, origins (no microcode)
  fxobj_shaders.py coverage --xsh STORED.xsh... SOURCE...
                                                      share of stored (played) shaders found
SOURCE is a directory (searched for .fxobj and .zip), a .zip, an .fxobj, or a
decrypted executable image (.bin/.elf).
"""
import argparse
import json
import os
import struct
import sys
import zipfile
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xxh3 import xxh3_64  # noqa: E402

MAGIC_VS = 0x102A1101
MAGIC_PS = 0x102A1100
TYPE_VS, TYPE_PS = 0, 1  # xenos::ShaderType
TYPE_NAMES = {TYPE_VS: "vs", TYPE_PS: "ps"}
LZX_WINDOW = 1 << 17


# Vertex fetch instruction bits that the D3D runtime fills from the vertex declaration
# (Xenia ucode.h VertexFetchInstruction): word 0 const_index, const_index_sel,
# prefetch_count; word 1 dst_swiz, format_comp_all, num_format_all, signed_rf_mode_all,
# data format, exp_adjust, is_mini_fetch; word 2 stride, offset.
FETCH_LAYOUT_MASK = (0x3FF00000, 0x7F3F7FFF, 0x7FFFFFFF)
# ALU export (word 0 bit 15) fields the runtime changes to link the vertex shader's
# outputs to the pixel shader's inputs: vector_dest, scalar_dest, vector_clamp.
EXPORT_MASK = 0x01003F3F
# What the runtime writes over an export the pixel shader does not read: an ALU
# instruction with no writes (scalar RETAIN_PREV, vector MAX, all masks 0).
ALU_NOP = (0xC8000000, 0x00000000, 0x02000000)


class Shader:
    __slots__ = ("type", "ucode", "hash", "origin", "offset")

    def __init__(self, type_, ucode, origin, offset):
        self.type, self.ucode, self.origin, self.offset = type_, ucode, origin, offset
        self.hash = xxh3_64(ucode)


def find_blobs(data, origin):
    """Yield every valid D3D shader blob in `data` (scan for the magic, check the header).

    Blob header (0x24 bytes), big-endian u32s: magic, virtual size (header, debug info,
    constant table, definitions, shader description), physical size (literal constants
    and microcode), debug info offset (0x24, or 0 without debug info), constant table
    offset, definition table offset, shader description offset, 0, 0.
    The shader description starts with u32 physical offset and u32 byte size of the
    microcode, both relative to the physical part, which follows the virtual part.
    """
    view = memoryview(data)
    pos = 0
    while True:
        pos = data.find(b"\x10\x2a\x11", pos)
        if pos < 0 or pos + 0x24 > len(data):
            return
        start, pos = pos, pos + 3
        magic, vsize, psize, dbg, _ct, _dt, desc = struct.unpack_from(">7I", data, start)
        if magic not in (MAGIC_VS, MAGIC_PS) or dbg not in (0, 0x24):
            continue
        if not (0x24 <= desc and desc + 8 <= vsize and start + vsize + psize <= len(data)):
            continue
        poff, size = struct.unpack_from(">2I", data, start + desc)
        if size == 0 or size % 4 or poff % 4 or poff + size > psize:
            continue
        ucode_at = start + vsize + poff
        yield Shader(TYPE_VS if magic == MAGIC_VS else TYPE_PS,
                     bytes(view[ucode_at:ucode_at + size]), origin, start)
        pos = start + vsize + psize


def read_zip_member(zf, path, info):
    """Bytes of a zip member; method 21 (XMemCompress LZX) via tools/lzx_delta.py."""
    if info.compress_type not in (zipfile.ZIP_STORED, 21):
        return zf.read(info)
    # The track archives (media/tracks/*/bin.zip) have no local headers: the central
    # directory's offset points straight at the member's data.
    with open(path, "rb") as f:
        f.seek(info.header_offset)
        h = f.read(30)
        start = info.header_offset
        if h[:4] == b"PK\x03\x04":
            n, m = struct.unpack("<HH", h[26:30])
            start += 30 + n + m
        f.seek(start)
        raw = f.read(info.compress_size)
    if info.compress_type == zipfile.ZIP_STORED:
        return raw
    return decode_xmem(raw, info.file_size)


def decode_xmem(raw, size):
    """A method-21 (XMemCompress LZX) member's bytes from its raw data, in Python (what
    tools/unxmem does with libmspack)."""
    import lzx_delta
    # Blocks: FF, u16 uncompressed size, u16 compressed size, data; or u16 compressed
    # size, data (a full 32 KB block). The data of all blocks is one LZX stream.
    stream, pos = bytearray(), 0
    while pos < len(raw):
        if raw[pos] == 0xFF:
            comp = (raw[pos + 3] << 8) | raw[pos + 4]
            pos += 5
        else:
            comp = (raw[pos] << 8) | raw[pos + 1]
            pos += 2
        if comp == 0 or pos + comp > len(raw):
            break
        stream += raw[pos:pos + comp]
        pos += comp
    return bytes(lzx_delta.decompress(bytes(stream), size, LZX_WINDOW))


def iter_sources(sources):
    """Yield (origin, bytes) for every shader-carrying file under the given sources. A source
    may also be an (origin, bytes) pair: an executable image read from an XEX in memory."""
    for src in sources:
        if isinstance(src, tuple):
            yield src
        elif os.path.isdir(src):
            for root, dirs, files in os.walk(src):
                dirs.sort()
                for name in sorted(files):
                    p = os.path.join(root, name)
                    if name.lower().endswith((".fxobj", ".zip")):
                        yield from iter_sources([p])
        elif src.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(src)
            except zipfile.BadZipFile:
                continue
            for info in zf.infolist():
                if info.filename.lower().endswith(".fxobj"):
                    yield f"{src}!{info.filename}", read_zip_member(zf, src, info)
        else:
            with open(src, "rb") as f:
                yield src, f.read()


def collect(sources):
    shaders = {}
    origins = defaultdict(list)
    for origin, data in iter_sources(sources):
        for s in find_blobs(data, origin):
            shaders.setdefault(s.hash, s)
            origins[s.hash].append(origin)
    return shaders, origins


def _words(u):
    return list(struct.unpack(f">{len(u) // 4}I", u))


def vertex_fetch_slots(w):
    """Instruction indices of vertex fetches, from the exec control flow instructions."""
    slots, cf_end, i = set(), None, 0
    while 3 * i + 2 < len(w) and (cf_end is None or i < cf_end):
        a, b, c = w[3 * i:3 * i + 3]
        for ins in (a | ((b & 0xFFFF) << 32), (b >> 16) | (c << 16)):
            op = (ins >> 44) & 0xF
            if op in (1, 2, 3, 4, 5, 6, 13, 14):  # exec variants
                addr, count, seq = ins & 0xFFF, (ins >> 12) & 7, (ins >> 16) & 0xFFF
                cf_end = addr if cf_end is None else min(cf_end, addr)
                for k in range(count):
                    idx = addr + k
                    if (seq >> (2 * k)) & 1 and 3 * idx < len(w) and (w[3 * idx] & 0x1F) == 0:
                        slots.add(idx)
            if op in (2, 4, 6, 14):  # exec end
                return slots
        i += 1
    return slots


def patch_size(patch):
    return sum(len(v) for v in patch.values())


def derive_binding(base, bound):
    """The bound fields that turn blob `base` into `bound`, or None if anything else differs."""
    if len(base) != len(bound):
        return None
    bw, uw = _words(base), _words(bound)
    slots = vertex_fetch_slots(bw)
    fetch, export, nop = set(), set(), set()
    for i, (x, y) in enumerate(zip(bw, uw)):
        if x == y:
            continue
        k, j = divmod(i, 3)
        if k in slots and not (x ^ y) & ~FETCH_LAYOUT_MASK[j]:
            fetch.add(k)
        elif k not in slots and tuple(uw[3 * k:3 * k + 3]) == ALU_NOP:
            nop.add(k)
        elif k not in slots and j == 0 and (x >> 15) & 1 and not (x ^ y) & ~EXPORT_MASK:
            export.add(k)
        else:
            return None
    export -= nop
    out = {}
    if fetch:
        out["fetch"] = [[k] + [uw[3 * k + j] & FETCH_LAYOUT_MASK[j] for j in range(3)]
                        for k in sorted(fetch)]
    if export:
        out["export"] = [[k, uw[3 * k] & EXPORT_MASK] for k in sorted(export)]
    if nop:
        out["nop"] = sorted(nop)
    return out


def apply_binding(base, recipe):
    """Rebuild a bound vertex shader from its blob and the list's bound fields."""
    w = _words(base)
    for k, *vals in recipe.get("fetch", ()):
        for j in range(3):
            w[3 * k + j] = (w[3 * k + j] & ~FETCH_LAYOUT_MASK[j] & 0xFFFFFFFF) | vals[j]
    for k, v in recipe.get("export", ()):
        w[3 * k] = (w[3 * k] & ~EXPORT_MASK & 0xFFFFFFFF) | v
    for k in recipe.get("nop", ()):
        w[3 * k:3 * k + 3] = ALU_NOP
    return struct.pack(f">{len(w)}I", *w)


def read_xsh(path):
    """Shaders of an SDK shader storage file (<title>.xsh): {hash: (type, ucode)}."""
    with open(path, "rb") as f:
        d = f.read()
    out = {}
    if d[:4] != b"XESH":
        return out
    o = 8
    while o + 12 <= len(d):
        h, w = struct.unpack_from("<QI", d, o)
        n, t = w & 0x7FFFFFFF, w >> 31
        o += 12
        u = d[o:o + 4 * n]
        if len(u) < 4 * n:
            break
        o += 4 * n
        out[h] = (t, u)
    return out


def coverage(stored, shaders):
    """Classify stored (played) shaders by how they can be rebuilt from the sources."""
    by_size = defaultdict(list)
    for s in shaders.values():
        by_size[s.type, len(s.ucode)].append(s)
    rows = Counter()
    kinds = Counter()
    for h, (t, u) in stored.items():
        kind = TYPE_NAMES[t]
        if h in shaders:
            rows[kind, "in the game files as is"] += 1
            continue
        best = None
        if t == TYPE_VS:
            for base in by_size.get((t, len(u)), ()):
                patch = derive_binding(base.ucode, u)
                if patch is not None and (best is None or patch_size(patch) < patch_size(best)):
                    best = patch
        if best is not None:
            rows[kind, "bound from a blob in the game files"] += 1
            kinds["+".join(sorted(best)) or "none"] += 1
        else:
            rows[kind, "not found"] += 1
    return rows, kinds


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("sources", nargs="+")
    p = sub.add_parser("list"); p.add_argument("sources", nargs="+"); p.add_argument("--json")
    p = sub.add_parser("coverage"); p.add_argument("--xsh", action="append", required=True)
    p.add_argument("sources", nargs="+")
    a = ap.parse_args()

    if a.cmd == "scan":
        per = Counter()
        for origin, data in iter_sources(a.sources):
            for s in find_blobs(data, origin):
                per[origin.split("!")[0], TYPE_NAMES[s.type]] += 1
        files = sorted({k[0] for k in per})
        for f in files:
            print(f"{per[f, 'vs']:5d} vs {per[f, 'ps']:5d} ps  {f}")
        shaders, _ = collect(a.sources)
        c = Counter(TYPE_NAMES[s.type] for s in shaders.values())
        print(f"unique: {c['vs']} vs, {c['ps']} ps, "
              f"{sum(len(s.ucode) for s in shaders.values())} microcode bytes")
    elif a.cmd == "list":
        shaders, origins = collect(a.sources)
        rows = [{"hash": f"{h:016X}", "type": TYPE_NAMES[s.type], "dwords": len(s.ucode) // 4,
                 "origins": sorted(set(origins[h]))} for h, s in sorted(shaders.items())]
        if a.json:
            with open(a.json, "w") as f:
                json.dump(rows, f, indent=1)
        else:
            for r in rows:
                print(r["hash"], r["type"], r["dwords"], r["origins"][0])
    elif a.cmd == "coverage":
        stored = {}
        for p in a.xsh:
            stored.update(read_xsh(p))
        shaders, _ = collect(a.sources)
        rows, kinds = coverage(stored, shaders)
        for kind in ("vs", "ps"):
            total = sum(n for (k, _), n in rows.items() if k == kind)
            print(f"{kind}: {total} stored")
            for (k, what), n in sorted(rows.items()):
                if k == kind:
                    print(f"  {n:5d} {100 * n / max(total, 1):5.1f}%  {what}")
        total = len(stored)
        found = sum(n for (_, w), n in rows.items() if w != "not found")
        print(f"all: {found} of {total} stored shaders rebuildable "
              f"({100 * found / max(total, 1):.1f}%)")
        print("bound vertex shaders by changed fields:", dict(sorted(kinds.items())))


if __name__ == "__main__":
    main()
