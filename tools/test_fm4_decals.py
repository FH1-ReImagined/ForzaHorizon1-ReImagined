"""tools/fm4_pvs.py's decals (#648): FM4's road lines, road edges and road detail drawn by FH1.
Synthetic model files and layout tables (no game files); with FM4_DISC=<FM4's disc 1 folder>
set, Suzuka's own decal models are checked too (RealModels)."""
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_pvs  # noqa: E402

FM4_DECAL_DEPTH = 0x082082E3  # FM4's decal buffers: depth tested, not written; stencil EQUAL
WRITTEN = 0x082082E7          # the same with the depth written (FM4's own *_zwrite decals)
ROAD_DEPTH = 0x087087E7       # its opaque buffers: depth written, stencil ALWAYS
NO_BLEND = 0x00010001         # RB_BLENDCONTROL0 of the painted lines and edges
BLEND = 0x07060706            # and of the road detail
NOP = 0x80000000
TEXTURES = 0x00054800         # a type-0 packet: fetch constants from 0 (six words)
VERTICES = 0x000548BA         # the vertex buffer's fetch constant
STATE = 0x00012200            # RB_DEPTHCONTROL and RB_BLENDCONTROL0
DRAW = 0xC0032201             # an indexed draw, four words after it
MARKER = bytes.fromhex("1122334455667789")
TAIL = b"tail-of-file"


def draw(depth=FM4_DECAL_DEPTH, blend=NO_BLEND):
    """One draw of a pass group's command buffer, 22 words: the textures, the vertex buffer,
    the depth and blend state, the indexed draw."""
    return [TEXTURES] + [0] * 6 + [VERTICES] + [0] * 6 + [STATE, depth, blend] + [DRAW, 0, 0, 0, 0]


def buffer(*draws, nops=2):
    return [w for d in draws for w in d] + [NOP] * nops


def model(buffers, tail=TAIL):
    """A track model's command-buffer part: a shader name, the .fx header (word 3 area A's size,
    4 area B's, 5 area C's, 7 eight times the records), 21 pass groups (u32 2, u32 n, n records,
    u32 m, m entries, 3 sizes) and the three areas. buffers: {group: [PM4 words]}. Per group,
    area A is D3D's precompiled command buffer header with a patch place per texture packet,
    area B the buffer at the next 32-byte boundary, area C a fixup per vertex packet and draw
    (bits 6-25 the word in the buffer it patches)."""
    groups, area_a, area_b, area_c, records = b"", b"", bytearray(), b"", 0
    for i in range(21):
        if i not in buffers:
            groups += struct.pack(">III3I", 2, 0, 0, 0, 0, 0)
            continue
        words = buffers[i]
        places = [k + 1 for k, w in enumerate(words) if w == TEXTURES]
        tend = 0xC8 + 4 * len(places)
        mark = ((tend + 7) & ~7) + 8
        head = bytearray(mark) + MARKER
        for off, v in ((0x68, len(head) - 8), (0x70, tend), (0x78, 4 * len(words) - 8), (0x7C, len(places)),
                       (0x90, 0xC8), (0xC0, 0x81000000 | (len(words) - 2))):
            struct.pack_into(">I", head, off, v)
        for t, k in enumerate(places):
            more = 0x40000000 if t + 1 < len(places) else 0
            struct.pack_into(">I", head, 0xC8 + 4 * t, 0x84000000 | more | (k << 6))
        fixups = b"".join(struct.pack(">II", 0x08000000 | ((k + 1) << 6) | (4 if w == VERTICES else 0), 0)
                          for k, w in enumerate(words) if w in (VERTICES, DRAW))
        recs = [struct.pack(">II", 5, 0x41000000)] + ([struct.pack(">II", 3, 0x47000000)] if places else [])
        groups += (struct.pack(">II", 2, len(recs)) + b"".join(recs) + struct.pack(">I", 0)
                   + struct.pack(">3I", len(head), 4 * len(words), len(fixups)))
        records += len(recs)
        area_a += bytes(head)
        area_b += bytes(((len(area_b) + 31) & ~31) - len(area_b)) + struct.pack(f">{len(words)}I", *words)
        area_c += fixups
    head = struct.pack(">8I", 1, 0, 1, len(area_a), len(area_b), len(area_c), 0, 8 * records)
    return (bytes(48) + b"track\\shaders\\rdline_blnd_spec_opac_3.fx" + head + groups + area_a + bytes(area_b)
            + area_c + tail)


def parts(data):
    """({group: (its records' block, its area A block, its buffer, its area C block)}, what
    follows area C) of a model file."""
    p = data.rfind(b".fx") + 3
    head = struct.unpack_from(">8I", data, p)
    g = fm4_pvs.pass_groups(data, p + 32)
    a = g[-1][1]
    b = a + head[3]
    c = b + head[4]
    out, o = {}, 0
    for i, x in enumerate(g):
        if not any(x[3]):
            continue
        o = (o + 31) & ~31
        out[i] = (data[x[0]:x[1]], data[a:a + x[3][0]], data[b + o:b + o + x[3][1]], data[c:c + x[3][2]])
        a, o, c = a + x[3][0], o + x[3][1], c + x[3][2]
    return out, data[c:]


def words(buf):
    return list(struct.unpack(f">{len(buf) // 4}I", buf))


def depths(buf):
    return [w for w in words(buf) if w in (FM4_DECAL_DEPTH, WRITTEN, ROAD_DEPTH)]


LINES = {11: buffer(draw()), 14: buffer(draw(), nops=3), 17: buffer(draw(), nops=1)}
DETAIL = {i: buffer(draw(blend=BLEND), nops=n) for i, n in ((11, 2), (14, 3), (17, 1))}
ROAD = {3: buffer(draw(ROAD_DEPTH)), 12: buffer(draw(ROAD_DEPTH), nops=3), 15: buffer(draw(ROAD_DEPTH), nops=1)}


class DecalGroup(unittest.TestCase):
    def moved(self, data, out, slot):
        """The checks every move has to pass: the file keeps its size, what stands before the
        groups and after area C stays, the other groups keep everything, and the decal group
        is in the slot's place with its records, its area A and its area C. Its buffer."""
        before, tail0 = parts(data)
        after, tail = parts(out)
        self.assertEqual(len(out), len(data))
        self.assertEqual(sorted(after), sorted(slot if i == 14 else i for i in before))
        first = fm4_pvs.pass_groups(data, data.rfind(b".fx") + 35)[0][0]
        self.assertEqual(out[:first], data[:first])
        self.assertEqual(tail, tail0)
        for i in before:
            if i != 14:
                self.assertEqual(after[i], before[i])
        self.assertEqual(after[slot][:2] + after[slot][3:], before[14][:2] + before[14][3:])
        self.assertEqual(len(after[slot][2]), len(before[14][2]))
        return before[14][2], after[slot][2]

    def test_a_decal_model_is_drawn_by_the_opaque_group(self):
        data = model(LINES)
        old, new = self.moved(data, fm4_pvs.decal_group_to(data), 12)
        # the buffer writes the depth, so that a road drawn after it does not paint it over;
        # nothing else in it changes
        self.assertEqual(words(new), [WRITTEN if w == FM4_DECAL_DEPTH else w for w in words(old)])
        self.assertEqual(depths(new), [WRITTEN])

    def test_a_blended_decal_is_drawn_by_the_transparent_group(self):
        data = model(DETAIL)
        old, new = self.moved(data, fm4_pvs.decal_group_to(data, 13), 13)
        self.assertEqual(new, old)                            # drawn after the road: FM4's state stays

    def test_the_buffers_own_blend_state_has_to_be_the_pass(self):
        # in the other pass the load faults: a model whose buffer is not of the pass is left
        self.assertIsNone(fm4_pvs.decal_group_to(model(LINES), 13))
        self.assertIsNone(fm4_pvs.decal_group_to(model(DETAIL), 12))
        mixed = dict(LINES)
        mixed[14] = buffer(draw(), draw(blend=BLEND))         # two draws, one of each kind
        unknown = dict(LINES)
        unknown[14] = [TEXTURES] + [0] * 6 + [0x2200, FM4_DECAL_DEPTH, DRAW, 0, 0, 0, 0]  # no blend write
        for buffers in (mixed, unknown):
            for slot in (12, 13):
                self.assertIsNone(fm4_pvs.decal_group_to(model(buffers), slot))

    def test_a_draw_before_the_buffers_blend_write_is_left(self):
        # such a draw takes the state the buffer drawn before it left, whatever the buffer's
        # own writes after it say (the review of #799: DRAW, then RB_BLENDCONTROL0)
        late = [TEXTURES] + [0] * 6 + [0x2200, FM4_DECAL_DEPTH, DRAW, 0, 0, 0, 0, 0x2201, NO_BLEND, NOP]
        second = buffer([DRAW, 0, 0, 0, 0], draw())           # the second draw is after one
        late_detail = buffer([DRAW, 0, 0, 0, 0], draw(blend=BLEND))
        for buffers in ({**LINES, 14: late}, {**LINES, 14: second}, {**DETAIL, 14: late_detail}):
            for slot in (12, 13):
                self.assertIsNone(fm4_pvs.decal_group_to(model(buffers), slot))
        # a draw after another one, with no state write between, keeps the buffer's state
        again = buffer(draw(), [TEXTURES] + [0] * 6 + [DRAW, 0, 0, 0, 0])
        data = model({**LINES, 14: again})
        _, new = self.moved(data, fm4_pvs.decal_group_to(data), 12)
        self.assertEqual(depths(new), [WRITTEN])
        data = model({**DETAIL, 14: buffer(draw(blend=BLEND), [TEXTURES] + [0] * 6 + [DRAW, 0, 0, 0, 0])})
        self.moved(data, fm4_pvs.decal_group_to(data, 13), 13)

    def test_a_state_write_the_scan_does_not_read_is_left(self):
        # RB_DEPTHCONTROL (0x2200) or RB_BLENDCONTROL0 (0x2201) written by a type-0 packet with
        # bit 15 (every value to the one register) or by a type-3 packet, between the buffer's
        # state write and its draw: the scan would miss what the draw is drawn with
        def between(blend, packet):
            d = draw(blend=blend)
            return buffer(d[:-5] + packet + d[-5:])
        for name, base, blend, slot, other in (("lines", LINES, NO_BLEND, 12, BLEND),
                                               ("detail", DETAIL, BLEND, 13, NO_BLEND)):
            for what, packet in (
                    ("one register, blend", [0x0001A201, other, other]),
                    ("one register, depth", [0x0000A200, WRITTEN]),
                    ("SET_CONSTANT", [0xC0012D00, 0x00040201, other]),
                    ("SET_CONSTANT over both", [0xC0042D00, 0x000401FF, 0, FM4_DECAL_DEPTH, other, 0]),
                    ("SET_CONSTANT2", [0xC0015500, 0x00002201, other]),
                    ("SET_SHADER_CONSTANTS", [0xC0015600, 0x00002200, WRITTEN]),
                    ("LOAD_ALU_CONSTANT", [0xC0022F00, 0x01000000, 0x00040200, 2]),
                    ("COND_WRITE", [0xC0054500, 0x00000003, 0x00001000, 0, 0xFFFFFFFF, 0x00002201, other]),
                    ("INDIRECT_BUFFER", [0xC0013F00, 0x01000000, 16])):
                data = model({**base, 14: between(blend, packet)})
                self.assertIsNone(fm4_pvs.decal_group_to(data, slot), (name, what))
            # the same packets writing other registers, or memory, change nothing
            for what, packet in (
                    ("one register, RB_COLORCONTROL", [0x0001A202, other, other]),
                    ("SET_CONSTANT of ALU constants", [0xC0012D00, 0x00000201, other]),
                    ("SET_CONSTANT, RB_COLORCONTROL", [0xC0012D00, 0x00040202, other]),
                    ("COND_WRITE to memory", [0xC0054500, 0x00000103, 0x00001000, 0, 0xFFFFFFFF, 0x00002201, other]),
                    ("type-3 NOP", [0xC0011000, 0x00002201, other])):
                data = model({**base, 14: between(blend, packet)})
                self.assertIsNotNone(fm4_pvs.decal_group_to(data, slot), (name, what))

    def test_every_draw_of_the_buffer_gets_the_depth_state(self):
        buffers = dict(LINES)
        buffers[14] = buffer(draw(), draw(), draw(ROAD_DEPTH))
        data = model(buffers)
        old, new = self.moved(data, fm4_pvs.decal_group_to(data), 12)
        self.assertEqual((depths(old), depths(new)), ([FM4_DECAL_DEPTH] * 2 + [ROAD_DEPTH], [WRITTEN] * 2 + [ROAD_DEPTH]))

    def test_decals_that_show_in_mirrors_and_cube_maps(self):
        # FM4 fills the mirror pass's decal group (8) and the cube map pass's (5) for them too
        buffers = dict(LINES)
        buffers[5] = buffer(draw(), nops=5)
        buffers[8] = buffer(draw(), nops=4)
        data = model(buffers)
        old, new = self.moved(data, fm4_pvs.decal_group_to(data), 12)
        self.assertEqual(depths(new), [WRITTEN])
        detail = dict(DETAIL)
        detail[8] = buffer(draw(blend=BLEND), nops=4)
        data = model(detail)
        self.moved(data, fm4_pvs.decal_group_to(data, 13), 13)

    def test_a_register_inside_a_longer_write(self):
        buffers = dict(LINES)
        buffers[14] = [TEXTURES] + [0] * 6 + [(3 << 16) | 0x21FF, 0x11111111, FM4_DECAL_DEPTH, NO_BLEND, 0x22222222,
                                               DRAW, 0, 0, 0, 0, NOP]
        data = model(buffers)
        old, new = self.moved(data, fm4_pvs.decal_group_to(data), 12)
        self.assertEqual(words(new)[7:12], [(3 << 16) | 0x21FF, 0x11111111, WRITTEN, NO_BLEND, 0x22222222])

    def test_other_models_are_left_alone(self):
        glass = {12: buffer(draw(ROAD_DEPTH)), 13: buffer(draw(ROAD_DEPTH, BLEND))}
        both = {12: buffer(draw(ROAD_DEPTH)), 14: buffer(draw())}             # not decals alone
        no_main = {11: buffer(draw()), 17: buffer(draw())}
        for buffers in (ROAD, glass, both, no_main):
            for slot in (12, 13):
                self.assertIsNone(fm4_pvs.decal_group_to(model(buffers), slot))
        self.assertIsNone(fm4_pvs.decal_group_to(b"not a model"))

    def test_a_buffer_that_is_not_as_expected_is_left_alone(self):
        no_depth = dict(LINES)
        no_depth[14] = [0x2201, NO_BLEND, DRAW, 0, 0, 0, 0]
        cut = dict(LINES)
        cut[14] = [(5 << 16) | 0x2200, FM4_DECAL_DEPTH, NO_BLEND]             # says six values, holds two
        # the transparent slot keeps FM4's depth state but also asks for its write
        blended_no_depth = {**DETAIL, 14: [0x2201, BLEND, DRAW, 0, 0, 0, 0, NOP]}
        self.assertIsNone(fm4_pvs.decal_group_to(model(blended_no_depth), 13))
        for buffers in (no_depth, cut):
            for slot in (12, 13):
                self.assertIsNone(fm4_pvs.decal_group_to(model(buffers), slot))

    def test_a_header_that_disagrees_with_the_groups_is_left_alone(self):
        data = model(LINES)
        self.assertIsNotNone(fm4_pvs.decal_group_to(data))
        p = data.rfind(b".fx") + 3
        for word in (3, 4, 5):                                # the sizes of areas A, B and C
            for change in (8, -8):
                bad = bytearray(data)
                struct.pack_into(">I", bad, p + 4 * word, struct.unpack_from(">I", bad, p + 4 * word)[0] + change)
                self.assertIsNone(fm4_pvs.decal_group_to(bytes(bad)), (word, change))
        self.assertIsNone(fm4_pvs.decal_group_to(data[:-len(TAIL) - 8]))      # the file ends inside area C
        self.assertIsNotNone(fm4_pvs.decal_group_to(data[:-len(TAIL)]))       # and right after it

    def test_after_a_fetch_constant_is_inserted(self):
        # the lightmap binding rebuilds a model's groups and areas first (insert_fetch_constant,
        # which moves the buffer's words and with them area A's and area C's places)
        buffers = dict(LINES)
        buffers[14] = buffer(draw(), draw(), nops=3)
        bound = fm4_pvs.insert_fetch_constant(model(buffers), 7)
        self.assertIsNotNone(bound)
        old, new = self.moved(bound, fm4_pvs.decal_group_to(bound), 12)
        self.assertEqual(len(old), 4 * (len(buffers[14]) + 2 * 7))            # a packet of 7 words a draw
        self.assertEqual(words(new).count(0x0005482A), 2)
        self.assertEqual(depths(new), [WRITTEN] * 2)
        # area C is the bound model's, byte for byte, and every fixup still lands on its packet
        self.assertEqual([c for _, _, _, c in parts(fm4_pvs.decal_group_to(bound))[0].values()],
                         [c for _, _, _, c in parts(bound)[0].values()])
        fixups = parts(fm4_pvs.decal_group_to(bound))[0][12][3]
        at = [(struct.unpack_from(">I", fixups, e)[0] >> 6) & 0xFFFFF for e in range(0, len(fixups), 8)]
        self.assertEqual([words(new)[k - 1] for k in at], [VERTICES, DRAW] * 2)

    def test_parts_of_the_depth_state(self):
        for mode, value in (("0", FM4_DECAL_DEPTH), ("1", WRITTEN), ("stencil", 0x087087E3), ("both", ROAD_DEPTH)):
            with mock.patch.object(fm4_pvs, "DECAL_DEPTH", fm4_pvs.DECAL_DEPTHS[mode]):
                data = model(LINES)
                _, new = self.moved(data, fm4_pvs.decal_group_to(data), 12)
            self.assertEqual([w for w in words(new) if w >> 24 == 0x08], [value], mode)


OPAQUE, TRANSPARENT = 0x083F0214, 0x083F0212   # an instance's flags: bit 2, bit 1


def groups_of(path):
    with open(path, "rb") as f:
        return sorted(parts(f.read())[0])


class Layout(unittest.TestCase):
    """draw_decals: the files of a track's bin folder, by the instances of its layouts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = self.tmp.name
        # model 0 painted lines, 1 a road, 2 blended road detail, 3 not streamed (no file),
        # 4 lines flagged both ways (one way a ribbon), 5 lines no instance uses,
        # 6 road detail flagged opaque, 7 lines flagged transparent
        for m, buffers in {0: LINES, 1: ROAD, 2: DETAIL, 4: LINES, 5: LINES, 6: DETAIL, 7: LINES}.items():
            self.write("trk", m, buffers)

        def layout(model_4):
            return {"prefix": "trk", "models": [{}] * 8,
                    "instances": [{"model": 0, "flags": OPAQUE}, {"model": 1, "flags": 0x083F0804},
                                  {"model": 2, "flags": TRANSPARENT}, {"model": 3, "flags": OPAQUE},
                                  {"model": 4, "flags": model_4}, {"model": 6, "flags": OPAQUE},
                                  {"model": 7, "flags": TRANSPARENT}],
                    "dynamic": [{"model": 2, "flags": TRANSPARENT}]}
        self.P, self.P2 = layout(OPAQUE), layout(TRANSPARENT)  # two ribbons, one bin folder
        self.converted = [("Ribbon_00", "", None, self.P, [], None, []),
                          ("Ribbon_01", "", None, self.P2, [], None, [])]

    def write(self, prefix, m, buffers):
        with open(os.path.join(self.bin, f"{prefix}.{m:05d}.rmb.bin"), "wb") as f:
            f.write(model(buffers))

    def read(self, m, prefix="trk"):
        with open(os.path.join(self.bin, f"{prefix}.{m:05d}.rmb.bin"), "rb") as f:
            return f.read()

    def groups(self, prefix="trk"):
        return {int(n.split(".")[1]): groups_of(os.path.join(self.bin, n))
                for n in sorted(os.listdir(self.bin)) if n.startswith(prefix + ".")}

    def flags(self):
        return [[i["flags"] for i in P["instances"] + P["dynamic"]] for P in (self.P, self.P2)]

    def test_each_model_goes_where_its_instances_are_flagged_for(self):
        flags = self.flags()
        self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), (2, 3))
        self.assertEqual(self.groups(), {0: [11, 12, 17], 1: [3, 12, 15], 2: [11, 13, 17], 4: [11, 14, 17],
                                         5: [11, 14, 17], 6: [11, 14, 17], 7: [11, 14, 17]})
        self.assertEqual(self.flags(), flags)                 # the layouts keep FM4's flags

    def test_every_decal_model_left_is_counted(self):
        # decal models neither slot takes are counted as left too: a buffer of both kinds,
        # one with no blend write, one that draws before its blend write, a header that
        # disagrees with the groups, and lines flagged neither way
        self.write("trk", 8, {**LINES, 14: buffer(draw(), draw(blend=BLEND))})
        self.write("trk", 9, {**LINES, 14: [TEXTURES] + [0] * 6 + [0x2200, FM4_DECAL_DEPTH, DRAW, 0, 0, 0, 0]})
        self.write("trk", 10, {**LINES, 14: buffer([DRAW, 0, 0, 0, 0], draw())})
        bad = bytearray(model(LINES))
        p = bad.rfind(b".fx") + 3
        struct.pack_into(">I", bad, p + 12, struct.unpack_from(">I", bad, p + 12)[0] + 8)  # area A's size
        with open(os.path.join(self.bin, "trk.00011.rmb.bin"), "wb") as f:
            f.write(bad)
        self.write("trk", 12, LINES)
        self.P["instances"] += [{"model": m, "flags": OPAQUE} for m in (8, 9, 10, 11)]
        self.P["instances"].append({"model": 12, "flags": OPAQUE & ~0x6})
        before = {m: self.read(m) for m in range(8, 13)}
        self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), (2, 8))
        self.assertEqual({m: self.read(m) for m in before}, before)

    def test_a_second_run_changes_nothing_more(self):
        fm4_pvs.draw_decals(self.converted, self.bin)
        first = self.groups()
        self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), (0, 3))   # the three left
        self.assertEqual(self.groups(), first)

    def test_each_prefix_by_its_own_instances(self):
        # two ribbons with model tables of their own: model 0 is lines in one, road detail in
        # the other
        self.write("alt", 0, DETAIL)
        self.P2["prefix"] = "alt"
        self.P2["instances"] = [{"model": 0, "flags": TRANSPARENT}]
        self.P2["dynamic"] = []
        self.P["instances"] = [{"model": 0, "flags": OPAQUE}]
        self.P["dynamic"] = []
        self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), (2, 0))
        self.assertEqual((self.groups()[0], self.groups("alt")[0]), ([11, 12, 17], [11, 13, 17]))

    def test_switched_off(self):
        before = self.groups()
        with mock.patch.object(fm4_pvs, "DECALS", False):
            self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), (0, 0))
        self.assertEqual(self.groups(), before)
        # with FM4's flags kept or forced (FM4_PVS_FLAGS, a test) nothing is touched either
        with mock.patch.object(fm4_pvs, "FLAGS", "keep"):
            self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), (0, 0))
        self.assertEqual(self.groups(), before)

    def test_the_blended_ones_can_be_left(self):
        with mock.patch.object(fm4_pvs, "DECAL_SLOT", {0x4: 12}):
            self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), (1, 4))
        self.assertEqual((self.groups()[0], self.groups()[2]), ([11, 12, 17], [11, 14, 17]))


def on_disc(root, *names):
    """A path under root, whatever the case of its folders (the disc's is Media/Tracks)."""
    for name in names:
        root = os.path.join(root, next((n for n in os.listdir(root) if n.lower() == name.lower()), name))
    return root


@unittest.skipUnless(os.environ.get("FM4_DISC"), "FM4_DISC (FM4's disc 1 folder) is not set: no real model is read")
class RealModels(unittest.TestCase):
    """Suzuka's own model files through the move, for what the synthetic ones cannot show: that
    the layout the code and these tests assume is the files'."""

    def test_suzukas_decal_models(self):
        import fm4_track_mod
        zpath = on_disc(os.environ["FM4_DISC"], "media", "tracks", "suzuka", "bin.zip")
        moved = changed = 0
        with zipfile.ZipFile(zpath) as zf, open(zpath, "rb") as fh, tempfile.TemporaryDirectory() as tmp:
            for info in zf.infolist():
                if not info.filename.lower().endswith(".rmb.bin"):
                    continue
                data = fm4_track_mod.member_data(zpath, zf, info, fh, tmp)
                outs = [(slot, fm4_pvs.decal_group_to(data, slot)) for slot in (12, 13)]
                outs = [(slot, out) for slot, out in outs if out is not None]
                if not outs:
                    continue
                (slot, out), = outs                           # one pass or the other, never both
                moved += 1
                before, tail0 = parts(data)
                after, tail = parts(out)
                self.assertEqual((len(out), tail), (len(data), tail0), info.filename)
                self.assertEqual(sorted(after), sorted(slot if i == 14 else i for i in before))
                for i in before:
                    j = slot if i == 14 else i
                    self.assertEqual(after[j][:2] + after[j][3:], before[i][:2] + before[i][3:])
                    old, new = words(before[i][2]), words(after[j][2])
                    packets = fm4_pvs._pm4_packets(after[j][2])
                    self.assertIsNotNone(packets, info.filename)
                    if i != 14 or slot == 13:
                        self.assertEqual(new, old)
                    else:                                     # the depth words alone, each a 0x2200 write's
                        depth_words = {at // 4 + 1 + 0x2200 - (w & 0x7FFF) for at, w in packets
                                       if not w >> 30 and not w & 0x8000
                                       and (w & 0x7FFF) <= 0x2200 <= (w & 0x7FFF) + ((w >> 16) & 0x3FFF)}
                        self.assertTrue(depth_words, info.filename)
                        clear, put = fm4_pvs.DECAL_DEPTH       # FM4_PVS_DECAL_DEPTH's mode
                        self.assertEqual(new, [w & ~clear | put if k in depth_words else w for k, w in enumerate(old)],
                                         info.filename)
                        changed += old != new
                    # every area C fixup lands in a packet that takes an address: a fetch
                    # constant write (a type-0 packet in 0x4800..0x48FF) or a draw (type 3)
                    for e in range(0, len(after[j][3]), 8):
                        k = (struct.unpack_from(">I", after[j][3], e)[0] >> 6) & 0xFFFFF
                        w = max((p for p in packets if p[0] // 4 < k), default=(0, 0))[1]
                        self.assertTrue(w >> 30 == 3 or (w >> 30 == 0 and 0x4800 <= w & 0x7FFF < 0x4900),
                                        (info.filename, i, k, hex(w)))
        # of Suzuka's 1282 models; six hold the road's depth state already (and with
        # FM4_PVS_DECAL_DEPTH=0 no buffer changes)
        self.assertEqual((moved, changed), (147, 141 if fm4_pvs.DECAL_DEPTH != (0, 0) else 0))


if __name__ == "__main__":
    unittest.main()
