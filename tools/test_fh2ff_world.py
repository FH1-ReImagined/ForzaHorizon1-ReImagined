"""tools/fh2ff_world.py and fm4_pvs.py's version 51 (#821): Fast & Furious's world as an FH1 one.
Synthetic files (no game files); with FH2FF_DISC=<F&F's disc folder> set, F&F's own world is
converted in memory too (RealWorld)."""
import os
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_pvs  # noqa: E402
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
        self.assertEqual(n["keys"], ["ai_disabled", "oneway", "road_type", "traffic_density"])
        bad = net([], [], ["a", "oneway_forward", "z"], ["x"])
        bad["keys"] = ["zz", "oneway_forward"]
        with self.assertRaises(ValueError):
            ff.fix_tags(bad)

    def test_spawn_on_the_longest_stretch_inside(self):
        nodes = [(x, 5, 0) for x in range(0, 1000, 100)] + [(0, 5, z) for z in range(0, 300, 100)]
        n = net([(list(range(10)), {"road_type": "a"}), ([10, 11, 12], {"road_type": "b"}),
                 ([0, 9], {"area_type": "lake"})], nodes, ["area_type", "road_type"], ["a", "b", "lake"])
        spawn, line, length = ff.spawn_of(n, lambda x, z: x < 650)
        self.assertEqual(spawn, "0.0,5.5,0.0,1.000,0.000")
        self.assertEqual(length, 600)
        self.assertEqual(len(line.splitlines()), 7)
        with self.assertRaises(ValueError):
            ff.spawn_of(n, lambda x, z: False)


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

    def test_silent_tile_is_xml(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(ff.SILENT_TILE)
        self.assertEqual(root.tag, "AudioTile")
        self.assertEqual(len(root.find("AmbienceZones")), 0)


@unittest.skipUnless(os.environ.get("FH2FF_DISC"), "FH2FF_DISC (F&F's disc folder) is not set: no real world is read")
class RealWorld(unittest.TestCase):
    def test_converts_whole(self):
        self.assertTrue(ff.check(os.environ["FH2FF_DISC"]))


if __name__ == "__main__":
    unittest.main()
