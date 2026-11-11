"""tools/fm4_pvs.py's decals (#648): FM4's road lines and road edges drawn by FH1. Synthetic
model files and layout tables only (no game files)."""
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_pvs  # noqa: E402

FM4_DECAL_DEPTH = 0x082082E3  # FM4's decal buffers: depth tested, not written; stencil EQUAL
ROAD_DEPTH = 0x087087E7       # its opaque buffers: depth written, stencil ALWAYS
NOP = 0x80000000


def depth_write(value, reg=0x2200):
    """A type-0 PM4 packet that writes one register."""
    return [reg, value]


def model(buffers):
    """A track model's command-buffer part as the converter reads it: a shader name, the .fx
    header (word 3 area A's size, word 4 area B's), 21 pass groups (u32 2, u32 n, n records,
    u32 m, m entries, 3 sizes) and the areas. buffers: {group: [PM4 words]}."""
    groups, area_a, area_b = b"", b"", bytearray()
    for i in range(21):
        if i not in buffers:
            groups += struct.pack(">III3I", 2, 0, 0, 0, 0, 0)
            continue
        buf = struct.pack(f">{len(buffers[i])}I", *buffers[i])
        block = struct.pack(">4I", 0xA0 + i, 0, 0, 0)       # area A, not read here
        record = struct.pack(">II", 3, 0x47000000 | i)      # the group's one record
        groups += struct.pack(">II", 2, 1) + record + struct.pack(">I", 0) + struct.pack(">3I", len(block), len(buf), 0)
        area_a += block
        area_b += bytes(((len(area_b) + 31) & ~31) - len(area_b)) + buf
    head = struct.pack(">8I", 1, 0, 0, len(area_a), len(area_b), 0, 0, 8 * len(buffers))
    return bytes(48) + b"track\\shaders\\rdline_blnd_spec_opac_3.fx" + head + groups + area_a + bytes(area_b)


def filled(data):
    g = fm4_pvs.pass_groups(data, data.rfind(b".fx") + 3 + 32)
    return [i for i, x in enumerate(g) if x[2] or any(x[3])]


def depth_values(data):
    """Every value the file writes to RB_DEPTHCONTROL, in file order."""
    key = struct.pack(">I", 0x2200)
    out, at = [], data.find(key)
    while at >= 0:
        out.append(struct.unpack_from(">I", data, at + 4)[0])
        at = data.find(key, at + 4)
    return out


DECAL = {11: depth_write(FM4_DECAL_DEPTH) + [NOP], 14: [NOP] + depth_write(FM4_DECAL_DEPTH) + [NOP, NOP],
         17: depth_write(FM4_DECAL_DEPTH)}
ROAD = {3: [NOP], 12: depth_write(ROAD_DEPTH), 15: [NOP]}


class DecalGroup(unittest.TestCase):
    def test_a_decal_model_is_drawn_by_the_opaque_group(self):
        data = model(DECAL)
        self.assertEqual(filled(data), [11, 14, 17])
        out = fm4_pvs.decal_group_to(data)
        self.assertEqual(len(out), len(data))                 # the file keeps its size
        self.assertEqual(filled(out), [11, 12, 17])
        # the group's record went with it, and the data after the groups stayed where it was
        g = fm4_pvs.pass_groups(out, out.rfind(b".fx") + 3 + 32)
        self.assertEqual(out[g[12][0] + 8:g[12][0] + 16], struct.pack(">II", 3, 0x47000000 | 14))
        g0 = fm4_pvs.pass_groups(data, data.rfind(b".fx") + 3 + 32)
        self.assertEqual(g[-1][1], g0[-1][1])
        self.assertEqual(out[g[-1][1]:g[-1][1] + 48], data[g0[-1][1]:g0[-1][1] + 48])  # area A
        # the main pass's buffer writes the depth and passes the stencil whatever it holds,
        # as the road's does; the other passes' buffers are as FM4 made them
        self.assertEqual(depth_values(out), [FM4_DECAL_DEPTH, ROAD_DEPTH, FM4_DECAL_DEPTH])

    def test_a_blended_decal_is_drawn_by_the_transparent_group(self):
        data = model(DECAL)
        out = fm4_pvs.decal_group_to(data, 13)
        self.assertEqual(len(out), len(data))
        self.assertEqual(filled(out), [11, 13, 17])
        g = fm4_pvs.pass_groups(out, out.rfind(b".fx") + 3 + 32)
        self.assertEqual(out[g[13][0] + 8:g[13][0] + 16], struct.pack(">II", 3, 0x47000000 | 14))
        # drawn after the road in any case: FM4's own depth state stays
        self.assertEqual(depth_values(out), [FM4_DECAL_DEPTH] * 3)
        self.assertEqual(out[g[-1][1]:], data[g[-1][1]:])     # the areas, byte for byte

    def test_decals_that_show_in_mirrors_and_cube_maps(self):
        # FM4 fills the mirror pass's decal group (8) and the cube map pass's (5) for them too
        buffers = dict(DECAL)
        buffers[5] = depth_write(FM4_DECAL_DEPTH) + [NOP] * 3
        buffers[8] = [NOP] + depth_write(FM4_DECAL_DEPTH)
        data = model(buffers)
        out = fm4_pvs.decal_group_to(data)
        self.assertEqual(len(out), len(data))
        self.assertEqual(filled(out), [5, 8, 11, 12, 17])
        # the main pass's buffer alone is changed: the fourth of the five
        self.assertEqual(depth_values(out), [FM4_DECAL_DEPTH] * 3 + [ROAD_DEPTH, FM4_DECAL_DEPTH])
        self.assertEqual(filled(fm4_pvs.decal_group_to(data, 13)), [5, 8, 11, 13, 17])

    def test_a_register_inside_a_longer_write(self):
        buffers = dict(DECAL)
        buffers[14] = [(2 << 16) | 0x21FF, 0x11111111, FM4_DECAL_DEPTH, 0x00010001, NOP]
        out = fm4_pvs.decal_group_to(model(buffers))
        g = fm4_pvs.pass_groups(out, out.rfind(b".fx") + 3 + 32)
        head = struct.unpack_from(">8I", out, out.rfind(b".fx") + 3)
        buf = out[g[-1][1] + head[3] + 32:][:20]             # the second buffer, at the next 32
        self.assertEqual(struct.unpack(">5I", buf), ((2 << 16) | 0x21FF, 0x11111111, ROAD_DEPTH, 0x00010001, NOP))

    def test_other_models_are_left_alone(self):
        self.assertIsNone(fm4_pvs.decal_group_to(model(ROAD)))
        glass = {12: depth_write(ROAD_DEPTH), 13: depth_write(ROAD_DEPTH)}
        self.assertIsNone(fm4_pvs.decal_group_to(model(glass)))
        both = {12: depth_write(ROAD_DEPTH), 14: depth_write(FM4_DECAL_DEPTH)}   # not decals alone
        self.assertIsNone(fm4_pvs.decal_group_to(model(both)))
        no_main = {11: depth_write(FM4_DECAL_DEPTH), 17: depth_write(FM4_DECAL_DEPTH)}
        self.assertIsNone(fm4_pvs.decal_group_to(model(no_main)))
        self.assertIsNone(fm4_pvs.decal_group_to(b"not a model"))

    def test_a_buffer_that_is_not_as_expected_is_left_alone(self):
        no_depth = dict(DECAL)
        no_depth[14] = [NOP, NOP]
        cut = dict(DECAL)
        cut[14] = [(5 << 16) | 0x2200, FM4_DECAL_DEPTH]      # says six values, holds one
        for slot in (12, 13):
            self.assertIsNone(fm4_pvs.decal_group_to(model(no_depth), slot))
            self.assertIsNone(fm4_pvs.decal_group_to(model(cut), slot))

    def test_fm4s_depth_state_can_be_kept(self):
        with mock.patch.object(fm4_pvs, "DECAL_DEPTH", (0, 0)):
            out = fm4_pvs.decal_group_to(model(DECAL))
        self.assertEqual(filled(out), [11, 12, 17])
        self.assertEqual(depth_values(out), [FM4_DECAL_DEPTH] * 3)


OPAQUE, TRANSPARENT = 0x083F0214, 0x083F0212   # an instance's flags: bit 2, bit 1


class Layout(unittest.TestCase):
    """draw_decals: the files of a track's bin folder, by the instances of its layouts."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = self.tmp.name
        # model 0 painted lines, 1 a road, 2 blended road detail, 3 not streamed (no file),
        # 4 decals flagged both ways (one way a ribbon), 5 decals no instance uses
        for m, buffers in {0: DECAL, 1: ROAD, 2: DECAL, 4: DECAL, 5: DECAL}.items():
            with open(os.path.join(self.bin, f"trk.{m:05d}.rmb.bin"), "wb") as f:
                f.write(model(buffers))

        def layout(model_4):
            return {"prefix": "trk", "models": [{}] * 6,
                    "instances": [{"model": 0, "flags": OPAQUE}, {"model": 1, "flags": 0x083F0804},
                                  {"model": 2, "flags": TRANSPARENT}, {"model": 3, "flags": OPAQUE},
                                  {"model": 4, "flags": model_4}],
                    "dynamic": [{"model": 2, "flags": TRANSPARENT}]}
        self.P, self.P2 = layout(OPAQUE), layout(TRANSPARENT)  # two ribbons, one bin folder
        self.converted = [("Ribbon_00", "", None, self.P, [], None, []),
                          ("Ribbon_01", "", None, self.P2, [], None, [])]

    def read(self, m):
        with open(os.path.join(self.bin, f"trk.{m:05d}.rmb.bin"), "rb") as f:
            return f.read()

    def groups(self):
        return {m: filled(self.read(m)) for m in (0, 1, 2, 4, 5)}

    def flags(self):
        return [[i["flags"] for i in P["instances"] + P["dynamic"]] for P in (self.P, self.P2)]

    def test_each_model_goes_where_its_instances_are_flagged_for(self):
        flags = self.flags()
        self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), 2)
        self.assertEqual(self.groups(), {0: [11, 12, 17], 1: [3, 12, 15], 2: [11, 13, 17],
                                         4: [11, 14, 17], 5: [11, 14, 17]})
        self.assertEqual(self.flags(), flags)                 # the layouts keep FM4's flags

    def test_a_second_run_changes_nothing_more(self):
        fm4_pvs.draw_decals(self.converted, self.bin)
        first = self.groups()
        self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), 0)   # none is decal-only now
        self.assertEqual(self.groups(), first)

    def test_switched_off(self):
        before = self.groups()
        with mock.patch.object(fm4_pvs, "DECALS", False):
            self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), 0)
        self.assertEqual(self.groups(), before)
        # with FM4's flags kept or forced (FM4_PVS_FLAGS, a test) nothing is touched either
        with mock.patch.object(fm4_pvs, "FLAGS", "keep"):
            self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), 0)
        self.assertEqual(self.groups(), before)

    def test_the_blended_ones_can_be_left(self):
        with mock.patch.object(fm4_pvs, "DECAL_SLOT", {0x4: 12}):
            self.assertEqual(fm4_pvs.draw_decals(self.converted, self.bin), 1)
        self.assertEqual(self.groups()[0], [11, 12, 17])
        self.assertEqual(self.groups()[2], [11, 14, 17])


if __name__ == "__main__":
    unittest.main()
