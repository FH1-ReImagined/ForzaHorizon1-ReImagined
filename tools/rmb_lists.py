#!/usr/bin/env python3
"""The command lists a track model carries (docs/native-renderer.md, "M4: the model lists").

A track's bin.zip holds its models as .rmb.bin members. After a model's geometry and the names
of its shaders (the last ends the part before the lists), each of 21 pass groups (pass x 3 +
layer) may hold one precompiled D3D command buffer:

  8 words     1, 0, 1, the sizes of areas A, B and C, a word, 8 x the groups' records
  21 groups   2, n, n records of two words, m, m entries of two words, then the group's three
              sizes: its part of areas A, B and C (all 0 for a group without a list)
  area A      per list the command buffer's header (0xC8 bytes) and its patch table, zeros, and
              the marker 11223344 55667789
  area B      per list its PM4, each at the next 32-byte boundary from the area's start, ending
              with the trailer 00000000 C0000000 (two words that are not run)
  area C      per list the buffer fixups, two words each

A record is the game's handle on a fixup: (slot << 4) | type, then where its places start
((kind << 24) | index; bit 30: in the patch table, else in area C). An entry is the loader's, for
the vertex and index buffers. The converter (forza-recomp/src/platform/native_list.h) needs
neither: tools/native_list_check.py uses them to check its reading of the tables.

This module only reads. tools/fm4_pvs.py writes models of this layout for FM4 circuits.
"""
import collections
import struct

GROUPS = 21
HEADER_BYTES = 0xC8
MARKER = bytes.fromhex("1122334455667789")
TRAILER = bytes.fromhex("00000000C0000000")

# One list. records and entries are the group's, as the file's bytes (two big-endian words
# each); header is the command buffer's first 0xC8 bytes, table its patch table, pm4 the list
# without its trailer. marker and trailer say whether those were where they belong.
ModelList = collections.namedtuple(
    "ModelList", "group records entries header table area_c pm4 marker trailer")
# head: the 8 words before the groups; lists: the populated groups' lists, in group order.
Model = collections.namedtuple("Model", "head lists")


def _groups(data, at):
    """The 21 pass groups from offset at: [(records, entries, sizes)] and the end, or None."""
    out = []
    for _ in range(GROUPS):
        if at + 8 > len(data):
            return None
        two, n = struct.unpack_from(">II", data, at)
        if two != 2 or n > 4096:
            return None
        entries_at = at + 8 + 8 * n
        if entries_at + 4 > len(data):
            return None
        m = struct.unpack_from(">I", data, entries_at)[0]
        end = entries_at + 4 + 8 * m + 12
        if m > 4096 or end > len(data):
            return None
        out.append((data[at + 8:entries_at], data[entries_at + 4:end - 12],
                    struct.unpack_from(">3I", data, end - 12)))
        at = end
    return out, at


def _parse_at(data, at):
    if at + 32 > len(data):
        return None
    head = struct.unpack_from(">8I", data, at)
    if head[0] != 1:
        return None
    groups = _groups(data, at + 32)
    if groups is None:
        return None
    groups, area_a = groups
    area_b = area_a + head[3]
    area_c = area_b + head[4]
    if area_c + head[5] > len(data):
        return None
    lists = []
    a, b, c = area_a, 0, area_c
    for group, (records, entries, sizes) in enumerate(groups):
        if not any(sizes):
            continue
        b = (b + 31) & ~31
        block = data[a:a + sizes[0]]
        buffer = data[area_b + b:area_b + b + sizes[1]]
        words = struct.unpack_from(">I", block, 0x7C)[0] if len(block) >= HEADER_BYTES else 0
        lists.append(ModelList(
            group, records, entries, block[:HEADER_BYTES],
            block[HEADER_BYTES:HEADER_BYTES + 4 * words], data[c:c + sizes[2]],
            buffer[:max(len(buffer) - 8, 0)], block[-8:] == MARKER, buffer[-8:] == TRAILER))
        a += sizes[0]
        b += sizes[1]
        c += sizes[2]
    # The three areas must be exactly their lists.
    if a != area_b or b != head[4] or c != area_c + head[5]:
        return None
    return Model(head, lists)


def parse(data):
    """The lists of a .rmb.bin, or None if no list table is found. The table follows the last
    shader name; a later ".fx" that is not one (bytes of a list) is passed over."""
    at = len(data)
    while True:
        at = data.rfind(b".fx", 0, at)
        if at < 0:
            return None
        model = _parse_at(data, at + 3)
        if model is not None:
            return model
