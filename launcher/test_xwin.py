"""The xwin CRT and SDK without Developer Mode (#634, #739): python3 -m unittest launcher/test_xwin.py"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import build_env, builder, pins   # noqa: E402

CRT = "14.44.35207"
SDK = "10.0.26100"


def make(root: Path, rels):
    for r in rels:
        (root / r).mkdir(parents=True, exist_ok=True)


def winsysroot(xdir: Path, crt=CRT, sdk=SDK, done=True):
    """What `xwin splat --use-winsysroot-style` makes."""
    make(xdir, [f"VC/Tools/MSVC/{crt}/include", f"VC/Tools/MSVC/{crt}/lib/x86_64",
                *(f"Windows Kits/10/Include/{sdk}/{d}" for d in ("ucrt", "um", "shared", "winrt", "cppwinrt")),
                f"Windows Kits/10/Lib/{sdk}/um/x86_64", f"Windows Kits/10/Lib/{sdk}/ucrt/x86_64"])
    if done:
        (xdir / build_env.XWIN_DONE).write_text("xwin\n")


def plain_failed(xdir: Path):
    """What 0.2.8's plain-layout splat left after os error 1314 on a laptop (#739): no versioned link."""
    make(xdir, ["crt/include", "crt/lib/x86_64", *(f"sdk/include/{d}" for d in ("ucrt", "um", "shared", "winrt",
                                                                                "cppwinrt")),
                "sdk/lib/um/x86_64", "sdk/lib/ucrt/x86_64"])


class Reporter(builder.Reporter):
    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)


class XwinCommand(unittest.TestCase):
    def test_makes_no_link(self):
        cmd = [str(c) for c in builder.xwin_splat_command(Path("xwin.exe"), Path("R"), Path("R/xwin"))]
        # The plain layout links sdk/include/<version> to "." whatever --disable-symlinks says.
        self.assertIn("--use-winsysroot-style", cmd)
        self.assertIn("--disable-symlinks", cmd)
        self.assertEqual(cmd[cmd.index("--output") + 1], str(Path("R/xwin")))
        self.assertLess(cmd.index("--accept-license"), cmd.index("splat"))


class XwinLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.x = Path(self.tmp.name) / "xwin"

    def tearDown(self):
        self.tmp.cleanup()

    def test_winsysroot_layout(self):
        winsysroot(self.x)
        inc, lib = build_env.xwin_dirs(self.x)
        self.assertEqual(inc[0], self.x / "VC/Tools/MSVC" / CRT / "include")
        self.assertIn(self.x / "Windows Kits/10/Include" / SDK / "ucrt", inc)
        self.assertIn(self.x / "Windows Kits/10/Include" / SDK / "um", inc)
        self.assertEqual(lib, [self.x / "VC/Tools/MSVC" / CRT / "lib/x86_64",
                               self.x / "Windows Kits/10/Lib" / SDK / "um/x86_64",
                               self.x / "Windows Kits/10/Lib" / SDK / "ucrt/x86_64"])
        self.assertTrue(all(p.is_dir() for p in inc + lib))

    def test_newest_complete_version(self):
        winsysroot(self.x, crt="14.38.33130")
        winsysroot(self.x, crt="14.44.35207")
        make(self.x, ["VC/Tools/MSVC/14.50.1/include"])     # no lib: not complete
        inc, _ = build_env.xwin_dirs(self.x)
        self.assertEqual(inc[0].parent.name, "14.44.35207")

    def test_unfinished_winsysroot_splat(self):
        winsysroot(self.x, done=False)
        self.assertIsNone(build_env.xwin_dirs(self.x))
        winsysroot(self.x)
        (self.x / "Windows Kits/10/Lib" / SDK / "ucrt/x86_64").rmdir()
        self.assertIsNone(build_env.xwin_dirs(self.x))

    def test_plain_splat_that_failed_on_the_link(self):
        # crt/include, sdk/include/ucrt and sdk/lib/um/x86_64 all exist: 0.2.8 would have used it.
        plain_failed(self.x)
        self.assertIsNone(build_env.xwin_dirs(self.x))

    @unittest.skipIf(sys.platform == "win32", "a directory symlink needs Developer Mode on Windows")
    def test_plain_splat_with_its_links_is_kept(self):
        plain_failed(self.x)
        os.symlink(".", self.x / "sdk/include" / SDK)
        os.symlink(".", self.x / "sdk/lib" / SDK)
        inc, lib = build_env.xwin_dirs(self.x)
        self.assertEqual(inc[0], self.x / "crt/include")
        self.assertEqual(lib[1], self.x / "sdk/lib/um/x86_64")

    def test_nothing(self):
        self.assertIsNone(build_env.xwin_dirs(self.x))


class XwinEnvironment(unittest.TestCase):
    """msvc_environment with no Visual Studio: xwin is run (here faked) and its folders used."""

    def run_env(self, root: Path, splat=winsysroot, accept=True):
        calls = []

        def fake_run(cmd, cwd, env, rep, *a, **k):
            calls.append([str(c) for c in cmd])
            splat(Path(cmd[cmd.index("--output") + 1]))

        exe_dir = build_env.toolchain_dir(root) / f"xwin-{pins.XWIN_VERSION}"
        with patch.object(build_env, "find_msvc", return_value=(None, "")), \
                patch.object(builder.fetch, "download", return_value=root / "xwin.tar.gz"), \
                patch.object(builder.fetch, "unpack",
                             side_effect=lambda *a, **k: (make(exe_dir, ["."]), (exe_dir / "xwin.exe").write_text(""))), \
                patch.object(builder, "run", side_effect=fake_run):
            env = builder.msvc_environment(builder.Options(root=root, jobs=1, accept_ms_license=accept),
                                           Reporter(), None, 0, 0)
        return env, calls

    def test_retry_after_the_0_2_8_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain_failed(root / "xwin")
            env, calls = self.run_env(root)
            self.assertEqual(len(calls), 1)
            self.assertIn("--use-winsysroot-style", calls[0])
            self.assertFalse((root / "xwin/crt").exists())     # the half splat is gone
            self.assertFalse((root / "xwin/sdk").exists())
            self.assertTrue((root / "xwin" / build_env.XWIN_DONE).is_file())
            inc = env["INCLUDE"].split(os.pathsep)
            self.assertEqual(inc[0], str(root / "xwin/VC/Tools/MSVC" / CRT / "include"))
            self.assertTrue(all(Path(p).is_dir() for p in inc + env["LIB"].split(os.pathsep)))
            self.assertEqual(env["FORZA_MSVC_COMPAT"], builder.XWIN_MSVC_COMPAT)
            # The second build uses it as it is.
            env2, calls2 = self.run_env(root)
            self.assertEqual(calls2, [])
            self.assertEqual(env2["INCLUDE"], env["INCLUDE"])

    def test_splat_that_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(builder.BuildError) as caught:
                self.run_env(Path(tmp), splat=lambda x: None)
            self.assertIn("Delete that folder", str(caught.exception))

    def test_needs_the_license(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(builder.BuildError) as caught:
                self.run_env(Path(tmp), accept=False)
            self.assertIn("license", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
