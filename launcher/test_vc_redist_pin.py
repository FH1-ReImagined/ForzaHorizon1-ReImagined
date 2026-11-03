"""The Microsoft Visual C++ redistributable the Windows installer bundles (#497): a versioned
Microsoft download pinned by SHA-256, fetched by ci_fetch.py and signature-checked in release.yml."""
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import fetch, pins

HERE = Path(__file__).resolve().parent
WORKFLOW = HERE.parent / ".github" / "workflows" / "release.yml"


class VcRedistPinTests(unittest.TestCase):
    def test_the_pin_is_a_versioned_microsoft_download_whose_path_names_its_hash(self):
        url, digest, kind = pins.DOWNLOADS["vc-redist-x64"]
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.hostname), ("https", "download.visualstudio.microsoft.com"))
        self.assertTrue(parts.path.endswith("/VC_redist.x64.exe"))
        self.assertEqual(kind, "file")
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn("/" + digest.upper() + "/", parts.path)

    def test_the_release_uses_the_pin_and_checks_microsofts_signature(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("aka.ms/vs/17/release/vc_redist", text)
        self.assertIn("ci_fetch.py vc-redist-x64 vc_redist.x64.exe", text)
        self.assertIn("Get-AuthenticodeSignature -LiteralPath vc_redist.x64.exe", text)
        self.assertIn("O=Microsoft Corporation", text)

    def run_ci_fetch(self, argv, download):
        with patch.object(sys, "argv", ["ci_fetch.py"] + argv), patch.object(fetch, "download", download):
            runpy.run_path(str(HERE / "packaging" / "ci_fetch.py"), run_name="__main__")

    def test_ci_fetch_copies_a_pinned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "VC_redist.x64.exe"
            source.write_bytes(b"MZ")
            dest = Path(directory) / "out" / "vc_redist.x64.exe"
            self.run_ci_fetch(["vc-redist-x64", str(dest)], lambda name, cache, progress: source)
            self.assertEqual(dest.read_bytes(), b"MZ")

    def test_ci_fetch_says_how_to_update_a_pin_that_no_longer_matches(self):
        def mismatch(name, cache, progress):
            raise RuntimeError("VC_redist.x64.exe: the download does not match its pinned checksum.")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(SystemExit) as stop:
            self.run_ci_fetch(["vc-redist-x64", str(Path(directory) / "x.exe")], mismatch)
        self.assertIn("pins.py", str(stop.exception.code))
        self.assertIn("download.visualstudio.microsoft.com", str(stop.exception.code))


if __name__ == "__main__":
    unittest.main()
