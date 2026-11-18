"""Launcher-only validation; the capability fixture contains synthetic bytes."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import game, native_scene, paths  # noqa: E402
from forza_launcher.config import Config, render_toml  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    from forza_launcher.ui.settings_page import SettingsPage
except ImportError:
    QApplication = None


class NativeSceneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.cfg = Config(self.folder / "launcher.json")
        self.install = game.GameInstall(self.folder, self.folder / game.EXE)
        self.binary = self.folder / ("rexgpu-xenos.dll" if paths.IS_WINDOWS else "librexgpu-xenos.so")
        self.binary.write_bytes(b"test renderer")
        self.capability = self.folder / native_scene.CAPABILITY

    def mark_capable(self):
        digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.capability.write_text(json.dumps({"api": 1, "renderer_sha256": digest}))

    def launch(self):
        args, notes = [], []
        extra = game.split_args(self.cfg.advanced.get("extra_args", ""))
        native_scene.configure(self.cfg, self.install, args, notes, extra)
        return args, notes

    def test_default_and_exact_pair(self):
        self.mark_capable()
        self.assertEqual(self.launch(), ([], []))
        self.cfg.settings.update(fh1_native_scene_width=1920, fh1_native_scene_height=1080)
        self.assertEqual(self.launch()[0], ["--fh1_native_scene_width=1920", "--fh1_native_scene_height=1080",
                                            "--fh1_output_width=1280", "--fh1_output_height=720"])
        self.assertNotIn("fh1_native_scene_width", render_toml(self.cfg))
        self.assertNotIn("fh1_native_scene_height", render_toml(self.cfg))

    def test_bad_pairs_and_bounds(self):
        self.mark_capable()
        for width, height in ((1920, 0), (0, 1080), (8961, 1080), (1920, 5041), (-1, 1080), (True, 1080)):
            self.cfg.settings.update(fh1_native_scene_width=width, fh1_native_scene_height=height)
            with self.subTest(width=width, height=height), self.assertRaises(RuntimeError):
                self.launch()
        self.cfg.settings.update(fh1_native_scene_width=8960, fh1_native_scene_height=5040)
        self.assertEqual(native_scene.pair(self.cfg.settings), (8960, 5040))

    def test_missing_malformed_wrong_version_and_mismatch(self):
        self.cfg.settings.update(fh1_native_scene_width=1920, fh1_native_scene_height=1080)
        for payload in (None, "{", '{"api":2}', '{"api":true}', '{"api":1,"renderer_sha256":"wrong"}'):
            if payload is None:
                self.capability.unlink(missing_ok=True)
            else:
                self.capability.write_text(payload)
            self.assertFalse(native_scene.available(self.cfg, self.install))
            self.assertEqual(self.launch()[0], [])
            self.assertIn("unavailable", self.launch()[1][0])
        self.mark_capable()
        self.assertTrue(native_scene.available(self.cfg, self.install))
        self.binary.write_bytes(b"different renderer")
        self.assertFalse(native_scene.available(self.cfg, self.install))

    def test_backend_and_plugin_overrides_are_not_supported(self):
        self.mark_capable()
        for backend in ("d3d12", "regpu"):
            self.cfg.settings["gpu_backend"] = backend
            self.assertFalse(native_scene.available(self.cfg, self.install))
        self.cfg.settings["gpu_backend"] = "vulkan"
        for extra in ("--gpu_backend=d3d12", "--gpu_plugin=regpu"):
            self.cfg.advanced["extra_args"] = extra
            self.assertFalse(native_scene.available(self.cfg, self.install))
        self.cfg.advanced["extra_args"] = ""
        self.cfg.advanced["extras_toml"] = 'gpu_plugin = "regpu"'
        self.assertFalse(native_scene.available(self.cfg, self.install))

    def test_explicit_scene_arguments_do_not_duplicate_and_other_modes_win(self):
        self.mark_capable()
        self.cfg.settings.update(fh1_native_scene_width=1920, fh1_native_scene_height=1080)
        self.cfg.advanced["extra_args"] = "--fh1_native_scene_width=1600"
        self.assertEqual(self.launch()[0], [])
        self.cfg.advanced["extra_args"] = ""
        for key, value in (("fh1_upscaler", "quality"), ("present_effect", "fsr3"), ("scene_resolution", "540"),
                           ("forza_antialiasing", "smaa"), ("forza_ultrawide", True)):
            with patch.dict(self.cfg.settings, {key: value}):
                args, notes = self.launch()
                self.assertEqual(args, [])
                self.assertTrue(notes)
        for kw in ({"vr": True}, {"battery_saver": True}):
            args, notes = [], []
            native_scene.configure(self.cfg, self.install, args, notes, [], **kw)
            self.assertEqual(args, [])
            self.assertTrue(notes)

    def test_launch_plan_keeps_output_and_hud(self):
        self.mark_capable()
        self.cfg.settings.update(fh1_native_scene_width=1920, fh1_native_scene_height=1080,
                                 resolution="1440p", resolution_scale=2)
        plan = game.build_plan(self.cfg, self.install, write=False)
        self.assertIn("--fh1_native_scene_width=1920", plan.argv)
        self.assertIn("--fh1_native_scene_height=1080", plan.argv)
        self.assertIn("--fh1_output_width=2560", plan.argv)
        self.assertIn("--fh1_output_height=1440", plan.argv)
        self.assertEqual(self.cfg.settings["resolution"], "1440p")
        self.assertEqual(self.cfg.settings["resolution_scale"], 2)

    def test_bad_saved_dimension_is_not_silently_clamped(self):
        self.cfg.settings.update(fh1_native_scene_width=10000, fh1_native_scene_height=1080)
        self.cfg.save()
        loaded = Config(self.cfg.path)
        self.assertEqual(native_scene.pair(loaded.settings), (0, 0))
        self.assertTrue(any("Invalid custom scene dimensions" in n for n in loaded.notices))

    def test_persisted_axis_limits(self):
        self.cfg.settings.update(fh1_native_scene_width=8960, fh1_native_scene_height=5040)
        self.cfg.save()
        loaded = Config(self.cfg.path)
        self.assertEqual(native_scene.pair(loaded.settings), (8960, 5040))

    def test_loader_checks_executable_folder_not_sdk_search_path(self):
        self.mark_capable()
        other = self.folder / "sdk-lib"
        other.mkdir()
        alternate = other / self.binary.name
        alternate.write_bytes(b"other renderer")
        digest = hashlib.sha256(alternate.read_bytes()).hexdigest()
        (other / native_scene.CAPABILITY).write_text(json.dumps({"api": 1, "renderer_sha256": digest}))
        self.install.lib_dirs = [other]
        for windows in (False, True):
            with patch.object(paths, "IS_WINDOWS", windows):
                # Supply each platform's actual plugin name beside the executable.
                name = "rexgpu-xenos.dll" if windows else "librexgpu-xenos.so"
                (self.folder / name).write_bytes(b"test renderer")
                (other / name).write_bytes(b"other renderer")
                self.assertTrue(native_scene.available(self.cfg, self.install))
                (self.folder / name).write_bytes(b"not the attested renderer")
                self.assertFalse(native_scene.available(self.cfg, self.install))
        self.capability.unlink()
        self.assertFalse(native_scene.available(self.cfg, self.install))

    def test_other_build_configuration_is_ambiguous(self):
        self.mark_capable()
        debug = self.folder / ("rexgpu-xenosd.dll" if paths.IS_WINDOWS else "librexgpu-xenosd.so")
        debug.write_bytes(b"debug renderer")
        self.assertFalse(native_scene.available(self.cfg, self.install))
        debug.unlink()
        self.assertTrue(native_scene.available(self.cfg, self.install))

    def test_output_presets_and_custom_sizes(self):
        self.mark_capable()
        self.cfg.settings.update(fh1_native_scene_width=8960, fh1_native_scene_height=5040)
        for resolution, expected in (("", (1280, 720)), ("480p", (640, 480)),
                                     ("900P", (1600, 900)), ("1_800p", (3200, 1800)),
                                     ("2160p", (3840, 2160)), (" 8960 X 5040 ", (8960, 5040))):
            self.cfg.settings["resolution"] = resolution
            args, _ = self.launch()
            self.assertIn(f"--fh1_output_width={expected[0]}", args)
            self.assertIn(f"--fh1_output_height={expected[1]}", args)
        for resolution in ("8961x720", "1280x5041", "0x720", "nonsense"):
            self.cfg.settings["resolution"] = resolution
            with self.subTest(resolution=resolution), self.assertRaises(RuntimeError):
                self.launch()

    def test_advanced_output_and_scene_overrides_are_left_alone(self):
        self.mark_capable()
        self.cfg.settings.update(fh1_native_scene_width=1920, fh1_native_scene_height=1080)
        for extra in ("--resolution=1080p", "--fh1_output_width=1920", "--fh1_upscaler=quality",
                      "--render_target_path_vulkan=fsi", "--scene_resolution=540"):
            self.cfg.advanced["extra_args"] = extra
            args, notes = self.launch()
            self.assertEqual(args, [])
            self.assertTrue(notes)
        self.cfg.advanced["extra_args"] = ""
        self.cfg.advanced["extras_toml"] = "fh1_native_scene_width = 1000"
        self.assertEqual(self.launch()[0], [])

    @unittest.skipIf(QApplication is None, "PySide6 is not installed")
    def test_controls_follow_capability_backend_and_mode(self):
        app = QApplication.instance() or QApplication([])
        class Window:
            def status(self, *args): pass
            def write_toml(self): pass
            def preview_text(self): return ""
            def reset_settings(self): pass
        with patch.object(game, "find_game", return_value=(self.install, [])):
            page = SettingsPage(self.cfg, Window())
            try:
                self.assertFalse(page.rows[native_scene.WIDTH].control.isEnabled())
                self.mark_capable()
                page._apply_dependencies()
                self.assertTrue(page.rows[native_scene.WIDTH].control.isEnabled())
                self.assertTrue(page.rows[native_scene.HEIGHT].control.isEnabled())
                with patch("forza_launcher.ui.settings_page.power.decide") as decide:
                    decide.return_value.active = True
                    page._apply_dependencies()
                    self.assertFalse(page.rows[native_scene.WIDTH].control.isEnabled())
                    self.assertFalse(page.rows[native_scene.HEIGHT].control.isEnabled())
                    decide.assert_called_with(self.cfg.effective_settings())
                    decide.return_value.active = False
                    page._apply_dependencies()
                    self.assertTrue(page.rows[native_scene.WIDTH].control.isEnabled())
                    self.assertTrue(page.rows[native_scene.HEIGHT].control.isEnabled())
                self.cfg.settings["gpu_backend"] = "d3d12"
                page._apply_dependencies()
                self.assertFalse(page.rows[native_scene.WIDTH].control.isEnabled())
                self.cfg.settings.update(gpu_backend="vulkan", present_effect="fsr3")
                page._apply_dependencies()
                self.assertFalse(page.rows[native_scene.HEIGHT].control.isEnabled())
            finally:
                page._save_timer.stop()
                page.deleteLater()


if __name__ == "__main__":
    unittest.main()
