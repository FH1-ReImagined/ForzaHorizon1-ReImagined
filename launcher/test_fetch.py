"""TLS context selection and the certificate error message (#85), without the network;
unpacking archives with links (#200)."""
import io
import os
import ssl
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import fetch, updater


def cert_error():
    reason = ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                                             "unable to get local issuer certificate")
    return urllib.error.URLError(reason)


class ContextSelectionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, fetch, "_CONTEXTS", fetch._CONTEXTS)
        fetch._CONTEXTS = None

    def test_windows_and_macos_try_the_os_store_then_the_default_context(self):
        for platform in ("win32", "darwin"):
            fetch._CONTEXTS = None
            os_ctx, default = MagicMock(name="truststore"), MagicMock(name="default")
            with self.subTest(platform=platform), patch.object(fetch.sys, "platform", platform), \
                    patch.object(fetch, "_truststore_context", return_value=os_ctx), \
                    patch.object(fetch, "_default_context", return_value=default):
                self.assertEqual(fetch.contexts(), [os_ctx, default])
                self.assertIs(fetch.ssl_context(), os_ctx)

    def test_windows_without_truststore_uses_the_default_context(self):
        default = MagicMock(name="default")
        with patch.object(fetch.sys, "platform", "win32"), \
                patch.object(fetch, "_truststore_context", return_value=None), \
                patch.object(fetch, "_default_context", return_value=default):
            self.assertEqual(fetch.contexts(), [default])

    def test_linux_keeps_the_default_context(self):
        default = MagicMock(name="default")
        with patch.object(fetch.sys, "platform", "linux"), \
                patch.object(fetch, "_truststore_context") as ts, \
                patch.object(fetch, "_default_context", return_value=default):
            self.assertEqual(fetch.contexts(), [default])
            ts.assert_not_called()

    def test_truststore_import_failure_gives_none(self):
        with patch.dict(sys.modules, {"truststore": None}):
            self.assertIsNone(fetch._truststore_context())

    def test_contexts_verify(self):
        for ctx in fetch.contexts():
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(ctx.check_hostname)


class UrlopenTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, fetch, "_CONTEXTS", fetch._CONTEXTS)
        self.first, self.second = object(), object()
        fetch._CONTEXTS = [self.first, self.second]

    def test_falls_back_to_the_next_context_on_a_certificate_error(self):
        calls = []

        def fake(req, timeout, context):
            calls.append(context)
            if context is self.first:
                raise cert_error()
            return "response"
        with patch.object(fetch.urllib.request, "urlopen", side_effect=fake), \
                patch("forza_launcher.paths.log_error") as log:
            self.assertEqual(fetch.urlopen("https://github.com/x", timeout=5), "response")
        self.assertEqual(calls, [self.first, self.second])
        log.assert_called_once()

    def test_every_context_failing_gives_an_actionable_error_with_the_host(self):
        with patch.object(fetch.urllib.request, "urlopen", side_effect=cert_error()), \
                patch("forza_launcher.paths.log_error") as log:
            with self.assertRaises(fetch.CertificateError) as cm:
                fetch.urlopen("https://github.com/x/y.zip")
        text = str(cm.exception)
        self.assertIn("github.com", text)
        self.assertIn("date/time", text)
        self.assertIn("Certificate verification remains enabled", text)
        self.assertIn("antivirus program scans HTTPS traffic", text)
        self.assertIn("proxy replaces certificates", text)
        self.assertIn("turn off its HTTPS scanning or add the launcher as an exception", text)
        self.assertRegex(text, "proxy")
        self.assertIn("launcher.log", text)
        self.assertEqual(log.call_count, 2)

    def test_other_errors_are_not_retried(self):
        error = urllib.error.URLError(ConnectionRefusedError())
        with patch.object(fetch.urllib.request, "urlopen", side_effect=error) as op:
            with self.assertRaises(urllib.error.URLError):
                fetch.urlopen("https://github.com/x")
        self.assertEqual(op.call_count, 1)
        http = urllib.error.HTTPError("https://github.com/x", 404, "nf", {}, None)
        with patch.object(fetch.urllib.request, "urlopen", side_effect=http) as op:
            with self.assertRaises(urllib.error.HTTPError):
                fetch.urlopen("https://github.com/x")
        self.assertEqual(op.call_count, 1)
        http.close()

    def test_update_check_reports_the_certificate_problem(self):
        with patch.object(fetch.urllib.request, "urlopen", side_effect=cert_error()), \
                patch("forza_launcher.paths.log_error"):
            with self.assertRaisesRegex(updater.UpdateError, "api.github.com.*date/time"):
                updater.check()


class ExtractTarLinkTests(unittest.TestCase):
    """The LLVM toolchain has symlinks; a data folder that cannot hold them (exFAT, some NTFS
    and network mounts, Windows without the symlink privilege) stopped unpacking with
    "StreamError: seeking backwards is not allowed" (#200)."""

    def archive(self, folder):
        arc = folder / "a.tar.xz"
        with tarfile.open(arc, "w:xz") as tar:
            def add(name, data=None, link=None, hard=False):
                info = tarfile.TarInfo("top/" + name)
                if link:
                    info.type, info.linkname = (tarfile.LNKTYPE if hard else tarfile.SYMTYPE), link
                    tar.addfile(info)
                else:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            add("bin/skipped", b"s" * 4000)
            add("bin/clang-cl", link="clang")   # a link to a link listed after it
            add("bin/clang", link="clang-23")
            add("bin/clang-23", b"c" * 4000)
            add("bin/to-skipped", link="skipped")
            add("bin/clang++", link="clang")
            add("bin/clang-hard", link="top/bin/clang-23", hard=True)
            add("bin/outside", link="../../etc/passwd")
            add("lib/later", b"l" * 4000)
        return arc

    def unpack(self, symlinks, through_symlink=False):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, folder, ignore_errors=True)
        out = folder / "out"
        if through_symlink:  # like /home -> /var/home on Fedora Atomic and Bazzite: one level deeper
            (folder / "var" / "home").mkdir(parents=True)
            os.symlink(folder / "var" / "home", folder / "home")
            out = folder / "home" / "out"
        def refuse(*args, **kwargs):
            raise OSError(1, "Operation not permitted")
        with patch.object(os, "symlink", os.symlink if symlinks else refuse):
            fetch.extract_tar(self.archive(folder), out, strip=1, only=lambda n: Path(n).as_posix() != "bin/skipped")
        return out

    def test_without_symlinks_links_become_copies_and_later_files_still_unpack(self):
        out = self.unpack(symlinks=False)
        self.assertEqual((out / "lib/later").read_bytes(), b"l" * 4000)
        self.assertEqual((out / "bin/clang++").read_bytes(), b"c" * 4000)
        self.assertFalse((out / "bin/clang++").is_symlink())
        self.assertEqual((out / "bin/clang-hard").read_bytes(), b"c" * 4000)
        self.assertEqual((out / "bin/clang-cl").read_bytes(), b"c" * 4000)
        self.assertFalse(os.path.lexists(out / "bin/to-skipped"))
        self.assertFalse(os.path.lexists(out / "bin/outside"))

    def test_with_symlinks_links_stay_links(self):
        # Windows requires Developer Mode or symlink privilege; some temporary
        # filesystems cannot create links either. Keep the copy-fallback test
        # above running on those hosts, and exercise preservation where supported.
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp)
            (probe / "file").write_bytes(b"probe")
            (probe / "directory").mkdir()
            try:
                os.symlink(probe / "file", probe / "file-link")
                os.symlink(probe / "directory", probe / "directory-link",
                           target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Temporary directory cannot create file/directory symlinks: {exc}")
        for through_symlink in (False, True):
            with self.subTest(through_symlink=through_symlink):
                self.check_links(self.unpack(symlinks=True, through_symlink=through_symlink))

    def check_links(self, out):
        self.assertTrue((out / "bin/clang++").is_symlink())
        self.assertEqual(os.readlink(out / "bin/clang++"), "clang")
        self.assertEqual((out / "bin/clang++").read_bytes(), b"c" * 4000)
        self.assertEqual((out / "lib/later").read_bytes(), b"l" * 4000)
        self.assertFalse(os.path.lexists(out / "bin/outside"))


if __name__ == "__main__":
    unittest.main()
