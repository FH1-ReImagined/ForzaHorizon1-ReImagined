import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import dlss, gpu, ingame, schema
from forza_launcher.config import Config


class InGameGraphicsTests(unittest.TestCase):
    def test_minimap_orientation_round_trip(self):
        self.assertFalse(self.cfg.settings["forza_minimap_camera"])
        for value in ("true", "false"):
            self.request(forza_minimap_camera=value)
            self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
            self.assertEqual(Config(self.cfg.path).settings["forza_minimap_camera"], value == "true")

    def test_minimap_orientation_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            ingame.translate({"version": 1, "settings": {"forza_minimap_camera": "camera"}},
                             self.cfg.settings)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root)
        self.pending = self.root / ingame.FILENAME

    def request(self, **values):
        self.pending.write_text(json.dumps({"version": 1, "settings": values}))

    def test_native_output_and_filtering_are_imported_and_persisted(self):
        self.cfg.settings["present_effect"] = "fsr3"
        self.request(output="1440p", upscaler="off", resolution_scale="2",
                     frame_rate="120", vsync="true", anisotropic_override="5",
                     forza_hud_edges="false")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        self.assertFalse(self.pending.exists())
        saved = Config(self.cfg.path)
        self.assertEqual(saved.settings["resolution"], "1440p")
        self.assertEqual(saved.settings["resolution_scale"], 2)
        self.assertEqual(saved.settings["frame_rate"], 120)
        self.assertEqual(saved.settings["present_effect"], "bilinear")
        self.assertEqual(saved.settings["anisotropic_override"], 5)
        self.assertFalse(saved.settings["forza_hud_edges"])

    def test_native_720p_output_survives_save_and_reload(self):
        self.request(output="720p", upscaler="off", anti_aliasing="game",
                     resolution_scale="1")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        saved = Config(self.cfg.path)
        self.assertEqual(saved.settings["resolution"], "720p")
        self.assertEqual(saved.settings["resolution_scale"], 1)
        self.assertEqual(saved.settings["forza_antialiasing"], "game")
        # Reconstruction selectors have separate, higher-output schemas.
        self.assertIn(saved.settings["fsr3_output_resolution"], ("1080p", "1440p", "4k"))

    def test_current_720p_does_not_reset_reconstruction_output(self):
        previous = self.cfg.settings["fsr3_output_resolution"]
        self.request(output="720p", upscaler="off", anti_aliasing="game", frame_rate="120")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        saved = Config(self.cfg.path)
        self.assertEqual(saved.settings["resolution"], "720p")
        self.assertEqual(saved.settings["fsr3_output_resolution"], previous)
        self.assertEqual(saved.settings["frame_rate"], 120)

    def test_cockpit_fov_round_trip_and_stock_reset(self):
        self.assertEqual(self.cfg.settings["forza_cockpit_fov"], 0)
        for degrees in (40, 62, 75, 90, 0):
            self.request(forza_cockpit_fov=str(degrees))
            self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
            self.assertEqual(Config(self.cfg.path).settings["forza_cockpit_fov"], degrees)

    def test_invalid_cockpit_fov_rejects_whole_request(self):
        previous = dict(self.cfg.settings)
        for value in ("-1", "1", "39", "91", "nan", "inf", "62.5", "true"):
            self.request(frame_rate="120", forza_cockpit_fov=value)
            self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
            self.assertEqual(self.cfg.settings, previous)
            self.assertTrue(self.pending.exists())

    def test_all_camera_bases_round_trip_and_reject_partial_requests(self):
        values = {key: str(60 + index) for index, key in enumerate(sorted(schema.CAMERA_FOV_KEYS))}
        self.request(**values)
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        restored = Config(self.cfg.path)
        for key, value in values.items():
            self.assertEqual(restored.settings[key], int(value))
        previous = dict(self.cfg.settings)
        for key in schema.CAMERA_FOV_KEYS:
            bad = {**values, key: "91"}
            self.request(**bad)
            self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
            self.assertEqual(self.cfg.settings, previous)

    def test_camera_and_world_lighting_settings_survive_one_native_save(self):
        fovs = {key: str(40 + i * 10) for i, key in enumerate(sorted(schema.CAMERA_FOV_KEYS))}
        self.request(**fovs, world_shadow_filter="softer", world_ambient_occlusion="high")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        saved = Config(self.cfg.path)
        for key, value in fovs.items():
            self.assertEqual(saved.settings[key], int(value))
        self.assertEqual(saved.settings["world_shadow_filter"], "softer")
        self.assertEqual(saved.settings["world_ambient_occlusion"], "high")

    def test_ultrawide_live_settings_preserve_launcher_rendering(self):
        self.cfg.settings.update(forza_ultrawide=True, resolution="720p", resolution_scale=1)
        self.request(frame_rate="120", vsync="false", anisotropic_override="5",
                     forza_hud_edges="true", forza_performance_overlay="fps")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        saved = Config(self.cfg.path)
        self.assertTrue(saved.settings["forza_ultrawide"])
        self.assertEqual(saved.settings["resolution"], "720p")
        self.assertEqual(saved.settings["resolution_scale"], 1)
        self.assertTrue(saved.settings["forza_hud_edges"])
        self.assertEqual(saved.settings["frame_rate"], 120)

    @unittest.skipUnless("fh1_upscaler" in schema.BY_KEY, "Windows NVIDIA setting")
    def test_dlss_choice_preserves_native_output_setting(self):
        self.request(output="1440p", upscaler="quality")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        self.assertEqual(self.cfg.settings["fh1_upscaler"], "quality")
        self.assertEqual(self.cfg.settings["fh1_output_resolution"], "1440p")

    def test_fsr3_turns_off_nvidia_and_sets_the_matching_mode(self):
        self.request(output="4k", upscaler="fsr3_balanced")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        self.assertEqual(self.cfg.settings["present_effect"], "fsr3")
        self.assertEqual(self.cfg.settings["fsr3_mode"], "balanced")
        self.assertEqual(self.cfg.settings["fsr3_output_resolution"], "4k")
        if "fh1_upscaler" in schema.BY_KEY:
            self.assertEqual(self.cfg.settings["fh1_upscaler"], "off")


    def test_separate_aa_round_trip_and_exclusive_upscaling(self):
        for aa in ("game", "smaa", "fsr3_native"):
            self.request(upscaler="off", anti_aliasing=aa)
            self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
            self.assertEqual(Config(self.cfg.path).settings["forza_antialiasing"], aa)
        for mode, aa in (("quality", "smaa"), ("fsr3_quality", "fsr3_native"),
                         ("off", "integrated"), ("off", "msaa8x")):
            previous = dict(self.cfg.settings)
            self.request(upscaler=mode, anti_aliasing=aa)
            self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
            self.assertEqual(self.cfg.settings, previous)
        self.cfg.settings.update(present_effect="fsr3", fsr3_mode="quality", fh1_upscaler="off")
        self.request(anti_aliasing="smaa")
        self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
        self.request(upscaler="fsr3_quality", anti_aliasing="integrated")
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
        self.assertEqual(self.cfg.settings["forza_antialiasing"], "game")




    def test_shadow_softness_is_imported_and_validated(self):
        for value in ("soft", "softer", "console"):
            with self.subTest(value=value):
                self.request(frame_rate="60", world_shadow_filter=value)
                self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
                self.assertEqual(Config(self.cfg.path).settings["world_shadow_filter"], value)
        previous = dict(self.cfg.settings)
        for bad in ("high", "Soft", ""):
            with self.subTest(bad=bad):
                self.request(frame_rate="120", world_shadow_filter=bad)
                self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
                self.assertEqual(self.cfg.settings, previous)

    def test_ambient_occlusion_is_imported_and_validated(self):
        for value in ("low", "high", "off"):
            with self.subTest(value=value):
                self.request(frame_rate="60", world_ambient_occlusion=value)
                self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
                self.assertEqual(Config(self.cfg.path).settings["world_ambient_occlusion"], value)
        previous = dict(self.cfg.settings)
        for bad in ("on", "High", ""):
            with self.subTest(bad=bad):
                self.request(frame_rate="120", world_ambient_occlusion=bad)
                self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
                self.assertEqual(self.cfg.settings, previous)

    def test_20_and_40_fps_are_imported(self):
        for rate in (20, 40, 30):
            with self.subTest(rate=rate):
                self.request(frame_rate=str(rate))
                self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))
                self.assertEqual(Config(self.cfg.path).settings["frame_rate"], rate)

    def test_invalid_request_does_not_partially_apply(self):
        previous = dict(self.cfg.settings)
        for values in [{"frame_rate": "120", "output": "12k"},
                       {"frame_rate": "360"}, {"frame_rate": "45"}, {"resolution_scale": "0"},
                       {"vsync": "maybe"}, {"upscaler": "dlss5"},
                       {"extra_args": "--unknown=true"}]:
            with self.subTest(values=values):
                self.request(**values)
                self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
                self.assertEqual(self.cfg.settings, previous)
                self.assertTrue(self.pending.exists())

    def test_failed_config_save_keeps_the_request_for_retry(self):
        previous = dict(self.cfg.settings)
        self.request(frame_rate="120")
        with patch.object(self.cfg, "save", side_effect=OSError("read only")):
            self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
        self.assertEqual(self.cfg.settings, previous)
        self.assertTrue(self.pending.exists())
        self.assertTrue(ingame.import_pending(self.cfg, lambda _: None))

    def test_non_string_values_and_boolean_version_are_rejected(self):
        previous = dict(self.cfg.settings)
        for document in [{"version": True, "settings": {"frame_rate": "120"}},
                         {"version": 1, "settings": {"output": []}},
                         {"version": 1, "settings": {"vsync": True}}]:
            with self.subTest(document=document):
                self.pending.write_text(json.dumps(document))
                self.assertFalse(ingame.import_pending(self.cfg, lambda _: None))
                self.assertEqual(self.cfg.settings, previous)

    def test_starting_launcher_imports_settings_before_showing_them(self):
        self.cfg.save()
        self.request(frame_rate="144")
        reloaded = Config(self.cfg.path)
        self.assertEqual(reloaded.settings["frame_rate"], 144)
        self.assertFalse(self.pending.exists())

    def test_unrelated_settings_and_profile_are_preserved(self):
        self.cfg.online["mode"] = "offline"
        self.cfg.settings["async_shader_compilation"] = False
        self.request(frame_rate="90")
        ingame.import_pending(self.cfg, lambda _: None)
        self.assertEqual(self.cfg.online["mode"], "offline")
        self.assertFalse(self.cfg.settings["async_shader_compilation"])



    def test_native_nvidia_session_prepares_runtime_for_later_menu_use(self):
        self.cfg.settings.update(gpu_backend="vulkan", fh1_upscaler="off")
        with patch("forza_launcher.paths.IS_WINDOWS", True), patch.object(self.cfg, "gpu_vendor", return_value=gpu.NVIDIA):
            self.assertTrue(dlss.menu_runtime_enabled(self.cfg))
            self.assertFalse(dlss.enabled(self.cfg))
            self.cfg.settings["gpu_backend"] = "d3d12"
            self.assertFalse(dlss.menu_runtime_enabled(self.cfg))


if __name__ == "__main__":
    unittest.main()
