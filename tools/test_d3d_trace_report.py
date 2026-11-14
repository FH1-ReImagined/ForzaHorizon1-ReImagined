#!/usr/bin/env python3
"""tools/d3d_trace_report.py over a synthetic PM4 trace: python3 tools/test_d3d_trace_report.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

# Two blocks of 10 frames. Block 1: RunCommandBuffer (id 23) wrote 600 dwords (one INDIRECT_BUFFER
# 0x3F per frame), DrawIndexedVertices (22) 200 (DRAW_INDX 0x22, 20 per frame); a foreign buffer
# with SET_CONSTANT read 200 dwords (DRAW_INDX, 10 per frame), launched by id 23.
BLOCK = """BLOCK frames=10 ticks=1 freq=1 game=traced
T 23 dw=600 pad=0 63:10
T 22 dw=200 pad=0 34:200
T 249 dw=200 pad=0 34:100 45:100
TO 23 dw=600 pad=0 63:10
TO 22 dw=200 pad=0 34:200
TO 249 dw=200 pad=0 34:100 45:100
IB foreign=0/0 setconst=10/200 mixed=0/0 written=10/800 nested=10
P setconst 23 runs=10 dw=200
FOREIGN distinct=1
F 17184D40 n=20 times=10 depth=2 setconst=1 parent=23
G 23 calls=10 outer=10 bytes=2400 outer_bytes=3200 disc=0 mcalls=10 masks=0,0,0,0,0,0
G 22 calls=200 outer=200 bytes=800 outer_bytes=800 disc=0 mcalls=200 masks=0,0,0,0,0,0
DEVS n=1 40162C00:3200
CURSORS
LR 82000000/23:10
END
"""


class Report(unittest.TestCase):
    def test_shares_and_parents(self):
        with tempfile.TemporaryDirectory() as d:
            trace = os.path.join(d, "pm4.txt")
            out = os.path.join(d, "out.json")
            with open(trace, "w") as f:
                f.write(BLOCK + BLOCK)
            text = subprocess.run([sys.executable, "-I", os.path.join(HERE, "d3d_trace_report.py"), trace,
                                   "--blocks", "0-1", "--json", out], check=True, capture_output=True,
                                  text=True).stdout
            with open(out) as f:
                r = json.load(f)
        self.assertEqual(r["frames"], 20)
        self.assertAlmostEqual(r["dwords_per_frame"], 100.0)
        self.assertAlmostEqual(r["draw_packets_per_frame"], 30.0)
        self.assertAlmostEqual(r["classes"]["written by D3D (tagged)"]["dw_pct"], 80.0)
        self.assertAlmostEqual(r["classes"]["(foreign buffer with SET_CONSTANT)"]["dw_pct"], 20.0)
        entry = {e["function"]: e for e in r["entry_points"]}
        self.assertAlmostEqual(entry["sub_824469B0"]["calls_per_frame"], 1.0)
        self.assertAlmostEqual(entry["sub_824469B0"]["dw_pct"], 60.0)
        self.assertEqual(r["foreign_parents"][0]["parent_name"], "sub_824469B0")
        self.assertAlmostEqual(r["foreign_parents"][0]["runs_per_frame"], 1.0)
        self.assertIn("RunCommandBuffer", text)


if __name__ == "__main__":
    unittest.main()
