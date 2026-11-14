#!/usr/bin/env python3
"""tools/rmb_lists.py and tools/native_list_check.py on a model made up here (no game files):
python3 -m unittest discover -s tools -p test_rmb_lists.py"""
import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import native_list_check  # noqa: E402
import rmb_lists  # noqa: E402

PLACEHOLDER = 0xEEEEEEE0


def words(*values):
    return struct.pack(">%dI" % len(values), *values)


def tile_block():
    """The three tiles' windows, as a list recorded for them starts."""
    block = [0xC0006100, 0]
    for tile, mask in enumerate((0x3, 0xC, 0x30)):
        block += [0xC0006000, mask, 0xC0032D01, 0x00040080, 0x7000 + tile, 0x8000 + tile,
                  0x9000 + tile]
    return block + [0xC0006000, 0xFFFFFFFF, 0xC0006100, 0]


def depth_list(stray=0x3F800000, tiles=False, shader_value=0x40):
    """A list in the shape of a depth pass: vertex constants, the vertex shader, state, the
    vertex buffer, one draw. Returns its PM4 (with the trailer), patch table, area C, records
    and entries. `stray` is one of the constants' words. With `tiles` it is in the shape of a
    scene pass's instead: a tile block first, a render target write, and no window write.
    `shader_value` is the value word of the shader address's entry (the files' are multiples
    of 4)."""
    pm4 = (tile_block() + [2 << 16 | 0x2000, 0x21, 0x22, 0x23]) if tiles else []
    pm4 += [0x80000000] * 3
    pm4 += [15 << 16 | 0x4000] + [stray] + [0x40000000 + i for i in range(15)]
    pm4 += [3 << 16 | 0x8000 | 0x57E, 0x00010011, 1, 0, 0]
    shader = len(pm4) + 1
    pm4 += [0xC0012700, PLACEHOLDER, 24]
    pm4 += [3 << 16 | 0x2200, 0x11, 0x22, 0x33, 0x44]
    fetch = len(pm4) + 1
    pm4 += [5 << 16 | 0x48BA, 1, 2, 3, 4, 5, 6]
    pm4 += [2 << 16 | 0x5000, 0, 0x00025000, 0]
    pm4 += [0x2102, 0]
    draw = len(pm4) + 1
    pm4 += [0xC0032201, 0, 12 << 16 | 6, PLACEHOLDER, 0x40000000 | 12]
    pm4 += [0x057F, draw - 1]
    if not tiles:
        pm4 += [2 << 16 | 0x2080, 0, 0, 0x02D00500]
    # The shader's place and the two buffers'; the buffers are in area C too.
    table = [0x88000000 | shader << 6, shader_value, 0x88000000 | fetch << 6 | 4, 0,
             0x88000000 | draw << 6, 0]
    area_c = [0x08000000 | fetch << 6 | 4, 0, 0x08000000 | draw << 6, 0]
    records = [9, 0x45000000, 1, 0x49000002, 2, 0x48000004]
    entries = [1, 0x09000000, 2, 0x08000002]
    return (words(*pm4) + rmb_lists.TRAILER, words(*table), words(*area_c), words(*records),
            words(*entries))


def other_opcode_list():
    """A list no model has: a DRAW_INDX_2 packet. The converter refuses it."""
    pm4 = [0x2102, 0, 0xC0003600, 3 << 16 | 0x86]
    return words(*pm4) + rmb_lists.TRAILER, b"", b"", b"", b""


def header_block(pm4, table, flags):
    """Area A's part of a list: the 0xC8-byte header, the patch table, zeros, the marker."""
    block = bytearray(rmb_lists.HEADER_BYTES) + table
    block += bytes(-len(block) % 8 + 8) + rmb_lists.MARKER
    n = (len(pm4) - 8) // 4
    for offset, word in ((0x00, 0x00400009), (0x68, len(block) - 8), (0x6C, flags),
                         (0x70, rmb_lists.HEADER_BYTES + len(table)), (0x74, 0xB8),
                         (0x78, len(pm4) - 8), (0x7C, len(table) // 4), (0x90, 0xC8),
                         (0xBC, 1), (0xC0, 0x81000000 | n)):
        struct.pack_into(">I", block, offset, word)
    for i in range(20):
        struct.pack_into(">I", block, 0x18 + 4 * i, 0xFFFFFFFF)
    return bytes(block)


def model(lists):
    """A .rmb.bin with the lists {pass group: (pm4, table, area C, records, entries)}."""
    groups, area_a, area_b, area_c = b"", b"", b"", b""
    records_total = 0
    for group in range(rmb_lists.GROUPS):
        if group not in lists:
            groups += words(2, 0, 0, 0, 0, 0)
            continue
        pm4, table, fixups, records, entries = lists[group]
        block = header_block(pm4, table, 0x886 if 12 <= group < 15 else 0x86)
        groups += (words(2, len(records) // 8) + records + words(len(entries) // 8) + entries +
                   words(len(block), len(pm4), len(fixups)))
        records_total += len(records) // 8
        area_a += block
        area_b += bytes(-len(area_b) % 32) + pm4
        area_c += fixups
    head = words(1, 0, 1, len(area_a), len(area_b), len(area_c), 0, 8 * records_total)
    return (b"geometry\0" + b"shaders\\track\\made_up.fx" + head + groups + area_a + area_b +
            area_c)


class Parse(unittest.TestCase):
    def test_lists_and_their_parts(self):
        depth = depth_list()
        other = other_opcode_list()
        parsed = rmb_lists.parse(model({0: depth, 12: other}))
        self.assertEqual(parsed.head[:3], (1, 0, 1))
        self.assertEqual([lst.group for lst in parsed.lists], [0, 12])
        first, second = parsed.lists
        self.assertEqual(first.pm4 + rmb_lists.TRAILER, depth[0])
        self.assertEqual((first.table, first.area_c, first.records, first.entries), depth[1:])
        self.assertEqual(len(first.header), rmb_lists.HEADER_BYTES)
        self.assertEqual(struct.unpack_from(">I", first.header, 0x6C)[0], 0x86)
        self.assertTrue(first.marker and first.trailer)
        # The second list's PM4 starts at the next 32-byte boundary of area B.
        self.assertEqual(second.pm4 + rmb_lists.TRAILER, other[0])
        self.assertEqual(struct.unpack_from(">I", second.header, 0x6C)[0], 0x886)
        self.assertEqual((second.table, second.area_c), (b"", b""))

    def test_a_model_without_lists(self):
        parsed = rmb_lists.parse(model({}))
        self.assertEqual(parsed.lists, [])

    def test_fx_bytes_inside_a_list_are_passed_over(self):
        # ".fx" as a constant's bytes, after the real table's start.
        data = model({0: depth_list(stray=0x2E667801)})
        self.assertGreater(data.rfind(b".fx"), data.find(b"made_up.fx") + 8)
        parsed = rmb_lists.parse(data)
        self.assertEqual([lst.group for lst in parsed.lists], [0])
        self.assertTrue(parsed.lists[0].marker and parsed.lists[0].trailer)

    def test_areas_that_do_not_add_up(self):
        data = bytearray(model({0: depth_list()}))
        at = data.find(b"made_up.fx") + len(b"made_up.fx")
        struct.pack_into(">I", data, at + 16, struct.unpack_from(">I", data, at + 16)[0] + 32)
        self.assertIsNone(rmb_lists.parse(bytes(data)))
        self.assertIsNone(rmb_lists.parse(b"no shader name here"))
        self.assertIsNone(rmb_lists.parse(b"x.fx" + bytes(16)))

    def test_missing_marker_and_trailer_are_told(self):
        data = model({0: depth_list()})
        self.assertFalse(rmb_lists.parse(data.replace(rmb_lists.MARKER, bytes(8))).lists[0].marker)
        cut = data.replace(rmb_lists.TRAILER, words(0, 0xC0000001))
        self.assertFalse(rmb_lists.parse(cut).lists[0].trailer)


@unittest.skipUnless(sys.platform.startswith("linux") and
                     (os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")),
                     "builds the checker: Linux with a C++ compiler")
class Check(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="test-rmb-lists-")
        cls.checker = native_list_check.build_checker(cls.dir)
        cls.archive = os.path.join(cls.dir, "bin.zip")
        both = model({0: depth_list(), 12: other_opcode_list()})
        with zipfile.ZipFile(cls.archive, "w") as z:
            z.writestr("made_up.00001.rmb.bin", both)
            z.writestr("made_up.00002.rmb.bin", both)  # the archives repeat members
            z.writestr("made_up.00003.rmb.bin", model({18: depth_list(shader_value=0x42)}))
            z.writestr("made_up.00004.rmb.bin", b"not a model")
            z.writestr("made_up.00005.rmb.bin", model({13: depth_list(tiles=True)}))
            z.writestr("made_up.00001.pgeo", b"another kind of member")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_tool(self, *options):
        out = os.path.join(self.dir, "counts.json")
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            status = native_list_check.main(
                [self.archive, "--checker", self.checker, "--json", out,
                 "--unxmem", os.path.join(self.dir, "none")] + list(options))
        with open(out) as f:
            return status, json.load(f), text.getvalue()

    def test_counts_and_proof(self):
        status, c, text = self.run_tool()
        self.assertEqual(c["archive"], dict(archives=1, members=5, distinct_members=4, read=5,
                                            unparsed=1, without_lists=0, lists=6,
                                            bytes=c["archive"]["bytes"]))
        self.assertEqual(c["census"]["lists"], 6)
        self.assertEqual(c["census"]["group_lists"], {"0": 2, "12": 2, "13": 1, "18": 1})
        self.assertEqual(c["census"]["type3_opcodes"], {"0022": 4, "0027": 4, "002D": 3, "0036": 2,
                                                        "0060": 4, "0061": 2})
        self.assertEqual(c["census"]["one_register_packets"], {"057E": 4})
        self.assertEqual(c["census"]["subcode_62_63_entries"], 0)
        self.assertEqual(c["census"]["record_types"], {"1": 4, "2": 4, "9": 4})
        # One of the four shader address entries has a value word that is no multiple of 4.
        self.assertEqual((c["census"]["shader_address_entries"],
                          c["census"]["shader_address_values_unaligned"]), (4, 1))
        conversion = c["conversion"]
        self.assertEqual((conversion["converted"], conversion["refused"]), (4, {"opcode": 2}))
        self.assertEqual(conversion["distinct_keys"], 3)  # the value word is part of the key
        self.assertEqual(conversion["slots_by_type"], {"1": 4, "2": 4, "9": 4})
        self.assertEqual((conversion["record_places_checked"], conversion["record_places_agree"],
                          conversion["slots_no_record_names"]), (20, 20, 0))
        # The list recorded for the tiles: its three window writes and its render target write
        # are scene_msaa's packets, and Untiled() tells its two forms apart.
        self.assertEqual((conversion["tiled"], conversion["tile_bound_agree"],
                          conversion["tile_bound_packets"], conversion["untiled_told"]),
                         (1, 1, 4, 1))
        for name, runs in c["proof"].items():
            want = 1 if "/untiled/" in name else 4
            self.assertEqual((runs["runs"], runs["all"], runs["draws"]), (want, want, want), name)
        self.assertEqual(c["key"]["same_after_fixups"], 4)
        self.assertEqual(c["key"]["sees_changed_pm4_word"], 4)
        self.assertEqual((c["key"]["same_untiled"], c["key"]["untiled_refused_as_input"]), (1, 1))
        self.assertEqual(c["reduction"]["register_deltas"], 2)
        self.assertEqual(c["draws_without_vertex_shader"]["draws"], 0)
        # The member that is no model is a failure of the run; a refusal alone is not.
        self.assertEqual(status, 1)
        self.assertIn("RESULT: FAIL", text)
        self.assertIn("1 member(s) without a readable list table", text)
        parsed = dict(c, archive=dict(c["archive"], unparsed=0))
        self.assertEqual(native_list_check.failures(parsed), [])
        wrong = dict(parsed, conversion=dict(conversion, untiled_told=0))
        self.assertEqual(native_list_check.failures(wrong),
                         ["Untiled() does not tell tiled from untiled on 1 list(s)"])
        self.assertNotIn("made_up", text)  # counts only: no member name

    def test_distinct_members_and_refusals(self):
        status, c, _ = self.run_tool("--distinct", "--no-refusals",
                                     "--expect-reduction", "1,1,1,1,2")
        self.assertEqual((c["archive"]["read"], c["census"]["lists"]), (4, 4))
        problems = native_list_check.failures(c, True, [1, 1, 1, 1, 2])
        self.assertEqual(status, 1)
        self.assertIn("1 list(s) refused", problems)
        self.assertTrue(any(p.startswith("reduction [2, 1, 2, 1, 1]") for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
