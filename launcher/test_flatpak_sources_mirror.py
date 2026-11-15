"""The Flatpak sources mirror helper (flatpak-sources-mirror.yml) without the network or gh."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HELPER = Path(__file__).parent / "packaging/flatpak_sources_mirror.py"
spec = importlib.util.spec_from_file_location("flatpak_sources_mirror", HELPER)
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)

A = hashlib.sha256(b"a").hexdigest()
B = hashlib.sha256(b"b").hexdigest()
BASE = mirror.MIRROR_BASE

MANIFEST = f"""\
modules:
  - name: one
    sources:
      - type: archive
        url: https://example.org/dist/one-1.0.tar.gz  # a comment
        mirror-urls:
          - {BASE}one-1.0.tar.gz
        sha256: {A}
  - name: two
    buildsystem: simple
    sources:
      - type: file
        path: helper.py
      - type: file
        url: https://example.org/p/two-2.0-py3-none-any.whl
        mirror-urls: [{BASE}two-2.0-py3-none-any.whl]
        sha256: '{B}'
        only-arches: [x86_64]
      - type: dir
        path: stage
"""


class ParseTests(unittest.TestCase):
    def test_reads_the_downloaded_sources_only(self):
        sources = mirror.parse_sources(MANIFEST)
        self.assertEqual([s["url"] for s in sources],
                         ["https://example.org/dist/one-1.0.tar.gz",
                          "https://example.org/p/two-2.0-py3-none-any.whl"])
        self.assertEqual(sources[0]["mirror-urls"], [BASE + "one-1.0.tar.gz"])
        self.assertEqual(sources[1]["mirror-urls"], [BASE + "two-2.0-py3-none-any.whl"])
        self.assertEqual(sources[1]["sha256"], B)
        self.assertEqual(mirror.problems(sources), [])
        self.assertEqual(mirror.pinned(sources), {"one-1.0.tar.gz": A, "two-2.0-py3-none-any.whl": B})

    def test_names_a_source_without_its_mirror(self):
        text = MANIFEST.replace(f"        mirror-urls:\n          - {BASE}one-1.0.tar.gz\n", "")
        found = mirror.problems(mirror.parse_sources(text))
        self.assertEqual(len(found), 1)
        self.assertIn(f"- {BASE}one-1.0.tar.gz", found[0])

    def test_names_a_wrong_mirror_and_a_missing_sha256(self):
        text = MANIFEST.replace(f"{BASE}two-2.0", f"{BASE}three-2.0").replace(f"        sha256: {A}\n", "")
        found = mirror.problems(mirror.parse_sources(text))
        self.assertEqual(len(found), 2)
        self.assertIn("no sha256", found[0])
        self.assertIn("two-2.0-py3-none-any.whl", found[1])

    def test_two_sources_with_one_file_name_and_other_bytes(self):
        text = MANIFEST.replace("p/two-2.0-py3-none-any.whl", "dist/one-1.0.tar.gz") \
                       .replace("two-2.0-py3-none-any.whl]", "one-1.0.tar.gz]")
        found = mirror.problems(mirror.parse_sources(text))
        self.assertTrue(any("same file name" in f for f in found), found)

    def test_a_git_source_cant_be_mirrored(self):
        text = MANIFEST.replace("type: archive", "type: git")
        self.assertIn("git source", mirror.problems(mirror.parse_sources(text))[0])

    def test_the_real_manifest_passes(self):
        sources = mirror.parse_sources(mirror.MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(mirror.problems(sources), [])
        self.assertGreaterEqual(len(sources), 8)
        self.assertIn("krb5-1.22.3.tar.gz", mirror.pinned(sources))

    def test_the_real_manifest_reads_the_same_with_a_yaml_parser(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        data = yaml.safe_load(mirror.MANIFEST.read_text(encoding="utf-8"))
        expected = [(s["url"], s["sha256"], s.get("mirror-urls", []))
                    for module in data["modules"] for s in module.get("sources", []) if "url" in s]
        self.assertEqual([(s["url"], s["sha256"], s["mirror-urls"])
                          for s in mirror.parse_sources(mirror.MANIFEST.read_text(encoding="utf-8"))], expected)


class ReleaseTests(unittest.TestCase):
    """status and upload against a fake gh holding a release in memory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.manifest = self.work / "manifest.yml"
        self.manifest.write_text(MANIFEST)
        self.release = None
        self.uploads = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_gh(self, *args, check=True):
        result = mock.Mock(returncode=0, stdout="", stderr="")
        if args[0] == "api":
            if self.release is None:
                result.returncode, result.stdout = 1, "Not Found"
            else:
                result.stdout = json.dumps(self.release)
        elif args[:2] == ("release", "create"):
            self.release = {"prerelease": "--prerelease" in args, "assets": []}
        elif args[:2] == ("release", "upload"):
            path = Path(args[3])
            data = path.read_bytes()
            self.release["assets"] = [a for a in self.release["assets"] if a["name"] != path.name]
            self.release["assets"].append({"name": path.name, "data": data.decode(),
                                           "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
            self.uploads.append(path.name)
        elif args[:2] == ("release", "download"):
            name = args[args.index("--pattern") + 1]
            out = Path(args[args.index("--dir") + 1])
            asset = next(a for a in self.release["assets"] if a["name"] == name)
            (out / name).write_text(asset["data"])
        return result

    def fetched(self):
        out = self.work / "out"
        out.mkdir()
        (out / "one-1.0.tar.gz").write_bytes(b"a")
        (out / "two-2.0-py3-none-any.whl").write_bytes(b"b")
        (out / mirror.SUMS).write_text(mirror.common.format_sums(
            {"one-1.0.tar.gz": A, "two-2.0-py3-none-any.whl": B}))
        return out

    def test_upload_creates_the_prerelease_then_status_is_complete(self):
        with mock.patch.object(mirror.common, "gh", self.fake_gh):
            self.assertEqual(mirror.cmd_status("o/r", "t", self.manifest), mirror.INCOMPLETE)
            mirror.cmd_upload(self.fetched(), "o/r", "t", "")
            self.assertTrue(self.release["prerelease"])
            self.assertEqual(sorted(self.uploads),
                             [mirror.SUMS, "one-1.0.tar.gz", "two-2.0-py3-none-any.whl"])
            self.assertEqual(mirror.cmd_status("o/r", "t", self.manifest), 0)

    def test_upload_never_replaces_other_bytes(self):
        self.release = {"prerelease": True, "assets": [
            {"name": "one-1.0.tar.gz", "data": "x", "digest": "sha256:" + hashlib.sha256(b"x").hexdigest()}]}
        with mock.patch.object(mirror.common, "gh", self.fake_gh):
            with self.assertRaises(SystemExit) as raised:
                mirror.cmd_upload(self.fetched(), "o/r", "t", "")
            self.assertIn("one-1.0.tar.gz", str(raised.exception))
            self.assertEqual(self.uploads, [])
            with self.assertRaises(SystemExit):
                mirror.cmd_status("o/r", "t", self.manifest)

    def test_fetch_stops_on_bytes_the_manifest_doesnt_pin(self):
        def fake_download(url, dest, attempts=3):
            Path(dest).write_bytes(b"a" if url.endswith(".tar.gz") else b"not b")

        with mock.patch.object(mirror.common, "download", fake_download):
            with self.assertRaises(SystemExit) as raised:
                mirror.cmd_fetch(self.work / "out", self.manifest)
        self.assertIn("two-2.0-py3-none-any.whl", str(raised.exception))
        self.assertFalse((self.work / "out" / "two-2.0-py3-none-any.whl").exists())

    def test_fetch_writes_the_sums(self):
        def fake_download(url, dest, attempts=3):
            Path(dest).write_bytes(b"a" if url.endswith(".tar.gz") else b"b")

        with mock.patch.object(mirror.common, "download", fake_download):
            mirror.cmd_fetch(self.work / "out", self.manifest)
        self.assertEqual(mirror.common.parse_sums((self.work / "out" / mirror.SUMS).read_text()),
                         {"one-1.0.tar.gz": A, "two-2.0-py3-none-any.whl": B})


if __name__ == "__main__":
    unittest.main()
