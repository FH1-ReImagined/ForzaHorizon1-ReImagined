"""Synthetic tests of the FM4 car converter (#648): no game file is read or included."""
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carbin_dump  # noqa: E402
import fm4_carbin  # noqa: E402
import fm4_cars  # noqa: E402
import fm4_physdef  # noqa: E402


def _string(s):
    return struct.pack(">I", len(s)) + s.encode()


def fm4_carbin_file(vertices, sections=None, morphs=None):
    """An FM4 car model (root 2, base 16, buffers v2/v3, 32-byte vertices): one "body" section
    with one mesh of the given vertices, or sections [(name, [(material, [(x, y, z)...])...])]
    with their vertices placed at those positions. morphs: FM4 morph entries (u32, one per
    vertex) for every section's LOD1-5 and LOD0 morph buffers."""
    if sections is None:
        sections = [("body", [("body", vertices)], (0, 0.5, 0), (1, 1, 1))]
    else:
        out = []
        for name, meshes in sections:
            pts = [p for _, ps in meshes for p in ps]
            c = [(min(p[k] for p in pts) + max(p[k] for p in pts)) / 2 for k in range(3)]
            h = [max(abs(p[k] - c[k]) for p in pts) or 1.0 for k in range(3)]
            enc = [(m, [struct.pack(">4h", *[round((p[k] - c[k]) / h[k] * 32767) for k in range(3)], 0) +
                        bytes(16) + bytes(8) for p in ps]) for m, ps in meshes]
            out.append((name, enc, c, h))
        sections = out
    out = struct.pack(">I", 2) + bytes(0x150)
    out += struct.pack(">II", 16, 16)               # CBaseCarModelData 16, ShaderVersion
    out += struct.pack(">II", 1, 0)                 # Emitters: none
    out += struct.pack(">I", 1) + bytes(0x60)       # DriverControls v1
    out += struct.pack(">II", 1, 5) + bytes(15 * 36 + 8) + struct.pack(">I", 0)
    out += struct.pack(">II", 1, 0)                 # BoneMappingTable
    entries = [(si, mi) for si, sec in enumerate(sections) for mi in range(len(sec[1]))]
    out += struct.pack(">III", 4, 4, 1)             # draw lists, one LOD
    out += struct.pack(">I", 0)                     # alpha list
    out += struct.pack(">I", len(entries)) + b"".join(struct.pack(">HH", *e) for e in entries)
    out += struct.pack(">II", 1, len(sections))     # SubModel array
    for name, meshes, c, h in sections:
        verts = [v for _, vs in meshes for v in vs]
        out += struct.pack(">I", 5) + struct.pack(">3f", *c) + struct.pack(">3f", *[-x for x in h])
        out += struct.pack(">3f", *h) + struct.pack(">f", 1.0) + bytes(12) + bytes(4)
        out += struct.pack(">IIIII", 1, 1, 0, 1, 0) + b"\0" + struct.pack(">I", 0)
        out += struct.pack(">I", 1) + _string(name)
        out += struct.pack(">III", 2, len(verts), 32) + b"".join(verts)
        out += struct.pack(">II", 1, len(meshes))   # mesh array
        first = 0
        for material, vs in meshes:
            idx = list(range(first, first + len(vs)))
            first += len(vs)
            out += struct.pack(">I", 3) + b"\0" + struct.pack(">16f", 0, 1, 0, 1, 0, 1, 0, 1, *([0] * 8))
            out += struct.pack(">I", 2) + _string(material) + struct.pack(">III", 1, 6, 0)
            out += struct.pack(">I", 1) + struct.pack(">12f", 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 1, 1)
            out += struct.pack(">III", 3, len(idx), 2) + struct.pack(">%dH" % len(idx), *idx) + struct.pack(">I", 0)
        out += struct.pack(">III", 2, 0, 0)         # VertexBuffer_LOD0, empty
        if morphs is None:
            out += struct.pack(">I", 1) + b"\0"     # MorphTargets
            out += struct.pack(">III", 2, 0, 0) * 2
        else:
            out += struct.pack(">I", 1) + b"\1"
            entries = struct.pack(">III", 2, len(morphs), 4) + struct.pack(">%dI" % len(morphs), *morphs)
            out += entries * 2
    return out + fm4_carbin.TAIL


def lamp(x, y, z, w=0.1, hgt=0.06):
    """A small lamp: a box centred at x, y, z with vertices every 2 cm across its face."""
    nx, ny = int(round(w / 0.02)), int(round(hgt / 0.02))
    return [(x - w / 2 + w * i / nx, y - hgt / 2 + hgt * j / ny, z + dz)
            for i in range(nx + 1) for j in range(ny + 1) for dz in (-0.01, 0.01)]


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

    @staticmethod
    def fm4_morph(dx, size, dz):
        """An FM4 morph entry: unit direction x and z fields, the size in the y field."""
        return (round(dx * 1023) & 0x7FF) | (round(size * 1023) & 0x7FF) << 11 | (round(dz * 511) & 0x3FF) << 22

    def test_fm4_morphs_become_offsets(self):
        # FM4's entries are directions with the dent's size in y (#747): read as FH1's offsets
        # they moved damaged vertices by up to 2 m.
        fm4 = [self.fm4_morph(1.0, 8 / 1023, 0.0),            # 8 steps along +x
               self.fm4_morph(0.0, -12 / 1023, -1.0),         # -12 steps along -z: +z
               self.fm4_morph(0.6, 20 / 1023, 0.0),           # y is the rest of the unit vector
               0x000003FF]                                    # no dent (FM4's filler)
        out = fm4_carbin.convert(fm4_carbin_file([vertex((0, 0, 0, 0))] * 4, morphs=fm4), "m")
        m = carbin_dump.parse(out)
        self.assertTrue(m["parsed_to_end"])
        for key in ("morph", "morph_lod0"):
            mb = m["sections"][0][key]
            self.assertEqual((mb["version"], mb["count"], mb["stride"]), (3, 4, 4))
            raw = [carbin_dump.morph_raw(v) for v in struct.unpack_from(">4I", out, mb["data_at"])]
            self.assertEqual(raw, [(8, 0, 0), (0, 0, 6), (12, 16, 0), (0, 0, 0)])
        # every converted offset stays small: FM4's unit directions are gone
        for v in struct.unpack_from(">4I", out, m["sections"][0]["morph"]["data_at"]):
            self.assertLess(max(abs(c) for c in carbin_dump.morph_entry(v)), 0.03)

    def test_fh1_morphs_not_converted_again(self):
        fm4 = [self.fm4_morph(1.0, 30 / 1023, 0.0)] * 3
        once = fm4_carbin.convert(fm4_carbin_file([vertex((0, 0, 0, 0))] * 3, morphs=fm4), "m")
        # an FH1 file (version 3 buffers) through the converter again keeps its entries
        again = fm4_carbin.convert(once, "m")
        a, b = carbin_dump.parse(once)["sections"][0], carbin_dump.parse(again)["sections"][0]
        self.assertEqual(once[a["morph"]["data_at"]:a["morph"]["data_at"] + 12],
                         again[b["morph"]["data_at"]:b["morph"]["data_at"] + 12])
        self.assertEqual(carbin_dump.morph_raw(struct.unpack_from(">I", once, a["morph"]["data_at"])[0]), (30, 0, 0))

    def test_fm4_morph_offset_edge_cases(self):
        # quantized directions a little longer than 1 are scaled back, never a NaN
        x, y, z = fm4_carbin.fm4_morph_offset(self.fm4_morph(1.0, 0.5, 1.0))
        self.assertAlmostEqual(x, z, places=3)
        self.assertEqual(y, 0.0)
        self.assertAlmostEqual((x * x + z * z) ** 0.5, 0.5, places=2)
        # the most negative field values clamp to -1
        self.assertEqual(fm4_carbin.morph_entry(-2.0, -2.0, -2.0), fm4_carbin.morph_entry(-1.0, -1.0, -1.0))

    def test_truncated_file_refused(self):
        src = fm4_carbin_file([vertex((0, 0, 0, 0))] * 3)
        with self.assertRaises(fm4_carbin.CarbinError):
            fm4_carbin.convert(src[:-40], "x")


class LightTests(unittest.TestCase):
    def model(self):
        return fm4_carbin.convert(fm4_carbin_file(None, [
            ("body", [("body", lamp(0, 0.6, 0, 1.8, 1.0)), ("tail_light", lamp(0, 1.0, 2.0, 0.3))]),
            ("headlightL", [("head_light", lamp(-0.7, 0.5, -2.0)), ("reflector", lamp(-0.6, 0.6, -1.8))]),
            ("headlightR", [("head_light", lamp(0.7, 0.5, -2.0))]),
            ("taillightL", [("tail_light", lamp(-0.6, 0.7, 2.1, 0.2)), ("reverse_light", lamp(-0.3, 0.7, 2.1))]),
            ("taillightR", [("tail_light", lamp(0.6, 0.7, 2.1, 0.2))]),
            ("bumperFrace", [("head_light", lamp(-0.8, 0.2, -2.2))]),
        ]), "lights/car")

    def records(self, data):
        n = struct.unpack_from(">I", data, 0x154)[0]
        return [data[0x158 + 32 * k:0x178 + 32 * k] for k in range(n)]

    def test_lamps_become_records(self):
        data = self.model()
        out = fm4_carbin.set_lights(data, fm4_cars.car_lights(data, 2012))
        m = carbin_dump.parse(out)
        self.assertTrue(m["parsed_to_end"])
        recs = self.records(out)
        self.assertEqual(m["lights"], len(recs))
        # (function, kind, side): headlight and flare per side, tail, brake and second brake
        # sprite per side, one reverse lamp; the middle lamp and the race bumper's are left out
        got = sorted((r[21], r[19], r[20]) for r in recs)
        self.assertEqual(got, sorted([(1, 2, 1), (1, 4, 1), (1, 2, 2), (1, 4, 2),
                                      (1, 4, 1), (2, 0, 1), (4, 0, 1), (1, 4, 2), (2, 0, 2), (4, 0, 2),
                                      (16, 0, 1)]))
        head = [r for r in recs if r[21] == 1 and r[19] == 2 and r[20] == 1][0]
        x, y, z = struct.unpack_from(">3f", head)
        self.assertAlmostEqual(x, -0.7, places=3)
        self.assertAlmostEqual(y, 0.5, places=3)
        self.assertAlmostEqual(z, 2.01 - fm4_cars.LIGHT_INSET, places=3)  # the list's z is forward
        self.assertEqual(struct.unpack_from(">3h", head, 12), (0, 0, 32767))
        brake = [r for r in recs if r[21] == 2 and r[20] == 2][0]
        self.assertLess(struct.unpack_from(">f", brake, 8)[0], -2.0)
        self.assertEqual(struct.unpack_from(">3h", brake, 12), (0, 0, -32767))
        self.assertEqual(tuple(brake[22:25]), (255, 0, 0))

    def test_headlight_kind_by_year(self):
        data = self.model()
        for year, kind in ((1969, 3), (2005, 1), (2011, 2)):
            recs = self.records(fm4_carbin.set_lights(data, fm4_cars.car_lights(data, year)))
            self.assertEqual({r[19] for r in recs if r[21] == 1 and r[19] != 4}, {kind})

    def test_lens_when_no_lamp_sections(self):
        data = fm4_carbin.convert(fm4_carbin_file(None, [
            ("body", [("body", lamp(0, 0.6, 0, 1.8, 1.0)), ("head_light", lamp(0.3, 0.4, -2.2))]),
            ("glassRHL", [("glass", lamp(0.75, 0.55, -1.6, 0.2, 0.1))]),
        ]), "lights/lens")
        recs = self.records(fm4_carbin.set_lights(data, fm4_cars.car_lights(data, 2002)))
        self.assertEqual(len(recs), 2)
        self.assertAlmostEqual(struct.unpack_from(">f", recs[0])[0], 0.75, places=3)
        self.assertEqual(recs[0][20], 2)

    def test_no_lamps_no_records(self):
        data = fm4_carbin.convert(fm4_carbin_file([vertex((0, 0, 0, 0))] * 3), "x")
        self.assertEqual(fm4_cars.car_lights(data, 2000), struct.pack(">I", 0))
        self.assertEqual(fm4_carbin.set_lights(data, struct.pack(">I", 0)), data)


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


MAKE_COLS = "ID INTEGER, DisplayName TEXT, ManufacturerCode TEXT, CountryID INT, IconPath TEXT, " \
            "IconPathLight TEXT, IconPathBase TEXT, Profile TEXT"
LOGO = "GAME:\\Media\\UI\\Textures\\UI4\\Logos\\Manufacturer_Logos_%s\\L_MAN_%s_Logo.tga"


def names(texts):
    """A string table holding {key: text}."""
    t = fm4_cars.StringTable()
    t.entries = {k: [v, "IDS_%d" % k, i, 0] for i, (k, v) in enumerate(sorted(texts.items()))}
    return t


def make_db(makes, cars):
    """A game database with List_CarMake rows (id, name key, profile) and Data_Car rows
    (id, MediaName, make id, make name key)."""
    db = sqlite3.connect(":memory:")
    db.execute("create table List_CarMake (%s)" % MAKE_COLS)
    db.execute("create table Data_Car (Id INTEGER, MediaName TEXT, MakeID INT, MakeName TEXT)")
    for mid, key, prof in makes:
        db.execute("insert into List_CarMake values (?,?,?,?,?,?,?,?)",
                   (mid, "_&%d" % (0x33230000 | key), prof[:3].upper(), 10, LOGO % ("I", prof),
                    LOGO % ("SM", prof), prof, "_&1"))
    for cid, name, mid, key in cars:
        db.execute("insert into Data_Car values (?,?,?,?)", (cid, name, mid, "_&%d" % (0x33230000 | key)))
    return db


class _NoRetune:
    def __init__(self, *a):
        pass

    def apply(self, row):
        pass


class MakeTests(unittest.TestCase):
    """#710: an FM4 make FH1 has under another id (Eagle) joins FH1's; one FH1 lacks is added."""

    def setUp(self):
        self.fm4 = make_db([(3, 1, "Audi"), (41, 2, "Renault"), (87, 3, "Eagle")],
                           [(462, "EAG_Talon_98", 87, 3), (453, "REN_Clio_03", 41, 2),
                            (100, "AUD_A1_10", 3, 1)])
        self.fh1 = make_db([(3, 1, "Audi"), (102, 9, "Eagle")], [(1519, "EAE_Speedster_12", 102, 9)])
        self.fm4_names = names({1: "Audi", 2: "Renault", 3: "Eagle"})
        self.fh1_names = names({1: "Audi", 9: "EAGLE "})

    def test_same_name_under_another_id_is_merged(self):
        merges = fm4_cars.make_merges(self.fm4, self.fh1, self.fm4_names, self.fh1_names)
        self.assertEqual(merges, {87: 102})

    def test_merged_cars_take_fh1s_make_and_its_name(self):
        merges = fm4_cars.make_merges(self.fm4, self.fh1, self.fm4_names, self.fh1_names)
        with mock.patch.object(fm4_cars, "Retune", _NoRetune):
            db = fm4_cars.Database(self.fm4, self.fh1, merges)
        db.collect([462, 453, 100])
        rows = db.finish({})
        cars = {r["Id"]: r for r in rows["Data_Car"]}
        self.assertEqual(cars[462]["MakeID"], 102)
        self.assertEqual(cars[462]["MakeName"], "_&%d" % (0x33230000 | 9))
        self.assertEqual(cars[453]["MakeID"], 41)
        self.assertEqual(cars[100]["MakeID"], 3)
        self.assertEqual([r["ID"] for r in rows["List_CarMake"]], [41])  # no second Eagle
        self.assertEqual(db.fh1_refs, {"_&%d" % (0x33230000 | 9)})


class LogoTests(unittest.TestCase):
    """#710: the logos of a make FH1 lacks come from FM4 (Renault's tile was black); FH1's own
    logos are not replaced."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: fm4_cars.ZIPS.close(self.tmp))

    def textures(self, name, members):
        path = os.path.join(self.tmp, name)
        with zipfile.ZipFile(path, "w") as z:
            for m in members:
                z.writestr(m, name.encode() + m.encode())
        return path

    def test_missing_logos_are_copied(self):
        fm4_zip = self.textures("fm4.zip", [
            "UI4/Logos/Manufacturer_Logos_LG/L_MAN_Renault_Logo.xds",
            "UI4/Logos/Manufacturer_Logos_SM/L_MAN_Renault_Logo.xds",
            "UI4/Logos/Manufacturer_Logos_LG/L_MAN_Acura_Logo.xds",
            "UI4/Logos/Manufacturer_Logos_LG/L_MAN_Peugeot_Logo.xds",
            "Thumbnails/Thumbnail_453.xds"])
        fh1_zip = self.textures("fh1.zip", [
            "UI4/Logos/Manufacturer_Logos_LG/L_MAN_Acura_Logo.xds",
            "UI4/Logos/Manufacturer_Logos_LG/L_MAN_Peugeot_Logo.xds"])
        conv = fm4_cars.Converter.__new__(fm4_cars.Converter)
        conv.out, conv.pack = os.path.join(self.tmp, "out"), None
        conv.fm4 = mock.Mock(logos=lambda: fm4_cars.logo_members(fm4_zip))
        conv.fh1 = mock.Mock(logos=lambda: set(fm4_cars.logo_members(fh1_zip)))
        makes = [{"IconPath": LOGO % ("I", "Renault"), "IconPathLight": LOGO % ("SM", "Renault")},
                 {"IconPath": LOGO % ("I", "Acura"), "IconPathLight": LOGO % ("SM", "Acura")}]
        report = {}
        conv.logos(makes, report)
        self.assertEqual(sorted(report["logos"]), [
            "UI4/Logos/Manufacturer_Logos_LG/L_MAN_Renault_Logo.xds",
            "UI4/Logos/Manufacturer_Logos_SM/L_MAN_Renault_Logo.xds"])
        got = os.path.join(conv.out, "media", "ui", "textures", "UI4", "Logos", "Manufacturer_Logos_LG",
                           "L_MAN_Renault_Logo.xds")
        with open(got, "rb") as f:
            self.assertEqual(f.read(), b"fm4.zipUI4/Logos/Manufacturer_Logos_LG/L_MAN_Renault_Logo.xds")
        self.assertFalse(os.path.exists(got.replace("Renault", "Acura")))


ET = (b'<Engine>\r\n  <VolumeAdjustments EngineVolume="0.800000" TransmissionVolume="0.600000"/>\r\n'
      b'  <GlobalCarVolume>\r\n    <PhysicsCoeff RPM="0.600000" Throttle="0.500000"/>\r\n'
      b'    <ThreePointCurve x0="0.000000" y0="0.390000" x1="0.500000" y1="0.430000" x2="1.000000" y2="0.700000"/>\r\n'
      b'  </GlobalCarVolume>\r\n  <HarmonicTunings><EmissionGroup0><Volume><Gain>\r\n'
      b'    <ThreePointCurve x0="0.000000" y0="0.320000" x1="0.500000" y1="0.460000" x2="1.000000" y2="0.700000"/>\r\n'
      b'  </Gain></Volume></EmissionGroup0></HarmonicTunings>\r\n'
      b'  <Exhaust><Upgrade L0="12N2_LP640_Exh_HT.xml"/></Exhaust>\r\n</Engine>\r\n')


HT = b'<Soundbank name="%s">\r\n  <ExhaustL>\r\n    <Loop wavebankindex="0" rpm_min="500"/>\r\n  </ExhaustL>\r\n</Soundbank>\r\n'


class EngineLevelTests(unittest.TestCase):
    """#712: an imported car keeps FM4's engine tuning, which FH1 never brought to its own cars'
    level; its GlobalCarVolume is raised, an FH1 car's tuning is left alone."""

    def test_only_the_global_volume_curve_is_raised(self):
        out = fm4_cars.louder_engine(ET)
        gcv = out[out.index(b"<GlobalCarVolume>"):out.index(b"</GlobalCarVolume>")]
        g = fm4_cars.ENGINE_GAIN
        self.assertIn(b'y0="%.6f"' % (0.39 * g), gcv)
        self.assertIn(b'y1="%.6f"' % (0.43 * g), gcv)
        self.assertIn(b'y2="1.000000"', gcv)  # 0.7 x gain, at most FH1's highest value
        self.assertIn(b'x2="1.000000"', gcv)
        self.assertIn(b'PhysicsCoeff RPM="0.600000" Throttle="0.500000"', gcv)
        self.assertEqual(out.replace(gcv, b""), ET.replace(ET[ET.index(b"<GlobalCarVolume>"):
                                                              ET.index(b"</GlobalCarVolume>")], b""))
        self.assertEqual(fm4_cars.louder_engine(ET, 1.0), ET)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: fm4_cars.ZIPS.close(self.tmp))

    def game(self, name, ets, hts=None):
        media = os.path.join(self.tmp, name, "media")
        os.makedirs(os.path.join(media, "audio", "cars", "Engines"))
        for zname, members in (("CarModelTuning.zip", {}), ("Engines/EngineTuning.zip", ets),
                               ("Engines/HarmonicTuning.zip", hts or {})):
            with zipfile.ZipFile(os.path.join(media, "audio", "cars", *zname.split("/")), "w") as z:
                for m, data in members.items():
                    z.writestr(m, data)
        return mock.Mock(media=media, update=None, pack_audio=lambda: {"cmt": {}, "et": {}, "ht": {}},
                         banks=lambda: {})

    def converter(self, fh1_ets, fm4_ets):
        conv = fm4_cars.Converter.__new__(fm4_cars.Converter)
        conv.out, conv.pack = os.path.join(self.tmp, "out"), None
        # FH1 has the harmonic tuning ET names (FH1's own, naming its own banks)
        conv.fh1 = self.game("fh1", fh1_ets, {"12N2_LP640_Exh_HT.xml": HT % b"12N2_LP640_Exh.fsb"})
        conv.fm4 = self.game("fm4", fm4_ets)
        conv.fh1db = sqlite3.connect(":memory:")
        conv.fh1db.execute("create table Data_Engine (EngineID INT, MediaName TEXT)")
        conv.fh1db.execute("create table List_UpgradeEngine (Ordinal INT, EngineID INT)")
        conv.fh1db.execute("insert into Data_Engine values (5, 'HON_NSXRGT_05')")
        conv.fh1db.execute("insert into List_UpgradeEngine values (569, 5)")
        return conv

    def et_out(self, conv, name):
        path = os.path.join(conv.out, "media", "audio", "cars", "engines", "enginetuning", name + "_ET.xml")
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return f.read()

    def test_imported_engines_get_fh1s_level_fh1_engines_do_not(self):
        conv = self.converter({"LAM_MurcielagoLP640_07_ET.xml": ET, "HON_NSXRGT_05_ET.xml": ET},
                              {"CHE_CamaroSS_10_ET.xml": ET})
        report = {}
        conv.audio("LAM_MurcielagoLP640_07", ["LAM_MurcielagoLP640_07", "HON_NSXRGT_05"], report)
        conv.audio("CHE_CamaroSS_10", ["CHE_CamaroSS_10"], report)
        conv.audio("LAM_MurcielagoLP640_07", ["LAM_MurcielagoLP640_07"], report)  # done once
        self.assertEqual(self.et_out(conv, "LAM_MurcielagoLP640_07"), fm4_cars.louder_engine(ET))
        self.assertEqual(self.et_out(conv, "CHE_CamaroSS_10"), fm4_cars.louder_engine(ET))  # FM4's copy
        self.assertIsNone(self.et_out(conv, "HON_NSXRGT_05"))  # an FH1 car's engine (a swap)
        self.assertEqual(report["louder"], 2)
        self.assertEqual(report["audio"], [("CHE_CamaroSS_10", ["CHE_CamaroSS_10_ET.xml"])])

    def test_a_traffic_cars_tuning_in_fh1_gives_way_to_fm4s(self):
        # #840: FH1's FOR_FocusST_06_ET.xml is a cut traffic car's, its real slots commented out
        conv = self.converter({"FOR_FocusST_06_ET.xml": TRAFFIC_ET, "Che_Volt_11_ET.xml": NA_ET,
                               "LAM_MurcielagoLP640_07_ET.xml": ET},
                              {"FOR_FocusST_06_ET.xml": ET, "Che_Volt_11_ET.xml": NA_ET,
                               "LAM_MurcielagoLP640_07_ET.xml": S1_ET})
        self.assertTrue(fm4_cars.traffic_tuning(TRAFFIC_ET))
        self.assertTrue(fm4_cars.traffic_tuning(NA_ET))
        self.assertFalse(fm4_cars.traffic_tuning(ET))
        self.assertFalse(fm4_cars.traffic_tuning(ET.replace(b"</Engine>", b"<!-- " + NA_SLOTS + b" -->\r\n</Engine>")))
        plan = conv.audio_plan("FOR_FocusST_06", ["FOR_FocusST_06"])
        # FM4's copy, louder, under its name; its harmonic tuning is FH1's: nothing else to bring
        self.assertEqual(plan, [("louder", "media/audio/cars/engines/enginetuning/FOR_FocusST_06_ET.xml",
                                 fm4_cars.louder_engine(ET), "FOR_FocusST_06_ET.xml")])
        report = {}
        conv.audio("FOR_FocusST_06", ["FOR_FocusST_06"], report)
        conv.audio("CHE_Volt_11", ["Che_Volt_11"], report)
        conv.audio("LAM_MurcielagoLP640_07", ["LAM_MurcielagoLP640_07"], report)
        self.assertEqual(self.et_out(conv, "FOR_FocusST_06"), fm4_cars.louder_engine(ET))
        self.assertEqual(self.et_out(conv, "Che_Volt_11"), fm4_cars.louder_engine(NA_ET))  # FM4's is NA too: FH1's
        self.assertEqual(self.et_out(conv, "LAM_MurcielagoLP640_07"), fm4_cars.louder_engine(ET))  # FH1's, as before
        self.assertEqual(report["louder"], 3)
        self.assertEqual(report["audio"], [("FOR_FocusST_06", ["FOR_FocusST_06_ET.xml"])])

    def test_fm4s_copy_brings_the_harmonic_tuning_fh1_lacks(self):
        conv = self.converter({"FOR_FocusST_06_ET.xml": TRAFFIC_ET}, {"FOR_FocusST_06_ET.xml": S1_ET})
        with self.assertRaisesRegex(fm4_cars.ConvertError, "5T1_A200_1478_Exh_HT.xml is in none of the files"):
            conv.audio_plan("FOR_FocusST_06", ["FOR_FocusST_06"])  # checked, never left naming a missing file


NA_SLOTS = (b'<EngineAmbient><Upgrade L0="NA_EngAmb_HT.xml"/></EngineAmbient>\r\n'
            b'  <Intake><Upgrade L0="NA_Int_HT.xml" L1="NA_Int_HT.xml"/></Intake>\r\n')
NA_ET = ET.replace(b"12N2_LP640_Exh_HT.xml", b"NA_Exh_HT.xml").replace(b"</Engine>", b"  " + NA_SLOTS + b"</Engine>")
TRAFFIC_ET = (ET.replace(b"  <Exhaust>", b"<!-- removing proper assignments ... civilian trafficin Horizon <Exhaust>", 1)
              .replace(b"</Exhaust>", b"</Exhaust>-->\r\n  " + NA_SLOTS
                       + b'  <Exhaust><Upgrade L0="Traffic_12N2_LP640_Exh_HT.xml"/></Exhaust>', 1))


S1 = "AUD_2_SportQuattro_86"
S1_ET = ET.replace(b"12N2_LP640_Exh_HT.xml", b"5T1_A200_1478_Exh_HT.xml")
S1_HT = HT % b"5T1_A200b_EXH.fsb"


class SoundBankTests(unittest.TestCase):
    """#776: an FM4 DLC car whose harmonic tuning names a sound bank FH1 lacks (the 1986 Audi
    Sport quattro S1's 5T1_A200b_EXH.fsb): bought, or the save's current car, it stopped the game
    with its disc read error. The import brings the bank as a loose file next to the car's zip;
    a car whose bank no file has is left out; an older import gets FH1's most similar tuning."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: fm4_cars.ZIPS.close(self.tmp))
        # FH1: its tuning archives and its loose banks (stand-ins, no game file)
        self.fh1_media = os.path.join(self.tmp, "fh1", "media")
        cars = os.path.join(self.fh1_media, "audio", "cars")
        write_zip(os.path.join(cars, "CarModelTuning.zip"), {"LAM_MurcielagoLP640_07_CMT.xml": b"cmt"})
        write_zip(os.path.join(cars, "Engines", "EngineTuning.zip"), {"LAM_MurcielagoLP640_07_ET.xml": ET})
        write_zip(os.path.join(cars, "Engines", "HarmonicTuning.zip"), {
            "12N2_LP640_Exh_HT.xml": HT % b"12N2_LP640_Exh.fsb",
            "5T1_A200_Exh_HT.xml": HT % b"5T1_A200_Exh.fsb",
            "5T1_A200_o633_Exh_HT.xml": HT % b"5T1_A200_Exh.fsb",
            "5T1_A200_Int_HT.xml": HT % b"5T1_A200_Int.fsb",
            "NA_Exh_HT.xml": HT % b"NA.fsb"})
        for bank in ("12N2_LP640_Exh.fsb", "5T1_A200_Exh.fsb", "5T1_A200_Int.fsb"):
            self.file(os.path.join(cars, "Engines", "Soundbanks", "LOD1", bank), b"FSB4" + bank.encode())
        fh1db = self.file(os.path.join(self.fh1_media, "db", "gamedb.slt"), sqlite_bytes(
            {"Data_Car": [(1017, "LAM_MurcielagoLP640_07")]},
            {"Data_Car": "Id INT, MediaName TEXT", "Data_Engine": "EngineID INT, MediaName TEXT",
             "List_UpgradeEngine": "Ordinal INT, EngineID INT"}))
        self.fh1 = mock.Mock(media=self.fh1_media, update=None, db=lambda: sqlite3.connect(fh1db))
        self.fh1db = self.fh1.db()
        self.addCleanup(self.fh1db.close)
        # FM4: a DLC pack's tuning (in its car zip) and its bank (loose next to it)
        self.fm4_bank = self.file(os.path.join(self.tmp, "dlc", "Media", "DLCZips", "29_pri_65", "Media", "audio",
                                               "cars", "Engines", "Soundbanks", "LOD1", "5T1_A200b_EXH.fsb"),
                                  b"FSB4 the S1's exhaust")
        pack_zip = write_zip(os.path.join(self.tmp, "dlc", "Media", "DLCZips", "29_pri_65.zip"), {
            fm4_cars.PACK_AUDIO["et"] + S1 + "_ET.xml": S1_ET,
            fm4_cars.PACK_AUDIO["ht"] + "5T1_A200_1478_Exh_HT.xml": S1_HT,
            fm4_cars.PACK_AUDIO["cmt"] + S1 + "_CMT.xml": b"cmt"})
        z = fm4_cars.ZIPS.get(pack_zip)
        audio = {k: {} for k in fm4_cars.PACK_AUDIO}
        for i in z.infolist():
            for k, pre in fm4_cars.PACK_AUDIO.items():
                if i.filename.startswith(pre):
                    audio[k][i.filename[len(pre):].lower()] = (pack_zip, i)
        self.fm4_banks = {"5t1_a200b_exh.fsb": (self.fm4_bank, "LOD1/5T1_A200b_EXH.fsb")}
        fm4_media = os.path.join(self.tmp, "fm4", "Media")
        os.makedirs(fm4_media)
        self.fm4 = mock.Mock(media=fm4_media, pack_audio=lambda: audio, banks=lambda: self.fm4_banks)
        self.package = os.path.join(self.tmp, "package")
        self.mod = os.path.join(self.tmp, "mod")

    def file(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def converter(self, package=True):
        conv = fm4_cars.Converter.__new__(fm4_cars.Converter)
        conv.fh1, conv.fm4, conv.fh1db, conv.out = self.fh1, self.fm4, self.fh1db, self.mod
        conv.pack = (fm4_cars.PackWriter(os.path.join(self.package, "Media", "DLCZips"), fm4_cars.PACK_STEM,
                                         fm4_cars.PACK_PRIORITY) if package else None)
        return conv

    def loose_bank(self, zip_name="fm4cars1_pri_60"):
        return os.path.join(self.package, "Media", "DLCZips", zip_name, "Media", "audio", "cars", "Engines",
                            "Soundbanks", "LOD1", "5T1_A200b_EXH.fsb")

    def import_s1(self):
        """The S1 the way run() converts it: its files into the pack, then its sound."""
        conv = self.converter()
        report = {}
        plan = conv.audio_plan(S1, [S1])
        conv.pack.add("media/cars/%s/%s.carbin" % (S1, S1.lower()), b"car")
        conv.audio_apply(S1, plan, report)
        conv.audio(S1, [S1], report)  # a second car with the same engine: nothing twice
        conv.pack.close()
        self.file(os.path.join(self.mod, "media", "db", "gamedb.slt"), sqlite_bytes(
            {"Data_Car": [(1478, S1), (1017, "LAM_MurcielagoLP640_07")], "Data_Engine": [(9, S1)],
             "List_UpgradeEngine": [(1, 1478, 9)]},
            {"Data_Car": "Id INT, MediaName TEXT", "Data_Engine": "EngineID INT, MediaName TEXT",
             "List_UpgradeEngine": "Id INT, Ordinal INT, EngineID INT"}))
        return report

    def test_bank_names(self):
        self.assertEqual(fm4_cars.bank_names(S1_HT), ["5T1_A200b_EXH.fsb"])
        self.assertEqual(fm4_cars.bank_names(b'<Soundbank name="6N1_NSX_EngAmb">'), ["6N1_NSX_EngAmb.fsb"])

    def test_the_bank_fh1_lacks_goes_next_to_the_cars_zip(self):
        report = self.import_s1()
        with open(self.loose_bank(), "rb") as f:
            self.assertEqual(f.read(), b"FSB4 the S1's exhaust")
        self.assertEqual(report["banks"], 1)
        self.assertEqual(report["audio"][0], (S1, [S1 + "_CMT.xml", S1 + "_ET.xml", "5T1_A200_1478_Exh_HT.xml",
                                                   "5T1_A200b_EXH.fsb"]))
        names = zipfile.ZipFile(os.path.join(self.package, "Media", "DLCZips", "fm4cars1_pri_60.zip")).namelist()
        self.assertIn(fm4_cars.PACK_AUDIO["ht"] + "5T1_A200_1478_Exh_HT.xml", names)
        self.assertFalse([n for n in names if n.lower().endswith(".fsb")])  # never in a zip
        self.assertEqual(fm4_cars.check_import(self.fh1, self.mod, self.package), [])

    def test_a_bank_fh1_has_is_not_copied(self):
        self.fm4_banks["5t1_a200_exh.fsb"] = (self.fm4_bank, "LOD1/5T1_A200_Exh.fsb")
        conv = self.converter()
        conv.audio("LAM_MurcielagoLP640_07", ["LAM_MurcielagoLP640_07"], {})
        self.assertEqual(conv.pack.loose, set())

    def test_a_car_whose_bank_no_file_has_is_left_out_before_anything_is_written(self):
        self.fm4_banks.clear()
        conv = self.converter()
        with self.assertRaisesRegex(fm4_cars.ConvertError, "5T1_A200b_EXH.fsb is in none of the files"):
            conv.audio_plan(S1, [S1])
        self.assertFalse(os.path.exists(self.mod))
        self.assertEqual(conv.pack.names, set())
        with self.assertRaisesRegex(fm4_cars.ConvertError, "only from a package"):
            self.fm4_banks["5t1_a200b_exh.fsb"] = (self.fm4_bank, "LOD1/5T1_A200b_EXH.fsb")
            self.converter(package=False).audio_plan(S1, [S1])

    def test_the_check_names_a_missing_bank_and_the_old_import_gets_fh1s_similar_tuning(self):
        self.import_s1()
        os.unlink(self.loose_bank())  # the import before #776 had no bank
        self.assertEqual(fm4_cars.check_import(self.fh1, self.mod, self.package), [
            ("%s (1478)" % S1, "5T1_A200_1478_Exh_HT.xml names the sound bank 5T1_A200b_EXH.fsb, which no file has")])
        fixes = fm4_cars.sound_fallbacks(self.fh1_media, self.package, self.mod)
        self.assertEqual(fixes, {"5T1_A200_1478_Exh_HT.xml": ("5T1_A200_Exh_HT.xml", HT % b"5T1_A200_Exh.fsb")})
        self.file(os.path.join(self.mod, "media", "audio", "cars", "engines", "harmonictuning",
                               "5T1_A200_1478_Exh_HT.xml"), fixes["5T1_A200_1478_Exh_HT.xml"][1])
        self.assertEqual(fm4_cars.check_import(self.fh1, self.mod, self.package), [])  # the mod's copy is read
        self.assertEqual(fm4_cars.sound_fallbacks(self.fh1_media, self.package, self.mod), {})  # once
        self.assertIsNone(fm4_cars.sound_fallbacks(os.path.join(self.tmp, "none"), self.package, self.mod))

    def test_similar_tunings(self):
        fh1 = ["5T1_A200_o633_Exh_HT.xml", "5T1_A200_Int_HT.xml", "5T1_A200_Exh_HT.xml", "2RT2_RX7_Exh_HT.xml",
               "2RN2_RX8_Exh_HT.xml", "2RN2_RX8_Int_HT.xml", "4N1_E30M3_Exh_HT.xml", "NA_Exh_HT.xml"]
        self.assertEqual(fm4_cars.similar_tunings("5T1_A200_1478_Exh_HT.xml", fh1)[0], "5T1_A200_Exh_HT.xml")
        self.assertEqual(fm4_cars.similar_tunings("2RN3_FBRX7_Int_HT.xml", fh1)[0], "2RN2_RX8_Int_HT.xml")
        self.assertEqual(fm4_cars.similar_tunings("4R3_787B_Exh_HT.xml", fh1)[:2],
                         ["2RN2_RX8_Exh_HT.xml", "2RT2_RX7_Exh_HT.xml"])  # a rotary before a 4-cylinder


# ---- FM4 DLC packs: stand-ins with the layout FM4's packs have (no game file) -------------------

DB_TABLES = {
    "Data_Car": "Id INTEGER, MediaName TEXT, MakeID INT, Year INT, StockWheelID INT",
    "List_CarMake": "ID INTEGER, DisplayName TEXT",
    "List_Wheels": "ID INTEGER, MediaName TEXT",
    "List_UpgradeEngine": "Id INTEGER, Ordinal INT, EngineID INT, IsStock INT",
    "Data_Engine": "EngineID INTEGER PRIMARY KEY, MediaName TEXT",
    "CarPartPositions": "Ordinal INT, Part TEXT",
}


def sqlite_bytes(rows, tables=DB_TABLES):
    """A database file's bytes: tables (name -> columns) with rows (name -> [tuples])."""
    with tempfile.TemporaryDirectory() as t:
        path = os.path.join(t, "db.slt")
        db = sqlite3.connect(path)
        for name, cols in tables.items():
            db.execute("create table [%s] (%s)" % (name, cols))
        for name, rs in rows.items():
            for r in rs:
                db.execute("insert into [%s] values (%s)" % (name, ",".join("?" * len(r))), r)
        db.commit()
        db.close()
        with open(path, "rb") as f:
            return f.read()


def write_zip(path, members):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def car_members(name, car_id=None):
    out = {"media/cars/%s/%s.carbin" % (name, name.lower()): b"car",
           "media/cars/%s/physics/maxdata.xml" % name: b"<MaxData/>",
           "media/wheels/%s/%s.carbin" % (name, name.lower()): b"wheel"}
    if car_id:
        out["media/UI/Textures/Thumbnails/Thumbnail_%d.xds" % car_id] = b"thumb%d" % car_id
    return out


def held_open(path):
    """True when this process has the file open (Linux /proc; elsewhere the delete itself fails)."""
    fds = "/proc/self/fd"
    if not os.path.isdir(fds):
        return False
    want = os.path.realpath(path)
    for fd in os.listdir(fds):
        try:
            if os.readlink(os.path.join(fds, fd)) == want:
                return True
        except OSError:
            pass
    return False


def windows_unlink():
    """os.unlink as Windows does it (#551, #707): a file this process still holds open is refused."""
    real = os.unlink

    def unlink(path, *a, **kw):
        if held_open(path):
            e = PermissionError(13, "The process cannot access the file because it is being used by "
                                    "another process", str(path))
            e.winerror = 32
            raise e
        return real(path, *a, **kw)
    return mock.patch.object(os, "unlink", unlink)


class DlcPackTests(unittest.TestCase):
    """FM4's DLC car packs: the cars' rows in each pack's database patch, merged over disc 1's
    database; their files, pictures and sound tuning in the pack's zips."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: fm4_cars.ZIPS.close(self.tmp))
        self.disc1 = os.path.join(self.tmp, "disc1")
        os.makedirs(os.path.join(self.disc1, "Media", "db"))
        with open(os.path.join(self.disc1, "Media", "db", "gamedb.slt"), "wb") as f:
            f.write(sqlite_bytes({"Data_Car": [(100, "AUD_A1_10", 3, 2010, 1)], "List_CarMake": [(3, "_&1")],
                                  "List_Wheels": [(1, "AUD_A1_10")],
                                  "List_UpgradeEngine": [(1, 100, 7, 1)], "Data_Engine": [(7, "old")],
                                  "CarPartPositions": [(100, "a")]}))
        self.dlc = os.path.join(self.tmp, "dlc")

    def pack(self, name, zips):
        dz = os.path.join(self.dlc, name, "Media", "DLCZips")
        for zname, members in zips.items():
            write_zip(os.path.join(dz, zname), members)
        with open(os.path.join(self.dlc, name, "Media", "27.puboffer"), "wb") as f:
            f.write(b" \r\n")

    def patch(self, rows):
        return {"media/db/patch/27000_merge.slt": sqlite_bytes(rows)}

    def test_a_dlc_packs_rows_files_picture_and_sound(self):
        self.pack("A", {"5c00_pri_65.zip": {**car_members("HYU_Veloster_12", 1424), **self.patch({
            "Data_Car": [(1424, "HYU_Veloster_12", 17, 2012, 2)], "List_CarMake": [(17, "_&2")],
            "List_Wheels": [(2, "HYU_Veloster_12")], "List_UpgradeEngine": [(2, 1424, 8, 1), (3, 1424, 7, 0)],
            "Data_Engine": [(8, "new"), (7, "patched")], "CarPartPositions": [(1424, "b"), (100, "a")]}),
            "media/audio/cars/Engines/EngineTuning/HYU_Veloster_12_ET.xml": b"et"},
            "CarModelTuning_pri_25028.zip": {"HYU_Veloster_12_CMT.xml": b"cmt"}})
        fm4 = fm4_cars.FM4(self.disc1, None, self.dlc)
        self.addCleanup(fm4.close)
        self.assertEqual(fm4.dlc_car_ids, {1424})
        db = fm4.db()
        self.addCleanup(db.close)
        self.assertEqual(db.execute("select Id from Data_Car order by Id").fetchall(), [(100,), (1424,)])
        # a key table takes the patch's row; a table of several rows a car gets only those it lacks
        self.assertEqual(db.execute("select * from Data_Engine order by EngineID").fetchall(),
                         [(7, "patched"), (8, "new")])
        self.assertEqual(sorted(db.execute("select * from CarPartPositions")), [(100, "a"), (1424, "b")])
        self.assertIn("hyu_veloster_12", fm4.car_files())  # a hex-named car zip (5c00_pri_65.zip)
        self.assertTrue(fm4.has_wheel("HYU_Veloster_12"))
        self.assertEqual(fm4.thumbnail(1424), b"thumb1424")
        audio = fm4.pack_audio()
        self.assertIn("hyu_veloster_12_et.xml", audio["et"])
        self.assertIn("hyu_veloster_12_cmt.xml", audio["cmt"])
        merged = fm4.db_path
        self.assertTrue(held_open(merged) or not os.path.isdir("/proc/self/fd"))
        with windows_unlink(), mock.patch.object(fm4_cars.time, "sleep"):
            fm4.close()  # closes the connections it handed out (db above) before the delete
        self.assertFalse(os.path.exists(merged))  # the merged copy is deleted with the rest (#551)

    def test_a_damaged_patch_leaves_no_temporary_file(self):
        self.pack("A", {"27_pri_65.zip": {**car_members("HYU_Veloster_12"),
                                          "media/db/patch/27000_merge.slt": b"not a database"}})
        tmp = os.path.join(self.tmp, "temp")
        os.makedirs(tmp)
        with mock.patch.object(tempfile, "tempdir", tmp), windows_unlink():
            with self.assertRaises(fm4_cars.ConvertError):
                fm4_cars.FM4(self.disc1, None, self.dlc)
        self.assertEqual(os.listdir(tmp), [])

    def test_without_dlc_disc_1s_database_is_read_as_it_is(self):
        fm4 = fm4_cars.FM4(self.disc1)
        self.addCleanup(fm4.close)
        self.assertEqual(fm4.db_path, os.path.join(self.disc1, "Media", "db", "gamedb.slt"))
        self.assertEqual(fm4.dlc_car_ids, set())

    def test_pack_cars_tells_a_car_pack_from_disc_2s_and_a_track_pack(self):
        self.pack("cars", {"27_pri_65.zip": {**car_members("HYU_Veloster_12"), **self.patch(
            {"Data_Car": [(1424, "HYU_Veloster_12", 17, 2012, 2)]})}})
        self.pack("disc2", {"0001_pri_65.zip": car_members("BUG_Veyron_09")})
        self.pack("track", {"0015_pri_65.zip": {"media/Tracks/x/bin.zip": b"t", **self.patch({})},
                            "PhysicsTrack_pri_65.zip": {"a.bin": b"t"}})
        found = {os.path.basename(p): fm4_cars.pack_cars(p) for p in fm4_cars.pack_folders(self.dlc)}
        self.assertEqual(found["cars"], ({"HYU_Veloster_12"}, [(1424, "HYU_Veloster_12")]))
        self.assertEqual(found["disc2"], ({"BUG_Veyron_09"}, []))
        self.assertEqual(found["track"], (set(), []))

    def converter(self):
        fm4 = fm4_cars.FM4(self.disc1, None, self.dlc)
        self.addCleanup(fm4.close)
        conv = fm4_cars.Converter.__new__(fm4_cars.Converter)
        conv.fm4, conv.fm4db = fm4, fm4.db()
        conv.fh1db = sqlite3.connect(":memory:")
        for name, cols in DB_TABLES.items():
            conv.fh1db.execute("create table [%s] (%s)" % (name, cols))
        conv.fh1 = mock.Mock(dlc_car_names=lambda: (set(), set()), has_wheel=lambda name: False)
        self.addCleanup(conv.fm4db.close)
        self.addCleanup(conv.fh1db.close)
        return conv

    def test_a_car_that_needs_another_packs_rows_or_files_is_left_out(self):
        self.pack("A", {"27_pri_65.zip": {**car_members("HYU_Veloster_12"), **car_members("NOW_Heel_11"),
                                          **car_members("NOW_Make_11"), **car_members("NOW_Rim_11"),
                                          **self.patch({
            "Data_Car": [(1424, "HYU_Veloster_12", 3, 2012, 2), (1500, "NOW_Heel_11", 3, 2011, 2),
                         (1501, "NOW_Make_11", 99, 2011, 2), (1502, "NOW_Rim_11", 3, 2011, 5),
                         (1503, "NOW_Model_11", 3, 2011, 2)],
            "List_Wheels": [(2, "HYU_Veloster_12"), (5, "OTHER_Pack_Wheel")],
            "List_UpgradeEngine": [(2, 1424, 8, 1), (3, 1500, 77, 1)], "Data_Engine": [(8, "new")]})}})
        reasons = {name: reason for _, name, reason in self.converter().candidates()}
        self.assertIsNone(reasons["HYU_Veloster_12"])
        self.assertIn("needs its engine (EngineID 77)", reasons["NOW_Heel_11"])
        self.assertIn("another DLC pack", reasons["NOW_Heel_11"])
        self.assertIn("needs its make (99)", reasons["NOW_Make_11"])
        self.assertIn("needs the files of its wheel OTHER_Pack_Wheel", reasons["NOW_Rim_11"])
        self.assertEqual(reasons["NOW_Model_11"], "no model in the FM4 files given")

    def test_a_dlc_cars_wheel_row_comes_along_and_a_clashing_engine_does_not(self):
        """FH1's database lacks the wheel rows of the Porsche Expansion's cars (they did not load
        without one), and gives two of the DLC's engine ids to other engines (the GT2 RS engine,
        a swap of four Porsches, is FH1's Camry engine): such a swap is left out, a car whose own
        engine clashes is not converted."""
        self.pack("A", {"34_pri_65.zip": {**car_members("POR_959_87"), **car_members("AH_Sprite_58"),
                                          **self.patch({
            "Data_Car": [(269, "POR_959_87", 3, 1987, 595), (1574, "AH_Sprite_58", 3, 1958, 2)],
            "List_Wheels": [(595, "POR_959_87"), (2, "AH_Sprite_58")],
            "List_UpgradeEngine": [(269000, 269, 23, 1), (269001, 269, 999, 0), (1574000, 1574, 1034, 1)],
            "Data_Engine": [(23, "POR_959_87"), (999, "POR_911GT2_12"), (1034, "AH_Sprite_58")]})}})
        conv = self.converter()
        conv.fh1db.execute("insert into Data_Engine values (999, 'TOY_Camry_07')")
        conv.fh1db.execute("insert into Data_Engine values (1034, 'FIA_ShowCase500AbarthSS_10')")
        conv.fh1db.execute("insert into List_Wheels values (2, 'AH_Sprite_58')")
        reasons = {name: reason for _, name, reason in conv.candidates()}
        self.assertIsNone(reasons["POR_959_87"])
        self.assertEqual(reasons["AH_Sprite_58"], "its engine's id (EngineID 1034) is another engine's in FH1")
        with mock.patch.object(fm4_cars, "Retune", _NoRetune):
            db = fm4_cars.Database(conv.fm4db, conv.fh1db)
        db.collect([269])
        rows = db.finish({})
        self.assertEqual([(r["ID"], r["MediaName"]) for r in rows["List_Wheels"]], [(595, "POR_959_87")])
        self.assertEqual([r["EngineID"] for r in rows["List_UpgradeEngine"]], [23])  # the swap left out
        self.assertEqual([r["EngineID"] for r in rows["Data_Engine"]], [23])


if __name__ == "__main__":
    unittest.main()
