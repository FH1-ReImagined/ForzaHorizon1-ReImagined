#!/usr/bin/env python3
"""The car list's picture of an FM1 car (#930): FM1's model drawn as FH1's thumbnails are.

FH1's thumbnails (media/ui/textures/thumbnails/thumbnail_<id>.xds in ui/Textures.zip) are 768 x
288, format 6 (8_8_8_8) with 8-in-32 byte order, tiled, one level: big-endian A, R, G, B per texel
behind a 52-byte header. They hold the car cut out (the alpha clear around it, a soft shadow
under it): seen from its front left at three quarters, filling x 128-470 with its wheels at
the bottom, and its right side turned nose up at x 670-752 (FH1's RX-7 measured).

This draws FM1's model the same way: the stock parts at FM1's LOD 1 and FM1's wheel at each of
the four wheel centres (mirrored on the right), the paint FM1's first livery colour with the
livery texture over it (opaque where printed, FH1's convention), the lamps and badges from the
texture, glass, trim and tires in fixed colours; Lambert and a specular highlight from one
light, a z-buffer, 2 x 2 samples per texel. Standard library only.
"""
import math
import struct

import fh1_xds
import fm1_cars

WIDTH, HEIGHT = 768, 288
MAIN_BOX = (128, 470, 262)     # x from, x to, the wheels' bottom
SIDE_BOX = (32, 255, 670, 752)  # (turned) y from, y to, x from, x to
SS = 2                          # samples per texel along each side

DARK = {2: (32, 38, 48), 6: (24, 26, 30), 5: (26, 26, 28), 9: (26, 26, 28), 15: (22, 22, 24), 20: (30, 30, 32),
        28: (26, 26, 28), 21: (18, 18, 18), 19: (30, 30, 34), 14: (40, 40, 42), 18: (20, 20, 20), 8: (24, 24, 24)}
SHINY = {1: 0.55, 2: 0.9, 6: 0.9, 7: 0.9, 11: 0.6, 25: 0.6, 27: 0.6}
SKIP = {13}  # the wheel's blur disc
LIGHT = (-0.45, 0.8, 0.4)


def _norm(v):
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


class Texture:
    def __init__(self, width, height, texels):
        self.w, self.h, self.t = width, height, texels

    def at(self, u, v):
        x = int((u % 1.0) * self.w) % self.w
        y = int((v % 1.0) * self.h) % self.h
        return self.t[y * self.w + x]


def texture_from_xpr(data):
    w, h, layers = fm1_cars.dxt5_layers(data)
    return Texture(w, h, fh1_xds.decode_level(layers[0][0], w // 4, h // 4))


def triangles(car, wheel_parts, lod=1):
    """[(3 positions, 3 normals, 3 uvs, material, kind)] in FM1's space (x right, y up, z forward);
    kind "car" or "wheel"."""
    out = []
    for part in fm1_cars.stock_parts(car):
        for slod, mat, idx in part.subparts:
            if slod != lod:
                continue
            for a, b, c in fm1_cars.strip_triangles(idx):
                if max(a, b, c) < len(part.verts):
                    vs = [part.verts[a], part.verts[b], part.verts[c]]
                    out.append(([v[0] for v in vs], [v[1] for v in vs], [v[2] for v in vs], mat, "car"))
    wheel_lod = min((s[0] for p in wheel_parts for s in p.subparts), default=1)
    for centre in car.wheels:
        mirror = centre[0] > 0  # FM1's wheel has its outer face at -x: the left side's
        for part in wheel_parts:
            for slod, mat, idx in part.subparts:
                if slod != wheel_lod or mat in SKIP:
                    continue
                for a, b, c in fm1_cars.strip_triangles(idx):
                    if max(a, b, c) >= len(part.verts):
                        continue
                    vs = [part.verts[a], part.verts[b], part.verts[c]]
                    pos = [((-p[0] if mirror else p[0]) + centre[0], p[1] + centre[1], p[2] + centre[2]) for p, _, _ in vs]
                    nrm = [((-n[0] if mirror else n[0]), n[1], n[2]) for _, n, _ in vs]
                    out.append((pos, nrm, [v[2] for v in vs], mat, "wheel"))
    return out


class Camera:
    """A perspective camera looking from direction `toward` (from the car to the eye)."""

    def __init__(self, target, toward, distance=9.0, focal=4.0):
        self.target, self.f = target, focal
        self.back = _norm(toward)
        self.right = _norm(_cross(self.back, (0.0, 1.0, 0.0)))  # FM1's space is left-handed
        self.up = _norm(_cross(self.right, self.back))
        self.eye = tuple(t + b * distance for t, b in zip(target, self.back))

    def project(self, p):
        d = tuple(a - b for a, b in zip(p, self.eye))
        z = -_dot(d, self.back)
        return (_dot(d, self.right) * self.f / z, -_dot(d, self.up) * self.f / z, z)


def _colour(mat, kind, uv, paint, livery, wheel_tex):
    if kind == "wheel":
        if mat in (11, 12) and wheel_tex:
            r, g, b, _ = wheel_tex.at(*uv)
            return (r, g, b) if mat == 11 else (r // 2, g // 2, b // 2)
        return DARK.get(mat, (40, 40, 40))
    if mat in DARK:
        return DARK[mat]
    r, g, b, a = livery.at(*uv) if livery else (0, 0, 0, 0)
    if mat == 1:
        k = a / 255.0
        return tuple(int(p * (1 - k) + t * k) for p, t in zip(paint, (r, g, b)))
    return (r, g, b)


def render(tris, view, width, height, paint, livery, wheel_tex):
    """(width x height RGBA bytes, the car's bounds (x0, y0, x1, y1) in texels): the triangles
    drawn by `view` (a function from a position to the texel x, y and depth), SS x SS samples."""
    W, H = width * SS, height * SS
    depth = [math.inf] * (W * H)
    colour = [None] * (W * H)
    light = _norm(LIGHT)
    for pos, nrm, uvs, mat, kind in tris:
        pts = [view(p) for p in pos]
        if min(p[2] for p in pts) <= 0:
            continue
        xs, ys = [p[0] * SS for p in pts], [p[1] * SS for p in pts]
        x0, x1 = max(0, int(min(xs))), min(W - 1, int(max(xs)) + 1)
        y0, y1 = max(0, int(min(ys))), min(H - 1, int(max(ys)) + 1)
        area = (xs[1] - xs[0]) * (ys[2] - ys[0]) - (xs[2] - xs[0]) * (ys[1] - ys[0])
        if abs(area) < 1e-9 or x0 > x1 or y0 > y1:
            continue
        for y in range(y0, y1 + 1):
            py = y + 0.5
            for x in range(x0, x1 + 1):
                px = x + 0.5
                w0 = ((xs[1] - px) * (ys[2] - py) - (xs[2] - px) * (ys[1] - py)) / area
                w1 = ((xs[2] - px) * (ys[0] - py) - (xs[0] - px) * (ys[2] - py)) / area
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                z = w0 * pts[0][2] + w1 * pts[1][2] + w2 * pts[2][2]
                i = y * W + x
                if z >= depth[i]:
                    continue
                depth[i] = z
                n = _norm(tuple(w0 * nrm[0][k] + w1 * nrm[1][k] + w2 * nrm[2][k] for k in range(3)))
                uv = (w0 * uvs[0][0] + w1 * uvs[1][0] + w2 * uvs[2][0], w0 * uvs[0][1] + w1 * uvs[1][1] + w2 * uvs[2][1])
                base = _colour(mat, kind, uv, paint, livery, wheel_tex)
                lam = abs(_dot(n, light))
                spec = SHINY.get(mat, 0.15) * max(0.0, abs(n[1]) * 0.6 + _dot(n, light) * 0.4) ** 24
                shade = 0.3 + 0.6 * lam
                colour[i] = tuple(min(255, int(c * shade + 255 * spec)) for c in base)
    out = bytearray(width * height * 4)
    bounds = [width, height, -1, -1]
    for y in range(height):
        for x in range(width):
            acc, n = [0, 0, 0], 0
            for sy in range(SS):
                for sx in range(SS):
                    c = colour[(y * SS + sy) * W + x * SS + sx]
                    if c:
                        n += 1
                        acc = [a + b for a, b in zip(acc, c)]
            if n:
                o = (y * width + x) * 4
                out[o:o + 4] = bytes([acc[0] // n, acc[1] // n, acc[2] // n, 255 * n // (SS * SS)])
                bounds = [min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y)]
    return bytes(out), tuple(bounds)


def _fit(tris, camera, box_w, box_h, anchor):
    """A view function: camera's projection scaled into box_w x box_h, anchored at (x, bottom y)
    (the car's horizontal middle and the lowest point)."""
    pts = [camera.project(p) for t in tris for p in t[0]]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    s = min(box_w / (x1 - x0), box_h / (y1 - y0))
    ax, ay = anchor

    def view(p):
        q = camera.project(p)
        return (ax + (q[0] - (x0 + x1) / 2) * s, ay + (q[1] - y1) * s, q[2])
    return view


def _shadow(img, width, height, cx, cy, rx, ry, strength=0.75):
    """A soft dark ellipse under the car where the alpha is clear."""
    for y in range(max(0, int(cy - ry * 1.6)), min(height, int(cy + ry * 1.6) + 1)):
        for x in range(max(0, int(cx - rx * 1.4)), min(width, int(cx + rx * 1.4) + 1)):
            d = ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2
            a = strength * max(0.0, 1.0 - d) ** 1.5
            if a <= 0:
                continue
            o = (y * width + x) * 4
            old = img[o + 3] / 255.0
            na = old + a * (1 - old)
            if na > 0:
                for k in range(3):
                    img[o + k] = int(img[o + k] * old / na)
                img[o + 3] = int(na * 255)


def thumbnail(car, wheel_parts, paint, livery, wheel_tex):
    """WIDTH x HEIGHT RGBA bytes (straight alpha, row by row): the front three-quarter view and
    the right side turned nose up."""
    tris = triangles(car, wheel_parts)
    allp = [p for t in tris for p in t[0]]
    centre = tuple((min(p[k] for p in allp) + max(p[k] for p in allp)) / 2 for k in range(3))
    img = bytearray(WIDTH * HEIGHT * 4)

    # the main view: from the front left, a little above
    cam = Camera(centre, (-0.85, 0.22, 1.0))
    x_from, x_to, bottom = MAIN_BOX
    view = _fit(tris, cam, x_to - x_from, bottom - 100, ((x_from + x_to) / 2, bottom))
    main, (bx0, by0, bx1, by1) = render(tris, view, WIDTH, HEIGHT, paint, livery, wheel_tex)
    _composite(img, main, WIDTH, HEIGHT)
    _shadow_under(img, main, WIDTH, HEIGHT, bx0, bx1, by1)

    # the right side, nose right, then turned a quarter anticlockwise (nose up)
    y_from, y_to, sx_from, sx_to = SIDE_BOX
    length, tall = y_to - y_from, sx_to - sx_from
    side_cam = Camera(centre, (1.0, 0.18, 0.12))
    sview = _fit(tris, side_cam, length, tall, (length / 2, tall - 2))
    side, _ = render(tris, sview, length, tall, paint, livery, wheel_tex)
    for y in range(tall):
        for x in range(length):
            # (x, y) of the side picture -> (y, length - 1 - x) turned: nose (right) up
            tx, ty = sx_from + y, y_from + (length - 1 - x)
            o, s = (ty * WIDTH + tx) * 4, (y * length + x) * 4
            if side[s + 3]:
                img[o:o + 4] = side[s:s + 4]
    return bytes(img)


def _composite(img, layer, width, height):
    for i in range(0, width * height * 4, 4):
        if layer[i + 3]:
            img[i:i + 4] = layer[i:i + 4]


def _shadow_under(img, layer, width, height, x0, x1, bottom):
    cx, rx = (x0 + x1) / 2, (x1 - x0) * 0.52
    _shadow(img, width, height, cx, bottom - 6, rx, 14)


def car_thumbnail(fm1_dir, car_name, paint):
    """RGBA bytes of an FM1 car's thumbnail (paint: its first livery colour, (r, g, b))."""
    cab, car = fm1_cars.load_car(fm1_dir, car_name)
    name = fm1_cars.cab_name(car, cab)

    def find(n):
        return next((k for k in cab.files if k.startswith("cars\\%s\\" % name.lower()) and k.endswith("\\" + n)), None)
    key = find("livery0hi.xpr") or find("livery0.xpr")
    livery = texture_from_xpr(cab.read(key)) if key else None
    _, wheel_parts, wheel_xpr, _ = fm1_cars.load_wheel(fm1_dir, car_name)
    wheel_tex = texture_from_xpr(wheel_xpr) if wheel_xpr else None
    return thumbnail(car, wheel_parts, paint, livery, wheel_tex)


def to_xds(rgba, template):
    """The thumbnail .xds: the template's header (an FH1 thumbnail, WIDTH x HEIGHT, format 6,
    tiled) with these texels."""
    f0, f1, f2 = struct.unpack_from(">3I", template, 28)
    w, h = (f2 & 0x1FFF) + 1, ((f2 >> 13) & 0x1FFF) + 1
    if (w, h) != (WIDTH, HEIGHT) or f1 & 0x3F != 6 or not f0 >> 31:
        raise ValueError("not a %d x %d 8_8_8_8 tiled thumbnail" % (WIDTH, HEIGHT))
    pitch = ((f0 >> 22) & 0x1FF) * 32
    body = bytearray(len(template) - 52)
    for y in range(h):
        for x in range(w):
            s = (y * w + x) * 4
            r, g, b, a = rgba[s:s + 4]
            o = fh1_xds.tiled_offset(x, y, pitch, 2)
            body[o:o + 4] = bytes((a, r, g, b))
    return template[:52] + bytes(body)


def from_xds(xds):
    """(width, height, RGBA bytes) of a thumbnail .xds (to check one)."""
    f0, f1, f2 = struct.unpack_from(">3I", xds, 28)
    w, h = (f2 & 0x1FFF) + 1, ((f2 >> 13) & 0x1FFF) + 1
    pitch = ((f0 >> 22) & 0x1FF) * 32
    body = xds[52:]
    out = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            o = fh1_xds.tiled_offset(x, y, pitch, 2)
            a, r, g, b = body[o:o + 4]
            out[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes((r, g, b, a))
    return w, h, bytes(out)
