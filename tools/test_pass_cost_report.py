#!/usr/bin/env python3
"""tools/pass_cost_report.py on made-up pass ledger CSVs (ReIGlue 0670's columns): the table, the
checks, and fixtures that must fail them (draws that do not add up, a decoder whose labels do not
add up to its thread clock, a thread that /proc says did more)."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pass_cost_report  # noqa: E402

GROUPS = pass_cost_report.GROUPS
STAGES = pass_cost_report.STAGES
HEADER = ("window,epoch,frames,split,label,name,class,layer,segments,order,rdr_cpu_ms,other_device,"
          "rdr_flush_ms,rdr_flush_calls,submitted,runs,dec_ms,dec_draws,dec_skipped,rec_ms,"
          "rec_draws,rec_issue_ms,rec_copies,rec_copy_ms,rec_empty,rt_surface,rt_color,rt_depth,"
          "frame_draws,dec_thread_ms,rec_thread_ms,rec_idle_ms,dec_idle_ms,marks,stale_marks,delta_draws," +
          ",".join("same_" + g for g in GROUPS) + ",stage_draws," +
          ",".join("stage_same_" + s for s in STAGES)).split(",")


def row(window, frames, label, name, cls, **values):
    r = {k: "0" for k in HEADER}
    r.update(window=str(window), epoch="%d.5" % (1000 + window), frames=str(frames), split="1",
             label=label, name=name, rt_surface="00000000", rt_color="00000000",
             rt_depth="00000000")
    r["class"] = cls
    for k, v in values.items():
        r[k] = str(v)
    return ",".join(r[k] for k in HEADER)


def ledger(issued_off=0, decoder_off=0.0):
    """Two windows of 60 frames: the scene (track and D3D draws) and the post chain."""
    lines = [",".join(HEADER)]
    for w in (0, 1):
        lines.append(row(w, 60, "02040000", "p4 #0", "segment", segments=180, order=0,
                         rdr_cpu_ms=60.0, rt_surface="0A020280", rt_color="00030000",
                         rt_depth="00010400"))
        lines.append(row(w, 60, "02040000", "p4 #0", "track", submitted=6000, runs=3000,
                         dec_ms=90.0, dec_draws=9000, rec_ms=180.0, rec_draws=9000,
                         rec_issue_ms=150.0, delta_draws=9000, same_textures=3000,
                         stage_draws=9000, stage_same_pipeline=6000))
        lines.append(row(w, 60, "02040000", "p4 #0", "d3d", submitted=600, dec_ms=30.0,
                         dec_draws=1800, rec_ms=40.0, rec_draws=1800, rec_issue_ms=30.0,
                         rec_copies=180, rec_copy_ms=6.0))
        lines.append(row(w, 60, "02040001", "p4 #0 +1", "segment", segments=60, order=1,
                         rdr_cpu_ms=6.0))
        lines.append(row(w, 60, "02040001", "p4 #0 +1", "up", submitted=120, dec_ms=6.0,
                         dec_draws=120, rec_ms=9.0, rec_draws=120, rec_issue_ms=6.0,
                         rec_copies=60, rec_copy_ms=3.0))
        lines.append(row(w, 60, "00000000", "frame", "frame",
                         frame_draws=9000 + 1800 + 180 + 120 + 60 + issued_off,
                         dec_thread_ms=132.0 + decoder_off, rec_thread_ms=234.0,
                         rec_idle_ms=30.0, dec_idle_ms=6.0, marks=600, stale_marks=300))
    return "\n".join(lines) + "\n"


def run(args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = pass_cost_report.main(args)
    return code, out.getvalue()


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, text):
        path = os.path.join(self.dir.name, name)
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_table_and_checks(self):
        path = self.write("pass.csv", ledger())
        js = os.path.join(self.dir.name, "out.json")
        code, text = run([path, "--check", "--delta", "--json", js])
        self.assertEqual(code, 0, text)
        self.assertIn("120 frames in 2 windows (split)", text)
        self.assertIn("the decoder outside the ring 0.100 ms", text)
        # The scene: 3 segments, 1 ms render thread, 2 ms decoder, 3.67 ms recorder, a frame.
        self.assertIn("| p4 #0 | 640 4x c3@0 d1@1024 | 3.0 | 1.000 | - | 2.000 | 3.667 | 3.000 | "
                      "0.100 | 110 | 180 | - | 183 | - |", text)
        self.assertIn("draws add up: ok", text)
        self.assertIn("decoder sums to its clock: ok", text)
        # The scene before its post chain (the order column), and the change-mask shares.
        self.assertLess(text.index("| p4 #0 |"), text.index("| p4 #0 +1 |"))
        self.assertIn("33.3%", text)  # textures unchanged in a third of the track's draws
        self.assertIn("66.7%", text)  # the pipeline in two thirds
        data = json.load(open(js))
        self.assertEqual(data["frames"], 120)
        self.assertTrue(all(c["ok"] for c in data["checks"]))

    def test_draws_that_do_not_add_up_fail(self):
        path = self.write("pass.csv", ledger(issued_off=1))
        code, text = run([path, "--check"])
        self.assertEqual(code, 1)
        self.assertIn("draws add up: FAILED", text)

    def test_decoder_off_its_clock_fails(self):
        path = self.write("pass.csv", ledger(decoder_off=20.0))
        code, text = run([path, "--check"])
        self.assertEqual(code, 1)
        self.assertIn("decoder sums to its clock: FAILED", text)

    def test_proc(self):
        path = self.write("pass.csv", ledger())
        run_dir = os.path.join(self.dir.name, "run")
        os.makedirs(run_dir)
        # 120 frames between the snapshots; /proc: decoder 2.2 ms a frame, recorder 3.9, render
        # thread (the guest thread created twelfth) 1.1.
        def snap(label, t, dec, rec, rdr):
            th = {"100": ["GPU Commands", dec], "101": ["GPU Recorder", rec]}
            for i in range(12):
                th[str(200 + i)] = ["XThread%04d" % i, rdr if i == 11 else 0]
            return json.dumps({"t": t, "label": label, "th": th})
        with open(os.path.join(run_dir, "snaps.jsonl"), "w") as f:
            f.write(snap("start", 1000.0, 0, 0, 0) + "\n")
            f.write(snap("end", 1002.0, int(2.2e6 * 120), int(3.9e6 * 120), int(1.1e6 * 120)) + "\n")
        with open(os.path.join(run_dir, "cost.csv"), "w") as f:
            f.write("epoch,ms\n")
            for i in range(121):
                f.write("%.4f,4.0\n" % (1000.0 + i * 2.0 / 121))
        code, text = run([path, "--check", "--run", run_dir])
        self.assertEqual(code, 0, text)
        self.assertIn("GPU Commands against /proc: ok", text)
        self.assertIn("g11 against /proc: ok", text)
        # The fixture that must fail: /proc says the decoder did a third more.
        with open(os.path.join(run_dir, "snaps.jsonl"), "w") as f:
            f.write(snap("start", 1000.0, 0, 0, 0) + "\n")
            f.write(snap("end", 1002.0, int(2.8e6 * 120), int(3.9e6 * 120), int(1.1e6 * 120)) + "\n")
        code, text = run([path, "--check", "--run", run_dir])
        self.assertEqual(code, 1)
        self.assertIn("GPU Commands against /proc: FAILED", text)


if __name__ == "__main__":
    unittest.main()
