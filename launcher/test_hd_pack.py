"""Make HD pack (#384, hd_pack.py): what is upscaled, the images (alpha, colours, size) and the
job with a stand-in upscaler (no download, no GPU), writing PNGs (a build without the compressor)
or DDS files (#413: forza-texcompress built from the sources when there is a C++ compiler, else a
stand-in). Disposable folders only."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import fetch, hd_pack, pins, texture_compress, texture_packs  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
import test_texture_compress  # noqa: E402

try:
    from PySide6.QtGui import QColor, QImage
except ImportError:
    QImage = None

FP = ["%016x" % (0x1111111111111111 * i) for i in range(1, 9)]

# The stand-in upscaler: realesrgan-ncnn-vulkan's arguments, a 4x smooth scale (and a fixed
# colour shift, which the job must undo).
FAKE = r'''import os, sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
if os.environ.get("FAKE_UPSCALER_FAIL"):
    print("vkCreateInstance failed", file=sys.stderr)
    sys.exit(255)
scale = int(args["-s"])
for p in sorted(Path(args["-i"]).glob("*.png")):
    img = QImage(str(p)).convertToFormat(QImage.Format_RGB888)
    out = img.scaled(img.width() * scale, img.height() * scale, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    data = bytearray(out.constBits())
    data[:] = bytes(max(0, v - 20) for v in data)   # darker, as the real model can be
    QImage(bytes(data), out.width(), out.height(), out.bytesPerLine(), QImage.Format_RGB888).save(
        str(Path(args["-o"]) / p.name))
'''


def index_line(fp, game_format, w, h, file_format):
    return f"{fp}\t{game_format}\t{w}x{h}\t1\t{file_format}\n"


@unittest.skipIf(QImage is None, "PySide6 is not installed")
class HdPackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.cfg = Config(self.t / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.t / "home")
        self.dump = hd_pack.dump_dir(self.cfg)
        self.dump.mkdir(parents=True)
        self.no_compressor()

    def no_compressor(self):
        """A build of the game without forza-texcompress: the pack is written as PNGs."""
        texture_compress._checked.clear()
        env = mock.patch.dict(os.environ, {"FORZA_TEXCOMPRESS": str(self.t / "no-compressor")})
        env.start()
        self.addCleanup(env.stop)

    def compressor(self):
        """forza-texcompress built from the sources (True), else the stand-in (False)."""
        texture_compress._checked.clear()
        tool = test_texture_compress.real_tool()
        if tool is None:
            tool = self.t / "fake_texcompress.py"
            tool.write_text(test_texture_compress.FAKE)
            command = mock.patch.object(texture_compress, "_command",
                                        lambda p, args: [sys.executable, str(p)] + args)
            command.start()
            self.addCleanup(command.stop)
        env = mock.patch.dict(os.environ, {"FORZA_TEXCOMPRESS": str(tool)})
        env.start()
        self.addCleanup(env.stop)
        return tool.suffix != ".py"

    def picture(self, path, w, h, alpha_hole=False, colour=(120, 90, 60)):
        img = QImage(w, h, QImage.Format_RGBA8888)
        for y in range(h):
            for x in range(w):
                c = QColor(colour[0] + (x * 3) % 40, colour[1] + (y * 5) % 40, colour[2], 255)
                if alpha_hole and x < w // 2:
                    c = QColor(0, 0, 0, 0)   # BC1's cut-out texels
                img.setPixelColor(x, y, c)
        img.save(str(path))

    def write_dump(self, rows):
        (self.dump / "index.tsv").write_text("".join(index_line(*r) for r in rows))
        for fp, _gf, w, h, ff in rows:
            self.picture(self.dump / f"{fp}.png", w, h, alpha_hole=(fp == FP[1]))

    def fake_upscaler(self):
        tool = hd_pack.tool_dir(self.cfg)
        (tool / "models").mkdir(parents=True)
        files = {}
        for name in hd_pack._wanted_files():
            p = tool / name
            p.write_text(FAKE if name == hd_pack._program_name() else name)
            files[name] = {"size": p.stat().st_size, "sha256": fetch.sha256_of(p)}
        (tool / hd_pack.MANIFEST).write_text(json.dumps(
            {"archive": pins.DOWNLOADS[hd_pack.platform_key()][1], "files": files}))
        # Run as a Python script (the real one is a program): the same arguments.
        real = hd_pack._command
        command = mock.patch.object(hd_pack, "_command",
                                    lambda cfg, src, dest: [sys.executable] + real(cfg, src, dest))
        command.start()
        self.addCleanup(command.stop)

    def test_what_is_upscaled_and_how_big(self):
        self.assertEqual(hd_pack.out_size(512, 256, 2), (1024, 512))
        self.assertEqual(hd_pack.out_size(512, 256, 4), (2048, 1024))
        self.assertEqual(hd_pack.out_size(2048, 1024, 4), (4096, 2048))   # 4x would pass 4096
        self.assertIsNone(hd_pack.out_size(4096, 64, 2))
        rows = [(FP[0], "k_DXT1", 64, 64, "BC1"), (FP[1], "k_DXT4_5", 128, 64, "BC3"),
                (FP[2], "k_DXN", 256, 256, "BC5"), (FP[3], "k_DXT5A", 256, 256, "BC4"),
                (FP[4], "k_8", 256, 256, "R8"), (FP[5], "k_DXT1", 512, 16, "BC1"),
                (FP[6], "k_DXT1", 32, 32, "BC1")]
        (self.dump / "index.tsv").write_text("".join(index_line(*r) for r in rows) + "junk\n")
        for fp in FP[:7]:
            (self.dump / f"{fp}.png").write_bytes(b"")
        (self.dump / f"{FP[1]}.png").unlink()
        p = hd_pack.plan(self.cfg, 2)
        self.assertEqual([i.fingerprint for i in p.todo], [FP[0]])
        self.assertEqual(p.skipped, {"normal maps": 1, "masks": 2, "small": 2, "without a .png in the dump": 1})
        self.assertEqual(p.dumped, 7)
        self.assertFalse(p.compressed)
        self.assertEqual(p.gpu_bytes(), 128 * 128 * 4 * 4 // 3)
        # Compressed (BC7, a byte a texel, plus a third for the mips): a quarter; 4x then needs a
        # card with 6 GB, not 10.
        self.assertEqual(hd_pack.plan(self.cfg, 2, compressed=True).gpu_bytes(), 128 * 128 * 4 // 3)
        self.assertEqual(hd_pack.plan(self.cfg, 2, compressed=True).disk_bytes(), 128 * 128 * 4 // 3)
        self.assertEqual((hd_pack.min_vram_gb_4x(True), hd_pack.min_vram_gb_4x(False)), (6, 10))

    def test_a_texture_is_made_only_at_the_scale_asked_for(self):
        """A 2x pack does not count as made for 4x (and back): the other scale is work to remake,
        and the page asks to replace the pack."""
        (self.dump / "index.tsv").write_text(index_line(FP[0], "k_DXT1", 64, 64, "BC1"))
        (self.dump / f"{FP[0]}.png").write_bytes(b"")
        pack = hd_pack.pack_dir(self.cfg)
        for made, other in ((2, 4), (4, 2)):
            with self.subTest(made=made):
                pack.mkdir(parents=True, exist_ok=True)
                hd_pack._manifest(self.cfg, made)
                QImage(64 * made, 64 * made, QImage.Format_RGB888).save(str(pack / f"{FP[0]}.png"))
                p = hd_pack.plan(self.cfg, made)
                self.assertEqual((p.done, len(p.todo), p.replaces), (1, 0, 0))
                p = hd_pack.plan(self.cfg, other)
                self.assertEqual((p.done, [i.fingerprint for i in p.todo], p.replaces), (0, [FP[0]], made))
                self.assertEqual(p.gpu_bytes(), (64 * other) ** 2 * 4 * 4 // 3)

    def test_alpha_holes_are_filled_and_alpha_kept(self):
        self.picture(self.t / "src.png", 16, 8, alpha_hole=True)
        info = hd_pack.prepare(self.t / "src.png", self.t / "in.png")
        self.assertEqual(info["alpha"][:8], b"\0" * 8)
        rgb = QImage(str(self.t / "in.png")).convertToFormat(QImage.Format_RGBA8888)
        src = QImage(str(self.t / "src.png")).convertToFormat(QImage.Format_RGBA8888)
        # A covered texel keeps its colour exactly; a hole gets its neighbours' (not black).
        self.assertEqual(rgb.pixelColor(12, 3).rgb(), src.pixelColor(12, 3).rgb())
        hole = rgb.pixelColor(2, 3)
        self.assertGreater(hole.red(), 80)
        self.assertEqual(hole.alpha(), 255)
        # A normal map is left out.
        img = QImage(8, 8, QImage.Format_RGBA8888)
        img.fill(QColor(128, 128, 255))
        img.save(str(self.t / "normal.png"))
        self.assertEqual(hd_pack.prepare(self.t / "normal.png", self.t / "in2.png"), "normal map")
        # So is a blend map (pure red and green over black).
        img = QImage(64, 64, QImage.Format_RGBA8888)
        for y in range(64):
            for x in range(64):
                img.setPixelColor(x, y, QColor(255, 0, 0) if (x // 8 + y // 8) % 3 == 0 else
                                  QColor(0, 255, 0) if (x // 8 + y // 8) % 3 == 1 else QColor(0, 0, 0))
        img.save(str(self.t / "blend.png"))
        self.assertEqual(hd_pack.prepare(self.t / "blend.png", self.t / "in3.png"), "data map")
        # A colourful but natural picture is colour.
        self.picture(self.t / "art.png", 64, 64, colour=(200, 60, 20))
        self.assertIsInstance(hd_pack.prepare(self.t / "art.png", self.t / "in4.png"), dict)

    def a_png_exists(self):
        # Cancel once the first texture is on disk (deterministic, unlike counting progress calls).
        return any(hd_pack.pack_dir(self.cfg).glob("*.png"))

    def test_the_job_makes_resumes_and_says_why_it_stopped(self):
        rows = [(FP[0], "k_DXT1", 64, 64, "BC1"), (FP[1], "k_DXT4_5", 128, 64, "BC3"),
                (FP[2], "k_DXT1", 64, 128, "BC1"), (FP[3], "k_DXN", 64, 64, "BC5"),
                (FP[4], "k_DXT1", 64, 64, "BC1")]
        self.write_dump(rows)
        normal = QImage(64, 64, QImage.Format_RGBA8888)
        normal.fill(QColor(128, 128, 255))
        normal.save(str(self.dump / f"{FP[4]}.png"))
        with self.assertRaises(RuntimeError):     # not installed
            hd_pack.make(self.cfg, 2)
        self.fake_upscaler()
        self.assertTrue(hd_pack.installed(self.cfg))
        # Stopped after the first texture's batch: what was made stays.
        with mock.patch.object(hd_pack, "BATCH_MAX", 1):
            with self.assertRaises(fetch.Cancelled):
                hd_pack.make(self.cfg, 2, cancelled=self.a_png_exists)
        pack = hd_pack.pack_dir(self.cfg)
        made = sorted(p.stem for p in pack.glob("*.png"))
        self.assertTrue(set(made) < {FP[0], FP[1], FP[2]})
        self.assertFalse(hd_pack.work_dir(self.cfg).exists())
        result = hd_pack.make(self.cfg, 2)
        self.assertEqual(result.made, 3 - len(made))
        self.assertEqual(hd_pack.plan(self.cfg, 2).skipped["normal maps"], 2)  # the DXN one and FP[4]
        self.assertEqual(sorted(p.stem for p in pack.glob("*.png")), [FP[0], FP[1], FP[2]])
        self.assertEqual(hd_pack.existing_scale(self.cfg), 2)
        self.assertEqual(hd_pack.plan(self.cfg, 2).todo, [])
        # Then 4x: everything is to remake, and making it replaces every file; then back to 2x.
        self.assertEqual((len(hd_pack.plan(self.cfg, 4).todo), hd_pack.plan(self.cfg, 4).replaces), (3, 2))
        hd_pack.make(self.cfg, 4)
        self.assertEqual(QImage(str(pack / f"{FP[0]}.png")).width(), 256)
        self.assertEqual((hd_pack.plan(self.cfg, 4).todo, hd_pack.existing_scale(self.cfg)), ([], 4))
        self.assertEqual(len(hd_pack.plan(self.cfg, 2).todo), 3)
        hd_pack.make(self.cfg, 2)
        self.assertEqual(QImage(str(pack / f"{FP[0]}.png")).width(), 128)
        out = QImage(str(pack / f"{FP[1]}.png"))
        self.assertEqual((out.width(), out.height()), (256, 128))
        self.assertTrue(out.hasAlphaChannel())
        self.assertEqual(out.pixelColor(10, 64).alpha(), 0)       # the cut-out half
        self.assertEqual(out.pixelColor(240, 64).alpha(), 255)
        self.assertFalse(QImage(str(pack / f"{FP[0]}.png")).hasAlphaChannel())
        # The stand-in's darkening is undone: the mean colour matches the source's.
        src = QImage(str(self.dump / f"{FP[0]}.png"))
        mine = QImage(str(pack / f"{FP[0]}.png"))
        def mean_red(img):
            return sum(img.pixelColor(x, y).red() for x in range(0, img.width(), 4)
                       for y in range(0, img.height(), 4)) / ((img.width() // 4) * (img.height() // 4))
        self.assertLess(abs(mean_red(src) - mean_red(mine)), 4)
        # In a cut-out texture, over the texels it shows (the filled holes do not count).
        src = QImage(str(self.dump / f"{FP[1]}.png"))
        mine = QImage(str(pack / f"{FP[1]}.png"))
        def shown_red(img):
            reds = [img.pixelColor(x, y).red() for x in range(0, img.width(), 2) for y in range(0, img.height(), 2)
                    if img.pixelColor(x, y).alpha() == 255]
            return sum(reds) / len(reds)
        self.assertLess(abs(shown_red(src) - shown_red(mine)), 4)
        pack_info = texture_packs.read_pack(pack)
        self.assertEqual((pack_info.title, pack_info.files, pack_info.problem),
                         ("HD pack (made on this PC)", 3, ""))
        # The upscaler failing: its message, and nothing half-written.
        (pack / f"{FP[2]}.png").unlink()
        with mock.patch.dict(os.environ, {"FAKE_UPSCALER_FAIL": "1"}):
            with self.assertRaises(RuntimeError) as e:
                hd_pack.make(self.cfg, 2)
        self.assertIn("vkCreateInstance failed", str(e.exception))
        self.assertFalse((pack / f"{FP[2]}.png").exists())
        # A changed program is not run.
        (hd_pack.tool_dir(self.cfg) / hd_pack._program_name()).write_text("print('changed')\n")
        with self.assertRaises(RuntimeError) as e:
            hd_pack.make(self.cfg, 2)
        self.assertIn("changed since it was downloaded", str(e.exception))

    def test_the_job_writes_compressed_textures_and_compresses_the_pngs_made_before(self):
        rows = [(FP[0], "k_DXT1", 64, 64, "BC1"), (FP[1], "k_DXT4_5", 128, 64, "BC3"),
                (FP[2], "k_DXT1", 64, 128, "BC1")]
        self.write_dump(rows)
        self.fake_upscaler()
        # The PNG pack a build without the compressor makes: the reference for the quality below.
        reference = self.t / "reference"
        hd_pack.make(self.cfg, 2)
        shutil.copytree(hd_pack.pack_dir(self.cfg), reference)
        shutil.rmtree(hd_pack.pack_dir(self.cfg))
        # Stopped after the first texture, still without the compressor: PNGs.
        with mock.patch.object(hd_pack, "BATCH_MAX", 1):
            with self.assertRaises(fetch.Cancelled):
                hd_pack.make(self.cfg, 2, cancelled=self.a_png_exists)
        pack = hd_pack.pack_dir(self.cfg)
        before = sorted(p.stem for p in pack.glob("*.png"))
        self.assertTrue(before)
        # With the compressor: the PNGs made before are compressed first, the rest written as DDS.
        real = self.compressor()
        p = hd_pack.plan(self.cfg, 2)
        self.assertTrue(p.compressed)
        self.assertEqual(p.png_done, len(before))
        logs = []
        result = hd_pack.make(self.cfg, 2, log=logs.append)
        self.assertTrue(result.compressed)
        self.assertEqual(result.converted, len(before))
        self.assertEqual(result.made, 3 - len(before))
        self.assertEqual(sum(result.formats.values()), result.made)
        self.assertEqual(list(pack.glob("*.png")), [])
        self.assertEqual(sorted(x.stem for x in pack.glob("*.dds")), [FP[0], FP[1], FP[2]])
        self.assertIn('format = "dds"', (pack / "pack.toml").read_text())
        self.assertFalse(hd_pack.work_dir(self.cfg).exists())
        p = hd_pack.plan(self.cfg, 2)
        self.assertEqual((p.done, p.png_done, p.todo), (3, 0, []))
        info = texture_packs.read_pack(pack)
        self.assertEqual((info.files, info.png_files, info.problem), (3, 0, ""))
        for fp, w, h in ((FP[0], 128, 128), (FP[1], 256, 128), (FP[2], 128, 256)):
            header = texture_packs.read_texture_header(pack / f"{fp}.dds")
            self.assertEqual((header.width, header.height, header.problem), (w, h, ""))
        if not real:
            print("(stand-in compressor: the quality checks are skipped)", file=sys.stderr)
            return
        # The real compressor: the cut-out is BC7; each texture decodes back close to the PNG the
        # same job writes without compression (alpha too: no holes in the cut-out half).
        self.assertEqual(texture_packs.read_texture_header(pack / f"{FP[1]}.dds").format, "BC7")
        tool = Path(os.environ["FORZA_TEXCOMPRESS"])
        for fp in (FP[0], FP[1], FP[2]):
            with self.subTest(fp=fp):
                data = test_texture_compress.decode(tool, pack / f"{fp}.dds", self.t / f"{fp}.rgba")[0]
                png = QImage(str(reference / f"{fp}.png")).convertToFormat(QImage.Format_RGBA8888)
                want = bytes(png.constBits())[: png.width() * png.height() * 4]
                self.assertGreater(test_texture_compress.psnr(want, data), 38)
                flips = sum(1 for i in range(3, len(want), 4) if abs(want[i] - 128) >= 32 and
                            (want[i] >= 128) != (data[i] >= 128))
                self.assertEqual(flips, 0)

    def test_a_compressed_pack_at_another_scale_is_remade(self):
        """The scale switch with DDS files: a 2x DDS pack counts as nothing made at 4x, making it
        replaces every file, and a PNG written over a DDS of the other scale (a build without the
        compressor) takes the DDS away (the game would use the DDS first)."""
        self.write_dump([(FP[0], "k_DXT1", 64, 64, "BC1"), (FP[1], "k_DXT4_5", 128, 64, "BC3")])
        self.fake_upscaler()
        self.compressor()
        pack = hd_pack.pack_dir(self.cfg)
        hd_pack.make(self.cfg, 2)
        self.assertEqual(sorted(x.name for x in pack.glob(f"{FP[0]}.*")), [f"{FP[0]}.dds"])
        self.assertEqual(texture_packs.read_texture_header(pack / f"{FP[0]}.dds").width, 128)
        p = hd_pack.plan(self.cfg, 4)
        self.assertEqual((p.done, p.png_done, len(p.todo), p.replaces), (0, 0, 2, 2))
        result = hd_pack.make(self.cfg, 4)
        self.assertEqual((result.made, result.converted), (2, 0))
        self.assertEqual(texture_packs.read_texture_header(pack / f"{FP[0]}.dds").width, 256)
        self.assertEqual((hd_pack.plan(self.cfg, 4).todo, hd_pack.existing_scale(self.cfg)), ([], 4))
        # Back to 2x without the compressor: PNGs, and no DDS of 4x left to win over them.
        self.no_compressor()
        p = hd_pack.plan(self.cfg, 2)
        self.assertEqual((p.done, len(p.todo), p.replaces, p.compressed), (0, 2, 4, False))
        hd_pack.make(self.cfg, 2)
        self.assertEqual(sorted(x.name for x in pack.iterdir() if x.suffix in (".png", ".dds")),
                         [f"{FP[0]}.png", f"{FP[1]}.png"])
        self.assertIn('format = "png"', (pack / "pack.toml").read_text())


if __name__ == "__main__":
    unittest.main()
