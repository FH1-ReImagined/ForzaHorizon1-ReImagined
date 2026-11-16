#!/usr/bin/env python3
"""Forza Motorsport (2005, original Xbox) cars in Forza Horizon (2012) (#930).

The first step: an FM1 car's model, textures and lamps built into an FH1 car's files (the donor),
which keeps the donor's handling, wheels and sounds until FM1's physics are converted. The FM1
model is moved so that its wheel centres sit on the donor's (CarPartPositions in FH1's database).

FM1's files, measured on the game's disc (docs/modding/fm1-cars.md has the details):
  media/cabs/car_<car>.cab   an archive (below); cars/<car>/car.carbin is the model
  archive    u32 magic 0xC0C0AAAA, u32 version 15, u32 count, u32 flags (bit 0: each file is its
             own zlib stream), ...; at 0x206, count - 9 entries of 6 x u32 (hash, offset, size,
             unpacked size, name offset, descriptor offset); then u32, the NUL-terminated names.
             .ca2/.xb2 archives are the same, the whole file stored as back-to-back zlib streams.
  car.carbin chunks: u32 size (the whole chunk), u32 type, a NUL-terminated name, the payload,
             then child chunks; u32 counts sit between some of them. Little-endian.
             Car { Attribs, Driver, Part*, Flare* }; Part { Verts, Damage, Pivots, Attributes,
             Subpart* }. Part: u32 hash, u32 name length, the name. Attribs: u32 1, u32 5, the 4
             wheel centres (f32 x3: LF, RF, RR, LR). Verts: u32 count, 20-byte vertices (s16 x3
             position * 5/32768 m, s16 x3 normal / 32767, u16 ?, u16 0, s16 x2 uv / 32768).
             Subpart: u32 LOD (1 the best), u32 material id, u32 6, u32 index count, 16 bytes
             (zero at LOD 1, not decoded at the others), the u16 strip indices; every LOD indexes
             the part's one vertex list. Flare: u32 0, u32 colour, f32 size, f32 x3 position,
             f32 x3 direction (a lamp's glow).
  textures   cars/<car>/liveries/standard/livery0hi.xpr (the atlas, 1024, DXT5 with its levels)
             and livery0.xpr (512 x 512 x 2: clean, then dirty and scratched): read_xpr below.
  space      x to the car's right (its left is -x), y up, z forward. FH1's models have z to the
             rear: z is negated, which mirrors the model, so every triangle's winding is turned.

Usage:
  fm1_cars.py parts FM1_DIR CAR
  fm1_cars.py car FM1_DIR FH1_DIR CAR DONOR OUT_DIR [--update TU_DIR] [--year Y] [--mod]
FM1_DIR is FM1's disc (with media/cabs), FH1_DIR the FH1 game folder (with media), DONOR an FH1
car's MediaName (MAZ_RX7_97). OUT_DIR gets the donor's three models with the FM1 car in them,
their stripped_ copies and the car's textures; with --mod, laid out as a mod folder. Standard
library only.
"""
import argparse
import math
import os
import re
import struct
import sys
import zlib
from dataclasses import dataclass, field

import fh1_carbin as fc
import fh1_carbuild as cb
import fh1_xds

CAB_MAGIC = 0xC0C0AAAA
POS = 5 / 32768
CHUNKS = {"Car", "Attribs", "Driver", "Part", "Verts", "Damage", "Pivots", "Attributes", "Subpart", "Flare"}

# FM1 material id -> FH1 techniques, the first one the donor's model uses. Measured by drawing
# each id's meshes of the Border MR2 (FM1 does not name them; Mega.ca2 cars/shadersettings has
# its shader groups: Body, Window, HeadLights, TailLights, BlackGlass, Chrome, Badge...).
FM1_MATERIALS = {
    1: ("body",),                                 # paint
    2: ("window", "glass"),                       # windows
    3: ("taillight2S", "tail_light", "tail_light_lod0"),  # tail and brake lamps
    4: ("oldhead", "head_light", "hidhead"),      # head lamps (and the bumper's lamps)
    5: ("black",),                                # one triangle on the MR2
    6: ("black_glass", "window"),                 # tinted glass (the MR2's T-top)
    7: ("chrome",),                               # exhaust tips, mirror glass
    9: ("black",),                                # black trim, grilles, wheel arches
    14: ("interior", "plastic"),                  # the cockpit (the _cockpit model has no "interior")
    15: ("black",),                               # the front's lower openings
    18: ("undercarriage", "black"),               # suspension, underbody
    19: ("carbon_fiber", "black"),                # the wing's planes
    20: ("black",),                               # side mouldings, lower trim
    21: ("rubber_trim", "black"),                 # window seals
    25: ("badge", "emblem", "chrome"),            # badges
    27: ("textured_reflector", "reflector", "chrome"),  # lamp reflectors
    28: ("black",),                               # engine cover vents
}
FALLBACK_MATERIALS = ("black", "plastic")

# FM1 part (its stock look, without _LOD1 and the variant letter) -> FH1 sections, the first one
# the donor's model has. A part the main model has no section for goes into the body; at LOD0 a
# part only goes into the file (_lod0 or _cockpit) that has its section.
PART_SECTIONS = [
    (r"bumperF", ("bumperFa", "bumperF")),
    (r"bumperR", ("bumperRa", "bumperR")),
    (r"hood", ("hooda", "hood")),
    (r"trunk(_m)?", ("trunk", "trunka")),
    (r"wing", ("winga", "trunk")),                # a stock wing sits on the trunk lid
    (r"exhaust", ("exhausta", "exhaust", "exhaustLa", "exhaustRa")),
    (r"exhaust([LR])", ("exhaust{0}a", "exhaust{0}", "exhausta", "exhaust")),
    (r"skirt([LR])", ("skirt{0}a", "body")),
    (r"glass(F|R|LF|RF)", ("glass{0}",)),
    (r"glass(LHL|RHL|LTL|RTL)", ("glass{0}", "body")),
    (r"glass(LR|RR)", ("glass{0}", "body")),
    (r"mirror([LR])", ("mirror{0}",)),
    (r"interior", ("interior",)),
    (r"steering_wheel", ("steering_wheel",)),
    (r"suspension", ("undercarriagea", "undercarriage")),
    (r"headlight[LR]down", ("body",)),            # pop-up lamps, lowered: fixed in place for now
    (r"headlight([LR])", ("headlight{0}",)),
    (r"taillight([LR])", ("taillight{0}",)),
    (r"body", ("body",)),
]
# the driver (and his limbs, separate parts on some cars), raised pop-ups, race parts, brakes
# (the Stratos's calipers are parts of its model: FH1 draws its own from the car's _caliper*_LOD0 files)
SKIP_PARTS = re.compile(r"^driver|^(head|chest|abdomen|(left|right)_\w+)$|up$|race|cage|^\d\d_|^(caliper|rotor)", re.I)
KEEP_SECTIONS = {"wheel"}  # the donor's own wheel stays (FM1's wheels are a later step)
A_PILLAR_MARGIN = 0.05


class Fm1Error(ValueError):
    pass


# ============================================================================= FM1 files

def _inflate_all(d):
    out, pos = [], 0
    while pos < len(d):
        o = zlib.decompressobj()
        out.append(o.decompress(d[pos:]) + o.flush())
        pos = len(d) - len(o.unused_data)
        while pos < len(d) and d[pos] == 0:
            pos += 1
    return b"".join(out)


class Cab:
    """An FM1 archive (.cab, .ca2, .xb2)."""

    def __init__(self, path):
        with open(path, "rb") as f:
            d = f.read()
        if struct.unpack_from("<I", d, 0)[0] != CAB_MAGIC:
            d = _inflate_all(d)
        magic, _, count, flags = struct.unpack_from("<4I", d, 0)
        if magic != CAB_MAGIC:
            raise Fm1Error("%s is not an FM1 archive" % path)
        n = count - 9  # measured: the table always holds count - 9 entries
        names = 0x206 + 24 * n + 4
        self.data, self.flags, self.files = d, flags, {}
        for i in range(n):
            _, off, size, usize, name_off, _ = struct.unpack_from("<6I", d, 0x206 + 24 * i)
            end = d.index(b"\0", names + name_off)
            name = d[names + name_off:end].decode("latin-1").replace("/", "\\").lower()
            self.files[name] = (off, size, usize)

    def read(self, name):
        off, size, usize = self.files[name.replace("/", "\\").lower()]
        b = self.data[off:off + size]
        return zlib.decompress(b) if self.flags & 1 and size != usize else b


@dataclass
class Part:
    name: str
    verts: list = field(default_factory=list)     # [(pos, normal, uv)] FM1 space
    subparts: list = field(default_factory=list)  # [(lod, material, strip indices)]


@dataclass
class Car:
    parts: list
    wheels: list   # the 4 wheel centres: LF, RF, RR, LR
    flares: list   # [(position, direction, size, colour)]
    looks: dict = None  # tooldata.xml's visual groups: {group: [[part names] per option]}, P0 stock


def _chunk(b, o):
    if o + 12 > len(b):
        return None
    size, _ = struct.unpack_from("<II", b, o)
    e = b.find(b"\0", o + 8, o + 24)
    if e < 0:
        return None
    name = b[o + 8:e].decode("latin-1", "replace")
    if name not in CHUNKS or size < 8 or o + size > len(b):
        return None
    return name, e + 1, o + size


def _children(b, o, end):
    while o < end - 8:
        c = _chunk(b, o)
        if c is None:
            o += 4  # a u32 count between chunks
            continue
        yield c
        o = c[2]


def read_car(data):
    top = _chunk(data, 0)
    if not top or top[0] != "Car":
        raise Fm1Error("not an FM1 car model")
    car = Car([], [], [])
    flares = []

    def flare(body):
        _, colour, size, x, y, z, dx, dy, dz = struct.unpack_from("<2I7f", data, body)
        flares.append(((x, y, z), (dx, dy, dz), size, colour))
    for name, body, end in _children(data, top[1], top[2]):
        if name == "Attribs":
            f = struct.unpack_from("<12f", data, body + 8)
            car.wheels = [f[i:i + 3] for i in range(0, 12, 3)]
        elif name == "Flare":
            flare(body)
        elif name == "Part":
            _, ln = struct.unpack_from("<II", data, body)
            part = Part(data[body + 8:body + 8 + ln].decode("latin-1"))
            for cname, cbody, cend in _children(data, body + 8 + ln, end):
                if cname == "Verts":
                    n = struct.unpack_from("<I", data, cbody)[0]
                    stride = (cend - cbody - 4) // max(1, n)
                    if n and stride < 20:
                        raise Fm1Error("%s: %d-byte vertices" % (part.name, stride))
                    for i in range(n):
                        v = struct.unpack_from("<3h3hHH2h", data, cbody + 4 + i * stride)
                        part.verts.append(((v[0] * POS, v[1] * POS, v[2] * POS),
                                           cb.unit((v[3] / 32767, v[4] / 32767, v[5] / 32767), (0.0, 1.0, 0.0)),
                                           (v[8] / 32768, v[9] / 32768)))
                elif cname == "Subpart":  # 16 bytes (zero at LOD 1) come before the indices
                    lod, mat, _, n = struct.unpack_from("<4I", data, cbody)
                    if cbody + 32 + 2 * n > cend:
                        raise Fm1Error("%s: a subpart's indices run past it" % part.name)
                    part.subparts.append((lod, mat, struct.unpack_from("<%dH" % n, data, cbody + 32)))
                elif cname == "Flare":
                    flare(cbody)
            car.parts.append(part)
    car.flares = flares
    return car


LOOK_GROUPS = ("FrontBumper", "RearBumper", "Wing", "SideSkirt", "Hood", "Mirror", "Headlight", "RearLense", "Antenna")


def read_looks(tooldata):
    """{group: [[part base names, lower case] per option P0, P1...]} of a tooldata.xml's visual
    groups (<FrontBumper><P0 ...><Parts P0="bumperFa_LOD"/>): P0 is the stock look."""
    looks = {}
    for g in LOOK_GROUPS:
        m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (g, g), tooldata, re.S)
        if not m:
            continue
        opts = []
        for p in re.finditer(r"<P(\d+)\b[^>]*>(.*?)</P\1>", m.group(1), re.S):
            names = re.findall(r'P\d+="([^"]+)"', " ".join(re.findall(r"<Parts\b[^>]*>", p.group(2))))
            opts.append([re.sub(r"_LOD\d*$", "", n, flags=re.I).lower() for n in names])
        if opts:
            looks[g] = opts
    return looks


def load_car(fm1_dir, car):
    path = os.path.join(fm1_dir, "media", "cabs", "car_%s.cab" % car)
    if not os.path.isfile(path):
        raise Fm1Error("no %s" % path)
    cab = Cab(path)
    key = "cars\\%s\\car.carbin" % car.lower()
    if key not in cab.files:
        key = next((k for k in cab.files if k.endswith("\\car.carbin")), None)
        if key is None:  # 20 of the disc's 231 car archives hold no model (car_ferrari288.cab)
            raise Fm1Error("%s holds no model (car.carbin) on this disc" % os.path.basename(path))
    model = read_car(cab.read(key))
    cars = os.path.join(fm1_dir, "media", "cabs", "Cars.ca2")
    if os.path.isfile(cars):
        tools = Cab(cars)
        key = "cars\\%s\\tooldata.xml" % car.lower()
        if key in tools.files:
            model.looks = read_looks(tools.read(key).decode("latin-1"))
    return cab, model


def wheel_name(fm1_dir, car):
    """The car's stock wheel: Cars.ca2 cars/<car>/tooldata.xml <WheelStyle><P0 File=>. The Border
    MR2's is the MR2 Turbo's, toyotamr2."""
    cab = Cab(os.path.join(fm1_dir, "media", "cabs", "Cars.ca2"))
    key = "cars\\%s\\tooldata.xml" % car.lower()
    text = cab.read(key).decode("latin-1") if key in cab.files else ""
    m = re.search(r"<WheelStyle>.*?<P0\b[^>]*File=\"([^\"]+)\"", text, re.S)
    return m.group(1).lower() if m else car.lower()


def load_wheel(fm1_dir, car):
    """(name, parts, wheel.xpr bytes, wheel_vol.xpr bytes or None) of the car's stock wheel: one wheel
    (Mega.ca2 tires/art/<wheel>/tire.carbin, the same chunks as a car; FM1 mirrors it for the right
    side) and its textures (MegaXPR.ca2: wheel.xpr, and wheel_vol.xpr, 4 slices: the spin blur).
    Materials: 8 the tire, 11 the rim, 12 the inner barrel, 13 the blur disc."""
    name = wheel_name(fm1_dir, car)
    mega = Cab(os.path.join(fm1_dir, "media", "cabs", "Mega.ca2"))
    key = "tires\\art\\%s\\tire.carbin" % name
    if key not in mega.files:
        raise Fm1Error("FM1 has no wheel %s" % name)
    xpr = Cab(os.path.join(fm1_dir, "media", "cabs", "MegaXPR.ca2"))

    def tex(n):
        k = "tires\\art\\%s\\%s" % (name, n)
        return xpr.read(k) if k in xpr.files else None
    return name, read_car(mega.read(key)).parts, tex("wheel.xpr"), tex("wheel_vol.xpr")


def read_xpr(data):
    """The textures of an FM1 .xpr bundle: [{format, width, height, depth, levels, offset}].
    XPR0: u32 magic, u32 size, u32 header size, then 20-byte texture descriptors (u32 common,
    u32 data offset from the header's end, u32 lock, u32 format, u32 size) up to 0xFFFFFFFF.
    Format bits: 4-7 dimensions (3: a volume), 8-15 the colour format (0x0F DXT5), 16-19 mip
    levels, 20-23, 24-27 and 28-31 log2 width, height and depth."""
    magic, _, hsize = struct.unpack_from("<4sII", data, 0)
    if magic not in (b"XPR0", b"XPR1"):
        raise Fm1Error("not an FM1 texture bundle")
    out, o = [], 12
    while o + 4 <= hsize:
        common = struct.unpack_from("<I", data, o)[0]
        if common == 0xFFFFFFFF:
            break
        if (common >> 16) & 7 == 4:
            _, off, _, fmt, _ = struct.unpack_from("<5I", data, o)
            volume = (fmt >> 4) & 0xF == 3
            out.append(dict(format=(fmt >> 8) & 0xFF, width=1 << ((fmt >> 20) & 0xF), height=1 << ((fmt >> 24) & 0xF),
                            depth=1 << ((fmt >> 28) & 0xF) if volume else 1, levels=max(1, (fmt >> 16) & 0xF),
                            offset=hsize + off))
            o += 20
        else:
            o += 12
    return out


def dxt5_layers(data):
    """(width, height, [[mip blocks] per layer]) of an FM1 .xpr's first texture: DXT5, its blocks
    stored linearly (PC order), level after level; a volume's slices interleaved block by block
    (block i of slice s at i * depth + s), its largest level only."""
    tex = read_xpr(data)
    if not tex or tex[0]["format"] != 0x0F:
        raise Fm1Error("not a DXT5 texture")
    t = tex[0]
    w, h, depth, at = t["width"], t["height"], t["depth"], t["offset"]
    if depth > 1:
        bw, bh = fh1_xds.level_blocks(w, h, 0)
        blob = data[at:at + bw * bh * depth * 16]
        return w, h, [[b"".join(blob[(i * depth + s) * 16:(i * depth + s + 1) * 16] for i in range(bw * bh))]
                      for s in range(depth)]
    levels = []
    for i in range(min(t["levels"], max(w, h).bit_length())):
        bw, bh = fh1_xds.level_blocks(w, h, i)
        levels.append(data[at:at + bw * bh * 16])
        at += bw * bh * 16
    if at > len(data):
        raise Fm1Error("truncated texture")
    return w, h, [levels]


# FH1's car textures (by name) and which of FM1's they become. FM1's livery atlas holds the
# paint's decals and the lamps, badges and cockpit pieces, with FH1's alpha convention (opaque
# where printed, clear where the paint shows), so the car's UVs stay as they are.
TEXTURE_SOURCES = {
    "nodamage.xds": "clean", "nodamage_lod0.xds": "clean", "lights.xds": "clean", "lights_lod0.xds": "clean",
    "damagelights.xds": "clean", "damagelights_lod0.xds": "clean", "interior_lod0.xds": "clean",
    "damage.xds": "damaged", "damage_lod0.xds": "damaged",
}


def textures(cab, car, donor_names):
    """{FH1 texture name (the donor's spelling): .xds bytes}. clean: livery0hi.xpr (1024, its own
    mip levels), else livery0.xpr's first slice; damaged: livery0.xpr's second slice (dirt and
    scratches), mip levels made here."""
    def find(name):
        return next((k for k in cab.files if k.startswith("cars\\%s\\" % car.lower()) and k.endswith("\\" + name)), None)
    hi, lo = find("livery0hi.xpr"), find("livery0.xpr")
    lo_layers = dxt5_layers(cab.read(lo)) if lo else None
    if hi:
        w, h, (clean,) = dxt5_layers(cab.read(hi))
    elif lo_layers:
        w, h, clean = lo_layers[0], lo_layers[1], lo_layers[2][0]
    else:
        raise Fm1Error("%s has no livery0 texture" % car)
    if len(clean) < 2:
        clean = fh1_xds.mips_from(clean[0], w, h)
    damaged = (w, h, clean)
    if lo_layers and len(lo_layers[2]) > 1:
        dw, dh = lo_layers[0], lo_layers[1]
        damaged = (dw, dh, fh1_xds.mips_from(lo_layers[2][1][0], dw, dh))
    files = {"clean": fh1_xds.write_dxt5(clean, w, h), "damaged": fh1_xds.write_dxt5(damaged[2], damaged[0], damaged[1])}
    return {n: files[TEXTURE_SOURCES[n.lower()]] for n in donor_names if n.lower() in TEXTURE_SOURCES}


TAIL_MATERIALS = {3}


def lamps(car, parts, dy, dz):
    """The car's lamps in FH1's model space: {"head": [(side, position)], "tail": [...]}, side 1
    its left, 2 its right. Head lamps are FM1's Flares (the raised lamps of a pop-up); tail lamps
    the middle of each side's tail lamp meshes at FM1's LOD 1 (FM1 gives them no Flare), at their
    rearmost point."""
    import fm4_cars as f4
    out = {"head": [], "tail": []}
    for pos, _, _, _ in car.flares:
        p = to_fh1(pos, dy, dz)
        out["head"].append((1 if p[0] < 0 else 2, p))
    pts = {1: [], 2: []}
    for part in parts:
        for lod, mat, idx in part.subparts:
            if lod == 1 and mat in TAIL_MATERIALS:
                for k in set(idx):
                    p = to_fh1(part.verts[k][0], dy, dz)
                    if p[2] > 0 and abs(p[0]) > 0.01:
                        pts[1 if p[0] < 0 else 2].append(p)
    for side in (1, 2):
        for lo, hi in (f4._lamp_clusters(pts[side]) if pts[side] else []):
            x, y = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
            if abs(x) >= f4.LIGHT_MIDDLE:
                out["tail"].append((side, (x, y, hi[2])))
    return out


def light_list(car, parts, dy, dz, year):
    """The main model's light list (docs/modding/fm4-cars.md, the light list): a head lamp and its
    flare at each head lamp, the tail light and two brake sprites at each tail lamp."""
    import fm4_cars as f4
    kind = 3 if year < 2000 else 1 if year < 2010 else 2
    recs = []
    found = lamps(car, parts, dy, dz)
    for side, (x, y, z) in found["head"]:
        recs.append(f4.light_record((x, y, z + f4.LIGHT_INSET), True, side, f4.HEAD_LAMP[kind]))
        flare = (f4.HEAD_LAMP[kind][0], 4, 1, f4.HEAD_FLARE[kind], 179, 7, (30, 70))
        recs.append(f4.light_record((x, y, z + f4.LIGHT_INSET - f4.FLARE_AHEAD), True, side, flare))
    for side, (x, y, z) in found["tail"]:
        for rec in f4.TAIL_LAMP:
            recs.append(f4.light_record((x, y, z - f4.LIGHT_INSET), False, side, rec))
    return struct.pack(">I", len(recs)) + b"".join(recs)


def strip_triangles(idx):
    """The triangles of an FM1 strip (no restarts; degenerate triangles join its runs)."""
    tris = []
    for i in range(len(idx) - 2):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if a != b and b != c and a != c:
            tris.append((a, b, c) if i % 2 == 0 else (b, a, c))
    return tris


def base_name(name):
    return re.sub(r"_LOD\d$", "", name)


def stock_parts(car):
    """The parts of the car's stock look: tooldata.xml's P0 of each visual group (the S2000's wing
    is wingNone, its rear bumper brings exhaustLa/Ra), the other options left out, and every part
    no group names; without a tooldata, of a lettered group (bumperFa, bumperFb...) only the 'a'.
    Never the driver (driver01, or his limbs: Head, Chest, Left_forearm...), the raised pop-up
    lamps, race parts or brakes."""
    if car.looks:
        options = {n for opts in car.looks.values() for o in opts for n in o}
        stock = {n for opts in car.looks.values() for n in opts[0]}
        # a lettered part of a family the groups use (winga..wingg, bumperFe, hoode) is an option
        # too, listed or not (the S2000 keeps unlisted ones); mirrorL is not lettered
        families = {re.sub(r"([a-g]|race[a-c]?|none)$", "", n) for n in options}

        def stock_look(name):
            low = name.lower()
            if low in stock:
                return True
            if low in options:
                return False
            m = re.fullmatch(r"(.+?)[a-g]", name)
            return not (m and m.group(1).lower() in families)
        return [p for p in car.parts if p.verts and not SKIP_PARTS.search(base_name(p.name))
                and stock_look(base_name(p.name))]
    names = [base_name(p.name) for p in car.parts if p.verts]
    groups = {}
    for n in names:
        m = re.match(r"^(.*?)([a-g])$", n)
        root = m.group(1) if m and any(o != n and o.startswith(m.group(1)) and len(o) == len(n) for o in names) else n
        groups.setdefault(root, []).append(n)
    keep = set()
    for root, members in groups.items():
        if len(members) == 1:
            keep.add(members[0])
        else:
            keep.update(n for n in members if n == root + "a")
    return [p for p in car.parts if p.verts and base_name(p.name) in keep and not SKIP_PARTS.search(base_name(p.name))]


# ============================================================================= FM1 -> FH1

def section_for(part, names):
    """The FH1 section (one of names) an FM1 part goes into, or None if this file has none."""
    low = {n.lower(): n for n in names}
    base = re.sub(r"^glass_", "glass", base_name(part))  # the Stratos's glass_F, glass_LF
    if re.fullmatch(r"(bumper[FR]|hood|trunk|skirt[LR]|exhaust[LR]?|glass[LR][HT]L)[a-g]", base):  # an option
        base = base[:-1]
    for pat, cands in PART_SECTIONS:
        m = re.fullmatch(pat, base)
        if m:
            for c in cands:
                c = c.format(*m.groups())
                if c.lower() in low:
                    return low[c.lower()]
            return None
    return low.get("body")


def material_for(mat, available):
    for c in FM1_MATERIALS.get(mat, ()) + FALLBACK_MATERIALS:
        if c in available:
            return c
    return sorted(available)[0]


def placement(car, donor_wheels):
    """(dy, dz): FM1 (x, y, z) -> FH1 (x, y + dy, -(z + dz)), the FM1 wheel centres on the donor's.
    donor_wheels: LF, RF, RR, LR as CarPartPositions has them (z forward)."""
    if len(car.wheels) != 4 or len(donor_wheels) != 4:
        return 0.0, 0.0
    mid = lambda w: ((w[0][2] + w[1][2]) / 2 + (w[2][2] + w[3][2]) / 2) / 2  # noqa: E731
    dy = sum(w[1] for w in donor_wheels) / 4 - sum(w[1] for w in car.wheels) / 4
    return dy, mid(donor_wheels) - mid(car.wheels)


def to_fh1(p, dy, dz):
    return (p[0], p[1] + dy, -(p[2] + dz))


def pieces(car, parts, kind, names, available, dy, dz, report):
    """FH1 Pieces of the car for one model file. kind: "main" (LOD 1-5) or "lod0"."""
    top = max((s[0] for p in parts for s in p.subparts), default=1)
    out = []
    for part in parts:
        sec = section_for(part.name, names)
        if sec is None:
            if kind == "main":
                sec = section_for("body", names)
                report.setdefault("into_body", []).append(part.name)
            else:
                continue
        report.setdefault("parts", {}).setdefault(base_name(part.name), set()).add(sec)
        pos = [to_fh1(v[0], dy, dz) for v in part.verts]
        nrm = [(v[1][0], v[1][1], -v[1][2]) for v in part.verts]
        uv = [v[2] for v in part.verts]
        lods = {s[0] for s in part.subparts}
        if kind == "main":
            want = {k: min(k, top) for k in range(1, 6) if min(k, top) in lods}
        else:
            want = {0: 1} if 1 in lods else {}
        for fh1_lod, fm1_lod in want.items():
            for lod, mat, idx in part.subparts:
                if lod != fm1_lod:
                    continue
                tris = [(c, b, a) for a, b, c in strip_triangles(idx)]  # z negated: winding turned
                if tris:
                    out.append(cb.Piece(sec, material_for(mat, available), fh1_lod, pos, nrm, uv, uv, tris))
    return out


def inside(p, lo, hi):
    return all(lo[k] <= p[k] <= hi[k] for k in range(3))


def a_pillar_pieces(body_pieces, bounds):
    """The body's triangles within the donor's a_pillar (the body as seen from the cockpit)."""
    lo = [b - A_PILLAR_MARGIN for b in bounds[0]]
    hi = [b + A_PILLAR_MARGIN for b in bounds[1]]
    out = []
    for p in body_pieces:
        tris = [t for t in p.tris if all(inside(p.pos[k], lo, hi) for k in t)]
        if tris:
            out.append(cb.Piece("a_pillar", p.material, p.lod, p.pos, p.nrm, p.uv0, p.uv1, tris))
    return out


def donor_wheels(db, media_name):
    row = db.execute("select Id from Data_Car where MediaName = ? collate nocase", (media_name,)).fetchone()
    if not row:
        raise Fm1Error("no car %s in FH1's database" % media_name)
    pos = dict((r[0], r[1:]) for r in db.execute(
        "select PartId, PartPosX, PartPosY, PartPosZ from CarPartPositions where Ordinal = ?", (row[0],)))
    return [pos[k] for k in (5, 6, 7, 8) if k in pos]


# DriverControls (CBaseCarModelData, version 3: 0x100 bytes, 16 rows of 4 big-endian floats, model
# space) hold the turning steering wheel: the game turns the steering_wheel section about the pivot
# along the axis, whatever the section's own offset (with the RX-7's rows the MR2's wheel swung
# about the RX-7's pivot 0.77 m to its right, up over the roof). The main model and the _cockpit
# model use different rows:
#   main     0 pivot, 1 axis (towards the driver), 2/3 the hands on the rim, 4 the seat, 5 on the
#            car's middle (the gear lever?)
#   cockpit  6-11 points round the rim (the hands' path), 12 pivot, 13 axis, 14 the seat
DRIVER_ROWS = {0: ((0, 2, 3, 4), 1), 12: ((6, 7, 8, 9, 10, 11, 12, 14), 13)}


def driver_controls_at(data):
    """Byte offset of the DriverControls rows in a carbin (None for an older version)."""
    import carbin_dump
    at = carbin_dump.parse(data)["base"]["at"]
    count = struct.unpack_from(">I", data, at + 12)[0]
    o = at + 16 + count * 140
    return o + 4 if struct.unpack_from(">I", data, o)[0] == 3 else None


def move_driver_controls(header, at, centre, axis):
    """The header with DriverControls' pivot on centre and its axis `axis`; the hands and the seat
    moved with the pivot. Unchanged if neither layout's pivot is set."""
    rows = [list(struct.unpack_from(">4f", header, at + 16 * r)) for r in range(16)]
    for pivot, (moved, axis_row) in DRIVER_ROWS.items():
        if any(rows[pivot][:3]):
            d = [c - p for c, p in zip(centre, rows[pivot][:3])]
            for r in moved:
                rows[r][:3] = [v + dv for v, dv in zip(rows[r][:3], d)]
            rows[axis_row][:3] = list(axis)
            out = bytearray(header)
            for r in range(16):
                struct.pack_into(">4f", out, at + 16 * r, *rows[r])
            return bytes(out)
    return header


def _jacobi_min(c):
    """The eigenvector of a symmetric 3 x 3 matrix with the smallest eigenvalue."""
    a = [row[:] for row in c]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(50):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda ij: abs(a[ij[0]][ij[1]]))
        if abs(a[p][q]) < 1e-15:
            break
        th = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        cs, sn = math.cos(th), math.sin(th)
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p], a[k][q] = cs * akp - sn * akq, sn * akp + cs * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k], a[q][k] = cs * apk - sn * aqk, sn * apk + cs * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p], v[k][q] = cs * vkp - sn * vkq, sn * vkp + cs * vkq
    i = min(range(3), key=lambda k: a[k][k])
    return [v[0][i], v[1][i], v[2][i]]


def wheel_axis(pieces, towards):
    """The steering wheel's turning axis: the normal of the plane its vertices lie closest to
    (the least spread direction; averaging face normals leant it 7 degrees on the MR2), at its
    finest LOD, pointing like `towards`."""
    finest = min(p.lod for p in pieces)
    pts = {tuple(p.pos[k]) for p in pieces if p.lod == finest for t in p.tris for k in t}
    n = len(pts) or 1
    m = [sum(q[k] for q in pts) / n for k in range(3)]
    c = [[sum((q[i] - m[i]) * (q[j] - m[j]) for q in pts) / n for j in range(3)] for i in range(3)]
    axis = _jacobi_min(c)
    if sum(a * b for a, b in zip(axis, towards)) < 0:
        axis = [-x for x in axis]
    length = math.sqrt(sum(x * x for x in axis)) or 1.0
    return tuple(x / length for x in axis)


def build_models(car, donor_files, wheels, year=1995, seed=930):
    """{file name: (Carbin, report)} for the donor's main, _lod0 and _cockpit models. The main
    model gets FM1's lamps as its light list (year: the headlight sprites of a car that old)."""
    import fm4_carbin
    dy, dz = placement(car, wheels)
    parts = stock_parts(car)
    out = {}
    transparent = cb.transparent_materials(list(donor_files.values()))
    for name, donor in donor_files.items():
        names = [s.name for s in donor.sections]
        kind = "lod0" if re.search(r"_(lod0|cockpit)\.carbin$", name, re.I) else "main"
        available = cb.materials(donor)
        report = {"placement": (round(dy, 4), round(dz, 4))}
        ps = pieces(car, parts, kind, names, available, dy, dz, report)
        if kind == "lod0" and "a_pillar" in names and "body" not in names:
            ap = next(s for s in donor.sections if s.name == "a_pillar")
            pts = fc.world(ap, ap.lod0)
            if pts:
                bounds = ([min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)])
                body = pieces(car, parts, kind, names + ["body"], available, dy, dz, {})
                ps += a_pillar_pieces([p for p in body if p.section == "body"], bounds)
        sw = [p for p in ps if p.section == "steering_wheel"]
        model = fc.clone(donor)
        if sw:  # the steering wheel turns about its section's pivot: FM1's wheel's centre
            pts = [p.pos[k] for p in sw for t in p.tris for k in t]
            centre = [(min(q[k] for q in pts) + max(q[k] for q in pts)) / 2 for k in range(3)]
            for s in model.sections:
                if s.name == "steering_wheel":
                    s.offset = centre
            report["steering_wheel_pivot"] = [round(c, 4) for c in centre]
        sh = cb.SHModel([donor])
        new, rep = cb.build(model, ps, "lod" if kind == "main" else "lod0", sh, seed=seed + len(out),
                            keep=KEEP_SECTIONS, transparent=transparent)
        if sw:
            at = driver_controls_at(fc.build(new))
            if at is not None:
                old_axis = struct.unpack_from(">3f", new.header, at + 16 * (1 if kind == "main" else 13))
                towards = old_axis if any(old_axis) else (0.0, 0.3, 0.95)
                axis = wheel_axis(sw, towards)
                new.header = move_driver_controls(new.header, at, centre, axis)
                report["steering_wheel_axis"] = [round(c, 4) for c in axis]
        if kind == "main":
            lights = light_list(car, parts, dy, dz, year)
            new.header = fm4_carbin.set_lights(new.header, lights)
            report["lights"] = struct.unpack_from(">I", lights)[0]
        report.update(rep)
        report["parts"] = {k: sorted(v) for k, v in report.get("parts", {}).items()}
        out[name] = (new, report)
    return out


# ============================================================================= command line

def _fh1_donor(fh1_dir, update, donor):
    import fm4_cars
    fh1 = fm4_cars.FH1(fh1_dir, update)
    path = fm4_cars.find_ci(fh1.media, "cars", donor + ".zip")
    if not path:
        raise Fm1Error("no media/cars/%s.zip in %s" % (donor, fh1_dir))
    z = fm4_cars.ZIPS.get(path)
    files = {}
    for suffix in ("", "_lod0", "_cockpit"):
        want = ("%s%s.carbin" % (donor, suffix)).lower()
        info = next((i for i in z.infolist() if i.filename.lower() == want), None)
        if info is None:
            raise Fm1Error("no %s in %s" % (want, path))
        files[info.filename] = fc.parse(fm4_cars.Member(z, path, info).read())
    db = fh1.db()
    try:
        wheels = donor_wheels(db, donor)
    finally:
        db.close()
    return files, wheels, [i.filename for i in z.infolist() if i.filename.lower().endswith(".xds")]


def car_files(cab, car, fh1_dir, update, donor, year=1995):
    """({file name: bytes}, {model: report}): the donor's three models and their stripped_
    copies with the FM1 car in them, and its textures made from FM1's."""
    files, wheels, xds = _fh1_donor(fh1_dir, update, donor)
    out, reports = {}, {}
    for name, (new, report) in build_models(car, files, wheels, year).items():
        out[name] = fc.build(new)
        out["stripped_" + name.lower()] = fc.build(new, stripped=True)
        reports[name] = report
    out.update(textures(cab, cab_name(car, cab), xds))
    return out, reports


def cab_name(car, cab):
    """The car's folder name in its archive (cars\\<name>\\car.carbin)."""
    return next(k.split("\\")[1] for k in cab.files if k.startswith("cars\\") and k.endswith("\\car.carbin"))


MOD_TOML = 'name = "%s"\ndescription = "%s"\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("parts", help="an FM1 car's parts, LODs and materials")
    p.add_argument("fm1")
    p.add_argument("car")
    m = sub.add_parser("car", help="an FM1 car in an FH1 car's model and texture files")
    m.add_argument("fm1")
    m.add_argument("fh1")
    m.add_argument("car")
    m.add_argument("donor")
    m.add_argument("out")
    m.add_argument("--update", help="FH1's title update folder (with media.zip)")
    m.add_argument("--year", type=int, default=1995, help="the car's year (its headlight sprites)")
    m.add_argument("--mod", action="store_true",
                   help="lay the files out as a mod folder: OUT/media/cars/DONOR/..., OUT/mod.toml")
    a = ap.parse_args(argv)
    cab, car = load_car(a.fm1, a.car)
    if a.cmd == "parts":
        stock = {p.name for p in stock_parts(car)}
        for part in car.parts:
            lods = sorted({s[0] for s in part.subparts})
            mats = sorted({s[1] for s in part.subparts})
            print("%-24s %5d verts  LODs %-12s materials %-24s %s" % (
                part.name, len(part.verts), lods, mats, "stock" if part.name in stock else ""))
        print("wheel centres:", [tuple(round(c, 3) for c in w) for w in car.wheels])
        print("flares:", [(tuple(round(c, 3) for c in f[0]), f[2]) for f in car.flares])
        return 0
    files, reports = car_files(cab, car, a.fh1, a.update, a.donor, a.year)
    folder = os.path.join(a.out, "media", "cars", a.donor) if a.mod else a.out
    os.makedirs(folder, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(folder, name), "wb") as f:
            f.write(data)
    if a.mod:
        with open(os.path.join(a.out, "mod.toml"), "w", encoding="utf-8") as f:
            f.write(MOD_TOML % ("FM1 %s test" % a.car, "FM1's %s in place of %s's model (#930)" % (a.car, a.donor)))
    for name, report in reports.items():
        print(name)
        for k, v in report.items():
            print("  %s: %s" % (k, v))
    print("%d files in %s" % (len(files), folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
