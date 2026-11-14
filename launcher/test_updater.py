"""Release selection and download integrity, without accessing the network."""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import GITHUB_URL, fetch, paths, updater


def setup_name(version, suffix=""):
    return f"ForzaHorizon1-ReImagined-Setup-{version}.exe{suffix}"


def put(folder, *parts):
    path = Path(folder).joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"setup")
    return path


def release_data():
    names = ["ForzaHorizon1-ReImagined-Setup-0.1.8.exe",
             "ForzaHorizon1-ReImagined-linux-x86_64.tar.gz",
             "ForzaHorizon1-ReImagined-x86_64.flatpak"]
    return {"tag_name": "v0.1.8", "assets": [
        {"name": name, "browser_download_url": GITHUB_URL + "/releases/download/v0.1.8/" + name,
         "digest": "sha256:" + hashlib.sha256(b"update").hexdigest(), "size": 6} for name in names]}


class UpdateTests(unittest.TestCase):
    def test_versions_do_not_downgrade_or_compare_development_builds(self):
        r = updater.parse_release(release_data())
        for current in ("0.1.7", "v0.1.7", "0.1.7+build.3"):
            self.assertTrue(r.newer_than(current))
        for current in ("0.1.8", "0.1.9", "0.10.0", "0.0.0-dev.abc", "dev", "0.1.8-rc.1"):
            self.assertFalse(r.newer_than(current))
        for tag in ("nightly", "v0.1.9-beta", "../../tmp", "v01.2.3"):
            with self.subTest(tag=tag), self.assertRaises(updater.UpdateError):
                updater.parse_release({"tag_name": tag})
        for flag in ("draft", "prerelease"):
            with self.assertRaises(updater.UpdateError):
                updater.parse_release({**release_data(), flag: True})

    def test_platform_selection(self):
        r = updater.parse_release(release_data())
        self.assertTrue(r.asset_for("win32", machine="AMD64").name.endswith(".exe"))
        self.assertTrue(r.asset_for("linux").name.endswith(".tar.gz"))
        self.assertTrue(r.asset_for("linux", flatpak=True).name.endswith(".flatpak"))
        self.assertIsNone(r.asset_for("darwin"))
        self.assertIsNone(r.asset_for("linux", machine="aarch64"))

    def test_untrusted_or_unverified_assets_are_not_offered(self):
        for key, value in (("name", "../bad.exe"), ("browser_download_url", "https://evil.test/update"),
                           ("digest", None), ("digest", "sha256:no"), ("size", -1), ("size", True)):
            data = release_data()
            data["assets"][0][key] = value
            with self.subTest(key=key, value=value):
                self.assertIsNone(updater.parse_release(data).asset_for("win32"))

    def test_api_errors_and_timeout(self):
        for code, message in ((403, "limit"), (429, "limit"), (404, "No published"), (500, "HTTP 500")):
            error = urllib.error.HTTPError(updater.API_URL, code, "error", {}, None)
            with patch.object(updater.urllib.request, "urlopen", side_effect=error), \
                    self.assertRaisesRegex(updater.UpdateError, message):
                updater.check()
            error.close()
        with patch.object(updater.urllib.request, "urlopen", side_effect=TimeoutError()), \
                self.assertRaisesRegex(updater.UpdateError, "connection"):
            updater.check()
        with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(release_data()).encode())) as op:
            self.assertEqual(updater.check().tag, "v0.1.8")
            self.assertEqual(op.call_args.kwargs["timeout"], updater.TIMEOUT)
            self.assertIs(op.call_args.kwargs["context"], fetch.ssl_context())

    def test_download_integrity_cancel_and_retry(self):
        asset = updater.parse_release(release_data()).asset_for("win32")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for body in (b"bad", b"longer than expected", b"broken"):
                with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(body)), \
                        self.assertRaises(updater.UpdateError):
                    updater.download(asset, folder)
                self.assertFalse((folder / asset.name).exists())
                self.assertEqual(list(folder.iterdir()), [])
            with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(b"update")), \
                    self.assertRaises(fetch.Cancelled):
                checks = iter([False, False, True])
                updater.download(asset, folder, cancelled=lambda: next(checks))
            self.assertEqual(list(folder.iterdir()), [])
            with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(b"update")) as op:
                path = updater.download(asset, folder)
                self.assertIs(op.call_args.kwargs["context"], fetch.ssl_context())
            self.assertEqual(path.read_bytes(), b"update")
            with patch.object(updater.urllib.request, "urlopen") as op:
                self.assertEqual(updater.download(asset, folder), path)
                op.assert_not_called()
            path.write_bytes(b"broken")
            with patch.object(updater.urllib.request, "urlopen", return_value=io.BytesIO(b"update")):
                self.assertEqual(updater.download(asset, folder).read_bytes(), b"update")


class DownloadCleanupTests(unittest.TestCase):
    """#865: the Setup the launcher downloaded is removed once its version is installed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # As on Windows: %APPDATA% under a profile with a space and a non-ASCII letter.
        self.appdata = Path(self.temp.name) / "Users" / "Jöhn Doe" / "AppData" / "Roaming"
        self.updates = self.appdata / "ForzaReImagined" / "updates"

    def test_downloads_live_in_the_roaming_config_folder_on_windows(self):
        with patch.object(paths, "IS_WINDOWS", True), patch.dict(os.environ, {"APPDATA": str(self.appdata)}):
            self.assertEqual(updater.downloads_dir(), self.updates)
            self.assertEqual(updater.downloads_dir(), paths.config_dir() / "updates")

    def test_installed_and_older_setups_are_removed(self):
        installed = put(self.updates, "v0.3.2", setup_name("0.3.2"))
        older = [put(self.updates, "v0.3.0", setup_name("0.3.0")),
                 put(self.updates, "v0.2.10", setup_name("0.2.10")),
                 put(self.updates, "v0.3.1", setup_name("0.3.1", ".part"))]   # an interrupted download
        with patch.object(paths, "IS_WINDOWS", True), patch.dict(os.environ, {"APPDATA": str(self.appdata)}):
            removed, kept = updater.cleanup_downloads(current="0.3.2")
        self.assertEqual(sorted(removed), sorted([installed, *older]))
        self.assertEqual(kept, [])
        for path in (installed, *older):
            self.assertFalse(path.exists())
        self.assertEqual(list(self.updates.iterdir()), [])   # the emptied tag folders go too
        self.assertTrue(self.updates.is_dir())

    def test_only_our_own_setup_files_are_touched(self):
        removed_one = put(self.updates, "v0.3.0", setup_name("0.3.0"))
        others = [
            put(self.updates, "v0.3.0", "notes.txt"),                                  # not ours
            put(self.updates, "v0.3.0", "ForzaHorizon1-ReImagined-linux-x86_64.tar.gz"),
            # Not our exact name. Its own folder: on Windows it would be the same file as the Setup above.
            put(self.updates, "v0.2.8", "forzahorizon1-reimagined-setup-0.2.8.exe"),
            put(self.updates, "v0.3.0", setup_name("0.3.0", ".bak")),
            put(self.updates, "v0.3.1", setup_name("0.2.9")),           # not in its own tag's folder
            put(self.updates, "notes", setup_name("0.1.0")),            # not a tag folder
            put(self.updates, setup_name("0.1.0")),                     # loose in the updates folder
            put(self.appdata, "ForzaReImagined", setup_name("0.1.0")),  # outside the updates folder
            put(self.appdata, setup_name("0.1.0")),                     # a manual download in Roaming
        ]
        (self.updates / "v0.2.0" / setup_name("0.2.0")).mkdir(parents=True)   # a folder with that name
        target = put(self.temp.name, "elsewhere", setup_name("0.2.1"))
        link = self.updates / "v0.2.1" / setup_name("0.2.1")
        link.parent.mkdir()
        try:
            link.symlink_to(target)
        except OSError:   # Windows without the symlink privilege
            link = None
        removed, kept = updater.cleanup_downloads(self.updates, current="0.3.2")
        self.assertEqual(removed, [removed_one])
        for path in others:
            self.assertTrue(path.is_file(), path)
        self.assertTrue((self.updates / "v0.2.0" / setup_name("0.2.0")).is_dir())
        self.assertTrue(target.is_file())
        if link is not None:
            self.assertTrue(link.is_symlink())

    def test_a_setup_still_needed_is_kept(self):
        # The player downloaded v0.3.3 on 0.3.2 and its Setup failed or was cancelled (or is still
        # running): the launcher that starts is still 0.3.2, so that Setup stays for the retry.
        pending = put(self.updates, "v0.3.3", setup_name("0.3.3"))
        newer = put(self.updates, "v0.4.0", setup_name("0.4.0", ".part"))
        ran = put(self.updates, "v0.3.2", setup_name("0.3.2"))
        removed, kept = updater.cleanup_downloads(self.updates, current="0.3.2")
        self.assertEqual(removed, [ran])
        self.assertTrue(pending.is_file())
        self.assertTrue(newer.is_file())
        # After the retry installs 0.3.3, the next start removes it.
        self.assertEqual(updater.cleanup_downloads(self.updates, current="0.3.3")[0], [pending])
        self.assertTrue(newer.is_file())

    def test_development_builds_and_missing_folders_remove_nothing(self):
        setup = put(self.updates, "v0.3.0", setup_name("0.3.0"))
        for current in ("dev", "0.0.0-dev.abc", "0.3.4-rc.1"):
            self.assertEqual(updater.cleanup_downloads(self.updates, current=current), ([], []))
        self.assertTrue(setup.is_file())
        self.assertEqual(updater.cleanup_downloads(self.updates / "missing", current="0.3.4"), ([], []))
        with patch.object(updater, "__version__", "0.3.4"):   # the running launcher's version by default
            self.assertEqual(updater.cleanup_downloads(self.updates)[0], [setup])

    def test_a_setup_still_open_is_kept_for_the_next_start(self):
        # Setup starts the new launcher while its own window is still open, and Windows refuses to
        # delete an open file: that Setup is kept and removed on a later run.
        setup = put(self.updates, "v0.3.2", setup_name("0.3.2"))
        real_unlink = Path.unlink

        def in_use(path, *args, **kwargs):
            if path == setup:
                raise PermissionError(13, "The process cannot access the file", str(path))
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", in_use):
            removed, kept = updater.cleanup_downloads(self.updates, current="0.3.2")
        self.assertEqual((removed, kept), ([], [setup]))
        self.assertTrue(setup.is_file())
        self.assertEqual(updater.cleanup_downloads(self.updates, current="0.3.2"), ([setup], []))
        self.assertFalse(setup.parent.exists())


if __name__ == "__main__":
    unittest.main()
