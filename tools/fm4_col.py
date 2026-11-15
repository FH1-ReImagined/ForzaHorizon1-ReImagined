#!/usr/bin/env python3
"""Forza Motorsport 4 track collision (<track>_track_NN.col) in Forza Horizon 1's layout (#648).

Both games index a ribbon's collision the same way: a header (grid of 100 m cells over the
track, 0xABCD1234 at +0x30, the cell count at +0x4C) and a table of 24-byte records (number,
grid cell, offset, size, 0, end). FM4 keeps every cell's data in the .col file after the
table; FH1 streams Colorado's cells as numbered .fiz files with its zones, but its collision
manager (TU4 sub_82DD8F40, which reads the header and table) can also read every cell from
the .col file at the records' offsets (sub_82DD8548, sub_82DD81B0), which a converted track
uses (FORZA_FM4_COL, forza-recomp/src/hooks/fm4_track.cpp).

A cell: a header, vertices, triangles (32 bytes: flags, three vertex offsets, the normal, a
word), then a 12x12 grid of 10 m squares (an offset each, 0 for none) and their lists (u16
count, u16 triangle numbers). Offsets are from the cell's start; the game turns them into
pointers (sub_82DD20C8). What differs:

  FM4                                  FH1
  header 0x40 bytes                    0x90: +0x3C a list of u16 ids (count at +0x7A) the
                                       triangles' low flag bits select, +0x7C the mask the
                                       game makes of them, +0x80 a pointer the game sets
  vertex 32 bytes (x, y, z, 0, the     vertex 16 bytes (x, y, z, the vertex normal packed:
    vertex normal packed, y and z the    pack_normal; FM4's own normal is kept, see NORMALS)
    other way round: fm4_normal, 12
    zero bytes)
  triangle flags 0x00MM0000            0xTTMM8000: the query (sub_82DD6C08) skips a
    (MM: the surface)                  triangle unless its low 16 bits share a bit with
                                       0x8000 | the mask, and its top byte TT one with the
                                       caller's. In Colorado's, TT is 0x0E (or 0x0A) on
                                       ground and 0x08 (or 0x0C) on walls; given 0x0E, a
                                       wall throws the car. The last word holds a byte
                                       per edge in Colorado's (edge_words), 0 in FM4's,
                                       kept 0 (see EDGES)

A triangle's surface is a number in the game's own list, and the two games' lists differ from
the 50th entry on (surface_map).

Usage: tools/fm4_col.py IN.col OUT.col
"""
import math
import os
import struct
import sys

FH1_HEADER = 0x90
# FM4_COL_SURFACE=n gives every triangle surface n (a test).
SURFACE = os.environ.get("FM4_COL_SURFACE")
# FM4_COL_REMAP=a:b,...: FH1 surface a becomes b (a test).
REMAP = {int(a, 0): int(b, 0) for a, b in (x.split(":") for x in
         os.environ.get("FM4_COL_REMAP", "").split(",") if ":" in x)}
# FM4_COL_WALL=n: the top flag byte of walls (a test; Colorado's are 0x08 or 0x0C).
WALL = int(os.environ.get("FM4_COL_WALL", "0x08"), 0)
# FM4_COL_EDGES=n: the triangles' last word (a test). Colorado's are mostly 0x00FFFFFF, but
# with that a wall FM4 built throws the car off at speed (60 to 180 km/h on touching it);
# FM4's own 0 gives a crash that slows the car, as in Colorado. FM4_COL_EDGES=fh1 gives the
# ground triangles the bytes FH1's own cells have (edge_words) and leaves the walls 0: on Le
# Mans's first 2.1 km the wheels rode the same with them as with 0, so 0 stays.
EDGES = os.environ.get("FM4_COL_EDGES", "0")
FH1_EDGES = EDGES == "fh1"
EDGES = 0 if FH1_EDGES else int(EDGES, 0)
# FM4_COL_GRID=n: each cell's squares rebuilt as n x n (FM4's are 12 x 12 of about 10 m; a test).
GRID = int(os.environ.get("FM4_COL_GRID", "0"))
# FM4_COL_NORMALS: the vertex normals FH1 gets. "fm4" (the default): FM4's own, which its
# vertices carry in another packing (fm4_normal). FH1 takes the road's normal under a wheel
# from them. With every ground normal straight up (the earlier default, "up") the suspension
# answered a road rising or falling under the car as it would its own travel, by the rate
# (as if the dampers took the car's speed along that normal for it): a road rising at
# 0.75 m/s (3 % at 90 km/h, 1 % at 270) held all four wheels on their bump stops, and one
# falling as fast left them hanging (#876). "smooth" (the triangles' mean, by smoothing
# group) and "face" (each triangle's own) are there for tests.
NORMALS = os.environ.get("FM4_COL_NORMALS", "fm4")
FM4_NORMALS = NORMALS == "fm4"
FLAT_NORMALS = NORMALS == "up"
FACE_NORMALS = NORMALS == "face"
DROP_DEGENERATE = os.environ.get("FM4_COL_KEEP_DEGENERATE", "0") != "1"
ALIGN = 0x800


def pack_normal(x, y, z):
    """A vertex normal in FH1's word: x in bits 0-10 and y in bits 11-21 (signed, 1023 for
    1), z in bits 22-31 (signed, 511 for 1), as Direct3D's HEND3N. Checked on Colorado's
    cells: the word is 0.10 degrees at the median from the mean normal of the triangles at a
    ground vertex, and bit 21, y's sign, is clear in 20,920 of 20,922 of them. (Read as z in
    bits 21-31 and y in bits 11-20, the earlier reading here, a normal whose z came out odd
    had that bit set: a normal lying flat for the game, which threw the car where the road's
    normals were not straight up.)"""
    m = (x * x + y * y + z * z) ** 0.5 or 1.0
    q = lambda v, top: max(-top, min(top, int(round(v / m * top))))
    return ((q(z, 511) & 0x3FF) << 22) | ((q(y, 1023) & 0x7FF) << 11) | (q(x, 1023) & 0x7FF)


def unpack_normal(word):
    """pack_normal's (x, y, z) back."""
    signed = lambda v, bits: v - (1 << bits) if v >> (bits - 1) else v
    return (signed(word & 0x7FF, 11) / 1023.0, signed((word >> 11) & 0x7FF, 11) / 1023.0,
            signed(word >> 22, 10) / 511.0)


def fm4_normal(word):
    """A vertex normal from FM4's word, the same packing with y and z the other way round (z in
    bits 11-21, y in bits 22-31): fitted to the triangles at each ground vertex of Le Mans
    (their mean normal is 0.06 degrees from it at the median, 1.5 with z's sign turned)."""
    x, z, y = unpack_normal(word)
    return (x, y, z)


SHARP_EDGE = 10.0  # degrees between two triangles from which FH1 marks their edge


def edge_words(corners, normals):
    """Each triangle's last word as FH1's own cells have it: a byte per edge (corner 0 to 1 in
    bits 16-23, 1 to 2 in bits 8-15, 2 to 0 in bits 0-7). 0xFF where the triangle across the
    edge lies level with this one or rises from it, or there is none in the cell; where it
    falls away by SHARP_EDGE degrees or more, 255 x the cosine of half the angle between the
    two. Fitted to Colorado's cells: of 84,541 ground edges with a neighbour, 82,767 get their
    own byte and all but 6 of the rest a byte 1 off. What the game does with the bytes is not
    known (FM4's word is 0, and FM4's roads drive with it): FM4_COL_EDGES=fh1, for tests.
    corners: each triangle's three points; normals: each triangle's normal."""
    across = {}
    for i, p in enumerate(corners):
        for e in range(3):
            if p[e] != p[(e + 1) % 3]:
                across.setdefault(frozenset((p[e], p[(e + 1) % 3])), []).append(i)
    words = []
    for i, p in enumerate(corners):
        n, word = normals[i], 0
        for e in range(3):
            edge = frozenset((p[e], p[(e + 1) % 3]))
            byte = None
            for j in across.get(edge, ()):
                far = next((q for q in corners[j] if q not in edge), None)
                if j == i or far is None:
                    continue
                m = normals[j]
                below = sum(n[k] * (far[k] - p[e][k]) for k in range(3)) < -1e-4
                cos = max(-1.0, min(1.0, n[0] * m[0] + n[1] * m[1] + n[2] * m[2]))
                angle = math.degrees(math.acos(cos))
                b = int(round(255 * math.cos(math.radians(angle) / 2))) if below and angle >= SHARP_EDGE else 255
                byte = b if byte is None else max(byte, b)
            word = word << 8 | (255 if byte is None else byte)
        words.append(word)
    return words


# FM4 surfaces FH1 lacks, as the nearest FH1 one. InvisibleTeleportingBorder is the ground round
# a track where FM4 puts the car back on it (Top Gear's airfield, from 26 m off the line); FH1
# does not, so it is ground to drive on, and FH1's Invisible has no friction at all.
NEAREST = {"Shoulder": "Asphault2", "SpeedBump": "RumbleStrip", "SmoothBlacktop": "Asphalt",
           "CobblestoneSmall2": "CobblestoneSmall", "InvisibleTeleportingBorder": "Asphalt",
           "SoccerBall": "RubberCone", "DragHighFrictionVHT": "ConcreteHighFriction",
           "BowlingPin": "BowlingPinGold", "SoccerBarrier": "ConcreteBarrier"}
# FM4 surfaces FH1 has under the same name as another surface, as the FH1 one that drives like
# FM4's. SlowAsphault is the tarmac run-off of FM4's chicanes: smooth, with less grip (no
# bumps, friction 0.6). FH1 gave the name its grass's bumps (a 5 cm height grid), which shook
# the car on what looks like tarmac (#876); FH1's ConcreteSlow is smooth with friction 0.75.
SAME_NAME_OTHER_SURFACE = {"SlowAsphault": "ConcreteSlow"}


def child_names(xml, parent):
    """The names of <parent>'s own child elements, in order, whatever their indentation
    (FM4's surface list indents some entries with spaces and some a tab deeper)."""
    import re
    m = re.search(rf"<{parent}\b[^>]*>(.*)</{parent}>", re.sub(r"<!--.*?-->", "", xml, flags=re.S), re.S)
    names, depth = [], 0
    for closing, name, empty in re.findall(r"<(/?)([\w.:-]+)(?:\s[^<>]*?)?(/?)>", m.group(1) if m else ""):
        if closing:
            depth -= 1
        else:
            if depth == 0:
                names.append(name)
            if not empty:
                depth += 1
    return names


def surface_map(fm4_xml, fh1_xml):
    """FM4 surface number -> FH1's, by name. A triangle's surface is its number in the
    game's surface list (media/physics surfaceTypes.xml): FM4 numbers its <SurfaceTypes>
    entries in order (61 on Disc 1), FH1 has a <SurfaceTypesList>; the first 49 agree, then
    FH1 has its own (FM4's 54, SmoothBlacktop, is FH1's Leaf5)."""
    import re
    fm4 = child_names(fm4_xml, "SurfaceTypes")
    fh1 = re.findall(r'<SurfaceType value="([^"]+)"', fh1_xml)
    index = {n: i for i, n in enumerate(fh1)}

    def number(name):
        name = SAME_NAME_OTHER_SURFACE.get(name, name)
        return index.get(name, index.get(NEAREST.get(name, ""), 0))
    return [number(n) for n in fm4]


def area(a, b, c):
    u = [b[k] - a[k] for k in range(3)]
    v = [c[k] - a[k] for k in range(3)]
    x = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    return 0.5 * (x[0] * x[0] + x[1] * x[1] + x[2] * x[2]) ** 0.5


def overlaps(tri, bx0, bz0, bx1, bz1):
    """A triangle (x, z corners) and a square overlap (separating axes)."""
    xs, zs = [p[0] for p in tri], [p[1] for p in tri]
    if max(xs) < bx0 or min(xs) > bx1 or max(zs) < bz0 or min(zs) > bz1:
        return False
    box = ((bx0, bz0), (bx1, bz0), (bx1, bz1), (bx0, bz1))
    for i in range(3):
        (ax, az), (cx, cz) = tri[i], tri[(i + 1) % 3]
        nx, nz = cz - az, ax - cx
        tp = [nx * p[0] + nz * p[1] for p in tri]
        bp = [nx * p[0] + nz * p[1] for p in box]
        if max(tp) < min(bp) or max(bp) < min(tp):
            return False
    return True


def convert_cell(d, surfaces=None):
    nv, nt, hs, toff = struct.unpack_from(">4I", d, 0)
    gx, gz = struct.unpack_from(">II", d, 0x10)
    goff = struct.unpack_from(">I", d, 0x38)[0]
    if hs != 0x40 or toff != hs + 32 * nv or goff != toff + 32 * nt:
        raise ValueError(f"not an FM4 cell: header {hs:#x}, triangles at {toff:#x}, grid at {goff:#x}")
    tris = [struct.unpack_from(">IIIIfffI", d, toff + 32 * i) for i in range(nt)]
    pos = [struct.unpack_from(">fff", d, hs + 32 * i) for i in range(nv)]
    own = [fm4_normal(struct.unpack_from(">I", d, hs + 32 * i + 16)[0]) for i in range(nv)]
    # Zero-area triangles (11 in Suzuka) are kept in place but given no flag bits, so no query
    # takes them (FM4_COL_KEEP_DEGENERATE=1 leaves them).
    degenerate = {i for i, t in enumerate(tris)
                  if area(*[pos[(v - hs) // 32] for v in t[1:4]]) < 1e-3} if DROP_DEGENERATE else set()
    at = [[] for _ in range(nv)]
    for t in tris:
        for v in t[1:4]:
            at[(v - hs) // 32].append(t[4:7])
    # A corner's normal: the mean of the triangles at its vertex that face within 45 degrees
    # of its own (smoothing groups), so a wall's foot does not lean the road beside it. A
    # vertex shared by a wall and the road becomes two, as in Colorado's cells.
    verts, index, corners = [], {}, []
    for t in tris:
        row = []
        for v in t[1:4]:
            k = (v - hs) // 32
            n = [0.0, 0.0, 0.0]
            for m in at[k]:
                if m[0] * t[4] + m[1] * t[5] + m[2] * t[6] > 0.707:
                    n[0] += m[0]; n[1] += m[1]; n[2] += m[2]
            key = (k, pack_normal(*n) if any(n) else pack_normal(*t[4:7]))
            if FM4_NORMALS and any(own[k]):
                key = (k, pack_normal(*own[k]))
            if FLAT_NORMALS and t[5] > 0.5:
                key = (k, pack_normal(0.0, 1.0, 0.0))
            if FACE_NORMALS:
                key = (k, pack_normal(*t[4:7]))
            if key not in index:
                index[key] = len(verts)
                verts.append(key)
            row.append(index[key])
        corners.append(row)
    nv = len(verts)
    vbase = FH1_HEADER
    tbase = vbase + 16 * nv
    gbase = tbase + 32 * nt
    lists_old = goff + 4 * gx * gz
    head = bytearray(d[:0x3C])
    if GRID:  # the squares rebuilt finer: GRID x GRID over the cell's bounds
        x0, z0, x1, z1 = struct.unpack_from(">4f", d, 0x18)
        side = (x1 - x0) / GRID
        lists = [[] for _ in range(GRID * GRID)]
        for i, t in enumerate(tris):
            p = [pos[(v - hs) // 32] for v in t[1:4]]
            tri2 = [(q[0], q[2]) for q in p]
            c0 = max(0, int((min(q[0] for q in tri2) - x0) // side))
            c1 = min(GRID - 1, int((max(q[0] for q in tri2) - x0) // side))
            r0 = max(0, int((min(q[1] for q in tri2) - z0) // side))
            r1 = min(GRID - 1, int((max(q[1] for q in tri2) - z0) // side))
            for r in range(r0, r1 + 1):
                for cc in range(c0, c1 + 1):
                    bx, bz = x0 + cc * side, z0 + r * side
                    if overlaps(tri2, bx, bz, bx + side, bz + side):
                        lists[r * GRID + cc].append(i)
        gx = gz = GRID
        blob = bytearray()
        offs = []
        lists_new = gbase + 4 * gx * gz
        for lst in lists:
            if not lst:
                offs.append(0)
                continue
            offs.append(lists_new + len(blob))
            blob += struct.pack(f">H{len(lst)}H", len(lst), *lst)
        out = bytearray(lists_new) + blob
        struct.pack_into(">II", head, 0x10, gx, gz)
        struct.pack_into(">4f", head, 0x28, side, side, 1 / side, 1 / side)
    else:
        lists_new = gbase + 4 * gx * gz
        out = bytearray(lists_new) + d[lists_old:]
        offs = None
    struct.pack_into(">4I", head, 0, nv, nt, vbase, tbase)
    struct.pack_into(">I", head, 0x38, gbase)
    out[:0x3C] = head
    for i, (k, word) in enumerate(verts):
        struct.pack_into(">fffI", out, vbase + 16 * i, *pos[k], word)
    edges = edge_words([[pos[(v - hs) // 32] for v in t[1:4]] for t in tris],
                       [t[4:7] for t in tris]) if FH1_EDGES else None
    for i, (fl, a, b, c, nx, ny, nz, _) in enumerate(tris):
        mat = (fl >> 16) & 0xFF
        if surfaces and mat < len(surfaces):
            mat = surfaces[mat]
        mat = REMAP.get(mat, mat)
        mat = (mat if SURFACE is None else int(SURFACE, 0)) << 16
        fl = (0x0E000000 if ny > 0.5 else WALL << 24) | mat | 0x8000
        if i in degenerate:
            fl = mat
        last = edges[i] if edges and ny > 0.5 else EDGES
        struct.pack_into(">IIIIfffI", out, tbase + 32 * i, fl,
                         *[vbase + 16 * v for v in corners[i]], nx, ny, nz, last)
    for k in range(gx * gz):
        if offs is not None:
            struct.pack_into(">I", out, gbase + 4 * k, offs[k])
            continue
        o = struct.unpack_from(">I", d, goff + 4 * k)[0]
        struct.pack_into(">I", out, gbase + 4 * k, o - lists_old + lists_new if o else 0)
    out += bytes(-len(out) % ALIGN)
    return bytes(out)


def convert_col(data, surfaces=None):
    """FM4's .col (version 2 at +0x2C) to FH1's layout, the cells still inline."""
    if struct.unpack_from(">I", data, 0x30)[0] != 0xABCD1234:
        raise ValueError("not a track collision file")
    n = struct.unpack_from(">I", data, 0x4C)[0]
    recs = [struct.unpack_from(">6I", data, 0x58 + 24 * i) for i in range(n)]
    start = recs[0][2] if recs else 0x1000
    out = bytearray(data[:start])
    for i, (k, cell, off, size, z, _) in enumerate(recs):
        c = convert_cell(data[off:off + size], surfaces)
        struct.pack_into(">6I", out, 0x58 + 24 * i, k, cell, len(out), len(c), z, len(out) + len(c))
        out += c
    struct.pack_into(">I", out, 0x28, len(out))
    return bytes(out)


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    data = open(argv[1], "rb").read()
    out = convert_col(data)
    open(argv[2], "wb").write(out)
    print(f"{argv[2]}: {struct.unpack_from('>I', data, 0x4C)[0]} cells, {len(data)} -> {len(out)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
