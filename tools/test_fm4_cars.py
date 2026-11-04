"""Synthetic tests of the FM4 car converter (#648): no game file is read or included."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carbin_dump  # noqa: E402
import fm4_carbin  # noqa: E402
import fm4_cars  # noqa: E402
import fm4_physdef  # noqa: E402


def _string(s):
    return struct.pack(">I", len(s)) + s.encode()


def fm4_carbin_file(vertices):
    """A one-section, one-mesh FM4 car model (root 2, base 16, buffers v2/v3, 32-byte vertices)."""
    out = struct.pack(">I", 2) + bytes(0x150)
    out += struct.pack(">II", 16, 16)               # CBaseCarModelData 16, ShaderVersion
    out += struct.pack(">II", 1, 0)                 # Emitters: none
    out += struct.pack(">I", 1) + bytes(0x60)       # DriverControls v1
    out += struct.pack(">II", 1, 5) + bytes(15 * 36 + 8) + struct.pack(">I", 0)
    out += struct.pack(">II", 1, 0)                 # BoneMappingTable
    out += struct.pack(">III", 4, 4, 1)             # draw lists, one LOD
    out += struct.pack(">I", 0)                     # alpha list
    out += struct.pack(">IHH", 1, 0, 0)             # opaque list: section 0 mesh 0
    out += struct.pack(">II", 1, 1)                 # SubModel array
    out += struct.pack(">I", 5) + struct.pack(">3f", 0, 0.5, 0) + struct.pack(">3f", -1, -1, -1)
    out += struct.pack(">3f", 1, 1, 1) + struct.pack(">f", 1.0) + bytes(12) + bytes(4)
    out += struct.pack(">IIIII", 1, 1, 0, 1, 0) + b"\0" + struct.pack(">I", 0)
    out += struct.pack(">I", 1) + _string("body")
    out += struct.pack(">III", 2, len(vertices), 32) + b"".join(vertices)
    out += struct.pack(">II", 1, 1)                 # mesh array
    out += struct.pack(">I", 3) + b"\0" + struct.pack(">16f", 0, 1, 0, 1, 0, 1, 0, 1, *([0] * 8))
    out += struct.pack(">I", 2) + _string("body") + struct.pack(">III", 1, 6, 0)
    out += struct.pack(">I", 1) + struct.pack(">12f", 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 1, 1)
    out += struct.pack(">III", 3, 3, 2) + struct.pack(">3H", 0, 1, 2) + struct.pack(">I", 0)
    out += struct.pack(">III", 2, 0, 0)             # VertexBuffer_LOD0, empty
    out += struct.pack(">I", 1) + b"\0"             # MorphTargets
    out += struct.pack(">III", 2, 0, 0) * 2
    return out + fm4_carbin.TAIL


def vertex(sh):
    return struct.pack(">4h", 100, 200, 300, 0) + bytes(4) + bytes(4) + struct.pack(">4h", 1, 2, 3, 4) + struct.pack(">4h", *sh)


class CarbinTests(unittest.TestCase):
    def test_fm4_model_reads_as_fh1(self):
        src = fm4_carbin_file([vertex((32767, -32767, 16384, 0)), vertex((0, 0, 0, 0)), vertex((-1, 1, 256, -256))])
        out = fm4_carbin.convert(src, "test/car")
        m = carbin_dump.parse(out)
        self.assertTrue(m["parsed_to_end"])
        self.assertEqual(m["kind"], 5)
        self.assertEqual(m["lights"], 0)
        self.assertEqual(m["base"]["version"], 17)
        vb = m["sections"][0]["vertex_buffer"]
        self.assertEqual((vb["version"], vb["stride"], vb["flags"]), (3, 28, 1))
        self.assertEqual(m["sections"][0]["meshes"][0]["index_buffer"]["version"], 4)
        first = out[vb["data_at"]:vb["data_at"] + 28]
        self.assertEqual(struct.unpack(">4b", first[24:28]), (127, -127, 64, 0))
        # empty buffers carry no key
        self.assertEqual(m["sections"][0]["vertex_buffer_lod0"]["flags"], 0)

    def test_positions_uvs_tangents_kept(self):
        v = vertex((1000, 2000, 3000, 4000))
        out = fm4_carbin.convert(fm4_carbin_file([v] * 3), "k")
        vb = carbin_dump.parse(out)["sections"][0]["vertex_buffer"]
        self.assertEqual(out[vb["data_at"]:vb["data_at"] + 24], v[:24])

    def test_keys_differ_by_seed_and_stripped_keeps_them(self):
        src = fm4_carbin_file([vertex((0, 0, 0, 0))] * 3)
        a = carbin_dump.parse(fm4_carbin.convert(src, "car/a"))
        b = carbin_dump.parse(fm4_carbin.convert(src, "car/b"))
        self.assertNotEqual(a["sections"][0]["vertex_buffer"]["key"], b["sections"][0]["vertex_buffer"]["key"])
        full = fm4_carbin.convert(src, "car/a")
        s = carbin_dump.parse(fm4_carbin.strip(full))
        self.assertTrue(s["parsed_to_end"])
        vb = s["sections"][0]["vertex_buffer"]
        self.assertEqual(vb["flags"], 3)
        self.assertEqual(vb["key"], a["sections"][0]["vertex_buffer"]["key"])
        self.assertEqual(s["sections"][0]["meshes"][0]["index_buffer"]["flags"], 2)

    def test_truncated_file_refused(self):
        src = fm4_carbin_file([vertex((0, 0, 0, 0))] * 3)
        with self.assertRaises(fm4_carbin.CarbinError):
            fm4_carbin.convert(src[:-40], "x")


class PhysdefTests(unittest.TestCase):
    def box(self):
        return [(x, y, z) for x in (-0.9, 0.9) for y in (0.0, 1.2) for z in (-2.1, 2.2)] + [(0, 1.3, 0)]

    def test_hull_is_a_closed_polytope_and_round_trips(self):
        verts, faces = fm4_physdef.hull_from_points(self.box())
        edges = {tuple(sorted(e)) for f in faces for e in zip(f, f[1:] + f[:1])}
        self.assertEqual(len(verts) - len(edges) + len(faces), 2)
        blob = fm4_physdef.hull_blob(verts, faces)
        h = fm4_physdef.parse_hull(blob)
        self.assertEqual(len(h["vertices"]), len(verts))
        self.assertEqual(len(h["faces"]), len(faces))
        self.assertEqual(len(h["edges"]), len(edges))
        # every point is inside (the hull is a little inset, as the game's are)
        lo, hi = fm4_physdef.extents(verts)
        self.assertLessEqual(hi[0], 0.9)
        self.assertAlmostEqual(lo[1], 0.0)

    def test_faces_wound_clockwise_from_outside(self):
        verts, faces = fm4_physdef.hull_from_points(self.box())
        c = [sum(v[i] for v in verts) / len(verts) for i in range(3)]
        for f in faces:
            a, b, d = (verts[i] for i in f[:3])
            n = fm4_physdef._cross(fm4_physdef._sub(b, a), fm4_physdef._sub(d, a))
            self.assertLess(fm4_physdef._dot(n, fm4_physdef._sub(a, c)), 1e-9)

    def test_build_writes_spheres_and_parses(self):
        xml = ('<MAXData><Collision><CollSpheres PosOffsetX="0" PosOffsetY="0.1" PosOffsetZ="0" '
               'PosScale="1" RadiusScale="2"><Sphere1 Radius="0.2" PosX="1" PosY="0" PosZ="0" Flags="1"/>'
               '<Sphere0 Radius="0.5" PosX="0" PosY="0.5" PosZ="1" Flags="2"/></CollSpheres></Collision></MAXData>')
        donor_hull = fm4_physdef.hull_from_points(self.box())
        donor = fm4_physdef.parse(fm4_physdef.write(
            {"mass": 0.01, "inertia": [2, 0, 0, 0, 3, 0, 0, 0, 1], "inverse_inertia": [0.5, 0, 0, 0, 1 / 3, 0, 0, 0, 1],
             "half_extents": [1.3, 0.7, 3.2], "bounding_radius": 4.5, "graphics_offset": [-0.5, -0.1, 1.4],
             "aabb_offset": [0.3, 0.6, -0.9]}, [], fm4_physdef.hull_blob(*donor_hull)))
        data, k = fm4_physdef.build(xml, [(p[0] * 1.1, p[1], p[2]) for p in self.box()], [donor])
        r = fm4_physdef.parse(data)
        spheres = [s for s in r["shapes"] if s["type"] == "sphere"]
        self.assertEqual([s["flags"] for s in spheres], [2, 1])
        self.assertAlmostEqual(spheres[0]["sphere"][1], 0.6, places=5)
        self.assertAlmostEqual(spheres[0]["sphere"][3], 1.0, places=5)
        self.assertEqual(r["shapes"][-1]["type"], "hull")
        self.assertEqual((r["ndefs"], r["version"], r["type"], r["children"]), (1, 13, 1, 0))
        self.assertGreater(r["half_extents"][0], 1.3)


class StringTableTests(unittest.TestCase):
    def table(self):
        t = fm4_cars.StringTable()
        t.entries = {5: ["Five", "IDS_Five", 1, 0x1111], 3: ["Three", "IDS_Three", 0, 0x2222]}
        return t

    def test_round_trip(self):
        data = self.table().encode()
        again = fm4_cars.StringTable(data)
        self.assertEqual(again.entries, self.table().entries)
        self.assertEqual(again.encode(), data)
        self.assertEqual(len(data) % 4, 0)

    def test_add_moves_a_used_key_and_keeps_known_names(self):
        t = self.table()
        self.assertEqual(t.add("IDS_New", "New", 7, 5), 6)
        self.assertEqual(t.add("IDS_Three", "x", 0, 99), 3)
        again = fm4_cars.StringTable(t.encode())
        self.assertEqual(again.text("_&%d" % (0xCD490000 | 6)), "New")
        self.assertEqual(again.entries[6][3], 7)


class FitTests(unittest.TestCase):
    def test_least_squares(self):
        X = [[x, 1.0] for x in range(10)]
        y = [2 * x + 3 for x in range(10)]
        a, b = fm4_cars._lstsq(X, y)
        self.assertAlmostEqual(a, 2)
        self.assertAlmostEqual(b, 3)


if __name__ == "__main__":
    unittest.main()
