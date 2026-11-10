"""A game and save folder the Flatpak may only read (#717): python3 -m unittest launcher/test_flatpak_folder.py"""
import errno
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import builder, datafolder as df, errors, paths   # noqa: E402

APP = paths.FLATPAK_APP_ID
WINDOWS_WORDS = ("Windows", "OneDrive", "Controlled folder access", "antivirus", "administrator")
EROFS_TEXT = "OSError: [Errno 30] Read-only file system: '/run/media/SD512/FH1'"
POSIX = unittest.skipIf(os.name == "nt", "Flatpak and Linux folders")


class Quiet(builder.Reporter):
    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)


class Detection(unittest.TestCase):
    def test_flatpak_id_or_info_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            none = Path(tmp) / ".flatpak-info"
            self.assertFalse(paths.in_flatpak({}, none))
            self.assertTrue(paths.in_flatpak({"FLATPAK_ID": APP}, none))
            none.write_text("[Application]\nname=" + APP + "\n")
            self.assertTrue(paths.in_flatpak({}, none))
            self.assertFalse(paths.in_flatpak({"FLATPAK_ID": ""}, Path(tmp) / "missing"))

    def test_system(self):
        with mock.patch.object(df, "IS_WINDOWS", True), mock.patch.object(df.paths, "IN_FLATPAK", True):
            self.assertEqual(df.system(), "windows")
        with mock.patch.object(df, "IS_WINDOWS", False), mock.patch.object(df.paths, "IN_FLATPAK", True):
            self.assertEqual(df.system(), "flatpak")
        with mock.patch.object(df, "IS_WINDOWS", False), mock.patch.object(df.paths, "IN_FLATPAK", False):
            self.assertEqual(df.system(), "linux")

    @POSIX
    def test_read_only_mount(self):
        class St:
            f_flag = os.ST_RDONLY
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(df.read_only_mount(Path(tmp) / "not" / "yet"))
            with mock.patch.object(df.os, "statvfs", return_value=St()) as statvfs:
                self.assertTrue(df.read_only_mount(Path(tmp) / "not" / "yet"))
            statvfs.assert_called_once_with(Path(tmp))   # the nearest folder that exists


@POSIX
class Message(unittest.TestCase):
    def flatpak(self, path, detail="", read_only=None):
        with mock.patch.object(df.paths, "FLATPAK_ID", APP):
            return df.blocked_message(path, detail, system_name="flatpak", read_only=read_only)

    def test_flatpak_names_folder_flatseal_and_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "FH1"
            folder.mkdir()
            m = self.flatpak(folder, EROFS_TEXT)
            self.assertIn(f"may only read the folder {folder}", m)
            self.assertIn("Flatseal", m)
            self.assertIn("Filesystem > Other files", m)
            self.assertIn(f"flatpak override --user --filesystem={folder} {APP}", m)
            self.assertIn("close and reopen the launcher", m)
            self.assertIn(df.CHANGE_FOLDER_UI, m)
            self.assertIn("Read-only file system", m)          # what the system said
            for w in WINDOWS_WORDS:
                self.assertNotIn(w, m)

    def test_flatpak_grants_the_parent_of_a_folder_still_to_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self.flatpak(Path(tmp) / "FH1" / "ForzaReImagined", read_only=True)
            self.assertIn(f"--filesystem={tmp} {APP}", m)
            self.assertEqual(df.flatpak_grant_path(Path(tmp) / "a" / "b"), Path(tmp))
            self.assertEqual(df.flatpak_grant_path(tmp), Path(tmp))
        self.assertEqual(df.flatpak_grant_path("/no-such-top-folder-717/x"), Path("/no-such-top-folder-717/x"))

    def test_override_command_quotes_spaces(self):
        with mock.patch.object(df.paths, "FLATPAK_ID", ""):
            self.assertEqual(df.flatpak_override_command("/run/media/deck/My Card"),
                             f"flatpak override --user '--filesystem=/run/media/deck/My Card' {APP}")

    def test_flatpak_without_read_only_also_names_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = self.flatpak(tmp, "PermissionError: [Errno 13] Permission denied", read_only=False)
            self.assertIn("could not write to the folder", m)
            self.assertIn("owner and permissions", m)
            self.assertIn("flatpak override", m)

    def test_read_only_found_from_the_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIn("may only read", self.flatpak(tmp, EROFS_TEXT))

    def test_flatpak_own_folder_is_not_a_permission_problem(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            own = Path(tmp) / ".var" / "app" / APP / "data" / "forza"
            self.assertTrue(df.flatpak_private(own))
            m = self.flatpak(own, "Permission denied", read_only=False)
            self.assertNotIn("flatpak override", m)
            self.assertIn("owner and permissions", m)

    def test_linux(self):
        with tempfile.TemporaryDirectory() as tmp:
            m = df.blocked_message(tmp, EROFS_TEXT, system_name="linux")
            self.assertIn(f"cannot write to the folder {tmp} because its drive is mounted read-only", m)
            self.assertNotIn("Flatpak", m)
            for w in WINDOWS_WORDS:
                self.assertNotIn(w, m)

    def test_check_folder_in_the_flatpak(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(df, "probe_writable", return_value=EROFS_TEXT), \
                mock.patch.object(df, "system", return_value="flatpak"):
            out = df.check_folder(tmp)
        self.assertEqual(out[0][0], "error")
        self.assertTrue(out[0][1].startswith(f"Cannot write to {tmp}: {EROFS_TEXT}."))
        self.assertIn("may only read", out[0][1])
        self.assertNotIn("Windows", out[0][1])

class AllSystems(unittest.TestCase):
    def test_windows_keeps_its_wording(self):
        m = df.blocked_message(r"D:\Games\Forza", "[WinError 5] Access is denied", system_name="windows")
        for w in ("Windows would not let", "OneDrive", "Controlled folder access", "antivirus", "Windows said",
                  df.CHANGE_FOLDER_UI):
            self.assertIn(w, m)
        self.assertNotIn("Flatpak", m)

    def test_errors(self):
        self.assertTrue(df.is_write_blocked_error(OSError(errno.EROFS, "Read-only file system")))
        self.assertTrue(df.is_write_blocked_error(PermissionError(13, "denied")))
        self.assertFalse(df.is_write_blocked_error(FileNotFoundError(2, "x")))

    def test_named_ui_exists(self):
        # CHANGE_FOLDER_UI names the Setup page's card and button: they must be the launcher's own words.
        pages = (Path(__file__).resolve().parent / "forza_launcher" / "ui" / "pages.py").read_text(encoding="utf-8")
        for label in ('label("Setup"', 'QLabel("Game and save folder")', 'QPushButton("Change folder")'):
            self.assertIn(label, pages)
        self.assertEqual(df.CHANGE_FOLDER_UI, 'Setup > "Game and save folder" > Change folder')


class NextStep(unittest.TestCase):
    def test_read_only_in_the_flatpak(self):
        with mock.patch.object(paths, "IN_FLATPAK", True):
            step = errors.next_step(OSError(errno.EROFS, "Read-only file system", "/run/media/x"), "install DLC")
        self.assertIn("Flatpak may only read", step)
        self.assertIn(f"flatpak override --user --filesystem=FOLDER {APP}", step)
        self.assertNotIn("Windows", step)

    def test_read_only_wrapped_as_install_error(self):
        from forza_launcher import installer
        try:
            try:
                raise OSError(errno.EROFS, "Read-only file system", "/run/media/x/game.part")
            except OSError as e:
                raise installer.InstallError(f"Disk error: {e}")
        except installer.InstallError as e:
            wrapped = e
        with mock.patch.object(paths, "IN_FLATPAK", False):
            self.assertIn("read-only drive", errors.next_step(wrapped, "extract game files"))


@POSIX
class Build(unittest.TestCase):
    def test_build_stops_before_it_starts(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(df, "probe_writable", return_value=EROFS_TEXT), \
                mock.patch.object(df, "system", return_value="flatpak"), \
                mock.patch.object(builder, "_run_build", side_effect=AssertionError("the build started")):
            with self.assertRaises(builder.FolderBlocked) as e:
                builder.run_build(builder.Options(Path(tmp)), Quiet(), Path(tmp) / "game")
        self.assertIn("flatpak override", str(e.exception))
        self.assertNotIn("Windows", str(e.exception))

    def test_read_only_during_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            inside = OSError(errno.EROFS, "Read-only file system", str(Path(tmp) / "runtime" / "x"))
            outside = OSError(errno.EROFS, "Read-only file system", "/usr/share/x")
            with mock.patch.object(df, "check_folder", return_value=[]), \
                    mock.patch.object(df, "system", return_value="flatpak"), \
                    mock.patch.object(builder.paths, "log_error"):
                with mock.patch.object(builder, "_run_build", side_effect=inside), \
                        self.assertRaises(builder.FolderBlocked) as e:
                    builder.run_build(builder.Options(Path(tmp)), Quiet(), Path(tmp) / "game")
                self.assertIn("may only read", str(e.exception))
                with mock.patch.object(builder, "_run_build", side_effect=outside), \
                        self.assertRaises(OSError) as e:
                    builder.run_build(builder.Options(Path(tmp)), Quiet(), Path(tmp) / "game")
                self.assertNotIsInstance(e.exception, builder.FolderBlocked)


@POSIX
class Extract(unittest.TestCase):
    def test_extraction_stops_before_it_starts(self):
        from forza_launcher.ui.workers import extract_task
        with tempfile.TemporaryDirectory() as tmp:
            task = extract_task("example.iso", str(Path(tmp) / "game"))
            result = mock.Mock()
            task.done.connect(result)
            with mock.patch("forza_launcher.ui.workers.datafolder.probe_writable", return_value=EROFS_TEXT), \
                    mock.patch.object(df, "system", return_value="flatpak"), \
                    mock.patch("forza_launcher.ui.workers.installer.extract_game",
                               side_effect=AssertionError("extraction started")), \
                    mock.patch("forza_launcher.ui.workers.paths.log_error", return_value="launcher.log"):
                task.run()
        self.assertTrue(task.blocked)
        self.assertFalse(result.call_args.args[0])
        self.assertIn("flatpak override", result.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
