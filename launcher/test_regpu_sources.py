"""The ReGPU submodule in the launcher's source bundle (builder.source_files): on Windows only the files
its CMake reads, elsewhere none of it, and never a submodule's .git link
(python3 -m unittest launcher/test_regpu_sources.py from the repository root)."""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from forza_launcher import builder  # noqa: E402

REGPU = ("CMakeLists.txt", "LICENSE", "README.md", "NOTES.md", ".git", ".gitignore", "docs/xenos-notes.md",
         "include/regpu/regpu.h", "src/log.cpp", "src/gpu/renderer.cpp", "tests/linkcheck.cpp",
         "tools/embed_hlsl.py", "tools/xenos_dis.cpp", "tools/xbdm.py", "tools/regpuctl.py")


class RegpuSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmp.name)
        base = self.proj / "forza-recomp"
        for rel in ["CMakeLists.txt", "regpu-plugin/CMakeLists.txt"] + [f"thirdparty/ReGPU/{f}" for f in REGPU]:
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def regpu(self, windows):
        return {f.relative_to("thirdparty/ReGPU").as_posix() for f in builder.source_files(self.proj, windows=windows)
                if f.parts[:2] == ("thirdparty", "ReGPU")}

    def test_windows_ships_what_cmake_reads(self):
        self.assertEqual(self.regpu(True), {"CMakeLists.txt", "LICENSE", "README.md", "include/regpu/regpu.h",
                                            "src/log.cpp", "src/gpu/renderer.cpp", "tests/linkcheck.cpp",
                                            "tools/embed_hlsl.py", "tools/xenos_dis.cpp"})

    def test_other_platforms_ship_none(self):
        self.assertEqual(self.regpu(False), set())
        files = {f.as_posix() for f in builder.source_files(self.proj, windows=False)}
        self.assertIn("regpu-plugin/CMakeLists.txt", files)


if __name__ == "__main__":
    unittest.main()
