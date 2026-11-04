import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
import check_hdr_capture as capture


def settings(ceiling=2):
    low, high = 0.0, 16.0
    for _ in range(60):
        k = (low + high) / 2
        if math.expm1(k) / k < (ceiling - .18) / .82:
            low = k
        else:
            high = k
    k = (low + high) / 2 if ceiling > 1 else 0
    return {"hdr_model": capture.MODEL, "width": 16, "height": 8,
            "paper_white": 200, "peak": 400, "scene_ceiling": ceiling,
            "diffuse_anchor": .18, "curve_exponent": k, "curve_denominator": math.expm1(k),
            "gamma": 0, "compare": False, "pattern": 0}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "frame"

    def tearDown(self):
        self.directory.cleanup()

    def write(self, config, output, alpha):
        Path(str(self.path) + ".json").write_text(json.dumps(config))
        output.astype("<f2").tofile(str(self.path) + ".rgba16f")
        stored = np.full(output.shape, 255, np.uint8)
        stored[:, :, 3] = alpha
        stored.tofile(str(self.path) + ".bgra8")

    def test_known_endpoints_and_translucent_protection(self):
        config = settings()
        output = np.ones((8, 16, 4))
        output[:, :, :3] = 5  # 400 nits, the selected scene ceiling.
        output[:, :4, :3] = 2.5  # 200 nits, protected overlay.
        alpha = np.zeros((8, 16), np.uint8)
        alpha[:, :4] = 1  # Even faint coverage protects the whole pixel.
        self.write(config, output, alpha)
        report = capture.check(self.path, expect_scene=True)
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["hud_max_channel_nits"], 200)
        self.assertEqual(report["peak_channel_nits"], 400)
        output[3, 10, 0] = 7.5
        self.write(config, output, alpha)
        report = capture.check(self.path)
        self.assertIn("output exceeds configured luminance bound", report["failures"])

    def test_zero_strength_and_nan_detection(self):
        output = np.ones((8, 16, 4))
        output[:, :, :3] = 2.5
        alpha = np.zeros((8, 16), np.uint8)
        self.write(settings(1), output, alpha)
        self.assertEqual(capture.check(self.path)["failures"], [])
        output[0, 0, 0] = np.nan
        self.write(settings(1), output, alpha)
        self.assertIn("non-finite output", capture.check(self.path)["failures"])

    def test_unknown_model_is_not_silently_checked(self):
        config = settings()
        del config["hdr_model"]
        with self.assertRaisesRegex(ValueError, "older or unknown"):
            capture.validate_settings(config)

    def test_grid_absolute_values(self):
        config = settings()
        config.update(width=80, height=60, pattern=2)
        grid = capture.calibration(config)
        self.assertEqual(grid[0, 0], -1)
        # First row inset centers: 18%, 50%, 75%, 100% of 200 nits.
        np.testing.assert_allclose(grid[15, [17, 32, 47, 62]] * 80, [36, 100, 150, 200])
        config["pattern"] = 3
        grid = capture.calibration(config)
        self.assertLessEqual(grid.max() * 80, 400)
        self.assertEqual(grid[45, 62] * 80, 400)


if __name__ == "__main__":
    unittest.main()
