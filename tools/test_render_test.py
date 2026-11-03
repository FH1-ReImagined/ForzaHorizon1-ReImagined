"""Synthetic tests of tools/render_test.py's image comparison and helpers; no game images.

Run: python3 -m unittest discover -s tools -p test_render_test.py
"""
import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

import render_test as rt


NOISE = Image.effect_noise((1280 + 16, 720 + 16), 60).convert("RGB")


def scene_image(seed=1, shift=0):
    """A busy 1280x720 test picture: noise texture, shapes and a flat 'HUD' box."""
    rng = random.Random(seed)
    im = NOISE.copy()
    draw = ImageDraw.Draw(im)
    for _ in range(80):
        x, y = rng.randrange(1280), rng.randrange(720)
        draw.ellipse([x, y, x + rng.randrange(10, 120), y + rng.randrange(10, 120)],
                     fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    im = im.crop((8 + shift, 8, 8 + shift + 1280, 728))
    ImageDraw.Draw(im).rectangle([1000, 600, 1200, 680], fill=(240, 240, 240))
    return im


class CompareTests(unittest.TestCase):
    def test_identical(self):
        a = scene_image()
        r = rt.compare(a, a.copy(), {})
        self.assertEqual((r["dssim"], r["bad"], r["mae"]), (0, 0, 0))

    def test_blur_is_flagged(self):
        a = scene_image()
        r = rt.compare(a, a.filter(ImageFilter.GaussianBlur(1.5)), {})
        self.assertGreater(r["dssim"], 0.05)
        self.assertGreater(r["bad"], 0.05)

    def test_colour_shift_is_flagged(self):
        a = scene_image()
        tinted = Image.merge("RGB", [c.point(lambda v, k=k: min(255, v + k))
                                     for c, k in zip(a.split(), (25, 0, 0))])
        r = rt.compare(a, tinted, {})
        self.assertGreater(r["bad"], 0.5)

    def test_local_change_counts_blocks(self):
        a = scene_image()
        b = a.copy()
        ImageDraw.Draw(b).rectangle([100, 100, 228, 228], fill=(0, 0, 0))
        r = rt.compare(a, b, {})
        # 128x128 px at 1280x720 is 64x64 at half size: 8x8 of the 80x45 blocks (1.8 %).
        self.assertAlmostEqual(r["bad"], 64 / 3600, delta=0.006)

    def test_masks_hide_a_change(self):
        a = scene_image()
        b = a.copy()
        ImageDraw.Draw(b).rectangle([100, 100, 228, 228], fill=(0, 0, 0))
        r = rt.compare(a, b, {"masks": [[96, 96, 140, 140]]})
        self.assertEqual(r["bad"], 0)
        self.assertLess(r["dssim"], 1e-6)

    def test_size_mismatch(self):
        with self.assertRaises(ValueError):
            rt.compare(scene_image(), scene_image().resize((640, 360)), {})

    def test_best_match_picks_the_closest_frame(self):
        base = [scene_image(shift=s) for s in (0, 3, 6)]
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, im in enumerate(base):
                paths.append(Path(d) / f"b{i}.png")
                im.save(paths[-1])
            cur = Path(d) / "c.png"
            scene_image(shift=3).save(cur)
            r = rt.best_match(paths, [cur], {})
            self.assertTrue(r["baseline"].endswith("b1.png"))
            self.assertEqual(r["dssim"], 0)
            out = Path(d) / "diff.png"
            rt.diff_image(r, out)
            self.assertEqual(Image.open(out).size, (1920, 360))


class HelperTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(rt.parse_time("07:47"), 28020)
        self.assertEqual(rt.parse_time("43200"), 43200.0)

    def test_mask_scales_with_the_image(self):
        m = rt.mask_image((640, 360), [[0, 0, 640, 360]])
        self.assertEqual(m.getpixel((319, 179)), 0)
        self.assertEqual(m.getpixel((320, 180)), 255)

    def test_scenes_file(self):
        cfg = rt.load_cfg()
        captured = {s.split()[1] for sess in cfg["session"] for s in sess["steps"]
                    if s.split()[0] == "capture"}
        self.assertEqual(set(cfg["scenes"]) - captured, set())
        ops = {"first_frame", "wait", "press", "world", "at", "clock", "capture", "trace", "log"}
        for sess in cfg["session"]:
            for step in sess["steps"]:
                self.assertIn(step.split()[0], ops, step)


if __name__ == "__main__":
    unittest.main()
