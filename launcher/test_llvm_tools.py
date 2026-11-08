"""The build checks every LLVM program it needs (#744): a downloaded LLVM folder that lacks one
is unpacked again, a system clang-cl without lld-link/llvm-lib gives way to the downloaded LLVM.
Run: python3 -m unittest launcher/test_llvm_tools.py"""
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import build_env as be, builder, fetch, pins  # noqa: E402

WIN_BIN = ("clang-cl.exe", "clang.exe", "lld-link.exe", "llvm-lib.exe", "llvm-rc.exe", "llvm-ar.exe")
LINUX_BIN = ("clang", "clang++", "clang-23", "lld")


def make_llvm(folder: Path, files=WIN_BIN, headers=True):
    """A fake unpacked LLVM folder (llvm-<version>)."""
    (folder / "bin").mkdir(parents=True, exist_ok=True)
    for f in files:
        (folder / "bin" / f).write_text("x")
    if headers:
        (folder / "lib" / "clang" / "23" / "include").mkdir(parents=True, exist_ok=True)
        (folder / "lib" / "clang" / "23" / "include" / "stddef.h").write_text("x")


def make_archive(path: Path, files=WIN_BIN, headers=True):
    """A fake LLVM release archive (one top folder, stripped when unpacked)."""
    top = "clang+llvm-23-x86_64-pc-windows-msvc"
    names = [f"bin/{f}" for f in files] + [f"bin/{f}" for f in ("clang-tidy.exe", "opt.exe")]
    if headers:
        names.append("lib/clang/23/include/stddef.h")
    names.append("lib/libclang.lib")   # not kept by llvm_member_wanted
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:xz") as tar:
        for n in names:
            data = b"x"
            info = tarfile.TarInfo(f"{top}/{n}")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return path


def executable(folder: Path, name: str) -> str:
    """A program shutil.which finds on this host (the tests run the Windows logic on Linux too)."""
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / (name + (".exe" if os.name == "nt" else ""))
    f.write_text("#!/bin/sh\n")
    f.chmod(0o755)
    return str(f)


class Detect(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        self.llvm = be.toolchain_dir(self.root) / f"llvm-{pins.LLVM_VERSION}"
        self.sys_dir = Path(tmp.name) / "LLVM" / "bin"
        self.on_path = {}   # program name -> what find_tool finds on PATH

    def detect(self, plat="windows", sys_clang=None):
        def find(name, notes=None, path=None, windows=None, version_probe=None):
            if name in ("cmake", "ninja"):
                return "/x/" + name
            if name == ("clang-cl" if plat == "windows" else "clang"):
                return sys_clang
            return self.on_path.get(name)

        def version_of(cmd, rx):
            exe = str(cmd[0])
            if exe.endswith("cmake"):
                return (4, 0)
            if exe.endswith("ninja"):
                return (1, 12)
            return (23, 1)
        with mock.patch.object(be, "find_tool", side_effect=find), \
                mock.patch.object(be, "_version_of", side_effect=version_of), \
                mock.patch.object(be, "vs_bundled_tool", return_value=None), \
                mock.patch.object(be, "cxx_works", return_value=None), \
                mock.patch.object(be, "_flatpak_path"), \
                mock.patch.object(be, "_plat", return_value=plat):
            return be._detect_tools(self.root)

    def test_complete_download_is_used(self):
        make_llvm(self.llvm)
        t = self.detect()
        self.assertEqual(t.missing, [])
        self.assertEqual(Path(t.cc), self.llvm / "bin" / "clang-cl.exe")
        self.assertEqual(t.llvm_incomplete, [])

    def test_download_without_llvm_lib_is_downloaded_again(self):
        make_llvm(self.llvm, [f for f in WIN_BIN if f != "llvm-lib.exe"])
        t = self.detect()
        self.assertEqual(t.missing, ["llvm-windows"])
        self.assertEqual(t.llvm_incomplete, ["bin/llvm-lib.exe"])
        self.assertEqual(t.cc, "")
        self.assertTrue(any("bin/llvm-lib.exe" in n and str(self.llvm) in n for n in t.notes), t.notes)

    def test_each_needed_program_counts(self):
        for gone in ("clang-cl.exe", "lld-link.exe", "llvm-lib.exe", "llvm-rc.exe"):
            with self.subTest(gone=gone):
                for f in self.llvm.glob("bin/*"):
                    f.unlink()
                make_llvm(self.llvm, [f for f in WIN_BIN if f != gone])
                t = self.detect()
                self.assertEqual(t.missing, ["llvm-windows"])
                self.assertEqual(t.llvm_incomplete, ["bin/" + gone])

    def test_download_without_headers_is_downloaded_again(self):
        make_llvm(self.llvm, headers=False)
        t = self.detect()
        self.assertEqual(t.missing, ["llvm-windows"])
        self.assertEqual(t.llvm_incomplete, [be.LLVM_HEADERS])

    def test_no_download_yet_is_just_missing(self):
        t = self.detect()
        self.assertEqual(t.missing, ["llvm-windows"])
        self.assertEqual(t.llvm_incomplete, [])

    def test_linux_download_without_clangxx(self):
        make_llvm(self.llvm, [f for f in LINUX_BIN if f != "clang++"])
        t = self.detect("linux")
        self.assertEqual(t.missing, ["llvm-linux"])
        self.assertEqual(t.llvm_incomplete, ["bin/clang++"])
        make_llvm(self.llvm, LINUX_BIN)
        t = self.detect("linux")
        self.assertEqual(t.missing, [])
        self.assertEqual(Path(t.cxx), self.llvm / "bin" / "clang++")

    def test_system_clang_cl_without_llvm_lib_uses_the_download(self):
        clang = executable(self.sys_dir, "clang-cl")
        executable(self.sys_dir, "lld-link")
        make_llvm(self.llvm)
        t = self.detect(sys_clang=clang)
        self.assertEqual(Path(t.cc), self.llvm / "bin" / "clang-cl.exe")
        self.assertIn("downloaded", t.source["clang"])
        self.assertTrue(any("llvm-lib" in n and clang in n for n in t.notes), t.notes)

    def test_system_clang_cl_without_llvm_lib_downloads_llvm(self):
        clang = executable(self.sys_dir, "clang-cl")
        t = self.detect(sys_clang=clang)
        self.assertEqual(t.missing, ["llvm-windows"])
        self.assertTrue(any("lld-link, llvm-lib" in n for n in t.notes), t.notes)

    def test_system_clang_cl_with_its_tools_is_used(self):
        clang = executable(self.sys_dir, "clang-cl")
        executable(self.sys_dir, "lld-link")
        executable(self.sys_dir, "llvm-lib")
        t = self.detect(sys_clang=clang)
        self.assertEqual(t.cc, clang)
        self.assertIn("system", t.source["clang"])
        self.assertEqual(t.missing, [])

    def test_system_clang_cl_with_llvm_lib_on_path_is_used(self):
        """The toolchain file looks next to clang-cl (FORZA_LLVM_BIN), then on PATH."""
        clang = executable(self.sys_dir, "clang-cl")
        executable(self.sys_dir, "lld-link")
        self.on_path["llvm-lib"] = r"C:\other\llvm-lib.exe"
        t = self.detect(sys_clang=clang)
        self.assertEqual(t.cc, clang)

    def test_toolchain_file_needs_what_is_checked(self):
        """Every program the toolchain file requires is in LLVM_TOOLS, llvm-rc in the download's list."""
        text = (Path(__file__).resolve().parents[1] / "tools" / "cmake" / "windows-native-clang-cl.cmake").read_text()
        import re
        required = re.findall(r"find_program\(\w+ ([\w-]+) .*REQUIRED\)", text)
        self.assertEqual(sorted(required), sorted(be.LLVM_TOOLS["windows"]))
        for name in re.findall(r"find_program\(\w+ ([\w-]+) ", text):
            self.assertIn(name, be.LLVM_TOOLS_DOWNLOADED["windows"])
            self.assertIn(name, builder.LLVM_BIN_KEEP)


class Rep(builder.Reporter):
    def __init__(self):
        self.lines = []

    def log(self, text):
        self.lines.append(text)


class EnsureToolchain(unittest.TestCase):
    """ensure_toolchain unpacks the LLVM download again over an incomplete folder."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "data"
        self.llvm = be.toolchain_dir(self.root) / f"llvm-{pins.LLVM_VERSION}"
        self.archive = self.root / "downloads" / "clang+llvm.tar.xz"
        self.downloads = []
        be.forget_tools()
        self.addCleanup(be.forget_tools)

    def run_ensure(self):
        def download(name, cache, progress=None, cancelled=None):
            self.downloads.append(name)
            return self.archive

        def find(name, notes=None, path=None, windows=None, version_probe=None):
            return "/x/" + name if name in ("cmake", "ninja") else None

        def version_of(cmd, rx):
            return (4, 0) if str(cmd[0]).endswith("cmake") else (1, 12)
        rep = Rep()
        with mock.patch.object(fetch, "download", side_effect=download), \
                mock.patch.object(be, "find_tool", side_effect=find), \
                mock.patch.object(be, "_version_of", side_effect=version_of), \
                mock.patch.object(be, "vs_bundled_tool", return_value=None), \
                mock.patch.object(be, "_flatpak_path"), \
                mock.patch.object(be, "_plat", return_value="windows"), \
                mock.patch.object(builder.time, "sleep"):
            tools = builder.ensure_toolchain(builder.Options(self.root, jobs=1), rep, 0.0, 1.0)
        return tools, rep

    def test_incomplete_folder_is_downloaded_and_replaced(self):
        make_llvm(self.llvm, [f for f in WIN_BIN if f != "llvm-lib.exe"])
        (self.llvm / "bin" / "stale.exe").write_text("old")
        make_archive(self.archive)
        tools, rep = self.run_ensure()
        self.assertEqual(self.downloads, ["llvm-windows"])
        self.assertEqual(Path(tools.cc), self.llvm / "bin" / "clang-cl.exe")
        self.assertTrue((self.llvm / "bin" / "llvm-lib.exe").is_file())
        self.assertFalse((self.llvm / "bin" / "stale.exe").exists())        # the old folder is gone
        self.assertFalse((self.llvm / "bin" / "clang-tidy.exe").exists())   # only the kept programs
        self.assertFalse(self.llvm.with_name(self.llvm.name + ".part").exists())
        self.assertFalse(self.archive.exists())                             # 2 GB: deleted when complete
        self.assertTrue(any("bin/llvm-lib.exe" in line for line in rep.lines), rep.lines)

    def test_still_missing_after_the_unpack_is_a_clear_error(self):
        make_llvm(self.llvm, [f for f in WIN_BIN if f != "llvm-lib.exe"])
        make_archive(self.archive, [f for f in WIN_BIN if f != "llvm-lib.exe"])
        with self.assertRaises(builder.BuildError) as cm:
            self.run_ensure()
        text = str(cm.exception)
        self.assertIn("bin/llvm-lib.exe", text)
        self.assertIn("antivirus", text)
        self.assertIn(str(self.llvm.parent), text)
        self.assertTrue(self.archive.is_file())   # kept: the next try needs no download

    def test_quarantined_after_the_unpack_is_a_clear_error(self):
        make_archive(self.archive)
        real = builder.install_llvm

        def install_then_quarantine(name, arc, dest, rep, progress):
            real(name, arc, dest, rep, progress)
            (dest / "bin" / "llvm-lib.exe").unlink()   # the antivirus takes it away
        with mock.patch.object(builder, "install_llvm", side_effect=install_then_quarantine):
            with self.assertRaises(builder.BuildError) as cm:
                self.run_ensure()
        self.assertIn("bin/llvm-lib.exe", str(cm.exception))
        self.assertIn("antivirus", str(cm.exception))

    def test_leftover_part_folder_is_cleared(self):
        part = self.llvm.with_name(self.llvm.name + ".part")
        make_llvm(part, ["junk.exe"])
        make_archive(self.archive)
        tools, _ = self.run_ensure()
        self.assertFalse(part.exists())
        self.assertFalse((self.llvm / "bin" / "junk.exe").exists())
        self.assertEqual(tools.missing, [])


if __name__ == "__main__":
    unittest.main()
