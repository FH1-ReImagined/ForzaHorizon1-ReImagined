"""The original menu configures without FH2 translations or game inputs."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "forza-recomp/cmake/NativePause.cmake"


@unittest.skipUnless(shutil.which("cmake"), "CMake is required")
class NativePauseConfiguration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="native pause config ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.build = self.root / "build"
        self.source.mkdir()
        (self.source / "dummy.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")
        (self.source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.25)\n"
            "project(native_pause_configuration LANGUAGES CXX)\n"
            'set(FORZA_TITLE_UPDATE 0 CACHE STRING "Game version")\n'
            f'include("{HELPER.as_posix()}")\n'
            "forza_validate_native_pause()\n"
            "add_executable(dummy dummy.cpp)\n"
            "forza_apply_native_pause(dummy)\n"
            'foreach(property COMPILE_DEFINITIONS INCLUDE_DIRECTORIES)\n'
            '  get_target_property(value dummy ${property})\n'
            '  if(NOT value)\n'
            '    set(value "")\n'
            '  endif()\n'
            '  file(WRITE "${CMAKE_BINARY_DIR}/${property}.txt" "${value}")\n'
            "endforeach()\n", encoding="utf-8")

    def configure(self, version=4, *options, real=False):
        return subprocess.run(
            ["cmake", "-S", str(ROOT / "forza-recomp" if real else self.source),
             "-B", str(self.build), f"-DFORZA_TITLE_UPDATE={version}", *options],
            capture_output=True, text=True, timeout=120)

    def assert_configuration(self, result, definitions=""):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.build / "COMPILE_DEFINITIONS.txt").read_text(), definitions)
        self.assertEqual((self.build / "INCLUDE_DIRECTORIES.txt").read_text(), "")

    def test_tu4_default_has_original_menu_without_private_includes(self):
        self.assert_configuration(self.configure(), "FORZA_NATIVE_PAUSE=1")

    def test_disc_default_keeps_stock_pause(self):
        self.assert_configuration(self.configure(0))

    def test_explicit_disable_has_no_menu_definition(self):
        self.assert_configuration(self.configure(4, "-DFORZA_NATIVE_PAUSE=OFF"))

    def test_reconfiguration_removes_definition(self):
        self.assert_configuration(self.configure(), "FORZA_NATIVE_PAUSE=1")
        self.assert_configuration(self.configure(0))

    def test_old_private_options_are_rejected_before_sdk_or_codegen(self):
        result = self.configure(4, "-DFORZA_FH2_NAVIGATION_DIR=obsolete", real=True)
        self.assertNotEqual(result.returncode, 0)
        output = " ".join((result.stdout + result.stderr).split())
        self.assertIn("The native menu uses no FH2 inputs", output)
        self.assertNotIn("First configure: running codegen", output)

    def test_events_option_also_rejected_when_disabled(self):
        result = self.configure(4, "-DFORZA_NATIVE_PAUSE=OFF",
                                "-DFORZA_FH2_GRID_EVENTS_DIR=obsolete")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("The native menu uses no FH2 inputs", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
