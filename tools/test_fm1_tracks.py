"""Synthetic tests of the FM1 circuit import (#930): fm1_tex and fm1_tracks. No game file is read or
included: archives, textures, meshes and the FM4 template model are made up here."""
import math
import os
import struct
import sys
import tempfile
import unittest
import zlib
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm1_tex as tex  # noqa: E402
import fm1_tracks as ft  # noqa: E402
import fm4_pvs  # noqa: E402


def dxt1_block(c0, c1, idx=0):
    return struct.pack("<HHI", c0, c1, idx)


def nv2a_desc(fmt_code, w, h, levels=1):
    """a 20-byte NV2A texture descriptor: 2D, colour format, mip levels, log2 sizes"""
    f = (2 << 4) | (fmt_code << 8) | (levels << 16) | ((w.bit_length() - 1) << 20) | ((h.bit_length() - 1) << 24)
    return struct.pack("<5I", 0, 0, 0, f, 0)


def archive(files):
    """an FM1 resource archive (uncompressed): files [(name, data, descriptor or b"")]"""
    n = len(files)
    names = b"".join(nm.encode() + b"\0" for nm, _, _ in files)
    descs = b"".join(d for _, _, d in files)
    names_at = 0x206 + 24 * n + 4
    data_at = names_at + len(names) + len(descs) + 0x1000
    table, name_off, desc_off, at, blob = b"", 0, 0, data_at, b""
    for nm, data, d in files:
        table += struct.pack("<6I", 0, at, len(data), len(data), name_off, desc_off)
        name_off += len(nm) + 1
        desc_off += len(d)
        at += len(data)
        blob += data
    hdr = struct.pack("<16I", 0xC0C0AAAA, 15, n + 9, 0, 0, 0, len(names), len(names), 0, 0, 0, 0, 0, 0, 0, 0)
    out = bytearray(hdr.ljust(0x206, b"\0") + table + b"\0" * 4 + names + descs)
    out = out.ljust(data_at, b"\0") + blob
    # the hidden header slots: exporterversion (5.0) 512 bytes before the first file, texcoordscale before that
    struct.pack_into("<f", out, data_at - 0x200, 5.0)
    struct.pack_into("<2f", out, data_at - 0x400, 2.0, 2.0)
    for k in (0x600, 0x800, 0xA00):
        struct.pack_into("<3f", out, data_at - k, -3.4e38, -3.4e38, -3.4e38)
    return bytes(out)


class Textures(unittest.TestCase):
    def test_block_round_trip_of_two_colours(self):
        red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
        px = [red if i % 2 else blue for i in range(16)]
        back = tex._decode_block(tex.DXT1, tex._encode_block(tex.DXT1, px))
        self.assertEqual([p[:3] for p in back], [p[:3] for p in px])

    def test_full_chain_of_a_small_texture(self):
        top = dxt1_block(0xF800, 0x001F, 0x55555555) * 4      # 8 x 8
        w, h, levels = tex.upscale_small_levels(tex.DXT1, 8, 8, [top])
        self.assertEqual((w, h, len(levels)), (32, 32, 6))
        self.assertEqual([len(lv) for lv in levels], [512, 128, 32, 8, 8, 8])
        # past 8:1 the long side is scaled less: 64 x 4 -> 256 x 32, not 512 x 32
        w, h, _ = tex.upscale_small_levels(tex.DXT1, 64, 4, [dxt1_block(0xFFFF, 0xFFFF) * 16])
        self.assertEqual((w, h), (256, 32))

    def test_bix_header_and_sizes(self):
        levels = [dxt1_block(0x1234, 0x1234) * max(1, 16 >> (2 * i)) for i in range(5)]
        bix, top = tex.to_bix(levels, 16, 16, tex.DXT1)
        self.assertEqual(bix[:4], b"BIX1")
        w, h, n, fmt, total, main = struct.unpack_from(">6I", bix, 4)
        self.assertEqual((w, h, n, fmt, main), (16, 16, 5, tex.DXT1, len(top)))
        self.assertEqual(total, main + len(bix) - 28)

    def test_flat_lightmap(self):
        bix, top = tex.flat_dxt1((163, 163, 163))
        self.assertEqual(len(top), tex.FLAT_MAIN)
        self.assertEqual(len(bix), 28 + tex.FLAT_MIPS)

    def test_scale_colour_keeps_four_colour_blocks(self):
        lv = tex.scale_colour([dxt1_block(0x0841, 0x0020, 0xE4E4E4E4)], tex.DXT1, 0.1)
        c0, c1, idx = struct.unpack("<HHI", lv[0])
        self.assertEqual((c0, c1, idx), (0, 0, 0))      # equal endpoints: index 0, not the clear mode
        lv = tex.scale_colour([dxt1_block(0xFFFF, 0x0000, 0x1B)], tex.DXT1, 0.5)
        self.assertEqual(struct.unpack("<HH", lv[0][:4]), (0x8410, 0))

    def test_cutout_and_grey(self):
        holes = dxt1_block(0x0000, 0xFFFF, 0xFFFFFFFF)     # 3-colour mode, every index 3: clear
        desc = nv2a_desc(0x0C, 8, 4)
        self.assertEqual(tex.cutout_share(desc, holes + dxt1_block(0xFFFF, 0)), 0.5)
        w, h, px = tex.top_texels(desc, dxt1_block(0xFFFF, 0xFFFF) * 2)
        self.assertEqual((w, h, tex.grey(px[0])), (8, 4, 255))
        self.assertAlmostEqual(tex.volume_mean_grey(dxt1_block(0xFFFF, 0xFFFF) * 64), 255.0)
        self.assertIsNone(tex.volume_mean_grey(b"\0" * 100))

    def test_cutout_levels_keep_their_alpha_coverage(self):
        def block(a0, a1, solid):       # 8-alpha block: `solid` texels index 0 (a0), the rest index 1 (a1)
            bits = sum(1 << (3 * i) for i in range(solid, 16))
            return bytes([a0, a1]) + bits.to_bytes(6, "little") + bytes(8)
        top = block(255, 0, 4) * 4                  # a quarter solid
        thin = block(100, 0, 8) * 1                 # averaged down: half the texels at 100, none solid
        levels = tex.keep_alpha_coverage([top, thin], tex.DXT5)
        self.assertEqual(levels[0], top)
        self.assertGreaterEqual(tex.alpha_coverage(levels[1]), tex.alpha_coverage(top))
        self.assertEqual(levels[1][2:], thin[2:])   # indices and colour kept
        self.assertEqual(tex.keep_alpha_coverage([top, thin], tex.DXT1), [top, thin])

    def test_grey_curve_follows_the_deciles(self):
        f = tex.grey_curve([20, 40], [80, 100])
        self.assertEqual((f(0), f(20), f(30), f(40), f(255)), (0.0, 80.0, 90.0, 100.0, 255.0))
        self.assertTrue(all(f(g) <= f(g + 1) for g in range(255)))

    def test_map_grey_lifts_and_keeps_the_mode(self):
        def c565(r, g, b):
            return (r >> 3) << 11 | (g >> 2) << 5 | b >> 3
        dark = struct.pack("<HHI", c565(40, 40, 64), c565(16, 16, 32), 0x1B1B1B1B)    # 4-colour mode
        out = tex.map_grey([dark], tex.DXT1, tex.grey_curve([20, 40], [80, 100]))[0]
        c0, c1, idx = struct.unpack("<HHI", out)
        self.assertGreater(c0, c1)
        px = tex._decode_block(tex.DXT1, out)
        self.assertTrue(all(70 < tex.grey(p) < 120 for p in px))
        before = tex._decode_block(tex.DXT1, dark)
        # the blue cast shrinks as it is lifted (5-bit steps keep some)
        self.assertTrue(all(p[2] - p[0] < q[2] - q[0] for p, q in zip(px, before) if q[2] - q[0] > 8))

    def test_hard_alpha_is_solid_or_clear(self):
        soft = bytes([200, 40]) + sum(k << (3 * i) for i, k in enumerate([0, 1, 2, 3, 4, 5, 6, 7] * 2)).to_bytes(
            6, "little") + bytes(8)
        hard = tex.hard_alpha([soft], tex.DXT5)[0]
        pal = tex._alpha_palette(hard[0], hard[1])
        bits = int.from_bytes(hard[2:8], "little")
        got = [pal[(bits >> (3 * i)) & 7] for i in range(16)]
        want = [255 if a >= 128 else 0 for a in [tex._alpha_palette(200, 40)[k] for k in [0, 1, 2, 3, 4, 5, 6, 7] * 2]]
        self.assertEqual(got, want)
        self.assertEqual(hard[8:], soft[8:])

    def test_tree_palette_columns(self):
        bix, top = tex.grey_palette(32)
        self.assertEqual(struct.unpack_from(">2I", bix, 4), (128, 32))


class Meshes(unittest.TestCase):
    def test_strips_and_normals(self):
        self.assertEqual(ft.strip_to_tris([0, 1, 2, 3, 3, 4]), [0, 1, 2, 2, 1, 3])
        up = (0 & 0x7FF) | (1023 << 11) | (0 << 22)
        n = ft.nv2a_normal_to_fm4(up)
        self.assertEqual(((n >> 10) & 0x3FF, n & 0x3FF, n >> 20), (511, 0, 0))
        towards = 511 << 22                                 # +z in FM1 is -z in FM4's mirror
        self.assertEqual(ft.nv2a_normal_to_fm4(towards) >> 20, (-511) & 0x3FF)

    def test_stripify_keeps_every_triangle(self):
        tris = [0, 1, 2, 2, 1, 3, 2, 3, 4, 5, 6, 7]
        strip = ft.stripify(tris)
        got, run = set(), []
        for v in strip + [0xFFFF]:
            if v == 0xFFFF:
                for i in range(len(run) - 2):
                    a, b, c = run[i:i + 3]
                    got.add(frozenset((a, b, c)) if i % 2 == 0 else frozenset((b, a, c)))
                run = []
            else:
                run.append(v)
        self.assertEqual(got, {frozenset(tris[k:k + 3]) for k in range(0, len(tris), 3)})

    def test_blend_split_follows_the_mask(self):
        # a 10 x 10 m square, mask left half white (layer A), right half black (B)
        pos = [[0, 0, 0], [10, 0, 0], [0, 0, 10], [10, 0, 10]]
        uv = [[x / 10, z / 10] for x, _, z in pos]
        mask = (16, 16, bytes(255 if x < 8 else 0 for _ in range(16) for x in range(16)))
        a, b = ft.split_blend(pos, [0] * 4, uv, uv, uv, uv, [0, 1, 2, 2, 1, 3], mask)
        self.assertTrue(a[4] and b[4])
        self.assertTrue(all(p[0] <= 5 for p in a[0]))
        self.assertTrue(all(p[0] >= 5 for p in b[0]))

    def test_floating_trees_settle_on_their_ground(self):
        ground = {"pos": [[-50.0, 0.0, -50.0], [50.0, 0.0, -50.0], [0.0, 0.0, 50.0]], "tris": [0, 1, 2],
                  "light": {"name": "lm"}, "kind": "diff"}

        def tree(low):
            return {"pos": [[0.0, low, 0.0], [2.0, low + 8, 0.0], [0.0, low + 8, 2.0]], "tris": [0, 1, 2],
                    "palette": True, "kind": "opac", "light": None}
        high, low = tree(12.0), tree(3.0)
        stats = Counter()
        ft.settle_trees([ground, high, low], stats)
        self.assertEqual(min(p[1] for p in high["pos"]), ft.TREE_CANOPY)   # 12 m up: lowered
        self.assertEqual(min(p[1] for p in low["pos"]), 3.0)               # 3 m up: as it was
        self.assertEqual(stats["instanced tree lowered to its ground"], 1)

    def test_unlit_meshes_join_per_tile(self):
        def mesh(x, base="tex_a"):
            return {"light": None, "kind": "diff", "base": {"name": base}, "pos": [[x, 0, 0], [x + 1, 0, 0],
                    [x, 0, 1]], "normals": [0] * 3, "uv0": [[0, 0]] * 3, "uvl": None, "tris": [0, 1, 2]}
        out = ft.merge_unlit([mesh(1), mesh(5), mesh(150), mesh(2, "tex_b")])
        self.assertEqual(sorted(len(m["pos"]) for m in out), [3, 3, 6])
        joined = next(m for m in out if len(m["pos"]) == 6)
        self.assertEqual(joined["tris"], [0, 1, 2, 3, 4, 5])

    def test_stretched_ground_is_retiled(self):
        def mesh(mpr):
            return {"base": {"name": "tex_tsukuba_terrain__terrain_dirt_bare_01"},
                    "pos": [[0, 0, 0], [mpr, 0, 0]], "uv0": [[0, 0], [1, 0]]}
        ms = [mesh(10), mesh(11), mesh(50)]
        stats = Counter()
        ft.retile(ms, stats)
        self.assertEqual(ms[2]["uv0"], [[0, 0], [5, 0]])
        self.assertEqual(ms[0]["uv0"], [[0, 0], [1, 0]])


def template_model(kind="diff"):
    """an FM4 model with a template's command buffers: slot 0 holds the uv block, vertex fetch and draw words"""
    pos = [[0, 0, 0], [1, 0, 0], [0, 0, 1], [1, 0, 1]]
    body, _, _, (n, vbytes, uv) = ft.fm4_model(kind, pos, [0] * 4, [[0, 0], [1, 0], [0, 1], [1, 1]],
                                               [[0, 0]] * 4, [0, 1, 2, 2, 1, 3], (0, 1), "tpl",
                                               [(1.0, 1.0, 1.0, 0.0), (10.0, 1.0, 1.0, 1.0)])
    words = list(struct.unpack(">4I", struct.pack(">4f", *uv))) + [
        0x10000000 | vbytes | 2, (n << 16) | 6, 0x40000000 | n, 0xC0DE]
    blob = struct.pack(">%dI" % len(words), *words)
    empty = {"hdr": struct.pack(">6I", 2, 0, 0, 0, 0, 0), "sizes": (0, 0, 0), "data": [b"", b"", b""]}
    used = {"hdr": struct.pack(">3I", 2, 0, 0) + struct.pack(">3I", 0, len(blob), 0),
            "sizes": (0, len(blob), 0), "data": [b"", blob, b""]}
    return body + ft.write_command_buffers((1, 0, 0, 0, 0, 0), [used] + [empty] * 20), (n, vbytes)


class Models(unittest.TestCase):
    def test_template_buffers_take_the_mesh(self):
        data, (n, vbytes) = template_model()
        t = ft.Template(data)
        self.assertEqual((t.n, t.vbytes), (n, vbytes))
        self.assertEqual(t.v4b[1], (10.0, 1.0, 1.0, 1.0))
        cbs = t.cbs_for(99, 3200, (0.0, 0.0, 2.0, 4.0))
        g, back = ft.read_command_buffers(b"\0" * 8 + cbs, 8)
        w = struct.unpack(">%dI" % (len(back[0]["data"][1]) // 4), back[0]["data"][1])
        self.assertEqual(w[:4], struct.unpack(">4I", struct.pack(">4f", 0.0, 0.0, 2.0, 4.0)))
        self.assertEqual(w[4:], (0x10000000 | 3200 | 2, (99 << 16) | 6, 0x40000000 | 99, 0xC0DE))
        self.assertEqual(len(back), 21)

    def test_model_bounds_and_uv_range(self):
        _, ctr, half, (n, vbytes, uv) = ft.fm4_model(
            "opac", [[0, 0, 0], [4, 2, 0], [0, 0, 6]], [0] * 3, [[3.5, 0], [4.5, 1], [3.5, 2]],
            [[0, 0], [1, 1], [0, 1]], [0, 1, 2], (0, 1), "m", [])
        self.assertEqual((ctr, half), ([2, 1, 3], [2, 1, 3]))
        self.assertEqual((n, vbytes), (3, 3 * 36))
        self.assertEqual(uv, (0.0, 0.0, 1.5, 2.0))     # uv0 shifted next to 0 by whole repeats

    def test_pvs_reads_back(self):
        models = [{"textures": [0, 1], "shaders": [0], "centre": [1, 2, 3], "half": [1, 1, 1], "subset": ft.SUBSET},
                  {"textures": [0, 2], "shaders": [1], "centre": [5, 2, 3], "half": [1, 1, 1],
                   "subset": ft.SUBSET | ft.OPAC_BIT}]
        recs = [{"index": i, "small_index": 9, "uv": (1.0, 1.0, 0.0, 0.0), "flags": 12} for i in range(3)]
        data = ft.write_pvs({"est_cpu_bytes": 7, "ribbon": 1, "zone_size": 3, "cells": [[0, 1], [1]],
                             "zone_textures": lambda s: sorted({t for m in s for t in models[m]["textures"]}),
                             "textures": recs, "shaders": ["shaders\\track\\diff_1"], "models": models,
                             "split_file": "TsukubaOut"})
        p = fm4_pvs.read_fm4(data)
        self.assertEqual((p["hdr"][0], p["ribbon"], p["a2"], p["prefix"]), (7, 1, 3, "TsukubaOut"))
        self.assertEqual([c["instances"] for c in p["cells"]], [[0, 1], [1]])
        self.assertEqual(p["cells"][1]["textures"], [0, 2])
        # u16 model, u16 4095, u32 subset (read_fm4 names the 4095 and the subset's high half "flags")
        self.assertEqual([(i["model"], i["flags"] >> 16, i["flags"] & 0xFFFF) for i in p["instances"]],
                         [(0, 4095, ft.SUBSET >> 16), (1, 4095, (ft.SUBSET | ft.OPAC_BIT) >> 16)])
        self.assertEqual(p["models"][1]["matrix"][12:15], [5.0, 2.0, 3.0])


def fm1_col():
    """an FM1 QuickPreview1.col: one square, one triangle (surface 0) listed in its 1 x 1 grid"""
    verts = [(0.0, 1.0, 0.0), (10.0, 1.0, 0.0), (0.0, 1.0, 10.0)]
    pv, pt = 0x40, 0x40 + 16 * 3
    pg = pt + 28
    lst = pg + 4
    blk = bytearray(lst + 4)
    struct.pack_into("<4I", blk, 0, 3, 1, pv, pt)
    struct.pack_into("<2I8f", blk, 0x10, 1, 1, 0, 0, 10, 10, 10, 10, 0.1, 0.1)
    struct.pack_into("<2I", blk, 0x38, pg, 0)
    for i, v in enumerate(verts):
        struct.pack_into("<3fI", blk, pv + 16 * i, *v, 0x7FBF2801)
    struct.pack_into("<BBH3I3f", blk, pt, 0x0E, 0, 0, pv, pv + 16, pv + 32, 0.0, 1.0, 0.0)
    struct.pack_into("<I", blk, pg, lst)
    struct.pack_into("<HH", blk, lst, 1, 0)
    head = bytearray(0x54 + 24)
    struct.pack_into("<2I8f", head, 0, 7, 4, 0, 0, 10, 10, 100, 100, 0.01, 0.01)
    struct.pack_into("<III", head, 0x28, len(head) + len(blk), 2, 0xABCD1234)
    struct.pack_into("<4f", head, 0x34, 100, 100, 8, 8)
    struct.pack_into("<II", head, 0x4C, 1, 0x54)
    struct.pack_into("<6I", head, 0x54, 0, 0, 0, len(head), len(blk), 0)
    return bytes(head + blk)


class OwnCircuits(unittest.TestCase):
    def test_collision_becomes_fm4s(self):
        import fm4_col
        out = ft.fm1_collision(fm1_col())
        self.assertEqual(struct.unpack_from(">III", out, 0x28)[1:], (2, 0xABCD1234))
        off = struct.unpack_from(">6I", out, 0x54)[3]
        nv, nt, pv, pt = struct.unpack_from(">4I", out, off)
        self.assertEqual((nv, nt, pv, pt), (3, 1, 0x40, 0x40 + 96))
        self.assertEqual(struct.unpack_from(">3fII", out, off + pv + 32), (10.0, 1.0, 0.0, 0, 0x7FBF2801))
        self.assertEqual(struct.unpack_from(">BBH3I", out, off + pt), (0x0E, 0, 0, 0x40, 0x60, 0x80))
        self.assertEqual(ft.collision_bounds_of(out), (0.0, 10.0, -10.0, 0.0))
        self.assertTrue(fm4_col.convert_col(out))

    def test_geo_reads_back(self):
        import fm4_spawn
        pts = [(float(i), 2.0, 0.0) for i in range(10)]
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "x_00.geo")
            with open(p, "wb") as f:
                f.write(ft.write_geo(pts))
            back = fm4_spawn.waypoints(p)
        self.assertEqual([tuple(round(v, 3) for v in q) for q in back], [(x, 2.0, z) for x, _, z in pts])

    def test_line_starts_at_the_grid(self):
        loop = [(math.cos(a) * 50, 0.0, math.sin(a) * 50) for a in [k * 2 * math.pi / 80 for k in range(80)]]
        grid = [(50.0, 0.0, -2.0), (48.0, 0.0, -2.0), (50.0, 0.0, -8.0)]      # slot 3 behind: forward is +z
        out = ft.start_at_grid(loop, grid, True)
        self.assertEqual(len(out), 80)
        self.assertGreater(out[1][2], out[0][2])
        self.assertTrue(4.5 <= math.hypot(out[0][0] - 50, out[0][2] + 2) < 9)
        rev = ft.start_at_grid(loop[::-1], grid, True)                      # the other way round: turned
        self.assertGreater(rev[1][2], rev[0][2])
        line = [(0.0, 0.0, float(z)) for z in range(-40, 41, 4)]
        cut = ft.start_at_grid(line, [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -6.0)], False)
        self.assertEqual(cut[-1], (0.0, 0.0, 40.0))
        self.assertGreater(cut[0][2], 0)

    def test_grid_sectors_and_length_from_fm1s_database(self):
        qp = (b'<track><startpoint pos="2" x="3.0" y="1.0" z="-4.5"/>'
              b'<startpoint pos="1" x="1.5" y="1.0" z="2.0"/><split point="0.33"/><split point="0.66"/></track>')
        tl = b'<tracks><track name="Other" length="100"/><track name="Rio" length="5042" RosettaName="Rio de Janeiro"/></tracks>'
        with tempfile.TemporaryDirectory() as t:
            self.assertIsNone(ft.mega_track(t, "Rio"))                     # no archive
            os.makedirs(os.path.join(t, "media", "cabs"))
            with open(os.path.join(t, "media", "cabs", "Mega.ca2"), "wb") as f:
                f.write(archive([("tracks\\rio\\quickpreview.xml", qp, b""), ("forzadb\\tracklist.xml", tl, b"")]))
            got = ft.mega_track(t, "Rio")
        self.assertEqual(got["grid"], [(1.5, 1.0, 2.0), (3.0, 1.0, -4.5)])    # by grid slot
        self.assertEqual(got["splits"], [0.33, 0.66])
        self.assertEqual((got["length"], got["name"], got["cams"]), (5042.0, "Rio de Janeiro", ""))

    def test_atlas_pages(self):
        a = tex.Atlases(side=32, most=16)
        lm = [dxt1_block(0xFFFF, 0xFFFF) * 4, dxt1_block(0x0000, 0x0000)]       # 8 x 8, its 4 x 4 level
        places = [a.add(8, 8, lm) for _ in range(17)]
        self.assertEqual(places[0], (0, (0.0, 0.0, 0.25, 0.25)))
        self.assertEqual(places[5], (0, (0.25, 0.25, 0.25, 0.25)))
        self.assertEqual(places[16][0], 1)                                  # 16 a page
        self.assertIsNone(a.add(32, 32, lm))
        levels = a.page_levels(0)
        self.assertEqual([len(lv) for lv in levels], [8 * 64, 8 * 16, 8 * 4, 8, 8, 8])
        self.assertEqual(levels[0][:8], dxt1_block(0xFFFF, 0xFFFF))


class Archive(unittest.TestCase):
    def test_reads_entries_and_header_slots(self):
        tex_data = dxt1_block(0xFFFF, 0xFFFF) * 4
        data = archive([("tex_a", tex_data, nv2a_desc(0x0C, 8, 8)), ("vbuff_x", b"\1\2\3\4", b"")])
        a = ft.Archive(zlib.compress(data))
        self.assertEqual([e["name"] for e in a.ents], ["tex_a", "vbuff_x"])
        self.assertEqual(a.data(a.res(10)), b"\1\2\3\4")
        self.assertEqual(tex.nv2a_levels(a.descriptor(a.by["tex_a"]), a.data(a.by["tex_a"]))[1:3], (8, 8))
        slot = a.header_slot()
        self.assertEqual(struct.unpack_from("<2f", a.d, slot - 0x200), (2.0, 2.0))

    def test_header_values_of_both_exporters(self):
        a = ft.Archive(archive([("vbuff_x", b"\0" * 4, b"")]))
        self.assertEqual(a.header_values(), ((2.0, 2.0), None))      # -FLT_MAX tree values: no trees
        # Maple Valley's layout: named exporterversion / texcoordscale, tree values right before the first file
        old = bytearray(archive([("texcoordscale", struct.pack("<2f", 128.0, 116.0), b""),
                                 ("exporterversion", struct.pack("<f", 5.0), b"")]))
        first = min(e["off"] for e in ft.Archive(bytes(old)).ents)
        for k in range(1, 6):
            old[first - 0x200 * k:first - 0x200 * k + 16] = bytes(16)
        struct.pack_into("<3f", old, first - 0x200, 0.5, 0.5, 0.5)
        struct.pack_into("<3f", old, first - 0x400, 1000.0, 100.0, 1200.0)
        struct.pack_into("<3f", old, first - 0x600, 900.0, 100.0, 1100.0)
        scale, trees = ft.Archive(bytes(old)).header_values()
        self.assertEqual(scale, (128.0, 116.0))
        self.assertEqual(trees, {"scale": 0.5, "range": (1000.0, 100.0, 1200.0), "offset": (900.0, 100.0, 1100.0)})

    def test_record_texture_ids(self):
        self.assertEqual(ft.Fm1Track.texture_ids([20, 6, 777, 30, 31, 0]), [30, 31, 0])
        self.assertEqual(ft.Fm1Track.texture_ids([20, 5, 1, 1, 1, 1, 30, 31]), [30, 31])

    def test_circuits_need_both_discs(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "fm1", "media", "Tracks", "tsukuba"))
            open(os.path.join(t, "fm1", "media", "Tracks", "tsukuba", "QuickPreview1.xb2"), "wb").close()
            os.makedirs(os.path.join(t, "fm4", "Media", "tracks", "Tsukuba"))
            self.assertEqual(ft.available(os.path.join(t, "fm1"), os.path.join(t, "fm4")), [])
            open(os.path.join(t, "fm4", "Media", "tracks", "Tsukuba", "bin.zip"), "wb").close()
            self.assertEqual(ft.available(os.path.join(t, "fm1"), os.path.join(t, "fm4")), ["Tsukuba"])


if __name__ == "__main__":
    unittest.main()
