#!/usr/bin/env python3
"""A converted FM4 circuit's road network and race route, from FM4's own AI line (#648).

FH1's sat-nav and its races read files a track of FM4's does not have:

  media/tracks/<track>/<track>.nav                   the road network: what the GPS and the
                                                     minimap draw, and what a race plans its
                                                     route on
  media/tracks/<track>/<track>.owr, <track>.oww      two small headers next to it (the .oww is
                                                     Colorado's, copied by fm4_track_mod.py)
  media/tracks/<track>/ribbon_00/trackrouteNNN.xml   a route's places: the grid, the finish,
                                                     the waypoints (NNN: the Tracks row's RouteId)
  media/aiopenworld/<track>/ribbon_00/route_NNN.owt  a route's line: the road's centre, its half
                                                     width and the racing line, every 2 m

and FH1's maps draw a road grey until the player has driven it. On a circuit that grey is lost
on the tarmac under the minimap, so a circuit also gets FH1's own two map profiles with every
road in its driven colour (media/ui/MapProfileMinimap.xml, MapProfileFullscreen.xml:
discovered_roads). They load with the circuit's mod, so Colorado's maps stay as they are.

This writes them from FM4's AI line of the layout, media/AI/Tracks/<track>_NN.geo (an "MLPData"
file, tools/fm4_spawn.py): its waypoints run round the lap from the start/finish line, each with
a vector across the road as long as half its width (fNormal) and the racing line's place on it
(fChiOptimal, -1..1). An FM4 (X, Y, Z) is FH1's (X, Z, Y).

What FH1 needs of a road network, found on Title Update 4 with a circuit loaded as a track of
its own:
  - the tag names the game looks up, in the file's own table, which is sorted (a binary
    search, sub_82D9D4A8). It asks for "oneway" and others by name and uses the answer
    unchecked: a network without them crashed free roam (sub_82D98D20 to sub_82D9C8E0, for
    each road once there is more than one) and the start of a race. So the table carries
    Colorado's names and values (LOOKED_UP: the names the game asks for);
  - the map screen's cursor_area, a rectangle round the circuit: without it the circuit sat
    in a corner of the map.
Kept from the network that then worked, not isolated as needs of their own: no closed road
(Colorado has none, its closed ways are areas: a lap is a ring of open ways that share their
end nodes, cut every ROAD_NODES nodes), and a road node near every route waypoint.

The network is the road of the layout the circuit's world is. Its other layouts' own stretches
of road are left out: the layout's barriers close them, and a road on the map that cannot be
reached would mislead the map and whatever plans a route. "write ... OTHER_GEO" joins them, for
the day barriers go by route.

A race row's RouteId is not 0 (free roam's own): RACE_ROUTE. FH1 loaded a track's world from
ribbon 0 whatever the row's RibbonIndex was, and the route's files from the row's ribbon, so
they go in ribbon_00. No Tracks row uses the route yet: nothing reads its two files.

Usage:
  tools/fm4_routes.py write GEO COLORADO_NAV TRACK OUT_DIR [OTHER_GEO ...]
      writes the files above under OUT_DIR/media/ for the circuit TRACK (its folder's name);
      OTHER_GEO: the AI lines of the circuit's other layouts, whose own stretches of road
      join the network (no route is written for them; the import passes none, see above)
  tools/fm4_routes.py profiles UI_DIR OUT_DIR
      writes FH1's two map profiles of UI_DIR (the game's media/ui) with every road in its
      driven colour under OUT_DIR/media/ui/
  tools/fm4_routes.py nav FILE.nav      prints a road network's counts and tag names
  tools/fm4_routes.py owt FILE.owt      prints a route's length and width
"""
import math
import os
import re
import struct
import sys

NAV_MAGIC = 0x0E177551
RACE_ROUTE = 101        # the RouteId of the circuit's race (0 is free roam's)
OWT_SPACING = 2.0       # metres between a route's points, as Colorado's
NAV_SPACING = 20.0      # metres between road nodes (Colorado's median)
ROAD_NODES = 60         # a road's nodes at most: the ring is cut into open ways
LINK_DISTANCE = 15.0    # another layout's line this far from the circuit's is a road of its own
CURSOR_MARGIN = 400.0   # metres round the circuit: the map screen's cursor_area
CLOSED_GAP = 60.0       # a line whose ends are farther apart is not a lap (a drag strip, a stage)
MIN_LINE = 100.0        # metres: a shorter AI line is no road (one whose points coincide)
MAX_LINE = 100000.0     # and a longer one is no circuit's (FM4's longest lap is under 26 km)
FINISH_TRIGGER = 58.0   # metres before the line: the finish cannons' trigger, as Colorado's
ROAD_TAGS = {"road_type": "a", "traffic_disabled": "true"}
# The tag names TU4 looks up in a road network (all of them are in Colorado's table).
LOOKED_UP = ("ai_disabled", "allowedfordemo", "area_type", "deadend", "discovery_zone", "display_name",
             "first_time_flow_disabled", "iefestivaldrive", "oneway", "road_type", "sat_nav_junction",
             "traffic_density", "traffic_disabled")


# ---- FM4's AI line --------------------------------------------------------------------------------

def ai_line(path):
    """[(position, across, racing line)] of an FM4 AI line, in FH1's coordinates: across is the
    vector to the road's edge (as long as half the road's width), the racing line a share of it."""
    with open(path, "rb") as f:
        d = f.read()
    end = d.find(b"MLPDataEnd")
    if not d.startswith(b"MLPDataStart") or end < 0:
        raise ValueError(f"{path}: not an MLPData file")
    arrays = {m.group(1): (m.group(2), int(m.group(3)), int(m.group(4)))
              for m in re.finditer(r"(\w+):(\w+):(\d+):\d+:\s+(\d+)", d[:end].decode("latin1"))}

    def floats(name, default=None):
        if name not in arrays:
            if default is None:
                raise ValueError(f"{path}: no {name}")
            return [default] * arrays["fWaypointX"][1]
        typ, n, off = arrays[name]
        if typ != "float":
            raise ValueError(f"{path}: {name} is {typ}")
        return struct.unpack_from(f">{n}f", d, off)
    x, y, z = floats("fWaypointX"), floats("fWaypointY"), floats("fWaypointZ")
    nx, ny, nz = floats("fNormalX"), floats("fNormalY"), floats("fNormalZ", 0.0)
    chi = floats("fChiOptimal", 0.0)
    return [((x[i], z[i], y[i]), (nx[i], nz[i], ny[i]), chi[i]) for i in range(len(x))]


def is_lap(pts):
    """Whether the line ends where it starts (a circuit, not a drag strip or a stage)."""
    return len(pts) >= 8 and math.dist(pts[0][0], pts[-1][0]) <= CLOSED_GAP


def checked(pts, name="the AI line"):
    """pts, if a road can be made of the line: every number finite, and MIN_LINE to MAX_LINE
    metres from its first point to its last. A file of points that coincide, or of wild
    coordinates, is refused here (ValueError) before anything divides by its length or makes a
    point every 2 m of it."""
    if not all(math.isfinite(v) for pos, across, chi in pts for v in (*pos, *across, chi)):
        raise ValueError(f"{name}: a waypoint that is not a number")
    total = length(pts, closed=False) if len(pts) > 1 else 0.0
    if not MIN_LINE <= total <= MAX_LINE:
        raise ValueError(f"{name}: {total:.0f} m of line (a circuit's is {MIN_LINE:.0f} m to "
                         f"{MAX_LINE / 1000:.0f} km)")
    return pts


def _mix(a, b, t):
    return tuple(p + (q - p) * t for p, q in zip(a, b))


def length(pts, closed=True):
    seq = pts + ([pts[0]] if closed else [])
    return sum(math.dist(a[0], b[0]) for a, b in zip(seq, seq[1:]))


def resample(pts, spacing, closed=True):
    """The line with a point every `spacing` metres (the lap divided evenly)."""
    seq = pts + ([pts[0]] if closed else [])
    cum = [0.0]
    for a, b in zip(seq, seq[1:]):
        cum.append(cum[-1] + math.dist(a[0], b[0]))
    if not 0.0 < cum[-1] <= 2 * MAX_LINE:  # see checked(): never a point count without bound
        raise ValueError(f"a line of {cum[-1]:.0f} m cannot be resampled")
    n = max(2, int(cum[-1] // spacing))
    step = cum[-1] / n
    out, j = [], 0
    for k in range(n if closed else n + 1):
        s = k * step
        while j < len(cum) - 2 and cum[j + 1] < s:
            j += 1
        t = (s - cum[j]) / ((cum[j + 1] - cum[j]) or 1.0)
        a, b = seq[j], seq[j + 1]
        out.append((_mix(a[0], b[0], t), _mix(a[1], b[1], t), a[2] + (b[2] - a[2]) * t))
    return out


def _at(pts, s):
    """(position, across, unit heading (x, z)) at s metres round a lap from its first point
    (negative: behind it)."""
    n = len(pts)
    seg = [math.dist(pts[i][0], pts[(i + 1) % n][0]) for i in range(n)]
    if not sum(seg) > 0.0:
        raise ValueError("a lap without length")
    s %= sum(seg)
    i = 0
    while s > seg[i] and i < n - 1:
        s -= seg[i]
        i += 1
    a, b = pts[i], pts[(i + 1) % n]
    t = s / (seg[i] or 1.0)
    fx, fz = b[0][0] - a[0][0], b[0][2] - a[0][2]
    f = math.hypot(fx, fz) or 1.0
    return _mix(a[0], b[0], t), _mix(a[1], b[1], t), (fx / f, fz / f)


# ---- route_NNN.owt --------------------------------------------------------------------------------

def owt(pts, looped=True):
    """An "OWTM" route: a 0x20-byte header (version 1, the count at 0x10, looped at 0x14), 48
    bytes a point (the centre, the vector across, the racing line, three zero words) and a
    16-byte end mark."""
    out = bytearray(b"OWTM" + struct.pack(">7I", 1, 0, 0, len(pts), 1 if looped else 0, 0, 0))
    for pos, across, chi in pts:
        out += struct.pack(">12f", *pos, 0.0, *across, 0.0, chi, 0.0, 0.0, 0.0)
    return bytes(out + b"OWTM" + struct.pack(">3I", 1, 0, 0))


def read_owt(data):
    """([(position, across, racing line)], looped) of a route file."""
    if data[:4] != b"OWTM" or len(data) < 0x30:
        raise ValueError("not an OWTM file")
    n, looped = struct.unpack_from(">2I", data, 0x10)
    if len(data) < 0x20 + 48 * n:
        raise ValueError("an OWTM file cut short")
    pts = []
    for i in range(n):
        r = struct.unpack_from(">12f", data, 0x20 + 48 * i)
        pts.append((r[0:3], r[4:7], r[8]))
    return pts, bool(looped)


# ---- TrackRouteNNN.xml ----------------------------------------------------------------------------

def _num(v):
    return f"{v:.6g}"


def _place(name, pos, heading, extra=""):
    return [f"\t\t<NamedTransform name='{name}'{extra}>",
            f"\t\t\t<Transform pos.x='{_num(pos[0])}' pos.y='{_num(pos[1])}' pos.z='{_num(pos[2])}' "
            f"facing.x='{_num(heading[0])}' facing.y='0.0' facing.z='{_num(heading[1])}'/>",
            "\t\t</NamedTransform>"]


def _beside(pos, across, metres, lift=0.0):
    """pos moved `metres` along the vector across the road, and `lift` up."""
    n = math.hypot(across[0], across[2]) or 1.0
    return (pos[0] + across[0] / n * metres, pos[1] + lift, pos[2] + across[2] / n * metres)


def route_xml(pts, grid=8, grid_spacing=6.0, waypoint_spacing=250.0):
    """A lap race's places, laid out as Colorado's circuits are (its TrackRoute049.xml): five
    finish cannons a side over the last 35 m, their trigger FINISH_TRIGGER metres before the
    line (Colorado's is 57.9), the place the car is put after the race, a route waypoint about
    every 250 m from the start line round to it again, the start gantry's cannon, and `grid`
    start places behind the line in two staggered columns (the first 10 m back)."""
    total = length(pts)
    out = ["<TrackRoute>", "\t<NamedTransforms>"]
    for k in range(5):
        for side, sign in (("left", 1.0), ("right", -1.0)):
            pos, across, f = _at(pts, total - 35.0 + 8.0 * k)
            half = math.hypot(across[0], across[2])
            out += _place(f"end_race_cannon_{side}_{k:02d}", _beside(pos, across, sign * (half + 3.0)), f)
    pos, across, f = _at(pts, total - FINISH_TRIGGER)
    out += _place("end_race_cannon_trigger", _beside(pos, across, 0.0, 0.5), f, " width='50.0'")
    pos, across, f = _at(pts, total - 25.0)
    out += _place("post_race_location_00", _beside(pos, across, 1.5, 0.3), (-f[0], -f[1]))
    count = max(4, int(round(total / waypoint_spacing)))
    for k in range(count + 1):
        pos, across, f = _at(pts, total * k / count)
        out += _place(f"route_waypoint_{k:02d}", _beside(pos, across, 0.0, 0.8), f)
    pos, across, f = _at(pts, 1.0)
    out += _place("start_gantry_cannon_00", _beside(pos, across, 0.0, 12.0), (-f[0], -f[1]))
    for k in range(grid):
        pos, across, f = _at(pts, -(10.0 + grid_spacing * k))
        half = math.hypot(across[0], across[2])
        out += _place(f"start_location_{k:02d}",
                      _beside(pos, across, (0.35 if k % 2 == 0 else -0.35) * half, 0.5), f)
    out += ["\t</NamedTransforms>", "</TrackRoute>", ""]
    return "\r\n".join(out).encode()


def waypoints_of(xml):
    """[(x, y, z)] of a TrackRoute file's route waypoints, in order."""
    found = re.findall(r"name='route_waypoint_(\d+)'>\s*<Transform pos\.x='([^']+)' pos\.y='([^']+)' "
                       r"pos\.z='([^']+)'", xml.decode())
    return [tuple(float(v) for v in row[1:]) for row in sorted(found, key=lambda row: int(row[0]))]


# ---- <track>.nav ----------------------------------------------------------------------------------

def read_nav(d):
    """A road network (big-endian): a 40-byte header (the magic word, then the counts of nodes,
    ways, tags, node-list entries, memberships, tag names and tag values, and the bytes of the
    two string tables), nodes of 32 bytes (id, x, y, z, the count and first of its memberships,
    the count and first of its tags), ways of 16 bytes (the count and first of its nodes in the
    node list, the count and first of its tags), the node list, memberships (way, the node's
    index in it), tags (name, value: string indexes), the string offsets and the strings, and
    the magic word again."""
    if len(d) < 44 or struct.unpack_from(">I", d, 0)[0] != NAV_MAGIC:
        raise ValueError("not a road network (.nav)")
    nn, nw, nt, nr, nm, nk, nv, kb, vb = struct.unpack_from(">9I", d, 4)
    p = 40
    nodes = [struct.unpack_from(">I3f4I", d, p + 32 * i) for i in range(nn)]
    p += 32 * nn
    ways = [struct.unpack_from(">4I", d, p + 16 * i) for i in range(nw)]
    p += 16 * nw
    node_list = list(struct.unpack_from(f">{nr}I", d, p))
    p += 4 * nr
    memberships = [struct.unpack_from(">2I", d, p + 8 * i) for i in range(nm)]
    p += 8 * nm
    tags = [struct.unpack_from(">2I", d, p + 8 * i) for i in range(nt)]
    p += 8 * nt
    key_at = struct.unpack_from(f">{nk}I", d, p)
    p += 4 * nk
    value_at = struct.unpack_from(f">{nv}I", d, p)
    p += 4 * nv
    key_bytes, value_bytes = d[p:p + kb], d[p + kb:p + kb + vb]

    def text(table, at):
        return table[at:table.index(b"\0", at)].decode("latin1")
    return {"nodes": nodes, "ways": ways, "node_list": node_list, "memberships": memberships, "tags": tags,
            "keys": [text(key_bytes, o) for o in key_at], "values": [text(value_bytes, o) for o in value_at]}


def write_nav(net):
    """read_nav's dict as a file (the strings in the order given: Colorado's are sorted)."""
    tables = []
    for names in (net["keys"], net["values"]):
        data, at = bytearray(), []
        for s in names:
            at.append(len(data))
            data += s.encode("latin1") + b"\0"
        tables.append((data, at))
    (kd, key_at), (vd, value_at) = tables
    out = bytearray(struct.pack(">10I", NAV_MAGIC, len(net["nodes"]), len(net["ways"]), len(net["tags"]),
                                len(net["node_list"]), len(net["memberships"]), len(key_at), len(value_at),
                                len(kd), len(vd)))
    for row in net["nodes"]:
        out += struct.pack(">I3f4I", *row)
    for row in net["ways"]:
        out += struct.pack(">4I", *row)
    out += struct.pack(f">{len(net['node_list'])}I", *net["node_list"])
    for row in net["memberships"] + net["tags"]:
        out += struct.pack(">2I", *row)
    out += struct.pack(f">{len(key_at)}I", *key_at) + struct.pack(f">{len(value_at)}I", *value_at) + kd + vd
    return bytes(out + struct.pack(">I", NAV_MAGIC))


def nav_of(positions, ways, keys=(), values=()):
    """A road network file. positions: [(x, y, z)], the nodes; ways: [([node indexes], {tag
    name: value})], which may share nodes (junctions). keys, values: more entries for the two
    string tables (Colorado's, see the top of this file)."""
    keys = sorted({k for _, t in ways for k in t} | set(keys))
    values = sorted({v for _, t in ways for v in t.values()} | set(values))
    rows, node_list, tags = [], [], []
    for idx, t in ways:
        first_tag = len(tags)
        tags += [(keys.index(k), values.index(t[k])) for k in sorted(t)]
        rows.append((len(idx), len(node_list), len(t), first_tag))
        node_list += list(idx)
    of_node = {}
    for w, (count, first, _, _) in enumerate(rows):
        for j in range(count):
            of_node.setdefault(node_list[first + j], []).append((w, j))
    memberships, nodes = [], []
    for i, (x, y, z) in enumerate(positions):
        mine = of_node.get(i, [])
        nodes.append((i, x, y, z, len(mine), len(memberships), 0, 0))
        memberships += mine
    return write_nav({"nodes": nodes, "ways": rows, "node_list": node_list, "memberships": memberships,
                      "tags": tags, "keys": keys, "values": values})


def circuit_roads(lap, others, tags, closed=True):
    """(positions, ways) of a circuit's roads. lap: [(x, y, z)], the circuit's line, a node
    every NAV_SPACING metres; others: the same of its other layouts. A stretch of another
    layout farther than LINK_DISTANCE from every node of the lap is a road of its own, joined
    to the lap's nodes nearest its two ends. The lap is cut into open ways at those junctions
    and every ROAD_NODES nodes, in two at least (Colorado has no closed road); not closed:
    one line, end to end."""
    pos, n = list(lap), len(lap)

    def near(p):
        return min(range(n), key=lambda i: math.dist(lap[i], p))
    links, cuts = [], {0}
    for line in others:
        m = len(line)
        far = [math.dist(lap[near(p)], p) > LINK_DISTANCE for p in line]
        if all(far) or not any(far):
            continue  # a layout of its own (no shared road), or the same road all the way
        start = next(i for i in range(m) if not far[i])
        i = 0
        while i < m:
            if not far[(start + i) % m]:
                i += 1
                continue
            run = []
            while i < m and far[(start + i) % m]:
                run.append(line[(start + i) % m])
                i += 1
            a, b = near(line[(start + i - len(run) - 1) % m]), near(line[(start + i) % m])
            if len(run) >= 2 and a != b:  # a != b: a road back to its own first node would be closed
                first = len(pos)
                pos += run
                links.append([a] + list(range(first, first + len(run))) + [b])
                cuts |= {a, b}
    if closed and cuts == {0} and n <= ROAD_NODES:
        cuts.add(n // 2)  # a lap this short would be one road back to its own first node
    ring = list(range(n)) + ([0] if closed else [])
    ways, cur = [], [ring[0]]
    for node in ring[1:]:
        cur.append(node)
        if node in cuts or len(cur) > ROAD_NODES:
            ways.append((cur, tags))
            cur = [node]
    if len(cur) > 1:
        ways.append((cur, tags))
    return pos, ways + [(link, tags) for link in links]


def road_network(track, lap, others=(), keys=(), values=(), closed=True):
    """The circuit's <track>.nav: its roads (circuit_roads) with the tags of a main road
    without traffic, named after the track, and the map screen's cursor_area round them
    (as Colorado's: a rectangle at height 0, closed, from its largest x and z)."""
    tags = dict(ROAD_TAGS, display_name=track.lower())
    positions, ways = circuit_roads(lap, list(others), tags, closed)
    x0, x1 = min(p[0] for p in positions) - CURSOR_MARGIN, max(p[0] for p in positions) + CURSOR_MARGIN
    z0, z1 = min(p[2] for p in positions) - CURSOR_MARGIN, max(p[2] for p in positions) + CURSOR_MARGIN
    first = len(positions)
    positions += [(x1, 0.0, z1), (x0, 0.0, z1), (x0, 0.0, z0), (x1, 0.0, z0)]
    ways.append((list(range(first, first + 4)) + [first], {"area_type": "cursor_area"}))
    return nav_of(positions, ways, keys, values)


def check_network(data, waypoints=(), reach=NAV_SPACING):
    """What is wrong with a road network for FH1, as a list of sentences (empty: nothing): a
    tag name the game asks for missing (a crash), the tag names out of order (the game finds
    one by halving the table; Colorado's values are in order too, each once), and what the
    networks that worked did not have: a closed road, a route waypoint with no road node within
    `reach` metres."""
    net = read_nav(data)
    problems = []
    for what, table in (("names", net["keys"]), ("values", net["values"])):
        raw = [s.encode("latin1") for s in table]
        if raw != sorted(set(raw)):
            problems.append(f"the tag {what} are not in byte order, each once: FH1 finds a name by "
                            "halving its table, and Colorado's two tables are in that order")
    key = {name: i for i, name in enumerate(net["keys"])}
    roads = []
    for w, (count, first, tag_count, first_tag) in enumerate(net["ways"]):
        names = {net["tags"][first_tag + j][0] for j in range(tag_count)}
        if key.get("road_type") not in names:
            continue
        idx = net["node_list"][first:first + count]
        roads += idx
        if count > 3 and idx[0] == idx[-1]:
            problems.append(f"road {w} is closed ({count} nodes): Colorado has no closed road")
    missing = [name for name in LOOKED_UP if name not in key]
    if missing:
        problems.append(f"no tag name {', '.join(missing)}: FH1 looks them up and uses the answer unchecked")
    at = [net["nodes"][i][1:4] for i in set(roads)]
    for k, p in enumerate(waypoints):
        if not at or min(math.dist(p, q) for q in at) > reach:
            problems.append(f"route waypoint {k} has no road node within {reach:g} m")
    return problems


# ---- The map's style ------------------------------------------------------------------------------

MAP_PROFILES = ("MapProfileMinimap.xml", "MapProfileFullscreen.xml")  # in FH1's media/ui


def discovered_roads(profile):
    """An FH1 map profile (bytes: media/ui/MapProfileMinimap.xml or MapProfileFullscreen.xml)
    with its roads drawn as driven ones from the start. A profile's road groups (those with a
    <Filter tag="road_type" .../>) give a road two colours: ColourPrimary until the player
    has driven it (the minimap's is the grey 122, 122, 122), ColourSecondary after (white).
    On a circuit the grey road ahead cannot be told from the tarmac under the minimap, which
    has no background of its own, and there is nothing to discover: the first colour becomes
    the second. Everything else is left byte for byte."""
    def symbolizer(m):
        block = m.group(0)
        first = re.search(rb"<ColourPrimary\b([^>]*?)/>", block)
        second = re.search(rb"<ColourSecondary\b([^>]*?)/>", block)
        if not first or not second:
            return block
        return block[:first.start(1)] + second.group(1) + block[first.end(1):]

    def group(m):
        block = m.group(0)
        if not re.search(rb"<Filter\s+tag\s*=\s*[\"']road_type[\"']", block):
            return block
        return re.sub(rb"<Symbolizer\b.*?</Symbolizer>", symbolizer, block, flags=re.S)
    return re.sub(rb"<Group\b.*?</Group>", group, profile, flags=re.S)


def map_profiles(ui_dir):
    """{path under media/: bytes}: FH1's two map profiles of ui_dir (the player's own
    media/ui, any case) with discovered_roads applied; a profile that is not there, or has no
    road group to change, is left out."""
    out = {}
    try:
        names = {n.lower(): n for n in os.listdir(ui_dir)}
    except OSError:
        return out
    for name in MAP_PROFILES:
        if name.lower() not in names:
            continue
        try:
            with open(os.path.join(ui_dir, names[name.lower()]), "rb") as f:
                data = f.read()
        except OSError:
            continue
        changed = discovered_roads(data)
        if changed != data:
            out[f"ui/{name}"] = changed
    return out


# ---- A circuit's files ----------------------------------------------------------------------------

def build(track, geo, colorado_nav, other_geos=(), route=RACE_ROUTE):
    """{path under media/: bytes} of the circuit's road network and, for a lap, its race route.
    geo: the AI line of the layout the circuit's world is (ribbon 0's); other_geos: its other
    layouts' lines, whose own stretches of road then join the network (the import passes none:
    see the top of this file); colorado_nav: the player's own
    media/tracks/colorado/colorado.nav, for the tag tables. Raises ValueError when a file is
    not what it should be (checked) or the network would not load (check_network)."""
    pts = checked(ai_line(geo), os.path.basename(geo))
    lap = is_lap(pts)
    with open(colorado_nav, "rb") as f:
        colorado = read_nav(f.read())
    key = track.lower()
    others = []
    for g in other_geos:
        line = checked(ai_line(g), os.path.basename(g))
        if is_lap(line):
            others.append([p[0] for p in resample(line, NAV_SPACING)])
    nodes = [p[0] for p in resample(pts, NAV_SPACING, closed=lap)]
    files = {f"tracks/{key}/{key}.nav": road_network(key, nodes, others, colorado["keys"], colorado["values"], lap),
             f"tracks/{key}/{key}.owr": struct.pack(">3I", NAV_MAGIC, 0, NAV_MAGIC)}
    waypoints = []
    if lap:
        xml = route_xml(pts)
        waypoints = waypoints_of(xml)
        files[f"tracks/{key}/ribbon_00/trackroute{route:03d}.xml"] = xml
        files[f"aiopenworld/{key}/ribbon_00/route_{route:03d}.owt"] = owt(resample(pts, OWT_SPACING))
    problems = check_network(files[f"tracks/{key}/{key}.nav"], waypoints)
    if problems:
        raise ValueError(f"{track}: " + "; ".join(problems))
    return files


def main(argv):
    if len(argv) >= 6 and argv[1] == "write":
        geo, colorado, track, out = argv[2:6]
        try:
            files = build(track, geo, colorado, argv[6:])
        except (OSError, ValueError, KeyError, IndexError, struct.error) as e:
            print(f"fm4_routes: {e}")
            return 1
        for rel, data in files.items():
            dst = os.path.join(out, "media", *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
            print(f"media/{rel}: {len(data)} bytes")
        return 0
    if len(argv) == 4 and argv[1] == "profiles":
        files = map_profiles(argv[2])
        for rel, data in files.items():
            dst = os.path.join(argv[3], "media", *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(data)
            print(f"media/{rel}: {len(data)} bytes")
        if not files:
            print(f"fm4_routes: no map profile with a road group in {argv[2]}")
        return 0 if files else 1
    if len(argv) == 3 and argv[1] in ("nav", "owt"):
        with open(argv[2], "rb") as f:
            data = f.read()
    if len(argv) == 3 and argv[1] == "nav":
        net = read_nav(data)
        roads = sum(1 for _, _, n, t in net["ways"]
                    if any(net["keys"][net["tags"][t + j][0]] == "road_type" for j in range(n)))
        print(f"{len(net['nodes'])} nodes, {len(net['ways'])} ways ({roads} roads), "
              f"{len(net['keys'])} tag names: {' '.join(net['keys'])}")
        for line in check_network(data):
            print(line)
        return 0
    if len(argv) == 3 and argv[1] == "owt":
        pts, looped = read_owt(data)
        widths = [2 * math.hypot(a[0], a[2]) for _, a, _ in pts]
        print(f"{len(pts)} points, {length(pts, looped):.0f} m, {'a lap' if looped else 'open'}, "
              f"{min(widths):.1f} to {max(widths):.1f} m wide")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
