"""Exercise actual SDK staging/cache decisions with fake downloads and disposable files."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import builder as b  # noqa: E402


HELPER = "lib/cmake/rexglue/rexglue_helpers.cmake"


class OverlayHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.dev, self.data = self.root / "dev", self.root / "data"
        self.prefix = self.data / "sdk" / "linux"
        for rel in ("bin/rexglue", "lib/librexgpu-xenos.so", "lib/librexruntime.so", HELPER):
            p = self.dev / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("new helper" if rel == HELPER else "fake SDK")
        target = self.prefix / HELPER
        target.parent.mkdir(parents=True)
        target.write_text("nightly helper")
        self.marker = self.prefix / ".forza-sdk-ready"
        self.marker.write_text(f"{b.pins.SDK_TAG}|{self.dev}")
        self.download = Mock(return_value=self.root / "fake.zip")
        for name, value in (("IS_WIN", False), ("sdk_dir", Mock(return_value=self.prefix)),
                            ("sdk_overlay_archive", Mock(return_value=None)),
                            ("dev_sdk_overlay", Mock(return_value=self.dev))):
            p = patch.object(b, name, value)
            p.start()
            self.addCleanup(p.stop)
        for name, value in (("download", self.download), ("extract_zip", self.unpack)):
            p = patch.object(b.fetch, name, value)
            p.start()
            self.addCleanup(p.stop)

    def unpack(self, archive, stage, **kwargs):
        (stage / "lib/cmake/rexglue").mkdir(parents=True)
        (stage / "bin").mkdir()
        (stage / HELPER).write_text("nightly helper")

    def ensure(self):
        return b.ensure_sdk(b.Options(self.data, jobs=1), Mock(), 0, 1)

    def test_cached_legacy_prefix_gets_development_helper(self):
        self.assertEqual(self.ensure(), self.prefix)
        self.assertEqual((self.prefix / HELPER).read_text(), "new helper")
        self.download.assert_called_once()

    def test_unchanged_helper_reuses_staged_sdk(self):
        self.ensure()
        self.download.reset_mock()
        self.ensure()
        self.download.assert_not_called()

    def test_same_size_helper_edit_refreshes_sdk_and_configure_stamp(self):
        self.ensure()
        before = b.sdk_identity(None, self.dev)
        tools = SimpleNamespace(cc="cc", cxx="cxx", cmake="cmake", ninja="ninja", source={})
        stamp = b.configure_stamp(tools, before, True)
        (self.dev / HELPER).write_text("alt helper")  # Same size; a size-only identity is stale.
        self.download.reset_mock()
        self.ensure()
        after = b.sdk_identity(None, self.dev)
        self.assertNotEqual(before, after)
        self.assertNotEqual(stamp, b.configure_stamp(tools, after, True))
        self.assertEqual((self.prefix / HELPER).read_text(), "alt helper")
        self.download.assert_called_once()

    def test_legacy_overlay_without_helper_keeps_compatible_identity(self):
        (self.dev / HELPER).unlink()
        self.ensure()
        self.download.assert_not_called()
        self.assertEqual((self.prefix / HELPER).read_text(), "nightly helper")

    def test_first_staging_legacy_overlay_uses_nightly_helper(self):
        (self.dev / HELPER).unlink()
        self.marker.unlink()
        self.ensure()
        self.download.assert_called_once()
        self.assertEqual((self.prefix / HELPER).read_text(), "nightly helper")

    def test_packaged_overlay_identity_takes_precedence(self):
        archive = self.root / "overlay.tar.gz"
        archive.write_bytes(b"fake archive")
        before = b.sdk_identity(archive, self.dev)
        (self.dev / HELPER).write_text("changed helper")
        self.assertEqual(before, b.sdk_identity(archive, self.dev))


if __name__ == "__main__":
    unittest.main()
