"""One session per forza.log (#609): the launcher moves the last session's log aside before the game
starts, and crash reports read only the new one. Disposable logs only; no game is started."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import crash_report, game
from forza_launcher.config import Config

OLD_FAULT = "[critical] [core] [t1] Unhandled read fault = guest 0x0BADBAD0 (old session)"
NEW_FAULT = "[critical] [core] [t1] Unhandled read fault = guest 0x0AF048B8 (new session)"
DEVICE = "[info] [gpu] [t1] Vulkan device 'Test GPU': driver version 0x1234"


def locked(path):
    """os.replace as on Windows while another process has `path` open: the move is refused."""
    real = os.replace

    def replace(src, dst):
        if Path(src) == Path(path):
            raise PermissionError(13, "The process cannot access the file because it is being used "
                                      "by another process", str(src))
        return real(src, dst)
    return mock.patch.object(game.os, "replace", side_effect=replace)


class RotateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.log = self.dir / "forza.log"
        self.notes = []

    def names(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_names(self):
        self.assertEqual(game.previous_log_path(self.log).name, "forza.previous.log")
        self.assertEqual(game.rolled_log_path(self.log).name, "forza.1.log")
        self.assertEqual(game.rolled_log_path(game.previous_log_path(self.log)).name, "forza.previous.1.log")

    def test_no_log_yet(self):
        self.assertTrue(game.rotate_log(self.log, self.notes.append))
        self.assertTrue(game.rotate_log(None, self.notes.append))
        self.assertEqual(self.names(), [])
        self.assertEqual(self.notes, [])

    def test_last_session_becomes_the_previous_one_and_one_copy_is_kept(self):
        self.log.write_text("session 1\n")
        self.assertTrue(game.rotate_log(self.log, self.notes.append))
        self.assertEqual(self.names(), ["forza.previous.log"])
        self.log.write_text("session 2\n")
        self.assertTrue(game.rotate_log(self.log, self.notes.append))
        self.assertEqual(self.names(), ["forza.previous.log"])
        self.assertEqual((self.dir / "forza.previous.log").read_text(), "session 2\n")
        self.assertEqual(self.notes, [])

    def test_the_roll_moves_with_its_session(self):
        self.log.write_text("end of session 1\n")
        (self.dir / "forza.1.log").write_text("start of session 1\n")
        game.rotate_log(self.log, self.notes.append)
        self.assertEqual(self.names(), ["forza.previous.1.log", "forza.previous.log"])
        self.assertEqual((self.dir / "forza.previous.1.log").read_text(), "start of session 1\n")
        # A session that stayed under the limit: the older session's roll goes.
        self.log.write_text("session 2\n")
        game.rotate_log(self.log, self.notes.append)
        self.assertEqual(self.names(), ["forza.previous.log"])
        self.assertEqual(self.notes, [])

    def test_a_log_an_older_game_still_holds_stays_and_is_noted(self):
        self.log.write_text("still being written\n")
        (self.dir / "forza.1.log").write_text("its start\n")
        (self.dir / "forza.previous.log").write_text("the session before\n")
        with locked(self.log):
            self.assertFalse(game.rotate_log(self.log, self.notes.append))
        self.assertEqual(self.log.read_text(), "still being written\n")
        self.assertEqual((self.dir / "forza.1.log").read_text(), "its start\n")
        self.assertEqual((self.dir / "forza.previous.log").read_text(), "the session before\n")
        self.assertEqual(len(self.notes), 1)
        self.assertIn("Kept forza.log", self.notes[0])
        self.assertIn("another game may still be writing it", self.notes[0])

    def test_a_roll_that_cannot_move_is_noted(self):
        self.log.write_text("end\n")
        roll = self.dir / "forza.1.log"
        roll.write_text("start\n")
        with locked(roll):
            self.assertTrue(game.rotate_log(self.log, self.notes.append))
        self.assertEqual((self.dir / "forza.previous.log").read_text(), "end\n")
        self.assertTrue(roll.exists())
        self.assertEqual(len(self.notes), 1)
        self.assertIn("forza.1.log", self.notes[0])


class SessionLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root / "data")
        (self.root / "version.txt").write_text("fixture-build-123")
        self.log = self.root / "forza.log"
        install = game.GameInstall(self.root, self.root / "forza")
        self.plan = game.LaunchPlan(install, self.root, ["forza", f"--log_file={self.log}"],
                                    {}, [], self.root / "forza.toml", "")
        self.notes = []

    def report(self):
        return crash_report.collect(self.plan, 134, crash_report.launch_context(self.cfg, self.plan))

    def test_the_report_reads_this_session_only(self):
        self.log.write_text(OLD_FAULT + "\n")
        game.prepare_log(self.plan, self.notes.append)
        self.assertIsNone(self.plan.log_before_start)
        self.log.write_text(DEVICE + "\n" + NEW_FAULT + "\n")
        body = self.report().body
        self.assertIn("0x0AF048B8", body)
        self.assertNotIn("0x0BADBAD0", body)
        self.assertIn("forza.previous.log", body)  # where the session before is
        self.assertIn("0x0AF048B8", game.crash_reason(self.plan, 134))

    def test_a_session_that_wrote_no_log(self):
        self.log.write_text(OLD_FAULT + "\n")
        game.prepare_log(self.plan, self.notes.append)
        self.assertTrue(game.exit_is_crash(self.plan, 1))
        body = self.report().body
        self.assertIn("The game wrote no log in this session.", body)
        self.assertNotIn("0x0BADBAD0", body)

    def test_a_locked_log_still_excludes_the_older_session(self):
        self.log.write_text(OLD_FAULT + "\n[info] Execution complete\n")
        with locked(self.log):
            game.prepare_log(self.plan, self.notes.append)
        self.assertEqual(len(self.notes), 1)
        self.assertIsNotNone(self.plan.log_before_start)
        self.assertTrue(game.exit_is_crash(self.plan, 1))
        body = self.report().body
        self.assertIn("previous run's log has been excluded", body)
        self.assertNotIn("0x0BADBAD0", body)

    def test_a_session_that_reached_the_size_limit(self):
        roll = self.root / "forza.1.log"
        roll.write_text("[info] older session's roll\n")
        self.log.write_text(OLD_FAULT + "\n")
        game.prepare_log(self.plan, self.notes.append)
        self.assertIsNone(game.session_roll(self.plan))
        # The game logs past the limit: its start is in forza.1.log, the rest in a new forza.log.
        roll.write_text(DEVICE + "\n" + "\n".join(f"[info] line {i}" for i in range(500)) + "\n")
        self.log.write_text("[info] after the roll\n" + NEW_FAULT + "\n")
        self.assertEqual(game.session_roll(self.plan), roll)
        body = self.report().body
        self.assertIn("driver version 0x1234", body)  # the start of the session, from the roll
        self.assertIn("line 499", body)                # the roll's last lines before the new file's
        self.assertIn("0x0AF048B8", body)
        self.assertNotIn("older session's roll", body)
        self.assertNotIn("0x0BADBAD0", body)

    def test_an_older_roll_left_beside_a_locked_log_is_not_this_sessions(self):
        roll = self.root / "forza.1.log"
        roll.write_text(DEVICE + "\n")
        self.log.write_text(OLD_FAULT + "\n")
        with locked(self.log):
            game.prepare_log(self.plan, self.notes.append)
        self.assertIsNone(game.session_roll(self.plan))

    def test_the_game_start_moves_the_last_log_aside(self):
        self.log.write_text(OLD_FAULT + "\n")
        session = game.Session(self.plan, log=self.notes.append)
        with mock.patch.object(session, "_acquire_save_lease"), \
                mock.patch.object(game.subprocess, "Popen") as popen, \
                mock.patch("forza_launcher.haptics.service.supported", return_value=False):
            session._start_game({})
        popen.assert_called_once()
        self.assertFalse(self.log.exists())
        self.assertEqual((self.root / "forza.previous.log").read_text(), OLD_FAULT + "\n")
        self.assertIsNone(self.plan.log_before_start)

    def test_a_log_the_game_start_cannot_move_is_noted_in_the_launcher_log(self):
        self.log.write_text(OLD_FAULT + "\n")
        session = game.Session(self.plan, log=self.notes.append)
        with mock.patch.object(session, "_acquire_save_lease"), \
                mock.patch.object(game.subprocess, "Popen"), \
                mock.patch("forza_launcher.haptics.service.supported", return_value=False), \
                mock.patch.object(game.paths, "log_error") as log_error, locked(self.log):
            session._start_game({})
        self.assertTrue(self.log.exists())
        self.assertFalse((self.root / "forza.previous.log").exists())
        self.assertEqual(len(self.notes), 1)
        log_error.assert_called_once_with(self.notes[0])
        self.assertIsNotNone(self.plan.log_before_start)

    def test_the_shader_prebuild_run_moves_the_last_log_aside_too(self):
        from forza_launcher import shader_prebuild
        self.log.write_text(OLD_FAULT + "\n")
        proc = mock.Mock(returncode=0)
        proc.poll.return_value = 0
        with mock.patch.object(shader_prebuild.subprocess, "Popen", return_value=proc), \
                self.assertRaises(shader_prebuild.PrebuildError):  # this fake game writes nothing
            shader_prebuild.run_game(self.plan, log=self.notes.append, poll=0)
        self.assertFalse(self.log.exists())
        self.assertTrue((self.root / "forza.previous.log").exists())


if __name__ == "__main__":
    unittest.main()
