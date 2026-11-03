"""Disposable filesystem and fake worker regressions; no game, FIFO or live guest memory."""
import io
import os
import struct
import subprocess
import tempfile
import threading
import types
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import Mock, patch

import render_test as rt


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / "work"
        self.base = self.root / "base"
        self.source = self.root / "profile" / rt.TITLE
        (self.source / "00000001").mkdir(parents=True)
        self.original = self.source / "00000001" / "original"
        self.original.write_bytes(b"original profile")

    def args(self, **kwargs):
        values = dict(work=self.work, baselines=self.base, save=str(self.source), xuid="1234",
                      scenes=None, runs=1, keep_runs=True, hang_secs=40, retries=0, lock_wait=0)
        return types.SimpleNamespace(**(values | kwargs))

    def session(self, run=None):
        session = rt.Session({"game": {}}, self.args(), run or self.work / "runs" / "new",
                             self.source, "1234")
        self.addCleanup(session.pool.shutdown)
        return session

    def alias(self, link, target):
        # Both operands must stay inside this test's owned disposable tree.
        self.assertTrue(link.absolute().is_relative_to(self.root))
        self.assertTrue(target.resolve().is_relative_to(self.root))
        link.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            link.symlink_to(target, target_is_directory=True)
        self.assertTrue(rt.is_reparse(link))

    def test_prune_keeps_active_and_unowned_but_removes_old_finished_runs(self):
        runs = self.work / "runs"
        protected = []
        for kind in (None, "run-active", "run-finished"):
            for i in range(6):
                d = runs / f"{kind}-{i}"
                d.mkdir(parents=True)
                (d / "original").write_text("fixture")
                if kind:
                    rt.mark_owned(d, kind)
                os.utime(d, (100 + i, 100 + i))
                if kind != "run-finished":
                    protected.append(d / "original")
        rt.prune_runs(self.work)
        self.assertTrue(all(p.exists() for p in protected))
        self.assertEqual({p.name for p in runs.glob("run-finished-*")},
                         {f"run-finished-{i}" for i in range(2, 6)})

    def test_prune_rejects_alias_root_without_deleting_targets(self):
        target = self.root / "other"
        target.mkdir()
        for i in range(6):
            d = target / str(i)
            d.mkdir()
            rt.mark_owned(d, "run-finished")
        self.alias(self.work / "runs", target)
        with self.assertRaises(ValueError):
            rt.prune_runs(self.work)
        self.assertEqual(len(list(target.iterdir())), 6)

    def test_prune_does_not_follow_an_owned_child_alias(self):
        target = self.root / "other"
        target.mkdir()
        rt.mark_owned(target, "run-finished")
        for i in range(6):
            self.alias(self.work / "runs" / str(i), target)
        rt.prune_runs(self.work)
        self.assertTrue((target / rt.OWNER).exists())
        self.assertEqual(len(list((self.work / "runs").iterdir())), 6)

    def test_source_overlap_is_rejected_before_profile_deletion(self):
        source = self.base / "save" / rt.TITLE
        (source / "00000001").mkdir(parents=True)
        original = source / "00000001" / "original"
        original.write_bytes(b"original profile")
        rt.mark_owned(source.parent, "save")
        args = self.args(save=str(source))
        with self.assertRaises(ValueError), patch.object(rt, "play") as play:
            rt.cmd_record({"scenes": {"fixture": {}}}, args)
        play.assert_not_called()
        self.assertEqual(original.read_bytes(), b"original profile")

    def test_unowned_scene_is_rejected_before_stored_save_is_replaced(self):
        save = self.base / "save"
        save.mkdir(parents=True)
        rt.mark_owned(save, "save")
        saved = save / "original"
        saved.write_text("stored profile")
        scene = self.base / "fixture"
        scene.mkdir()
        with self.assertRaises(ValueError):
            rt.cmd_record({"scenes": {"fixture": {}}}, self.args())
        self.assertEqual(saved.read_text(), "stored profile")
        self.assertTrue(scene.exists())

    def test_record_copies_profile_without_modifying_source(self):
        with patch.object(rt, "play", return_value=({}, {"fixture": "synthetic stop"})):
            with self.assertRaises(SystemExit):
                rt.cmd_record({"scenes": {"fixture": {}}}, self.args())
        self.assertEqual(self.original.read_bytes(), b"original profile")
        copied = self.base / "save" / rt.TITLE / "00000001" / "original"
        self.assertEqual(copied.read_bytes(), self.original.read_bytes())
        self.assertTrue(rt.owned(self.base / "save", "save"))

    def test_nonempty_unmarked_save_is_never_replaced(self):
        save = self.base / "save"
        save.mkdir(parents=True)
        original = save / "original"
        original.write_text("unrelated")
        with self.assertRaises(ValueError), patch.object(rt.shutil, "copytree") as copy:
            rt.cmd_record({"scenes": {"fixture": {}}}, self.args())
        copy.assert_not_called()
        self.assertEqual(original.read_text(), "unrelated")

    def test_record_replaces_owned_save_and_baseline_and_preserves_source(self):
        for name, kind in (("save", "save"), ("fixture", "baseline")):
            d = self.base / name
            d.mkdir(parents=True)
            rt.mark_owned(d, kind)
            (d / "old").write_text("old tool output")
        png = self.root / "synthetic.png"
        rt.Image.new("RGB", (16, 16)).save(png)
        cfg = {"scenes": {"fixture": {}}, "defaults": {"max_dssim": .01, "max_bad": .01}}
        with patch.object(rt, "play", return_value=({"fixture": [png]}, {})), \
                patch.object(rt, "provenance", return_value={}):
            rt.cmd_record(cfg, self.args())
        self.assertFalse((self.base / "save" / "old").exists())
        self.assertFalse((self.base / "fixture" / "old").exists())
        self.assertTrue((self.base / "fixture" / "baseline.json").is_file())
        self.assertEqual((self.base / "fixture" / "run1_0.png").read_bytes(), png.read_bytes())
        self.assertEqual(self.original.read_bytes(), b"original profile")

    def test_record_rejects_nested_output_alias_before_replacing_save(self):
        save = self.base / "save"
        save.mkdir(parents=True)
        rt.mark_owned(save, "save")
        original = save / "original"
        original.write_text("stored profile")
        self.alias(self.base / "fixture" / "alias", self.source.parent)
        with self.assertRaises(ValueError), patch.object(rt.shutil, "copytree") as copy:
            rt.cmd_record({"scenes": {"fixture": {}}}, self.args())
        copy.assert_not_called()
        self.assertEqual(original.read_text(), "stored profile")

    def test_existing_run_is_refused_without_copying_or_launching(self):
        run = self.work / "runs" / "existing"
        run.mkdir(parents=True)
        original = run / "original"
        original.write_text("active")
        with patch.object(rt.shutil, "copytree") as copy, patch.object(rt.subprocess, "Popen") as popen:
            with self.assertRaises(FileExistsError):
                self.session(run).start()
        copy.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(original.read_text(), "active")

    def test_run_creation_is_owned_and_exclusive_before_copy(self):
        s = self.session()
        real_open = open

        def fake_open(path, *args, **kwargs):
            if str(path) == "/proc/meminfo":
                return io.StringIO("MemAvailable: 20971520 kB\n")
            return real_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=fake_open), \
                patch.object(rt.shutil, "copytree", side_effect=OSError("stop before FIFO")), \
                patch.object(rt.subprocess, "Popen") as popen:
            with self.assertRaises(OSError):
                s.start()
        popen.assert_not_called()
        self.assertTrue(rt.owned(s.dir, "run-active"))
        s.stop()
        self.assertTrue(rt.owned(s.dir, "run-finished"))

    def test_profile_alias_is_rejected_before_copy(self):
        alias = self.root / "alias"
        self.alias(alias, self.source.parent)
        with self.assertRaises(ValueError):
            rt.profile_source(alias)
        self.assertEqual(self.original.read_bytes(), b"original profile")

    def test_short_clock_read_means_unavailable_clock(self):
        guest = Mock()
        guest.u32.side_effect = struct.error("short read")
        self.assertIsNone(rt.clock_address(guest))

    def test_dead_clock_without_an_exception_also_fails_session(self):
        s = self.session()
        s.clock = Mock(error=None, addr=0x500000)
        s.clock.is_alive.return_value = False
        with self.assertRaisesRegex(rt.Hang, "unexpected exit"):
            s.check()

    def test_failed_clock_clears_address_closes_guest_and_fails_session(self):
        guest = Mock()
        guest.put_f32.side_effect = ValueError("synthetic writer failure")
        with patch.object(rt, "Guest", return_value=guest), \
                patch.object(rt, "clock_address", return_value=0x500000):
            clock = rt.ClockHold(0)
            clock.set(43200)
            clock.start()
            clock.join(1)
        self.assertFalse(clock.is_alive())
        self.assertIsNone(clock.addr)
        guest.close.assert_called_once()
        s = self.session()
        s.clock, s.proc = clock, Mock()
        s.proc.poll.return_value = None
        with self.assertRaisesRegex(rt.Hang, "synthetic writer failure"):
            s.check()

    def test_stop_joins_clock_before_process_cleanup_even_if_conversion_fails(self):
        events = []
        s = self.session()
        s.clock = Mock(ident=1, error=None)
        s.clock.join.side_effect = lambda: events.append("join")
        s.proc = Mock()
        s.proc.poll.return_value = None
        s.proc.send_signal.side_effect = lambda sig: events.append("signal")
        future = Future()
        future.set_exception(ValueError("bad image"))
        s.converting = [future]
        with self.assertRaisesRegex(ValueError, "bad image"):
            s.stop()
        self.assertEqual(events, ["join", "signal"])
        s.proc.wait.assert_called_once()
        self.assertTrue(s.pool._shutdown)

    def test_cleanup_failure_is_session_error_without_successful_shots(self):
        cfg = {"session": [{"name": "fixture", "steps": ["capture fixture"]}]}
        s = Mock(shots={"fixture": [Path("invalid.png")]})
        s.stop.side_effect = ValueError("bad conversion")
        with patch.object(rt, "Session", return_value=s), patch.object(rt, "bench_lock_wait"):
            shots, errors = rt.play(cfg, self.args(), {"fixture"}, "same-second", self.source, "1234")
        self.assertEqual(shots, {})
        self.assertIn("bad conversion", errors["fixture"])

    def test_same_label_sessions_get_distinct_run_paths(self):
        cfg = {"session": [{"name": "fixture", "steps": ["capture fixture"]}]}
        s = Mock(shots={"fixture": []})
        with patch.object(rt, "Session", return_value=s) as constructor, \
                patch.object(rt, "bench_lock_wait"):
            for _ in range(2):
                rt.play(cfg, self.args(), {"fixture"}, "same-second", self.source, "1234")
        a, b = [call.args[2] for call in constructor.call_args_list]
        self.assertEqual(a.parent, self.work / "runs")
        self.assertEqual(b.parent, a.parent)
        self.assertNotEqual(a, b)

    def test_probe_returns_exit_two_for_failed_session(self):
        cfg = {"session": [{"name": "fixture", "steps": ["capture fixture"]}]}
        with patch.object(rt, "play", return_value=({}, {"fixture": "clock worker failed"})):
            self.assertEqual(rt.cmd_probe(cfg, self.args(session=None)), 2)

    def test_check_returns_exit_two_for_failed_session(self):
        scene = self.base / "fixture"
        scene.mkdir(parents=True)
        (scene / "baseline.json").write_text('{"files": []}')
        save = self.base / "save"
        save.mkdir()
        (save / "save.json").write_text('{"xuid": "1234"}')
        cfg = {"scenes": {"fixture": {}}, "defaults": {"max_dssim": .01, "max_bad": .01}}
        with patch.object(rt, "play", return_value=({}, {"fixture": "clock worker failed"})), \
                patch.object(rt, "provenance", return_value={}):
            self.assertEqual(rt.cmd_check(cfg, self.args(report=None)), 2)


if __name__ == "__main__":
    unittest.main()
