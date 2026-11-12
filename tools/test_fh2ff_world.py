"""tools/fh2ff_world.py and fm4_pvs.py's version 51 (#821): Fast & Furious's world as an FH1 one.
Synthetic files (no game files); with FH2FF_DISC=<F&F's disc folder> set, F&F's own world is
converted in memory too (RealWorld)."""
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_pvs  # noqa: E402
import fm4_routes  # noqa: E402
import fh2ff_world as ff  # noqa: E402

TAIL32 = bytes(range(32))


def placement_bytes(routes=None, ver=51):
    """A placement (fm4_pvs.read_placement): LODs, position, rotation, four floats, then the
    dynamic data when routes is given (u32, count, the routes, 32 bytes)."""
    b = struct.pack(">3H3f9H4f", 1, 2, 3, 10.0, 20.0, 30.0, *range(9), 0.0, 0.0, 0.0, 1.0)
    if routes is None:
        return b + b"\0"
    rt = struct.pack(f">{len(routes)}H", *routes) if ver >= 51 else bytes(routes)
    return b + b"\1" + struct.pack(">IB", 144, len(routes)) + rt + TAIL32


def layout_bytes(ver, routes):
    """A layout with no zones, one dynamic instance and its placement."""
    b = b"FPVS" + struct.pack(">III", ver, 7, 8) + struct.pack(">HI", 0, 3) + b"\1\1"
    b += struct.pack(">III", 100, 0, 0) + b"\0" + struct.pack(">H", 50)
    b += struct.pack(">I", 0)                       # cells
    b += struct.pack(">III", 0, 0, 0)               # textures, shaders, instances
    b += struct.pack(">I", 0)                       # models
    b += struct.pack(">III", 0, 0, 0)               # always, models_a, models_b
    b += struct.pack(">I", 1) + struct.pack(">HHIIIH", 0, 0, 0, 0, 0, 0xFFFF)
    b += placement_bytes(routes, ver)
    b += struct.pack(">I", 4) + b"aout" + b"\0\0"
    b += struct.pack(">II", 0, 0) + b"\0" + struct.pack(">I", 0)
    return b


class Version51(unittest.TestCase):
    def test_layout_reads_and_writes_back_as_50(self):
        P = fm4_pvs.read_fh1(layout_bytes(51, [2381, 7]))
        self.assertEqual(P["version"], 51)
        x, routes, tail = P["dynamic_placements"][0]["extra"]
        self.assertEqual((x, routes, tail), (144, (2381, 7), TAIL32))
        with self.assertRaises(ValueError):          # 16-bit routes are never written
            fm4_pvs.write_fh1(P)
        e = P["dynamic_placements"][0]
        e["extra"] = (x, bytes({2381: 1, 7: 2}[r] for r in routes), tail)
        out = fm4_pvs.write_fh1(P)
        self.assertEqual(out, layout_bytes(50, [1, 2]))
        self.assertEqual(fm4_pvs.read_fh1(out)["version"], 50)

    def test_version_50_unchanged(self):
        data = layout_bytes(50, [3, 4])
        self.assertEqual(fm4_pvs.write_fh1(fm4_pvs.read_fh1(data)), data)

    def test_other_versions_refused(self):
        with self.assertRaises(ValueError):
            fm4_pvs.read_fh1(layout_bytes(52, [1]))

    def test_placement_routes(self):
        r = fm4_pvs.Reader(placement_bytes([5, 600]))
        e = fm4_pvs.read_placement(r, 51)
        self.assertEqual(e["extra"][1], (5, 600))
        self.assertEqual(r.p, len(r.d))
        r = fm4_pvs.Reader(placement_bytes([5, 6], ver=50))
        self.assertEqual(fm4_pvs.read_placement(r)["extra"][1], b"\5\6")


def pgeo(kind=3, routes=(2381,), name=b"crowd_test_2381\0", rest=b"REST" * 8, dyn=True):
    """A procedural geometry file with FH2's 76-byte dynamic data block, its name and a part after
    them (16-byte aligned)."""
    head = bytearray(0x60)
    head[0:4] = b"OEGP"
    struct.pack_into(">I", head, 4, 0x2A)
    struct.pack_into(">I", head, 0x30, kind)
    struct.pack_into(">I", head, 0x3C, len(name))
    struct.pack_into(">I", head, 0x58, 0x13BAD430 if dyn else 0)
    body = bytearray()
    if dyn:
        block = bytearray(76)
        struct.pack_into(">I", block, 0, 0xFFFFFFFF)
        block[4:12] = b"gameplay"
        block[36] = len(routes)
        block[37] = 0x74                                   # the pad byte (seen non-zero)
        for k, r in enumerate(routes):
            struct.pack_into(">H", block, 38 + 2 * k, r)
        body += block
    body += name
    while (0x60 + len(body)) % 16:
        body += b"\xEE"                                    # garbage up to the alignment, as on the disc
    body += rest
    struct.pack_into(">I", head, 0x48, 0x60 + len(body))
    return bytes(head + body)


class ProceduralGeometry(unittest.TestCase):
    def test_dynamic_block_made_fh1s(self):
        d = pgeo()
        self.assertEqual(ff.pgeo_routes(d), [2381])
        out = ff.convert_pgeo(d, {2381: 9})
        self.assertEqual(struct.unpack_from(">I", out, 0x48)[0], len(out))
        self.assertEqual(out[0x60:0x60 + 36], d[0x60:0x60 + 36])      # physics, gameplay id
        self.assertEqual(out[0x60 + 36], 1)
        self.assertEqual(out[0x60 + 37], 9)                             # the route as a byte
        self.assertEqual(out[0x60 + 38:0x60 + 56], bytes(18))
        name = b"crowd_test_2381\0"
        self.assertEqual(out[0x98:0x98 + len(name)], name)
        rest_at = (0x98 + len(name) + 15) & ~15
        self.assertEqual(out[rest_at:], b"REST" * 8)
        self.assertEqual(out[:0x48], d[:0x48])

    def test_an_animated_scene_keeps_its_references(self):
        # the name, then (4-byte aligned) a geometry reference and the 28-byte scene data; the
        # object instances 16-byte aligned after them
        name = b"Anim_instances(0)387_0\x00"
        refs_and_data = struct.pack(">2I", 14, 0) + struct.pack(">7I", 1, 0, 0, 0, 0, 0, 0)
        d = bytearray(pgeo(kind=ff.ANIMATED_SCENE, name=name)[:0x60 + 76])
        struct.pack_into(">I", d, 0x44, 1)              # one geometry
        d += name + b"\x38" + refs_and_data            # one byte of padding to 4 (garbage)
        while len(d) % 16:
            d += b"\xEE"
        d += b"INST" * 8
        struct.pack_into(">I", d, 0x48, len(d))
        out = ff.convert_pgeo(bytes(d), {2381: 1})
        at = 0x98 + len(name)
        self.assertEqual(out[0x98:at], name)
        self.assertEqual(out[at:at + 1], b"\x00")       # the padding zeroed
        self.assertEqual(out[at + 1:at + 1 + len(refs_and_data)], refs_and_data)
        self.assertEqual(out[(at + 1 + len(refs_and_data) + 15) & ~15:], b"INST" * 8)
        self.assertEqual(struct.unpack_from(">I", out, 0x48)[0], len(out))

    def test_without_block_unchanged(self):
        d = pgeo(kind=2, dyn=False)
        self.assertIs(ff.convert_pgeo(d, {}), d)
        self.assertEqual(ff.pgeo_routes(d), [])
        self.assertEqual(ff.pgeo_kind(d), 2)

    def test_names_of_every_length(self):
        for n in range(1, 40):
            name = b"x" * n + b"\0"
            out = ff.convert_pgeo(pgeo(name=name), {2381: 1})
            at = (0x98 + len(name) + 15) & ~15
            self.assertEqual(out[0x98:0x98 + len(name)], name)
            self.assertEqual(out[at:], b"REST" * 8)

    def test_size_must_match(self):
        d = bytearray(pgeo())
        struct.pack_into(">I", d, 0x48, len(d) + 1)
        with self.assertRaises(ValueError):
            ff.convert_pgeo(bytes(d), {2381: 1})

    def test_unknown_route_is_zero(self):
        out = ff.convert_pgeo(pgeo(routes=(5, 6)), {5: 3})
        self.assertEqual(out[0x60 + 36:0x60 + 39], bytes([2, 3, 0]))


class Hex(unittest.TestCase):
    def test_102_as_101(self):
        grid = struct.pack(">4i", 0, -1, -1, 1)
        zc = struct.pack(">4i", 0, 0, 1, 1)
        v102 = b"HEXY" + struct.pack(">ifffiii", 102, 100.0, -5.0, -6.0, 2, 2, 2) + grid + zc + b"\1\2" + b"\x3f\x2f"
        v101 = ff.convert_hex(v102)
        self.assertEqual(v101, b"HEXY" + struct.pack(">ifffiii", 101, 100.0, -5.0, -6.0, 2, 2, 2) + grid + zc + b"\x3f\x2f")
        self.assertEqual(ff.convert_hex(v101), v101)

    def test_bad_hex(self):
        with self.assertRaises(ValueError):
            ff.convert_hex(b"HEXY" + struct.pack(">ifffiii", 102, 100.0, 0, 0, 2, 1, 1) + bytes(8))

    def test_zone_cells(self):
        g = fm4_pvs.HexGrid([(0, 0), (1000, 1000)], 100.0)
        data = bytearray(g.write())
        inside = ff.zone_cells(bytes(data))
        cx, cz = g.centre(4, 4)
        self.assertTrue(inside(cx, cz))
        self.assertFalse(inside(cx + 10000, cz))
        self.assertFalse(inside(g.ox - 500, cz))
        struct.pack_into(">i", data, 32 + 4 * (4 * g.w + 5), -1)     # a neighbour without a zone
        self.assertFalse(ff.zone_cells(bytes(data))(cx, cz))


class Names(unittest.TestCase):
    def test_fh1_order_and_names(self):
        paths = ["b.bin", "shaders/track/H_DIFF_1.fxobj", "__R00Z00001.pvsz", "a.bix"]
        order, data = ff.fh1_names(paths)
        crcs = [zlib.crc32(p.lower().encode()) for p in order]
        self.assertEqual(crcs, sorted(crcs))
        names = ff.read_filename_map(data)
        self.assertEqual(len(names), 4)
        for p, n in zip(order, names):
            self.assertEqual(n, p.split("/")[-1].lower() if "/" in p else p)

    def test_tile_names(self):
        self.assertEqual(ff.fh1_tile_name("Map/background/high-1-3.xds"), "high-3-1.xds")
        self.assertEqual(ff.fh1_tile_name("Map\\background\\low-0-0.xds"), "low-0-0.xds")
        self.assertIsNone(ff.fh1_tile_name("Map/background/lines.xds"))


def collobj(k, kind, x, y, z):
    return (f'\t<Obj{k} PhysicsType="{kind}" GraphicsName="#{k}">\n\t\t<Pos x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>\n'
            f'\t\t<Orientation/>\n\t</Obj{k}>\n')


class Closures(unittest.TestCase):
    def test_only_fs_own_closures_go(self):
        mine = "<CollObjs>\n" + collobj(0, "GEN_OBJ_TrafficCone_French_Large.0.rmb", 10, 5, 20) + \
            collobj(1, "GEN_OBJ_TrafficCone_French_Large.0.rmb", 50, 5, 60) + \
            collobj(2, "gen_obj_benches_wooden.0.rmb", 11, 5, 21) + \
            collobj(3, "FRA_OBJ_Sign_RouteBarree.0.rmb", -7.25, 3.5, 8) + "</CollObjs>\n"
        theirs = "<CollObjs>\n" + collobj(0, "gen_obj_trafficcone_french_large.0.rmb", 50.2, 5, 59.8) + "</CollObjs>\n"
        out, gone = ff.closures(mine, theirs)
        self.assertEqual(sorted(gone), [(-7.25, 3.5, 8.0), (10.0, 5.0, 20.0)])
        self.assertEqual(out.count("<Obj"), 4)
        self.assertIn('<Obj0 PhysicsType="GEN_OBJ_TrafficCone_French_Large.0.rmb" GraphicsName="#0">\n\t\t'
                      '<Pos x="10.000000" y="-5000.000000" z="20.000000"/>', out)
        self.assertIn('y="5.000000" z="60.000000"', out)        # FH2 has it: kept
        self.assertIn('y="5.000000" z="21.000000"', out)        # not a closure: kept
        self.assertIn('<Pos x="-7.250000" y="-5000.000000" z="8.000000"/>', out)

    def test_fh2s_object_across_a_metre_edge_stays(self):
        mine = "<CollObjs>\n" + collobj(0, "gen_obj_trafficcone_medium.0.rmb", 10.51, 5, 20.49) + "</CollObjs>\n"
        theirs = "<CollObjs>\n" + collobj(0, "gen_obj_trafficcone_medium.0.rmb", 10.49, 5, 20.51) + "</CollObjs>\n"
        out, gone = ff.closures(mine, theirs)
        self.assertEqual(gone, [])
        self.assertEqual(out, mine)
        far = "<CollObjs>\n" + collobj(0, "gen_obj_trafficcone_medium.0.rmb", 11.6, 5, 20.5) + "</CollObjs>\n"
        self.assertEqual(len(ff.closures(mine, far)[1]), 1)


def net(ways, nodes, keys, values):
    """A road network dict as fm4_routes.read_nav gives it: ways as ([node indexes], {key: value})."""
    out = {"keys": list(keys), "values": list(values), "tags": [], "ways": [], "node_list": [],
           "nodes": [(k, x, y, z, 0, 0, 0, 0) for k, (x, y, z) in enumerate(nodes)], "memberships": []}
    for idx, tags in ways:
        first, ft = len(out["node_list"]), len(out["tags"])
        out["node_list"] += idx
        out["tags"] += [(out["keys"].index(k), out["values"].index(v)) for k, v in sorted(tags.items())]
        out["ways"].append((len(idx), first, len(tags), ft))
    return out


class RoadNetwork(unittest.TestCase):
    def test_blocked_take_fh2s_types(self):
        nodes = [(0, 0, 0), (10, 0, 0), (20, 0, 0), (500, 0, 500), (510, 0, 500)]
        mine = net([([0, 1, 2], {"road_type": "blocked"}), ([3, 4], {"road_type": "blocked"}),
                    ([1, 2], {"road_type": "a"})], nodes, ["road_type"], ["a", "b", "blocked", "dirt"])
        theirs = net([([0, 1, 2], {"road_type": "dirt"})], [(0.3, 0, 0.2), (10, 0, 0), (19.6, 0, 0)],
                     ["road_type"], ["dirt"])
        counts = ff.retype_blocked(mine, theirs)
        self.assertEqual(counts, {"dirt (FH2's)": 1, "b (no FH2 road there)": 1})
        types = [mine["values"][mine["tags"][ft][1]] for _, _, _, ft in mine["ways"]]
        self.assertEqual(types, ["dirt", "b", "a"])

    def test_tag_names(self):
        n = net([], [], ["ai_disabled", "oneway_forward", "road_type", "traffic_density_name"], ["x"])
        ff.fix_tags(n)
        self.assertEqual(n["keys"], sorted(ff.COLORADO_TAG_NAMES))
        bad = net([], [], ["a", "oneway_forward", "z"], ["x"])
        bad["keys"] = ["zz", "oneway_forward"]
        with self.assertRaises(ValueError):
            ff.fix_tags(bad)

    def test_traffic_density_ids(self):
        nodes = [(0, 0, 0), (10, 0, 0)]
        n = net([([0, 1], {"road_type": "a", "traffic_density_name": "a_high"}),
                 ([0, 1], {"road_type": "b", "traffic_density_name": "b"}),
                 ([0, 1], {"road_type": "freeway", "traffic_density_name": "freeway"}),
                 ([0, 1], {"profile_name": "x", "traffic_density_name": "made_up"})],
                nodes, ["profile_name", "road_type", "traffic_density_name"],
                ["a", "a_high", "b", "freeway", "made_up", "x"])
        ff.fix_tags(n)
        tags = [{n["keys"][k]: n["values"][v] for k, v in n["tags"][ft:ft + tc]} for _, _, tc, ft in n["ways"]]
        self.assertEqual([t["traffic_density"] for t in tags], ["5", "2", "4", "1"])
        self.assertEqual([t.get("road_type") for t in tags], ["a", "b", "freeway", None])
        self.assertEqual(n["values"], sorted(set(n["values"])))
        self.assertNotIn("a_high", n["values"])
        fm4_routes.check_network(fm4_routes.write_nav(n))

    def test_streaming_blocks_of_a_cell_and_its_neighbours(self):
        # a 3 x 3 hex grid, zones 0..3 in four cells; zone k has model k, of (k + 1) KB
        cells = [-1, 0, -1,
                 1, 2, 3,
                 -1, -1, -1]
        hexd = struct.pack(">4sifffiii", b"HEXY", 101, 100.0, 0.0, 0.0, 4, 3, 3) + struct.pack(">9i", *cells)
        w = ff.World.__new__(ff.World)
        w.uid_models = {k: {k, 99} for k in range(4)}       # 99: a model with no file, not counted
        w.model_entry = {k: k for k in range(4)}
        w.info_of = [mock.Mock(file_size=(k + 1) * 1024) for k in range(4)]
        # the centre cell (1, 1) holds zone 2; its neighbours (1, 0), (0, 1), (2, 1) the others
        self.assertEqual(w.stream_blocks(hexd), 1 + 2 + 3 + 4)

    def test_keep_right(self):
        line = ff.keep_right([(0, 1, 0), (10, 1, 0)], 2.0)
        self.assertEqual(line, [(0.0, 1, -2.0), (10.0, 1, -2.0)])  # heading +x: right is -z

    def test_spawn_on_the_longest_stretch_inside(self):
        nodes = [(x, 5, 0) for x in range(0, 1000, 100)] + [(0, 5, z) for z in range(0, 300, 100)]
        n = net([(list(range(10)), {"road_type": "a"}), ([10, 11, 12], {"road_type": "b"}),
                 ([0, 9], {"area_type": "lake"})], nodes, ["area_type", "road_type"], ["a", "b", "lake"])
        spawn, line, length = ff.spawn_of(n, lambda x, z: x < 650)
        self.assertEqual(spawn, "0.0,5.5,-2.0,1.000,0.000")  # the right-hand lane (keep_right)
        self.assertEqual(length, 600)
        self.assertEqual(len(line.splitlines()), 7)
        with self.assertRaises(ValueError):
            ff.spawn_of(n, lambda x, z: False)


ARENA_XML = ("<TrackRoute>\r\n\t<NamedTransforms>\r\n"
             "\t\t<NamedTransform name='Arena_00' width='4.0'>\r\n"
             "\t\t\t<Transform pos.x='1' pos.y='2' pos.z='3' facing.x='0.0' facing.y='0.0' facing.z='4.0'/>\r\n"
             "\t\t</NamedTransform>\r\n"
             "\t\t<NamedTransform name='start_location_00' width='4.0'>\r\n"
             "\t\t\t<Transform pos.x='5' pos.y='6' pos.z='7' facing.x='3.0' facing.y='0.0' facing.z='4.0'/>\r\n"
             "\t\t</NamedTransform>\r\n\t</NamedTransforms>\r\n</TrackRoute>\r\n")


class Routes(unittest.TestCase):
    def test_an_arena_in_fh1s_terms(self):
        import xml.etree.ElementTree as ET
        out = ff.route_xml(ARENA_XML)
        root = ET.fromstring(out)
        names = [n.get("name") for n in root.iter("NamedTransform")]
        self.assertEqual(names, ["route_boundary_00", "start_location_00"])   # no post-race places
        start = root.find("NamedTransforms/NamedTransform[@name='start_location_00']/Transform")
        self.assertEqual((start.get("facing.x"), start.get("facing.z")), ("0.6", "0.8"))   # a unit vector
        self.assertIsNone(root.find("NamedTransforms/NamedTransform[@name='route_boundary_00']").get("width"))
        self.assertEqual(ff.route_xml(out), out)
        self.assertIn("\r\n", out)

    def test_a_race_gets_its_post_race_places(self):
        import xml.etree.ElementTree as ET
        race = ARENA_XML.replace("Arena_00", "end_position_00")
        root = ET.fromstring(ff.route_xml(race))
        post = root.find("NamedTransforms/NamedTransform[@name='post_race_location_00']/Transform")
        self.assertEqual(post.get("pos.x"), "1")   # from the end position
        root = ET.fromstring(ff.route_xml(race.replace("end_position_00", "route_waypoint_00")))
        self.assertEqual(root.find("NamedTransforms/NamedTransform[@name='post_race_location_00']/Transform")
                         .get("pos.x"), "5")       # no end position: the start place

    @staticmethod
    def owt2(segments, points):
        """An OWTM version 2 file: segments as (first, next, count), points as x positions."""
        b = b"OWTM" + struct.pack(">3I", 2, 0, 0) + struct.pack(">4I", len(segments), 0, 0, 0)
        for first, nxt, count in segments:
            b += struct.pack(">8I", first, nxt, ff.NONE, ff.NONE, count, 1, 1, 0xCAFEFEED)
        for x in points:
            b += struct.pack(">12f", x, 1.0, 2.0, 0.0, 0.0, 0.0, 6.0, 0.0, -0.5, 0.5, x / 100.0, 0.0)
        return b + b"OWTM" + struct.pack(">3I", 2, 0, 0)

    def test_ai_line_version_2(self):
        # two segments in a loop, a branch segment off it (not in the main chain)
        d = self.owt2([(0, 1, 2), (2, 0, 2), (4, ff.NONE, 1)], [0, 2, 4, 6, 50])
        pts, looped = ff.owt2_line(d)
        self.assertTrue(looped)
        self.assertEqual([p[0][0] for p in pts], [0, 2, 4, 6])
        self.assertEqual(pts[1][:2], ((2.0, 1.0, 2.0), (0.0, 0.0, 6.0)))
        self.assertAlmostEqual(pts[1][2], 0.02, places=6)   # the racing line (float32)
        pts, looped = ff.owt2_line(self.owt2([(0, ff.NONE, 3)], [0, 2, 4]))
        self.assertFalse(looped)
        back, back_looped = fm4_routes.read_owt(fm4_routes.owt(pts, looped))
        self.assertEqual((len(back), back_looped), (3, False))
        with self.assertRaises(ValueError):
            ff.owt2_line(d[:-20])
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "aiopenworld.zip")
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("anthem/Ribbon_00/Route12.owt", d)
                z.writestr("anthem/Ribbon_00/Route13.owt", d)      # no Tracks row: left out
                z.writestr("anthem/Ribbon_00/Route3001.owt", b"OWTM" + b"\0" * 40)   # does not read
            out = os.path.join(tmp, "out")
            self.assertEqual(ff.ai_lines(src, {12, 3001}, out), {"looped": 1, "not read": 1})
            self.assertEqual(os.listdir(out), ["route_012.owt"])

    def test_rows_with_a_route_file(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            rb = os.path.join(tmp, "Ribbon_00")
            os.mkdir(rb)
            for n in ("TrackRoute012.xml", "TrackRoute3001.xml"):
                open(os.path.join(rb, n), "w").write(ARENA_XML)
            src = os.path.join(tmp, "src.slt")
            con = sqlite3.connect(src)
            con.execute("CREATE TABLE Tracks (id INTEGER, DisplayName TEXT, ShortDisplayName TEXT, MediaName TEXT, "
                        "RibbonIndex INTEGER, RibbonConfiguration INTEGER, RouteId INTEGER, EnvironmentId INTEGER, "
                        "DevName TEXT, FH2Only INTEGER)")
            con.executemany("INSERT INTO Tracks VALUES (?, '_&1', '_&2', 'Anthem', 0, ?, ?, 70, ?, 1)",
                            [(3, 0, 12, "Anthem Route 12"), (571, 7, 3001, "NEW DOCK PLAY 3001"),
                             (10571, 7, 6706, "NEW DOCK PLAY 3001"),    # no route file: left out
                             (0, 5, 0, "ANTHEM")])                      # free roam: not a race
            con.commit()
            con.close()
            rows = ff.world_routes(src, rb)
            self.assertEqual([r["RouteId"] for r in rows], [12, 3001])
            db = sqlite3.connect(":memory:")
            db.execute("CREATE TABLE Tracks (id INTEGER, DisplayName TEXT, ShortDisplayName TEXT, MediaName TEXT, "
                       "RibbonIndex INTEGER, RibbonConfiguration INTEGER, RouteId INTEGER, EnvironmentId INTEGER, DevName TEXT)")
            db.execute("INSERT INTO Tracks (id) VALUES (5000)")   # an id taken: the next one is used
            self.assertEqual(ff.add_world_routes(db, rows), {0: 1, 7: 1})
            got = db.execute("SELECT id, DisplayName, MediaName, EnvironmentId, RouteId FROM Tracks WHERE id > 5000").fetchall()
            self.assertEqual(got, [(5001, "Anthem Route 12", "Anthem", 70, 12), (5002, "NEW DOCK PLAY 3001", "Anthem", 70, 3001)])


class Lighting(unittest.TestCase):
    def test_track_settings_as_64(self):
        v66 = ('<TrackSettings Version="66">\r\n  <SkyboxIndex value="1"/>\r\n  <CarGlowScale value="2.0"/>\r\n'
               '  <TintColor r="1" g="1" b="1"/>\r\n  <Particles>\r\n    <A value="1"/>\r\n  </Particles>\r\n'
               '  <ToneMapHoodExposure value="1"/>\r\n  <ToneMapHighBumperExposure value="1"/>\r\n'
               '  <WorldClear r="0.026"/>\r\n</TrackSettings>\r\n')
        v64 = ff.track_settings_64(v66)
        self.assertEqual(v64, '<TrackSettings Version="64">\r\n  <SkyboxIndex value="1"/>\r\n'
                              '  <WorldClear r="0.026"/>\r\n</TrackSettings>\r\n')
        self.assertEqual(ff.track_settings_64(v64), v64)
        with self.assertRaises(ValueError):
            ff.track_settings_64('<TrackSettings Version="70">')

    def test_missing_grading_maps(self):
        with tempfile.TemporaryDirectory() as d:
            mine, theirs = os.path.join(d, "ff.zip"), os.path.join(d, "fh1.zip")
            with zipfile.ZipFile(mine, "w") as z:
                z.writestr("ColourGradingMaps/Amalfi.dds", b"amalfi")
                z.writestr("ColourGradingMaps/Night.dds", b"night")
                z.writestr("ColourGradingMaps/../evil.dds", b"no")
            with zipfile.ZipFile(theirs, "w") as z:
                z.writestr("ColourGradingMaps/Night.dds", b"fh1 night")
            out = os.path.join(d, "out")
            self.assertEqual(ff.missing_grading_maps(mine, theirs, out, d), 1)
            self.assertEqual(os.listdir(os.path.join(out, "colourgradingmaps")), ["amalfi.dds"])


def fiz(surfaces):
    """A collision cell file: the 0x20 header, then FH1's cell with one vertex and a triangle a
    surface."""
    nv, nt = 1, len(surfaces)
    cell = bytearray(struct.pack(">4I", nv, nt, 0x90, 0x90 + 16 * nv) + bytes(0x90 - 16))
    cell += struct.pack(">fffI", 1.0, 2.0, 3.0, 0)
    for s in surfaces:
        cell += struct.pack(">IIIIfffI", 0x0E008000 | s << 16, 0x90, 0x90, 0x90, 0.0, 1.0, 0.0, 0)
    head = b"fiz " + struct.pack(">IIII", 1, len(cell), 0, 0) + bytes(12)
    return head + bytes(cell) + b"tail"


class Surfaces(unittest.TestCase):
    FH1 = ["Asphalt", "Dirt", "Grass", "Gravel"]
    FF = FH1 + ["RallyGrass", "Dirt2", "Dirt3", "RallyGravel"]

    def test_map_by_name(self):
        self.assertEqual(ff.surface_map(self.FF, self.FH1), {4: 2, 5: 1, 6: 1, 7: 3})
        with self.assertRaises(ValueError):
            ff.surface_map(self.FF + ["Lava"], self.FH1)

    def test_cells_remapped(self):
        d = fiz([0, 7, 2, 4])
        out = ff.remap_fiz(d, {7: 3, 4: 2})
        flags = [struct.unpack_from(">I", out, 0x20 + 0xA0 + 32 * k)[0] for k in range(4)]
        self.assertEqual([(f >> 16) & 0xFF for f in flags], [0, 3, 2, 2])
        self.assertEqual([f & 0xFF00FFFF for f in flags], [0x0E008000] * 4)
        self.assertEqual(len(out), len(d))
        self.assertTrue(out.endswith(b"tail"))
        same = fiz([0, 1])
        self.assertIs(ff.remap_fiz(same, {7: 3}), same)


class Guards(unittest.TestCase):
    def test_reserved_names(self):
        for n in ("CON", "nul", "com1.txt", "LPT9.xml", "aux "):
            self.assertTrue(ff.reserved_name(n), n)
        for n in ("console.xml", "com10", "null", "thing.xml"):
            self.assertFalse(ff.reserved_name(n), n)

    def test_bare_fh2_prints_the_usage(self):
        self.assertEqual(ff.main(["fh2ff_world.py", "build", "a", "b", "c", "d", "--fh2"]), 2)

    def test_zone_lists_must_be_one_to_one(self):
        w = ff.World.__new__(ff.World)
        w.P = {"g2": 0, "version": 51}
        cell = fm4_pvs.Writer()
        for vec in ([7], [], [], [], [], [1]):        # instances, models, textures, flags, files, drawn
            cell.u32(len(vec))
            for v in vec:
                cell.u32(v) if vec == [7] else cell.u8(v)
        cell.u32(0)                                    # no placements for the one instance
        info = zipfile.ZipInfo("__R00Z00000.pvsz")
        w._zone_data = [(info, bytes(cell.b))]
        with self.assertRaisesRegex(ValueError, "not one to one"):
            list(w.zones())


class Archive(unittest.TestCase):
    def test_written_archive_reads_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.zip")
            with zipfile.ZipFile(src, "w") as z:
                z.writestr("a/model.rmb.bin", b"model data" * 10)
            out = os.path.join(tmp, "bin.zip")
            w = ff.TrackZipWriter(out)
            w.add("zone.pvsz", b"zone bytes")
            arch = ff.Archive(src)
            info = arch.infos[0]
            w.add_raw(info.filename, info, arch.raw(info))
            arch.close()
            start, size, count = w.close()
            self.assertEqual(count, 2)
            self.assertEqual(os.path.getsize(out), start + size)
            back = ff.Archive(out)
            self.assertEqual([i.filename for i in back.infos], ["zone.pvsz", "a/model.rmb.bin"])
            self.assertEqual(back.read(back.infos[0]), b"zone bytes")
            self.assertEqual(back.read(back.infos[1]), b"model data" * 10)
            back.close()

    def test_copies_of_a_file_are_read_once(self):
        import warnings
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.zip")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # zipfile warns of a duplicate name: FH2's archive has them
                with zipfile.ZipFile(src, "w") as z:
                    z.writestr("a.rmb.bin", b"model")
                    z.writestr("b.fiz", b"cell")
                    z.writestr("a.rmb.bin", b"model")   # FH2 (and Colorado): again, near another zone
            arch = ff.Archive(src)
            self.assertEqual([i.filename for i in arch.infos], ["a.rmb.bin", "b.fiz"])
            self.assertEqual(arch.copies, 1)
            self.assertEqual(len(arch.by_base["a.rmb.bin"]), 1)
            arch.close()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with zipfile.ZipFile(src, "w") as z:
                    z.writestr("a.rmb.bin", b"model")
                    z.writestr("a.rmb.bin", b"other")
            with self.assertRaises(ValueError):
                ff.Archive(src)

    def test_silent_tile_is_xml(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(ff.SILENT_TILE)
        self.assertEqual(root.tag, "AudioTile")
        self.assertEqual(len(root.find("AmbienceZones")), 0)


@unittest.skipUnless(os.environ.get("FH2FF_DISC"), "FH2FF_DISC (F&F's disc folder) is not set: no real world is read")
class RealWorld(unittest.TestCase):
    def test_converts_whole(self):
        self.assertTrue(ff.check(os.environ["FH2FF_DISC"]))


class OutputSafety(unittest.TestCase):
    paths = (Path("fm4-tracks") / ff.NAME.lower(), Path("mods") / ff.MOD)

    def populate(self, root, value):
        for path in self.paths:
            directory = root / path
            directory.mkdir(parents=True)
            (directory / "marker").write_text(value)

    def assert_contents(self, root, value):
        for path in self.paths:
            self.assertEqual((root / path / "marker").read_text(), value)
        self.assertFalse((root / ".fh2ff-publish").exists())
        self.assertFalse(list(root.glob(".fh2ff-build-*")))

    def test_missing_disc_preserves_both_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()   # the same spelling build() uses (8.3 names on Windows)
            self.populate(root, "previous")
            with self.assertRaises(FileNotFoundError):
                ff.build(root / "missing", root, root / "media.zip", root)
            self.assert_contents(root, "previous")

    def test_publish_rolls_back_if_second_install_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()   # the same spelling build() uses (8.3 names on Windows)
            self.populate(root, "previous")
            real_rename = Path.rename

            def rename(source, target):
                if target == root / self.paths[1] and source.parent.name == "mods":
                    raise OSError("second install failed")
                return real_rename(source, target)

            def convert(ff_disc, fh1_disc, update_zip, stage, fh2_disc):
                self.populate(stage, "new")

            with mock.patch.object(ff, "_build_into", side_effect=convert):
                with mock.patch.object(Path, "rename", rename):
                    with self.assertRaisesRegex(OSError, "second install failed"):
                        ff.build(root, root, root / "media.zip", root)
                self.assert_contents(root, "previous")
                ff.build(root, root, root / "media.zip", root)
            self.assert_contents(root, "new")

    def test_archive_copies_nested_member(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()   # the same spelling build() uses (8.3 names on Windows)
            archive = root / "test.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("TRACKS/Colorado/Nested/Thing.xml", b"valid")
            output = root / "output"
            self.assertEqual(ff.copy_member(archive, "tracks/colorado/", output, root), 1)
            self.assertEqual((output / "nested" / "thing.xml").read_bytes(), b"valid")

    def test_archive_rejects_non_relative_components_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()   # the same spelling build() uses (8.3 names on Windows)
            archive = root / "test.zip"
            for relative in ("../marker", "/marker", "C:marker", "nested/.. /marker", "NUL"):
                with self.subTest(relative=relative):
                    with zipfile.ZipFile(archive, "w") as z:
                        z.writestr("tracks/colorado/" + relative, b"rejected")
                    output = root / "output"
                    with self.assertRaisesRegex(ValueError, "Unsafe archive member"):
                        ff.copy_member(archive, "tracks/colorado/", output, root)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
