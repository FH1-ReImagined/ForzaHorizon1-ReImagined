"""tools/fm4_routes.py (#648): a circuit's road network and race route from FM4's AI lines, and
tools/fm4_track_mod.py's use of it. Synthetic AI lines and road networks only (no game files)."""
import contextlib
import io
import math
import os
import re
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_routes  # noqa: E402
import fm4_track_mod  # noqa: E402

COLORADO_KEYS = fm4_routes.LOOKED_UP + ("grid_control", "uturn")  # Colorado has names of its own too

# A road network written out by hand in the layout of the player's colorado.nav (which reads and
# writes back byte for byte: 12,036 nodes, 459 ways): two nodes 10 m apart and one road between
# them, tagged oneway = true and road_type = a. Nothing here comes from fm4_routes.
HAND_NAV = bytes.fromhex(
    "0E177551"                                      # the magic word
    "00000002 00000001 00000002"                    # 2 nodes, 1 way, 2 tags
    "00000002 00000002"                             # 2 node-list entries, 2 memberships
    "00000002 00000002"                             # 2 tag names, 2 tag values
    "00000011 00000007"                             # 17 bytes of names, 7 of values
    # nodes, 32 bytes: id, x, y, z, memberships (count, first), tags (count, first)
    "00000000 00000000 00000000 00000000 00000001 00000000 00000000 00000000"
    "00000001 41200000 00000000 00000000 00000001 00000001 00000000 00000000"   # x = 10.0
    # ways, 16 bytes: nodes (count, first in the node list), tags (count, first)
    "00000002 00000000 00000002 00000000"
    "00000000 00000001"                             # the node list
    "00000000 00000000  00000000 00000001"          # memberships: (way, the node's index in it)
    "00000000 00000001  00000001 00000000"          # tags: (name, value) = (oneway, true), (road_type, a)
    "00000000 00000007"                             # where each name starts
    "00000000 00000002"                             # where each value starts
    "6F6E6577617900 726F61645F7479706500"           # "oneway", "road_type"
    "6100 7472756500"                               # "a", "true"
    "0E177551")                                     # the magic word again


def write_geo(path, points, half_width=6.0, chi=0.25):
    """An FM4 AI line (MLPData) through `points` (FM4's x, y, with z the height): the waypoints,
    the vector across the road (to the left of the way round, half_width long) and the racing
    line, as tools/fm4_routes.py reads them."""
    n = len(points)
    across = []
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        if math.dist(a, b) > 100:  # the end of a strip: the way the point before it runs
            a, b = points[i - 1], points[i]
        d = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        across.append((-(b[1] - a[1]) / d * half_width, (b[0] - a[0]) / d * half_width, 0.0))
    arrays = [("fWaypointX", [p[0] for p in points]), ("fWaypointY", [p[1] for p in points]),
              ("fWaypointZ", [p[2] for p in points]), ("fNormalX", [a[0] for a in across]),
              ("fNormalY", [a[1] for a in across]), ("fNormalZ", [a[2] for a in across]),
              ("fChiOptimal", [chi] * n)]
    head = "MLPDataStart\n" + "".join(f"{name}:float:{n}:1:{{{name}:>8}}\n" for name, _ in arrays) + "MLPDataEnd\n"
    size = len(head.format(**{name: 0 for name, _ in arrays}).encode())
    data = head.format(**{name: size + k * 4 * n for k, (name, _) in enumerate(arrays)}).encode()
    data += b"".join(struct.pack(f">{n}f", *values) for _, values in arrays)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def oval(length=1000.0, step=2.0, height=50.0):
    """A circle of `length` metres, a point every `step` (as FM4's lines: about 2 m)."""
    n = int(length / step)
    r = length / (2 * math.pi)
    return [(r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), height) for i in range(n)]


def colorado_nav(path, keys=COLORADO_KEYS):
    """A stand-in for the player's colorado.nav: one road, with the tag names it carries."""
    tags = {k: "x" for k in keys}
    data = fm4_routes.nav_of([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)], [([0, 1], tags)], values=("freeway", "b"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def roads_of(net):
    """[[node indexes]] of a network's roads (the ways with a road_type)."""
    road_type = net["keys"].index("road_type")
    out = []
    for count, first, tag_count, first_tag in net["ways"]:
        if any(net["tags"][first_tag + j][0] == road_type for j in range(tag_count)):
            out.append(net["node_list"][first:first + count])
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.said = io.StringIO()  # what the tools print
        quiet = contextlib.redirect_stdout(self.said)
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.ai = os.path.join(self.tmp.name, "Disc 1", "Media", "AI", "Tracks")
        self.geo = os.path.join(self.ai, "ring_00.geo")
        write_geo(self.geo, oval())
        self.fh1 = os.path.join(self.tmp.name, "fh1")
        self.colorado = os.path.join(self.fh1, "media", "tracks", "colorado", "colorado.nav")
        colorado_nav(self.colorado)


class AiLine(Base):
    def test_fm4_coordinates_become_fh1s(self):
        pts = fm4_routes.ai_line(self.geo)
        self.assertEqual(len(pts), 500)
        pos, across, chi = pts[0]
        r = 1000 / (2 * math.pi)
        # FM4's (x, y, z) is FH1's (x, z, y): the height is FH1's y
        self.assertAlmostEqual(pos[0], r, places=3)
        self.assertAlmostEqual(pos[1], 50.0, places=3)
        self.assertAlmostEqual(pos[2], 0.0, places=3)
        self.assertAlmostEqual(math.hypot(across[0], across[2]), 6.0, places=3)
        self.assertAlmostEqual(across[1], 0.0, places=5)
        self.assertAlmostEqual(chi, 0.25)
        self.assertTrue(fm4_routes.is_lap(pts))
        self.assertAlmostEqual(fm4_routes.length(pts), 1000.0, delta=0.1)

    def test_a_drag_strip_is_not_a_lap(self):
        strip = os.path.join(self.ai, "strip_00.geo")
        write_geo(strip, [(2.0 * i, 0.0, 5.0) for i in range(300)])
        self.assertFalse(fm4_routes.is_lap(fm4_routes.ai_line(strip)))

    def test_another_file_is_refused(self):
        bad = os.path.join(self.tmp.name, "bad.geo")
        with open(bad, "wb") as f:
            f.write(b"not an AI line")
        with self.assertRaises(ValueError):
            fm4_routes.ai_line(bad)

    def test_a_line_no_road_can_be_made_of_is_refused(self):
        # points that all coincide pass for a lap (its ends are 0 m apart), wild coordinates
        # would ask for a point every 2 m of them: both are refused with a ValueError, before
        # anything divides by the length or counts the points
        lines = {"still": [(5.0, 5.0, 1.0)] * 20,
                 "short": [(0.1 * i, 0.0, 1.0) for i in range(20)],
                 "wild": [(1e30 * i, 0.0, 1.0) for i in range(20)],
                 "nan": [(float("nan"), 2.0 * i, 1.0) for i in range(200)],
                 "inf": [(2.0 * i, float("inf"), 1.0) for i in range(200)]}
        for name, points in lines.items():
            geo = os.path.join(self.ai, f"{name}_00.geo")
            write_geo(geo, points)
            pts = fm4_routes.ai_line(geo)
            with self.assertRaises(ValueError, msg=name):
                fm4_routes.checked(pts, name)
            with self.assertRaises(ValueError, msg=name):
                fm4_routes.build(name, geo, self.colorado)
        good = fm4_routes.ai_line(self.geo)
        self.assertIs(fm4_routes.checked(good), good)
        still = fm4_routes.ai_line(os.path.join(self.ai, "still_00.geo"))
        self.assertTrue(fm4_routes.is_lap(still))                     # which is why checked() comes first
        for call in (lambda: fm4_routes.resample(still, 2.0), lambda: fm4_routes.route_xml(still),
                     lambda: fm4_routes.resample(fm4_routes.ai_line(os.path.join(self.ai, "wild_00.geo")), 2.0)):
            with self.assertRaises(ValueError):
                call()


class Route(Base):
    def test_owt_layout_and_round_trip(self):
        pts = fm4_routes.resample(fm4_routes.ai_line(self.geo), fm4_routes.OWT_SPACING)
        data = fm4_routes.owt(pts)
        self.assertEqual(data[:4], b"OWTM")
        self.assertEqual(struct.unpack_from(">7I", data, 4), (1, 0, 0, len(pts), 1, 0, 0))
        self.assertEqual(len(data), 0x20 + 48 * len(pts) + 16)
        self.assertEqual(data[-16:], b"OWTM" + struct.pack(">3I", 1, 0, 0))
        back, looped = fm4_routes.read_owt(data)
        self.assertTrue(looped)
        self.assertEqual(fm4_routes.owt(back, looped), data)
        # a point every 2 m, the half width and the racing line kept
        self.assertAlmostEqual(math.dist(back[0][0], back[1][0]), 2.0, delta=0.01)
        self.assertAlmostEqual(math.hypot(back[7][1][0], back[7][1][2]), 6.0, delta=0.01)
        self.assertAlmostEqual(back[7][2], 0.25, places=5)
        with self.assertRaises(ValueError):
            fm4_routes.read_owt(data[:100])

    def test_places_of_a_lap_race(self):
        pts = fm4_routes.ai_line(self.geo)
        xml = fm4_routes.route_xml(pts).decode()
        for name, count in (("start_location_", 8), ("end_race_cannon_left_", 5), ("end_race_cannon_right_", 5),
                            ("route_waypoint_", 5), ("end_race_cannon_trigger", 1), ("post_race_location_00", 1),
                            ("start_gantry_cannon_00", 1)):
            self.assertEqual(xml.count(f"name='{name}"), count, name)
        way = fm4_routes.waypoints_of(xml.encode())
        self.assertEqual(len(way), 5)  # every 250 m of the 1000 m, the last on the first again
        self.assertAlmostEqual(math.dist(way[0], way[-1]), 0.0, delta=0.01)
        self.assertAlmostEqual(math.dist(way[0], way[1]), 1000 / math.pi * math.sin(math.pi / 4), delta=0.5)
        # the grid is behind the start line (the line runs towards +z at its first point): the
        # first place 10 m back, the next 6 m behind it on the other side of the road
        grid = [tuple(float(v) for v in m) for m in re.findall(
            r"name='start_location_\d+'>\s*<Transform pos\.x='([^']+)' pos\.y='([^']+)' pos\.z='([^']+)'", xml)]
        r = 1000 / (2 * math.pi)
        self.assertAlmostEqual(grid[0][2], -10.0, delta=0.5)
        self.assertAlmostEqual(grid[1][2], -16.0, delta=0.5)
        self.assertLess(grid[0][0], r)   # 0.35 of the half width to one side,
        self.assertGreater(grid[1][0], r)  # then to the other
        self.assertAlmostEqual(grid[0][1], 50.5, places=3)  # half a metre over the road
        # the finish cannons' trigger is 58 m before the line, as in Colorado's TrackRoute049.xml
        # (57.9 m), and the cannons stand over the last 35 m
        def place(name):
            m = re.search(rf"name='{name}'[^>]*>\s*<Transform pos\.x='([^']+)' pos\.y='([^']+)' pos\.z='([^']+)'", xml)
            return tuple(float(v) for v in m.groups())
        chord = lambda metres: 2 * r * math.sin(metres / (2 * r))  # noqa: E731
        self.assertAlmostEqual(math.dist(place("end_race_cannon_trigger")[::2], way[0][::2]), chord(58.0), delta=0.3)
        self.assertLess(place("end_race_cannon_trigger")[2], place("end_race_cannon_left_00")[2])
        self.assertAlmostEqual(math.dist(place("post_race_location_00")[::2], way[0][::2]), chord(25.0), delta=1.6)


class Network(Base):
    def test_a_network_written_out_by_hand(self):
        net = fm4_routes.read_nav(HAND_NAV)
        self.assertEqual(net, {"nodes": [(0, 0.0, 0.0, 0.0, 1, 0, 0, 0), (1, 10.0, 0.0, 0.0, 1, 1, 0, 0)],
                               "ways": [(2, 0, 2, 0)], "node_list": [0, 1], "memberships": [(0, 0), (0, 1)],
                               "tags": [(0, 1), (1, 0)], "keys": ["oneway", "road_type"], "values": ["a", "true"]})
        self.assertEqual(fm4_routes.write_nav(net), HAND_NAV)
        # and the writer the converter uses makes the same file of the same road
        made = fm4_routes.nav_of([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)], [([0, 1], {"road_type": "a", "oneway": "true"})])
        self.assertEqual(made, HAND_NAV)
        self.assertEqual(roads_of(net), [[0, 1]])
        # a file cut short, or one that does not end on the magic word's place, is not read as less
        with self.assertRaises((ValueError, struct.error)):
            fm4_routes.read_nav(HAND_NAV[:60])

    def test_the_string_tables_are_in_order(self):
        # FH1 finds a tag name by halving the table, and Colorado's values are in order too
        net = fm4_routes.read_nav(fm4_routes.road_network(
            "Ring", [p[0] for p in fm4_routes.resample(fm4_routes.ai_line(self.geo), fm4_routes.NAV_SPACING)],
            keys=COLORADO_KEYS, values=("freeway", "b")))
        self.assertEqual(fm4_routes.check_network(fm4_routes.write_nav(net)), [])
        for table, word in (("keys", "names"), ("values", "values")):
            for change in (lambda t: t[1:2] + t[0:1] + t[2:],      # two swapped
                           lambda t: t[:1] + t[:1] + t[1:]):       # one twice
                bad = dict(net)
                bad[table] = change(net[table])
                problems = fm4_routes.check_network(fm4_routes.write_nav(bad))
                self.assertTrue(any(f"tag {word} are not in byte order" in line for line in problems), (table, problems))
        # byte order, not the alphabet's: "Z" comes before "a"
        mixed = dict(net)
        mixed["values"] = sorted(net["values"] + ["Zebra"], key=str.lower)
        self.assertNotEqual(mixed["values"], sorted(mixed["values"]))
        self.assertTrue(any("values" in line for line in fm4_routes.check_network(fm4_routes.write_nav(mixed))))

    def test_round_trip(self):
        with open(self.colorado, "rb") as f:
            d = f.read()
        net = fm4_routes.read_nav(d)
        self.assertEqual(fm4_routes.write_nav(net), d)
        self.assertEqual(net["keys"], sorted(COLORADO_KEYS))    # sorted: the game searches it
        self.assertEqual(struct.unpack_from(">I", d, 0)[0], fm4_routes.NAV_MAGIC)
        self.assertEqual(struct.unpack_from(">I", d, len(d) - 4)[0], fm4_routes.NAV_MAGIC)
        with self.assertRaises(ValueError):
            fm4_routes.read_nav(b"\0" * 64)

    def test_a_circuit_is_a_ring_of_open_roads(self):
        lap = [p[0] for p in fm4_routes.resample(fm4_routes.ai_line(self.geo), fm4_routes.NAV_SPACING)]
        self.assertEqual(len(lap), 49)                  # a node every 20 m of the 1000 m
        net = fm4_routes.read_nav(fm4_routes.road_network("Ring", lap, keys=COLORADO_KEYS))
        # one road round a lap this short would end on its own first node, and Colorado has no
        # closed road: it is cut in two
        roads = roads_of(net)
        self.assertEqual([(r[0], r[-1]) for r in roads], [(0, 24), (24, 0)])
        self.assertEqual(sum(len(r) - 1 for r in roads), 49)
        # a longer circuit is cut every 60 nodes
        long_lap = [p[0] for p in fm4_routes.resample(fm4_routes.ai_line(self.geo), 5.0)]
        self.assertEqual(len(long_lap), 199)
        net = fm4_routes.read_nav(fm4_routes.road_network("Ring", long_lap, keys=COLORADO_KEYS))
        roads = roads_of(net)
        self.assertEqual([len(r) for r in roads], [61, 61, 61, 20])
        for a, b in zip(roads, roads[1:] + roads[:1]):
            self.assertEqual(a[-1], b[0])               # each road ends on the next one's first node
            self.assertNotEqual(a[0], a[-1])            # and none is closed
        # the tags of every road, and the map's cursor_area round the circuit
        tags = {net["keys"][k]: net["values"][v] for k, v in net["tags"]}
        self.assertEqual(tags, {"road_type": "a", "traffic_disabled": "true", "display_name": "ring",
                                "area_type": "cursor_area"})
        count, first, _, _ = net["ways"][-1]
        area = [net["nodes"][i][1:4] for i in net["node_list"][first:first + count]]
        self.assertEqual(area[0], area[-1])
        r = 1000 / (2 * math.pi)
        self.assertAlmostEqual(max(p[0] for p in area), r + 400, delta=1)
        self.assertAlmostEqual(min(p[2] for p in area), -r - 400, delta=1)
        self.assertEqual({p[1] for p in area}, {0.0})

    def test_another_layouts_own_road_is_joined_at_both_ends(self):
        lap = [p[0] for p in fm4_routes.resample(fm4_routes.ai_line(self.geo), fm4_routes.NAV_SPACING)]
        # a layout that leaves the ring along a chord, from node 10 to node 30
        a, b = lap[10], lap[30]
        chord = [tuple(p + (q - p) * t / 12 for p, q in zip(a, b)) for t in range(13)]
        other = lap[:11] + chord[1:-1] + lap[30:]
        positions, ways = fm4_routes.circuit_roads(lap, [other], {"road_type": "a"})
        links = [w for w, _ in ways if any(i >= len(lap) for i in w)]
        self.assertEqual(len(links), 1)
        self.assertEqual((links[0][0], links[0][-1]), (10, 30))
        for w, _ in ways:
            self.assertNotEqual(w[0], w[-1])
        # the ring is cut at the two junctions
        ring = [w for w, _ in ways if all(i < len(lap) for i in w)]
        self.assertEqual([(w[0], w[-1]) for w in ring], [(0, 10), (10, 30), (30, 0)])
        # the same road all the way, or no shared road at all: no link, the lap's own two roads
        self.assertEqual(fm4_routes.circuit_roads(lap, [lap], {})[0], lap)
        away = [(x + 5000.0, y, z) for x, y, z in lap]
        positions, ways = fm4_routes.circuit_roads(lap, [away], {})
        self.assertEqual((positions, [(w[0], w[-1]) for w, _ in ways]), (lap, [(0, 24), (24, 0)]))

    def test_what_fh1_would_not_load_is_named(self):
        lap = [p[0] for p in fm4_routes.resample(fm4_routes.ai_line(self.geo), fm4_routes.NAV_SPACING)]
        good = fm4_routes.road_network("Ring", lap, keys=COLORADO_KEYS)
        self.assertEqual(fm4_routes.check_network(good, [lap[3], lap[20]]), [])
        # a closed road
        ring = list(range(len(lap))) + [0]
        closed = fm4_routes.nav_of(lap, [(ring, {"road_type": "a"})], COLORADO_KEYS)
        self.assertIn("closed", " ".join(fm4_routes.check_network(closed)))
        # the tag names the game looks up, missing (a network's own three are not enough)
        bare = " ".join(fm4_routes.check_network(fm4_routes.road_network("Ring", lap)))
        self.assertIn("oneway", bare)
        self.assertIn("sat_nav_junction", bare)
        self.assertNotIn("road_type", bare)
        # a route waypoint away from every road
        far = fm4_routes.check_network(good, [lap[3], (9000.0, 0.0, 9000.0)])
        self.assertEqual(far, ["route waypoint 1 has no road node within 20 m"])


class CircuitFiles(Base):
    def test_a_laps_files(self):
        files = fm4_routes.build("Ring", self.geo, self.colorado)
        self.assertEqual(sorted(files), ["aiopenworld/ring/ribbon_00/route_101.owt",
                                         "tracks/ring/ribbon_00/trackroute101.xml",
                                         "tracks/ring/ring.nav", "tracks/ring/ring.owr"])
        self.assertEqual(files["tracks/ring/ring.owr"],
                         struct.pack(">3I", fm4_routes.NAV_MAGIC, 0, fm4_routes.NAV_MAGIC))
        pts, looped = fm4_routes.read_owt(files["aiopenworld/ring/ribbon_00/route_101.owt"])
        self.assertTrue(looped)
        self.assertEqual(len(pts), 499)        # a point every 2 m
        net = fm4_routes.read_nav(files["tracks/ring/ring.nav"])
        self.assertIn("oneway", net["keys"])   # Colorado's tag names and values, with the circuit's own
        self.assertIn("freeway", net["values"])
        self.assertIn("ring", net["values"])
        # every route waypoint has a road node within reach
        way = fm4_routes.waypoints_of(files["tracks/ring/ribbon_00/trackroute101.xml"])
        self.assertEqual(fm4_routes.check_network(files["tracks/ring/ring.nav"], way), [])

    def test_a_drag_strip_gets_its_road_and_no_race_route(self):
        strip = os.path.join(self.ai, "strip_00.geo")
        write_geo(strip, [(2.0 * i, 0.0, 5.0) for i in range(300)])
        files = fm4_routes.build("Strip", strip, self.colorado)
        self.assertEqual(sorted(files), ["tracks/strip/strip.nav", "tracks/strip/strip.owr"])
        roads = roads_of(fm4_routes.read_nav(files["tracks/strip/strip.nav"]))
        self.assertEqual(len(roads), 1)
        self.assertNotEqual(roads[0][0], roads[0][-1])

    def test_colorados_network_without_the_tag_names_is_refused(self):
        colorado_nav(self.colorado, keys=("road_type",))
        with self.assertRaises(ValueError) as e:
            fm4_routes.build("Ring", self.geo, self.colorado)
        self.assertIn("oneway", str(e.exception))

    def test_the_command_line(self):
        out = os.path.join(self.tmp.name, "out")
        self.assertEqual(fm4_routes.main(["fm4_routes.py", "write", self.geo, self.colorado, "Ring", out]), 0)
        self.assertTrue(os.path.isfile(os.path.join(out, "media", "tracks", "ring", "ring.nav")))
        self.assertTrue(os.path.isfile(os.path.join(out, "media", "aiopenworld", "ring", "ribbon_00", "route_101.owt")))
        self.assertEqual(fm4_routes.main(["fm4_routes.py", "write", self.colorado, self.colorado, "Ring", out]), 1)
        self.assertEqual(fm4_routes.main(["fm4_routes.py"]), 2)


# A map profile in the layout of FH1's media/ui/MapProfileMinimap.xml (written out here, none
# of the game's): a road group with an outline and its two colours, one with a single colour, a
# route and an icon group that are no roads, and a comment.
PROFILE = b"""<MapRenderProfiles>\r
    <!-- MINI MAP -->\r
    <Profile name="minimap" >\r
      <Group name="a roads" >\r
        <Symbolizer type="line_fade_lr" sort_order="90" >\r
          <Size value="14" />\r
          <ColourPrimary r="0" g="0" b="0" a="128" />\r
          <ColourSecondary r="0" g="0" b="0" a="128" />\r
        </Symbolizer>\r
        <Symbolizer type="line_coloured_noalpha" sort_order="110" >\r
          <Size value="14" />\r
          <ColourPrimary r="122" g="122" b="122" a="255" />\r
          <ColourSecondary r="255" g="255" b="255" a="255" />\r
        </Symbolizer>\r
        <Filter tag="road_type" value="a" />\r
      </Group>\r
      <Group name="freeways" >\r
        <Symbolizer type="line_coloured" sort_order="110" >\r
            <ColourSecondary r="199" g="182" b="99" a="255" />     \r
            <ColourPrimary r="120" g="120" b="120" a="255" />\r
        </Symbolizer>\r
        <Symbolizer type="line_fade_lr" sort_order="90" >\r
          <ColourPrimary r="0" g="0" b="0" a="255" />\r
        </Symbolizer>\r
        <Filter tag="road_type" value="freeway" />\r
      </Group>\r
      <Group name="route" >\r
        <Symbolizer type="line_coloured" sort_order="165" >\r
          <ColourPrimary r="74" g="238" b="97" a="255" />\r
          <ColourSecondary r="74" g="238" b="97" a="0" />\r
        </Symbolizer>\r
        <Filter tag="type" value="route" />\r
      </Group>\r
      <Group name="gas_station" >\r
        <Symbolizer type="icon_facing" sort_order="420" >\r
          <ColourPrimary r="255" g="255" b="255" a="255" />\r
        </Symbolizer>\r
        <Filter tag="activity_type" value="gas_station" />\r
      </Group>\r
    </Profile>\r
</MapRenderProfiles>\r
"""


class MapProfiles(Base):
    """The circuit's map profiles: FH1's own with every road in its driven colour."""

    def test_a_road_not_driven_yet_gets_its_driven_colour(self):
        out = fm4_routes.discovered_roads(PROFILE)
        was, now = PROFILE.split(b"\r\n"), out.split(b"\r\n")
        self.assertEqual(len(was), len(now))
        changed = [(a.strip(), b.strip()) for a, b in zip(was, now) if a != b]
        self.assertEqual(changed, [
            (b'<ColourPrimary r="122" g="122" b="122" a="255" />', b'<ColourPrimary r="255" g="255" b="255" a="255" />'),
            (b'<ColourPrimary r="120" g="120" b="120" a="255" />', b'<ColourPrimary r="199" g="182" b="99" a="255" />')])
        # the route's fading green, the icons and the roads' outlines are as they were
        self.assertIn(b'<ColourSecondary r="74" g="238" b="97" a="0" />', out)
        self.assertEqual(out.count(b'r="0" g="0" b="0"'), PROFILE.count(b'r="0" g="0" b="0"'))
        self.assertEqual(fm4_routes.discovered_roads(out), out)   # done once is done

    def test_a_file_that_is_no_profile_is_left_alone(self):
        for data in (b"", b"<a/>", b"\xff\xfe\x00 not xml <Group", PROFILE.replace(b"road_type", b"road_kind")):
            self.assertEqual(fm4_routes.discovered_roads(data), data)

    def test_the_games_two_profiles(self):
        ui = os.path.join(self.fh1, "Media", "UI")
        os.makedirs(ui)
        self.assertEqual(fm4_routes.map_profiles(ui), {})                  # none there
        self.assertEqual(fm4_routes.map_profiles(os.path.join(ui, "no")), {})
        with open(os.path.join(ui, "mapprofileminimap.XML"), "wb") as f:   # any case
            f.write(PROFILE)
        with open(os.path.join(ui, "MapProfileFullscreen.xml"), "wb") as f:
            f.write(b"<MapRenderProfiles/>")                               # no road group: left out
        with open(os.path.join(ui, "LoadingDefs.xml"), "wb") as f:
            f.write(PROFILE)                                               # not a map profile
        files = fm4_routes.map_profiles(ui)
        self.assertEqual(list(files), ["ui/MapProfileMinimap.xml"])
        self.assertEqual(files["ui/MapProfileMinimap.xml"], fm4_routes.discovered_roads(PROFILE))

    def test_the_command_line(self):
        ui = os.path.join(self.fh1, "media", "ui")
        os.makedirs(ui)
        out = os.path.join(self.tmp.name, "out")
        self.assertEqual(fm4_routes.main(["fm4_routes.py", "profiles", ui, out]), 1)   # nothing to write
        with open(os.path.join(ui, "MapProfileMinimap.xml"), "wb") as f:
            f.write(PROFILE)
        self.assertEqual(fm4_routes.main(["fm4_routes.py", "profiles", ui, out]), 0)
        with open(os.path.join(out, "media", "ui", "MapProfileMinimap.xml"), "rb") as f:
            self.assertEqual(f.read(), fm4_routes.discovered_roads(PROFILE))


class TrackMod(Base):
    """tools/fm4_track_mod.py's part: which AI line, and the files in the mod."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(os.path.dirname(self.colorado), "colorado.oww"), "wb") as f:
            f.write(b"OWWF" + b"\0" * 28)
        self.out = os.path.join(self.tmp.name, "mod")

    def test_the_layouts_line_and_the_others(self):
        for n in (1, 2):
            write_geo(os.path.join(self.ai, f"Ring_{n:02d}.geo"), oval(600.0))
        write_geo(os.path.join(self.ai, "ringroad_00.geo"), oval(400.0))  # another circuit
        own, others = fm4_track_mod.ai_lines(self.ai, "Ring", 1)
        self.assertEqual(os.path.basename(own), "Ring_01.geo")
        self.assertEqual([os.path.basename(p) for p in others], ["ring_00.geo", "Ring_02.geo"])
        own, _ = fm4_track_mod.ai_lines(self.ai, "Ring", 7)      # no line of its own: the first
        self.assertEqual(os.path.basename(own), "ring_00.geo")
        self.assertEqual(fm4_track_mod.ai_lines(self.ai, "Oval", 0), (None, []))

    def test_the_ribbon_a_circuit_without_ribbon_0_is_converted_from(self):
        track = os.path.join(self.tmp.name, "LeMans")
        for n in (1, 2):
            os.makedirs(os.path.join(track, f"Ribbon_{n:02d}"))
        self.assertEqual(fm4_track_mod.fm4_ribbon(track, 0), 1)
        self.assertEqual(fm4_track_mod.fm4_ribbon(track, 2), 2)
        os.makedirs(os.path.join(track, "Ribbon_00"))
        self.assertEqual(fm4_track_mod.fm4_ribbon(track, 0), 0)

    def test_the_files_go_into_the_mod(self):
        # the circuit's own ribbon folder, as FM4 spells it
        os.makedirs(os.path.join(self.out, "media", "tracks", "ring", "Ribbon_00"))
        lap = fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1)
        self.assertAlmostEqual(lap, 1000.0, delta=0.1)
        media = os.path.join(self.out, "media")
        ribbons = [n for n in os.listdir(os.path.join(media, "tracks", "ring")) if n.lower() == "ribbon_00"]
        self.assertEqual(ribbons, ["Ribbon_00"])            # no second folder beside it
        self.assertEqual(os.listdir(os.path.join(media, "tracks", "ring", "Ribbon_00")), ["trackroute101.xml"])
        for rel in ("tracks/ring/ring.nav", "tracks/ring/ring.owr", "tracks/ring/ring.oww",
                    "tracks/ring/Ribbon_00/trackroute101.xml", "aiopenworld/ring/ribbon_00/route_101.owt"):
            self.assertTrue(os.path.isfile(os.path.join(media, *rel.split("/"))), rel)
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.startswith("fm4routes-")], [])   # the staging folder
        with open(os.path.join(media, "tracks", "ring", "ring.oww"), "rb") as f:
            self.assertEqual(f.read()[:4], b"OWWF")     # Colorado's, as the circuit's
        self.assertFalse(os.path.exists(os.path.join(media, "ui")))    # this game folder has no map profiles

    def test_the_map_profiles_go_into_the_mod(self):
        ui = os.path.join(self.fh1, "media", "UI")      # the game's folder, any case
        os.makedirs(ui)
        with open(os.path.join(ui, "MapProfileMinimap.xml"), "wb") as f:
            f.write(PROFILE)
        self.assertIsNotNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1))
        with open(os.path.join(self.out, "media", "ui", "MapProfileMinimap.xml"), "rb") as f:
            self.assertEqual(f.read(), fm4_routes.discovered_roads(PROFILE))
        with open(os.path.join(ui, "MapProfileMinimap.xml"), "rb") as f:
            self.assertEqual(f.read(), PROFILE)         # the game's own is never changed
        self.assertTrue(os.path.isfile(os.path.join(self.out, "media", "tracks", "ring", "ring.nav")))

    def test_the_other_layouts_roads_are_left_out(self):
        # Ring's other layouts leave the lap along roads of their own: the circuit's world is
        # one layout, so its network is that layout's lap alone (two roads for a lap this short)
        lap = [p[0] for p in fm4_routes.resample(fm4_routes.ai_line(self.geo), fm4_routes.NAV_SPACING)]
        a, b = lap[10], lap[30]
        chord = [tuple(p + (q - p) * t / 60 for p, q in zip(a, b)) for t in range(61)]
        write_geo(os.path.join(self.ai, "Ring_01.geo"), [(x, z, y) for x, y, z in lap[:11] + chord[1:-1] + lap[30:]])
        self.assertIsNotNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1))
        with open(os.path.join(self.out, "media", "tracks", "ring", "ring.nav"), "rb") as f:
            roads = roads_of(fm4_routes.read_nav(f.read()))
        self.assertEqual([(r[0], r[-1]) for r in roads], [(0, 24), (24, 0)])
        # the tool itself still joins them when it is given the lines
        files = fm4_routes.build("Ring", self.geo, self.colorado, [os.path.join(self.ai, "Ring_01.geo")])
        self.assertEqual(len(roads_of(fm4_routes.read_nav(files["tracks/ring/ring.nav"]))), 4)

    def test_whatever_goes_wrong_the_import_goes_on(self):
        # the step is optional: no error of its own reaches the import, and nothing is left behind
        os.makedirs(os.path.join(self.out, "media", "tracks", "ring", "Ribbon_00"))
        write_geo(os.path.join(self.ai, "Still_00.geo"), [(5.0, 5.0, 1.0)] * 20)
        before = sorted(os.path.join(d, n) for d, ds, fs in os.walk(self.tmp.name) for n in ds + fs)

        def nothing_left():
            self.assertEqual(sorted(os.path.join(d, n) for d, ds, fs in os.walk(self.tmp.name) for n in ds + fs), before)
        for error in (ZeroDivisionError("float modulo"), RuntimeError("anything"), MemoryError(), OverflowError("n"),
                      RecursionError(), AssertionError(), TypeError("x"), OSError(28, "No space left on device")):
            with mock.patch.object(fm4_routes, "build", side_effect=error):
                self.assertIsNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1), error)
            nothing_left()
        self.assertIn("no road network: ZeroDivisionError: float modulo", self.said.getvalue())
        # an AI line whose points all coincide (it passes for a lap): refused, not divided by
        self.assertIsNone(fm4_track_mod.add_routes(self.out, "Still", 0, self.ai, self.fh1))
        self.assertIn("no road network: Still_00.geo: 0 m of line", self.said.getvalue())
        nothing_left()
        # a file that cannot be written, and a move that fails after others were made: every
        # file and folder the step made is taken out again
        real_open, real_replace, moves = open, os.replace, []

        def no_third_file(path, *a, **k):
            if os.path.basename(os.path.dirname(str(path))).startswith("fm4routes-") and os.path.basename(str(path)) == "2":
                raise OSError(28, "No space left on device")
            return real_open(path, *a, **k)

        def no_third_move(src, dst):
            moves.append(dst)
            if len(moves) == 5:     # the last one, after the folders of the route's line were made
                raise PermissionError(13, "Permission denied", dst)
            return real_replace(src, dst)
        with mock.patch("builtins.open", no_third_file):
            self.assertIsNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1))
        nothing_left()
        with mock.patch.object(os, "replace", no_third_move):
            self.assertIsNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1))
        self.assertEqual(len(moves), 5)
        nothing_left()
        # and with nothing in the way the same call writes all five
        self.assertAlmostEqual(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1), 1000.0, delta=0.1)
        self.assertEqual(sum(len(fs) for _, _, fs in os.walk(os.path.join(self.out, "media"))), 5)

    def test_the_command_line_needs_the_free_roam_world(self):
        # the route's files go in ribbon_00, which only --free-roam makes of a circuit without
        # one (Le Mans): --fm4-ai without it would make a second, lower-case ribbon folder
        track = os.path.join(self.tmp.name, "Disc 1", "Media", "tracks", "Ring")
        os.makedirs(track)
        for extra in (["--fh1-disc", self.fh1], ["--free-roam", "317"], []):
            argv = ["fm4_track_mod.py", track, "media.zip", self.out, "--fm4-ai", self.ai] + extra
            with mock.patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()) as said:
                with self.assertRaises(SystemExit) as e:
                    fm4_track_mod.main()
            self.assertEqual(e.exception.code, 2)
            self.assertIn("--fm4-ai needs --fh1-disc and --free-roam", said.getvalue())
        self.assertFalse(os.path.exists(self.out))          # refused before anything is written

    def test_a_build_without_the_tool_converts_as_before(self):
        with mock.patch.dict(sys.modules, {"fm4_routes": None}):  # as if tools/fm4_routes.py were not there
            self.assertIsNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1))
        self.assertFalse(os.path.exists(self.out))
        self.assertIn("no road network", self.said.getvalue())

    def test_a_circuit_without_a_line_or_a_loadable_network_keeps_what_it_had(self):
        self.assertIsNone(fm4_track_mod.add_routes(self.out, "Oval", 0, self.ai, self.fh1))
        self.assertFalse(os.path.exists(self.out))
        colorado_nav(self.colorado, keys=("road_type",))  # the tag names are missing: nothing written
        self.assertIsNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1))
        self.assertFalse(os.path.exists(self.out))
        os.remove(self.colorado)                            # no colorado.nav at all
        self.assertIsNone(fm4_track_mod.add_routes(self.out, "Ring", 0, self.ai, self.fh1))
        self.assertFalse(os.path.exists(self.out))


if __name__ == "__main__":
    unittest.main()
