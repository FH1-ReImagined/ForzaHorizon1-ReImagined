"""LZX decoding with a preloaded reference window, as used by XEX delta patches (.xexp).

Pure Python (standard library only), so the launcher can run it anywhere. A port of the
parts of libmspack's lzxd.c that XEX patches use (the ReXGlue SDK's lzx.cpp calls it the
same way: no reset interval, 32 KB frames, not the "LZX DELTA" container format, the
reference data placed at the end of the window).

    apply_delta_records(records, window_size, dest)   # xex2_delta_patch records, in place
"""

# Derived from libmspack's lzxd.c, (C) 2003-2013 Stuart Caie, licensed under the GNU Lesser
# General Public License version 2.1. As LGPL 2.1 section 3 allows, this derived file is
# distributed under the GNU General Public License version 3 or later, like the rest of this
# repository (see LICENSE and NOTICE.md).
import struct

MIN_MATCH = 2
NUM_CHARS = 256
PRETREE_NUM = 20
LENGTH_NUM = 249
ALIGNED_NUM = 8
FRAME_SIZE = 32768
POSITION_SLOTS = {15: 30, 16: 32, 17: 34, 18: 36, 19: 38, 20: 42, 21: 50}

EXTRA_BITS = []
POSITION_BASE = []
_j = 0
for _i in range(0, 52, 2):
    EXTRA_BITS += [_j, _j]
    if _i != 0 and _j < 17:
        _j += 1
_j = 0
for _i in range(52):
    POSITION_BASE.append(_j)
    _j += 1 << EXTRA_BITS[_i]


TRACE = None  # a list to record matches (output position, window position, length)


class LzxError(Exception):
    pass


def build_table(lens):
    """Canonical Huffman decode table: (table, bits); table[peek(bits)] = (symbol, length)."""
    maxlen = max(lens) if lens else 0
    if maxlen == 0:
        return None, 0
    table = [None] * (1 << maxlen)
    code = 0
    for length in range(1, maxlen + 1):
        for sym, l in enumerate(lens):
            if l == length:
                span = 1 << (maxlen - length)
                start = code << (maxlen - length)
                if start + span > len(table):
                    raise LzxError("over-subscribed Huffman table")
                table[start:start + span] = [(sym, length)] * span
                code += 1
        code <<= 1
    return table, maxlen


class Bits:
    """libmspack's MSB-first bit buffer fed with 16-bit little-endian words."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.buf = 0
        self.left = 0

    def ensure(self, n):
        while self.left < n:
            d = self.data
            p = self.pos
            lo = d[p] if p < len(d) else 0
            hi = d[p + 1] if p + 1 < len(d) else 0
            self.pos = p + 2
            self.buf = ((self.buf << 16) | (hi << 8) | lo) & ((1 << 64) - 1)
            self.left += 16

    def read(self, n):
        if n == 0:
            return 0
        self.ensure(n)
        self.left -= n
        return (self.buf >> self.left) & ((1 << n) - 1)

    def sym(self, tb):
        table, bits = tb
        if table is None:
            raise LzxError("symbol from an empty Huffman table")
        self.ensure(16)
        entry = table[(self.buf >> (self.left - bits)) & ((1 << bits) - 1)]
        if entry is None:
            raise LzxError("invalid Huffman code")
        self.left -= entry[1]
        return entry[0]


def _read_lens(bits, lens, first, last):
    pre = build_table([bits.read(4) for _ in range(PRETREE_NUM)])
    x = first
    while x < last:
        z = bits.sym(pre)
        if z == 17:
            y = bits.read(4) + 4
            lens[x:x + y] = [0] * y
            x += y
        elif z == 18:
            y = bits.read(5) + 20
            lens[x:x + y] = [0] * y
            x += y
        elif z == 19:
            y = bits.read(1) + 4
            z = bits.sym(pre)
            z = lens[x] - z
            if z < 0:
                z += 17
            lens[x:x + y] = [z] * y
            x += y
        else:
            z = lens[x] - z
            if z < 0:
                z += 17
            lens[x] = z
            x += 1


def decompress(data, out_len, window_size, ref=b""):
    """Decode out_len bytes of an LZX stream whose window ends with `ref`."""
    window_bits = window_size.bit_length() - 1
    if window_size != 1 << window_bits or window_bits not in POSITION_SLOTS:
        raise LzxError(f"bad window size {window_size:#x}")
    num_main = NUM_CHARS + POSITION_SLOTS[window_bits] * 8
    if len(ref) > window_size:
        raise LzxError("reference data larger than the window")
    # Linear history: the initial window (zeros, then the reference at its end), then output.
    # A match offset is at most the window size, so hist[-offset] is the window's byte.
    hist = bytearray(window_size - len(ref)) + bytearray(ref)
    base = len(hist)
    bits = Bits(data)
    # Runs may overshoot the last element; libmspack's arrays have 64 spare entries.
    main_lens = [0] * (num_main + 64)
    length_lens = [0] * (LENGTH_NUM + 64)
    r0 = r1 = r2 = 1
    if bits.read(1):
        raise LzxError("Intel E8 translation is not supported")
    block_type = 0
    block_remaining = 0
    block_length = 0
    main_tb = length_tb = aligned_tb = None
    produced = 0
    while produced < out_len:
        frame = min(FRAME_SIZE, out_len - produced)
        frame_end = produced + frame
        while produced < frame_end:
            if block_remaining == 0:
                if block_type == 3 and (block_length & 1):
                    bits.pos += 1
                block_type = bits.read(3)
                hi = bits.read(16)
                block_remaining = block_length = (hi << 8) | bits.read(8)
                if block_type == 2:
                    aligned_tb = build_table([bits.read(3) for _ in range(ALIGNED_NUM)])
                if block_type in (1, 2):
                    _read_lens(bits, main_lens, 0, NUM_CHARS)
                    _read_lens(bits, main_lens, NUM_CHARS, num_main)
                    main_tb = build_table(main_lens[:num_main])
                    _read_lens(bits, length_lens, 0, LENGTH_NUM)
                    length_tb = build_table(length_lens[:LENGTH_NUM])
                elif block_type == 3:
                    if bits.left == 0:
                        bits.ensure(16)
                    bits.left = 0
                    bits.buf = 0
                    r0, r1, r2 = struct.unpack_from("<III", bits.data, bits.pos)
                    bits.pos += 12
                else:
                    raise LzxError(f"bad block type {block_type}")
            run = min(block_remaining, frame_end - produced)
            block_remaining -= run
            if block_type == 3:
                chunk = bits.data[bits.pos:bits.pos + run]
                if len(chunk) != run:
                    raise LzxError("uncompressed block past the end of the input")
                hist += chunk
                bits.pos += run
                produced += run
                continue
            target = produced + run
            aligned = block_type == 2
            while produced < target:
                main = bits.sym(main_tb)
                if main < NUM_CHARS:
                    hist.append(main)
                    produced += 1
                    continue
                main -= NUM_CHARS
                match_len = main & 7
                if match_len == 7:
                    match_len += bits.sym(length_tb)
                match_len += MIN_MATCH
                slot = main >> 3
                if slot > 2:
                    extra = EXTRA_BITS[slot] if slot < 36 else 17
                    off = POSITION_BASE[slot] - 2
                    if not aligned:
                        off += bits.read(extra)
                    elif extra > 3:
                        off += bits.read(extra - 3) << 3
                        off += bits.sym(aligned_tb)
                    elif extra == 3:
                        off += bits.sym(aligned_tb)
                    elif extra > 0:
                        off += bits.read(extra)
                    else:
                        off = 1
                    r2, r1, r0 = r1, r0, off
                elif slot == 0:
                    off = r0
                elif slot == 1:
                    off = r1
                    r1, r0 = r0, off
                else:
                    off = r2
                    r2, r0 = r0, off
                if off > window_size or off > len(hist):
                    raise LzxError("match offset outside the window")
                src = len(hist) - off
                if TRACE is not None:
                    TRACE.append((len(hist) - base, src - (window_size - len(ref)), match_len))
                if off >= match_len:
                    hist += hist[src:src + match_len]
                else:  # overlapping copy repeats the last `off` bytes
                    pat = hist[src:]
                    hist += (pat * (match_len // off + 1))[:match_len]
                produced += match_len
            if produced > target:  # the last match ran into the next block
                over = produced - target
                if over > block_remaining:
                    raise LzxError("match overruns the block")
                block_remaining -= over
        if produced != frame_end:
            raise LzxError("decoded beyond the frame")
        # Realign the input to 16 bits after each frame.
        if bits.left > 0:
            bits.ensure(16)
        if bits.left & 15:
            bits.left -= bits.left & 15
    return bytes(hist[base:base + out_len])


def apply_delta_records(records, window_size, dest, dest_off=0):
    """Apply a run of xex2_delta_patch records (old_addr, new_addr, uncompressed_len,
    compressed_len, data) to the bytearray `dest`, like the SDK's lzxdelta_apply_patch."""
    pos = 0
    end = len(records)
    while pos < end:
        if end - pos < 12:
            break
        old, new, ulen, clen = struct.unpack_from(">IIHH", records, pos)
        if old == 0 and new == 0 and ulen == 0 and clen == 0:
            break
        old += dest_off
        new += dest_off
        if clen == 0:
            dest[new:new + ulen] = bytes(ulen)
            pos += 12
        elif clen == 1:
            dest[new:new + ulen] = dest[old:old + ulen]
            pos += 12
        else:
            data = bytes(records[pos + 12:pos + 12 + clen])
            ref = bytes(dest[old:old + ulen])
            dest[new:new + ulen] = decompress(data, ulen, window_size, ref)
            pos += 12 + clen
    return pos
