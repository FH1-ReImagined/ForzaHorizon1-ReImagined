#!/usr/bin/env python3
"""Tests of tools/fm4_col.py (FM4 track collision in FH1's layout) on made-up cells and surface
lists: no game files. Run: python -m unittest discover -s tools -p 'test_fm4_col.py'"""
import math
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_col  # noqa: E402


def fm4_word(x, y, z):
    """A normal in FM4's vertex word: x in bits 0-10, z in bits 11-21, y in bits 22-31."""
    q = lambda v, top: int(round(v * top))
    return ((q(y, 511) & 0x3FF) << 22) | ((q(z, 1023) & 0x7FF) << 11) | (q(x, 1023) & 0x7FF)


def normal_of(a, b, c):
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    m = math.sqrt(sum(q * q for q in n))
    return tuple(q / m for q in n)


def fm4_cell(points, normals, triangles):
    """An FM4 cell: points [(x, y, z)], normals [(x, y, z)] per point, triangles [(surface,
    (i, j, k))] wound so their normal comes out of normal_of. One square listing them all."""
    hs = 0x40
    toff = hs + 32 * len(points)
    goff = toff + 32 * len(triangles)
    head = bytearray(hs)
    struct.pack_into(">4I", head, 0, len(points), len(triangles), hs, toff)
    struct.pack_into(">II", head, 0x10, 1, 1)
    struct.pack_into(">4f", head, 0x18, 0.0, 0.0, 100.0, 100.0)
    struct.pack_into(">4f", head, 0x28, 100.0, 100.0, 0.01, 0.01)
    struct.pack_into(">I", head, 0x38, goff)
    body = bytearray()
    for p, n in zip(points, normals):
        body += struct.pack(">fffII", *p, 0, fm4_word(*n)) + bytes(12)
    for surface, (i, j, k) in triangles:
        n = normal_of(points[i], points[j], points[k])
        body += struct.pack(">IIIIfffI", surface << 16, hs + 32 * i, hs + 32 * j, hs + 32 * k, *n, 0)
    body += struct.pack(">I", goff + 4)
    body += struct.pack(f">H{len(triangles)}H", len(triangles), *range(len(triangles)))
    return bytes(head + body)


def read_cell(cell):
    """([(x, y, z, word)], [(flags, (i, j, k), normal, last word)]) of a converted cell."""
    nv, nt, vbase, tbase = struct.unpack_from(">4I", cell, 0)
    verts = [struct.unpack_from(">fffI", cell, vbase + 16 * i) for i in range(nv)]
    tris = []
    for i in range(nt):
        fl, a, b, c, nx, ny, nz, last = struct.unpack_from(">IIIIfffI", cell, tbase + 32 * i)
        tris.append((fl, tuple((v - vbase) // 16 for v in (a, b, c)), (nx, ny, nz), last))
    return verts, tris


FM4_SURFACES = """<Root>
\t<SurfaceCategories>
\t\t<Car />
\t</SurfaceCategories>
\t<SurfaceTypes>
\t\t<Asphalt Category="HardWorld">
\t\t\t<Friction >
\t\t\t\t<FrictionScale value="1" />
\t\t\t</Friction>
\t\t</Asphalt>
\t\t<!-- <Removed Category="HardWorld"></Removed> -->
\t\t<Grass Category="SoftWorld"><Friction ><FrictionScale value="0.8" /></Friction></Grass>
\t    <SmallPart Category="GenCollObj">
\t        <Friction >
\t            <FrictionScale value="1" />
\t        </Friction>
\t    </SmallPart>
\t\t<SlowAsphault Category="HardWorld">
\t\t\t<Friction ><FrictionScale value="0.6" /></Friction>
\t\t</SlowAsphault>
\t\t\t<BumpyAsphaltSlight Category="HardWorld">
\t\t\t<Friction ><FrictionScale value="1" /></Friction>
\t\t</BumpyAsphaltSlight>
\t\t<BumpyAsphaltModerate Category="HardWorld" />
\t\t<SmoothBlacktop Category="HardWorld"><Friction /></SmoothBlacktop>
\t\t<InvisibleTeleportingBorder Category="RumbleStrip"><Friction /></InvisibleTeleportingBorder>
\t\t<OnlyInFm4 Category="HardWorld"><Friction /></OnlyInFm4>
\t</SurfaceTypes>
</Root>
"""
FH1_SURFACES = """<Root>
\t<SurfaceTypesList>
\t\t<SurfaceType value="Asphalt" />
\t\t<SurfaceType value="Grass" />
\t\t<SurfaceType value="SmallPart" />
\t\t<SurfaceType value="SlowAsphault" />
\t\t<SurfaceType value="ConcreteSlow" />
\t\t<SurfaceType value="BumpyAsphaltSlight" />
\t\t<SurfaceType value="BumpyAsphaltModerate" />
\t\t<SurfaceType value="BumpyConcreteExtreme" />
\t\t<SurfaceType value="Invisible" />
\t</SurfaceTypesList>
\t<SurfaceTypes>
\t\t<Asphalt Category="HardWorld"><Friction /></Asphalt>
\t</SurfaceTypes>
</Root>
"""


class SurfaceMap(unittest.TestCase):
    def test_every_entry_is_counted_whatever_its_indentation(self):
        # FM4's own list indents 7 of its 61 entries with spaces or a third tab; a reader that
        # missed them gave every later surface the number of another one (#876)
        self.assertEqual(fm4_col.child_names(FM4_SURFACES, "SurfaceTypes"),
                         ["Asphalt", "Grass", "SmallPart", "SlowAsphault", "BumpyAsphaltSlight",
                          "BumpyAsphaltModerate", "SmoothBlacktop", "InvisibleTeleportingBorder", "OnlyInFm4"])

    def test_children_only_and_no_comments(self):
        xml = "<A><B x='1'><C/><B/></B><!-- <D/> --><E/><F></F></A>"
        self.assertEqual(fm4_col.child_names(xml, "A"), ["B", "E", "F"])
        self.assertEqual(fm4_col.child_names(xml, "Missing"), [])

    def test_surfaces_go_by_name(self):
        m = fm4_col.surface_map(FM4_SURFACES, FH1_SURFACES)
        self.assertEqual(m[0], 0)   # Asphalt
        self.assertEqual(m[1], 1)   # Grass
        self.assertEqual(m[2], 2)   # the entry indented with spaces
        self.assertEqual(m[4], 5)   # the one a tab deeper: slight bumps stay slight
        self.assertEqual(m[5], 6)   # moderate stays moderate, not FH1's extreme
        self.assertEqual(len(m), 9)

    def test_fm4_surfaces_fh1_lacks_or_has_as_another(self):
        m = fm4_col.surface_map(FM4_SURFACES, FH1_SURFACES)
        self.assertEqual(m[3], 4)   # SlowAsphault: FH1's ConcreteSlow, not its bumpy namesake
        self.assertEqual(m[6], 0)   # SmoothBlacktop: Asphalt
        self.assertEqual(m[7], 0)   # the teleporting border: ground to drive on, never Invisible
        self.assertEqual(m[8], 0)   # a name neither list knows


class Normals(unittest.TestCase):
    def test_up_is_the_word_earlier_imports_carry(self):
        self.assertEqual(fm4_col.pack_normal(0.0, 1.0, 0.0), 0x001FF800)

    def test_round_trip(self):
        for n in ((0.0, 1.0, 0.0), (0.0119, 0.9998, -0.0130), (-0.3, 0.9, 0.31), (1.0, 0.0, 0.0),
                  (0.0, 0.0, -1.0), (0.2, -0.5, 0.84)):
            m = math.sqrt(sum(q * q for q in n))
            back = fm4_col.unpack_normal(fm4_col.pack_normal(*n))
            for a, b in zip(n, back):
                self.assertAlmostEqual(a / m, b, delta=0.002)

    def test_y_keeps_its_sign_whatever_z_is(self):
        # bit 21 is y's sign. The earlier packing put z's lowest bit there: every other
        # tilted normal lay flat for the game, and the car was thrown where the road tilts.
        for k in range(-40, 41):
            word = fm4_col.pack_normal(0.01, 1.0, k / 1023.0)
            self.assertEqual(word >> 21 & 1, 0, k)
            self.assertGreater(fm4_col.unpack_normal(word)[1], 0.99)

    def test_fm4_word(self):
        n = (0.0119, 0.9998, -0.0130)
        back = fm4_col.fm4_normal(fm4_word(*n))
        for a, b in zip(n, back):
            self.assertAlmostEqual(a, b, delta=0.002)
        # FM4's has y and z the other way round: read as FH1's it is not this normal
        self.assertLess(fm4_col.unpack_normal(fm4_word(*n))[1], 0.5)


class Edges(unittest.TestCase):
    def words(self, tris):
        return fm4_col.edge_words(tris, [normal_of(*t) for t in tris])

    def test_level_neighbours_and_open_edges(self):
        a = ((0, 0, 0), (0, 0, 10), (10, 0, 0))
        b = ((10, 0, 0), (0, 0, 10), (10, 0, 10))
        self.assertEqual(self.words([a, b]), [0xFFFFFF, 0xFFFFFF])
        self.assertEqual(self.words([a]), [0xFFFFFF])

    def test_a_ridge_is_marked_on_both_sides(self):
        s = math.tan(math.radians(30))  # each side falls 30 degrees: 60 between the two
        a = ((0, 0, 0), (0, 0, 10), (10, -10 * s, 0))    # falls towards +x
        b = ((0, 0, 0), (-10, -10 * s, 0), (0, 0, 10))   # falls towards -x
        byte = round(255 * math.cos(math.radians(30)))
        wa, wb = self.words([a, b])
        self.assertEqual(wa, byte << 16 | 0xFFFF)        # a's edge 0 is the ridge (corner 0 to 1)
        self.assertEqual(wb, 0xFFFF00 | byte)            # b's edge 2 (corner 2 to 0)

    def test_a_valley_and_a_gentle_ridge_are_not(self):
        s = math.tan(math.radians(30))
        a = ((0, 0, 0), (0, 0, 10), (10, 10 * s, 0))     # rises on both sides: a valley
        b = ((0, 0, 0), (-10, 10 * s, 0), (0, 0, 10))
        self.assertEqual(self.words([a, b]), [0xFFFFFF, 0xFFFFFF])
        g = math.tan(math.radians(4))                    # 8 degrees between the two: under 10
        a = ((0, 0, 0), (0, 0, 10), (10, -10 * g, 0))
        b = ((0, 0, 0), (-10, -10 * g, 0), (0, 0, 10))
        self.assertEqual(self.words([a, b]), [0xFFFFFF, 0xFFFFFF])


class Cell(unittest.TestCase):
    def setUp(self):
        tilt = (0.0119, 0.9998, -0.0130)
        self.points = [(0, 0, 0), (0, 0, 10), (10, 0, 0), (10, 0, 10), (10, 3, 0), (10, 3, 10)]
        self.normals = [tilt, tilt, tilt, tilt, (-1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)]
        # two road triangles and a wall rising from the road's edge at x = 10
        self.cell = fm4_cell(self.points, self.normals,
                             [(3, (0, 1, 2)), (5, (2, 1, 3)), (1, (2, 3, 4)), (1, (4, 3, 5))])

    def test_layout_surfaces_and_flags(self):
        out = fm4_col.convert_cell(self.cell, [0, 9, 2, 4, 5, 6])
        self.assertEqual(struct.unpack_from(">4I", out, 0)[2], fm4_col.FH1_HEADER)
        verts, tris = read_cell(out)
        self.assertEqual([t[0] for t in tris],
                         [0x0E048000, 0x0E068000, 0x08098000, 0x08098000])
        for (x, y, z, _), p in zip(verts, self.points):
            self.assertEqual((x, y, z), tuple(float(v) for v in p))

    def test_fm4s_own_normals_are_kept(self):
        verts, tris = read_cell(fm4_col.convert_cell(self.cell))
        self.assertEqual(len(verts), len(self.points))
        for (_, _, _, word), n in zip(verts, self.normals):
            for a, b in zip(fm4_col.unpack_normal(word), n):
                self.assertAlmostEqual(a, b, delta=0.003)
        # the road's corners are not straight up (the earlier default)
        self.assertNotEqual(verts[0][3], fm4_col.pack_normal(0.0, 1.0, 0.0))

    def test_the_last_word_stays_fm4s_unless_asked(self):
        verts, tris = read_cell(fm4_col.convert_cell(self.cell))
        self.assertEqual([t[3] for t in tris], [0, 0, 0, 0])
        fm4_col.FH1_EDGES = True
        try:
            verts, tris = read_cell(fm4_col.convert_cell(self.cell))
        finally:
            fm4_col.FH1_EDGES = False
        self.assertEqual(tris[0][3], 0xFFFFFF)  # a level neighbour and open edges
        self.assertEqual(tris[1][3], 0xFFFFFF)  # the wall rises from its edge
        self.assertEqual(tris[2][3], 0)         # walls keep 0
        self.assertEqual(tris[3][3], 0)

    def test_col_file(self):
        head = bytearray(0x1000)
        struct.pack_into(">I", head, 0x2C, 2)
        struct.pack_into(">I", head, 0x30, 0xABCD1234)
        struct.pack_into(">I", head, 0x4C, 1)
        struct.pack_into(">6I", head, 0x58, 0, 0, 0x1000, len(self.cell), 0, 0x1000 + len(self.cell))
        out = fm4_col.convert_col(bytes(head) + self.cell, [0, 9, 2, 4, 5, 6])
        k, cell, off, size, zero, end = struct.unpack_from(">6I", out, 0x58)
        self.assertEqual((off, end), (0x1000, len(out)))
        self.assertEqual(struct.unpack_from(">I", out, 0x28)[0], len(out))
        self.assertEqual(len(read_cell(out[off:off + size])[1]), 4)


if __name__ == "__main__":
    unittest.main()
