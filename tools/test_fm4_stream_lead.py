"""tools/fm4_stream_lead.py (#824): the trace reader and the moments it derives, on a
synthetic trace (no game files)."""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_stream_lead as lead  # noqa: E402

TRACE = """\
car,100.000,0.00,0.00,0.00
want,100.000,0,7,8,-1,-1,-1,-1,-1
want,100.000,1,-1,-1,-1,-1,-1,-1,-1
zone,100.100,live,7
zone,100.100,live,8
model,100.200,12,1,AB000000
model,100.250,13,0,AB000100
table,100.200,12,AA000000
tex,100.300,12,0,2E000000,32,32
tex,100.300,12,1,2E000100,32,16
tex,100.300,12,2,00000000,0,0
pool,100.500,1200,52033,300,280
car,101.000,50.00,0.00,0.00
tex,101.400,12,0,AA100000,256,256
want,102.000,0,8,7,9,-1,-1,-1,-1
zone,102.100,live,9
tex,103.000,12,1,AA100100,256,128
zone,104.000,dropped,7
gone,104.500,12
model,106.000,12,1,AB000000
tex,106.100,12,0,2E000000,32,32
tex,106.100,12,1,AA100100,256,128
pool,106.500,1500,52033,310,281,12,480
texobj,100.300,40,440
texobj,100.300,41,440
texobj,100.300,7,040
texobj,100.300,9,FFE
texobj,100.400,40,400
texobj,100.400,41,400
texobj,101.400,40,600
texobj,104.000,40,440
texobj,104.000,41,440
texobj,106.000,41,600
tex,107.5"""


class Trace(unittest.TestCase):
    def setUp(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write(TRACE)
        self.addCleanup(os.unlink, f.name)
        (self.car, self.zone, self.camera, self.model, self.slots,
         self.pool, self.states) = lead.read_trace(f.name)

    def test_rows(self):
        self.assertEqual(self.car, [(100.0, 0.0, 0.0, 0.0), (101.0, 50.0, 0.0, 0.0)])
        self.assertEqual(self.camera, [(100.0, 7), (102.0, 8)])      # the first centre's own zone
        self.assertEqual(self.zone[7].spans, [[100.1, 104.0]])
        self.assertEqual(self.zone[9].spans, [[102.1, None]])
        self.assertEqual(self.model[12].spans, [[100.2, 104.5], [106.0, None]])
        self.assertNotIn(13, self.model)                             # its file did not make a model
        self.assertEqual(self.slots[12][1], [(100.3, 512), (103.0, 32768), (106.1, 32768)])
        self.assertEqual(self.pool[0], (100.5, 1200, 52033, 300, 280, 0, 0))     # an older trace's row
        self.assertEqual(self.pool[-1], (106.5, 1500, 52033, 310, 281, 12, 480))
        # the last row, cut short when the game stopped, is left out
        self.assertEqual(len(self.slots[12][0]), 3)

    def test_texture_requests(self):
        asks, (most, when) = lead.texture_requests(self.states, 107.0)
        # 40: asked at 100.4, loaded a second later, let go at 104; 41: asked for 3.6 s and never
        # loaded, then asked again and loaded at once; 7 is no streamed texture, 9 has no object
        self.assertEqual(sorted(asks), [(40, 100.4, 101.4, 104.0), (41, 100.4, None, 104.0),
                                        (41, 106.0, 106.0, None)])
        self.assertEqual((most, when), (2, 100.4))       # both waiting from 100.4 to 101.4
        self.assertEqual(lead.texture_requests({}, 107.0), ([], (0, None)))

    def test_full_sizes_are_the_largest_seen_of_a_texture_number(self):
        full = lead.full_sizes(self.slots, {12: [40, 41, 42]})
        self.assertEqual(full, {40: 65536, 41: 32768, 42: 0})
        # a second model with the same texture in another slot, only ever at the resident size
        slots = dict(self.slots)
        slots[20] = {3: [(105.0, 1024)]}
        full = lead.full_sizes(slots, {12: [40, 41, 42], 20: [0, 0, 0, 40]})
        self.assertEqual(full[40], 65536)

    def test_textures_full(self):
        numbers = [40, 41, 42]
        full = lead.full_sizes(self.slots, {12: numbers})
        # the first time loaded: slot 0 at 101.4, slot 1 at 103.0; slot 2 never holds a texture
        self.assertEqual(lead.textures_full(self.slots[12], numbers, full, 100.2, 104.5), 103.0)
        # loaded again: slot 1 full at once, slot 0 never before the trace ends
        self.assertEqual(lead.textures_full(self.slots[12], numbers, full, 106.0, None), math.inf)
        # a span with no slot seen
        self.assertIsNone(lead.textures_full(self.slots[12], numbers, full, 110.0, None))
        # a slot beyond the model's texture list counts as full
        self.assertEqual(lead.textures_full({5: [(100.3, 16)]}, numbers, full, 100.2, None), 100.3)
        # only the counted textures' slots: without 40, the reload is full at once; none at all
        self.assertEqual(lead.textures_full(self.slots[12], numbers, full, 106.0, None, {41, 42}), 106.1)
        self.assertEqual(lead.textures_full(self.slots[12], numbers, full, 100.2, 104.5, {41}), 103.0)
        self.assertIsNone(lead.textures_full(self.slots[12], numbers, full, 100.2, 104.5, set()))


class SpansTest(unittest.TestCase):
    def test_at_and_since(self):
        s = lead.Spans()
        s.begin(1.0); s.begin(1.5); s.end(3.0); s.end(3.5); s.begin(5.0)
        self.assertEqual(s.spans, [[1.0, 3.0], [5.0, None]])
        self.assertEqual(s.at(2.0), (1.0, 3.0))
        self.assertEqual(s.at(3.0), (5.0, None))      # not held at 3: the next one, late
        self.assertEqual(s.since(0.5), 1.0)
        self.assertEqual(s.since(9.0), 5.0)
        t = lead.Spans()
        t.begin(1.0); t.end(2.0)
        self.assertIsNone(t.at(2.5))

    def test_merged(self):
        a, b, c = lead.Spans(), lead.Spans(), lead.Spans()
        a.begin(1.0); a.end(3.0)
        b.begin(2.0); b.end(4.0)
        c.begin(6.0)
        self.assertEqual(lead.merged([a, b, c]).spans, [[1.0, 4.0], [6.0, None]])
        self.assertEqual(lead.merged([a, b, c]).since(3.5), 1.0)
        self.assertEqual(lead.merged([]).spans, [])

    def test_percentile(self):
        self.assertEqual(lead.percentile([5, 1, 3, 2, 4], 0.5), 3)
        self.assertEqual(lead.percentile([5, 1, 3, 2, 4], 0.9), 5)
        self.assertEqual(lead.percentile([7], 0.1), 7)


class Kinds(unittest.TestCase):
    def test_kind_of(self):
        self.assertEqual(lead.kind_of("tree_blnd_2"), "trees and bushes")
        self.assertEqual(lead.kind_of("armco_spec"), "barriers and fences")
        self.assertEqual(lead.kind_of("x_unknown"), "other")


if __name__ == "__main__":
    unittest.main()
