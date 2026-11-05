"""Make decal pack (#659, decal_pack.py): reading a decal .carbin (the console's tiled layout and
channel order), its fingerprint, and the job with the stand-in upscaler of test_hd_pack (no
download, no GPU, no game files: a made-up Livery.zip). Disposable folders only."""
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import decal_pack, fetch, texture_packs  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
import test_hd_pack  # noqa: E402

try:
    from PySide6.QtGui import QImage
except ImportError:
    QImage = None


def tiled_texel(x, y, width):
    """XGAddress2DTiledOffset for 4-byte texels, a texel at a time (the reference the run-based
    table in decal_pack must agree with)."""
    aligned = (width + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (aligned >> 5)) << 9
    micro = ((x & 7) + ((y & 6) << 2)) << 2
    offset = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 8) << 5) + ((y & 1) << 4)
    return (((offset & ~511) << 3) + ((offset & 448) << 2) + (offset & 63) + ((y & 16) << 7)
            + (((((y & 8) >> 2) + (x >> 3)) & 3) << 6)) >> 2


def carbin(rgba, width, height, levels=9):
    """A decal .carbin as the game has them: a header (zeros here but for the texture's fields),
    the largest level tiled with A, R, G, B a texel (the dump's red is the stored blue), and a
    tail standing in for the mips."""
    base = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            r, g, b, a = rgba[(y * width + x) * 4:(y * width + x) * 4 + 4]
            i = tiled_texel(x, y, width) * 4
            base[i:i + 4] = bytes((a, b, g, r))
    head = bytearray(decal_pack._DATA)
    struct.pack_into(">IIII", head, decal_pack._WIDTH, width, height, levels, decal_pack._ARGB8)
    return bytes(head) + bytes(base) + b"\x00" * 1000


def logo(width, height, colour=(200, 30, 40)):
    """A filled box in the middle (opaque), white with alpha 0 around it, as the game's decals."""
    out = bytearray()
    for y in range(height):
        for x in range(width):
            inside = width // 4 <= x < 3 * width // 4 and height // 4 <= y < 3 * height // 4
            out += bytes(colour + (255,)) if inside else b"\xff\xff\xff\x00"
    return bytes(out)


@unittest.skipIf(QImage is None, "PySide6 is not installed")
class DecalPackTests(unittest.TestCase):
    setUp = test_hd_pack.HdPackTests.setUp
    no_compressor = test_hd_pack.HdPackTests.no_compressor
    fake_upscaler = test_hd_pack.HdPackTests.fake_upscaler

    def livery(self, decals):
        self.cfg.d["game_root"] = str(self.t / "game")
        zip_path = decal_pack.livery_zip(self.cfg)
        zip_path.parent.mkdir(parents=True)
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("decals/base.carbin", b"\x00" * 490)      # not a decal
            z.writestr("vinyls/shape001.carbin", b"\x00" * 4000)
            for name, data in decals.items():
                z.writestr(f"decals/{name}.carbin", data)

    def test_untile_and_channels(self):
        for w, h in ((256, 256), (256, 128), (64, 32)):
            with self.subTest(size=(w, h)):
                offsets = decal_pack._tiled_offsets(w, h)
                self.assertEqual(sorted(offsets), list(range(0, w * h * 4, 16)))   # each run once
                rgba = bytes((x * 7 + y) & 255 for y in range(h) for x in range(w * 4))
                parsed = decal_pack.parse(carbin(rgba, w, h))
                self.assertEqual(parsed[:2], (w, h))
                self.assertEqual(decal_pack.untile(parsed[2], w, h), rgba)
        self.assertIsNone(decal_pack.parse(b"\x00" * 490))
        bad = bytearray(carbin(logo(64, 64), 64, 64))
        struct.pack_into(">I", bad, decal_pack._FORMAT, 0x1A200152)   # another texture format
        self.assertIsNone(decal_pack.parse(bytes(bad)))

    def test_fingerprint(self):
        """Packs::Fingerprint (SDK patch 0280) of a k_8_8_8_8 texture as the decals are bound. The
        formula was checked against the game: the 43 decals a Paint Shop visit dumped had these
        names. Pinned here for a made-up texture (the value from the SDK's own xxHash)."""
        base = bytes(range(256)) * 1024
        self.assertEqual(decal_pack.fingerprint(base, 256, 256), "6a9ae7fc2837f1b1")
        self.assertNotEqual(decal_pack.fingerprint(base[:131072], 256, 128),
                            decal_pack.fingerprint(base[:131072], 128, 256))

    def test_make(self):
        a, b = logo(64, 64), logo(64, 32, (20, 40, 200))
        self.livery({"decal001": carbin(a, 64, 64), "decal002": carbin(b, 64, 32),
                     "decal003": carbin(a, 64, 64)})                      # the same picture twice
        p = decal_pack.plan(self.cfg, 4)
        self.assertEqual((p.found, p.decals, p.done, p.replaces), (True, 3, 0, 0))
        with self.assertRaises(RuntimeError):     # the upscaler is not installed
            decal_pack.make(self.cfg, 4)
        self.fake_upscaler()
        result = decal_pack.make(self.cfg, 4)
        self.assertEqual((result.made, result.failed), (2, []))
        pack = decal_pack.pack_dir(self.cfg)
        fp_a = decal_pack.fingerprint(decal_pack.parse(carbin(a, 64, 64))[2], 64, 64)
        out = QImage(str(pack / f"{fp_a}.png")).convertToFormat(QImage.Format_RGBA8888)
        self.assertEqual((out.width(), out.height()), (256, 256))
        # The box keeps its colour and gets its alpha from the upscaled alpha; the colour under
        # the transparent texels next to it is the box's, not the white the game stores there.
        inside, edge = out.pixelColor(128, 128), out.pixelColor(128, 60)
        self.assertEqual(inside.alpha(), 255)
        self.assertLess(abs(inside.red() - 200), 30)
        self.assertLess(edge.alpha(), 40)
        self.assertLess(edge.blue(), 150)
        self.assertEqual(out.pixelColor(2, 2).alpha(), 0)
        self.assertFalse(decal_pack.work_dir(self.cfg).exists())
        self.assertEqual(decal_pack.existing_scale(self.cfg), 4)
        p = decal_pack.plan(self.cfg, 4)
        self.assertEqual((p.done, p.todo), (3, 0))
        self.assertEqual(texture_packs.read_pack(pack).title, "Decal pack (made on this PC)")
        self.assertTrue(texture_packs.read_pack(pack).made_here)
        # Another scale replaces the pack.
        self.assertEqual(decal_pack.plan(self.cfg, 2).replaces, 4)
        self.assertEqual(decal_pack.make(self.cfg, 2).made, 2)
        self.assertEqual(QImage(str(pack / f"{fp_a}.png")).width(), 128)

    def test_cancel_keeps_what_was_made(self):
        self.livery({f"decal{n:03}": carbin(logo(64, 64, (10 * n, 40, 90)), 64, 64) for n in range(1, 4)})
        self.fake_upscaler()
        pack = decal_pack.pack_dir(self.cfg)
        with mock.patch.object(test_hd_pack.hd_pack, "BATCH_MAX", 1):
            with self.assertRaises(fetch.Cancelled):
                decal_pack.make(self.cfg, 2, cancelled=lambda: any(pack.glob("*.png")))
        made = len(list(pack.glob("*.png")))
        self.assertGreaterEqual(made, 1)
        p = decal_pack.plan(self.cfg, 2)
        self.assertEqual(p.done, made)
        self.assertEqual(decal_pack.make(self.cfg, 2).made, 3 - made)

    def test_only_the_decal_pack_asks_to_be_preloaded(self):
        """Its pack.toml says preload = true (SDK patch 0436 reads it); the game gets no global
        texture_replace_preload, so an HD pack is still read as its textures load."""
        self.livery({"decal001": carbin(logo(64, 64), 64, 64)})
        self.fake_upscaler()
        decal_pack.make(self.cfg, 2)
        manifest = (decal_pack.pack_dir(self.cfg) / texture_packs.MANIFEST).read_text()
        self.assertIn("\npreload = true\n", manifest)
        hd = texture_packs.textures_dir(self.cfg) / texture_packs.HD_PACK
        hd.mkdir()
        QImage(8, 8, QImage.Format_RGB888).save(str(hd / f"{'1' * 16}.png"))
        self.cfg.settings["texture_replace"] = True
        self.assertEqual(texture_packs.launch_args(self.cfg), ["--texture_packs=hd-pack|decal-pack"])

    def test_order_below_the_hd_pack(self):
        root = texture_packs.textures_dir(self.cfg)
        for folder in ("aaa", texture_packs.HD_PACK, texture_packs.DECAL_PACK):
            (root / folder).mkdir(parents=True)
            QImage(8, 8, QImage.Format_RGB888).save(str(root / folder / f"{'1' * 16}.png"))
        self.assertEqual([p.folder for p in texture_packs.listed(self.cfg)],
                         [texture_packs.HD_PACK, texture_packs.DECAL_PACK, "aaa"])


    def test_the_page(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
        from forza_launcher.ui.texture_packs_page import TexturePacksPage
        self.app = QApplication.instance() or QApplication([])
        page = TexturePacksPage(self.cfg, window=None)
        self.addCleanup(lambda: (page.deleteLater(), QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)))
        self.assertIn("Livery.zip) are not there", page.decal_estimate.text())
        self.assertFalse(page.decal_button.isEnabled())
        self.livery({"decal001": carbin(logo(64, 64), 64, 64)})
        page.refresh()
        self.assertIn("1 decal files: 1 to upscale.", page.decal_estimate.text())
        self.assertTrue(page.decal_button.isEnabled())
        self.fake_upscaler()
        page.make_decal_pack()
        page.task.wait(20000)
        for _ in range(20):
            QCoreApplication.processEvents()
            if page.task is None:
                break
        self.assertIn("Made 1 decal(s)", page.decal_status.text())
        self.assertIn("All 1 decals are in the decal pack.", page.decal_estimate.text())
        self.assertFalse(page.decal_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
