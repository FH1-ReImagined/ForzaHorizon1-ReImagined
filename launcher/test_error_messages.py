"""Failure messages preserve evidence and give operation-specific next steps."""
import errno
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import socket
import ssl
import sys
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import builder, errors, fetch, installer
from forza_launcher.ui.workers import Task, TaskReporter, extract_task


class ErrorMessages(unittest.TestCase):
    def test_file_access_disk_and_memory_are_distinct(self):
        for error, phrase in ((FileNotFoundError(2, "missing", "game.exe"), "missing path"),
                              (PermissionError(13, "denied", "data"), "writable data folder"),
                              (OSError(errno.ENOSPC, "full"), "Free space"),
                              (OSError(errno.ENOMEM, "memory"), "reduce the build job count")):
            with self.subTest(error=error):
                text = errors.message(error, "build", "test-launcher.log")
                self.assertIn(phrase, text)
                self.assertIn(str(error), text)
                self.assertIn("Game build failed", text)
                self.assertIn("test-launcher.log", text)

    def test_wrapped_network_failures_keep_underlying_repair(self):
        for reason, phrase in ((socket.gaierror(-2, "lookup failed"), "could not be resolved"),
                               (TimeoutError("timed out"), "timed out"),
                               (ConnectionRefusedError("refused"), "connection"),
                               (ssl.SSLCertVerificationError("certificate"), "verification enabled")):
            with self.subTest(reason=reason):
                self.assertIn(phrase, errors.next_step(URLError(reason), "build"))

    def test_ssl_error_number_is_not_a_filesystem_errno(self):
        error = ssl.SSLCertVerificationError(1, "certificate verify failed")
        for wrapped in (error, URLError(error), fetch.CertificateError("certificate verify failed")):
            text = errors.next_step(wrapped, "build")
            self.assertIn("verification enabled", text)
            self.assertIn("date/time and trusted certificates", text)
            self.assertIn("antivirus program scans HTTPS traffic", text)
            self.assertIn("proxy replaces certificates", text)
            self.assertIn("turn off its HTTPS scanning or add the launcher as an exception", text)
            self.assertNotIn("writable data folder", text)

    def test_other_tls_failures_do_not_claim_certificate_cause(self):
        text = errors.next_step(ssl.SSLError(1, "unexpected TLS EOF"), "build")
        self.assertIn("secure connection failed", text)
        self.assertNotIn("date/time", text)

    def test_http_status_does_not_guess_rate_limit_or_credentials(self):
        for code in (403, 404, 429, 503):
            text = errors.message(HTTPError("https://example.test/sdk", code, "download failed", {}, None),
                                  "build", "launcher.log")
            self.assertIn(str(code), text)
            if code == 403:
                self.assertNotIn("rate-limit", text)
                self.assertNotIn("password", text)
            elif code == 429:
                self.assertIn("rate-limiting", text)

    def test_unknown_cause_does_not_invent_repair(self):
        error = RuntimeError("unrecognized native tool failure")
        text = errors.message(error, "extract game files", "launcher.log")
        self.assertIn("Game extraction failed", text)
        self.assertIn(str(error), text)
        self.assertIn("cause is still unclear", text)
        self.assertNotIn("rebuild", text)

    def test_exception_context_and_subprocess_detail_are_preserved(self):
        try:
            raise OSError(errno.ENOSPC, "disk full")
        except OSError:
            error = builder.BuildError("compiler stopped")
            error.__context__ = sys.exception()
        error.detail = "CMake Error: fixture detail"
        text = errors.message(error, "build", "launcher.log")
        self.assertIn("Free space", text)
        self.assertIn(error.detail, text)

    def test_task_failure_uses_operation_and_logs_original_exception(self):
        for error in (builder.BuildError("unsupported TU4: choose the original package"),
                      installer.InstallError("missing executable"), PermissionError("fixture denied")):
            def fail(task):
                raise error
            task = Task(fail, "extract game files")
            result = mock.Mock()
            task.done.connect(result)
            with mock.patch("forza_launcher.ui.workers.paths.log_error", return_value="launcher.log") as log:
                task.run()
            text = result.call_args.args[1]
            self.assertFalse(result.call_args.args[0])
            self.assertIn("Game extraction failed", text)
            self.assertIn(str(error), text)
            self.assertIn("launcher.log", text)
            log.assert_called_once()

    def test_cancellation_is_not_reported_as_failure(self):
        def fail(task):
            raise fetch.Cancelled()
        task = Task(fail, "build")
        result = mock.Mock()
        task.done.connect(result)
        task.run()
        result.assert_called_once_with(False, "Cancelled.")

    def test_failed_build_names_last_reported_step(self):
        def fail(task):
            TaskReporter(task).status("Preparing the SDK")
            raise FileNotFoundError(2, "missing", "sdk.zip")
        task = Task(fail, "build")
        result = mock.Mock()
        task.done.connect(result)
        with mock.patch("forza_launcher.ui.workers.paths.log_error", return_value="launcher.log"):
            task.run()
        self.assertIn("Step: Preparing the SDK", result.call_args.args[1])
        self.assertIn("sdk.zip", result.call_args.args[1])

    def test_windows_runtime_codes_are_not_generic_permission_failures(self):
        for code, phrase in ((126, "runtime component"), (193, "platform/architecture"),
                              (32, "program using the file"), (1455, "memory-heavy")):
            error = OSError("Windows launch error")
            error.winerror = code
            self.assertIn(phrase, errors.next_step(error, "start the game"))

    def test_extraction_failure_also_retains_reported_step(self):
        task = extract_task("example.iso", "destination")
        result = mock.Mock()
        task.done.connect(result)
        with mock.patch("forza_launcher.ui.workers.installer.extract_game", side_effect=installer.InstallError("bad archive")), \
                mock.patch("forza_launcher.ui.workers.paths.log_error", return_value="launcher.log"):
            task.run()
        self.assertIn("Step: Extracting the game files", result.call_args.args[1])

    def test_qt_dialog_displays_angle_brackets_and_ampersands_literally(self):
        from PySide6.QtGui import QTextDocument
        original = "C++ error: <vector> & <a href='bad'>\nCheck file"
        document = QTextDocument()
        document.setHtml(errors.dialog_text(original))
        self.assertEqual(document.toPlainText(), original)
