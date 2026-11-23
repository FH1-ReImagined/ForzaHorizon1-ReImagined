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

    def test_a_source_whatever_key_it_starts_with(self):
        # url first, then sha256 first: still sources, read the same as written type first.
        for first in ("url", "sha256"):
            with self.subTest(first=first):
                text = self.reordered(first)
                sources = mirror.parse_sources(text)
                self.assertEqual([s["url"] for s in sources],
                                 ["https://example.org/dist/one-1.0.tar.gz",
                                  "https://example.org/p/two-2.0-py3-none-any.whl"])
                self.assertEqual(sources[0]["type"], "archive")
                self.assertEqual(sources[0]["mirror-urls"], [BASE + "one-1.0.tar.gz"])
                self.assertEqual(mirror.problems(sources), [])
                self.assert_reads_like_yaml(text)

    def test_names_a_source_without_its_mirror_whatever_key_it_starts_with(self):
        for first in ("type", "url", "sha256"):
            with self.subTest(first=first):
                text = self.reordered(first).replace(f"        mirror-urls:\n          - {BASE}one-1.0.tar.gz\n", "")
                self.assertNotIn(f"{BASE}one-1.0", text)
                found = mirror.problems(mirror.parse_sources(text))
                self.assertEqual(len(found), 1, found)
                self.assertIn("(https://example.org/dist/one-1.0.tar.gz): needs", found[0])
                self.assertIn(f"- {BASE}one-1.0.tar.gz", found[0])

    def test_lists_at_the_keys_indent_and_nested_mappings(self):
        text = MANIFEST.replace(f"        mirror-urls:\n          - {BASE}one-1.0.tar.gz\n",
                                f"        mirror-urls:\n        - {BASE}one-1.0.tar.gz\n"
                                "        x-checker-data:\n          type: anitya\n"
                                "          url: https://example.org/releases.json\n")
        sources = mirror.parse_sources(text)
        self.assertEqual(sources[0]["mirror-urls"], [BASE + "one-1.0.tar.gz"])
        self.assertEqual(mirror.problems(sources), [])
        self.assert_reads_like_yaml(text)

    def test_a_url_it_cant_place_stops_the_run(self):
        for text in (MANIFEST.replace("      - type: dir\n", "      - {type: file, url: https://example.org/x.whl}\n      - type: dir\n"),
                     MANIFEST.replace("  - name: two\n", "  - name: two\n    url: https://example.org/x.whl\n"),
                     MANIFEST.replace("        url: https://example.org/dist", "         url: https://example.org/dist")):
            with self.subTest(text=text):
                with self.assertRaises(SystemExit):
                    mirror.parse_sources(text)

    def test_a_flow_style_sources_list_stops_the_run(self):
        # Valid YAML for flatpak-builder, but the check reads block lists only: a source written
        # this way must stop the run rather than pass without its mirror.
        flow = (f"    sources: [{{type: file, url: https://example.org/x.whl, sha256: {A}}}]\n",
                "    sources: [\n"
                f"      {{type: file, url: https://example.org/x.whl, sha256: {A}}}\n"
                "    ]\n",
                "    sources:\n"
                f"      [{{type: file, url: https://example.org/x.whl, sha256: {A}}}]\n",
                "    sources: [{\n"
                "        type: file,\n"
                "        url: https://example.org/x.whl,\n"
                f"        sha256: {A}\n"
                "      }]\n",
                f"    sources: [{{sha256: {A}, type: file,\n"
                "               url: https://example.org/x.whl}]\n")
        for sources in flow:
            text = MANIFEST + "  - name: three\n    buildsystem: simple\n" + sources
            with self.subTest(sources=sources):
                try:
                    import yaml
                    module = yaml.safe_load(text)["modules"][-1]
                    self.assertEqual(module["sources"][0]["url"], "https://example.org/x.whl")
                except ImportError:
                    pass
                with self.assertRaises(SystemExit) as raised:
                    mirror.parse_sources(text)
                self.assertIn("line ", str(raised.exception))

    def test_an_empty_flow_list_and_flow_lists_inside_a_source_are_fine(self):
        text = MANIFEST + "  - name: three\n    buildsystem: simple\n    sources: []\n"
        self.assertEqual(len(mirror.parse_sources(text)), 2)
        self.assert_reads_like_yaml(text)

    @staticmethod
    def reordered(first):
        """MANIFEST with each downloaded source's `first` key moved to the start of its item."""
        lines, out = MANIFEST.splitlines(), []
        i = 0
        while i < len(lines):
            if lines[i].startswith("      - type: ") and i + 1 < len(lines) and "url:" in lines[i + 1]:
                block = [lines[i][8:]]
                i += 1
                while i < len(lines) and lines[i].startswith("        "):
                    block.append(lines[i][8:])
                    i += 1
                keys = [n for n, l in enumerate(block) if not l.startswith(" ")]
                at = next(n for n in keys if block[n].startswith(first + ":"))
                end = next((n for n in keys if n > at), len(block))
                block = block[at:end] + block[:at] + block[end:]
                out += ["      - " + block[0]] + ["        " + l for l in block[1:]]
            else:
                out.append(lines[i])
                i += 1
        return "\n".join(out) + "\n"

    def assert_reads_like_yaml(self, text):
        try:
            import yaml
        except ImportError:
            return
        data = yaml.safe_load(text)
        expected = [(s["type"], s["url"], s["sha256"], s.get("mirror-urls", []))
                    for module in data["modules"] for s in module.get("sources", []) if "url" in s]
        self.assertEqual([(s["type"], s["url"], s["sha256"], s["mirror-urls"])
                          for s in mirror.parse_sources(text)], expected)

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
