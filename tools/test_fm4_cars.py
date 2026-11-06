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


def fm4_carbin_file(vertices, sections=None):
    """An FM4 car model (root 2, base 16, buffers v2/v3, 32-byte vertices): one "body" section
    with one mesh of the given vertices, or sections [(name, [(material, [(x, y, z)...])...])]
    with their vertices placed at those positions."""
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
        out += struct.pack(">I", 1) + b"\0"         # MorphTargets
        out += struct.pack(">III", 2, 0, 0) * 2
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

    def game(self, name, ets):
        media = os.path.join(self.tmp, name, "media")
        os.makedirs(os.path.join(media, "audio", "cars", "Engines"))
        for zname, members in (("CarModelTuning.zip", {}), ("Engines/EngineTuning.zip", ets),
                               ("Engines/HarmonicTuning.zip", {})):
            with zipfile.ZipFile(os.path.join(media, "audio", "cars", *zname.split("/")), "w") as z:
                for m, data in members.items():
                    z.writestr(m, data)
        return mock.Mock(media=media, update=None)

    def converter(self, fh1_ets, fm4_ets):
        conv = fm4_cars.Converter.__new__(fm4_cars.Converter)
        conv.out, conv.pack = os.path.join(self.tmp, "out"), None
        conv.fh1, conv.fm4 = self.game("fh1", fh1_ets), self.game("fm4", fm4_ets)
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


if __name__ == "__main__":
    unittest.main()
