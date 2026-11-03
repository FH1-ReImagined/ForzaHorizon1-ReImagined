"""Actual short subprocess failures retain useful output without diagnosing it."""
import errno
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import builder


class Reporter(builder.Reporter):
    def __init__(self, cancelled=False):
        self.lines = []
        self.is_cancelled = cancelled

    def log(self, line):
        self.lines.append(line)

    def cancelled(self):
        return self.is_cancelled


class CommandErrorTests(unittest.TestCase):
    def run_child(self, source, rep=None):
        rep = rep or Reporter()
        with tempfile.TemporaryDirectory() as directory, patch.object(builder, "CURRENT_ROOT", None):
            builder.run([sys.executable, "-c", source], Path(directory), dict(os.environ), rep)
        return rep

    def test_real_stderr_and_operation_are_retained(self):
        with self.assertRaises(builder.BuildError) as caught:
            self.run_child("import sys; print('fatal error: required-scene.bsg is missing', file=sys.stderr); sys.exit(7)")
        error = caught.exception
        self.assertEqual(error.exit_code, 7)
        self.assertEqual(error.command, sys.executable)
        self.assertIn("required-scene.bsg is missing", error.detail)
        self.assertIn("Working directory:", error.detail)
        self.assertIn("Exit code: 7", error.detail)

    def test_early_error_survives_long_trailing_output_and_detail_is_bounded(self):
        rep = Reporter()
        with self.assertRaises(builder.BuildError) as caught:
            self.run_child("import sys; print('CMake Error: SDK target was not found'); "
                           "[print('noise-' + 'x'*3000) for _ in range(30)]; "
                           "print('last diagnostic'); sys.exit(2)", rep)
        self.assertIn("CMake Error: SDK target was not found", caught.exception.detail)
        self.assertIn("last diagnostic", caught.exception.detail)
        self.assertLessEqual(len(caught.exception.detail), 8192)
        self.assertTrue(any(len(line) > 2000 for line in rep.lines))  # Full log is untrimmed.

    def test_failure_without_output_still_names_command_directory_and_code(self):
        with self.assertRaises(builder.BuildError) as caught:
            self.run_child("import sys; sys.exit(9)")
        self.assertIn("Command:", caught.exception.detail)
        self.assertIn("Exit code: 9", caught.exception.detail)
        self.assertNotIn("Last command output", caught.exception.detail)

    def test_output_is_not_used_to_guess_a_failure_cause(self):
        with self.assertRaises(builder.BuildError) as caught:
            self.run_child("import sys; print('memory access disk permission words in ordinary text'); sys.exit(4)")
        self.assertNotIsInstance(caught.exception, builder.FolderBlocked)
        self.assertNotIn("out of memory", str(caught.exception).lower())

    def test_spawn_failure_keeps_original_exception_as_cause(self):
        original = FileNotFoundError(errno.ENOENT, "compiler was removed", "missing-compiler")
        with patch.object(builder.subprocess, "Popen", side_effect=original), self.assertRaises(builder.BuildError) as caught:
            builder.run(["missing-compiler"], Path.cwd(), dict(os.environ), Reporter())
        self.assertIs(caught.exception.__cause__, original)
        self.assertEqual(caught.exception.command, "missing-compiler")
        self.assertIsNone(caught.exception.exit_code)

    def test_success_returns_normally_and_streams_output(self):
        self.assertIn("success marker", self.run_child("print('success marker')").lines)

    def test_cancellation_does_not_become_build_failure(self):
        with self.assertRaises(builder.Cancelled):
            self.run_child("import sys; print('cancelled marker'); sys.exit(5)", Reporter(cancelled=True))

    @unittest.skipUnless(builder.IS_WIN, "Windows access-denied classification")
    def test_existing_folder_blocked_error_retains_command_detail(self):
        with self.assertRaises(builder.FolderBlocked) as caught:
            self.run_child("import sys; print('Access is denied.'); sys.exit(1)")
        self.assertEqual(caught.exception.exit_code, 1)
        self.assertIn("Access is denied.", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
