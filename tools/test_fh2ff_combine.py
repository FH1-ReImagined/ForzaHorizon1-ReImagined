"""tools/fh2ff_combine.py (#821): one world from Fast & Furious's and Forza Horizon 2's.
Synthetic files only (no game files)."""
import hashlib
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fh2ff_combine as fc  # noqa: E402
import fh2ff_world as ff  # noqa: E402


def pgeo(kind, name, tex=(), geo=(), models=(), dyn=False, tail=b"DATA"):
    """A procedural geometry file as pgeo_parts reads it: the 0x60-byte header, FH2's dynamic
    block when dyn, the name, then (Models) the model references, their instance lists and
    names, then the texture and geometry references (texture id or file number, 0), then tail.
    models: [(name, [LOD instance lists])]."""
    raw = name.encode() + b"\0"
    body = raw
    if kind == fc.MODELS and models:
        body += bytes(-(0x60 + (ff.DYN_FH2 if dyn else 0) + len(body)) % 16)
        lists, names = b"", b""
        for mname, lods in models:
            n = (mname.encode() + b"\0") if mname else b""
            counts = [len(lods[k]) if k < len(lods) else 0 for k in range(3)]
            body += struct.pack(">8f", *range(8)) + struct.pack(">I", len(n))
            body += b"".join(struct.pack(">II", c, 0xDEAD) for c in counts) + b"\0" * 4
            for lod in lods:
                lists += b"".join(struct.pack(">I", v) for v in lod)
            names += n
        body += lists + names
    body += bytes(-(0x60 + (ff.DYN_FH2 if dyn else 0) + len(body)) % 4)
    body += b"".join(struct.pack(">II", t, 0) for t in tex) + b"".join(struct.pack(">II", g, 0) for g in geo)
    body += tail
    head = bytearray(b"OEGP" + bytes(0x5C))
    struct.pack_into(">I", head, 0x30, kind)
    struct.pack_into(">3I", head, 0x3C, len(raw), len(tex), len(geo))
    struct.pack_into(">I", head, 0x54, len(models))
    struct.pack_into(">I", head, 0x58, 1 if dyn else 0)
    out = bytes(head) + (bytes(ff.DYN_FH2) if dyn else b"") + body
    out = bytearray(out)
    struct.pack_into(">I", out, 0x48, len(out))
    return bytes(out)


class ProceduralGeometryReferences(unittest.TestCase):
    def test_vegetation_textures_follow_the_name(self):
        d = pgeo(2, "Veg_1", tex=[10211, 10212])
        p = fc.pgeo_parts(d)
        self.assertEqual(fc.read_refs(d, p["tex"]), [10211, 10212])
        self.assertEqual(p["geo"][1], 0)
        self.assertEqual(p["inst"][1], 0)
        out = fc.rewrite_pgeo(d, tex_map=lambda v: v + 0x2900)
        self.assertEqual(fc.read_refs(out, fc.pgeo_parts(out)["tex"]), [10211 + 0x2900, 10212 + 0x2900])
        self.assertEqual(len(out), len(d))
        self.assertTrue(out.endswith(b"DATA"))

    def test_scene_with_the_dynamic_block(self):
        d = pgeo(5, "Ferris", tex=[7], geo=[14, 15], dyn=True)
        p = fc.pgeo_parts(d)
        self.assertEqual(fc.read_refs(d, p["geo"]), [14, 15])
        out = fc.rewrite_pgeo(d, tex_map=lambda v: v + 100, geo_shift=4465)
        q = fc.pgeo_parts(out)
        self.assertEqual(fc.read_refs(out, q["tex"]), [107])
        self.assertEqual(fc.read_refs(out, q["geo"]), [14 + 4465, 15 + 4465])

    def test_models_instance_lists_and_their_textures(self):
        d = pgeo(fc.MODELS, "Models_Ungrouped_736", tex=[10226, 10224],
                 models=[("NOTFOUND", []), (None, [[46506]]), (None, [[46497], [46505, 46504]])])
        p = fc.pgeo_parts(d)
        self.assertEqual(fc.read_refs(d, p["inst"], 4), [46506, 46497, 46505, 46504])
        self.assertEqual(fc.read_refs(d, p["tex"]), [10226, 10224])
        inst = {46506: 7, 46497: 8, 46505: 9, 46504: 10}
        out = fc.rewrite_pgeo(d, tex_map=lambda v: v - 46, inst_map=inst)
        q = fc.pgeo_parts(out)
        self.assertEqual(fc.read_refs(out, q["inst"], 4), [7, 8, 9, 10])
        self.assertEqual(fc.read_refs(out, q["tex"]), [10180, 10178])
        self.assertIn(b"NOTFOUND\0", out)

    def test_round_trip_and_unchanged_files(self):
        d = pgeo(fc.MODELS, "M", tex=[3], models=[(None, [[5, 6]])])
        out = fc.rewrite_pgeo(d, tex_map=lambda v: v + 1, inst_map={5: 50, 6: 60})
        back = fc.rewrite_pgeo(out, tex_map=lambda v: v - 1, inst_map={50: 5, 60: 6})
        self.assertEqual(back, d)
        same = pgeo(3, "Crowd")                     # characters: no references
        self.assertIs(fc.rewrite_pgeo(same, tex_map=lambda v: v + 1), same)
        self.assertIs(fc.rewrite_pgeo(b"not a pgeo"), b"not a pgeo")
        self.assertIsNone(fc.pgeo_parts(b"XXXX" + bytes(0x60)))

    def test_short_file_refused(self):
        d = bytearray(pgeo(2, "V", tex=[1, 2]))
        struct.pack_into(">I", d, 0x40, 999)        # more texture references than the file holds
        with self.assertRaises(ValueError):
            fc.pgeo_parts(bytes(d))


def bundle(entries):
    """A texture bundle: count, then (id, w, h, levels, format, -1, size, data) each."""
    out = struct.pack(">I", len(entries))
    for tid, data in entries:
        out += struct.pack(">7I", tid, 4, 4, 1, 0x52, 0xFFFFFFFF, len(data)) + data
    return out


class Bundles(unittest.TestCase):
    def test_numbers_carry_on(self):
        a = [0x10000001, 0x10000003, 0x10000005]
        b = [0x10000003, 0x10000001]
        self.assertEqual(fc.bundle_numbers(a, b), {0x10000001: 0x10000007, 0x10000003: 0x10000009})
        with self.assertRaises(ValueError):
            fc.bundle_numbers([0x10000001, 0x10000005], b)

    def test_entries(self):
        d = bundle([(4, b"abcd"), (9, b"xy")])
        self.assertEqual([(t, n) for t, _, n in fc.bundle_entries(d)], [(4, 32), (9, 30)])
        with self.assertRaises(ValueError):
            fc.bundle_entries(d + b"!")

    def test_own_textures_kept_under_new_ids(self):
        d = bundle([(4, b"abcd"), (9, b"xy"), (12, b"zz")])
        out = fc.rewrite_bundle(d, {9: 9 + 0x2900, 12: 77})
        self.assertEqual([t for t, _, _ in fc.bundle_entries(out)], [9 + 0x2900, 77])
        self.assertIn(b"xy", out)
        self.assertNotIn(b"abcd", out)
        self.assertIsNone(fc.rewrite_bundle(d, {}))


class Collision(unittest.TestCase):
    @staticmethod
    def index(records):
        head = bytearray(0x58)
        struct.pack_into(">I", head, 0x4C, len(records))
        return bytes(head) + b"".join(struct.pack(fc.REC, *r) for r in records)

    def test_added_zones_cells_take_the_base_place(self):
        colA = self.index([(0, 100, 0, 10, 0, 10), (1, 101, 10, 20, 0, 30), (2, 102, 30, 5, 0, 35)])
        colB = self.index([(0, 101, 0, 40, 0, 40), (1, 102, 40, 7, 0, 47), (2, 500, 47, 9, 0, 56)])
        # the base has files for cells 0 and 1; the added zones name the added world's record 0
        out, new, taken = fc.combine_collision(colA, colB, a_files={0, 1}, b_take={0})
        self.assertEqual(new, {0: 1, 1: 2, 2: 3})        # 101 replaces the base's, 102 fills a
        self.assertEqual(taken, {1: 0})                   # place without a file, 500 is new
        n = struct.unpack_from(">I", out, 0x4C)[0]
        recs = [struct.unpack_from(fc.REC, out, 0x58 + 24 * k) for k in range(n)]
        self.assertEqual([r[3] for r in recs], [10, 40, 7, 9])
        start = struct.unpack_from(">I", out, 0x54)[0]
        self.assertEqual(start % 0x800, 0)
        self.assertEqual(recs[0][2], start)
        for a, b in zip(recs, recs[1:]):
            self.assertEqual(b[2], a[5])                  # laid out one after another
        self.assertEqual(recs[-1][5], 0)                  # the last record's end, as the games' own
        _, _, kept = fc.combine_collision(colA, colB, a_files={0, 1})
        self.assertEqual(kept, {})                        # without b_take the base's file stays


class FakeZip:
    def __init__(self, data):
        self.data = data

    def read(self, info):
        return self.data[info.name]


class Info:
    def __init__(self, name, size):
        self.name, self.file_size = name, size


class FakeWorld:
    """What Combined.match_textures reads of a world: names, info_of, zip and the records."""

    def __init__(self, files, records):
        self.names = list(files)
        self.info_of = [Info(n, len(d)) for n, d in files.items()]
        self.zip = FakeZip(dict(files))
        self.P = {"textures": records}


def record(tid, k, flags=260):
    return (tid, k, 1.0, 1.0, 0.0, 0.0, flags)


class SharedTextures(unittest.TestCase):
    def combined(self, a_files, a_recs, b_files, b_recs, share=True):
        C = fc.Combined.__new__(fc.Combined)
        C.A, C.B = FakeWorld(a_files, a_recs), FakeWorld(b_files, b_recs)
        with mock.patch.object(fc, "TESTS", set() if share else {"noshare"}):
            C.match_textures()
        return C

    def test_noshare_every_added_texture_is_its_own(self):
        C = self.combined({"_0x00001080.bix": b"concrete"}, [record(5, 0), record(0x1080, 1)],
                          {"_0x00001052.bix": b"concrete"}, [record(5, 0), record(0x1052, 1)], share=False)
        self.assertEqual(C.index, {0: 2, 1: 3})
        self.assertEqual(C.ids, {5: 5 + fc.TEXTURE_ID_STEP, 0x1052: 0x1052 + fc.TEXTURE_ID_STEP})
        self.assertEqual(C.twins, 0)
        self.assertEqual(C.own_ids, {5, 0x1052})

    def test_bundles_and_procedural_geometry_name_records_in_the_table(self):
        # 2 base records, 2 added: the added ones are records 2 and 3, whatever their ids
        C = self.combined({}, [record(1, 0), record(2, 1)], {}, [record(10402, 0), record(10403, 1)], share=False)
        total = 2 + len(C.own)
        out = fc.rewrite_bundle(bundle([(1, b"x"), (0, b"y")]), {k: C.index[k] for k in C.own})
        self.assertEqual([t for t, _, _ in fc.bundle_entries(out)], [3, 2])
        self.assertTrue(all(t < total for t, _, _ in fc.bundle_entries(out)))
        d = fc.rewrite_pgeo(pgeo(2, "Veg", tex=[1, 0]), tex_map=lambda v: C.index.get(v, v))
        self.assertEqual(fc.read_refs(d, fc.pgeo_parts(d)["tex"]), [3, 2])

    def test_the_same_texture_under_another_id_is_the_base(self):
        C = self.combined(
            {"_0x00001080.bix": b"concrete", "_0x00001080_B.bix": b"CONCRETE", "_0x00000005.bix": b"sand"},
            [record(5, 0), record(0x1080, 1)],
            {"_0x00001052.bix": b"concrete", "_0x00001052_B.bix": b"CONCRETE", "_0x00000005.bix": b"stripes"},
            [record(5, 0), record(0x1052, 1)])
        self.assertEqual(C.ids[0x1052], 0x1080)            # the base's record and id
        self.assertEqual(C.index[1], 1)
        self.assertEqual(C.ids[5], 5 + fc.TEXTURE_ID_STEP)  # same id, other bytes: its own
        self.assertEqual(C.index[0], 2)
        self.assertEqual(C.own, [0])
        self.assertEqual(C.twins, 1)
        self.assertEqual(C.tex(0x01000001), 0x01000001)     # flags kept
        self.assertEqual(C.tex(0x02000000), 0x02000002)

    def test_bundled_textures_and_record_fields(self):
        a_b = bundle([(0, b"mask")])                         # bundles name record numbers
        b_b = bundle([(0, b"mask"), (1, b"other")])
        C = self.combined({"_0x10000001.bundle": a_b, "_0x00000002.bix": b"x"},
                          [record(77, 0, 257), record(2, 1)],
                          {"_0x10000023.bundle": b_b, "_0x00000003.bix": b"x"},
                          [record(9940, 0, 257), record(9941, 1, 257), record(3, 2, 256)])
        self.assertEqual(C.ids[9940], 77)                   # the same bundled bytes
        self.assertIn(9941, C.own_ids)
        self.assertIn(3, C.own_ids)                         # same file, other flags: its own

    def test_names_of_texture_files(self):
        C = self.combined({"_0x00000010.bix": b"a"}, [record(0x10, 0)],
                          {"_0x00000020.bix": b"a", "_0x00000021.bix": b"b"}, [record(0x20, 0), record(0x21, 1)])
        C.bundle_new, C.NM, C.NPG, C.zone_new = {}, 0, 0, {}
        self.assertIsNone(C.b_name("_0x00000020.bix", {}, set()))
        self.assertEqual(C.b_name("_0x00000021_B.bix", {}, set()), f"_0x{0x21 + fc.TEXTURE_ID_STEP:08X}_B.bix")


def collobj(k, kind, x, z, y=1.0):
    return (f'\t<Obj{k} PhysicsType="{kind}" GraphicsName="#{k}">\n\t\t<Pos x="{x:.6f}" y="{y:.6f}" z="{z:.6f}"/>\n'
            f'\t\t<Orientation>\n\t\t\t<XAxis x="1" y="0" z="0"/>\n\t\t</Orientation>\n\t</Obj{k}>\n')


class RibbonObjects(unittest.TestCase):
    def test_collision_objects_of_the_region(self):
        a = ('<?xml version="1.0" ?>\n<CollObjs>\n'
             + collobj(0, "gen_obj_crate.rmb", 500.0, 500.0)                      # outside: stays
             + collobj(1, "gen_obj_polereflector.rmb", -3950.0, -5800.0)          # the region: goes
             + "</CollObjs>\n")
        b = ('<?xml version="1.0" ?>\n<CollObjs>\n'
             + collobj(0, "gen_obj_smashboard.0.rmb", -3900.0, -5760.0)          # the region: comes
             + collobj(1, "GEN_BAR_Metal_Mesh.0.rmb", -3901.0, -5761.0)          # a closure there
             + collobj(2, "gen_obj_polereflector.rmb", -3950.2, -5800.1)         # a closure the base
             + collobj(3, "gen_obj_smashboard.0.rmb", 100.0, 100.0)              # has too: comes
             + "</CollObjs>\n")                                                   # outside: not taken
        out, gone = fc.merge_collobjs(a, b, lambda x, z: x < 0)
        self.assertEqual(gone, [(-3901.0, 1.0, -5761.0)])
        self.assertEqual(out.count("<Obj"), 3)
        self.assertIn('<Obj0 PhysicsType="gen_obj_crate.rmb" GraphicsName="#0">', out)
        self.assertIn('<Obj1 PhysicsType="gen_obj_smashboard.0.rmb" GraphicsName="#1">', out)
        self.assertIn('<Obj2 PhysicsType="gen_obj_polereflector.rmb" GraphicsName="#2">', out)
        self.assertIn('x="-3950.200000"', out)
        self.assertNotIn('x="-3950.000000"', out)
        self.assertIn("</Obj2>", out)
        self.assertTrue(out.startswith('<?xml version="1.0" ?>\n<CollObjs>\n\t<Obj0 '))
        self.assertTrue(out.rstrip().endswith("</CollObjs>"))
        odd = a.replace("</CollObjs>", '<Obj9 Physics="x"><Pos x="1" y="1" z="1"/></Obj9></CollObjs>')
        with self.assertRaisesRegex(ValueError, "not read"):     # an object the pattern misses
            fc.merge_collobjs(odd, b, lambda x, z: x < 0)

    def test_smashable_effects(self):
        a = '<Track>\n  <FileEffectPair smashableName="bin" system_name="Dust" />\n</Track>\n'
        b = ('<Track>\n  <FileEffectPair smashableName="BIN" system_name="Other" />\n'
             '  <FileEffectPair smashableName="container" system_name="Sparks" />\n</Track>\n')
        out = fc.merge_smashable_effects(a, b)
        self.assertEqual(out.count("FileEffectPair"), 2)
        self.assertIn('smashableName="container"', out)
        self.assertNotIn("Other", out)


def fiz(x0, z0, tris, tail=b"OWBB"):
    """A collision cell file: a 116 m square from (x0, z0), 12 x 12 squares of 10 m, the triangles
    ((x, y, z) corners) with no square lists (retile_fiz makes them), a second part."""
    verts = [p for t in tris for p in t]
    hs, nv, nt = 0x90, len(verts), len(tris)
    toff = hs + 16 * nv
    goff = toff + 32 * nt
    head = bytearray(hs)
    struct.pack_into(">4I", head, 0, nv, nt, hs, toff)
    struct.pack_into(">II", head, 0x10, 12, 12)
    struct.pack_into(">4f", head, 0x18, x0, z0, x0 + 116, z0 + 116)
    struct.pack_into(">4f", head, 0x28, 10.0, 10.0, 0.1, 0.1)
    struct.pack_into(">I", head, 0x38, goff)
    cell = bytes(head) + b"".join(struct.pack(">3fI", *p, 0x7F) for p in verts)
    cell += b"".join(struct.pack(">4I3fI", 0x0E028000, *[hs + 16 * (3 * k + j) for j in range(3)], 0.0, 1.0, 0.0, 0)
                     for k in range(nt))
    cell += bytes(4 * 144)
    cell += bytes(-len(cell) % 0x800)
    return b"fiz " + struct.pack(">7I", 1, len(cell), len(tail), 0x779, 0, 0, 0) + cell + tail


class CollisionGrid(unittest.TestCase):
    def test_a_cell_cut_again_for_a_grid_12_m_off(self):
        near = [(10, 0, 20), (20, 0, 20), (10, 0, 30)]            # inside both squares
        far = [(10, 0, 110), (20, 0, 110), (10, 0, 114)]          # in the old square's far strip only
        below = [(10, 0, -9), (20, 0, -9), (10, 0, -5)]           # in the neighbour: the new strip
        first = fiz(0.0, 0.0, [near, far])
        south = fiz(0.0, -100.0, [below, near])                   # holds a copy of `near` too
        out = fc.retile_fiz([first, south], 0.0, 12.72)
        bounds, verts, tris = fc.read_fiz(out)
        self.assertEqual([round(v, 2) for v in bounds], [0.0, -12.72, 116.0, 103.28])
        got = sorted(tuple(sorted(verts[k][0] for k in t[1])) for t in tris)
        self.assertEqual(got, sorted(tuple(sorted(map(lambda p: tuple(map(float, p)), t))) for t in (near, below)))
        self.assertTrue(out.endswith(b"OWBB"))
        self.assertEqual(struct.unpack_from(">I", out, 8)[0] % 0x800, 0)
        cell = out[0x20:]
        goff = struct.unpack_from(">I", cell, 0x38)[0]
        offs = struct.unpack_from(">144I", cell, goff)
        # `below` (z -9 to -5) is in the first row of squares (z -12.72 to -2.72), column 1
        k = 0 * 12 + 1
        n = struct.unpack_from(">H", cell, offs[k])[0]
        listed = struct.unpack_from(f">{n}H", cell, offs[k] + 2)
        self.assertEqual([sorted(verts[v][0] for v in tris[i][1])[0][2] for i in listed], [-9.0])
        self.assertEqual(sum(1 for o in offs if o), 5)            # near: 3 squares; below: 2

    def test_unmoved_keeps_every_triangle(self):
        d = fiz(50.0, 50.0, [[(60, 0, 60), (70, 0, 60), (60, 0, 70)]])
        _, verts, tris = fc.read_fiz(fc.retile_fiz([d], 0.0, 0.0))
        self.assertEqual((len(verts), len(tris)), (3, 1))
        with self.assertRaises(ValueError):
            fc.read_fiz(b"nope" + bytes(64))


class RoadNetwork(unittest.TestCase):
    def test_region_ways_join_the_base(self):
        import fm4_routes
        a = fm4_routes.read_nav(fm4_routes.nav_of(
            [(0, 0, 0), (100, 0, 0), (200, 0, 0), (0, 0, 50), (60, 0, 50)],
            [([0, 1, 2], {"road_type": "b"}), ([3, 4], {"road_type": "a"})]))
        b = fm4_routes.read_nav(fm4_routes.nav_of(
            [(100.4, 0, 0), (180, 0, 0), (250, 0, 0), (170, 0, 30), (240, 0, 30), (0, 0, 80), (60, 0, 80)],
            [([0, 1, 2], {"road_type": "b", "traffic_density": "freeway"}),
             ([3, 4], {"road_type": "none"}), ([5, 6], {"road_type": "dirt"})]))
        out = fc.merge_nav(a, b, lambda x, z: x > 150)
        net = fm4_routes.read_nav(fm4_routes.write_nav(out))       # writes and reads back whole
        pos = [(round(n[1], 1), n[3]) for n in net["nodes"]]
        ways = [[pos[k] for k in net["node_list"][f:f + c]] for c, f, _, _ in net["ways"]]
        self.assertEqual(ways, [[(0, 0), (100, 0)],                 # the base's road, cut at the edge
                                [(0, 50), (60, 50)],                # the base's other road
                                [(100, 0), (180, 0), (250, 0)],     # joined at the base's node
                                [(170, 30), (240, 30)]])            # inside: comes; outside: not
        self.assertNotIn((200, 0), pos)                              # the base's node inside: gone
        ids = [n[0] for n in net["nodes"]]
        self.assertEqual(len(set(ids)), len(ids))
        for k, n in enumerate(net["nodes"]):                         # memberships agree with ways
            for w, i in net["memberships"][n[5]:n[5] + n[4]]:
                c, f, _, _ = net["ways"][w]
                self.assertEqual(net["node_list"][f + i], k)
        tags = [{net["keys"][t]: net["values"][v] for t, v in net["tags"][tf:tf + tc]} for _, _, tc, tf in net["ways"]]
        self.assertEqual(tags[0], {"road_type": "b"})
        self.assertEqual(tags[2], {"road_type": "b", "traffic_density": "freeway"})
        self.assertEqual(tags[3], {"road_type": "none"})

    def test_a_road_both_worlds_have_stays_the_base(self):
        import fm4_routes
        a = fm4_routes.read_nav(fm4_routes.nav_of(
            [(0, 0, 0), (100, 0, 0), (200, 0, 0), (300, 0, 0)], [([0, 1, 2, 3], {"road_type": "a"})]))
        b = fm4_routes.read_nav(fm4_routes.nav_of(
            [(100.3, 0, 0), (200.2, 0, 0), (300, 0, 0.4), (200, 0, 60), (200, 0, 120)],
            [([0, 1, 2], {"road_type": "blocked"}), ([1, 3, 4], {"road_type": "none"})]))
        net = fc.merge_nav(a, b, lambda x, z: x > 150)
        pos = [(round(n[1]), round(n[3])) for n in net["nodes"]]
        ways = [[pos[k] for k in net["node_list"][f:f + c]] for c, f, _, _ in net["ways"]]
        tags = [{net["keys"][t]: net["values"][v] for t, v in net["tags"][tf:tf + tc]} for _, _, tc, tf in net["ways"]]
        self.assertEqual(ways, [[(0, 0), (100, 0), (200, 0), (300, 0)],     # whole, the base's
                                [(200, 0), (200, 60), (200, 120)]])         # the added world's own
        self.assertEqual(tags, [{"road_type": "a"}, {"road_type": "none"}])
        self.assertEqual(net["keys"], sorted(net["keys"]))          # the game searches them sorted
        self.assertEqual(net["values"], sorted(net["values"]))
        self.assertTrue(all(n[7] == 0 for n in net["nodes"] if not n[6]))


class Region(unittest.TestCase):
    def test_neighbours(self):
        self.assertEqual(sorted(fc.hex_neighbours(2, 5)), [(1, 4), (1, 5), (2, 4), (2, 6), (3, 4), (3, 5)])
        self.assertEqual(sorted(fc.hex_neighbours(3, 5)), [(2, 5), (2, 6), (3, 4), (3, 6), (4, 5), (4, 6)])

    def test_grows_over_changed_cells_only(self):
        cells_a = {(2, 5): 0, (3, 5): 1, (4, 5): 2, (5, 5): 3}
        cells_b = {(1, 5): 0, (2, 5): 1, (3, 5): 2, (4, 5): 3}
        same = {(k, 0, 0, 1, 1) for k in range(10)}
        prints_a = {(2, 5): same, (3, 5): same, (4, 5): same}
        prints_b = {(2, 5): {(99, 0, 0, 1, 1)} | set(list(same)[:2]),     # rebuilt: 2 of 11 shared
                    (3, 5): same, (4, 5): {(99, 0, 0, 1, 1)}}              # the same; rebuilt but
        region = fc.changed_region(cells_a, cells_b, prints_a, prints_b)   # not next to the others
        self.assertEqual(region, {(1, 5), (2, 5)})
        self.assertEqual(fc.changed_region(cells_a, cells_b, prints_a, prints_b, similar=0.0), {(1, 5)})

    def test_where_an_instance_stands(self):
        P = {"models": [{"matrix": [0.0] * 9 + [-3696.0, 1.9, 5952.0], "extent": [178.0, 0.0, 195.0]},
                        {"matrix": [0.0] * 9 + [0.0, 1.0, 0.0], "extent": [1.0, 2.0, 0.5]}],
             "instances": [{"model": 0}, {"model": 1}]}
        static = {"pos": (0.0, 0.0, 0.0)}
        dynamic = {"pos": (-3430.0, 2.0, -6196.0)}
        self.assertEqual(fc.placed_at(P, 0x80000000, static), (-3696.0, -5952.0, 195.0))
        self.assertEqual(fc.placed_at(P, 1, dynamic), (-3430.0, -6196.0, 1.0))


class Output(unittest.TestCase):
    def test_inputs_are_found_whatever_their_case(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "Media", "Tracks", "ANTHEM", "ribbon_00"))
            self.assertEqual(fc.find_ci(d, "media", "tracks", "Anthem", "Ribbon_00"),
                             os.path.join(d, "Media", "Tracks", "ANTHEM", "ribbon_00"))
            self.assertIsNone(fc.find_ci(d, "media", "db", "gamedb.slt"))

    def test_the_output_is_never_an_input(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "FH2"), os.path.join(d, "FF")
            os.makedirs(os.path.join(a, "media")); os.makedirs(b)
            for out in (a, b, os.path.join(a, "media", "out"), d):
                with self.assertRaisesRegex(ValueError, "input folder"):
                    fc.output_checked(a, b, out)
            new = os.path.join(d, "OUT")
            self.assertEqual(fc.output_checked(a, b, new), os.path.realpath(new))
            with mock.patch.object(os.path, "commonpath", side_effect=ValueError("another drive")):
                fc.output_checked(a, b, new)                        # inputs on another drive: fine
            os.makedirs(os.path.join(new, "media"))                 # an earlier output: fine
            fc.output_checked(a, b, new)
            open(os.path.join(new, "notes.txt"), "w").close()       # anything else: refused
            with self.assertRaisesRegex(ValueError, "other files"):
                fc.output_checked(a, b, new)

    def test_missing_inputs_are_named_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            a, b, out = os.path.join(d, "FH2"), os.path.join(d, "FF"), os.path.join(d, "OUT")
            os.makedirs(a); os.makedirs(b)
            with self.assertRaisesRegex(ValueError, "media/tracks/Anthem"):
                fc.combine(a, b, out)
            self.assertEqual(sorted(os.listdir(d)), ["FF", "FH2"])


class Hex(unittest.TestCase):
    def test_cells(self):
        w, h = 3, 2
        data = b"HEXY" + struct.pack(">ifffiii", 101, 100.0, -4750.0, -9006.66, 2, w, h)
        data += struct.pack(f">{w * h}i", -1, 0, -1, -1, -1, 1)
        cells, grid = fc.hex_cells(data)
        self.assertEqual(cells, {(1, 0): 0, (2, 1): 1})
        self.assertEqual(grid[4:], (3, 2))


if __name__ == "__main__":
    unittest.main()
