#!/usr/bin/env python3
"""Read and write FH1's per-car physicsdefinition.bin (Forza Motorsport 4 has no such file).

Layout (big-endian, the game's versioned serializer; field names from the executable's
serializer strings next to `PhysicsDefinition`, `CollSpheres`, `ConvexHull`; checked on all
176 files of the game's car archives, which parse to their end with this reader):

    u32 NumDefinitions (1)
    PhysicsDefinition: u32 version (13), u32 DefinitionType (1)
      f32 Mass (0.01 in every car), f32[9] InertiaTensor, f32[9] InverseInertiaTensor,
      f32[3] HalfExtents, f32 BoundingRadius, f32[3] GraphicsOffset, u32 NumChildren (0),
      f32[3] AabbCentreOffset (version > 11)
    u32 NumCollisionShapes, then each shape: u32 ShapeType and
      0 sphere: u16 XShapeFlags (the MAXData.xml sphere's Flags), f32[4] PositionPlusRadius
      2 convex hull: u32 ConvexHullSize, that many bytes (below)
    10 bytes, the same in every car: 00000001 0000 ffffffff

The spheres are MAXData.xml's <CollSpheres> (Physics/MAXData.xml, which FM4 cars have too),
in the same order. The hull blob:

    u8 2, u8 4, 14 zero bytes, f32[16] identity matrix
    u32 offsets of: vertices, vertex records, edges, faces; u8 V, E, F, 0; u32 blob size
    24 + 16 * (F // 2) zero bytes (filled by the game at load)
    V x f32[4]  vertices (w 0)
    V x (u32 offset, u32 offset, u8 2k, u8 k, u16 0): per vertex, the offsets of its list of
                k edge pairs (2k bytes, edge indices) and of its k faces (k bytes, ascending);
                k is the vertex's valence. In the game's files each incident edge is listed
                twice; here each pair is the two edges of the vertex in one of its faces.
    the byte lists of the vertex records
    E x (u8 v0, u8 v1)   edges
    F x (u32 offset, u8 n, 3 zero bytes), then the face vertex lists (n bytes each), wound
                clockwise seen from outside

The rigid-body fields (mass, inertia, half extents, radius, offsets) do not follow from the
hull or the model in the game's files (they are not the hull's mass properties); a new car
gets the values of the game car whose hull is closest in size, scaled to the new hull
(`build`). Standard library only.
"""
import itertools
import math
import re
import struct

TAIL = bytes.fromhex("000000010000ffffffff")


class PhysdefError(ValueError):
    pass


# ---------------------------------------------------------------------------- reading

def parse(data):
    o = 0

    def u32():
        nonlocal o
        v = struct.unpack_from(">I", data, o)[0]
        o += 4
        return v

    def fl(n):
        nonlocal o
        v = struct.unpack_from(">%df" % n, data, o)
        o += 4 * n
        return list(v)

    r = {"ndefs": u32(), "version": u32(), "type": u32()}
    r["mass"] = fl(1)[0]
    r["inertia"], r["inverse_inertia"] = fl(9), fl(9)
    r["half_extents"], r["bounding_radius"] = fl(3), fl(1)[0]
    r["graphics_offset"], r["children"], r["aabb_offset"] = fl(3), u32(), fl(3)
    shapes = []
    for _ in range(u32()):
        t = u32()
        if t == 0:
            flags = struct.unpack_from(">H", data, o)[0]
            o += 2
            shapes.append({"type": "sphere", "flags": flags, "sphere": fl(4)})
        elif t == 2:
            size = u32()
            shapes.append({"type": "hull", "hull": parse_hull(data[o:o + size])})
            o += size
        else:
            raise PhysdefError("shape type %d at 0x%x" % (t, o - 4))
    r["shapes"] = shapes
    if data[o:] != TAIL:
        raise PhysdefError("unexpected end: %s" % data[o:].hex())
    return r


def parse_hull(b):
    vo, ao, eo, fo, nv, ne, nf, _, size = struct.unpack_from(">4I4BI", b, 80)
    if size != len(b):
        raise PhysdefError("hull size")
    verts = [struct.unpack_from(">3f", b, vo + 16 * i) for i in range(nv)]
    edges = [(b[eo + 2 * i], b[eo + 2 * i + 1]) for i in range(ne)]
    faces = []
    for i in range(nf):
        off, n = struct.unpack_from(">IB", b, fo + 8 * i)
        faces.append(list(b[off:off + n]))
    return {"vertices": verts, "edges": edges, "faces": faces}


# ---------------------------------------------------------------------------- writing

def hull_blob(verts, faces):
    """verts: [(x, y, z)], faces: vertex index lists wound clockwise seen from outside."""
    nv, nf = len(verts), len(faces)
    edges, edge_of = [], {}
    for f in faces:
        for a, b in zip(f, f[1:] + f[:1]):
            k = (min(a, b), max(a, b))
            if k not in edge_of:
                edge_of[k] = len(edges)
                edges.append((a, b))
    ne = len(edges)
    if nv > 255 or ne > 255 or nf > 255 or nv - ne + nf != 2:
        raise PhysdefError("bad hull: %d vertices, %d edges, %d faces" % (nv, ne, nf))
    vo = 128 + 16 * (nf // 2)
    ao = vo + 16 * nv
    lists = bytearray()
    records = []
    list_base = ao + 12 * nv
    for v in range(nv):
        pairs, vfaces = [], []
        for fi, f in enumerate(faces):
            if v in f:
                i = f.index(v)
                prev, nxt = f[i - 1], f[(i + 1) % len(f)]
                pairs += [edge_of[(min(prev, v), max(prev, v))], edge_of[(min(v, nxt), max(v, nxt))]]
                vfaces.append(fi)
        k = len(vfaces)
        a = list_base + len(lists)
        lists += bytes(pairs)
        c = list_base + len(lists)
        lists += bytes(vfaces)
        records.append(struct.pack(">IIBBH", a, c, 2 * k, k, 0))
    eo = list_base + len(lists)
    fo = eo + 2 * ne
    face_lists = bytearray()
    face_records = bytearray()
    flist_base = fo + 8 * nf
    for f in faces:
        face_records += struct.pack(">IB3x", flist_base + len(face_lists), len(f))
        face_lists += bytes(f)
    size = flist_base + len(face_lists)
    out = bytearray(b"\x02\x04" + bytes(14))
    out += struct.pack(">16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    out += struct.pack(">4I4BI", vo, ao, eo, fo, nv, ne, nf, 0, size)
    out += bytes(vo - len(out))
    for x, y, z in verts:
        out += struct.pack(">4f", x, y, z, 0.0)
    out += b"".join(records) + lists
    out += b"".join(bytes(e) for e in edges)
    out += face_records + face_lists
    assert len(out) == size
    return bytes(out)


def write(body, spheres, hull):
    """body: dict as parse() gives; spheres: [(flags, x, y, z, r)]; hull: blob bytes."""
    out = bytearray(struct.pack(">3I", 1, 13, 1))
    out += struct.pack(">f", body["mass"])
    out += struct.pack(">9f", *body["inertia"]) + struct.pack(">9f", *body["inverse_inertia"])
    out += struct.pack(">3f", *body["half_extents"]) + struct.pack(">f", body["bounding_radius"])
    out += struct.pack(">3f", *body["graphics_offset"]) + struct.pack(">I", 0)
    out += struct.pack(">3f", *body["aabb_offset"])
    out += struct.pack(">I", len(spheres) + 1)
    for flags, x, y, z, r in spheres:
        out += struct.pack(">IH4f", 0, flags, x, y, z, r)
    out += struct.pack(">II", 2, len(hull)) + hull
    out += TAIL
    return bytes(out)


# ---------------------------------------------------------------------------- MAXData.xml

def maxdata_spheres(xml):
    """[(flags, x, y, z, r)] of MAXData.xml's <CollSpheres>, offsets and scales applied."""
    if isinstance(xml, bytes):
        xml = xml.decode("latin-1")
    m = re.search(r"<CollSpheres([^>]*)>(.*?)</CollSpheres>", xml, re.S)
    if not m:
        raise PhysdefError("MAXData.xml has no CollSpheres")

    def attrs(s):
        return {k: float(v) for k, v in re.findall(r'(\w+)="([-+0-9.eE]+)"', s)}
    top = attrs(m.group(1))
    off = [top.get("PosOffsetX", 0.0), top.get("PosOffsetY", 0.0), top.get("PosOffsetZ", 0.0)]
    ps, rs = top.get("PosScale", 1.0), top.get("RadiusScale", 1.0)
    out = []
    for tag, body in re.findall(r"<Sphere(\d+)([^>]*)/>", m.group(2)):
        a = attrs(body)
        out.append((int(tag), int(a.get("Flags", 2)), off[0] + ps * a["PosX"], off[1] + ps * a["PosY"],
                    off[2] + ps * a["PosZ"], rs * a["Radius"]))
    out.sort()
    return [s[1:] for s in out]


# ---------------------------------------------------------------------------- the hull

def _dirs():
    """Plane normals: the box, the side profile (y-z) every 22.5 degrees, and 45-degree
    corners in plan view (x-z), like the game's hulls (extruded side profile, cut corners)."""
    d = [(1, 0, 0), (-1, 0, 0), (0, -1, 0)]
    for k in range(9):  # from straight ahead (z+) over the roof to straight back
        a = math.pi * k / 8
        d.append((0.0, math.sin(a), math.cos(a)))
    for a in (-1, 1):
        d.append((0.0, -0.5, 0.866 * a))  # under the bumpers
    for sx in (-1, 1):
        for sz in (-1, 1):
            d.append((sx * 0.7071, 0.0, sz * 0.7071))
    return [tuple(c / math.sqrt(sum(x * x for x in v)) for c in v) for v in d]


def _solve3(n1, n2, n3, d1, d2, d3):
    def det(a, b, c):
        return (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0]))
    D = det(n1, n2, n3)
    if abs(D) < 1e-9:
        return None
    out = []
    for i in range(3):
        m = [list(r) for r in (n1, n2, n3)]
        for r, dv in zip(m, (d1, d2, d3)):
            r[i] = dv
        out.append(det(*m) / D)
    return tuple(out)


def hull_from_points(points, shrink=0.985):
    """A convex polytope around the points with the game's kind of shape (planar sides, at
    most 26 faces): the intersection of the half-spaces of _dirs() through the extreme
    points. Returns (vertices, faces wound clockwise seen from outside). When more than
    three planes meet in one point (a symmetric body), the planes are moved by a few
    millimetres, each by its own amount, until every vertex is a clean corner."""
    normals = _dirs()
    base = []
    for n in normals:
        d = max(p[0] * n[0] + p[1] * n[1] + p[2] * n[2] for p in points)
        if d > 0 and n != (0.0, -1.0, 0.0):
            d *= shrink
        base.append((n, d))
    for attempt in range(6):
        nudge = 0.0015 * attempt
        planes = [(n, d - nudge * ((k * 7) % len(base)) / len(base)) for k, (n, d) in enumerate(base)]
        verts, faces = _polytope(planes)
        edges = {tuple(sorted(e)) for f in faces for e in zip(f, f[1:] + f[:1])}
        if len(verts) - len(edges) + len(faces) == 2 and all(len(f) >= 3 for f in faces):
            return verts, faces
    raise PhysdefError("no clean collision hull for this body")


def _polytope(planes):
    eps = 1e-5
    verts = []
    for (a, da), (b, db), (c, dc) in itertools.combinations(planes, 3):
        p = _solve3(a, b, c, da, db, dc)
        if p is None:
            continue
        if all(p[0] * n[0] + p[1] * n[1] + p[2] * n[2] <= d + eps for n, d in planes):
            if not any(sum((p[i] - q[i]) ** 2 for i in range(3)) < 1e-6 for q in verts):
                verts.append(p)
    faces = []
    for n, d in planes:
        on = [i for i, p in enumerate(verts) if abs(p[0] * n[0] + p[1] * n[1] + p[2] * n[2] - d) < 1e-4]
        if len(on) < 3:
            continue
        c = [sum(verts[i][k] for i in on) / len(on) for k in range(3)]
        # an in-plane basis
        t = (0.0, 1.0, 0.0) if abs(n[1]) < 0.9 else (1.0, 0.0, 0.0)
        u = _norm(_cross(n, t))
        w = _cross(n, u)
        ang = {i: math.atan2(_dot(_sub(verts[i], c), w), _dot(_sub(verts[i], c), u)) for i in on}
        ring = sorted(on, key=lambda i: ang[i])
        # counter-clockwise around n (outward) is ring order; the game's faces run clockwise
        faces.append(ring[::-1])
    # drop vertices that ended up on no face (cannot happen for a closed polytope)
    used = sorted({i for f in faces for i in f})
    remap = {o: i for i, o in enumerate(used)}
    return [verts[i] for i in used], [[remap[i] for i in f] for f in faces]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    s = math.sqrt(_dot(a, a))
    return (a[0] / s, a[1] / s, a[2] / s)


def extents(verts):
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    return lo, hi


def scaled_body(donor, donor_hull, new_hull):
    """The donor's rigid-body fields scaled from its hull to the new one, axis by axis."""
    dlo, dhi = extents(donor_hull)
    nlo, nhi = extents(new_hull)
    s = [(nhi[i] - nlo[i]) / max(1e-6, dhi[i] - dlo[i]) for i in range(3)]
    body = dict(donor)
    body["half_extents"] = [donor["half_extents"][i] * s[i] for i in range(3)]
    body["graphics_offset"] = [donor["graphics_offset"][i] * s[i] for i in range(3)]
    body["aabb_offset"] = [donor["aabb_offset"][i] * s[i] for i in range(3)]
    body["bounding_radius"] = donor["bounding_radius"] * max(s)
    # I_ij scales with the product of the two axes' scales (a body stretched along x by s
    # has its x second moment multiplied by s^2): I = tr(C) 1 - C with C_ij scaled by s_i s_j
    m = donor["inertia"]
    C = [[0.0] * 3 for _ in range(3)]
    tr = (m[0] + m[4] + m[8]) / 2.0
    for i in range(3):
        for j in range(3):
            C[i][j] = (tr - m[3 * i + i]) if i == j else -m[3 * i + j]
    C = [[C[i][j] * s[i] * s[j] for j in range(3)] for i in range(3)]
    t = C[0][0] + C[1][1] + C[2][2]
    inertia = [(t - C[i][j]) if i == j else -C[i][j] for i in range(3) for j in range(3)]
    body["inertia"] = inertia
    body["inverse_inertia"] = _inv3(inertia)
    return body


def _inv3(m):
    a, b, c, d, e, f, g, h, i = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-12:
        raise PhysdefError("singular inertia")
    return [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det,
            (f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det,
            (d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]


def build(maxdata_xml, points, donors):
    """physicsdefinition.bin for a car: spheres from its MAXData.xml, a hull around its body's
    vertices, the rigid-body fields from the closest of donors ([(parse() result)] of game
    cars). Returns (bytes, donor index)."""
    verts, faces = hull_from_points(points)
    lo, hi = extents(verts)
    size = [hi[i] - lo[i] for i in range(3)]
    best, best_d = None, None
    for k, d in enumerate(donors):
        h = [s for s in d["shapes"] if s["type"] == "hull"][0]["hull"]["vertices"]
        dl, dh = extents(h)
        dist = sum(((dh[i] - dl[i]) - size[i]) ** 2 for i in range(3))
        if best_d is None or dist < best_d:
            best, best_d = k, dist
    donor = donors[best]
    dh = [s for s in donor["shapes"] if s["type"] == "hull"][0]["hull"]["vertices"]
    body = scaled_body(donor, dh, verts)
    return write(body, maxdata_spheres(maxdata_xml), hull_blob(verts, faces)), best
