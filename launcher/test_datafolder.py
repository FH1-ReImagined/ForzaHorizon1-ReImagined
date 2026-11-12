"""The data folder (issue #38): python3 -m unittest launcher/test_datafolder.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import datafolder as df   # noqa: E402


def touch(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class Detect(unittest.TestCase):
    def test_onedrive_by_environment(self):
        r = df.in_onedrive(r"C:\Users\ann\OneDrive\Documents\forza", env_roots=[r"C:\Users\ann\OneDrive"], attrs=lambda p: None)
        self.assertIn("OneDrive", r)
        r = df.in_onedrive(r"c:\users\ANN\onedrive - Corp\Documents", env_roots=[r"C:\Users\ann\OneDrive - Corp"], attrs=lambda p: None)
        self.assertIsNotNone(r)

    def test_onedrive_by_name(self):
        self.assertIsNotNone(df.in_onedrive(r"C:\Users\ann\OneDrive - Contoso\Documents\forza", env_roots=[], attrs=lambda p: None))

    def test_not_onedrive(self):
        self.assertIsNone(df.in_onedrive(r"C:\Users\ann\AppData\Local\ForzaReImagined", env_roots=[r"C:\Users\ann\OneDrive"],
                                         attrs=lambda p: None))
        self.assertIsNone(df.in_onedrive(r"D:\Games\Forza", env_roots=[], attrs=lambda p: None))

    def test_onedrive_by_attribute(self):
        def attrs(p):       # the Documents folder is a placeholder; the rest are plain
            return df.ATTR_RECALL_ON_DATA_ACCESS if p.lower().endswith("documents") else 0x10
        r = df.in_onedrive(r"C:\Users\ann\Documents\forza", env_roots=[], attrs=attrs)
        self.assertIn("cloud-synced", r)

    def test_access_denied_text(self):
        for t in ["ninja: error: rebuilding 'build.ninja': GetFileAttributesEx(C:/Users/x/Documents/forza/sdk/cmake/SDL3Config.cmake): GetLastError() = 5",
                  "error: Access is denied.", "fatal error: cannot open 'x.h': Permission denied", "[WinError 5] Access is denied"]:
            self.assertTrue(df.ACCESS_DENIED_RE.search(t), t)
        self.assertFalse(df.ACCESS_DENIED_RE.search("GetLastError() = 50"))
        self.assertFalse(df.ACCESS_DENIED_RE.search("[12/300] Building CXX object x.o"))

    def test_blocked_message_names_the_causes(self):
        m = df.blocked_message(r"C:\Users\ann\Documents\forza", "GetLastError() = 5", system_name="windows")
        for w in ("OneDrive", "Controlled folder access", "antivirus", "Choose another folder", "GetLastError() = 5"):
            self.assertIn(w, m)

    def test_probe_writable(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(df.probe_writable(Path(d) / "new" / "sub"))
            self.assertEqual(os.listdir(Path(d) / "new" / "sub"), [])
            f = Path(d) / "file"
            f.write_text("x")
            self.assertIn("Error", df.probe_writable(f / "inside"))

    def test_access_denied_exception(self):
        self.assertTrue(df.is_access_denied_error(PermissionError(13, "denied")))
        e = OSError(13, "x")
        self.assertTrue(df.is_access_denied_error(e))
        self.assertFalse(df.is_access_denied_error(FileNotFoundError(2, "x")))

    def test_check_folder_space_and_onedrive(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("forza_launcher.build_env.free_disk", return_value=3 * df.GB):
                out = df.check_folder(d)
                self.assertEqual(out[-1][0], "error")
                self.assertIn("3 GB free", out[-1][1])
            with mock.patch("forza_launcher.build_env.free_disk", return_value=100 * df.GB), \
                    mock.patch.object(df, "in_onedrive", return_value="it is inside your OneDrive folder"):
                out = df.check_folder(d)
                self.assertEqual([l for l, _ in out], ["warn", "ok"])


class ReadProblem(unittest.TestCase):
    def test_unreadable_file_and_folder(self):
        if os.name == "nt" or os.geteuid() == 0:
            self.skipTest("needs POSIX permissions and a non-root user")
        with tempfile.TemporaryDirectory() as tmp:
            touch(Path(tmp) / "sdk" / "cmake" / "a.cmake")
            self.assertIsNone(df.read_problem(tmp))
            os.chmod(Path(tmp) / "sdk" / "cmake" / "a.cmake", 0)
            self.assertIn("a.cmake", df.read_problem(tmp))
            os.chmod(Path(tmp) / "sdk" / "cmake" / "a.cmake", 0o600)
            os.chmod(Path(tmp) / "sdk" / "cmake", 0)
            try:
                self.assertIn("cmake", df.read_problem(tmp))
            finally:
                os.chmod(Path(tmp) / "sdk" / "cmake", 0o700)


class Defaults(unittest.TestCase):
    def env(self, tmp):
        return mock.patch.dict(os.environ, {"USERPROFILE": str(Path(tmp) / "u"), "LOCALAPPDATA": str(Path(tmp) / "u" / "AppData" / "Local")})

    def test_fresh_install_uses_localappdata(self):
        with tempfile.TemporaryDirectory() as tmp, self.env(tmp):
            self.assertEqual(df.windows_default(), Path(tmp) / "u" / "AppData" / "Local" / "ForzaReImagined")

    def test_existing_documents_install_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp, self.env(tmp):
            touch(Path(tmp) / "u" / "Documents" / "forza" / "game" / "default.xex")
            self.assertEqual(df.windows_default(), Path(tmp) / "u" / "Documents" / "forza")
            # once the new folder exists, it is the one (a move or a fresh start made it)
            (Path(tmp) / "u" / "AppData" / "Local" / "ForzaReImagined").mkdir(parents=True)
            self.assertEqual(df.windows_default(), Path(tmp) / "u" / "AppData" / "Local" / "ForzaReImagined")

    def test_empty_documents_folder_is_not_an_install(self):
        with tempfile.TemporaryDirectory() as tmp, self.env(tmp):
            (Path(tmp) / "u" / "Documents" / "forza").mkdir(parents=True)
            self.assertEqual(df.windows_default().name, "ForzaReImagined")

    def test_normalize_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(df.normalize_choice(tmp), Path(tmp))                       # empty
            touch(Path(tmp) / "holiday.jpg")
            self.assertEqual(df.normalize_choice(tmp), Path(tmp) / "ForzaReImagined")   # somebody's files
            (Path(tmp) / "runtime").mkdir()
            self.assertEqual(df.normalize_choice(tmp), Path(tmp))                       # one of ours


class GameDefault(unittest.TestCase):
    """Where the game writes without --user_data_root, and its identity always (#815)."""

    def test_linux_is_the_xdg_data_home(self):
        with mock.patch.object(df, "IS_WINDOWS", False), \
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg/data"}):
            self.assertEqual(df.game_default_data_home(), Path("/xdg/data") / "forza")

    def test_linux_without_xdg_data_home_is_local_share(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"}
        with mock.patch.object(df, "IS_WINDOWS", False), mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(Path, "home", return_value=Path("/home/me")):
            self.assertEqual(df.game_default_data_home(), Path("/home/me/.local/share/forza"))
        with mock.patch.object(df, "IS_WINDOWS", False), mock.patch.dict(os.environ, {"XDG_DATA_HOME": ""}), \
                mock.patch.object(Path, "home", return_value=Path("/home/me")):
            self.assertEqual(df.game_default_data_home(), Path("/home/me/.local/share/forza"))


class Move(unittest.TestCase):
    def test_copy_keeps_old_and_skips_rebuildable(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "old", Path(tmp) / "new"
            touch(old / "0000000000000000" / "4D5309C9" / "00000001" / "save.dat", b"s" * 10)
            touch(old / "runtime" / "forza.exe", b"e" * 5)
            touch(old / "build" / "state.json", b"{}")
            touch(old / "build" / "forza-recomp" / "out" / "big.obj", b"o" * 50)
            touch(old / "sdk" / "x", b"1")
            touch(old / "toolchain" / "x", b"1")
            touch(old / "downloads" / "x", b"1")
            touch(old / "game" / "default.xex", b"g" * 7)
            logs = []
            n = df.copy_user_data(old, new, True, False, log=logs.append)
            self.assertEqual(n, 10 + 5 + 2)
            self.assertTrue((new / "0000000000000000" / "4D5309C9" / "00000001" / "save.dat").is_file())
            self.assertTrue((new / "runtime" / "forza.exe").is_file())
            self.assertTrue((new / "build" / "state.json").is_file())
            for gone in ("sdk", "toolchain", "downloads", "game"):
                self.assertFalse((new / gone).exists(), gone)
            self.assertFalse((new / "build" / "forza-recomp").exists())
            self.assertTrue((old / "game" / "default.xex").is_file())       # nothing removed from the old folder
            self.assertTrue((old / "0000000000000000" / "4D5309C9" / "00000001" / "save.dat").is_file())

    def test_game_only_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "old", Path(tmp) / "new"
            touch(old / "game" / "media" / "a.zip", b"a" * 4)
            touch(old / "game" / "default.xex", b"g" * 7)
            touch(old / "runtime" / "forza.exe")
            self.assertEqual(df.estimate_move(old, False, True), 11)
            self.assertEqual(df.copy_user_data(old, new, False, True), 11)
            self.assertTrue((new / "game" / "media" / "a.zip").is_file())
            self.assertFalse((new / "runtime").exists())
            (new / "game" / "default.xex").write_bytes(b"")       # a half-copied file is copied again
            df.copy_user_data(old, new, False, True)
            self.assertEqual((new / "game" / "default.xex").read_bytes(), b"g" * 7)

    def test_carry_game_written_data_once(self):
        # v0.1.4-v0.1.5 on Windows: the game wrote to Documents\forza, the launcher used another folder.
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "Documents" / "forza", Path(tmp) / "Local" / "ForzaReImagined"
            p = "B13EBABEBABEBABE"
            touch(old / p / "4D5309C9" / "00000001" / "save", b"s" * 3)
            touch(old / "cache" / "shaders" / "shareable" / "4D5309C9.xsh", b"c" * 5)
            touch(old / "0000000000000000" / "4D5309C9" / "00000002" / "dlc", b"d")
            touch(old / "game" / "default.xex")            # a v0.1.x launcher install: not carried
            touch(old / "runtime" / "forza.exe")
            touch(new / "game" / "default.xex")
            self.assertEqual(df.carry_game_written_data(old, new, p), 9)
            self.assertTrue((new / p / "4D5309C9" / "00000001" / "save").is_file())
            self.assertTrue((new / "cache" / "shaders" / "shareable" / "4D5309C9.xsh").is_file())
            self.assertTrue((new / "0000000000000000" / "4D5309C9" / "00000002" / "dlc").is_file())
            self.assertFalse((new / "runtime").exists())
            self.assertTrue((old / p / "4D5309C9" / "00000001" / "save").is_file())   # old untouched
            touch(old / p / "later", b"l")                   # the new folder has a profile now: done
            self.assertEqual(df.carry_game_written_data(old, new, p), 0)
            self.assertEqual(df.carry_game_written_data(new, new, p), 0)

    def test_carry_never_overwrites_and_profile_is_all_or_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "Documents" / "forza", Path(tmp) / "Local" / "ForzaReImagined"
            p = "B13EBABEBABEBABE"
            touch(old / p / "4D5309C9" / "00000001" / "a", b"a")
            touch(old / p / "4D5309C9" / "00000001" / "b", b"b")
            touch(old / "0000000000000000" / "4D5309C9" / "00000002" / "dlc", b"old")
            touch(new / "0000000000000000" / "4D5309C9" / "00000002" / "dlc", b"launcher")
            real = df.shutil.copy2

            def fail_on_b(src, dst, *a, **k):
                if Path(src).name == "b":
                    raise OSError(28, "No space left on device")
                return real(src, dst, *a, **k)
            with mock.patch.object(df.shutil, "copy2", fail_on_b):
                with self.assertRaises(OSError):
                    df.carry_game_written_data(old, new, p)
            self.assertFalse((new / p).exists())                 # no half profile: the gate stays open
            self.assertFalse((new / (p + ".carrying")).exists())
            self.assertEqual((new / "0000000000000000" / "4D5309C9" / "00000002" / "dlc").read_bytes(),
                             b"launcher")                        # never overwritten
            self.assertTrue((old / p / "4D5309C9" / "00000001" / "b").is_file())
            self.assertEqual(df.carry_game_written_data(old, new, p), 2)   # the next launch finishes it
            self.assertEqual((new / p / "4D5309C9" / "00000001" / "b").read_bytes(), b"b")

    def test_cancel(self):
        with tempfile.TemporaryDirectory() as tmp:
            touch(Path(tmp) / "old" / "a")
            with self.assertRaises(InterruptedError):
                df.copy_user_data(Path(tmp) / "old", Path(tmp) / "new", cancelled=lambda: True)


if __name__ == "__main__":
    unittest.main()
