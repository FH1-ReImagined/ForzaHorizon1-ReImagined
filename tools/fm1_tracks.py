#!/usr/bin/env python3
"""Forza Motorsport (2005, original Xbox) circuits in Forza Horizon 1, through the FM4 circuit import (#930).

An FM1 circuit is laid out as an FM4 one: a folder shaped like FM4's Disc 1 (Media/tracks/<Track>, Media/AI/Tracks,
Media/db, Media/physics) that the launcher's FM4 circuit import (launcher/forza_launcher/
fm4tracks.py: tools/fm4_track_mod.py, then tools/fm4_pvs.py) converts as it converts FM4's own circuits. Only the
visuals are FM1's: FM1 built these circuits in the coordinates FM4 kept (collision vertices shared: Tsukuba 88 %,
Road America 95 %, Silverstone 81 %), so FM4's collision, AI lines, track settings, layouts and names are used as they
are. Circuits FM4 has no collision of (OWN_CIRCUITS: Tokyo, New York, Rio, Alpine Ring, Blue Mountains, Pacific
Shipyards, Fujimi's hill climb, FM1's Road Atlanta and test track) take
FM1's own collision, road splines, grid and sectors instead, on Tsukuba's FM4 data, under names of their own. Both
discs are the player's own; nothing made here may be shared.

Usage:
  tools/fm1_tracks.py list FM1_DISC FM4_DISC1
  tools/fm1_tracks.py stage FM1_DISC FM4_DISC1 CIRCUIT OUT_DIR [--view M] [--clip M]

  FM1_DISC   Forza Motorsport's (original Xbox) disc folder, with media/Tracks
  FM4_DISC1  Forza Motorsport 4's Disc 1 folder, with Media/tracks, Media/AI/Tracks, Media/db
  CIRCUIT    FM4's folder name: Tsukuba, RoadAmerica, Silverstone, MapleValley, LagunaSeca, SunsetInfield
             (CIRCUITS), or an FM1-only circuit's own name: FM1Fujimi, FM1Tokyo, FM1NewYork,
             FM1PacificShipyards, FM1Rio, FM1AlpineRing, FM1BlueMountains, FM1RoadAtlanta, FM1TestTrack
             (OWN_CIRCUITS)
  OUT_DIR    the FM4-disc-shaped folder to write (its Media/tracks/<CIRCUIT> is replaced)

FM1 track = media/Tracks/<track>/QuickPreview1.xb2, an FM1 resource archive (Archive): mesh records
(model_quickpreview), one vertex buffer each (vertices, then u16 triangle-strip indices) and NV2A DXT textures.
Each FM1 mesh becomes a single-mesh FM4-format model on diff_1 (opaque) or diff_opac_2_2sd (alpha-tested). The
game draws FM4 circuits from their models' own command buffers, so every model takes those of an FM4 model on the
same shader (TEMPLATES, with_template_cbs), patched where they depend on the mesh.

What was found doing it (fh1track/HANDOFF.md has the trail):
- FM1 vertices: position float3, normal u32 at +12 (NV2A 11:11:10), uv s16 pairs from +16, every uv set decoded as
  (s16 / 32768 + 1) * texcoordscale / 2 (the archive's texcoordscale resource). The lightmap uv is the last pair
  (+20 for 24-byte vertices). FM4's world-space meshes are FM1's world mirrored: z is negated, which also gives FM1's
  triangles FM4's winding.
- A mesh record's u16s after its 16-byte header: kind 6 (meshes) vb, 6, a value that is NOT a resource id, the
  textures (lightmap last), a pad; kind 5 (cards) vb, 5, u32s (2 crowd cards / 5 trees), textures, pad.
- Every texture keeps its full mip chain, packed tail included, as FM4's .bix files do: non-square textures cut short
  drew black patches from far away. Textures with a side of 16 or less are scaled up to 32 with every level made
  from the top one; past 8:1 the long side is scaled less (fm1_tex.upscale_small_levels).
- Lightmap records take flags 12 (8 alone = an atlas tile drawn from the static pack) and a small_index past FM4's
  static pack; FM4's <Track>.stx.bin stays (the game waits forever without one). Every shader the PVS lists must be
  in bin.zip, used or not (a missing one: endless "Please wait").
- Meshes without a lightmap (trees, cards, signs) that share a texture are joined per 100 m (merge_unlit): thousands
  of small model files (Road America 9026) made a zone ask for more than the game streams.
- Blend meshes (shoulders, terrain edges: colour A, colour B, a blends__ / alpha__ mask, the lightmap) are split by
  their mask (split_blend): white = A, checked by area (Tsukuba's rumble strips cover 5-11 % of their verges).
- Instanced trees (44-byte vertices) are placed from their records (Fm1Track.tree) as crossed quads, lit by the mean
  of their own volume lightmap through a shared grey palette (so they still join per tile).
- Road America's asphalt textures are light (median grey ~160, Tsukuba's 73): a circuit's road textures are scaled
  to ROAD_LEVEL. Ground textures laid far larger on some meshes (Tsukuba's run-off dirt every 52-57 m, elsewhere
  10 m) are retiled (retile).
- FM1's own visibility (QuickPreview1.pvs) is not decoded: cells of CELL m see what is within --view m.
Not done: per-texel tree light, FM1's skybox (FH draws its own sky), power lines, Tsukuba's pond seen in game.
"""
import argparse
import math
import os
import re
import shutil
import statistics
import struct
import sys
import tempfile
import zipfile
import zlib
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import fm1_tex as tex  # noqa: E402

# FM4 folder -> (FM1 folder, how far each visibility cell sees in m): FM1 circuits whose ground FM4's collision
# fits (Laguna Seca and Sunset judged by its height under FM1's road, not by shared vertices). FM4 rebuilt the
# Nurburgring, Road Atlanta and the test track (FM1's own of the last two: OWN_CIRCUITS)
CIRCUITS = {"Tsukuba": ("tsukuba", 700.0), "RoadAmerica": ("RoadAmerica", 500.0),
            "Silverstone": ("Silverstone", 700.0), "MapleValley": ("FarmersLip", 700.0),
            "LagunaSeca": ("Lagunaseca", 700.0), "SunsetInfield": ("SunsetSpeedway", 700.0)}

TEX_BASE = 0x00200000          # file ids of FM1's textures
WHITE_LM = 0x00001000          # flat lightmap of meshes without one
TREE_PALETTE = 0x00001001      # instanced trees' grey palette lightmap
ATLAS_BASE = 0x00001200        # + page: lightmap atlases (fm1_tex.Atlases)
CARD_LM = 0x00001100          # + grey: flat lightmaps of cards lit by shadows__card_gray_NN
GREY_ID = 0x00000FFF           # stand-in base colour
UNLIT_LEVEL = 163              # flat light of meshes without a lightmap (FM1's lightmaps peak near it in sun)
TREE_LEVELS = 32
TREE_FLOAT, TREE_CANOPY = 5.0, 2.5     # settle_trees: a canopy this high over its ground is lowered to this
LM_FLAGS = 12
# The texture format word every FM4 lightmap has (Silverstone's 1,367: DXT1 with sign/gamma bits
# 0x7F00); fm1_tex writes 0x1A200152, which FM4's colour textures use too. FM1's lightmaps are as
# bright as FM4's (median 92 against 91, Silverstone) but under the plain word the circuits drew
# washed out, pale asphalt and grass beside FM4's own Silverstone
LM_FORMAT = 0x1A207F52
LM_DARK = 62                   # FM1's lightmap median (mean grey) under this: lifted onto FM4's (lightmap_curve)
UNUSED = 0xFFFFFFFE
SHADERS = {"diff": "diff_1", "opac": "diff_opac_2_2sd", "line": "rdline_blnd_spec_opac_3"}
# vertex layout per kind: stride, normal, colour, uv0, lightmap uv offsets
LAYOUT = {"diff": (32, 12, None, 16, 20), "opac": (36, 12, 16, 20, 24)}
# kind -> (FM4 track the command buffers come from, its model number): single-mesh strip models, Tsukuba 636
# (diff_1, 32-byte vertices), Suzuka 170 (diff_opac_2_2sd, 36: Tsukuba has none)
TEMPLATES = {"diff": ("Tsukuba", 636), "opac": ("Suzuka", 170)}
ROT_WORLD = (15360, 0, 0, 0, 15360, 0, 0, 0, 48128)      # half floats: diag(1, 1, -1), FM4's world-space models
LOD_NONE = (48128, 48128, 48128)                         # -1: always
MAX_TRIS = 15000
CUTOUT_MIN = 0.02              # share of alpha < 128 texels that makes a base texture alpha-tested
SKIP_NAMES = ("skybox", "occluder")
SUBSET = 0x40000               # FM4's most common instance subset
OPAC_BIT = 0x20000             # FM4's transparent bit in the instance's high half
CELL = 150.0
MAX_SEEN = 3400                # most models a visibility cell may see (Maple Valley's 3,406 load)
MAX_SEEN_TEX = 1500            # and textures (Maple Valley's 1,462 load; FM4's own cells reach 610)
# the game loads a circuit's models and textures through one list of 6,144 entries with no bound
# (CTrackPresentation::UpdatePVS's TCachedLoadBatcher<6144>; CachedLoadBatcher::AddRequest writes past it):
# Tokyo's 4,861 models and 2,518 textures faulted as it loaded, its 3,563 and 1,397 load
MAX_LOAD = 6144 - 400
MIN_VIEW = 250.0
CLIP = 600.0                   # meshes wholly this far outside the collision's bounds are left out
MERGE_TILE = 100.0
# larger while a circuit is over the load list (MAX_LOAD): FM1's 21 km Nürburgring takes 800 m
MERGE_TILES = (MERGE_TILE, 150.0, 200.0, 300.0, 400.0, 600.0, 800.0)
WATER_TINT = (125, 140, 150)   # what FM1's see-through water is drawn over
NORMAL_UP = 0x0007FC00
ROAD_TEX = re.compile(r"road__road_\d+[a-z]?$")
ROAD_LEVEL = 85
GROUND_TEX = re.compile(r"_terrain__terrain_(dirt|grass|gravel|sand)")


class Fm1TrackError(ValueError):
    pass


def find_ci(base, *parts):
    """base/parts..., each part matched without regard to case, or None"""
    p = base
    for part in parts:
        try:
            names = os.listdir(p)
        except OSError:
            return None
        hit = next((n for n in names if n.lower() == part.lower()), None)
        if hit is None:
            return None
        p = os.path.join(p, hit)
    return p


def fm1_tracks_dir(fm1_disc):
    """FM1's media/Tracks folder from the disc folder (or its media folder)"""
    return find_ci(fm1_disc, "media", "Tracks") or find_ci(fm1_disc, "Tracks")


def fm4_media(fm4_disc):
    return find_ci(fm4_disc, "Media") or (fm4_disc if find_ci(fm4_disc, "tracks") else None)


def available(fm1_disc, fm4_disc):
    """[FM4 folder] of the circuits both discs have"""
    t1, m4 = fm1_tracks_dir(fm1_disc), fm4_media(fm4_disc)
    if not t1 or not m4:
        return []
    return [c for c, (f1, _) in CIRCUITS.items()
            if find_ci(t1, f1, "QuickPreview1.xb2") and find_ci(m4, "tracks", c, "bin.zip")]


# ---- FM1's resource archive (.xb2: the whole archive as back-to-back zlib streams, files stored raw)

class Archive:
    """header: u32 magic 0xC0C0AAAA, version 15, count, flags (bit 0: files are zlib streams), data size x 2, names
    size x 2, ?, ?, descriptor size x 2, ?; at 0x206 (count - 9) x {u32 hash, offset, size, unpacked size, name
    offset, descriptor offset}; then u32, the names, the descriptors (Xbox D3D resource headers), the data. Resource
    ids are entry index + 9 (0-8: hidden header resources, 512-byte slots before the first file)"""

    def __init__(self, data):
        if data[:4] != b"\xaa\xaa\xc0\xc0":
            out, pos = [], 0
            while pos < len(data):
                o = zlib.decompressobj()
                out.append(o.decompress(data[pos:]) + o.flush())
                pos = len(data) - len(o.unused_data)
                while pos < len(data) and data[pos] == 0:
                    pos += 1
            data = b"".join(out)
        self.d = data
        self.hdr = struct.unpack_from("<16I", data, 0)
        if self.hdr[0] != 0xC0C0AAAA:
            raise Fm1TrackError("not an FM1 resource archive")
        n = self.hdr[2] - 9
        self.names_at = 0x206 + 24 * n + 4
        self.ents = []
        for i in range(n):
            h, off, size, usize, no, fo = struct.unpack_from("<6I", data, 0x206 + 24 * i)
            at = self.names_at + no
            self.ents.append({"off": off, "size": size, "usize": usize, "desc_off": fo,
                              "name": data[at:data.index(b"\x00", at)].decode("latin-1")})
        self.by = {e["name"]: e for e in self.ents}

    def data(self, e):
        b = self.d[e["off"]:e["off"] + e["size"]]
        if self.hdr[3] & 1 and e["size"] != e["usize"]:
            return zlib.decompress(b)
        return b

    def descriptor(self, e):
        at = self.names_at + self.hdr[6] + e["desc_off"]
        return self.d[at:at + 20]

    def res(self, i):
        return self.ents[i - 9] if 9 <= i < len(self.ents) + 9 else None

    def header_slot(self):
        """the hidden slot holding 'exporterversion' (5.0): texcoordscale is 512 bytes before it, then
        instscalescale, instrangetrans, instoffsettrans (512 bytes apart)"""
        first = min(e["off"] for e in self.ents)
        return next((s for s in range(first - 0x200, first - 0x1200, -0x200)
                     if struct.unpack_from("<f", self.d, s)[0] == 5.0 and not any(self.d[s + 4:s + 16])), None)

    def header_values(self):
        """((texcoordscale u, v), trees or None): trees = {"scale": instscalescale, "range": instrangetrans,
        "offset": instoffsettrans} (-FLT_MAX: the circuit has no instanced trees). Maple Valley's archive (an
        older exporter) has exporterversion and texcoordscale as named files instead, and its hidden slots start
        with instscalescale right before the first file"""
        slot = self.header_slot()
        scale = struct.unpack_from("<2f", self.d, slot - 0x200) if slot else (1.0, 1.0)
        if not slot and "texcoordscale" in self.by:
            scale = struct.unpack_from("<2f", self.data(self.by["texcoordscale"]), 0)
            slot = min(e["off"] for e in self.ents) + 0x200
        if not slot:
            return scale, None
        sc, rg, of = (struct.unpack_from("<3f", self.d, slot - k) for k in (0x400, 0x600, 0x800))
        if not all(abs(x) < 1e6 for x in sc + rg + of):
            return scale, None
        return scale, {"scale": sc[0], "range": rg, "offset": of}


def strip_to_tris(idx):
    tris = []
    for i in range(len(idx) - 2):
        a, b, c = idx[i], idx[i + 1], idx[i + 2]
        if a == b or b == c or a == c:
            continue
        tris += (a, b, c) if i % 2 == 0 else (b, a, c)
    return tris


def nv2a_normal_to_fm4(n):
    """NV2A NORMPACKED3 (x 11, y 11, z 10 bits, signed) -> FM4 / Xenos 10:10:10 signed (x low), z negated (mirror)"""
    def s(v, bits):
        return v - (1 << bits) if v & (1 << (bits - 1)) else v
    x, y, z = s(n & 0x7FF, 11) / 1023.0, s((n >> 11) & 0x7FF, 11) / 1023.0, -s(n >> 22, 10) / 511.0
    out = 0
    for i, c in enumerate((x, y, z)):
        out |= (int(round(max(-1.0, min(1.0, c)) * 511)) & 0x3FF) << (10 * i)
    return out


def split_blend(pos, normals, uva, uvb, uvm, uvl, tris, mask, depth=3, texels=8.0):
    """FM1 blend meshes (two colour layers mixed by a mask) for a one-texture shader: triangles halved at their edge
    midpoints (every attribute is linear across a triangle, so this is exact) until about `texels` mask texels across
    (at most `depth` times), then each piece goes to layer A where the mask is white, else B.
    mask: (width, height, grey bytes). -> two (pos, normals, uv0, uvl, tris), for A and B"""
    mw, mh, mpx = mask

    def sample(u, v):
        return mpx[(int(v * mh) % mh) * mw + int(u * mw) % mw]

    out = [([], [], [], [], []), ([], [], [], [], [])]
    cache = [{}, {}]

    def vert(side, key, p, n, uv, ul):
        o, c = out[side], cache[side]
        if key not in c:
            c[key] = len(o[0])
            o[0].append(list(p))
            o[1].append(n)
            o[2].append(list(uv))
            o[3].append(list(ul))
        return c[key]

    def emit(tri, d):
        # tri: three (pos, normal, uva, uvb, uvm, uvl, key)
        ms = [t[4] for t in tri]
        span = max(max(abs(ms[i][0] - ms[j][0]) * mw, abs(ms[i][1] - ms[j][1]) * mh)
                   for i, j in ((0, 1), (1, 2), (0, 2)))
        if span > texels and d < depth:
            mids = []
            for i, j in ((0, 1), (1, 2), (2, 0)):
                a, b = tri[i], tri[j]
                half = [tuple((x + y) / 2 for x, y in zip(a[k], b[k])) for k in (0, 2, 3, 4, 5)]
                # a midpoint's key is its edge (either direction), so neighbours share the vertex
                mids.append((half[0], a[1], *half[1:], frozenset((a[6], b[6]))))
            m01, m12, m20 = mids
            for t in ((tri[0], m01, m20), (m01, tri[1], m12), (m20, m12, tri[2]), (m01, m12, m20)):
                emit(t, d + 1)
            return
        cu = sum(m[0] for m in ms) / 3
        cv = sum(m[1] for m in ms) / 3
        w = (sample(cu, cv) * 2 + sum(sample(*m) for m in ms)) / 5
        side = 0 if w >= 128 else 1
        out[side][4].extend(vert(side, t[6], t[0], t[1], t[2] if side == 0 else t[3], t[5]) for t in tri)

    def node(i):
        return (tuple(pos[i]), normals[i], tuple(uva[i]), tuple(uvb[i]), tuple(uvm[i]), tuple(uvl[i]), i)

    for k in range(0, len(tris) - 2, 3):
        emit(tuple(node(i) for i in tris[k:k + 3]), 0)
    return out


def _quat_matrix(x, y, z, w):
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


class Fm1Track:
    def __init__(self, path):
        with open(path, "rb") as f:
            self.a = a = Archive(f.read())
        (self.su, self.sv), self.inst = a.header_values()
        if "exporterversion" in a.by:
            # the older exporter's instanced trees (Maple Valley's 1,302) are placed as on the other circuits but
            # drew as leaf planes floating over the road (the same lap driven with and without them); its meshed
            # trees are plenty, so these are left out (Fujimi's floated too: settle_trees lowers them)
            self.inst = None
        need = ("model_quickpreview", "meshnametable_track")
        if any(n not in a.by for n in need):
            raise Fm1TrackError("the archive has no track meshes")
        md, mt = a.data(a.by["model_quickpreview"]), a.data(a.by["meshnametable_track"])
        n = struct.unpack_from("<I", md, 0)[0]
        offs = list(struct.unpack_from(f"<{n}I", md, 4)) + [len(md)]
        noffs = struct.unpack_from(f"<{n}I", mt, 4)
        self.mesh_names = [mt[o:mt.index(b"\x00", o)].decode("latin-1") for o in noffs]
        self.records = [md[offs[m]:offs[m + 1]] for m in range(n)]
        self._cache = {}

    def _memo(self, kind, e, fn):
        key = (kind, e["name"])
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def tree(self, r, buf, icount, vbytes):
        """an instanced tree (44-byte vertex buffer shared by its copies; record: u16 vb, u16 5, 5 u32, u16 texture,
        u16 lightmap) placed in the world: (pos, uv0, tris). Position = u16 / 65535 * instrangetrans -
        instoffsettrans from h5, h6, h7 of the record's u16s from byte 20; rotation = quaternion x, y, z as s16 /
        32767 (h2, h3, h4; w makes it unit), fitted on Road America's collision (median 0.4 m off the ground).
        Vertices: card position float3 (x instscalescale), colour, uv s16 x 2 ((s + 32768) / 1024), crown centre,
        scale, corner offset float2 in m. FM1 turns the cards to the camera; here each is two quads at right angles"""
        inst = self.inst
        h = struct.unpack_from("<10H", r, 20)
        t = [inst["range"][a] * h[f] / 65535 - inst["offset"][a] for a, f in enumerate((5, 6, 7))]
        q = [(x - 65536 if x >= 32768 else x) / 32767 for x in (h[2], h[3], h[4])]
        rot = _quat_matrix(*q, max(0.0, 1 - sum(x * x for x in q)) ** 0.5)
        idx = list(struct.unpack_from(f"<{icount}H", buf, vbytes))
        nv = max(idx) + 1
        local, uv0 = [None] * (2 * nv), [None] * (2 * nv)
        for v in range(nv):
            px, py, pz, _, u, w, _, _, _, _, ox, oy = struct.unpack_from("<3fIhh3f3f", buf, v * 44)
            c = (px * inst["scale"], py * inst["scale"], pz * inst["scale"])
            local[v] = (c[0] + ox, c[1] + oy, c[2])
            local[nv + v] = (c[0], c[1] + oy, c[2] + ox)
            uv0[v] = [(u + 32768) / 1024, (w + 32768) / 1024]
            uv0[nv + v] = list(uv0[v])
        pos = [[sum(rot[i][j] * p[j] for j in range(3)) + t[i] for i in range(3)] for p in local]
        tris = []
        for i in range(0, len(idx) - 2, 3):
            tris += idx[i:i + 3]
        tris += [i + nv for i in tris]
        return pos, uv0, tris

    @staticmethod
    def texture_ids(ids):
        """the texture resource ids of a mesh record's u16 list (after its 16-byte header)"""
        if len(ids) > 1 and ids[1] == 6:
            return ids[3:]
        if len(ids) > 1 and ids[1] == 5:
            return ids[6:] if len(ids) == 8 else ids[12:] if len(ids) >= 14 else ids[2:]
        return ids[1:]

    def meshes(self, skipped):
        """-> one dict per drawable mesh: name, m, kind, pos, normals, uv0, uvl (or None), tris (flat), base / light
        texture entries and what decides how it is lit"""
        a = self.a
        seen = Counter()
        for m, r in enumerate(self.records):
            name = self.mesh_names[m]
            k = seen[name]
            seen[name] += 1
            flags, _, _, icount, vbytes = struct.unpack_from("<IHHII", r, 0)
            ids = list(struct.unpack_from(f"<{(len(r) - 16) // 2}H", r, 16))
            vb = a.by.get(f"vbuff_quickpreview_{name}_vb{k}") or a.res(ids[0])
            if not vb or not vb["name"].startswith("vbuff_"):
                skipped["no vertex buffer"] += 1
                continue
            buf = a.data(vb)
            idx = list(struct.unpack_from(f"<{icount}H", buf, vbytes))
            nv = max(idx) + 1 if idx else 0
            st = vbytes // nv if nv else 0
            if not nv or vbytes % nv:
                skipped["odd vertex buffer"] += 1
                continue
            if st < 20 or flags & 0x200000 or name.startswith(SKIP_NAMES):
                skipped["sky / occluder / lines"] += 1
                continue
            if st == 44:
                if not self.inst or len(r) != 44:
                    skipped["instanced trees"] += 1
                    continue
                pos, uv0, tris = self.tree(r, buf, icount, vbytes)
                for p in pos:
                    p[2] = -p[2]                    # FM4's mirror, as below
                t = a.res(struct.unpack_from("<H", r, 40)[0])
                level = self.tree_level(a.res(struct.unpack_from("<H", r, 42)[0]))
                uvl = None if level is None else [[(level + 0.5) / TREE_LEVELS, 0.5] for _ in pos]
                yield {"name": name, "m": m, "kind": "opac", "pos": pos, "normals": [NORMAL_UP] * len(pos),
                       "uv0": uv0, "uvl": uvl, "tris": tris, "light": None, "palette": uvl is not None,
                       "base": t if t and t["name"].startswith("tex_") else None}
                continue
            texs = [t for t in (a.res(i) for i in self.texture_ids(ids)) if t and t["name"].startswith("tex_")]

            def is_lm(t):       # lightmaps: names ending lightingmap / lightmap (not streetlight, spotlights)
                return t["name"].endswith(("lightingmap", "lightmap"))

            def is_mask(t):     # blend masks: Tsukuba's blends__, Road America's / Silverstone's alpha__
                return "_blends__" in t["name"] or "_alpha__" in t["name"]
            # colour: not a lightmap, a mask, nor shadows__card_gray_NN (flat light values: the "grey hills")
            colour = [t for t in texs if not is_lm(t) and not is_mask(t)
                      and "shadows__" not in t["name"]] or [t for t in texs if not is_lm(t)]
            light = [t for t in texs if is_lm(t)]
            base = colour[0] if colour else None
            cards = st == 40 and flags & 1
            pos, uv0, normals = [], [], []
            for v in range(nv):
                o = v * st
                x, y, z = struct.unpack_from("<3f", buf, o)
                if cards:
                    ox, oy = struct.unpack_from("<2f", buf, o + 32)
                    x, y = x + ox, y + oy
                    u, w = struct.unpack_from("<2h", buf, o + 12)
                    normals.append(NORMAL_UP)
                else:
                    u, w = struct.unpack_from("<2h", buf, o + 16)
                    normals.append(nv2a_normal_to_fm4(struct.unpack_from("<I", buf, o + 12)[0]))
                # FM4's world-space meshes are FM1's world mirrored: z negated (also FM4's winding)
                pos.append([x, y, -z])
                uv0.append([(u / 32768 + 1) * self.su / 2, (w / 32768 + 1) * self.sv / 2])
            uvl = None
            if light and st in (24, 28, 32):
                lo = 20 if st == 24 else st - 4
                uvl = [[(p - 32768) / 32768 * self.su / 2, (q - 32768) / 32768 * self.sv / 2]
                       for p, q in (struct.unpack_from("<2H", buf, v * st + lo) for v in range(nv))]
            if cards:
                tris = []
                for i in range(0, len(idx) - 2, 3):
                    tris += idx[i:i + 3]
            else:
                tris = strip_to_tris(idx)
            if not tris:
                skipped["no triangles"] += 1
                continue
            # alpha-tested: record flags bit 0, or a base texture with cut-outs (terrain edges, road lines, tree
            # lines); not road pieces (flags 0x1000), whose alpha is a mask, not holes
            opac = flags & 1 or (not flags & 0x1000 and base is not None and self.cutout(base) > CUTOUT_MIN)
            tint = None
            if base is not None and "water" in base["name"]:
                opac, tint = False, WATER_TINT      # see-through water, drawn solid over a sky-like grey-blue
            blend = next((t for t in texs if is_mask(t)), None)
            if blend is not None and len(colour) > 1 and st == 32 and uvl is not None:
                mask = self.mask(blend)
                if mask is not None:
                    uvb, uvm = [], []
                    for v in range(nv):
                        p, q, c, d = struct.unpack_from("<2h2H", buf, v * st + 20)
                        uvb.append(((p / 32768 + 1) * self.su / 2, (q / 32768 + 1) * self.sv / 2))
                        uvm.append(((c - 32768) / 32768 * self.su / 2, (d - 32768) / 32768 * self.sv / 2))
                    for layer, (p, n, uv, ul, t) in zip(colour[:2], split_blend(pos, normals, uv0, uvb, uvm, uvl,
                                                                                tris, mask)):
                        if t:
                            yield {"name": name, "m": m, "kind": "opac" if flags & 1 else "diff", "pos": p,
                                   "normals": n, "uv0": uv, "uvl": ul, "tris": t, "base": layer, "light": light[0],
                                   "blend": True}
                    continue
            yield {"name": name, "m": m, "kind": "opac" if opac else "diff", "pos": pos, "normals": normals,
                   "uv0": uv0, "uvl": uvl, "tris": tris, "base": base, "light": light[0] if uvl is not None else None,
                   "tint": tint, "flat_light": next((t for t in texs if "card_gray" in t["name"]), None),
                   "road": bool(flags & 0x1000 and base is not None and ROAD_TEX.search(base["name"]))}

    def tree_level(self, e):
        """palette level of an instanced tree's volume lightmap (its texel mean; 129 = the unlit level), or None"""
        if e is None or "lightmap" not in e["name"]:
            return None

        def level():
            mean = tex.volume_mean_grey(self.a.data(e))
            if mean is None:
                return None
            return min(TREE_LEVELS - 1, int(min(255.0, mean * UNLIT_LEVEL / 129) * TREE_LEVELS / 256))
        return self._memo("tree", e, level)

    def mask(self, e):
        """(width, height, grey bytes) of a blend mask's top level, or None"""
        def grey():
            got = tex.top_texels(self.a.descriptor(e), self.a.data(e))
            return None if got is None else (got[0], got[1], bytes(tex.grey(p) for p in got[2]))
        return self._memo("mask", e, grey)

    def mean_rgb(self, e):
        def mean():
            got = tex.top_texels(self.a.descriptor(e), self.a.data(e))
            if got is None:
                return (160, 160, 160)
            px = got[2]
            return tuple(sum(p[i] for p in px) // len(px) for i in range(3))
        return self._memo("mean", e, mean)

    def cutout(self, e):
        return self._memo("cut", e, lambda: tex.cutout_share(self.a.descriptor(e), self.a.data(e)))

    def bix(self, e, scale=None, under=None, cutout=False, curve=None):
        """(bix, bix_B) of an FM1 texture entry, or None when it is not DXT1 / DXT5. scale: its colour multiplied
        (Road America's light asphalt). under: a colour it is drawn over by its alpha (water). cutout: drawn
        alpha-tested: its smaller levels keep the top level's alpha coverage (fm1_tex.keep_alpha_coverage) and every
        alpha is made solid or clear (fm1_tex.hard_alpha). curve: a grey mapping laid on its colour (lightmaps)"""
        got = tex.nv2a_levels(self.a.descriptor(e), self.a.data(e))
        if not got or not got[3]:
            return None
        fmt, w, h, levels = got
        if curve is not None:
            levels = tex.map_grey(levels, fmt, curve)
        if cutout and under is None:
            levels = tex.hard_alpha(tex.keep_alpha_coverage(levels, fmt), fmt)
        if scale is not None:
            levels = tex.scale_colour(levels, fmt, scale)
        if under is not None:
            levels = tex.over_colour(levels, fmt, under)
        if min(w, h) <= 16:     # Xenos keeps such a top level in the packed tail: scaled up, full chain
            w, h, levels = tex.upscale_small_levels(fmt, w, h, levels)
        if max(w, h) > 8 * min(w, h):
            # past 8:1 (Tokyo's 64 x 1024 kerbs, 32 x 512 lines): the short side widened to 8:1, a shape whose
            # mip layout is FM4's own; Tokyo faulted loading its first zone's textures with them
            nw, nh = (w, w // 8) if w > h else (h // 8, h)
            levels = tex.resize_levels(fmt, w, h, levels[0], nw, nh)
            w, h = nw, nh
        return tex.to_bix(levels, w, h, fmt)


def lightmap_curve(src, meshes, fm4z, p0, log=print):
    """the grey mapping that lays FM1's lightmap brightness onto FM4's lightmaps of the same circuit (the deciles of
    each lightmap's mean grey, up to the median; from there the curve runs back to white unchanged), or None when
    FM4's cannot be read. FM1 lit some circuits far darker than FM4 under the same shaders: Sunset's infield (FM1's
    evening sun, deciles 21-41 against FM4's 78-112) drew black, Maple Valley's median is 68 against 125;
    Silverstone's already match (91 / 92) and keep theirs. Above the median the deciles are not followed: FM1's
    sunlit parts are as bright as FM4's or brighter (Sunset's oval straight drew white already)"""
    fm1 = []
    seen = set()
    for m in meshes:
        e = m.get("light")
        if e is not None and e["name"] not in seen:
            seen.add(e["name"])
            fm1.append(tex.grey(src.mean_rgb(e)))
    fm4 = []
    for t in p0["textures"]:
        name = f"_0x{t[0]:08x}.bix"
        if t[6] & 8 and name in fm4z.by:
            g = tex.bix_mean_grey(fm4z.read(name)[1])
            if g is not None:
                fm4.append(g)
    if len(fm1) < 20 or len(fm4) < 20:
        return None
    q1, q4 = statistics.quantiles(fm1, n=10)[:5], statistics.quantiles(fm4, n=10)[:5]
    if q1[-1] >= LM_DARK:
        # Maple Valley (71 against FM4's 125) and Laguna Seca (110 / 170) drew paler than FM4's own with it, and
        # looked right without, as Road America (82 / 198) did; only FM1's dark ones take it (Sunset 55, Fujimi's
        # morning 54, the night circuits about 30)
        log(f"lightmaps: FM1's median {q1[-1]:.0f} against FM4's {q4[-1]:.0f}, kept")
        return None
    log(f"lightmaps: FM1's deciles up to the median {[round(q) for q in q1]} laid onto FM4's {[round(q) for q in q4]}")
    return tex.grey_curve(q1, q4)


def settle_trees(meshes, stats, cell=8.0):
    """Instanced trees whose canopy's lowest point is more than TREE_FLOAT m above the circuit's own lit meshes under
    its middle are lowered to TREE_CANOPY m above them. Laguna Seca's and Road America's canopies start 2.3 / 2.7 m
    up (the trunks are meshes of their own); Fujimi's at a median 9.7 m, up to 20 (they drew floating over the
    road)"""
    trees = [m for m in meshes if m.get("palette") and m["kind"] == "opac"]
    if not trees:
        return
    grid = {}
    for m in meshes:
        if m.get("light") is None or m.get("palette"):
            continue
        p, tri = m["pos"], m["tris"]
        for i in range(0, len(tri) - 2, 3):
            a, b, c = p[tri[i]], p[tri[i + 1]], p[tri[i + 2]]
            x0, x1 = min(a[0], b[0], c[0]), max(a[0], b[0], c[0])
            z0, z1 = min(a[2], b[2], c[2]), max(a[2], b[2], c[2])
            if x1 - x0 > 400 or z1 - z0 > 400:
                continue
            for gx in range(int(x0 // cell), int(x1 // cell) + 1):
                for gz in range(int(z0 // cell), int(z1 // cell) + 1):
                    grid.setdefault((gx, gz), []).append((a, b, c))

    def ground(x, z, below):
        best = None
        for a, b, c in grid.get((int(x // cell), int(z // cell)), ()):
            d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
            if abs(d) < 1e-9:
                continue
            l1 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / d
            l2 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / d
            l3 = 1 - l1 - l2
            if min(l1, l2, l3) >= -1e-4:
                h = l1 * a[1] + l2 * b[1] + l3 * c[1]
                if h <= below and (best is None or h > best):
                    best = h
        return best
    for m in trees:
        xs, ys, zs = ([p[i] for p in m["pos"]] for i in range(3))
        h = ground((min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2, max(ys))
        if h is not None and min(ys) - h > TREE_FLOAT:
            dy = min(ys) - h - TREE_CANOPY
            for p in m["pos"]:
                p[1] -= dy
            stats["instanced tree lowered to its ground"] += 1


def load_need(meshes):
    """what the game loads for these meshes (fm1_tracks.MAX_LOAD): a model each, their colour textures and their own
    lightmaps (atlas pages counted apart)"""
    return len(meshes) + len({m["base"]["name"] for m in meshes if m["base"]}) + len(
        {m["light"]["name"] for m in meshes if m["light"] is not None})


def merge_unlit(meshes, tile=MERGE_TILE):
    """Meshes without a lightmap of their own (none, or one in a lightmap atlas) that share a kind, base texture and
    light are joined per `tile` m square (each model file carries ~5 KB of command buffers; thousands made a zone ask
    for more than the game streams, and the game loads a circuit's models and textures through one list of 6,144
    entries: Tokyo's 4,861 models and 2,518 textures faulted as it loaded, in CachedLoadBatcher::AddRequest)"""
    out, groups = [], {}
    for m in meshes:
        if m["light"] is not None or m.get("tint"):
            out.append(m)
            continue
        n = len(m["pos"])
        cx, cz = sum(p[0] for p in m["pos"]) / n, sum(p[2] for p in m["pos"]) / n
        key = (m["kind"], m["base"]["name"] if m["base"] else None, (m.get("flat_light") or {}).get("name"),
               m.get("palette", False), m.get("atlas"), m.get("road", False), int(cx // tile), int(cz // tile))
        groups.setdefault(key, []).append(m)
    for ms in groups.values():
        if len(ms) == 1:
            out.append(ms[0])
            continue
        tris, base = [], 0
        for m in ms:
            tris += [t + base for t in m["tris"]]
            base += len(m["pos"])
        out.append(dict(ms[0], pos=[p for m in ms for p in m["pos"]], normals=[n for m in ms for n in m["normals"]],
                        uv0=[u for m in ms for u in m["uv0"]], tris=tris,
                        uvl=[u for m in ms for u in m["uvl"]]
                        if ms[0].get("palette") or ms[0].get("atlas") is not None else None))
    return out


def retile(meshes, stats, limit=3.0, far=40.0):
    """Ground textures laid far larger on some meshes than elsewhere (Tsukuba's dirt_bare_01: ~10 m per repeat on
    three pieces, 52-57 m on ten, the blurred run-off): a mesh laid at more than `far` m per repeat takes the largest
    scale that at least two meshes share (within 25 %) and that is at most 1 / `limit` of its own, its uv multiplied
    by the nearest whole factor (uv wraps; neighbours on the same scale scale alike)"""
    def metres_per_repeat(m):
        p, uv = m["pos"], m["uv0"]
        r = []
        for i in range(1, len(p)):
            du = math.hypot(uv[i][0] - uv[i - 1][0], uv[i][1] - uv[i - 1][1])
            if du > 1e-6:
                r.append(math.dist(p[i], p[i - 1]) / du)
        return statistics.median(r) if r else None
    by = {}
    for m in meshes:
        if m["base"] is not None and GROUND_TEX.search(m["base"]["name"]) and not m.get("blend"):
            v = metres_per_repeat(m)
            if v:
                by.setdefault(m["base"]["name"], []).append((v, m))
    for name, ms in by.items():
        vals = sorted(v for v, _ in ms)
        shared = [v for i, v in enumerate(vals[:-1]) if vals[i + 1] <= v * 1.25]
        for v, m in ms:
            ref = max((r for r in shared if r <= v / limit), default=None)
            if v > far and ref:
                k = round(v / ref)
                m["uv0"] = [[u * k, w * k] for u, w in m["uv0"]]
                stats[f"ground uv x{k} ({name.split('__')[-1]})"] += 1


# ---- FM4 models (.rmb.bin, version 6)

def stripify(tris):
    """triangle list -> strips joined by the restart index 0xFFFF, same winding"""
    tl = [tuple(tris[k:k + 3]) for k in range(0, len(tris) - 2, 3)]
    tl = [t for t in tl if t[0] != t[1] and t[1] != t[2] and t[0] != t[2]]
    edge = {}
    for ti, (a, b, c) in enumerate(tl):
        for u, v, w in ((a, b, c), (b, c, a), (c, a, b)):
            edge.setdefault((u, v), []).append((ti, w))
    used = [False] * len(tl)

    def grow(s):
        taken = []
        while True:
            p, q = s[-2], s[-1]
            want = (p, q) if (len(s) - 2) % 2 == 0 else (q, p)
            nxt = next(((ti, w) for ti, w in edge.get(want, ()) if not used[ti] and ti not in taken), None)
            if nxt is None:
                return taken
            taken.append(nxt[0])
            s.append(nxt[1])

    out = []
    for ti, (a, b, c) in enumerate(tl):
        if used[ti]:
            continue
        used[ti] = True
        best = None
        for r in ((a, b, c), (b, c, a), (c, a, b)):
            s = list(r)
            taken = grow(s)
            if best is None or len(taken) > len(best[1]):
                best = (s, taken)
        for t in best[1]:
            used[t] = True
        if out:
            out.append(0xFFFF)
        out += best[0]
    return out


def _vec(fmt_one, items):
    b = struct.pack(">II", 1, len(items))
    for it in items:
        b += struct.pack(fmt_one, *it) if isinstance(it, (tuple, list)) else struct.pack(fmt_one, it)
    return b


def _strings(items):
    b = struct.pack(">II", 1, len(items))
    for s in items:
        e = s.encode("latin1")
        b += struct.pack(">I", len(e)) + e
    return b


def fm4_model(kind, pos, normals, uv0, uvl, idx, tex_slots, name, v4b):
    """a single piece / mesh / material FM4 model on SHADERS[kind], without its command buffers:
    -> (bytes, centre, half size, (index count, vertex bytes, uvOffsetScale))"""
    stride, n_off, c_off, t0_off, t2_off = LAYOUT[kind]
    # uv repeats: the base uv shifted by whole units next to 0 before both sets share one decode range
    fu, fv = math.floor(min(u for u, _ in uv0)), math.floor(min(v for _, v in uv0))
    uv0 = [(u - fu, v - fv) for u, v in uv0]
    both = uv0 + [tuple(x) for x in uvl]
    lo = [min(x[i] for x in both) for i in (0, 1)]
    sc = [max(max(x[i] for x in both) - lo[i], 1e-4) for i in (0, 1)]

    def raw(x):
        return [min(65535, max(0, int(round((x[i] - lo[i]) / sc[i] * 65535.0)))) for i in (0, 1)]
    nv = len(pos)
    vb = bytearray(stride * nv)
    for k in range(nv):
        o = stride * k
        struct.pack_into(">3f", vb, o, *pos[k])
        struct.pack_into(">I", vb, o + n_off, normals[k])
        if c_off is not None:
            struct.pack_into(">I", vb, o + c_off, 0xFFFFFFFF)
        r0, rl = raw(uv0[k]), raw(uvl[k])
        struct.pack_into(">I", vb, o + t0_off, r0[0] << 16 | r0[1])
        struct.pack_into(">I", vb, o + t2_off, rl[0] << 16 | rl[1])
    # vertices identical once packed are welded, then strips
    uniq, new_of = {}, []
    for k in range(nv):
        new_of.append(uniq.setdefault(bytes(vb[stride * k:stride * (k + 1)]), len(uniq)))
    vb = b"".join(uniq)
    nv = len(uniq)
    idx = stripify([new_of[i] for i in idx])
    plo = [min(p[i] for p in pos) for i in range(3)]
    phi = [max(p[i] for p in pos) for i in range(3)]
    ctr = [(a + b) / 2 for a, b in zip(plo, phi)]
    half = [(b - a) / 2 for a, b in zip(plo, phi)]
    c0 = (lo[0], lo[1], sc[0], sc[1])
    nm = name.encode("latin1")
    out = struct.pack(">I", 6) + struct.pack(">4f", *plo, 0.0) + struct.pack(">4f", *phi, 0.0)
    out += struct.pack(">16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    out += struct.pack(">I", 4) + struct.pack(">II", 4, 0) + struct.pack(">II", 1, 1)
    out += struct.pack(">I", 1) + struct.pack(">4f", *ctr, 0.0) + struct.pack(">4f", *(-h for h in half), 0.0)
    out += struct.pack(">4f", *half, 0.0)
    out += struct.pack(">I", 1) + struct.pack(">I", len(nm)) + nm
    out += struct.pack(">III", 2, nv, stride) + bytes(vb)
    out += struct.pack(">II", 1, 1) + struct.pack(">II", 1, 2) + struct.pack(">I", len(nm)) + nm
    out += struct.pack(">III", 0, 6, 0)
    out += struct.pack(">I", 1) + struct.pack(">4f", 0, 0, 0, 0) + struct.pack(">4f", 1, 1, 1, 1) + struct.pack(">4f", *c0)
    out += struct.pack(">III", 3, len(idx), 2) + struct.pack(">%dH" % len(idx), *idx)
    out += struct.pack(">I", 1)                                   # mesh field d
    out += struct.pack(">II", 1, 1) + struct.pack(">III", 1, 1, 1)
    out += struct.pack(">III", 3, 0, 0) + _vec(">4f", []) + _vec(">4f", v4b) + _vec(">I", list(tex_slots))
    out += _strings(["shaders\\track\\%s.fx" % SHADERS[kind]]) + _strings([])
    return out, ctr, half, (len(idx), nv * stride, c0)


class _R:
    def __init__(self, d):
        self.d, self.p = d, 0

    def u32(self):
        self.p += 4
        return struct.unpack_from(">I", self.d, self.p - 4)[0]

    def f4(self):
        self.p += 16
        return struct.unpack_from(">4f", self.d, self.p - 16)

    def skip_string(self):
        n = self.u32()
        self.p += n


def read_template(d):
    """an FM4 single-piece model: (index count, vertex bytes, primitive, uvOffsetScale, material v4b, offset of its
    command buffers). Layout as FH1's CTrackModel reader (fh1track/rmb.py)"""
    r = _R(d)
    r.p = 4 + 32 + 64 + 4
    if r.u32() != 4 or r.u32():
        raise Fm1TrackError("template model: ordered sub-model lists")
    r.u32()
    pieces = []
    for _ in range(r.u32()):
        r.u32()
        r.p += 48
        r.u32()
        r.skip_string()
        ver = r.u32()
        if ver != 2:
            raise Fm1TrackError(f"template model: vertex buffer v{ver}")
        count, stride = r.u32(), r.u32()
        r.p += count * stride
        r.u32()
        meshes = []
        for _ in range(r.u32()):
            r.u32()
            mv = r.u32()
            r.skip_string()
            _, b, _ = r.u32(), r.u32(), r.u32()
            r.u32()
            bb = (r.f4(), r.f4(), r.f4())
            iv = r.u32()
            if iv >= 4 and r.u32() & 1:
                raise Fm1TrackError("template model: shared index buffer")
            n, fmt = r.u32(), r.u32()
            r.p += n * (4 if (iv < 3 or fmt == 4) else 2)
            if mv >= 2:
                r.u32()
            meshes.append((n, b, bb[2]))
        pieces.append((count * stride, meshes))
    r.u32()
    v4b = None
    for _ in range(r.u32()):
        r.u32()
        r.u32()
        for _ in range(r.u32()):            # material: u32 version, a, b, vec4 list, vec4 list, u32 list
            r.p += 12
            lists = []
            for size in (16, 16, 4):
                r.u32()
                n = r.u32()
                lists.append([struct.unpack_from(">4f", d, r.p + 16 * i) for i in range(n)] if size == 16 else n)
                r.p += size * n
            if v4b is None:
                v4b = [tuple(v) for v in lists[1]]
    for _ in range(2):
        r.u32()
        for _ in range(r.u32()):
            r.skip_string()
    vbytes, meshes = pieces[0]
    n, prim, uv = meshes[0]
    return n, vbytes, prim, uv, v4b, r.p


def read_command_buffers(d, pos):
    """-> (group header, [{hdr, sizes, data} x 21]): the precompiled GPU command buffers after the materials"""
    g = struct.unpack_from(">6I", d, pos)
    p, cbs = pos + 24, []
    while len(cbs) < 64:
        if cbs and p + g[1] + g[2] + g[3] == len(d):
            break
        start = p
        _, na = struct.unpack_from(">II", d, p)
        p += 8 + na * 8
        nb = struct.unpack_from(">I", d, p)[0]
        p += 4 + nb * 8
        s = struct.unpack_from(">3I", d, p)
        p += 12
        cbs.append({"hdr": d[start:p], "sizes": s})
    else:
        raise Fm1TrackError("template model: command buffers do not add up")
    blobs = [d[p:p + g[1]], d[p + g[1]:p + g[1] + g[2]], d[p + g[1] + g[2]:p + g[1] + g[2] + g[3]]]
    off = [0, 0, 0]
    for cb in cbs:
        parts = []
        for k, a in enumerate((4, 32, 4)):
            n = cb["sizes"][k]
            parts.append(blobs[k][off[k]:off[k] + n])
            if n:
                off[k] += (n + a - 1) // a * a
        cb["data"] = parts
    return g, cbs


def write_command_buffers(g, cbs):
    out = bytearray(struct.pack(">6I", g[0], 0, 0, 0, g[4], g[5]))
    blobs = [bytearray(), bytearray(), bytearray()]
    for cb in cbs:
        out += cb["hdr"]
        for k, a in enumerate((4, 32, 4)):
            if cb["sizes"][k]:
                blobs[k] += b"\0" * ((len(blobs[k]) + a - 1) // a * a - len(blobs[k]))
                blobs[k] += cb["data"][k]
    struct.pack_into(">3I", out, 4, *(len(b) for b in blobs))
    return bytes(out + blobs[0] + blobs[1] + blobs[2])


class Template:
    """an FM4 model's command buffers for models of its shader: the VS uv block (uvOffsetScale), the vertex fetch
    size and the draw's index count are what depend on the mesh (diff of FM4 Tsukuba's diff_1 models 319 / 636)"""

    def __init__(self, data):
        self.n, self.vbytes, prim, uv, self.v4b, pos = read_template(data)
        if prim != 6:
            raise Fm1TrackError("template model: not a strip")
        self.uv = list(struct.unpack(">4I", struct.pack(">4f", *uv)))
        self.g, self.cbs = read_command_buffers(data, pos)

    def cbs_for(self, n, vbytes, uv):
        new_uv = list(struct.unpack(">4I", struct.pack(">4f", *uv)))
        cbs = []
        for slot, cb in enumerate(self.cbs):
            if not cb["sizes"][1]:
                cbs.append(cb)
                continue
            w = list(struct.unpack(">%dI" % (len(cb["data"][1]) // 4), cb["data"][1]))
            for old, new, what in ((self.uv, new_uv, "uv block"),
                                   ([0x10000000 | self.vbytes | 2], [0x10000000 | vbytes | 2], "vertex fetch size"),
                                   ([(self.n << 16) | 6], [(n << 16) | 6], "draw initiator"),
                                   ([0x40000000 | self.n], [0x40000000 | n], "draw size")):
                hits = [i for i in range(len(w) - len(old) + 1) if w[i:i + len(old)] == old]
                if len(hits) != 1:
                    raise Fm1TrackError(f"template slot {slot}: {what} found {len(hits)} times")
                w[hits[0]:hits[0] + len(old)] = new
            cbs.append(dict(cb, data=[cb["data"][0], struct.pack(">%dI" % len(w), *w), cb["data"][2]]))
        return write_command_buffers(self.g, cbs)


# ---- FM4's files

class Fm4Zip:
    """an FM4 bin.zip: its members by lower-case name, read decompressed (tools/fm4_track_mod.member_data)"""

    def __init__(self, path, tmp):
        import fm4_track_mod
        self.path, self.tmp, self._read = path, tmp, fm4_track_mod.member_data
        self.zf = zipfile.ZipFile(path)
        self.by = {i.filename.replace("\\", "/").lower(): i for i in self.zf.infolist()}

    def read(self, name):
        info = self.by[name.lower()]
        with open(self.path, "rb") as fh:
            return info.filename, self._read(self.path, self.zf, info, fh, self.tmp)


def write_pvs(p):
    """FM4 PVS (version 27) of the staged circuit (tools/fm4_pvs.read_fm4 reads it back)"""
    import fm4_pvs
    w = fm4_pvs.Writer()
    w.raw(b"FPVS")
    w.u32(27)
    w.u32(p["est_cpu_bytes"])
    w.u32(0)
    w.u16(p["ribbon"])
    w.u16(p["zone_size"])

    def zone(z):
        w.vec(z, w.u16)
        w.vec(z, w.u16)
        tx = p["zone_textures"](z)
        w.vec(tx, w.u32)
        w.vec([0] * len(tx), w.u8)
    w.vec(p["cells"], zone)

    def texture(t):
        w.u32(t["index"])
        w.u32(t["small_index"])
        for v in t["uv"]:
            w.f32(v)
        w.u32(t["flags"])
    w.vec(p["textures"], texture)
    w.vec(p["shaders"], w.string)

    def inst(mi):
        w.u16(mi)
        w.u16(4095)
        w.u32(p["models"][mi]["subset"])
        w.u16(0)
        w.u32(0xFFFFFFFF)
        w.raw(bytes(16))
        w.u16(0xFFFF)
        for v in LOD_NONE:
            w.u16(v)
        for v in (0.0, 0.0, 0.0):
            w.f32(v)
        for v in ROT_WORLD:
            w.u16(v)
    w.vec(list(range(len(p["models"]))), inst)

    def model(m):
        w.vec(m["textures"], w.u32)
        w.vec(m["shaders"], w.u32)
        c, h = m["centre"], m["half"]
        for v in (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, c[0], c[1], c[2], 1):
            w.f32(float(v))
        for v in (h[0], h[1], h[2], 0.0):
            w.f32(float(v))
    w.vec(p["models"], model)
    w.vec([], w.u16)        # sky instances (FH draws its own sky)
    w.vec([], w.u16)
    w.vec([], w.u16)
    w.vec([], inst)         # dynamic instances
    w.string(p["split_file"])
    w.u8(1)
    w.u8(0)
    w.vec([], w.string)
    w.vec([], lambda v: w.vec(v, w.u16))
    return bytes(w.b)


COL_COOKIE = 0xABCD1234
COL_ALIGN = 0x800


def fm1_collision(data):
    """FM1's QuickPreview1.col -> an FM4 .col (what tools/fm4_col.py converts), for circuits FM4 has no collision of.
    Both are the same stream world (FM4's Tsukuba is FM1's, converted): FM1's is little-endian, version 2, with
    16-byte vertices (float3, packed normal) and 28-byte triangles (u8 flags, u8 surface, u16 routes, 3 vertex
    pointers, float3 normal); FM4's is big-endian with 32-byte vertices (float3, 0, the normal word, 12 zero bytes)
    and 32-byte triangles (the same, then a zero word). The world header's cell size and the square parameters'
    last pair stay in FM1's byte order, as FM4's own files have them. Surfaces are FM4's ids already (FM1's
    surfacetypes.ini order)"""
    if len(data) < 0x54 or struct.unpack_from("<I", data, 0x30)[0] != COL_COOKIE:
        raise Fm1TrackError("not a Forza Motorsport (2005) collision file")
    g = struct.unpack_from("<2I8f", data, 0)
    grid_base = struct.pack(">2I4f", *g[:6]) + data[0x18:0x20] + struct.pack(">2f", *g[8:10])
    square_params = struct.pack(">4f", *struct.unpack_from("<4f", data, 0x34)) + data[0x44:0x4C]
    n, table = struct.unpack_from("<II", data, 0x4C)
    squares = [struct.unpack_from("<6I", data, table + 24 * i) for i in range(n)]
    blocks = []
    for _, aid, gid, off, size, _ in squares:
        b = data[off:off + size]
        nv, nt, pv, pt = struct.unpack_from("<4I", b, 0)
        gx, gz = struct.unpack_from("<2I", b, 0x10)
        pg, extra = struct.unpack_from("<2I", b, 0x38)
        vbase, tbase = 0x40, 0x40 + 32 * nv
        gbase = tbase + 32 * nt
        lists_at = gbase + 4 * gx * gz
        out = bytearray(lists_at)
        struct.pack_into(">4I", out, 0, nv, nt, vbase, tbase)
        struct.pack_into(">2I8f", out, 0x10, gx, gz, *struct.unpack_from("<8f", b, 0x18))
        struct.pack_into(">2I", out, 0x38, gbase, extra)
        for i in range(nv):
            x, y, z, word = struct.unpack_from("<3fI", b, pv + 16 * i)
            struct.pack_into(">3fII", out, vbase + 32 * i, x, y, z, 0, word)
        for i in range(nt):
            fl, sf, routes, a, bb, c, nx, ny, nz = struct.unpack_from("<BBH3I3f", b, pt + 28 * i)
            struct.pack_into(">BBH3I3fI", out, tbase + 32 * i, fl, sf, routes,
                             *(vbase + 32 * ((p - pv) // 16) for p in (a, bb, c)), nx, ny, nz, 0)
        for k in range(gx * gz):
            p = struct.unpack_from("<I", b, pg + 4 * k)[0]
            if not p:
                continue
            cnt = struct.unpack_from("<H", b, p)[0]
            struct.pack_into(">I", out, gbase + 4 * k, len(out))
            out += struct.pack(f">H{cnt}H", cnt, *struct.unpack_from(f"<{cnt}H", b, p + 2))
        out += bytes(-len(out) % COL_ALIGN)
        blocks.append((aid, gid, bytes(out)))
    head_len = 0x54 + 24 * n
    at = head_len + (-head_len % COL_ALIGN)
    table_b = bytearray()
    for aid, gid, blk in blocks:
        table_b += struct.pack(">6I", at, aid, gid, at, len(blk), 0)
        at += len(blk)
    head = bytearray(grid_base + struct.pack(">III", at, 2, COL_COOKIE) + square_params
                     + struct.pack(">II", n, 0x54) + table_b)
    head += bytes(-len(head) % COL_ALIGN)
    return bytes(head) + b"".join(blk for _, _, blk in blocks)


# surfaces (FM1's surfacetypes.ini order, FM4's ids) the lap line of an FM1-only circuit may take besides
# tools/fm4_line.py's asphalt, kerbs and pit lane, and what a metre on them costs: Pacific Shipyards' Brick (10)
OWN_ROAD = {10: 1.5, 21: 1.5}


def mega_track(fm1_disc, folder):
    """{"grid": [(x, y, z)] by grid slot, "splits": [share], "length": m, "name": FM1's name, "cams": xml text} of an
    FM1 circuit, from media/cabs/Mega.ca2 (tracks/<folder>/quickpreview.xml, tracksegcams.xml; forzadb/
    tracklist.xml), or None without the archive"""
    path = find_ci(fm1_disc, "media", "cabs", "Mega.ca2") or find_ci(fm1_disc, "cabs", "Mega.ca2")
    if not path:
        return None
    with open(path, "rb") as fh:
        cab = Archive(fh.read())        # the same archive as a track's .xb2
    by = {e["name"].replace("/", "\\").lower(): e for e in cab.ents}
    low = folder.lower()

    def text(name):
        e = by.get(name)
        return cab.data(e).decode("latin-1") if e else ""
    qp = text(f"tracks\\{low}\\quickpreview.xml")
    grid = sorted((int(m.group(1)), tuple(float(m.group(k)) for k in (2, 3, 4))) for m in re.finditer(
        r'<startpoint\s+pos="(\d+)"\s+x="([-\d.]+)"\s+y="([-\d.]+)"\s+z="([-\d.]+)"', qp))
    splits = [float(s) for s in re.findall(r'<split\s+point="([\d.]+)"', qp)]
    length, name = None, folder
    for m in re.finditer(r"<track\b(.*?)/>", text("forzadb\\tracklist.xml"), re.S):
        a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if a.get("name", "").lower() == low:
            length, name = float(a.get("length") or 0) or None, a.get("RosettaName") or folder
    return {"grid": [p for _, p in grid], "splits": splits, "length": length, "name": name,
            "cams": text(f"tracks\\{low}\\tracksegcams.xml")}


def road_centre(archive, step=4.0):
    """[(x, y, z)] along the middle of an FM1 circuit's road, from its roadinner / roadouter splines
    (model_quickpreview_splines: records "USER", index count, vertex bytes, vertex buffer id; the buffer float3
    points, then u16 line-list pairs), every `step` m, or None. They are what FM4 made its AI lines from (Tsukuba's
    first point is FM4's waypoint 0 within 0.2 m). A first point far from the next (Pacific Shipyards' 113 m,
    Fujimi's 326 m) is a stray and left out"""
    if "model_quickpreview_splines" not in archive.by or "meshnametable_splines" not in archive.by:
        return None
    md, nt = archive.data(archive.by["model_quickpreview_splines"]), archive.data(archive.by["meshnametable_splines"])
    n = struct.unpack_from("<I", md, 0)[0]
    offs = list(struct.unpack_from(f"<{n}I", md, 4)) + [len(md)]
    names = [nt[o:nt.index(b"\x00", o)].decode("latin-1") for o in struct.unpack_from(f"<{n}I", nt, 4)]
    edges = {}
    for k, name in enumerate(names):
        if name not in ("roadinner", "roadouter"):
            continue
        r = md[offs[k]:offs[k + 1]]
        if len(r) < 18 or r[4:8] != b"USER":
            return None
        cnt, vb, rid = struct.unpack_from("<IIH", r, 8)
        e = archive.res(rid)
        if not e:
            return None
        d = archive.data(e)
        idx = struct.unpack_from(f"<{cnt}H", d, vb)
        order = [idx[0]] + [idx[i + 1] for i in range(0, len(idx), 2)]
        edges[name] = [struct.unpack_from("<3f", d, 12 * i) for i in order]
    a, b = edges.get("roadinner"), edges.get("roadouter")
    if not a or not b or len(a) != len(b) or len(a) < 8:
        return None
    mid = [tuple((p[i] + q[i]) / 2 for i in range(3)) for p, q in zip(a, b)]
    while len(mid) > 8 and math.dist(mid[0], mid[1]) > 30.0:
        mid = mid[1:]
    out = [mid[0]]
    for p in mid[1:]:
        d = math.dist(out[-1], p)
        while d >= step:
            t = step / d
            out.append(tuple(out[-1][i] + (p[i] - out[-1][i]) * t for i in range(3)))
            d = math.dist(out[-1], p)
    return out


def start_at_grid(pts, grid, closed):
    """pts turned to run the grid's way and begin level with grid slot 1, a few metres ahead of it (FM4's Tsukuba:
    its waypoint 0 is 4.5 m ahead); an open line (a hill climb) is cut there instead of wrapped"""
    if not grid:
        return pts
    g1 = grid[0]
    i = min(range(len(pts)), key=lambda k: (pts[k][0] - g1[0]) ** 2 + (pts[k][2] - g1[2]) ** 2)
    ahead = pts[min(i + 2, len(pts) - 1)] if not closed else pts[(i + 2) % len(pts)]
    if len(grid) > 2:   # the grid points forward: slot 3 is behind slot 1
        back = grid[2]
        if (ahead[0] - pts[i][0]) * (g1[0] - back[0]) + (ahead[2] - pts[i][2]) * (g1[2] - back[2]) < 0:
            pts = pts[::-1]
            i = len(pts) - 1 - i
    k = 0
    while math.hypot(pts[i][0] - g1[0], pts[i][2] - g1[2]) < 4.5 and k < len(pts):
        i, k = (i + 1) % len(pts), k + 1
    return pts[i:] + pts[:i] if closed else pts[i:]


def road_way(col_data, a, b, tmp, step=4.0):
    """[(x, y, z)] from a to b over the road of an FM4 .col (tools/fm4_line.py's asphalt graph), or None"""
    import fm4_col
    import fm4_line
    colp = os.path.join(tmp, "way.col")
    with open(colp, "wb") as f:
        f.write(fm4_col.convert_col(col_data))
    road, fm4_line.ROAD = fm4_line.ROAD, {**fm4_line.ROAD, **OWN_ROAD}
    fm4_line.WALLS.clear()
    try:
        cen, nb = fm4_line.graph(fm4_line.road_triangles(colp))
    finally:
        fm4_line.ROAD = road
    if not cen:
        return None
    near = lambda p: min(range(len(cen)), key=lambda k: (cen[k][0] - p[0]) ** 2 + (cen[k][2] - p[2]) ** 2)
    path = fm4_line.shortest(cen, nb, near(a), near(b))
    if not path:
        return None
    out = [tuple(a)]
    for p in [cen[k] for k in path] + [tuple(b)]:
        d = math.dist(out[-1], p)
        while d >= step:
            t = step / d
            out.append(tuple(out[-1][i] + (p[i] - out[-1][i]) * t for i in range(3)))
            d = math.dist(out[-1], p)
    return out[1:]


def own_line(col_data, cams_xml, grid, tmp, vmax_kmh=120, archive=None, lap=True):
    """[(x, y, z)] round an FM1 circuit's lap: the middle of its road splines (road_centre), else a way round its
    own collision between its trackside cameras (cameras_line). A lap whose splines stop short of their start is
    closed over the road of its collision"""
    centre = road_centre(archive) if archive is not None else None
    if centre:
        closed = math.dist(centre[0], centre[-1]) < 60.0
        if lap and not closed:
            way = road_way(col_data, centre[-1], centre[0], tmp)
            if way:
                centre, closed = centre + way, True
        return start_at_grid(centre, grid, closed)
    return cameras_line(col_data, cams_xml, grid, tmp, vmax_kmh)


def cameras_line(col_data, cams_xml, grid, tmp, vmax_kmh=120):
    """[(x, y, z)] round an FM1 circuit's lap from its own collision (fm1_collision's) and trackside cameras
    (tools/fm4_line.py: the asphalt between the cameras, in their order round the lap), started at the start line:
    the waypoint level with grid slot 1, a few metres ahead of it (FM4's Tsukuba: its waypoint 0 is 4.5 m ahead).
    FM1's cameras are FM4's but for the keys' Time and a StartCut below 0 (the lap's end): both are made FM4's"""
    import fm4_col
    import fm4_line
    colp, camp, outp = (os.path.join(tmp, n) for n in ("own.col", "cams.xml", "line.txt"))
    with open(colp, "wb") as f:
        f.write(fm4_col.convert_col(col_data))
    xml = re.sub(r"<Key\s", '<Key Time="0.0" ', cams_xml)
    xml = re.sub(r'StartCut="(-[\d.]+)"', lambda m: f'StartCut="{1 + float(m.group(1)):.6f}"', xml)
    if grid:    # the grid is the first place on the way (Fujimi's hill climb: its cameras start far up the hill)
        x, y, z = grid[0]
        xml = (f'<Cam StartCut="0.000000"><Key Time="0.0" PosX="{x:.3f}" PosY="{y:.3f}" PosZ="{z:.3f}"/></Cam>\n'
               + xml)
    with open(camp, "w", encoding="utf-8") as f:
        f.write(xml)
    road, fm4_line.ROAD = fm4_line.ROAD, {**fm4_line.ROAD, **OWN_ROAD}
    fm4_line.WALLS.clear()      # the module's, filled by each collision it reads
    try:
        failed = fm4_line.main(["fm4_line", colp, camp, outp, str(vmax_kmh)])
    finally:
        fm4_line.ROAD = road
    if failed:
        raise Fm1TrackError("no road round the lap between FM1's trackside cameras")
    with open(outp) as f:
        pts = [tuple(map(float, ln.split()[:3])) for ln in f if ln.strip()]
    return start_at_grid(pts, grid, True)


def write_geo(pts, half_width=6.0):
    """an FM4 AI line (media/AI/Tracks/<track>_NN.geo, "MLPData": a text header of named arrays, then the arrays,
    big-endian; tools/fm4_spawn.py) of FH1-world points: waypoint (X, Y, Z) = (x, z, y - 1), fNormal the vector
    across the road to its edge (half_width m), fChiOptimal the middle"""
    n = len(pts)
    across = []
    for k in range(n):
        a, b = pts[k - 1], pts[(k + 1) % n]
        dx, dz = b[0] - a[0], b[2] - a[2]
        d = math.hypot(dx, dz) or 1.0
        across.append((-dz / d * half_width, dx / d * half_width))
    arrays = [("fWaypointX", [p[0] for p in pts]), ("fWaypointY", [p[2] for p in pts]),
              ("fWaypointZ", [p[1] - 1.0 for p in pts]), ("fNormalX", [c[0] for c in across]),
              ("fNormalY", [c[1] for c in across]), ("fNormalZ", [0.0] * n), ("fChiOptimal", [0.5] * n)]
    head_len = len("MLPDataStart:\n") + sum(len(f"{nm}:float:{n}:1:{0:>9}\n") for nm, _ in arrays) + len("MLPDataEnd:\n")
    head, at = "MLPDataStart:\n", head_len
    for nm, _ in arrays:
        head += f"{nm}:float:{n}:1:{at:>9}\n"
        at += 4 * n
    head += "MLPDataEnd:\n"
    body = b"".join(struct.pack(f">{n}f", *vals) for _, vals in arrays)
    return head.encode("latin-1") + body


def collision_bounds(ribbon_dir):
    """(x0, x1, z0, z1) of an FM4 layout's collision vertices (stream world v2, squares inline), z mirrored like
    our meshes; None without a .col"""
    datas = []
    for f in os.listdir(ribbon_dir):
        if f.lower().endswith(".col"):
            with open(os.path.join(ribbon_dir, f), "rb") as fh:
                datas.append(fh.read())
    return collision_bounds_of(*datas)


def collision_bounds_of(*datas):
    """collision_bounds of FM4 .col files' bytes"""
    xs, zs = [], []
    for d in datas:
        if len(d) < 0x54 or struct.unpack_from(">I", d, 0x30)[0] != COL_COOKIE:
            continue
        n, table = struct.unpack_from(">II", d, 0x4c)
        for i in range(n):
            _, _, _, off, size, _ = struct.unpack_from(">6I", d, table + 24 * i)
            b = d[off:off + size]
            nv, _, pv, _ = struct.unpack_from(">4I", b, 0)
            for k in range(nv):
                x, _, z = struct.unpack_from(">3f", b, pv + 32 * k)
                xs.append(x)
                zs.append(-z)
    return (min(xs), max(xs), min(zs), max(zs)) if xs else None


# ---- staging

def stage(fm1_disc, fm4_disc, circuit, out, view=None, clip=CLIP, log=print):
    own = OWN_CIRCUITS.get(circuit)
    if circuit not in CIRCUITS and not own:
        raise Fm1TrackError(f"{circuit} is not an FM1 circuit this import knows "
                            f"({', '.join(list(CIRCUITS) + list(OWN_CIRCUITS))})")
    f1, default_view = (own[0], own[2]) if own else CIRCUITS[circuit]
    view = default_view if view is None else view
    tracks1, fm4m = fm1_tracks_dir(fm1_disc), fm4_media(fm4_disc)
    xb2 = tracks1 and find_ci(tracks1, f1, "QuickPreview1.xb2")
    if not xb2:
        raise Fm1TrackError(f"Forza Motorsport's disc has no media/Tracks/{f1}/QuickPreview1.xb2")
    host = OWN_HOST if own else circuit
    fm4src = fm4m and find_ci(fm4m, "tracks", host)
    if not fm4src or not find_ci(fm4src, "bin.zip"):
        raise Fm1TrackError(f"FM4's Disc 1 has no Media/tracks/{host}/bin.zip")
    own_col = mega = None
    if own:
        colp = find_ci(tracks1, f1, "QuickPreview1.col")
        mega = mega_track(fm1_disc, f1)
        if not colp or not mega or not mega["grid"]:
            raise Fm1TrackError(f"{own[1]} needs FM1's media/Tracks/{f1}/QuickPreview1.col and media/cabs/Mega.ca2")
        with open(colp, "rb") as fh:
            own_col = fm1_collision(fh.read())
    media = os.path.join(out, "Media")
    tdir = os.path.join(media, "tracks", circuit)
    if os.path.isdir(tdir):
        shutil.rmtree(tdir)
    os.makedirs(tdir)
    lower = circuit.lower()
    import fm4_pvs
    ribbons = {}
    for d in os.listdir(fm4src):
        if re.fullmatch(r"(?i)ribbon_\d\d", d):
            pf = find_ci(os.path.join(fm4src, d), f"{host.lower()}_{int(d[7:]):02d}.pvs")
            if pf:
                with open(pf, "rb") as fh:
                    ribbons[int(d[7:])] = (os.path.join(fm4src, d), fm4_pvs.read_fm4(fh.read()))
    if not ribbons:
        raise Fm1TrackError(f"FM4's {host} has no layouts")
    if own:     # one layout: FM1's lap, as ribbon 0
        ribbons = {0: ribbons[min(ribbons)]}
    first = min(ribbons)
    p0 = ribbons[first][1]
    split = p0["prefix"]
    tmp = tempfile.mkdtemp(prefix="fm1-")
    try:
        _stage(xb2, fm4m, fm4src, circuit, ribbons, first, p0, split, tdir, media, view, clip, tmp, log, own_col)
        if own:
            with open(xb2, "rb") as fh:
                archive = Archive(fh.read())
            stage_own_data(circuit, own, mega, own_col, media, tmp, log, archive)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    log(f"staged FM1 {f1} as {circuit} -> {media}")


# FM1 circuits FM4 has no collision of (#930): the FM4 folder name they are staged under (their key in the launcher,
# so none replaces an FM4 import) -> (FM1 folder, FM1's name, how far each visibility cell sees, FH1 environment id:
# unused by FH1's and FM4's Environments). Their collision, AI line, grid, sectors and lap length are FM1's own
# (fm1_collision, own_line, mega_track); the drawing data, track settings and database rows are OWN_HOST's
OWN_CIRCUITS = {"FM1Fujimi": ("JapaneseMountain", "Fujimi Kaido", 700.0, 130),
                "FM1Tokyo": ("Tokyo", "Tokyo", 700.0, 131),
                "FM1NewYork": ("NewYork", "New York", 700.0, 132),
                "FM1PacificShipyards": ("Vancouver", "Pacific Shipyards", 700.0, 133),
                "FM1Rio": ("Rio", "Rio de Janeiro", 700.0, 134),
                "FM1AlpineRing": ("AlpineYodel", "Alpine Ring", 700.0, 135),
                "FM1BlueMountains": ("FigJam", "Blue Mountains", 700.0, 136),
                "FM1RoadAtlanta": ("RoadAtlanta", "Road Atlanta", 700.0, 138),
                "FM1TestTrack": ("TestTrack", "Test Track", 700.0, 139)}
OWN_HOST = "Tsukuba"
# point to point (a hill climb; Pacific Shipyards' one road from the docks into town): the line runs from the grid
# to the end, not round a lap
POINT_TO_POINT = {"FM1Fujimi", "FM1PacificShipyards"}
OWN_TRACK_ID = 900          # + the circuit's place in OWN_CIRCUITS: its Tracks row id (FM4's end at 326)


def stage_own_data(circuit, own, mega, own_col, media, tmp, log=print, archive=None):
    """the AI line (Media/AI/Tracks/<circuit>_00.geo) and database rows (an Environments, Tracks and SplitPoints
    row, OWN_HOST's with FM1's lap length and sectors) of an FM1-only circuit staged under `circuit`"""
    import sqlite3
    lower = circuit.lower()
    pts = own_line(own_col, mega["cams"], mega["grid"], tmp, archive=archive, lap=circuit not in POINT_TO_POINT)
    ai_out = os.path.join(media, "AI", "Tracks")
    os.makedirs(ai_out, exist_ok=True)
    with open(os.path.join(ai_out, f"{lower}_00.geo"), "wb") as fh:
        fh.write(write_geo(pts))
    log(f"AI line: {len(pts)} waypoints, {sum(math.dist(p, q) for p, q in zip(pts, pts[1:])):.0f} m "
        f"(FM1's lap {mega['length'] or 0:.0f} m)")
    dbp = os.path.join(media, "db", "gamedb.slt")
    if not os.path.isfile(dbp):
        raise Fm1TrackError("FM4's Disc 1 has no Media/db/gamedb.slt")
    db = sqlite3.connect(dbp)
    try:
        def row(table, where, args):
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
            got = db.execute(f"SELECT * FROM {table} WHERE {where} LIMIT 1", args).fetchone()
            return cols, (dict(zip(cols, got)) if got else None)

        def put(table, cols, values):
            names = [c for c in cols if c in values]
            db.execute(f"INSERT OR REPLACE INTO {table} ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                       [values[c] for c in names])
        cols, env = row("Environments", "MediaName LIKE ?", (OWN_HOST,))
        if env is None:
            raise Fm1TrackError(f"FM4's database has no Environments row for {OWN_HOST}")
        env.update({"Id": own[3], "MediaName": circuit, "ThumbImageName": circuit})
        put("Environments", cols, env)
        tcols, tr = row("Tracks", "MediaName LIKE ? AND RibbonIndex=0", (OWN_HOST,))
        tid = OWN_TRACK_ID + list(OWN_CIRCUITS).index(circuit)
        tr.update({"id": tid, "MediaName": circuit, "RibbonIndex": 0, "ReverseIndex": 0, "IsReverse": 0,
                   "Length": round(mega["length"] or 0) or round(sum(
                       math.dist(pts[i], pts[i - 1]) for i in range(len(pts)))), "EnvironmentId": own[3]})
        put("Tracks", tcols, tr)
        db.execute("DELETE FROM SplitPoints WHERE TrackId=?", (tid,))
        nxt = (db.execute("SELECT MAX(ID) FROM SplitPoints").fetchone()[0] or 0) + 1
        for k, s in enumerate(mega["splits"]):
            db.execute("INSERT INTO SplitPoints (ID, TrackId, Split) VALUES (?, ?, ?)", (nxt + k, tid, s))
        db.commit()
    finally:
        db.close()


def _stage(xb2, fm4m, fm4src, circuit, ribbons, first, p0, split, tdir, media, view, clip, tmp, log, own_col=None):
    lower = circuit.lower()
    fm4z = Fm4Zip(find_ci(fm4src, "bin.zip"), tmp)
    log(f"reading FM1's {os.path.basename(os.path.dirname(xb2))}")
    src = Fm1Track(xb2)
    files, records, rec_of = {}, [], {}
    stats = Counter()
    no_pack = max(t[1] + 1 for _, p in ribbons.values() for t in p["textures"])

    def record(tid, flags=4, small=None):
        if tid not in rec_of:
            rec_of[tid] = len(records)
            records.append({"index": tid, "small_index": no_pack if small is None else small,
                            "uv": (1.0, 1.0, 0.0, 0.0), "flags": flags})
        return rec_of[tid]

    # material slots 1 / 2 (textures of the circuit's static pack) take those of one of its own diff_1 models
    shaders0 = [s.split("\\")[-1] for s in p0["shaders"]]
    own = TEMPLATES["diff"][1] if TEMPLATES["diff"][0].lower() == lower else next(
        (mi for mi, m in enumerate(p0["models"])
         if [shaders0[s] for s in m["shaders"]] == ["diff_1"] and len(m["textures"]) == 4), None)
    if own is None:
        # Maple Valley has no diff_1 model: a diff_spec_1 one holds the same two pack textures after its base
        # and lightmap (flags 0 and 1)
        own = next((mi for mi, m in enumerate(p0["models"])
                    if [shaders0[s] for s in m["shaders"]] == ["diff_spec_1"] and len(m["textures"]) == 4
                    and [p0["textures"][t][6] for t in m["textures"][2:]] == [0, 1]), None)
    if own is None:
        raise Fm1TrackError(f"FM4's {circuit} has no diff_1 model to take its material from")
    extra = []
    for t in p0["models"][own]["textures"][2:]:
        r = p0["textures"][t]
        for suf in (".bix", "_B.bix", ".bin"):
            name = f"_0x{r[0]:08x}{suf}"
            if name in fm4z.by:
                n, b = fm4z.read(name)
                files[n] = b
        extra.append(record(r[0], r[6], r[1]))
    templates, zips = {}, {lower: fm4z}
    for kind, (tr, mi) in TEMPLATES.items():
        if tr.lower() not in zips:
            td = find_ci(fm4m, "tracks", tr)
            if not td or not find_ci(td, "bin.zip"):
                raise Fm1TrackError(f"FM4's Disc 1 has no Media/tracks/{tr} (its models are the templates)")
            zips[tr.lower()] = Fm4Zip(find_ci(td, "bin.zip"), tmp)
        tz = zips[tr.lower()]
        if tr.lower() == lower:
            tsplit = split
        else:
            td = find_ci(fm4m, "tracks", tr)
            rib = sorted(x for x in os.listdir(td) if re.fullmatch(r"(?i)ribbon_\d\d", x))[0]
            pf = next(f for f in os.listdir(os.path.join(td, rib)) if f.lower().endswith(".pvs"))
            import fm4_pvs
            with open(os.path.join(td, rib, pf), "rb") as fh:
                tsplit = fm4_pvs.read_fm4(fh.read())["prefix"]
        templates[kind] = Template(tz.read(f"{tsplit}.{mi:05d}.rmb.bin")[1])

    tex_id = {}

    def texture(e, flags=4, tint=None, road=False, cutout=False):
        """record index of an FM1 texture (its .bix pair written once); grey when it cannot be converted"""
        scale = road_k if road and road_k is not None else None
        curve = lm_curve if flags == LM_FLAGS else None
        key = e["name"] + (f"|{tint}" if tint else "") + ("|road" if scale else "") + ("|cut" if cutout else "") + (
            "|lm" if curve else "")
        if key not in tex_id:
            got = src.bix(e, scale, tint, cutout, curve)
            if got is None:
                stats["texture not DXT"] += 1
                tex_id[key] = grey_record()
            else:
                tid = TEX_BASE + len(tex_id)
                tex_id[key] = tid
                files[f"_0x{tid:08X}.bix"], files[f"_0x{tid:08X}_B.bix"] = got
        if flags == LM_FLAGS:
            lm_ids.add(tex_id[key])
        return record(tex_id[key], flags)

    lm_ids = {WHITE_LM, TREE_PALETTE}   # the lightmaps' files: FM4's lightmap format word (LM_FORMAT)

    def grey_record():
        if f"_0x{GREY_ID:08X}.bix" not in files:
            files[f"_0x{GREY_ID:08X}.bix"], files[f"_0x{GREY_ID:08X}_B.bix"] = tex.flat_dxt1((160, 160, 160))
        return GREY_ID

    files[f"_0x{WHITE_LM:08X}.bix"], files[f"_0x{WHITE_LM:08X}_B.bix"] = tex.flat_dxt1((UNLIT_LEVEL,) * 3)
    skipped = Counter()
    log("reading the meshes")
    meshes = list(src.meshes(skipped))
    retile(meshes, stats)
    settle_trees(meshes, stats)
    bounds = None if clip <= 0 else collision_bounds_of(own_col) if own_col else collision_bounds(ribbons[first][0])
    if bounds:
        bx0, bx1, bz0, bz1 = bounds[0] - clip, bounds[1] + clip, bounds[2] - clip, bounds[3] + clip
        keep = []
        for m in meshes:
            xs, zs = [p[0] for p in m["pos"]], [p[2] for p in m["pos"]]
            if max(xs) < bx0 or min(xs) > bx1 or max(zs) < bz0 or min(zs) > bz1:
                skipped[f"more than {clip:.0f} m outside the collision"] += 1
            else:
                keep.append(m)
        meshes = keep
    lm_curve = lightmap_curve(src, meshes, fm4z, p0, log)
    atlases, placed = tex.Atlases(), {}

    def atlas_place(e):
        """the lightmap's (atlas page, its rectangle there), or None (not DXT1 / DXT5, or larger than an atlas
        takes)"""
        if e["name"] not in placed:
            got = tex.nv2a_levels(src.a.descriptor(e), src.a.data(e))
            placed[e["name"]] = None
            if not use_atlases:
                return None
            if got and got[0] in (tex.DXT1, tex.DXT5) and got[3]:
                fmt, w, h, levels = got
                if fmt == tex.DXT5:     # Tokyo's lightmaps: the colour is what lights (DXT1 pages)
                    levels, fmt = [tex.dxt5_to_dxt1(lv) for lv in levels], tex.DXT1
                if lm_curve is not None:
                    levels = tex.map_grey(levels, fmt, lm_curve)
                placed[e["name"]] = atlases.add(w, h, levels)
        return placed[e["name"]]
    # lightmaps into atlases (their uv moved to their place), so lit meshes can be joined like the unlit ones; only
    # where the circuit would not fit the load list without: on Laguna Seca, Road America and Alpine Ring a model
    # with an atlas lightmap faulted in CBaseModelRenderer::FixupTextures (cause not found; Tokyo, New York, Rio and
    # five more load with them), and none of those three needs them
    need = min(load_need(merge_unlit(meshes, tile)) for tile in MERGE_TILES)     # Road America: at 300 m
    use_atlases = need > MAX_LOAD - 100
    log(f"load list: {need} models and textures without lightmap atlases"
        + (" (over the list: atlases)" if use_atlases else ""))
    for m in meshes:
        place = atlas_place(m["light"]) if m["light"] is not None else None
        if place:
            page, (u0, v0, du, dv) = place
            m["uvl"] = [[u0 + min(1.0, max(0.0, u)) * du, v0 + min(1.0, max(0.0, v)) * dv] for u, v in m["uvl"]]
            m["atlas"], m["light"] = page, None
            stats["lightmap in an atlas"] += 1
    n0 = len(meshes)
    for tile in MERGE_TILES:
        merged = merge_unlit(meshes, tile)
        if load_need(merged) + len(atlases.pages) <= MAX_LOAD - 100:
            break
    meshes = merged
    log(f"merge: {n0} meshes -> {len(meshes)} (joined per {tile:.0f} m, texture and lightmap atlas)")
    road_k = None
    roads = sorted({m["base"]["name"]: m["base"] for m in meshes if m.get("road")}.values(), key=lambda t: t["name"])
    if roads:
        greys = sorted(sum(src.mean_rgb(t)) / 3 for t in roads)
        med = greys[len(greys) // 2]
        if med > ROAD_LEVEL:
            road_k = ROAD_LEVEL / med
            log(f"road: {len(roads)} asphalt textures, median grey {med:.0f} -> colour x {road_k:.2f}")
    log("writing the models")
    models = []
    for mesh in meshes:
        b_slot = texture(mesh["base"], tint=mesh.get("tint"), road=mesh.get("road", False),
                         cutout=mesh["kind"] == "opac") if mesh["base"] else None
        if b_slot is None:
            b_slot = record(grey_record())
            stats["no base texture"] += 1
        flat_uv = [[0.0, 0.0]] * len(mesh["pos"])
        if mesh.get("atlas") is not None:
            l_slot, uvl = record(ATLAS_BASE + mesh["atlas"], LM_FLAGS), mesh["uvl"]
        elif mesh["light"] is not None:
            l_slot, uvl = texture(mesh["light"], LM_FLAGS), mesh["uvl"]
        elif mesh.get("palette"):
            if f"_0x{TREE_PALETTE:08X}.bix" not in files:
                files[f"_0x{TREE_PALETTE:08X}.bix"], files[f"_0x{TREE_PALETTE:08X}_B.bix"] = \
                    tex.grey_palette(TREE_LEVELS)
            l_slot, uvl = record(TREE_PALETTE, LM_FLAGS), mesh["uvl"]
            stats["tree palette lighting"] += 1
        elif mesh.get("flat_light") is not None:
            # cards lit by shadows__card_gray_NN (a flat grey): a flat lightmap of that grey
            v = src.mean_rgb(mesh["flat_light"])[0]
            fid = CARD_LM + v
            if f"_0x{fid:08X}.bix" not in files:
                files[f"_0x{fid:08X}.bix"], files[f"_0x{fid:08X}_B.bix"] = tex.flat_dxt1((v, v, v))
            lm_ids.add(fid)
            l_slot, uvl = record(fid, LM_FLAGS), flat_uv
            stats["flat card lighting"] += 1
        else:
            l_slot, uvl = record(WHITE_LM, LM_FLAGS), flat_uv
            stats["no lightmap"] += 1
        tex_list = [b_slot, l_slot] + extra
        slots = (0, 2, 3, UNUSED, UNUSED, UNUSED, UNUSED, 1)
        tpl = templates[mesh["kind"]]
        tris = mesh["tris"]
        for ci, c0 in enumerate(range(0, len(tris), 3 * MAX_TRIS)):
            tri = tris[c0:c0 + 3 * MAX_TRIS]
            cu = sorted(set(tri))
            cmap = {v: i for i, v in enumerate(cu)}
            data, ctr, half, (n, vbytes, uv) = fm4_model(
                mesh["kind"], [mesh["pos"][v] for v in cu], [mesh["normals"][v] for v in cu],
                [mesh["uv0"][v] for v in cu], [uvl[v] for v in cu], [cmap[v] for v in tri], slots,
                f"FM1_{mesh['m']:05d}_{ci}", tpl.v4b)
            mi = len(models)
            files[f"{split}.{mi:05d}.rmb.bin"] = data + tpl.cbs_for(n, vbytes, uv)
            models.append({"textures": tex_list, "shaders": [list(SHADERS).index(mesh["kind"])],
                           "centre": ctr, "half": half,
                           "subset": SUBSET | (OPAC_BIT if mesh["kind"] == "opac" else 0)})
        stats[mesh["kind"]] += 1
    log(f"meshes: {dict(stats)}, skipped {dict(skipped)}; {len(models)} models, {len(records)} textures")
    # every shader the PVS lists must be in the archive, used or not
    for sh in SHADERS.values():
        name = f"shaders/track/{sh}.fxobj"
        if any(n.lower() == name for n in files):
            continue
        z = fm4z if name in fm4z.by else next((z for z in zips.values() if name in z.by), None)
        if z is None:
            raise Fm1TrackError(f"no FM4 circuit read here has {name}")
        n, b = z.read(name)
        files[n] = b
    stx = next((n for n in fm4z.by if n.endswith(".stx.bin")), None)
    if stx:
        n, b = fm4z.read(stx)
        if own_col:     # an FM1-only circuit on its host's pack: the game looks for <track>.stx.bin
            n = f"{circuit}.stx.bin"
        files[n] = b
    for i in range(len(atlases.pages)):
        tid = ATLAS_BASE + i
        files[f"_0x{tid:08X}.bix"], files[f"_0x{tid:08X}_B.bix"] = tex.to_bix(
            atlases.page_levels(i), atlases.side, atlases.side, tex.DXT1)
        lm_ids.add(tid)
    if atlases.pages:
        log(f"lightmaps: {sum(len(p['cells']) for p in atlases.pages)} packed into {len(atlases.pages)} atlases")
    for tid in lm_ids:
        n = f"_0x{tid:08X}.bix"
        if n in files and files[n][:4] == b"BIX1" and struct.unpack_from(">I", files[n], 16)[0] == tex.DXT1:
            files[n] = files[n][:16] + struct.pack(">I", LM_FORMAT) + files[n][20:]
    with zipfile.ZipFile(os.path.join(tdir, "bin.zip"), "w", zipfile.ZIP_STORED) as zo:
        for n, b in files.items():
            zo.writestr(n, b)
    log(f"bin.zip: {len(files)} files, {sum(len(b) for b in files.values()) / 1e6:.1f} MB")

    # ---- visibility per FM4 layout: cells of CELL m, each seeing the models whose box comes within `view` m
    ctr = [(m["centre"][0], m["centre"][2]) for m in models]
    half = [(abs(m["half"][0]), abs(m["half"][2])) for m in models]
    lo = [min(c[i] - h[i] for c, h in zip(ctr, half)) for i in (0, 1)]
    hi = [max(c[i] + h[i] for c, h in zip(ctr, half)) for i in (0, 1)]

    def steps(a, b):
        return [a + CELL / 2 + k * CELL for k in range(max(0, math.ceil((b - a) / CELL)))]
    while True:
        cells = []
        for cx in steps(lo[0], hi[0]):
            for cz in steps(lo[1], hi[1]):
                seen = [mi for mi, ((x, z), (hx, hz)) in enumerate(zip(ctr, half))
                        if max(abs(x - cx) - hx, 0) ** 2 + max(abs(z - cz) - hz, 0) ** 2 < view * view]
                if seen:
                    cells.append(seen)
        most = max(len(c) for c in cells)
        most_tex = max(len({t for mi in c for t in models[mi]["textures"]}) for c in cells)
        if (most <= MAX_SEEN and most_tex <= MAX_SEEN_TEX) or view <= MIN_VIEW:
            break
        view -= 50.0
    log(f"visibility: {len(cells)} cells of {CELL:.0f} m, view {view:.0f} m, "
        f"{most} instances and {most_tex} textures at most ({len(models)} in all)")
    if len(models) + len(records) > MAX_LOAD:
        raise Fm1TrackError(f"{len(models)} models and {len(records)} textures: more than the game loads for a "
                            f"circuit ({MAX_LOAD} in all)")

    def zone_textures(seen):
        return sorted({t for mi in seen for t in models[mi]["textures"]})
    import fm4_pvs
    for r, (rdir, p) in sorted(ribbons.items()):
        data = write_pvs({"est_cpu_bytes": p["hdr"][0], "ribbon": r, "zone_size": p["a2"], "cells": cells,
                          "zone_textures": zone_textures, "textures": records,
                          "shaders": ["shaders\\track\\" + s for s in SHADERS.values()], "models": models,
                          "split_file": split})
        back = fm4_pvs.read_fm4(data)
        assert len(back["instances"]) == len(models)
        rd = os.path.join(tdir, f"Ribbon_{r:02d}")
        os.makedirs(rd, exist_ok=True)
        with open(os.path.join(rd, f"{lower}_{r:02d}.pvs"), "wb") as fh:
            fh.write(data)
        if own_col:     # FM1's own collision; the host's cameras and CollObjs are for another place
            with open(os.path.join(rd, f"{lower}_track_{r:02d}.col"), "wb") as fh:
                fh.write(own_col)
            continue
        for f in os.listdir(rdir):
            if f.lower().endswith((".col", ".xml")):    # collision, cameras, CollObjs (not FM4's .crowd)
                shutil.copyfile(os.path.join(rdir, f), os.path.join(rd, f))
    # TrackSettings.xml only: FM4's <Track>_ribbonNN.sh / .crowd describe FM4's own models
    ts = find_ci(fm4src, "TrackSettings.xml")
    if ts:
        shutil.copyfile(ts, os.path.join(tdir, "TrackSettings.xml"))
    # ---- FM4's AI lines, surfaces, database and names (same circuit, same coordinates)
    ai_out = os.path.join(media, "AI", "Tracks")
    os.makedirs(ai_out, exist_ok=True)
    ai = find_ci(fm4m, "AI", "Tracks")
    for f in os.listdir(ai) if ai else ():
        if f.lower().startswith(lower + "_"):
            shutil.copyfile(os.path.join(ai, f), os.path.join(ai_out, f))
    for parts in (("physics", "surfaceTypes.xml"), ("db", "gamedb.slt")):
        s = find_ci(fm4m, *parts)
        if s:
            os.makedirs(os.path.join(media, parts[0]), exist_ok=True)
            shutil.copyfile(s, os.path.join(media, *parts))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="the circuits both discs have")
    ls.add_argument("fm1_disc")
    ls.add_argument("fm4_disc1")
    sp = sub.add_parser("stage", help="lay an FM1 circuit out as an FM4 one")
    sp.add_argument("fm1_disc")
    sp.add_argument("fm4_disc1")
    sp.add_argument("circuit")
    sp.add_argument("out")
    sp.add_argument("--view", type=float, help="how far each visibility cell sees (m)")
    sp.add_argument("--clip", type=float, default=CLIP, help="leave out meshes this far outside the collision (m)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            for c in available(a.fm1_disc, a.fm4_disc1):
                print(c)
            return 0
        stage(a.fm1_disc, a.fm4_disc1, a.circuit, a.out, a.view, a.clip, lambda s: print(s, flush=True))
    except Fm1TrackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
