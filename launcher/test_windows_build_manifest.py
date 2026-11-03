"""Installed payloads can be identified even when Defender removes one file."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).parent / "packaging" / "windows_build_manifest.py"
spec = importlib.util.spec_from_file_location("windows_build_manifest", SCRIPT)
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)
BUILD = {"version": "0.1.34", "commit": "a" * 40,
         "run_url": "https://github.com/FH1-ReImagined/ForzaHorizon1-ReImagined/actions/runs/123"}


class WindowsBuildManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name) / "stage"
        self.stage.mkdir()
        (self.stage / "forza-launcher.exe").write_bytes(b"abc")
        internal = self.stage / "_internal"
        internal.mkdir()
        (internal / "bundled script.py").write_bytes(b"print('fixture')\n")

    def test_cli_records_final_bytes_relative_paths_and_provenance(self):
        subprocess.run([sys.executable, str(SCRIPT), str(self.stage),
                        "--version", BUILD["version"], "--commit", BUILD["commit"],
                        "--run-url", BUILD["run_url"]], check=True, capture_output=True)
        raw = (self.stage / manifest.MANIFEST).read_bytes()
        report = json.loads(raw)
        self.assertNotIn(str(self.stage).encode(), raw)
        self.assertNotIn(b"\r", raw)
        self.assertEqual(report["source_commit"], BUILD["commit"])
        self.assertEqual(report["workflow_run"], BUILD["run_url"])
        self.assertEqual([f["path"] for f in report["files"]],
                         ["_internal/bundled script.py", "forza-launcher.exe"])
        launcher = report["files"][1]
        self.assertEqual(launcher["size"], 3)
        self.assertEqual(launcher["sha256"],
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_rerun_excludes_manifest_and_records_modified_payload(self):
        path = manifest.write_manifest(self.stage, **BUILD)
        first = path.read_bytes()
        self.assertEqual(manifest.write_manifest(self.stage, **BUILD).read_bytes(), first)
        (self.stage / "forza-launcher.exe").write_bytes(b"new final bytes")
        report = json.loads(manifest.write_manifest(self.stage, **BUILD).read_text())
        self.assertEqual(len(report["files"]), 2)
        self.assertEqual(report["files"][1]["sha256"], hashlib.sha256(b"new final bytes").hexdigest())

    def test_missing_launcher_or_bad_provenance_preserves_old_manifest(self):
        path = manifest.write_manifest(self.stage, **BUILD)
        before = path.read_bytes()
        for build in ({**BUILD, "commit": "main"},
                      {**BUILD, "run_url": "https://example.com/private?token=secret"}):
            with self.assertRaises(ValueError):
                manifest.write_manifest(self.stage, **build)
            self.assertEqual(path.read_bytes(), before)
        (self.stage / "forza-launcher.exe").unlink()
        with self.assertRaisesRegex(ValueError, "launcher is missing"):
            manifest.write_manifest(self.stage, **BUILD)
        self.assertEqual(path.read_bytes(), before)

    def test_records_versions_without_requiring_optional_packages(self):
        def version(name):
            if name == "pyinstaller":
                return "6.22.3"
            raise manifest.metadata.PackageNotFoundError(name)
        with patch.object(manifest.metadata, "version", side_effect=version):
            self.assertEqual(manifest.build_manifest(self.stage, **BUILD)["build_packages"],
                             {"pyinstaller": "6.22.3"})

    def test_rejects_symlinks_without_hashing_external_files(self):
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("not part of this package")
        link = self.stage / "linked.txt"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "Linked package entry"):
            manifest.build_manifest(self.stage, **BUILD)


if __name__ == "__main__":
    unittest.main()
