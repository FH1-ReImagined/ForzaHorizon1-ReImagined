"""GPU detection, per-vendor presets and warnings (#52). Run: python3 -m unittest launcher/test_gpu.py"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forza_launcher import gpu, schema  # noqa: E402
from forza_launcher.config import Config, render_toml  # noqa: E402


def fake_sysfs(root, cards, board=None, vendor="Valve"):
    for n, vid in enumerate(cards):
        d = Path(root) / "class/drm" / f"card{n}" / "device"
        d.mkdir(parents=True)
        (d / "vendor").write_text(vid + "\n")
        (d / "device").write_text("0x1234\n")
    Path(root, "class/drm/card0-HDMI-A-1").mkdir(parents=True, exist_ok=True)   # a connector, not a card
    dmi = Path(root) / "devices/virtual/dmi/id"
    dmi.mkdir(parents=True)
    if board:
        (dmi / "board_name").write_text(board + "\n")
        (dmi / "board_vendor").write_text(vendor + "\n")


class Detection(unittest.TestCase):
    def test_text(self):
        for text, want in [("NVIDIA GeForce RTX 3080", gpu.NVIDIA), ("AMD Radeon RX 9060 XT", gpu.AMD),
                           ("Advanced Micro Devices, Inc.", gpu.AMD), ("Intel(R) UHD Graphics 770", gpu.INTEL),
                           ("Intel Arc A770", gpu.INTEL), ("Microsoft Basic Render Driver", ""),
                           ("Parsec Virtual Display Adapter", ""), ("", ""), ("Matrox G200", "")]:
            self.assertEqual(gpu.vendor_from_text(text), want, text)

    def test_pick_prefers_the_discrete_card(self):
        self.assertEqual(gpu.pick({gpu.INTEL, gpu.NVIDIA}), gpu.NVIDIA)
        self.assertEqual(gpu.pick({gpu.INTEL, gpu.AMD}), gpu.AMD)
        self.assertEqual(gpu.pick({gpu.AMD, gpu.NVIDIA}), gpu.NVIDIA)
        self.assertEqual(gpu.pick(set()), "")

    def test_linux_sysfs(self):
        with tempfile.TemporaryDirectory() as t:
            fake_sysfs(t, ["0x8086", "0x10de"], board="Some Board", vendor="ASUS")
            self.assertEqual(gpu.detect_linux(t).vendor, gpu.NVIDIA)
        with tempfile.TemporaryDirectory() as t:
            fake_sysfs(t, ["0x1002"])
            self.assertEqual(gpu.detect_linux(t).vendor, gpu.AMD)
        with tempfile.TemporaryDirectory() as t:
            fake_sysfs(t, ["0x1234"])
            self.assertEqual(gpu.detect_linux(t).vendor, "")

    def test_steam_deck_beats_its_amd_apu(self):
        for board in ("Jupiter", "Galileo"):
            with tempfile.TemporaryDirectory() as t:
                fake_sysfs(t, ["0x1002"], board=board)
                self.assertEqual(gpu.detect_linux(t).vendor, gpu.DECK, board)
        with tempfile.TemporaryDirectory() as t:   # another maker's board of that name
            fake_sysfs(t, ["0x1002"], board="Jupiter", vendor="Acme")
            self.assertEqual(gpu.detect_linux(t).vendor, gpu.AMD)

    def test_powershell_and_wmic(self):
        ps = json.dumps({"gpus": [{"Name": "Intel(R) UHD Graphics", "AdapterCompatibility": "Intel Corporation",
                                   "PNPDeviceID": "PCI\\VEN_8086&DEV_9BC4"},
                                  {"Name": "NVIDIA GeForce RTX 3060 Laptop GPU", "AdapterCompatibility": "NVIDIA",
                                   "PNPDeviceID": "PCI\\VEN_10DE&DEV_2560"}], "board": "ROG"})
        names, board = gpu.parse_powershell(ps)
        self.assertEqual(gpu.vendor_from_adapters(names)[0], gpu.NVIDIA)
        self.assertEqual(board, "ROG")
        one = gpu.parse_powershell(json.dumps({"gpus": {"Name": "AMD Radeon RX 7800 XT"}, "board": "Jupiter"}))
        self.assertEqual(one, (["AMD Radeon RX 7800 XT  "], "Jupiter"))
        self.assertEqual(gpu.parse_powershell("not json"), ([], ""))
        csv = "Node,AdapterCompatibility,Name\r\nPC,Advanced Micro Devices,AMD Radeon(TM) Graphics\r\n"
        self.assertEqual(gpu.vendor_from_adapters(gpu.parse_wmic_csv(csv))[0], gpu.AMD)
        # a name that says nothing, an id that does
        self.assertEqual(gpu.vendor_from_adapters(["Display adapter PCI\\VEN_10DE&DEV_1"])[0], gpu.NVIDIA)

    def test_override_and_failure(self):
        with mock.patch.dict(os.environ, {"FORZA_GPU": "intel"}):
            self.assertEqual(gpu.detect().vendor, gpu.INTEL)
        with mock.patch.dict(os.environ, {"FORZA_GPU": ""}), \
                mock.patch.object(gpu, "detect_linux", side_effect=RuntimeError), \
                mock.patch.object(gpu, "detect_windows", side_effect=RuntimeError):
            self.assertEqual(gpu.detect().vendor, "")


class Presets(unittest.TestCase):
    def test_every_preset_key_exists_and_is_valid(self):
        for v in gpu.VENDORS:
            for key, value in gpu.PRESETS[v].settings.items():
                if key == "gpu_backend" and key not in schema.BY_KEY:
                    continue   # Windows only
                self.assertIn(key, schema.BY_KEY, (v, key))
                self.assertEqual(schema.coerce(schema.BY_KEY[key], value), value, (v, key))
            for k in gpu.PRESETS[v].known:
                self.assertTrue(k.key in schema.BY_KEY or k.key == "gpu_backend", k.key)

    def test_nvidia_avoids_the_known_problems(self):
        p = gpu.PRESETS[gpu.NVIDIA].settings
        self.assertEqual(p["gpu_backend"], "vulkan")
        self.assertLessEqual(p["resolution_scale"], 2)
        self.assertEqual(p["present_effect"], "fsr")

    def test_nvidia_keeps_no_driver_pipelines(self):
        # #640: the driver reused none of them in any start; the file grew to 512 MB and slowed every start
        self.assertIs(gpu.PRESETS[gpu.NVIDIA].settings["vulkan_store_pipeline_cache"], False)
        w = gpu.warnings(gpu.NVIDIA, {"vulkan_store_pipeline_cache": True})
        self.assertEqual([(k, i) for k, _t, i in w], [("vulkan_store_pipeline_cache", 640)])
        self.assertNotIn("vulkan_store_pipeline_cache", gpu.settings_for(gpu.AMD))   # RADV reuses them
        self.assertEqual(gpu.warnings(gpu.AMD, {"vulkan_store_pipeline_cache": True}), [])

    def test_deck_is_light(self):
        p = gpu.PRESETS[gpu.DECK].settings
        self.assertEqual((p["resolution_scale"], p["frame_rate"], p["present_effect"]), (1, 30, "fsr"))

    def test_warnings(self):
        w = gpu.warnings(gpu.NVIDIA, {"resolution_scale": 3, "frame_rate": 60, "async_shader_compilation": True,
                                      "store_shaders": True})
        self.assertEqual([(k, i) for k, _t, i in w], [("resolution_scale", 47)])
        self.assertEqual(gpu.warnings(gpu.NVIDIA, {"resolution_scale": 2}), [])
        w = gpu.warnings(gpu.DECK, {"resolution_scale": 2, "frame_rate": 60})
        self.assertEqual({(k, i) for k, _t, i in w}, {("resolution_scale", 45), ("frame_rate", 45)})
        self.assertEqual(gpu.warnings(gpu.AMD, {"resolution_scale": 4, "frame_rate": 144}), [])
        self.assertEqual(gpu.warnings("", {"resolution_scale": 8}), [])

    def test_backend_warning_only_on_windows(self):
        with mock.patch.object(gpu, "IS_WINDOWS", True):
            w = gpu.warnings(gpu.NVIDIA, {"gpu_backend": "d3d12"})
            self.assertEqual([(k, i) for k, _t, i in w], [("gpu_backend", 46)])
            self.assertEqual(gpu.choice_note(gpu.NVIDIA, "gpu_backend", "d3d12"), "flickers on NVIDIA, #46")
            self.assertEqual(gpu.choice_note(gpu.NVIDIA, "gpu_backend", "vulkan"), "")
            self.assertEqual(gpu.choice_note(gpu.AMD, "gpu_backend", "d3d12"), "")
            self.assertIn("gpu_backend", gpu.settings_for(gpu.AMD))
        with mock.patch.object(gpu, "IS_WINDOWS", False):
            self.assertEqual(gpu.warnings(gpu.NVIDIA, {"gpu_backend": "d3d12"}), [])
            self.assertNotIn("gpu_backend", gpu.settings_for(gpu.NVIDIA))


class ConfigFlow(unittest.TestCase):
    def cfg(self, tmp, content=None):
        path = Path(tmp) / "launcher.json"
        if content is not None:
            path.write_text(json.dumps(content))
        return Config(path)

    def test_first_run_gets_the_preset(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            self.assertEqual(c.detect_gpu(gpu.Detected(gpu.DECK, "Steam Deck")), gpu.DECK)
            self.assertEqual((c.settings["frame_rate"], c.settings["resolution_scale"]), (30, 1))
            self.assertIn("frame_rate", render_toml(c) + "frame_rate")   # smoke: renders
            self.assertEqual(c.gpu_vendor(), gpu.DECK)

    def test_existing_settings_are_kept(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"settings": {"frame_rate": 144, "resolution_scale": 3}})
            self.assertTrue(c.loaded)
            c.detect_gpu(gpu.Detected(gpu.NVIDIA, "x"))
            self.assertEqual(c.gpu_vendor(), gpu.NVIDIA)
            self.assertEqual((c.settings["frame_rate"], c.settings["resolution_scale"]), (144, 3))

    def test_nvidia_launcher_json_of_0_2_6_drops_the_driver_pipelines_once(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"gpu": {"vendor": "nvidia", "checked": True},
                             "settings": {"vulkan_store_pipeline_cache": True}})
            self.assertFalse(c.settings["vulkan_store_pipeline_cache"])
            c.settings["vulkan_store_pipeline_cache"] = True     # the player turns it on again: it stays
            c.save()
            self.assertTrue(Config(c.path).settings["vulkan_store_pipeline_cache"])
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"gpu": {"vendor": "amd", "checked": True},
                             "settings": {"vulkan_store_pipeline_cache": True}})
            self.assertTrue(c.settings["vulkan_store_pipeline_cache"])
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected(gpu.NVIDIA, "x"))
            self.assertFalse(c.settings["vulkan_store_pipeline_cache"])
            self.assertIn("vulkan_store_pipeline_cache = false", render_toml(c))

    def test_detected_once(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected())          # unknown card
            self.assertTrue(c.gpu["checked"])
            with mock.patch.object(gpu, "detect", side_effect=AssertionError("asked again")):
                c.detect_gpu()

    def test_player_choice_wins(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected(gpu.AMD, "x"))
            c.settings["resolution_scale"] = 3
            self.assertEqual(c.set_gpu_vendor(gpu.NVIDIA, False), [])     # only the vendor changes
            self.assertEqual(c.settings["resolution_scale"], 3)
            self.assertEqual(c.gpu_vendor(), gpu.NVIDIA)
            self.assertIn("resolution_scale", c.set_gpu_vendor(gpu.NVIDIA, True))   # asked for the preset
            self.assertEqual(c.settings["resolution_scale"], 1)
            c.save()
            self.assertEqual(Config(c.path).gpu_vendor(), gpu.NVIDIA)     # survives a restart
            c.set_gpu_vendor("", False)
            self.assertEqual(c.gpu_vendor(), "")
            with mock.patch.object(gpu, "detect", side_effect=AssertionError("asked again")):
                c.detect_gpu()


class GameVendorArgument(unittest.TestCase):
    """The game is told which card to use (#89): --gpu_vendor from the Settings choice."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.cfg.online["mode"] = "offline"
        from forza_launcher import game
        self.game = game
        self.install = game.GameInstall(t / "rt", t / "rt" / game.EXE)

    def plan_args(self):
        return self.game.build_plan(self.cfg, self.install, write=False, log=lambda _t: None).argv

    def test_the_chosen_vendor_is_passed(self):
        for vendor, want in [(gpu.NVIDIA, "nvidia"), (gpu.AMD, "amd"), (gpu.INTEL, "intel")]:
            self.cfg.gpu["vendor"] = vendor
            self.assertIn(f"--gpu_vendor={want}", self.plan_args())

    def test_other_deck_and_unknown_pass_nothing(self):
        for vendor in ("", gpu.DECK, "voodoo"):
            self.cfg.gpu["vendor"] = vendor
            self.assertFalse([a for a in self.plan_args() if a.startswith("--gpu_vendor")], vendor)

    def test_the_player_own_choice_wins(self):
        self.cfg.gpu["vendor"] = gpu.NVIDIA
        self.cfg.advanced["extra_args"] = "--gpu_vendor=intel"
        self.assertEqual([a for a in self.plan_args() if a.startswith("--gpu_vendor")], ["--gpu_vendor=intel"])
        self.cfg.advanced["extra_args"] = "--vulkan_device=1"
        self.assertFalse([a for a in self.plan_args() if a.startswith("--gpu_vendor")])
        self.cfg.advanced["extra_args"] = ""
        self.cfg.advanced["extras_toml"] = 'gpu_vendor = "amd"'
        self.assertFalse([a for a in self.plan_args() if a.startswith("--gpu_vendor")])
        self.cfg.advanced["extras_toml"] = "d3d12_adapter = 1"
        self.assertFalse([a for a in self.plan_args() if a.startswith("--gpu_vendor")])
        self.cfg.advanced["extras_toml"] = ""
        self.cfg.settings["vulkan_device"] = 2
        self.assertFalse([a for a in self.plan_args() if a.startswith("--gpu_vendor")])
        self.cfg.settings["vulkan_device"] = -1
        self.assertTrue([a for a in self.plan_args() if a.startswith("--gpu_vendor")])

    def test_laptop_with_intel_and_nvidia_detects_nvidia_and_passes_it(self):
        names = ["Intel(R) UHD Graphics 630", "NVIDIA GeForce GTX 1050"]   # the laptop of #89
        self.cfg.gpu["vendor"] = gpu.vendor_from_adapters(names)[0]
        self.assertIn("--gpu_vendor=nvidia", self.plan_args())


if __name__ == "__main__":
    unittest.main()
