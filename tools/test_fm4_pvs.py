import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import fm4_pvs
from fm4_pvs import HexGrid, insert_fetch_constant, pass_groups


def model_with_draw_fixups():
    pm4 = struct.pack(">21I", 0x00054800, *([0] * 6),
                      0x000548BA, *([0] * 6), 0xC0032201, *([0] * 4),
                      0x80000000, 0x80000000)
    header = bytearray(0xE0)
    for offset, word in ((0x68, 0xD8), (0x70, 0xCC), (0x78, len(pm4) - 8),
                         (0x7C, 1), (0x90, 0xC8), (0xC0, 0x81000000 | ((len(pm4) - 8) // 4)),
                         (0xC8, 0x84000040)):
        struct.pack_into(">I", header, offset, word)
    header[-8:] = bytes.fromhex("1122334455667789")
    # The vertex fixup uses base 32 + 4 dwords, and the index fixup base 60 + 2 dwords.
    draw_fixups = struct.pack(">4I", 0x08000204, 0, 0x080003C0, 0)
    material = struct.pack(">8I", 1, 0, 1, len(header), len(pm4), len(draw_fixups), 0, 8)
    group = struct.pack(">8I", 2, 1, 3, 0x47000000, 0, len(header), len(pm4), len(draw_fixups))
    return b".fx" + material + group + struct.pack(">6I", 2, 0, 0, 0, 0, 0) * 20 + header + pm4 + draw_fixups


class FetchInsertion(unittest.TestCase):
    def test_relocates_vertex_and_index_fixups(self):
        model = model_with_draw_fixups()
        output = insert_fetch_constant(model, 7)
        self.assertIsNotNone(output)
        groups = pass_groups(output, 35)
        size_a, size_b, size_c = groups[0][3]
        self.assertEqual((size_b, size_c), (112, 16))
        start = groups[-1][1] + size_a
        self.assertEqual(struct.unpack_from(">I", output, start + 28)[0], 0x0005482A)
        self.assertEqual(struct.unpack_from(">4I", output, start + size_b),
                         (0x080003C4, 0, 0x08000580, 0))

    def test_refuses_unrecognized_draw_fixup(self):
        model = model_with_draw_fixups()
        self.assertIsNone(insert_fetch_constant(model[:-16] + b"\x0c" + model[-15:], 7))


class HexNeighbours(unittest.TestCase):
    def test_six_around_one_hex_apart(self):
        grid = HexGrid([(0, 0), (2000, 2000)], 100)
        for cell in ((10, 10), (11, 10)):  # an even and an odd column
            cx, cz = grid.centre(*cell)
            near = grid.neighbours(*cell)
            self.assertEqual(len(set(near)), 6)
            for n in near:
                self.assertAlmostEqual(math.dist((cx, cz), grid.centre(*n)), 3 ** 0.5 * 100, places=6)

    def test_symmetric_and_inside_the_grid(self):
        grid = HexGrid([(0, 0), (700, 500)], 100)
        cells = set(grid.cells)
        for cell in grid.cells:
            for n in grid.neighbours(*cell):
                self.assertIn(n, cells)
                self.assertIn(cell, grid.neighbours(*n))
        self.assertLess(len(grid.neighbours(0, 0)), 6)


def synthetic_ribbon(points):
    """An FM4 ribbon (read_fm4's layout) of 1 m pieces at points (x, z), one FM4 cell."""
    insts = [{"pos": (x, 0.0, z), "rot": [0] * 9, "model": k, "idx": 0, "u12": 0xFFFFFFFF,
              "flags": 0, "lod": 0, "v16": 0, "u32": 0} for k, (x, z) in enumerate(points)]
    models = [{"extent": [1.0, 1.0, 1.0, 0.0], "matrix": [0.0] * 16, "textures": [], "shaders": []}
              for _ in points]
    return {"hdr": (27, 0), "ribbon": 0, "instances": insts, "models": models, "textures": [],
            "shaders": [], "cells": [{"instances": list(range(len(points)))}], "dynamic": [],
            "always": [], "prefix": "t", "b160": None, "b196": None, "names": []}


class ZoneReach(unittest.TestCase):
    """What a zone holds (FM4_PVS_ZONE_REACH, #824), on pieces scattered over 1.2 x 0.9 km."""

    def setUp(self):
        rnd = random.Random(824)
        self.points = [(rnd.uniform(0, 1200), rnd.uniform(0, 900)) for _ in range(1500)]
        self.patches = [mock.patch.object(fm4_pvs, name, False) for name in ("VISIBLE", "SKY", "GROUND")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def convert(self, reach, fits=None):
        with mock.patch.object(fm4_pvs, "ZONE_REACH", reach):
            P, zones, grid = fm4_pvs.convert(synthetic_ribbon(self.points), [], fits)
        self.reach = P["zone_reach"]
        held = [{i & 0x7FFFFFFF for i in z["instances"]} for z in zones]
        for z, h in zip(zones, held):
            self.assertEqual(len(h), len(z["instances"]))  # each instance once
            self.assertEqual(len(z["placements"]), len(z["instances"]))
        self.assertEqual(P["zones"][:-1], [(len(z["instances"]), 0, len(z["models"])) for z in zones])
        self.assertEqual(P["zones"][-1][0], fm4_pvs.POOL_ZONES * max(len(z["instances"]) for z in zones))
        return held, grid

    def near(self, grid, cell, d):
        c = grid.centre(*cell)
        return {k for k, p in enumerate(self.points) if math.dist(p, c) <= d}

    def test_own_holds_its_hex_alone(self):
        held, grid = self.convert("own")
        self.assertEqual(self.reach, "their own instances")
        for cell, h in zip(grid.cells, held):
            own = {k for k, p in enumerate(self.points) if grid.cell_of(*p) == cell}
            self.assertLessEqual(own, h)
            self.assertLessEqual(h, self.near(grid, cell, 100 + 1.5))  # cells_near: reach + 1 radius

    def test_neighbours_hold_what_is_seen_from_the_zone(self):
        own, grid = self.convert("own")
        held, grid2 = self.convert("neighbours")
        self.assertEqual(grid.cells, grid2.cells)
        for k, (cell, h) in enumerate(zip(grid.cells, held)):
            index = {c: j for j, c in enumerate(grid.cells)}
            expect = set(own[k]).union(*(own[index[n]] for n in grid.neighbours(*cell)))
            self.assertEqual(h, expect)
            # the seven hexes cover everything within two radii of the centre
            self.assertLessEqual(self.near(grid, cell, 199.0), h)
        self.assertGreater(sum(map(len, held)), 4 * sum(map(len, own)))

    def test_falls_back_to_own_when_the_pool_is_too_small(self):
        own, _ = self.convert("own")
        asked = []
        held, _ = self.convert("neighbours", lambda zones, grid: asked.append(len(zones)) or False)
        self.assertTrue(self.reach.startswith("their own instances (with their neighbours': over"))
        self.assertEqual(held, own)
        self.assertEqual(len(asked), 1 + len(fm4_pvs.NEIGHBOUR_NEAR))
        kept, _ = self.convert("neighbours", lambda zones, grid: True)
        self.assertEqual(self.reach, "their own and their neighbours' instances")
        self.assertNotEqual(kept, own)

    def test_a_shorter_reach_before_own_alone(self):
        own, grid = self.convert("own")
        whole, _ = self.convert("neighbours")
        asked = []
        held, _ = self.convert("neighbours", lambda zones, grid: asked.append(1) or len(asked) > 1)
        near = fm4_pvs.NEIGHBOUR_NEAR[0]
        self.assertIn(f"within {near:.0f} m", self.reach)
        for k, cell in enumerate(grid.cells):
            self.assertLessEqual(own[k], held[k])
            self.assertLessEqual(held[k], whole[k])
            self.assertLessEqual(self.near(grid, cell, near - 1.0), held[k])
            self.assertLessEqual(held[k] - own[k], self.near(grid, cell, near + 1.5))
        self.assertLess(sum(map(len, held)), sum(map(len, whole)))
        self.assertGreater(sum(map(len, held)), sum(map(len, own)))

    def test_own_is_never_asked_to_fit(self):
        self.convert("own", lambda zones, grid: self.fail("asked"))

    def test_default_and_bad_values(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("FM4_")}
        here = os.path.dirname(os.path.abspath(__file__))
        code = "import fm4_pvs; print(fm4_pvs.ZONE_REACH)"
        out = subprocess.run([sys.executable, "-c", code], cwd=here, env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "neighbours")
        bad = subprocess.run([sys.executable, "-c", code], cwd=here, capture_output=True, text=True,
                             env=dict(env, FM4_PVS_ZONE_REACH="all"))
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("FM4_PVS_ZONE_REACH", bad.stderr)


class StreamPool(unittest.TestCase):
    def test_blocks_of_a_zone_and_its_neighbours(self):
        grid = HexGrid([(0, 0), (500, 400)], 100)
        zones = [{"models": [k], "textures": []} for k in range(len(grid.cells))]
        F = {"prefix": "t", "textures": []}
        with tempfile.TemporaryDirectory() as d:
            for k in range(len(grid.cells)):
                with open(os.path.join(d, f"t.{k:05d}.rmb.bin"), "wb") as f:
                    f.write(b"\0" * 1024 * (k + 1))
            want = max(sum(j + 1 for j in [k] + [grid.cells.index(n) for n in grid.neighbours(*c)])
                       for k, c in enumerate(grid.cells))
            self.assertEqual(fm4_pvs.stream_blocks(F, zones, grid, d), want)

    def test_the_games_cap_with_the_margin(self):
        with mock.patch.object(fm4_pvs, "STREAM_MARGIN", 1.25):
            self.assertTrue(fm4_pvs.fits_stream_pool(52428))
            self.assertTrue(fm4_pvs.fits_stream_pool(52429))   # 65536.25 asked: 65536
            self.assertFalse(fm4_pvs.fits_stream_pool(52430))
        self.assertEqual(fm4_pvs.MAX_STREAM_BLOCKS, 65536)


class GroundCopyInTheReach(unittest.TestCase):
    """The ground's model copy (FM4_PVS_GROUND_LAYER=A, B or copy) is in every zone before its
    file is written: the reach's fit check counts the size of the file it copies (#831)."""

    def setUp(self):
        for name, value in (("VISIBLE", False), ("SKY", False), ("GROUND", True),
                            ("GROUND_LAYER", "copy"), ("STREAM_MARGIN", 1.0)):
            p = mock.patch.object(fm4_pvs, name, value)
            p.start()
            self.addCleanup(p.stop)

    def convert(self, d, reach, cap=None):
        """Small pieces (64 bytes each) and a 64 KB terrain piece, the ground's source, in d;
        the reach chosen for a pool of cap blocks; the blocks the zones need once the copy is
        written, and those they needed with the copy left out (as before #831)."""
        rnd = random.Random(831)
        points = [(rnd.uniform(0, 1200), rnd.uniform(0, 900)) for _ in range(400)]
        F = synthetic_ribbon(points)
        F["shaders"] = ["track\\terr_blnd_spec_a"]
        F["models"].append({"extent": [60.0, 1.0, 60.0, 0.0], "textures": [], "shaders": [0],
                            "matrix": [0.0] * 12 + [1150.0, 0.0, -850.0, 1.0]})
        F["instances"].append({"pos": (0.0, 0.0, 0.0), "rot": [0x3C00, 0, 0, 0, 0x3C00, 0, 0, 0, 0xBC00],
                               "model": len(points), "idx": 0, "u12": 0xFFFFFFFF, "flags": 0,
                               "lod": 0, "v16": 0, "u32": 0})
        F["cells"][0]["instances"].append(len(points))
        files = [f"t.{m:05d}.rmb.bin" for m in range(len(F["models"]))]
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))
        for k, f in enumerate(files):
            with open(os.path.join(d, f), "wb") as out:
                out.write(b"\0" * (64 if k < len(points) else 64 * 1024))
        fits = cap and (lambda zones, grid: fm4_pvs.fits_stream_pool(
            fm4_pvs.stream_blocks(F, zones, grid, d)))
        with mock.patch.object(fm4_pvs, "ZONE_REACH", reach), \
                mock.patch.object(fm4_pvs, "GROUND_COPIES", {}), \
                mock.patch.object(fm4_pvs, "MAX_STREAM_BLOCKS", cap or fm4_pvs.MAX_STREAM_BLOCKS):
            P, zones, grid = fm4_pvs.convert(F, files, fits)
            self.assertEqual(list(fm4_pvs.GROUND_COPIES), [f"t.{len(points) + 1:05d}.rmb.bin"])
            pending = fm4_pvs.stream_blocks(F, zones, grid, d)
            with mock.patch.dict(fm4_pvs.GROUND_COPIES, clear=True):
                left_out = fm4_pvs.stream_blocks(F, zones, grid, d)
            fm4_pvs.write_ground_copies(d)
        written = fm4_pvs.stream_blocks(F, zones, grid, d)
        self.assertEqual(pending, written)
        return P["zone_reach"], written, left_out

    def test_the_chosen_reach_fits_once_the_copy_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            reach, full, full_left_out = self.convert(d, "neighbours")
            self.assertEqual(reach, "their own and their neighbours' instances")
            _, own, _ = self.convert(d, "own")
            # The copy is most of a zone's blocks. Counted as nothing (before #831), the whole
            # neighbours' reach fitted any pool their own instances alone fit, and needed far
            # more blocks once the copy was written.
            self.assertLessEqual(full_left_out, own)
            self.assertGreater(full, full_left_out + 32)
            for cap in range(own, full):
                reach, need, _ = self.convert(d, "neighbours", cap)
                self.assertLessEqual(need, cap, reach)
                self.assertNotEqual(reach, "their own and their neighbours' instances")
            reach, need, _ = self.convert(d, "neighbours", full)
            self.assertEqual(reach, "their own and their neighbours' instances")


if __name__ == "__main__":
    unittest.main()
