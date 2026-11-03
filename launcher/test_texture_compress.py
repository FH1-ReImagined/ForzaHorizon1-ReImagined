"""Texture compression (#413, texture_compress.py): forza-texcompress built from
forza-recomp/texcompress with this system's C++ compiler (FORZA_TEXCOMPRESS names a built one;
skipped without either), its DDS files decoded back and compared with the pictures they were made
from, a pack folder's PNGs compressed, and the wrapper with a stand-in compressor that fails,
crashes or is cancelled. Disposable folders only."""
import atexit
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import fetch, texture_compress, texture_packs  # noqa: E402
from forza_launcher.config import Config  # noqa: E402

try:
    from PySide6.QtGui import QColor, QImage
except ImportError:
    QImage = None

REPO = Path(__file__).resolve().parent.parent
FP = ["%016x" % (0x1111111111111111 * i) for i in range(1, 9)]
_BUILT = {}
GIVEN = os.environ.get("FORZA_TEXCOMPRESS")   # before the tests change it


def real_tool() -> Path | None:
    """FORZA_TEXCOMPRESS, else forza-texcompress compiled once from the sources with c++."""
    given = GIVEN
    if given:
        return Path(given) if Path(given).is_file() else None
    if "tool" in _BUILT:
        return _BUILT["tool"]
    _BUILT["tool"] = None
    cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if sys.platform == "win32" or not cxx:
        return None
    out_dir = Path(tempfile.mkdtemp(prefix="texcompress-"))
    atexit.register(shutil.rmtree, out_dir, True)   # the tests of other modules use it too
    tc, tp = REPO / "forza-recomp" / "texcompress", REPO / "forza-recomp" / "thirdparty" / "bc7enc_rdo"
    exe = out_dir / "forza-texcompress"
    cmd = [cxx, "-std=c++20", "-O2", "-pthread", "-DRGBCX_USE_SMALLER_TABLES=1", "-I", str(tp), "-I", str(tc),
           str(tc / "main.cpp"), str(tc / "texcompress.cpp"), str(tp / "bc7enc.cpp"), str(tp / "bc7decomp.cpp"),
           str(tp / "rgbcx.cpp"), "-o", str(exe)]
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode == 0 and exe.is_file():
        _BUILT["tool"] = exe
    return _BUILT["tool"]


def picture(path: Path, w: int, h: int, kind: str):
    """'ground' (opaque, smooth colours with fine grain), 'leaf' (a cut-out: alpha 0 outside, 255
    inside, a 3-texel ramp at the edge, the green also under the transparent texels) or 'flat'."""
    img = QImage(w, h, QImage.Format_RGBA8888)
    for y in range(h):
        for x in range(w):
            grain = ((x * 7 + y * 13) % 5) - 2
            if kind == "ground":
                c = (110 + x * 60 // w + grain, 90 + y * 40 // h + grain, 60 + grain, 255)
            elif kind == "leaf":
                d = math.hypot((x - w / 2) / (w * 0.4), (y - h / 2) / (h * 0.4))
                a = round(min(1.0, max(0.0, (1.0 - d) * w * 0.4 / 3 + 0.5)) * 255)
                c = (40 + grain, 120 + grain, 30, a)
            else:
                c = (200, 30, 40, 255)
            img.setPixelColor(x, y, QColor(*c))
    img.save(str(path))


def rgba_of(path: Path):
    img = QImage(str(path)).convertToFormat(QImage.Format_RGBA8888)
    return bytes(img.constBits())[: img.width() * img.height() * 4]


def decode(tool: Path, dds: Path, out: Path):
    """The largest level of a DDS file as RGBA8 (forza-texcompress --decode), and its format."""
    done = subprocess.run([str(tool), "--decode", str(dds), str(out)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    width, height, fmt, levels = done.stdout.split()
    return out.read_bytes(), int(width), int(height), fmt, int(levels)


def psnr(a: bytes, b: bytes, channels=(0, 1, 2)) -> float:
    total = sum((a[i + c] - b[i + c]) ** 2 for i in range(0, len(a), 4) for c in channels)
    n = len(a) // 4 * len(channels)
    return 99.0 if total == 0 else 10 * math.log10(255 * 255 * n / total)


# The stand-in: forza-texcompress's arguments and output lines, a DDS of zero BC7 blocks per job.
FAKE = r'''import os, struct, sys, time
from pathlib import Path
args = sys.argv[1:]
if args[0] == "--version":
    print(os.environ.get("FAKE_VERSION", "forza-texcompress 1 (stand-in)"))
    sys.exit(0)
mode = os.environ.get("FAKE_MODE", "")
if mode == "crash":
    print("cannot start: out of memory", file=sys.stderr)
    sys.exit(3)
for line in Path(args[-1]).read_text(encoding="utf-8").splitlines():
    choice, w, h, raw, out = line.split("\t")
    w, h = int(w), int(h)
    if mode == "sleep":
        time.sleep(30)
    if "bad" in out or os.path.getsize(raw) != w * h * 4:
        print(f"fail\t{out}\tthe input is wrong", flush=True)
        continue
    header = [0] * 31
    header[0:4] = [124, 0x81007, h, w]
    header[6] = 1
    header[18:21] = [32, 0x4, struct.unpack("<I", b"DX10")[0]]
    header[26] = 0x1000
    data = b"DDS " + struct.pack("<31I", *header) + struct.pack("<5I", 98, 3, 0, 1, 0)
    data += b"\0" * (((w + 3) // 4) * ((h + 3) // 4) * 16)
    Path(out + ".tmp").write_bytes(data)
    os.replace(out + ".tmp", out)
    print(f"ok\t{out}\tBC7\t1\t50.00\t0", flush=True)
sys.exit(1 if any("bad" in l for l in Path(args[-1]).read_text(encoding="utf-8").splitlines()) else 0)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.cfg = Config(self.t / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.t / "home")
        texture_compress._checked.clear()

    def use_tool(self, path: Path, script=False):
        env = mock.patch.dict(os.environ, {"FORZA_TEXCOMPRESS": str(path)})
        env.start()
        self.addCleanup(env.stop)
        if script:   # a Python script stands in for the program: the same arguments
            command = mock.patch.object(texture_compress, "_command",
                                        lambda p, args: [sys.executable, str(p)] + args)
            command.start()
            self.addCleanup(command.stop)


@unittest.skipIf(QImage is None, "PySide6 is not installed")
class RealCompressor(Base):
    def setUp(self):
        super().setUp()
        tool = real_tool()
        if tool is None:
            self.skipTest("no forza-texcompress and no C++ compiler to build one")
        self.tool = tool
        self.use_tool(tool)

    def test_a_pack_folder_becomes_dds_files_that_decode_back_close_to_its_pngs(self):
        self.assertTrue(texture_compress.available(self.cfg))
        pack = self.t / "Rocks HD"
        (pack / "terrain").mkdir(parents=True)
        picture(pack / f"{FP[0]}.png", 96, 64, "ground")
        picture(pack / "terrain" / f"{FP[1]}.png", 128, 128, "leaf")
        picture(pack / f"{FP[2]}.png", 64, 32, "flat")
        (pack / f"{FP[3]}.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really")       # cannot be read
        picture(pack / f"{FP[4]}.png", 16, 16, "flat")
        (pack / f"{FP[4]}.dds").write_bytes(b"theirs")                              # the game uses this one
        originals = {fp: rgba_of(p) for fp, p in ((FP[0], pack / f"{FP[0]}.png"),
                                                    (FP[1], pack / "terrain" / f"{FP[1]}.png"),
                                                    (FP[2], pack / f"{FP[2]}.png"))}
        before = texture_packs.read_pack(pack)
        self.assertEqual(before.png_files, 4)
        calls = []
        result = texture_compress.compress_folder(self.cfg, pack, self.t / "work",
                                                  lambda n, total: calls.append((n, total)))
        self.assertEqual(result.compressed, 3)
        self.assertEqual(len(result.failed), 1)
        self.assertIn(FP[3], result.failed[0])
        self.assertEqual(calls[-1], (4, 4))
        self.assertFalse((self.t / "work").exists())
        # The PNGs are gone (not the unreadable one, nor the one beside a .dds); DDS files instead.
        self.assertEqual(sorted(p.name for p in pack.rglob("*.png")), [f"{FP[3]}.png", f"{FP[4]}.png"])
        self.assertEqual((pack / f"{FP[4]}.dds").read_bytes(), b"theirs")
        files = {FP[0]: pack / f"{FP[0]}.dds", FP[1]: pack / "terrain" / f"{FP[1]}.dds",
                 FP[2]: pack / f"{FP[2]}.dds"}
        sizes = {FP[0]: (96, 64), FP[1]: (128, 128), FP[2]: (64, 32)}
        for fp, path in files.items():
            with self.subTest(fp=fp):
                header = texture_packs.read_texture_header(path)
                self.assertEqual(header.problem, "")
                self.assertEqual((header.width, header.height), sizes[fp])
                data, w, h, fmt, levels = decode(self.tool, path, self.t / f"{fp}.rgba")
                self.assertEqual((w, h, fmt), (*sizes[fp], header.format))
                self.assertEqual(levels, max(w, h).bit_length())     # every mip level, down to 1x1
                self.assertEqual(struct.unpack_from("<I", path.read_bytes(), 28)[0], levels)
                self.assertGreater(psnr(originals[fp], data), 38)
        # The cut-out: BC7, alpha kept: transparent stays transparent, opaque opaque, and no texel
        # far from half alpha crosses it (an alpha test's cut: no holes).
        self.assertEqual(texture_packs.read_texture_header(files[FP[1]]).format, "BC7")
        leaf = decode(self.tool, files[FP[1]], self.t / "leaf.rgba")[0]
        src = originals[FP[1]]
        for i in range(3, len(src), 4):
            if abs(src[i] - 128) >= 32:
                self.assertEqual(src[i] >= 128, leaf[i] >= 128, f"texel {i // 4}")
            if src[i] == 0:
                self.assertLessEqual(leaf[i], 16)
            if src[i] == 255:
                self.assertGreaterEqual(leaf[i], 239)
        self.assertGreater(psnr(src, leaf, channels=(3,)), 38)
        # A flat colour: BC1 (an eighth of RGBA8), exact.
        self.assertEqual(texture_packs.read_texture_header(files[FP[2]]).format, "BC1")
        self.assertGreater(psnr(originals[FP[2]], decode(self.tool, files[FP[2]], self.t / "f.rgba")[0]), 45)
        # The pack lists them (and the PNG beside the unusable .dds): a quarter of the graphics
        # memory or less.
        after = texture_packs.read_pack(pack)
        self.assertEqual((after.files, after.png_files), (4, 1))
        mine = sum(texture_packs.read_texture_header(p).gpu_bytes() for p in files.values())
        self.assertLessEqual(mine * 4, sum(w * h * 4 * 4 // 3 for w, h in sizes.values()) + 64)
        self.assertEqual(result.dds_bytes, sum(p.stat().st_size for p in files.values()))

    def test_a_job_list_and_its_lines(self):
        picture(self.t / "a.png", 64, 64, "ground")
        raw = self.t / "a.rgba"
        texture_compress.write_raw(raw, rgba_of(self.t / "a.png"))
        jobs = [texture_compress.Job("bc7", 64, 64, raw, self.t / "out" / "a.dds"),
                texture_compress.Job("bc1", 64, 64, raw, self.t / "out" / "b.dds"),
                texture_compress.Job("auto", 32, 32, raw, self.t / "out" / "c.dds")]   # the wrong size
        (self.t / "out").mkdir()
        outcome = texture_compress.run(self.cfg, jobs, self.t / "work")
        self.assertEqual(outcome.done[str(jobs[0].output)].format, "BC7")
        self.assertEqual(outcome.done[str(jobs[1].output)].format, "BC1")
        self.assertGreater(outcome.done[str(jobs[0].output)].psnr, outcome.done[str(jobs[1].output)].psnr)
        self.assertIn("16384 bytes, not 4096", outcome.failed[str(jobs[2].output)])
        self.assertFalse(jobs[2].output.exists())
        self.assertEqual(sorted(p.name for p in (self.t / "out").iterdir()), ["a.dds", "b.dds"])


    def test_unicode_paths_and_replacing_an_existing_output(self):
        folder = self.t / "caf\u00e9 \u65e5\u672c"
        folder.mkdir()
        raw, output = folder / "pixels.rgba", folder / "texture.dds"
        raw.write_bytes(b"\x80\x40\x20\xff" * 16)
        output.write_bytes(b"old output")
        job = texture_compress.Job("bc7", 4, 4, raw, output)
        result = texture_compress.run(self.cfg, [job], self.t / "work")
        self.assertIn(str(output), result.done)
        self.assertTrue(output.read_bytes().startswith(b"DDS "))
        self.assertFalse(Path(str(output) + ".tmp").exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows sharing semantics")
    def test_locked_output_preserves_previous_file(self):
        import ctypes
        from ctypes import wintypes

        raw, output = self.t / "pixels.rgba", self.t / "texture.dds"
        raw.write_bytes(b"\x80\x40\x20\xff" * 16)
        output.write_bytes(b"old output")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        # Allow reads, but deny replacing this disposable output while the job runs.
        handle = kernel.CreateFileW(str(output), 0x80000000, 1, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            job = texture_compress.Job("bc7", 4, 4, raw, output)
            result = texture_compress.run(self.cfg, [job], self.t / "work")
            self.assertIn(str(output), result.failed)
            self.assertNotIn(str(output), result.done)
            self.assertEqual(output.read_bytes(), b"old output")
            self.assertFalse(Path(str(output) + ".tmp").exists())
        finally:
            kernel.CloseHandle(handle)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux full-device fixture")
    def test_failed_buffer_flush_does_not_publish_output(self):
        raw, output = self.t / "pixels.rgba", self.t / "texture.dds"
        raw.write_bytes(b"\x80\x40\x20\xff" * 16)
        output.write_bytes(b"old output")
        temp = Path(str(output) + ".tmp")
        temp.symlink_to("/dev/full")
        job = texture_compress.Job("bc7", 4, 4, raw, output)
        result = texture_compress.run(self.cfg, [job], self.t / "work")
        self.assertIn(str(output), result.failed)
        self.assertNotIn(str(output), result.done)
        self.assertEqual(output.read_bytes(), b"old output")
        self.assertFalse(temp.is_symlink())


class StandInCompressor(Base):
    def setUp(self):
        super().setUp()
        self.fake = self.t / "fake_texcompress.py"
        self.fake.write_text(FAKE)
        self.use_tool(self.fake, script=True)
        self.raw = self.t / "x.rgba"
        texture_compress.write_raw(self.raw, b"\x80" * 8 * 8 * 4)

    def jobs(self, names):
        return [texture_compress.Job("auto", 8, 8, self.raw, self.t / f"{n}.dds") for n in names]

    def test_available_only_with_its_version(self):
        self.assertTrue(texture_compress.available(self.cfg))
        texture_compress._checked.clear()
        with mock.patch.dict(os.environ, {"FAKE_VERSION": "forza-texcompress 0 (old)"}):
            self.assertFalse(texture_compress.available(self.cfg))
        texture_compress._checked.clear()
        with mock.patch.dict(os.environ, {"FORZA_TEXCOMPRESS": str(self.t / "missing")}):
            self.assertIsNone(texture_compress.tool_path(self.cfg))
            self.assertFalse(texture_compress.available(self.cfg))

    def test_a_failed_job_and_a_crash(self):
        jobs = self.jobs(["one", "bad", "two"])
        seen = []
        outcome = texture_compress.run(self.cfg, jobs, self.t / "work", lambda n, total: seen.append((n, total)))
        self.assertEqual(sorted(Path(p).stem for p in outcome.done), ["one", "two"])
        self.assertEqual(outcome.failed, {str(jobs[1].output): "the input is wrong"})
        self.assertEqual(seen[-1], (3, 3))
        with mock.patch.dict(os.environ, {"FAKE_MODE": "crash"}):
            outcome = texture_compress.run(self.cfg, self.jobs(["three"]), self.t / "work2")
        self.assertEqual(outcome.done, {})
        self.assertIn("cannot start: out of memory", outcome.failed[str(self.t / "three.dds")])

    def test_cancel_stops_it(self):
        start = time.monotonic()
        with mock.patch.dict(os.environ, {"FAKE_MODE": "sleep"}):
            with self.assertRaises(fetch.Cancelled):
                texture_compress.run(self.cfg, self.jobs(["slow"]), self.t / "work",
                                     cancelled=lambda: time.monotonic() - start > 0.5)
        self.assertLess(time.monotonic() - start, 15)
        self.assertFalse((self.t / "slow.dds").exists())

    def test_a_folder_name_with_a_tab_is_refused(self):
        with self.assertRaises(RuntimeError):
            texture_compress.run(self.cfg, [texture_compress.Job("auto", 8, 8, self.raw, self.t / "a\tb.dds")],
                                 self.t / "work")

    def test_without_the_compressor(self):
        with mock.patch.dict(os.environ, {"FORZA_TEXCOMPRESS": str(self.t / "missing")}):
            with self.assertRaises(RuntimeError) as e:
                texture_compress.run(self.cfg, self.jobs(["x"]), self.t / "work")
        self.assertIn("build the game again", str(e.exception))


if __name__ == "__main__":
    unittest.main()
