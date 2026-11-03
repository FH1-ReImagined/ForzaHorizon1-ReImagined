"""Download-integrity diagnostics and resumable cancellation without a network."""
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import errors, fetch


class Response(io.BytesIO):
    status = 200

    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class DownloadDiagnosticsTests(unittest.TestCase):
    def test_network_failure_names_resource_without_changing_exception_type(self):
        for error in (HTTPError("https://example.invalid/sdk.zip", 404, "missing", {}, None),
                      URLError(TimeoutError("download timed out"))):
            with tempfile.TemporaryDirectory() as directory, \
                    patch.dict(fetch.pins.DOWNLOADS, {"test-diagnostic": ("https://example.invalid/sdk.zip", "a" * 64, "zip")}), \
                    patch.object(fetch, "urlopen", side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    fetch.download("test-diagnostic", Path(directory))
                self.assertIs(caught.exception, error)
                message = errors.message(error, "build", "launcher.log")
                self.assertIn("sdk.zip", message)
                self.assertIn("example.invalid", message)

    def test_checksum_failure_names_file_and_both_hashes_without_deleting_other_cache(self):
        expected = hashlib.sha256(b"expected").hexdigest()
        actual = hashlib.sha256(b"incorrect").hexdigest()
        with tempfile.TemporaryDirectory() as directory, Response(b"incorrect") as response:
            cache = Path(directory)
            other = cache / "other.zip"
            other.write_bytes(b"kept")
            with patch.dict(fetch.pins.DOWNLOADS, {"test-diagnostic": ("https://example.invalid/sdk.zip", expected, "zip")}), \
                    patch.object(fetch, "urlopen", return_value=response), self.assertRaises(RuntimeError) as caught:
                fetch.download("test-diagnostic", cache)
            self.assertIn("sdk.zip", str(caught.exception))
            self.assertIn("Expected SHA-256 " + expected, str(caught.exception))
            self.assertIn("actual SHA-256 " + actual, str(caught.exception))
            self.assertFalse((cache / "sdk.zip.part").exists())
            self.assertFalse((cache / "sdk.zip").exists())
            self.assertEqual(other.read_bytes(), b"kept")

    def test_valid_download_still_publishes_and_reuses_verified_cache(self):
        data = b"valid download"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory, Response(data) as response, \
                patch.dict(fetch.pins.DOWNLOADS, {"test-diagnostic": ("https://example.invalid/sdk.zip", digest, "zip")}), \
                patch.object(fetch, "urlopen", return_value=response) as op:
            cached = fetch.download("test-diagnostic", Path(directory))
            self.assertEqual(cached.read_bytes(), data)
            self.assertEqual(fetch.download("test-diagnostic", Path(directory)), cached)
            self.assertEqual(op.call_count, 1)

    def test_cancelled_download_keeps_partial_for_resume(self):
        data = b"a" * (1 << 20) + b"b"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as directory, Response(data) as response, \
                patch.dict(fetch.pins.DOWNLOADS, {"test-diagnostic": ("https://example.invalid/sdk.zip", digest, "zip")}), \
                patch.object(fetch, "urlopen", return_value=response):
            cache = Path(directory)
            calls = iter((False, True))
            with self.assertRaises(fetch.Cancelled):
                fetch.download("test-diagnostic", cache, cancelled=lambda: next(calls))
            self.assertEqual((cache / "sdk.zip.part").read_bytes(), data[:1 << 20])
            self.assertFalse((cache / "sdk.zip").exists())


if __name__ == "__main__":
    unittest.main()
