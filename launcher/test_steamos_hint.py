"""SteamOS: the .tar.gz launcher points at the Flatpak when C++ headers are missing (#135).
Run: python3 -m unittest launcher/test_steamos_hint.py"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import build_env, paths  # noqa: E402

STEAMOS_MSG = (
    "SteamOS has no C++ development headers, so this launcher can't build the game here. "
    "Use the Flatpak version of the launcher instead (it brings its own compiler): download "
    "ForzaHorizon1-ReImagined-x86_64.flatpak from the releases page and install it in Desktop Mode "
    "(Discover can open it). Your saves and settings can stay where they are.")
WHY = "fatal error: 'format' file not found"
GENERIC = ("The compiler cannot build a C++23 program (" + WHY + "). Install your system's "
           "C++ development packages: g++ and libstdc++ headers (Debian/Ubuntu: build-essential; "
           "Fedora: gcc-c++; Arch: gcc), then try again.")


class SteamOSHint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        llvm = self.root / "toolchain" / f"llvm-{build_env.pins.LLVM_VERSION}" / "bin"
        llvm.mkdir(parents=True)
        (llvm / "clang").write_text("")
        (llvm / "clang++").write_text("")
        (llvm.parent / "lib" / "clang" / "23" / "include").mkdir(parents=True)

    def release(self, text):
        path = Path(self.tmp.name) / "os-release"
        path.write_text(text)
        return path

    def detect(self, os_release, flatpak=False):
        with patch.object(build_env, "find_tool", return_value=None), \
                patch.object(build_env, "cxx_works", return_value=WHY), \
                patch.object(build_env, "_flatpak_path"), \
                patch.object(build_env, "OS_RELEASE", str(os_release)), \
                patch.object(paths, "IN_FLATPAK", flatpak), \
                patch.object(build_env, "_plat", return_value="linux"):
            return build_env._detect_tools(self.root)

    def test_steamos_tar_gz_points_at_the_flatpak(self):
        tools = self.detect(self.release("NAME=SteamOS\nID=steamos\nID_LIKE=arch\n"))
        self.assertEqual(tools.problems, [STEAMOS_MSG])

    def test_other_distro_keeps_the_generic_message(self):
        tools = self.detect(self.release('ID=fedora\nID_LIKE="rhel centos"\n'))
        self.assertEqual(tools.problems, [GENERIC])

    def test_steamos_flatpak_keeps_the_generic_message(self):
        tools = self.detect(self.release('ID="steamos"\n'), flatpak=True)
        self.assertEqual(tools.problems, [GENERIC])

    def test_id_like_steamos(self):
        path = self.release('ID=custom\nID_LIKE="arch steamos"\n')
        self.assertTrue(build_env.is_steamos(path))

    def test_missing_os_release_is_false(self):
        self.assertFalse(build_env.is_steamos(Path(self.tmp.name) / "absent"))

    def test_unreadable_os_release_is_false(self):
        self.assertFalse(build_env.is_steamos(self.tmp.name))
