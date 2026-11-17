"""Synthetic tests of the FM1 car import (#930): fh1_carbin, fh1_carbuild, fh1_xds, fm1_cars and
fm1_import. No game file is read or included."""
import math
import os
import random
import sqlite3
import struct
import sys
import types
import unittest
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carbin_dump  # noqa: E402
import fh1_carbin as fc  # noqa: E402
import fh1_carbuild as cb  # noqa: E402
import fh1_xds  # noqa: E402
import fm1_cars  # noqa: E402
import fm1_import  # noqa: E402
import fm4_carbin  # noqa: E402
from test_fm4_cars import fm4_carbin_file  # noqa: E402


def fh1_model(sections, lod0=False):
    """An FH1 car model (root 5): sections [(name, [(material, [(x, y, z)...])...])], each mesh
    a strip of its points; lod0: its meshes at LOD 0, as in a _lod0 or _cockpit model."""
    c = fc.parse(fm4_carbin.convert(fm4_carbin_file(None, sections), "test/donor"))
    if lod0:
        for s in c.sections:
            s.lod0, s.lod = s.lod, fc.Pool(0, 0, [0], b"")
            for sb in s.subs:
                sb.lod = 0
    return c


def quad(x, y, z, w=0.2):
    return [(x, y, z), (x + w, y, z), (x, y + w, z), (x + w, y + w, z)]


def box_piece(section, material, lod, lo, hi):
    """A closed box, outward normals per corner, FH1 winding."""
    corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    nrm = [cb.unit(tuple(p[k] - c[k] for k in range(3))) for p in corners]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    tris = [t for a, b, cc, d in faces for t in ((a, b, cc), (a, cc, d))]
    uv = [((i % 4) / 3.0, (i // 4) / 1.0) for i in range(8)]
    return cb.Piece(section, material, lod, corners, nrm, uv, uv, tris)


class CarbinTests(unittest.TestCase):
    def test_round_trip_and_draw_lists(self):
        data = fm4_carbin.convert(fm4_carbin_file(None, [("body", [("body", quad(0, 0, 0))]),
                                                         ("glassF", [("window", quad(0, 1, 0))])]), "k")
        c = fc.parse(data)
        self.assertEqual([s.name for s in c.sections], ["body", "glassF"])
        self.assertEqual(fc.build(c), data)
        _, arrays, _ = fc.draw_lists(c.header)
        self.assertEqual(arrays, [[], [0, 1 << 16]])  # one LOD: no transparent entries, both opaque
        c.header = fc.set_draw_lists(c.header, [[1 << 16], [0]])
        self.assertEqual(fc.draw_lists(c.header)[1], [[1 << 16], [0]])
        self.assertTrue(carbin_dump.parse(fc.build(c))["parsed_to_end"])

    def test_strip_to_list_restarts(self):
        tris = fc.strip_to_list([0, 1, 2, 3, fc.RESTART32, 4, 5, 6], fc.RESTART32)
        self.assertEqual(tris, [(0, 1, 2), (2, 1, 3), (4, 5, 6)])


class BuilderTests(unittest.TestCase):
    def test_frame_quaternion_keeps_the_normal(self):
        rnd = random.Random(1)
        for _ in range(500):
            n = cb.unit(tuple(rnd.uniform(-1, 1) for _ in range(3)))
            t = tuple(rnd.uniform(-1, 1) for _ in range(3))
            q = cb.frame_quaternion(n, t)
            back = fc.quat_normal(q)
            self.assertLess(max(abs(a - b) for a, b in zip(back, n)), 1e-9)

    def test_strips_keep_every_triangle_and_winding(self):
        rnd = random.Random(2)
        rows = []  # a 5 x 5 grid as strips, one a row: the order FM1's strips give
        for r in range(5):
            rows += [v for c in range(6) for v in (r * 6 + c, r * 6 + c + 6)] + [fc.RESTART32]
        grid = fc.strip_to_list(rows, fc.RESTART32)
        soup = [tuple(rnd.sample(range(30), 3)) for _ in range(40)]
        for tris in (grid, soup, grid[::-1]):
            strip = cb.to_strip(tris)
            back = fc.strip_to_list(strip, fc.RESTART32)

            def canon(t):
                k = t.index(min(t))
                return t[k:] + t[:k]
            self.assertEqual(sorted(canon(t) for t in back), sorted(canon(tuple(t)) for t in tris))
        self.assertLess(len(cb.to_strip(grid)), 3 * len(grid))  # joined, not one strip per triangle

    def test_orient_turns_triangles_against_their_normals(self):
        pos = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        nrm = [(0, 0, 1)] * 3
        self.assertEqual(cb.orient(pos, nrm, [(0, 1, 2)]), [(0, 1, 2)])
        self.assertEqual(cb.orient(pos, nrm, [(0, 2, 1)]), [(1, 2, 0)])

    def test_sh_model_fits_a_linear_function_of_the_normal(self):
        """SH0 is signed: a constant term and linear ones of both signs, as FH1's cars have."""
        rnd = random.Random(3)
        coef = [(75, 8, 20, 5), (-25, -3, -50, 4), (3, 0, -4, 80), (-5, -82, 1, 0)]  # like the RX-7's
        data = b""
        for _ in range(400):
            n = cb.unit(tuple(rnd.uniform(-1, 1) for _ in range(3)))
            q = cb.frame_quaternion(n, (0.3, 0.2, 0.9))
            qs = [round(c * 32767) for c in q]
            nn = fc.quat_normal(qs)
            sh = [round(a + b * nn[0] + c * nn[1] + d * nn[2]) for a, b, c, d in coef]
            data += struct.pack(">4h2H2H4h4b", 0, 0, 0, 0, 0, 0, 0, 0, *qs, *sh)
        pool = fc.Pool(400, 28, [1, 7], data)
        model = types.SimpleNamespace(sections=[types.SimpleNamespace(lod=pool, lod0=fc.Pool(0, 0, [0], b""))])
        sh = cb.SHModel([model])
        for n in (cb.unit((0.2, 0.9, -0.3)), cb.unit((-0.7, -0.6, 0.4))):
            want = [round(a + b * n[0] + c * n[1] + d * n[2]) for a, b, c, d in coef]
            self.assertLessEqual(max(abs(x - y) for x, y in zip(sh(n), want)), 1)
        self.assertEqual(fc.vertices(pool)[0][4], struct.unpack_from(">4b", data, 24))

    def build(self):
        donor = fh1_model([
            ("body", [("body", quad(0, 0.2, 0))]), ("bumperFa", [("body", quad(0, 0.2, -2))]),
            ("bumperFb", [("body", quad(0, 0.2, -2.1))]), ("wheel", [("rim", quad(0, 0.3, 1))]),
            ("seatL", [("interior", quad(-0.4, 0.4, 0))]), ("glassF", [("window", quad(0, 0.9, -0.5))])])
        body = box_piece("body", "body", 1, (-0.9, 0.0, -2.0), (0.9, 1.2, 2.1))
        bumper = box_piece("bumperFa", "black", 1, (-0.8, 0.0, -2.2), (0.8, 0.4, -1.9))
        glass = box_piece("glassF", "window", 1, (-0.6, 0.8, -0.8), (0.6, 1.0, -0.4))
        sh = cb.SHModel([donor])
        new, report = cb.build(donor, [body, bumper, glass], "lod", sh, keep={"wheel"}, transparent={"window"})
        return donor, new, report, body

    def test_build_fills_copies_keeps_and_empties(self):
        donor, new, report, _ = self.build()
        self.assertEqual(sorted(report["filled"]), ["body", "bumperFa", "glassF"])
        self.assertEqual(report["from_stock"], ["bumperFb"])
        self.assertEqual(report["kept"], ["wheel"])
        self.assertEqual(report["emptied"], ["seatL"])
        wheel = next(s for s in new.sections if s.name == "wheel")
        self.assertEqual(fc.build(fc.Carbin(b"", [wheel], b"")), fc.build(fc.Carbin(b"", [donor.sections[3]], b"")))
        data = fc.build(new)
        self.assertEqual(fc.build(fc.parse(data)), data)
        self.assertTrue(carbin_dump.parse(data)["parsed_to_end"])
        self.assertTrue(carbin_dump.parse(fc.build(new, stripped=True))["parsed_to_end"])

    def test_build_encodes_positions_normals_and_uvs(self):
        _, new, _, body = self.build()
        sec = next(s for s in new.sections if s.name == "body")
        self.assertEqual(sec.bmin, [-b for b in sec.bmax])
        world = fc.world(sec, sec.lod)
        verts = fc.vertices(sec.lod)
        sub = sec.subs[0]
        self.assertEqual((sub.name, sub.lod, sub.index_type, sub.index_size), ("body", 1, 6, 4))
        for p, n, uv in zip(body.pos, body.nrm, body.uv0):
            k = min(range(len(world)), key=lambda i: math.dist(world[i], p))
            self.assertLess(math.dist(world[k], p), 1e-4)
            self.assertLess(math.dist(fc.quat_normal(verts[k][3]), n), 2e-3)
            u = verts[k][1][0] / 65535 * sub.uvxf[1] + sub.uvxf[0]
            v = verts[k][1][1] / 65535 * sub.uvxf[3] + sub.uvxf[2]
            self.assertLess(math.dist((u, v), uv), 1e-4)
        self.assertEqual(len(fc.strip_to_list(sub.indices, fc.RESTART32)), 12)

    def test_build_draw_lists(self):
        _, new, _, _ = self.build()
        names = [s.name for s in new.sections]
        transparent, opaque = fc.draw_lists(new.header)[1]
        drawn = lambda a: sorted((names[e >> 16], new.sections[e >> 16].subs[e & 0xFFFF].name) for e in a)  # noqa: E731
        self.assertEqual(drawn(transparent), [("glassF", "window")])
        self.assertIn(("body", "body"), drawn(opaque))
        self.assertIn(("wheel", "rim"), drawn(opaque))
        self.assertIn(("bumperFb", "black"), drawn(opaque))
        self.assertIn(("seatL", "interior"), drawn(opaque))  # emptied: one zero-area triangle, its own material

    def test_a_piece_needs_a_donor_section(self):
        donor = fh1_model([("body", [("body", quad(0, 0, 0))])])
        with self.assertRaises(ValueError):
            cb.build(donor, [box_piece("hooda", "body", 1, (0, 0, 0), (1, 1, 1))], "lod", cb.SHModel([donor]))


def random_levels(width, height, seed=5):
    rnd = random.Random(seed)
    levels = []
    for i in range(max(width, height).bit_length()):
        w, h = fh1_xds.level_blocks(width, height, i)
        levels.append(bytes(rnd.randrange(256) for _ in range(w * h * 16)))
    return levels


class XdsTests(unittest.TestCase):
    def test_write_read_round_trip(self):
        levels = random_levels(128, 128)
        xds = fh1_xds.write_dxt5(levels, 128, 128)
        words = struct.unpack_from(">13I", xds)
        self.assertEqual(words[:7], fh1_xds.RESOURCE)
        self.assertEqual(words[8] & 0x3F, 0x35)
        self.assertEqual(words[9], 127 | 127 << 13)
        self.assertEqual((words[11] >> 6) & 0xF, 7)  # the last mip level
        # largest level (32 x 32 blocks), the 64 and 32 levels on their own surfaces, the rest packed
        self.assertEqual(len(xds), 52 + 4 * 32 * 32 * 16)
        self.assertEqual(words[12] >> 12, 32 * 32 * 16 // 4096)  # the mips start after the largest
        self.assertEqual(fh1_xds.read_dxt5(xds), (128, 128, levels))

    def test_packed_mip_offsets(self):
        # measured on FH1's lights.xds (512 x 512): levels 16, 8, 4, 2, 1 in one surface
        got = [fh1_xds.packed_mip_offset(512, 512, m) for m in range(3, 10)]
        self.assertEqual(got, [None, None, (16, 0), (8, 0), (4, 0), (0, 8), (0, 4)])

    def test_sizes_are_checked(self):
        with self.assertRaises(ValueError):
            fh1_xds.write_dxt5(random_levels(96, 64), 96, 64)
        with self.assertRaises(ValueError):
            fh1_xds.write_dxt5(random_levels(64, 64)[:1], 64, 64)

    def test_block_codec(self):
        flat = [(200, 100, 50, 255)] * 16
        px = fh1_xds.decode_block(fh1_xds.encode_block(flat))
        self.assertTrue(all(max(abs(a - b) for a, b in zip(p, flat[0])) <= 4 for p in px))
        ramp = [(16 * i, 255 - 16 * i, 128, 17 * i) for i in range(16)]
        px = fh1_xds.decode_block(fh1_xds.encode_block(ramp))
        # 4 colours over a 240-step ramp: 40 apart at most halfway between two (DXT's own limit)
        self.assertLessEqual(max(abs(a - b) for p, q in zip(px, ramp) for a, b in zip(p, q)), 45)
        self.assertLessEqual(max(abs(p[3] - q[3]) for p, q in zip(px, ramp)), 20)  # 8 alpha levels

    def test_mips_from(self):
        block = fh1_xds.encode_block([(10, 200, 30, 255)] * 16)
        levels = fh1_xds.mips_from(block * 16 * 16, 64, 64)
        self.assertEqual(len(levels), 7)
        for i, lv in enumerate(levels):
            w, h = fh1_xds.level_blocks(64, 64, i)
            self.assertEqual(len(lv), w * h * 16)
            p = fh1_xds.decode_block(lv[:16])[0]
            self.assertLessEqual(max(abs(a - b) for a, b in zip(p, (10, 200, 30, 255))), 4)


def fm1_xpr(width, height, levels, depth=1):
    """An FM1 texture bundle with one DXT5 texture: levels (2-D) or, for a volume, its slices'
    largest levels (interleaved block by block)."""
    log = lambda n: n.bit_length() - 1  # noqa: E731
    if depth > 1:
        n = len(levels[0]) // 16
        payload = b"".join(levels[s][i * 16:(i + 1) * 16] for i in range(n) for s in range(depth))
        fmt = 3 << 4 | 0x0F << 8 | 1 << 16 | log(width) << 20 | log(height) << 24 | log(depth) << 28
    else:
        payload = b"".join(levels)
        fmt = 2 << 4 | 0x0F << 8 | len(levels) << 16 | log(width) << 20 | log(height) << 24
    hsize = 12 + 20 + 4
    return (b"XPR0" + struct.pack("<II", hsize + len(payload), hsize) + struct.pack("<5I", 4 << 16 | 1, 0, 0, fmt, 0)
            + struct.pack("<I", 0xFFFFFFFF) + payload)


def fm1_chunk(name, payload=b"", children=b"", typ=0):
    body = name.encode() + b"\0" + payload + children
    return struct.pack("<II", 8 + len(body), typ) + body


def fm1_vertex(pos, nrm=(0.0, 1.0, 0.0), uv=(0.25, 0.75)):
    return struct.pack("<3h3hHH2h", *[round(p / fm1_cars.POS) for p in pos], *[round(c * 32767) for c in nrm],
                       0, 0, *[round(c * 32768) for c in uv])


def fm1_part(name, verts, subparts):
    """subparts: [(lod, material, strip indices)]."""
    kids = fm1_chunk("Verts", struct.pack("<I", len(verts)) + b"".join(verts))
    kids += fm1_chunk("Damage", struct.pack("<I", 0)) + fm1_chunk("Pivots", struct.pack("<I", 0))
    kids += fm1_chunk("Attributes", bytes(12)) + struct.pack("<I", len(subparts))
    for lod, mat, idx in subparts:
        block = bytes(16) if lod == 1 else bytes([7, 1, 0, 0] * 4)  # FM1 fills it at the other LODs
        kids += fm1_chunk("Subpart", struct.pack("<4I", lod, mat, 6, len(idx)) + block
                          + struct.pack("<%dH" % len(idx), *idx), typ=2)
    return fm1_chunk("Part", struct.pack("<II", 0x1234, len(name)) + name.encode(), kids)


def fm1_car(parts, wheels, flares=()):
    kids = fm1_chunk("Attribs", struct.pack("<II", 1, 5) + struct.pack("<12f", *[c for w in wheels for c in w]))
    kids += fm1_chunk("Driver", bytes(8)) + struct.pack("<I", len(parts)) + b"".join(parts)
    for pos, d, size, colour in flares:
        kids += fm1_chunk("Flare", struct.pack("<2I7f", 0, colour, size, *pos, *d))
    return fm1_chunk("Car", b"", kids)


def fm1_cab(files, whole=False):
    """An FM1 archive: each file its own zlib stream, or (whole) the archive one zlib stream."""
    names, entries = b"", []
    for name, data in files.items():
        stored = data if whole else zlib.compress(data)
        entries.append((len(names), stored, len(data)))
        names += name.encode() + b"\0"
    n = len(files)
    out = struct.pack("<4I", fm1_cars.CAB_MAGIC, 15, n + 9, 0 if whole else 1).ljust(0x206, b"\0")
    off = 0x206 + 24 * n + 4 + len(names)
    for name_off, stored, size in entries:
        out += struct.pack("<6I", 0, off, len(stored), size, name_off, 0)
        off += len(stored)
    out += struct.pack("<I", 0) + names + b"".join(s for _, s, _ in entries)
    return zlib.compress(out) if whole else out


WHEELS = [(-0.74, 0.17, 1.23), (0.74, 0.17, 1.23), (0.73, 0.18, -1.17), (-0.73, 0.18, -1.17)]


def box_part(name, lo, hi, material=1, lods=(1, 2)):
    corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    verts = [fm1_vertex(p, cb.unit(tuple(p[k] - c[k] for k in range(3))), (i / 8, 0.5)) for i, p in enumerate(corners)]
    strip = [0, 1, 2, 3, 6, 7, 4, 5, 0, 1]  # a band around four faces
    return fm1_part(name, verts, [(lod, material, strip) for lod in lods])


class Fm1Tests(unittest.TestCase):
    def car(self):
        parts = [box_part("body_LOD1", (-0.85, 0.0, -2.0), (0.85, 1.1, 2.1)),
                 box_part("glassF_LOD1", (-0.6, 0.8, 0.4), (0.6, 1.0, 0.8), material=2),
                 box_part("steering_wheel", (-0.5, 0.45, 0.2), (-0.2, 0.75, 0.3), material=14, lods=(1,)),
                 box_part("driver01", (-0.6, 0.0, -0.3), (-0.1, 1.0, 0.8))]
        flares = [((0.5, 0.52, 1.83), (0.0, 0.0, 1.0), 0.5, 0xFFFFFFFF), ((-0.5, 0.52, 1.83), (0.0, 0.0, 1.0), 0.5, 0xFFFFFFFF)]
        return fm1_car(parts, WHEELS, flares)

    def test_cab_reads_its_files(self, whole=False):
        files = {"cars\\test\\car.carbin": self.car(), "cars\\test\\other.bin": b"x" * 100}
        path = os.path.join(self.tmp(), "car_test.cab")
        with open(path, "wb") as f:
            f.write(fm1_cab(files, whole))
        cab = fm1_cars.Cab(path)
        self.assertEqual(cab.read("cars/test/car.carbin"), files["cars\\test\\car.carbin"])
        self.assertEqual(cab.read("CARS\\TEST\\OTHER.BIN"), b"x" * 100)

    def test_cab_stored_as_one_zlib_stream(self):
        self.test_cab_reads_its_files(whole=True)

    def tmp(self):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def test_read_car(self):
        car = fm1_cars.read_car(self.car())
        self.assertEqual([p.name for p in car.parts], ["body_LOD1", "glassF_LOD1", "steering_wheel", "driver01"])
        body = car.parts[0]
        self.assertEqual(len(body.verts), 8)
        pos, nrm, uv = body.verts[7]
        self.assertLess(math.dist(pos, (0.85, 1.1, 2.1)), 2e-4)
        self.assertAlmostEqual(math.dist(nrm, (0, 0, 0)), 1.0, places=6)
        self.assertEqual(uv, (round(7 / 8 * 32768) / 32768, 0.5))
        # the indices follow the 16-byte block, at LOD 1 and at the others, all of them
        self.assertEqual(body.subparts, [(1, 1, (0, 1, 2, 3, 6, 7, 4, 5, 0, 1)), (2, 1, (0, 1, 2, 3, 6, 7, 4, 5, 0, 1))])
        self.assertEqual([tuple(round(c, 3) for c in w) for w in car.wheels], WHEELS)
        self.assertEqual(len(car.flares), 2)
        self.assertEqual((tuple(round(c, 3) for c in car.flares[0][0]), car.flares[0][1], car.flares[0][2]),
                         ((0.5, 0.52, 1.83), (0.0, 0.0, 1.0), 0.5))

    def test_strip_triangles(self):
        self.assertEqual(fm1_cars.strip_triangles([0, 1, 2, 3, 3, 4, 5]), [(0, 1, 2), (2, 1, 3), (3, 4, 5)])

    def test_stock_parts(self):
        names = ["body_LOD1", "bumperFa_LOD1", "bumperFb_LOD1", "hood_LOD1", "driver01", "headlightLup_LOD1",
                 "headlightLdown_LOD1", "wingraceA_LOD1", "11_caliperLF", "cagerace_LOD1"]
        car = fm1_cars.Car([fm1_cars.Part(n, [((0, 0, 0), (0, 1, 0), (0, 0))]) for n in names], [], [])
        self.assertEqual([p.name for p in fm1_cars.stock_parts(car)],
                         ["body_LOD1", "bumperFa_LOD1", "hood_LOD1", "headlightLdown_LOD1"])

    def test_stock_look_from_tooldata(self):
        tool = """<Upgrades><RearBumper><P0 Name="Bumper 0"><Parts P0="bumperRa_LOD" P1="exhaustLa_LOD"/></P0>
                  <P1 Name="Bumper 1"><Parts P0="bumperRb_LOD" P1="exhaustLd_LOD"/></P1></RearBumper>
                  <Wing><P0 Name="Wing 0"><Parts P0="wingNone_LOD"/></P0><P1><Parts P0="winga_LOD"/></P1></Wing>
                  <Antenna> </Antenna></Upgrades>"""
        looks = fm1_cars.read_looks(tool)
        self.assertEqual(looks, {"RearBumper": [["bumperra", "exhaustla"], ["bumperrb", "exhaustld"]],
                                 "Wing": [["wingnone"], ["winga"]]})
        parts = [box_part(n + "_LOD1", (0, 0, 0), (1, 1, 1)) for n in
                 ("body", "bumperRa", "bumperRb", "exhaustLa", "exhaustLd", "wingNone", "winga", "Head", "Left_forearm", "driver01")]
        car = fm1_cars.read_car(fm1_car(parts, WHEELS))
        car.looks = looks
        self.assertEqual(sorted(fm1_cars.base_name(p.name) for p in fm1_cars.stock_parts(car)),
                         ["body", "bumperRa", "exhaustLa", "wingNone"])  # no options, no driver
        car.looks = None  # no tooldata: the lettered guess
        self.assertNotIn("Head", [fm1_cars.base_name(p.name) for p in fm1_cars.stock_parts(car)])

    def test_section_for(self):
        names = ["body", "bumperFa", "bumperFb", "trunk", "hooda", "glassF", "glassLF", "exhausta", "skirtLb", "interior"]
        want = {"bumperFa_LOD1": "bumperFa", "bumperF_LOD1": "bumperFa", "hood_LOD1": "hooda", "wing_LOD1": "trunk",
                "trunk_m_LOD1": "trunk", "glassLR_LOD1": "body", "skirtL_LOD1": "body", "headlightLdown_LOD1": "body",
                "exhaust_LOD1": "exhausta", "glassLF_LOD1": "glassLF", "something_LOD1": "body", "glassR_LOD1": None,
                "mirrorL_LOD1": None}
        for part, sec in want.items():
            self.assertEqual(fm1_cars.section_for(part, names), sec, part)
        self.assertIsNone(fm1_cars.section_for("body_LOD1", ["interior", "a_pillar"]))
        s2000 = ["body", "exhaustLa", "exhaustRa", "glassLHL", "glassRHL", "glassLTL"]
        for part, sec in {"exhaustLa_LOD1": "exhaustLa", "exhaustRa_LOD1": "exhaustRa", "glassLHL_LOD1": "glassLHL",
                          "glassRHLd_LOD1": "glassRHL", "glassRTL_LOD1": "body"}.items():
            self.assertEqual(fm1_cars.section_for(part, s2000), sec, part)
        self.assertEqual(fm1_cars.section_for("exhaustLa_LOD1", ["body", "exhausta"]), "exhausta")  # one exhaust
        self.assertEqual(fm1_cars.section_for("glass_LF_LOD1", ["body", "glassLF"]), "glassLF")  # the Stratos's spelling
        self.assertTrue(fm1_cars.SKIP_PARTS.search("caliperLF"))

    def test_material_for(self):
        self.assertEqual(fm1_cars.material_for(14, {"interior", "black"}), "interior")
        self.assertEqual(fm1_cars.material_for(14, {"plastic", "black"}), "plastic")
        self.assertEqual(fm1_cars.material_for(99, {"plastic", "black"}), "black")
        self.assertEqual(fm1_cars.material_for(3, {"tail_light", "taillight2S"}), "taillight2S")

    def test_placement_puts_the_axles_on_the_donors(self):
        car = fm1_cars.Car([], WHEELS, [])
        donor = [(-0.747, 0.115, 1.285), (0.747, 0.115, 1.285), (0.732, 0.115, -1.107), (-0.732, 0.115, -1.107)]
        dy, dz = fm1_cars.placement(car, donor)
        front = fm1_cars.to_fh1(((WHEELS[0][0] + WHEELS[1][0]) / 2, WHEELS[0][1], WHEELS[0][2]), dy, dz)
        rear = fm1_cars.to_fh1((0, WHEELS[2][1], WHEELS[2][2]), dy, dz)
        mid = (-(1.285 + -1.107) / 2)  # the donor's axle midpoint, model z (forward is -z)
        self.assertAlmostEqual((front[2] + rear[2]) / 2, mid, places=6)
        self.assertAlmostEqual((front[1] + rear[1]) / 2, 0.115, places=6)
        self.assertLess(front[2], rear[2])  # the front is -z

    def test_lods(self):
        """FH1 LOD k gets FM1 LOD k; past the car's last FM1 LOD, its last one (the farthest) again.
        A part FM1 stops drawing earlier (the steering wheel) stops in FH1 too."""
        car = fm1_cars.read_car(self.car())
        parts = fm1_cars.stock_parts(car)
        names = ["body", "glassF", "steering_wheel"]
        got = {}
        for p in fm1_cars.pieces(car, parts, "main", names, {"body", "window", "interior"}, 0.0, 0.0, {}):
            got.setdefault(p.section, set()).add(p.lod)
        self.assertEqual(got, {"body": {1, 2, 3, 4, 5}, "glassF": {1, 2, 3, 4, 5}, "steering_wheel": {1}})
        lod0 = fm1_cars.pieces(car, parts, "lod0", names, {"body", "window", "interior"}, 0.0, 0.0, {})
        self.assertEqual({p.lod for p in lod0}, {0})
        self.assertEqual(len(lod0), 3)  # FM1's LOD 1 of each part

    def test_xpr_textures(self):
        hi = random_levels(64, 64, seed=8)
        w, h, layers = fm1_cars.dxt5_layers(fm1_xpr(64, 64, hi))
        self.assertEqual((w, h, layers), (64, 64, [hi]))
        clean, dirty = random_levels(64, 64, seed=9)[0], random_levels(64, 64, seed=10)[0]
        w, h, layers = fm1_cars.dxt5_layers(fm1_xpr(64, 64, [clean, dirty], depth=2))
        self.assertEqual(layers, [[clean], [dirty]])

    def test_textures_from_fm1s_liveries(self):
        hi = random_levels(64, 64, seed=8)
        clean, dirty = random_levels(64, 64, seed=9)[0], random_levels(64, 64, seed=10)[0]
        files = {"cars\\test\\car.carbin": self.car(),
                 "cars\\test\\liveries\\standard\\livery0hi.xpr": fm1_xpr(64, 64, hi),
                 "cars\\test\\liveries\\standard\\livery0.xpr": fm1_xpr(64, 64, [clean, dirty], depth=2)}
        path = os.path.join(self.tmp(), "car_test.cab")
        with open(path, "wb") as f:
            f.write(fm1_cab(files))
        cab = fm1_cars.Cab(path)
        self.assertEqual(fm1_cars.cab_name(None, cab), "test")
        out = fm1_cars.textures(cab, "test", ["Nodamage.xds", "Damage.xds", "lights_LOD0.xds", "plastic2_NRM.xds"])
        self.assertEqual(sorted(out), ["Damage.xds", "Nodamage.xds", "lights_LOD0.xds"])  # the normal map stays
        self.assertEqual(fh1_xds.read_dxt5(out["Nodamage.xds"]), (64, 64, hi))  # FM1's own levels, as they are
        self.assertEqual(out["lights_LOD0.xds"], out["Nodamage.xds"])
        w, h, levels = fh1_xds.read_dxt5(out["Damage.xds"])
        self.assertEqual((w, h, levels[0], len(levels)), (64, 64, dirty, 7))  # the dirty slice, levels made

    def test_light_list(self):
        def lamp(x0):  # a lamp's face, a vertex every 2 cm
            return [(x0 + 0.02 * i, 0.5 + 0.02 * j, -2.0) for i in range(11) for j in range(6)]
        pts = lamp(-0.7) + lamp(0.5)
        verts = [fm1_vertex(p) for p in pts]
        half = len(pts) // 2
        lamps = fm1_part("bumperR_LOD1", verts, [(1, 3, list(range(half))), (1, 3, list(range(half, len(pts)))),
                                                 (2, 3, [0, 1, 2])])
        car = fm1_cars.read_car(fm1_car([lamps], WHEELS, [((0.5, 0.52, 1.83), (0, 0, 1), 0.5, 0), ((-0.5, 0.52, 1.83), (0, 0, 1), 0.5, 0)]))
        data = fm1_cars.light_list(car, car.parts, 0.0, 0.0, 1995)
        n = struct.unpack_from(">I", data)[0]
        self.assertEqual(n, 4 + 6)  # a head lamp and its flare a side; a tail light and two brake sprites a side
        recs = [struct.unpack_from(">3f3h14B", data, 4 + 32 * i) for i in range(n)]  # [8]: the side
        head = [r for r in recs if r[5] > 0]
        self.assertEqual(sorted((round(r[0], 2), r[8]) for r in head[::2]), [(-0.5, 1), (0.5, 2)])  # side 1 = left
        self.assertTrue(all(abs(r[2] - (1.83 - 0.03)) < 1e-4 for r in head[::2]))  # the list's z points forward
        tail = [r for r in recs if r[5] < 0]
        self.assertEqual(sorted({(round(r[0], 2), r[8]) for r in tail}), [(-0.6, 1), (0.6, 2)])
        self.assertTrue(all(r[2] < -1.9 for r in tail))

    def test_build_models(self):
        car = fm1_cars.read_car(self.car())
        main = fh1_model([("body", [("body", quad(0, 0.2, 0))]), ("bumperFa", [("body", quad(0, 0.2, -2))]),
                          ("glassF", [("window", quad(0, 0.9, -0.5))]), ("steering_wheel", [("interior", quad(0.4, 0.5, -0.2))]),
                          ("wheel", [("rim", quad(0, 0.3, 1))]), ("seatL", [("interior", quad(-0.4, 0.4, 0))])])
        cockpit = fh1_model([("interior", [("plastic", quad(0, 0.5, 0))]), ("steering_wheel", [("plastic", quad(0.4, 0.5, -0.2))]),
                             ("a_pillar", [("body", quad(0, 0.9, -1))]), ("glassF", [("window", quad(0, 0.9, -0.5))])], lod0=True)
        donor_wheels = [(-0.747, 0.115, 1.285), (0.747, 0.115, 1.285), (0.732, 0.115, -1.107), (-0.732, 0.115, -1.107)]
        out = fm1_cars.build_models(car, {"CAR.carbin": main, "CAR_cockpit.carbin": cockpit}, donor_wheels)
        new, report = out["CAR.carbin"]
        self.assertEqual(sorted(report["filled"]), ["body", "glassF", "steering_wheel"])
        self.assertEqual(sorted(report["emptied"]), ["bumperFa", "seatL"])
        self.assertEqual(report["kept"], ["wheel"])
        self.assertNotIn("driver01", report["parts"])
        lods = sorted({sb.lod for s in new.sections if s.name == "body" for sb in s.subs})
        self.assertEqual(lods, [1])  # only the LODs the donor draws the section at
        sw = next(s for s in new.sections if s.name == "steering_wheel")
        dy, dz = fm1_cars.placement(car, donor_wheels)
        centre = fm1_cars.to_fh1((-0.35, 0.6, 0.25), dy, dz)
        self.assertLess(math.dist(sw.offset, centre), 2e-4)  # FM1's wheel's centre (FM1 steps 0.15 mm)
        self.assertTrue(carbin_dump.parse(fc.build(new))["parsed_to_end"])
        new, report = out["CAR_cockpit.carbin"]
        self.assertIn("steering_wheel", report["filled"])
        self.assertEqual({sb.name for s in new.sections if s.name == "steering_wheel" for sb in s.subs}, {"plastic"})
        self.assertEqual({sb.lod for s in new.sections for sb in s.subs}, {0})
        self.assertTrue(carbin_dump.parse(fc.build(new))["parsed_to_end"])


def fh1_db():
    """A small FH1 database: two cars (424 the donor, 309 another), their body, an upgrade list and
    its physics rows (car x 1000 + n), an event, a make."""
    db = sqlite3.connect(":memory:")
    db.executescript("""
        create table Data_Car (Id INT PRIMARY KEY, Year INT, MakeID INT, DisplayName TEXT, ModelShort TEXT,
            MediaName TEXT, ModelId INT, Thumbnail TEXT, BaseCost INT);
        create table Data_CarBody (Id INT PRIMARY KEY, ModelWheelbase REAL);
        create table List_UpgradeCarBody (Id INT PRIMARY KEY, Ordinal INT, Level INT, CarBodyID INT, IsStock INT);
        create table List_UpgradeCarBodyHood (Id INT PRIMARY KEY, CarBodyID INT, Level INT, IsStock INT);
        create table List_UpgradeEngine (Id INT PRIMARY KEY, Ordinal INT, Level INT, EngineID INT, IsStock INT);
        create table List_SpringDamperPhysics (SpringDamperPhysicsID INT PRIMARY KEY, Ordinal INT, DefRideHeight REAL);
        create table List_UpgradeSpringDamper (Id INT PRIMARY KEY, Ordinal INT, Level INT, FrontSpringDamperPhysicsID INT);
        create table CarPartPositions (ID INT PRIMARY KEY, Ordinal INT, PartId INT, PartPosX REAL, PartPosY REAL, PartPosZ REAL);
        create table Combo_Colors (ID INT PRIMARY KEY, Ordinal INT, Sequence INT, Metallic INT, RGB INT,
            BaseTexture TEXT, DamageTexture TEXT, ColorClassId INT);
        create table EventParticipants (ID INT PRIMARY KEY, EventID INT, Ordinal INT);
        create table Combo_Engines (EngineComboID INT PRIMARY KEY, Ordinal INT, EngineID INT, Stock INT);
        create table List_CarMake (ID INT PRIMARY KEY, DisplayName TEXT, ManufacturerCode TEXT, IconPathBase TEXT);
        create table List_Wheels (ID INT PRIMARY KEY, MediaName TEXT, Diameter REAL, IconPath TEXT);
    """)
    db.executemany("insert into List_Wheels values (?, ?, 16, ?)", [(163, "MAZ_RX7_97", "MAZ_RX7_97"), (164, "VW_Corrado_95", "x")])
    for car, media in ((424, "MAZ_RX7_97"), (309, "VW_Corrado_95")):
        db.execute("insert into Data_Car values (?, 1997, 26, '_&3444125869', '_&3444160674', ?, ?, ?, 18000)",
                   (car, media, car, "game:\\Media\\UI\\Textures\\Thumbnails\\Thumbnail_%d.xds" % car))
        db.execute("insert into Data_CarBody values (?, 2.39)", (car,))
        db.execute("insert into List_UpgradeCarBody values (?, ?, 0, ?, 1)", (car * 1000, car, car))
        db.execute("insert into List_UpgradeCarBodyHood values (?, ?, 1, 1)", (car * 1000 + 1, car))
        db.execute("insert into List_UpgradeEngine values (?, ?, 0, 178, 1)", (car * 1000, car))
        db.execute("insert into List_SpringDamperPhysics values (?, ?, 0.2)", (car * 1000 + 2, car))
        db.execute("insert into List_UpgradeSpringDamper values (?, ?, 0, ?)", (car * 1000 + 1, car, car * 1000 + 2))
        for part, pos in enumerate([(-0.58, 0.44, 1.75), (0.58, 0.44, 1.75), (-0.56, 0.54, -1.88), (0.56, 0.54, -1.88),
                                    (0.43, 0.50, 0.19), (-0.75, 0.115, 1.285), (0.75, 0.115, 1.285), (0.73, 0.115, -1.11),
                                    (-0.73, 0.115, -1.11), (0.40, 0.61, 0.19)]):
            db.execute("insert into CarPartPositions values (?, ?, ?, ?, ?, ?)", (car * 1000 + part, car, part) + pos)
        db.execute("insert into Combo_Colors values (?, ?, 1, 0, 11865873, 'nodamage.tga', 'damage.tga', 14)",
                   (car * 1000 + 1, car))
    db.execute("insert into Combo_Colors values (1, 1, 1, 0, 3570000, 'nodamage.tga', 'damage.tga', 9)")
    db.execute("insert into EventParticipants values (7, 3, 424)")
    db.executemany("insert into Combo_Engines values (?, ?, 178, 1)", [(212, 424), (213, 309)])
    db.execute("insert into List_CarMake values (49, '_&857956309', 'TOY', 'Toyota')")
    db.commit()  # an open write transaction would hold up a backup of it
    return db


def engine_db():
    """FH1's engine tables in small: engine 178 (a twin-turbo rotary) and drivetrain 177 with a
    car 424 on them, two torque curves, a flywheel, turbos, a gearbox and the powertrains."""
    db = sqlite3.connect(":memory:")
    curve_cols = ", ".join("v%d REAL" % i for i in range(6))
    db.executescript("""
        create table Data_Engine (EngineID INT PRIMARY KEY, [EngineMass-kg] REAL, MediaName TEXT, ConfigID INT,
            CylinderID INT, Compression REAL, AspirationID_Stock INT, [StockBoost-bar] REAL, MomentInertia REAL,
            EngineName TEXT, Rotary INT);
        create table Data_Drivetrain (DrivetrainID INT PRIMARY KEY, DrivetypeID INT, EngineMountingDirection INT);
        create table List_UpgradeEngine (Id INT PRIMARY KEY, Ordinal INT, Level INT, EngineID INT, IsStock INT);
        create table List_UpgradeDrivetrain (Id INT PRIMARY KEY, Ordinal INT, DrivetrainID INT, PowertrainId INT, IsStock INT);
        create table Combo_Engines (EngineComboID INT PRIMARY KEY, Ordinal INT, EngineID INT, Stock INT);
        create table List_UpgradeEngineCamshaft (Id INT PRIMARY KEY, EngineID INT, Level INT, IsStock INT, RedlineRPM REAL,
            StallRPM REAL, StartRPM REAL, RedlineWearSpeed REAL, TorqueCurveMaxRPM REAL, NumRPMEntriesArray INT,
            TorqueCurveFullThrottleID INT);
        create table List_TorqueCurve (TorqueCurveID INT PRIMARY KEY, TorqueScale REAL, NumTorqueValues INT, %s);
        create table List_UpgradeEngineFlywheel (Id INT PRIMARY KEY, EngineID INT, Level INT, IsStock INT, MomentInertia REAL);
        create table List_UpgradeEngineDisplacement (Id INT PRIMARY KEY, EngineID INT, Level INT, IsStock INT, Disp INT);
        create table List_UpgradeEngineTurboSingle (Id INT PRIMARY KEY, EngineID INT, Level INT, IsStock INT,
            ManufacturerID INT, MassDiff REAL, WeightDistDiff REAL, Price INT, MomentInertia REAL, MaxScale REAL,
            PowerMaxScale REAL, MinScale REAL, PowerMinScale REAL, RobScale REAL);
        create table List_UpgradeEngineTurboTwin (Id INT PRIMARY KEY, EngineID INT, Level INT, IsStock INT,
            ManufacturerID INT, MassDiff REAL, WeightDistDiff REAL, Price INT, MomentInertia REAL, MaxScale REAL,
            PowerMaxScale REAL, MinScale REAL, PowerMinScale REAL, RobScale REAL);
        create table List_UpgradeEngineDSC (Id INT PRIMARY KEY, EngineID INT, Level INT, IsStock INT, ManufacturerID INT,
            MassDiff REAL, Price INT, ZeroRPMScale REAL, RedlineRPMScale REAL, RobScale REAL, TorqueDropOffRPM0 REAL);
        create table List_UpgradeEngineIntercooler (Id INT PRIMARY KEY, EngineID INT, Level INT, IsStock INT, MaxScaleScale REAL);
        create table List_UpgradeDrivetrainTransmission (Id INT PRIMARY KEY, DrivetrainID INT, Level INT, IsStock INT,
            MomentInertia REAL, GearShiftTime REAL, FinalDriveRatio REAL, NumGears INT, %s);
        create table Powertrains (Id INT PRIMARY KEY, DrivetrainId INT, EnginePlacementId INT);
    """ % (curve_cols, ", ".join("GearRatio%d REAL" % i for i in range(9))))
    db.execute("insert into Data_Engine values (178, 130, 'MAZ_RX7_97', 4, 8, 9.0, 3, 0.68, 0.1, 'R2TT', 1)")
    db.execute("insert into Data_Drivetrain values (177, 2, 0)")
    db.execute("insert into List_UpgradeEngine values (424000, 424, 0, 178, 1)")
    db.execute("insert into List_UpgradeDrivetrain values (424000, 424, 177, 1, 1)")
    db.execute("insert into Combo_Engines values (179, 424, 178, 1)")
    for lv in range(2):
        db.execute("insert into List_UpgradeEngineCamshaft values (?, 178, ?, ?, 7500, 800, 4500, 0.01, 10000, 6, ?)",
                   (178000 + lv, lv, int(lv == 0), 178000 + 2 * lv))
        db.execute("insert into List_TorqueCurve values (?, 230, 6, 0.6, 0.8, 1.0, 0.9, 0.5, -2.63)", (178000 + 2 * lv,))
        db.execute("insert into List_UpgradeEngineFlywheel values (?, 178, ?, ?, ?)", (178000 + lv, lv, int(lv == 0), 0.3 - 0.1 * lv))
    db.execute("insert into List_UpgradeEngineDisplacement values (178000, 178, 0, 1, 1308)")
    db.execute("insert into List_UpgradeEngineTurboTwin values (178000, 178, 1, 1, 0, 0, 0, 0, 16.8, 1.34, 104.4, 0.89, 10, 0.94)")
    db.executemany("insert into List_UpgradeEngineIntercooler values (?, 178, ?, ?, ?)",
                   [(178000, 1, 1, 1.0), (178002, 2, 0, 1.025), (178003, 3, 0, 1.06)])
    for lv in (1, 2, 3):
        db.execute("insert into List_UpgradeEngineTurboSingle values (?, 178, ?, 0, 149, 7, 0.002, 2700, 35, 1.5, 152, 0.92, 10, 0.92)",
                   (178000 + lv, lv))
    for lv in range(4):
        db.execute("insert into List_UpgradeDrivetrainTransmission values (?, 177, ?, ?, 0.012, 0.2, 4.1, 6, -3.3, 3.5, 2.0, 1.4, 1.0, 0.7, -1, -1, -1)",
                   (177000 + lv, lv, int(lv == 0)))
    db.executemany("insert into Powertrains values (?, ?, ?)", [(1, 2, 1), (2, 2, 2), (5, 3, 2)])
    db.commit()
    return db


def fm1_engine_data():
    """FM1's engine (fm1_engine's shape): a 4-cylinder turbo, mid-engined, rear-wheel drive."""
    def tuning(lv):
        return {"data": {"RedlineRPM": 7000 + 333 * lv, "StallRPM": 800, "StartRPM": 4257, "RedlineWearSpeed": 0.01},
                "max_rpm": 5000, "n": 5, "full_scale": 209 + lv, "full": [0.6, 0.9, 1.0, 0.8, 0.35, 0, 0],
                "zero": [0.1, 0, -1, -2, -2.63, -3, -4]}
    turbo = {"l%d" % k: {"OnOff": "ON", "Inertia": 100.0 + k, "MaxScale": 1.4 + k / 10, "PowerMaxScale": 100.0 + k,
                         "MinScale": 0.93, "RobScale": 0.92} for k in range(4)}
    gears = {"data": {"Inertia": 0.0117, "GearShiftTime": 0.17, "FinalDriveRatio": 4.29},
             "ratios": [-3.2, 3.23, 1.91, 1.32, 0.92, 0.73, -1, -1]}
    supercharger = {"l%d" % k: {"OnOff": "OFF", "ZeroRPMScale": 1.3 + k / 10, "RedlineRPMScale": 1.3 + k / 10,
                                "RobScale": 0.94} for k in range(4)}
    return {"model": {"drive": 3, "placement": 2}, "aspiration": "turbo",
            "base": {"Mass": 195.0, "Inertia": 0.4}, "tuning": {k: tuning(k) for k in range(4)}, "turbo": turbo,
            "supercharger": supercharger,
            "parts": {"intakeexhaust": {0: 1.0, 1: 1.03}, "intercooler": {0: 1.0, 1: 1.04}},
            "stock": {"intakeexhaust": 1}, "transmission": {k: gears for k in range(3)}}


class SteeringTests(unittest.TestCase):
    def rows(self, rows):
        block = bytearray(0x100)
        for r, v in rows.items():
            struct.pack_into(">4f", block, 16 * r, *v, 0.0)
        return b"head" + bytes(block) + b"tail"

    def test_main_layout_moves_with_the_wheel(self):
        h = self.rows({0: (0.426, 0.499, -0.212), 1: (0.017, 0.233, 0.972), 2: (0.236, 0.566, -0.207),
                       3: (0.621, 0.566, -0.207), 4: (0.381, 0.19, 0.246), 5: (0.001, 0.32, -0.056)})
        out = fm1_cars.move_driver_controls(h, 4, (-0.346, 0.58, -0.334), (0.0, 0.24, 0.97))
        rows = [struct.unpack_from(">3f", out, 4 + 16 * r) for r in range(6)]
        d = (-0.346 - 0.426, 0.58 - 0.499, -0.334 + 0.212)
        self.assertEqual([round(c, 4) for c in rows[0]], [-0.346, 0.58, -0.334])  # the pivot on the wheel
        self.assertEqual([round(c, 4) for c in rows[1]], [0.0, 0.24, 0.97])
        for r, old in ((2, (0.236, 0.566, -0.207)), (4, (0.381, 0.19, 0.246))):  # the hands and seat follow
            self.assertEqual([round(c, 4) for c in rows[r]], [round(o + dd, 4) for o, dd in zip(old, d)])
        self.assertEqual([round(c, 4) for c in rows[5]], [0.001, 0.32, -0.056])  # the middle stays
        self.assertEqual((out[:4], out[-4:]), (b"head", b"tail"))

    def test_cockpit_layout_and_unset_blocks(self):
        h = self.rows({6: (0.555, 0.608, -0.188), 12: (0.399, 0.553, -0.171), 13: (0.0, 0.296, 0.955)})
        out = fm1_cars.move_driver_controls(h, 4, (-0.3, 0.6, -0.3), (0.0, 0.2, 0.98))
        self.assertEqual([round(c, 3) for c in struct.unpack_from(">3f", out, 4 + 16 * 12)], [-0.3, 0.6, -0.3])
        self.assertEqual([round(c, 3) for c in struct.unpack_from(">3f", out, 4 + 16 * 6)], [-0.144, 0.655, -0.317])
        blank = self.rows({1: (0.0, 0.0, 1.0)})
        self.assertEqual(fm1_cars.move_driver_controls(blank, 4, (1, 2, 3), (0, 1, 0)), blank)

    def test_wheel_axis_is_the_plane_normal(self):
        """A ring in a plane tipped 15 degrees back, with lopsided normals: the axis is the plane's."""
        tilt = math.radians(15)
        axis = (0.0, math.sin(tilt), math.cos(tilt))
        u, v = (1.0, 0.0, 0.0), (0.0, math.cos(tilt), -math.sin(tilt))
        pos = [tuple(0.18 * (math.cos(a) * u[k] + math.sin(a) * v[k]) for k in range(3))
               for a in [i * math.pi / 8 for i in range(16)]]
        tris = [(0, i, i + 1) for i in range(1, 15)]
        piece = cb.Piece("steering_wheel", "plastic", 1, pos, [(0.6, 0.0, 0.8)] * 16, [(0, 0)] * 16, [(0, 0)] * 16, tris)
        got = fm1_cars.wheel_axis([piece], (0.0, 0.3, 0.95))
        self.assertLess(math.dist(got, axis), 1e-6)
        got = fm1_cars.wheel_axis([piece], (0.0, -0.3, -0.95))  # its sign follows `towards`
        self.assertLess(math.dist(got, tuple(-c for c in axis)), 1e-6)


class ThumbnailTests(unittest.TestCase):
    def test_xds_round_trip(self):
        import fm1_thumbnail as ft
        header = struct.pack(">13I", 3, 1, 0, 0, 0, 0xFFFF0000, 0xFFFF0000, 0x86000002, 0x86,
                             (ft.WIDTH - 1) | (ft.HEIGHT - 1) << 13, 0xC14, 0, 0xA00)
        template = header + bytes(ft.WIDTH * ft.HEIGHT * 4)
        rgba = bytes((x * 7 + y) % 256 for y in range(ft.HEIGHT) for x in range(ft.WIDTH * 4))
        xds = ft.to_xds(rgba, template)
        self.assertEqual(xds[:52], header)
        self.assertEqual(ft.from_xds(xds), (ft.WIDTH, ft.HEIGHT, rgba))
        with self.assertRaises(ValueError):
            ft.to_xds(rgba, template[:36] + struct.pack(">I", 0x3FF | 0x3FF << 13) + template[40:])  # 1024 x 1024

    def test_render_a_car(self):
        """A box car on four box wheels: the main view sits in its box, the side view nose up."""
        import fm1_thumbnail as ft
        car = fm1_cars.read_car(fm1_car([box_part("body_LOD1", (-0.85, 0.1, -2.0), (0.85, 1.1, 2.1), lods=(1,))], WHEELS))
        wheel = fm1_cars.read_car(fm1_car([box_part("wheel_LOD1", (-0.1, -0.3, -0.3), (0.1, 0.3, 0.3), material=11, lods=(1,))],
                                          WHEELS)).parts
        rgba = ft.thumbnail(car, wheel, (200, 20, 20), None, None)
        alpha = [rgba[i + 3] for i in range(0, len(rgba), 4)]
        xs = [i % ft.WIDTH for i, a in enumerate(alpha) if a == 255]
        main = [x for x in xs if x < 600]
        self.assertGreaterEqual(min(main), ft.MAIN_BOX[0] - 2)
        self.assertLessEqual(max(main), ft.MAIN_BOX[1] + 2)
        side = [i for i, a in enumerate(alpha) if a == 255 and i % ft.WIDTH >= 600]
        rows = [i // ft.WIDTH for i in side]
        self.assertGreater(max(rows) - min(rows), max(i % ft.WIDTH for i in side) - min(i % ft.WIDTH for i in side))  # turned


class EngineTests(unittest.TestCase):
    SPEC = {"config": "I", "cylinders": 4, "displacement": 1998, "compression": 8.5, "boost": 0.9, "name": "I4T   -   Test"}

    def rows(self, eng=None, spec=None):
        db = engine_db()
        self.addCleanup(db.close)
        rows = {"Data_Car": [{"Id": 55239, "Displacement": 1308, "NumGears": 6}],
                "List_UpgradeEngine": [{"Id": 55239000, "Ordinal": 55239, "Level": 0, "EngineID": 178, "IsStock": 1}],
                "List_UpgradeDrivetrain": [{"Id": 55239000, "Ordinal": 55239, "DrivetrainID": 177, "PowertrainId": 1, "IsStock": 1}],
                "Combo_Engines": [{"EngineComboID": 180, "Ordinal": 55239, "EngineID": 178, "Stock": 1}]}
        ids = fm1_import.engine_rows(db, rows, eng or fm1_engine_data(), spec or self.SPEC, 55239, "TOY_BorderMR2_95")
        return db, rows, ids

    def test_an_engine_and_drivetrain_of_its_own(self):
        db, rows, ids = self.rows()
        self.assertEqual(ids, (55239, 55239))
        e = rows["Data_Engine"][0]
        self.assertEqual((e["EngineID"], e["MediaName"], e["ConfigID"], e["CylinderID"], e["AspirationID_Stock"], e["Rotary"]),
                         (55239, "TOY_BorderMR2_95", 3, 1, 2, 0))
        self.assertAlmostEqual(e["MomentInertia"] + rows["List_UpgradeEngineFlywheel"][0]["MomentInertia"], 0.4)
        self.assertEqual(rows["List_UpgradeEngine"][0]["EngineID"], 55239)
        self.assertEqual(rows["Combo_Engines"][0]["EngineID"], 55239)
        self.assertEqual((rows["List_UpgradeDrivetrain"][0]["DrivetrainID"], rows["List_UpgradeDrivetrain"][0]["PowertrainId"]),
                         (55239, 2))
        car = rows["Data_Car"][0]
        self.assertEqual((car["EngineConfigID"], car["CylinderID"], car["AspirationTypeId"], car["Displacement"],
                          car["EnginePlacementID"], car["DriveTypeID"], car["PowertrainID"], car["NumGears"]),
                         (3, 1, 2, 1998, 2, 2, 2, 5))
        self.assertEqual(rows["List_UpgradeEngineDisplacement"][0]["Disp"], 1998)
        self.assertNotIn(178, {r.get("EngineID") for t in rows.values() for r in t})  # the donor's rows untouched

    def test_torque_curves(self):
        _, rows, _ = self.rows()
        cams = {r["Level"]: r for r in rows["List_UpgradeEngineCamshaft"]}
        self.assertEqual((cams[1]["RedlineRPM"], cams[1]["TorqueCurveMaxRPM"], cams[1]["NumRPMEntriesArray"],
                          cams[1]["TorqueCurveFullThrottleID"]), (7333, 5000, 5, 55239001))
        curves = {r["TorqueCurveID"]: r for r in rows["List_TorqueCurve"]}
        c = curves[55239000]
        self.assertAlmostEqual(c["TorqueScale"], 209 * 1.03)  # the stock intake/exhaust (FM1's l1) folded in
        self.assertEqual([c["v%d" % i] for i in range(6)], [0.6, 0.9, 1.0, 0.8, -2.63, 0.0])  # the last point: zero throttle

    def test_turbo_and_gearbox(self):
        _, rows, _ = self.rows()
        self.assertNotIn("List_UpgradeEngineTurboTwin", {t for t, rs in rows.items() if rs})
        turbos = {r["Level"]: r for r in rows["List_UpgradeEngineTurboSingle"]}
        self.assertEqual(sorted(turbos), [1, 2, 3])
        self.assertEqual((turbos[1]["Id"], turbos[1]["IsStock"], turbos[1]["Price"]), (55239000, 1, 0))
        self.assertAlmostEqual(turbos[1]["MomentInertia"], 100 * fm1_import.TURBO_INERTIA)
        self.assertAlmostEqual(turbos[2]["MaxScale"], 1.5)  # FH1 level 2 from FM1's l1
        self.assertAlmostEqual(turbos[3]["PowerMaxScale"], 103 * fm1_import.TURBO_POWER_MAX)  # level 3 from l3
        self.assertEqual(turbos[1]["PowerMinScale"], 10.0)
        trans = {r["Level"]: r for r in rows["List_UpgradeDrivetrainTransmission"]}
        self.assertEqual([trans[0]["GearRatio%d" % i] for i in range(7)], [-3.2, 3.23, 1.91, 1.32, 0.92, 0.73, -1.0])
        self.assertEqual((trans[0]["NumGears"], trans[0]["FinalDriveRatio"]), (6, 4.29))  # 5 forward and reverse
        self.assertEqual(trans[3]["NumGears"], 6)  # FH1's race gearbox stays

    def test_three_cam_levels_stock_at_one(self):
        """Most FH1 engines: cam levels 1 (stock), 2, 3. The stock one takes FM1's l0, then l1 and l3."""
        db = engine_db()
        self.addCleanup(db.close)
        db.execute("delete from List_UpgradeEngineCamshaft")
        for lv in (1, 2, 3):
            db.execute("insert into List_UpgradeEngineCamshaft values (?, 178, ?, ?, 7500, 800, 4500, 0.01, 10000, 6, 178000)",
                       (178000 + (0 if lv == 1 else lv), lv, int(lv == 1)))
        rows = {"Data_Car": [{"Id": 55239, "Displacement": 1308, "NumGears": 6}],
                "List_UpgradeEngine": [{"Id": 55239000, "Ordinal": 55239, "Level": 0, "EngineID": 178, "IsStock": 1}],
                "List_UpgradeDrivetrain": [{"Id": 55239000, "Ordinal": 55239, "DrivetrainID": 177, "PowertrainId": 1, "IsStock": 1}]}
        fm1_import.engine_rows(db, rows, fm1_engine_data(), self.SPEC, 55239, "X")
        cams = {r["Level"]: r["RedlineRPM"] for r in rows["List_UpgradeEngineCamshaft"]}
        self.assertEqual(cams, {1: 7000, 2: 7333, 3: 7999})  # FM1's l0, l1, l3

    def test_aspiration_from_fm1s_stock_parts(self):
        eng = fm1_engine_data()
        self.assertEqual(fm1_import.fm1_aspiration(eng), "turbo")
        for k in range(4):
            eng["turbo"]["l%d" % k]["OnOff"] = "OFF"
        self.assertEqual(fm1_import.fm1_aspiration(eng), "natural")
        eng["supercharger"]["l0"]["OnOff"] = "ON"
        self.assertEqual(fm1_import.fm1_aspiration(eng), "supercharger")

    def test_natural_aspiration_has_no_forced_rows(self):
        eng = fm1_engine_data()
        eng["aspiration"] = "natural"
        _, rows, _ = self.rows(eng)
        for t in fm1_import.FORCED_TABLES + ("List_UpgradeEngineIntercooler",):
            self.assertFalse(rows.get(t), t)
        self.assertEqual((rows["Data_Engine"][0]["AspirationID_Stock"], rows["Data_Car"][0]["AspirationTypeId"]), (1, 1))

    def test_supercharger(self):
        eng = fm1_engine_data()
        eng["aspiration"] = "supercharger"
        for k in range(4):
            eng["supercharger"]["l%d" % k]["OnOff"] = "ON"
        _, rows, _ = self.rows(eng)
        sc = {r["Level"]: r for r in rows["List_UpgradeEngineDSC"]}
        self.assertEqual(sorted(sc), [1, 2, 3])
        self.assertEqual((sc[1]["Id"], sc[1]["IsStock"], sc[1]["TorqueDropOffRPM0"]), (55239000, 1, 9000.0))
        self.assertAlmostEqual(sc[1]["ZeroRPMScale"], 1.3)
        self.assertAlmostEqual(sc[3]["RedlineRPMScale"], 1.6)  # FM1's l3
        self.assertFalse(rows.get("List_UpgradeEngineTurboSingle"))
        self.assertEqual(len(rows["List_UpgradeEngineIntercooler"]), 3)
        self.assertEqual(rows["Data_Engine"][0]["AspirationID_Stock"], 5)

    def test_pick_sound_matches_the_engine(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript("""
            create table Data_Car (Id INT, MediaName TEXT, Year INT, MakeID INT);
            create table List_UpgradeEngine (Id INT, Ordinal INT, EngineID INT, IsStock INT);
            create table Data_Engine (EngineID INT, ConfigID INT, CylinderID INT, AspirationID_Stock INT);
            insert into Data_Car values (424, 'MAZ_RX7_97', 1997, 26), (441, 'NIS_SilviaSpecR_00', 2000, 31),
                                        (977, 'TOY_CelicaST185_90', 1992, 49), (500, 'TOY_Supra_98', 1998, 49);
            insert into List_UpgradeEngine values (1, 424, 178, 1), (2, 441, 195, 1), (3, 977, 900, 1), (4, 500, 901, 1);
            insert into Data_Engine values (178, 4, 8, 3), (195, 3, 1, 2), (900, 3, 1, 2), (901, 3, 3, 3);
        """)
        # a 1995 Toyota 4-cylinder turbo: the Toyota with that engine, never the rotary or the six
        self.assertEqual(fm1_import.pick_sound(db, "I", 4, 2, 49, 1995), "TOY_CelicaST185_90")
        self.assertEqual(fm1_import.pick_sound(db, "I", 4, 2, 31, 1999), "NIS_SilviaSpecR_00")  # the make first
        self.assertEqual(fm1_import.pick_sound(db, "I", 4, 2, 49, 1995, have=lambda m: m != "TOY_CelicaST185_90"),
                         "NIS_SilviaSpecR_00")  # only cars with sound files
        self.assertIsNone(fm1_import.pick_sound(db, "V", 8, 1))

    def test_a_spec_that_contradicts_fm1_is_refused(self):
        eng = fm1_engine_data()
        eng["aspiration"] = "natural"
        with self.assertRaises(fm1_import.Fm1ImportError):
            self.rows(eng, dict(self.SPEC, aspiration="twin"))


class RatingTests(unittest.TestCase):
    def classes(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("create table CarClasses (Id INT PRIMARY KEY, MaxPerformanceIndex REAL, MaxDisplayPerformanceIndex INT)")
        db.executemany("insert into CarClasses values (?, ?, ?)",
                       [(0, 0.005, 99), (1, 0.459, 200), (2, 0.539, 300), (3, 0.5975, 400), (4, 0.6505, 500)])
        return db

    def test_display_pi(self):
        db = self.classes()
        pi, cid = fm1_import.display_pi(db, 445)
        self.assertEqual(cid, 4)
        self.assertAlmostEqual(pi, 0.6213, places=3)  # the RX-7's: B 445
        pi, cid = fm1_import.display_pi(db, 317)
        self.assertEqual(cid, 3)
        self.assertAlmostEqual(pi, 0.539 + 0.1675 * 0.0585)  # a quarter below 317: the game rounds up
        with self.assertRaises(fm1_import.Fm1ImportError):
            fm1_import.display_pi(db, 600)

    def test_apply_ratings(self):
        db = self.classes()
        car = {"SpeedRating": 7.1, "ClassID": 4, "SimPeakTorque": 2.94}
        figures = {"SimPeakTorque": 2.78, "SimRedlineAngVel": 733.0}
        self.assertFalse(fm1_import.apply_ratings(db, car, figures, None))
        self.assertEqual((car["SpeedRating"], car["SimPeakTorque"]), (7.1, 2.78))  # FM1's figures even so
        self.assertTrue(fm1_import.apply_ratings(db, car, figures, fm1_import.RATINGS["bordermr2"]))
        self.assertEqual((car["SpeedRating"], car["LaunchRating"], car["ClassID"]), (3.8, 6.7, 3))

    def test_every_sim_figure_from_fm1(self):
        cols = set(fm1_import.GAMECARS_FIGURES.values())
        self.assertEqual(len(cols), 14)
        self.assertTrue(all(c.startswith("Sim") for c in cols))
        self.assertTrue(all(o % 4 == 0 and 8 <= o < fm1_import.GAMECARS_ROW for o in fm1_import.GAMECARS_FIGURES))


class PartGainTests(unittest.TestCase):
    MR2 = {"intakeexhaust": {0: 1.0, 1: 1.0313, 2: 1.0625, 3: 1.1251},
           "fuelignition": {0: 1.0, 1: 1.0648, 2: 1.1295, 3: 1.2591},
           "intercooler": {0: 1.0, 1: 1.04, 2: 1.08, 3: 1.13}}

    @staticmethod
    def rows(table, levels, stock, scales, col="TorqueScale"):
        return [{"Level": lv, "IsStock": int(lv == stock), col: s} for lv, s in zip(levels, scales)]

    def erows(self):
        return {"List_UpgradeEngineIntake": self.rows("i", [0, 1, 2, 3], 0, [1.0, 1.027, 1.056, 1.085]),
                "List_UpgradeEngineExhaust": self.rows("e", [0, 1, 2, 3], 0, [1.0, 1.040, 1.085, 1.137]),
                "List_UpgradeEngineFuelSystem": self.rows("f", [1, 2, 3], 1, [1.0, 1.08, 1.125]),
                "List_UpgradeEngineIgnition": self.rows("g", [1, 2, 3], 1, [1.0, 1.065, 1.103]),
                "List_UpgradeEngineIntercooler": self.rows("c", [1, 2, 3], 1, [1.0, 1.025, 1.06], "MaxScaleScale")}

    def test_gain_at(self):
        g = self.MR2["intercooler"]
        self.assertAlmostEqual(fm1_import._gain_at(g, 2), 1.08)
        self.assertAlmostEqual(fm1_import._gain_at(g, 2.5), (1.08 * 1.13) ** 0.5)
        self.assertAlmostEqual(fm1_import._gain_at({1: 1.1}, 0), 1.0)

    def test_pairs_reach_fm1s_gains(self):
        er = self.erows()
        eng = {"parts": self.MR2, "stock": {"intakeexhaust": 1, "fuelignition": 0, "intercooler": 0}}
        fm1_import.apply_part_gains(er, eng)
        level = lambda t, lv, c="TorqueScale": next(r[c] for r in er[t] if r["Level"] == lv)  # noqa: E731
        both = lambda lv: level("List_UpgradeEngineIntake", lv) * level("List_UpgradeEngineExhaust", lv)  # noqa: E731
        self.assertAlmostEqual(both(0), 1.0)
        self.assertAlmostEqual(both(3), 1.1251 / 1.0313)          # FM1's top over its stock level 1
        self.assertLess(both(1), both(2))
        self.assertLess(both(2), both(3))
        # the donor's proportion kept: the exhaust gains more than the intake
        self.assertGreater(level("List_UpgradeEngineExhaust", 3), level("List_UpgradeEngineIntake", 3))
        fuel = level("List_UpgradeEngineFuelSystem", 3) * level("List_UpgradeEngineIgnition", 3)
        self.assertAlmostEqual(fuel, 1.2591)                       # two FH1 levels reach FM1's level 3
        self.assertAlmostEqual(level("List_UpgradeEngineIntercooler", 3, "MaxScaleScale"), 1.13)
        self.assertAlmostEqual(level("List_UpgradeEngineIntercooler", 1, "MaxScaleScale"), 1.0)

    def test_no_donor_gain_split_evenly(self):
        er = {"List_UpgradeEngineIntake": self.rows("i", [0, 1], 0, [1.0, 1.0]),
              "List_UpgradeEngineExhaust": self.rows("e", [0, 1], 0, [1.0, 1.0])}
        fm1_import.apply_part_gains(er, {"parts": {"intakeexhaust": {0: 1.0, 1: 1.21}}, "stock": {}})
        self.assertAlmostEqual(er["List_UpgradeEngineIntake"][1]["TorqueScale"], 1.1)
        self.assertAlmostEqual(er["List_UpgradeEngineExhaust"][1]["TorqueScale"], 1.1)

    def test_no_fm1_part_keeps_donor(self):
        er = self.erows()
        fm1_import.apply_part_gains(er, {"parts": {}, "stock": {}})
        self.assertEqual(er["List_UpgradeEngineIntake"][3]["TorqueScale"], 1.085)


class ImportTests(unittest.TestCase):
    INFO = {"name": "BorderMR2", "display": "Border MR2 Turbo T-bar (Wiedebody)", "ordinal": 239,
            "make": "Toyota", "year": 1995, "colours": [(54, 124, 84, 1), (10, 26, 16, 0)], "price": 30000}

    def test_names_and_media_name(self):
        self.assertEqual(fm1_import.names(self.INFO),
                         ("Border MR2 Turbo T-bar Widebody (FM1)", "Toyota Border MR2 Turbo T-bar (FM1)"))
        self.assertEqual(fm1_import.media_name("TOY", self.INFO), "TOY_BorderMR2_95")
        s7 = dict(self.INFO, name="s7", year=2004)  # FM1's s7, FH1's SAL_S7_04: not the same name
        self.assertEqual(fm1_import.media_name("SAL", s7, lambda m: m.lower() == "sal_s7_04"), "SAL_S7FM1_04")
        self.assertEqual(fm1_import.names(dict(self.INFO, display="NSX", make="Acura")), ("NSX (FM1)", "Acura NSX (FM1)"))
        # FM1's name may start with the make: not twice
        self.assertEqual(fm1_import.names(dict(self.INFO, display="Honda S2000", make="Honda")), ("S2000 (FM1)", "Honda S2000 (FM1)"))
        self.assertEqual(fm1_import.names(dict(self.INFO, display="HondaIntegraR", make="Honda")),
                         ("Integra R (FM1)", "Honda Integra R (FM1)"))  # FM1's internal-style names
        self.assertEqual(fm1_import.names(dict(self.INFO, display="Ferrari355Challenge", make="Ferrari"))[0],
                         "355 Challenge (FM1)")

    def test_wheel_rows(self):
        db = self.db()
        rows = {"Data_Car": [{"Id": 55239, "StockWheelID": 163}]}
        self.assertEqual(fm1_import.wheel_rows(db, rows, 163, 55239, "TOY_BorderMR2_95"), ("MAZ_RX7_97", 55239))
        self.assertEqual(rows["List_Wheels"], [{"ID": 55239, "MediaName": "TOY_BorderMR2_95", "Diameter": 16,
                                                "IconPath": "TOY_BorderMR2_95"}])
        self.assertEqual(rows["Data_Car"][0]["StockWheelID"], 55239)
        db.execute("insert into List_Wheels values (55239, 'other', 15, 'other')")
        self.assertEqual(fm1_import.wheel_rows(db, rows, 163, 55239, "TOY_BorderMR2_95")[1], 55240)  # taken: the next free

    def test_fit_tire_moves_the_bead_and_keeps_the_tread(self):
        ring = [(x, r * math.cos(a), r * math.sin(a)) for r in (0.24, 0.32) for x in (-0.1, 0.1)
                for a in (0.0, 2.0, 4.0)]
        tire = cb.Piece("wheel", "tire", 1, ring, [(1, 0, 0)] * len(ring), [(0, 0)] * len(ring), [(0, 0)] * len(ring),
                        [(0, 1, 6), (1, 7, 6)])
        fitted = fm1_import.fit_tire(tire, 0.2)
        radii = sorted({round(math.hypot(p[1], p[2]), 4) for p in fitted.pos})
        self.assertEqual(radii, [0.2, 0.32])
        self.assertEqual([p[0] for p in fitted.pos], [p[0] for p in ring])
        self.assertEqual(fitted.uv0, tire.uv0)

    def test_wheel_pieces(self):
        """FM1's rim, barrel and blur disc at every FH1 LOD (LOD 0 from FM1's LOD 1), scaled; its
        tire left out and the donor's kept."""
        wheel = [box_part("wheel_LOD1", (-0.1, -0.3, -0.3), (0.1, 0.3, 0.3), material=11, lods=(1, 2)),
                 box_part("tire", (-0.1, -0.3, -0.3), (0.1, 0.3, 0.3), material=8, lods=(1, 2))]
        parts = fm1_cars.read_car(fm1_car(wheel, WHEELS)).parts
        donor = fh1_model([("wheel", [("rim", quad(0, 0, 0)), ("tire", quad(0, 0.1, 0))])])
        pieces = fm1_import.wheel_pieces(parts, donor.sections[0], 2.0)
        mine = [p for p in pieces if p.material == "rim"]
        self.assertEqual(sorted(p.lod for p in mine), [0, 1, 2, 3, 4, 5])
        self.assertAlmostEqual(max(abs(v[1]) for v in mine[0].pos), 0.6, places=3)
        self.assertEqual({p.material for p in pieces}, {"rim", "tire"})
        self.assertTrue(all(p.lod > 0 for p in pieces if p.material == "tire"))

    def test_clone_rows(self):
        rows = fm1_import.clone_rows(self.db(), 424, 55239)
        self.assertNotIn("EventParticipants", rows)  # the car, not the events it is in
        self.assertEqual([r["Id"] for r in rows["Data_Car"]], [55239])
        self.assertEqual(rows["Data_Car"][0]["ModelId"], 55239)
        self.assertEqual(rows["Data_CarBody"], [{"Id": 55239, "ModelWheelbase": 2.39}])  # a body of its own
        self.assertEqual(rows["List_UpgradeCarBodyHood"], [{"Id": 55239001, "CarBodyID": 55239, "Level": 1, "IsStock": 1}])
        self.assertEqual(rows["List_UpgradeEngine"][0]["EngineID"], 178)  # the donor's engine, shared
        self.assertEqual(rows["List_UpgradeSpringDamper"][0]["FrontSpringDamperPhysicsID"], 55239002)
        self.assertEqual([r["SpringDamperPhysicsID"] for r in rows["List_SpringDamperPhysics"]], [55239002])
        self.assertEqual(len(rows["CarPartPositions"]), 10)
        self.assertNotIn(309, {r.get("Ordinal") for t in rows.values() for r in t})  # the other car untouched
        # a key not made from the car's id gets the table's next free one, not the donor's
        self.assertEqual(rows["Combo_Engines"], [{"EngineComboID": 214, "Ordinal": 55239, "EngineID": 178, "Stock": 1}])

    def test_clone_rows_aero_physics(self):
        """The wing rows name List_AeroPhysics rows by the car's id x 1000 + n; that table has no car column but its
        rows are copied too (left out, the game hung loading the Stratos); the drivetrain's tables are not"""
        db = self.db()
        db.executescript("""
            create table List_AeroPhysics (AeroPhysicsID INT PRIMARY KEY, AtRest REAL);
            create table List_UpgradeRearWing (Id INT PRIMARY KEY, Ordinal INT, Level INT, AeroPhysicsID INT);
            create table List_UpgradeDrivetrainClutch (Id INT PRIMARY KEY, DrivetrainID INT, Level INT);
            insert into List_AeroPhysics values (424001, 0.5), (424002, 0.7), (309001, 0.4);
            insert into List_UpgradeRearWing values (424000, 424, 0, 424001), (424001, 424, 1, 424002);
            insert into List_UpgradeDrivetrainClutch values (424000, 424, 0);
        """)
        rows = fm1_import.clone_rows(db, 424, 55239)
        self.assertEqual(sorted(r["AeroPhysicsID"] for r in rows["List_AeroPhysics"]), [55239001, 55239002])
        self.assertEqual(sorted(r["AeroPhysicsID"] for r in rows["List_UpgradeRearWing"]), [55239001, 55239002])
        self.assertNotIn("List_UpgradeDrivetrainClutch", rows)  # engine_rows' table

    def test_clone_rows_body_ids_times_1000(self):
        """A donor whose body is its id x 1000 (FH1's Stratos: 458000): the new body is the new id x 1000 too"""
        db = self.db()
        db.execute("update List_UpgradeCarBody set CarBodyID = 424000 where Ordinal = 424")
        db.execute("update List_UpgradeCarBodyHood set CarBodyID = 424000 where Id = 424001")
        db.execute("update Data_CarBody set Id = 424000 where Id = 424")
        rows = fm1_import.clone_rows(db, 424, 55239)
        self.assertEqual([r["Id"] for r in rows["Data_CarBody"]], [55239000])
        self.assertEqual(rows["List_UpgradeCarBody"][0]["CarBodyID"], 55239000)
        self.assertEqual(rows["List_UpgradeCarBodyHood"][0]["CarBodyID"], 55239000)

    def test_car_rows(self):
        db = self.db()
        refs = ["_&%d" % (52553 << 16 | 0x8000), "_&%d" % (52553 << 16 | 0x8001)]
        lamps = {"head": [(1, (-0.5, 0.46, -1.89)), (2, (0.5, 0.46, -1.89))], "tail": [(1, (-0.44, 0.51, 1.92))]}
        rows, donor = fm1_import.car_rows(db, 424, 55239, "TOY_BorderMR2_95", self.INFO, refs, (-0.35, 0.58, -0.33), lamps)
        self.assertEqual(donor, "MAZ_RX7_97")
        car = rows["Data_Car"][0]
        self.assertEqual((car["Year"], car["MakeID"], car["MediaName"], car["DisplayName"], car["ModelShort"]),
                         (1995, 49, "TOY_BorderMR2_95", refs[0], refs[1]))
        self.assertTrue(car["Thumbnail"].endswith("Thumbnail_55239.xds"))
        self.assertEqual([(c["ID"], c["Sequence"], c["Metallic"], c["RGB"]) for c in rows["Combo_Colors"]],
                         [(55239001, 1, 1, 54 << 16 | 124 << 8 | 84), (55239002, 2, 0, 10 << 16 | 26 << 8 | 16)])
        self.assertEqual(rows["Combo_Colors"][0]["ColorClassId"], 9)  # the nearest of FH1's paints
        pos = {r["PartId"]: (round(r["PartPosX"], 3), round(r["PartPosY"], 3), round(r["PartPosZ"], 3))
               for r in rows["CarPartPositions"]}
        self.assertEqual(pos[0], (-0.5, 0.46, 1.89))  # the list's z points forward
        self.assertEqual(pos[2], (-0.44, 0.51, -1.92))
        self.assertEqual(pos[3], (0.56, 0.54, -1.88))  # no FM1 lamp found: the donor's
        self.assertEqual(pos[4], (-0.35, 0.58, 0.33))  # the steering wheel, now on the left
        self.assertEqual(pos[9], (-0.32, 0.69, 0.33))  # where it sat from the wheel, mirrored
        self.assertEqual(pos[5], (-0.75, 0.115, 1.285))  # the wheels stay the donor's
        # the rows go into a full copy of the database (a car in a patch counts as not installed)
        import fm4_cars
        d = self.tmp()
        source, path = os.path.join(d, "source.slt"), os.path.join(d, "gamedb.slt")
        with sqlite3.connect(source) as f:
            db.backup(f)
        f.close()
        fm4_cars.write_full_db(source, db, rows, path)
        p = sqlite3.connect(path)
        self.assertEqual(p.execute("select Id, MediaName from Data_Car order by Id").fetchall(),
                         [(309, "VW_Corrado_95"), (424, "MAZ_RX7_97"), (55239, "TOY_BorderMR2_95")])
        self.assertEqual(p.execute("select count(*) from CarPartPositions where Ordinal = 55239").fetchone()[0], 10)
        p.close()

    def test_physics(self):
        """FM1's weight, springs, anti-roll bars, aero and steering lock into the car's own rows."""
        rows = {
            "Data_Car": [{"CurbWeight": 12.8, "WeightDistribution": 0.5, "BodyAeroLongitudinalDrag": 137.5,
                          "TireAeroHackShouldApply": 0, "SteerMaxAngle": 42.0}],
            "List_UpgradeCarBodyWeight": [{"Level": 0, "Mass": 1284.0, "InitialMass": 1284.0, "CMHeight": 0.43,
                                           "CMBackFront": 0.5, "CMLeftRight": 0.5, "BlockDimX": 1.9, "BlockDimY": 0.77,
                                           "BlockDimZ": 4.5}],
            "List_UpgradeSpringDamper": [{"Level": 1, "FrontSpringDamperPhysicsID": 1, "RearSpringDamperPhysicsID": 101},
                                         {"Level": 4, "FrontSpringDamperPhysicsID": 4, "RearSpringDamperPhysicsID": 104}],
            "List_SpringDamperPhysics": [{"SpringDamperPhysicsID": i, "DefRideHeight": 0.2, "DefSpringRate": 38.75,
                                          "CamberChangeRatioCompression": 0.125, "CamberChangeRatioExpansion": 0.125}
                                         for i in (1, 101, 4, 104)],
            "List_UpgradeAntiSwayFront": [{"Level": 1, "AntiSwayPhysicsID": 1}],
            "List_AntiSwayPhysics": [{"AntiSwayPhysicsID": 1, "DefSwaybarStiffness": 7.44, "SwaybarDamping": 2.0}],
        }
        phys = {"weight": {0: {"Mass": 1342.6, "CM_Height": 0.39, "CM_BackFront": 0.42, "CM_LeftRight": 0.5,
                               "BlockDim_x": 1.93, "BlockDim_y": 0.83, "BlockDim_z": 4.43}},
                "suspension": {"l0": {e: {"DefRideHeight": 0.124, "DefSpringRate": r, "DefSwaybarStiffness": s,
                                          "SwaybarDamping": 2.0, "CamberChangeRatio": 0.22}
                                      for e, r, s in (("Front", 37.5, 8.09), ("Rear", 51.8, 5.86))}},
                "aero": {"LongitudinalDrag": 262.5}, "tire_aero": {"ShouldApply": True}, "steering": {"MaxAngle": 35.0}}
        fm1_import.apply_physics(rows, phys)
        car = rows["Data_Car"][0]
        self.assertAlmostEqual(car["CurbWeight"], 13.426)
        self.assertEqual((car["WeightDistribution"], car["BodyAeroLongitudinalDrag"], car["TireAeroHackShouldApply"],
                          car["SteerMaxAngle"]), (0.42, 262.5, 1, 35.0))
        self.assertEqual(rows["List_UpgradeCarBodyWeight"][0]["Mass"], 1342.6)
        s = {r["SpringDamperPhysicsID"]: r for r in rows["List_SpringDamperPhysics"]}
        self.assertEqual((s[1]["DefSpringRate"], s[101]["DefSpringRate"]), (37.5, 51.8))
        self.assertEqual(s[1]["DefRideHeight"], 0.2)  # the donor's: the model sits on its wheels
        self.assertEqual(s[1]["CamberChangeRatioExpansion"], 0.22)
        self.assertEqual(s[4]["DefSpringRate"], 38.75)  # FH1's rally level stays the donor's
        self.assertEqual(rows["List_AntiSwayPhysics"][0]["DefSwaybarStiffness"], 8.09)

    def test_string_tables(self):
        import fm4_cars
        en, de = fm4_cars.StringTable(), fm4_cars.StringTable()
        en.entries[0x8000] = ["RX-7", "IDS_DisplayName_424", 0, 1]
        de.entries[0x8001] = ["anders", "IDS_Other", 0, 2]
        refs, out = fm1_import.string_tables({"en": en, "de": de}, [("IDS_DisplayName_55239", "Border MR2 (FM1)"),
                                                                     ("IDS_ModelShort_55239", "Toyota Border MR2 (FM1)")], 52553)
        self.assertEqual(refs, ["_&%d" % (52553 << 16 | 0x8002), "_&%d" % (52553 << 16 | 0x8003)])  # free in both
        for lang in ("en", "de"):
            t = fm4_cars.StringTable(out[lang])
            self.assertEqual((t.text(refs[0]), t.text(refs[1])), ("Border MR2 (FM1)", "Toyota Border MR2 (FM1)"))
        self.assertEqual(fm4_cars.StringTable(out["en"]).text("_&%d" % (52553 << 16 | 0x8000)), "RX-7")

    def test_fm1_info(self):
        carlist = ('<carlist>\r\n  <car name="AcuraNSX" displayName="NSX" Ordinal="2" >\r\n  </car>\r\n'
                   '  <car name="BorderMR2" displayName="Border MR2 Turbo T-bar (Wiedebody)" Make="Border" Ordinal="239" >'
                   '\r\n  </car>\r\n</carlist>').encode()
        model = struct.pack("<5I", 2, 2, 0, 0, 2002).ljust(104, b"\0") + struct.pack("<9I", 239, 57, 16, 0, 1995, 30000, 32000, 29000, 27541).ljust(104, b"\0")
        make = struct.pack("<3I", 57, 5, 0) + b"Toyota".ljust(16, b"\0")
        d = self.tmp()
        os.makedirs(os.path.join(d, "media", "cabs"))
        with open(os.path.join(d, "media", "cabs", "Mega.ca2"), "wb") as f:
            f.write(fm1_cab({"cars\\carlist.xml": carlist, "forzadb\\cardb\\model.dat": model, "forzadb\\cardb\\make.dat": make}))
        liv = '<livery>\r\n<override red=%d green=%d blue=%d texture="livery0"/>\r\n<shaders name="ShaderSettings\\%s.xml"/>'
        with open(os.path.join(d, "media", "cabs", "car_bordermr2.cab"), "wb") as f:
            f.write(fm1_cab({"cars\\bordermr2\\liveries\\standard\\livery0.livery": (liv % (54, 124, 84, "metallic")).encode(),
                             "cars\\bordermr2\\liveries\\standard\\livery1.livery": (liv % (10, 26, 16, "nonmetallic")).encode()}))
        cab = fm1_cars.Cab(os.path.join(d, "media", "cabs", "car_bordermr2.cab"))
        info = fm1_import.fm1_info(d, cab, "bordermr2")
        self.assertEqual(info, dict(self.INFO))

    def db(self):
        db = fh1_db()
        self.addCleanup(db.close)
        return db

    def tmp(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        return d


if __name__ == "__main__":
    unittest.main()
