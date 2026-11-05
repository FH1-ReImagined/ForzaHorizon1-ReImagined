"""Intel integrated graphics on a Vulkan driver below 1.3 (#672): detection, the Direct3D 12 default of a new
launcher.json on Windows, the warning on Setup and Play and its "Use Direct3D 12". Run:
python3 -m unittest launcher/test_intel_vulkan.py"""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forza_launcher import build_env, gpu, vulkan_probe  # noqa: E402
from forza_launcher.config import Config  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

# What the probe reported, or would, on the PCs of #669, #640 and #268
HD520 = {"name": "Intel(R) HD Graphics 520", "vendor_id": 0x8086, "device_id": 0x1916, "type": 1,
         "api": "1.2.203", "driver": 0x19453A}
UHD_2020 = {"name": "Intel(R) UHD Graphics", "vendor_id": 0x8086, "device_id": 0x9BC4, "type": 1,
            "api": "1.2.131", "driver": 0}
GTX1650TI = {"name": "NVIDIA GeForce GTX 1650 Ti", "vendor_id": 0x10DE, "device_id": 0x1F95, "type": 2,
             "api": "1.3.278", "driver": 0}
VEGA_12 = {"name": "AMD Radeon(TM) Vega 8 Graphics", "vendor_id": 0x1002, "device_id": 0x15D8, "type": 1,
           "api": "1.2.170", "driver": 0}
IRIS_13 = {"name": "Intel(R) Iris(R) Xe Graphics", "vendor_id": 0x8086, "device_id": 0x9A49, "type": 1,
           "api": "1.3.289", "driver": 0}
ARC_12 = {"name": "Intel(R) Arc(TM) A770 Graphics", "vendor_id": 0x8086, "device_id": 0x56A0, "type": 2,
          "api": "1.2.200", "driver": 0}


class Detection(unittest.TestCase):
    def test_api_text_unpacks_the_version(self):
        self.assertEqual(vulkan_probe.api_text((1 << 22) | (2 << 12) | 203), "1.2.203")

    def test_parse(self):
        text = json.dumps({"devices": [HD520, {"name": "broken"}, "x"]})
        self.assertEqual(gpu.parse_vulkan_devices(text), [HD520])
        for bad in ("", "{", json.dumps({"error": "OSError: no loader"}), json.dumps({"devices": []}), "[]"):
            self.assertIsNone(gpu.parse_vulkan_devices(bad), bad)
        self.assertEqual(gpu.api_tuple("1.2.203"), (1, 2, 203))
        self.assertEqual(gpu.api_tuple("1.3"), (1, 3, 0))
        self.assertIsNone(gpu.api_tuple("unknown"))

    def test_probe_runs_in_its_own_process_and_is_asked_once(self):
        def fake_run(args, timeout=12):
            Path(args[-1]).write_text(json.dumps({"devices": [HD520]}))
            calls.append(args)
            return ""
        calls = []
        with mock.patch.object(gpu, "_vk_devices", []), mock.patch.object(gpu, "_run", side_effect=fake_run), \
                mock.patch.dict(os.environ, {"FORZA_VULKAN_DEVICES": ""}):
            self.assertEqual(gpu.vulkan_devices(), [HD520])
            self.assertEqual(gpu.vulkan_devices(), [HD520])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][-2].endswith("vulkan_probe.py") or calls[0][1] == "--vulkan-devices")

    def test_probe_failure_is_unknown(self):
        with mock.patch.object(gpu, "_vk_devices", []), mock.patch.object(gpu, "_run", side_effect=OSError), \
                mock.patch.dict(os.environ, {"FORZA_VULKAN_DEVICES": ""}):
            self.assertIsNone(gpu.vulkan_devices())

    def test_the_probe_script_reports_an_error_without_a_driver(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(vulkan_probe, "_loader", side_effect=OSError("none")):
            out = Path(t) / "devices.json"
            self.assertEqual(vulkan_probe.main([str(out)]), 0)
            self.assertIsNone(gpu.parse_vulkan_devices(out.read_text()))

    def test_frozen_launcher_runs_itself(self):
        with mock.patch("forza_launcher.paths.FROZEN", True):
            self.assertEqual(gpu.vulkan_probe_command("f.json")[1:], ["--vulkan-devices", "f.json"])
        from forza_launcher import cli
        with tempfile.TemporaryDirectory() as t, mock.patch.object(vulkan_probe, "devices", return_value=[HD520]):
            out = Path(t) / "d.json"
            self.assertEqual(cli.main(["--vulkan-devices", str(out)]), 0)
            self.assertEqual(gpu.parse_vulkan_devices(out.read_text()), [HD520])


class Rule(unittest.TestCase):
    def test_intel_igpu_below_1_3(self):
        self.assertIs(gpu.old_intel_vulkan([HD520], gpu.INTEL), HD520)          # #669
        self.assertIs(gpu.old_intel_vulkan([HD520], ""), HD520)                 # "Other": still the only GPU

    def test_laptop_uses_its_geforce(self):                                    # #640's laptop
        self.assertIsNone(gpu.old_intel_vulkan([UHD_2020, GTX1650TI], gpu.NVIDIA))
        self.assertIsNone(gpu.old_intel_vulkan([UHD_2020, GTX1650TI], ""))      # the game prefers the discrete one
        self.assertIs(gpu.old_intel_vulkan([GTX1650TI, UHD_2020], gpu.INTEL), UHD_2020)
        self.assertIs(gpu.old_intel_vulkan([GTX1650TI, UHD_2020], gpu.NVIDIA, index=1), UHD_2020)

    def test_never_amd_never_new_or_discrete_intel_never_unknown(self):
        self.assertIsNone(gpu.old_intel_vulkan([VEGA_12], gpu.AMD))             # #268
        self.assertIsNone(gpu.old_intel_vulkan([VEGA_12], ""))
        self.assertIsNone(gpu.old_intel_vulkan([IRIS_13], gpu.INTEL))
        self.assertIsNone(gpu.old_intel_vulkan([ARC_12], gpu.INTEL))
        self.assertIsNone(gpu.old_intel_vulkan([{**HD520, "api": ""}], gpu.INTEL))
        self.assertIsNone(gpu.old_intel_vulkan(None, gpu.INTEL))
        self.assertIsNone(gpu.old_intel_vulkan([], gpu.INTEL))


class ConfigFlow(unittest.TestCase):
    def cfg(self, tmp, content=None, vendor=gpu.INTEL):
        path = Path(tmp) / "launcher.json"
        if content is not None:
            path.write_text(json.dumps(content))
        c = Config(path)
        c.detect_gpu(gpu.Detected(vendor, "x"))
        return c

    def test_new_config_on_windows_defaults_to_d3d12(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(gpu, "IS_WINDOWS", True):
            c = self.cfg(t)
            self.assertEqual(c.settings.get("gpu_backend", "vulkan"), "vulkan")       # the Intel preset
            self.assertTrue(c.detect_vulkan([HD520]))
            self.assertEqual(c.settings.get("gpu_backend", "vulkan"), "d3d12")
            self.assertEqual(c.intel_vulkan_warning(), "")              # already on Direct3D 12
            c.save()
            self.assertEqual(Config(c.path).gpu["vulkan"], [HD520])    # kept in launcher.json

    def test_new_config_on_linux_only_warns(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(gpu, "IS_WINDOWS", False):
            c = self.cfg(t)
            c.detect_vulkan([HD520])
            self.assertEqual(c.settings.get("gpu_backend", "vulkan"), "vulkan")
            text = c.intel_vulkan_warning()
            self.assertIn("Mesa", text)
            self.assertNotIn("Direct3D", text)
            self.assertEqual(c.intel_vulkan_fix(), {})

    def test_existing_config_is_not_changed(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(gpu, "IS_WINDOWS", True):
            c = self.cfg(t, {"settings": {"gpu_backend": "vulkan"}})
            c.detect_vulkan([HD520])
            self.assertEqual(c.settings.get("gpu_backend", "vulkan"), "vulkan")
            text = c.intel_vulkan_warning()
            for words in ("HD Graphics 520", "Vulkan 1.2.203", "intel.com", "Direct3D 12"):
                self.assertIn(words, text)
            self.assertEqual(c.intel_vulkan_fix(), {"gpu_backend": "d3d12"})
            c.settings.update(c.intel_vulkan_fix())
            self.assertEqual((c.intel_vulkan_warning(), c.intel_vulkan_fix()), ("", {}))

    def test_regpu_needs_no_vulkan_driver_and_is_never_the_advice(self):
        # ReGPU is Direct3D 12 too: no Vulkan driver warning with it; the notice offers Direct3D 12 only.
        with tempfile.TemporaryDirectory() as t, mock.patch.object(gpu, "IS_WINDOWS", True):
            c = self.cfg(t, {"settings": {"gpu_backend": "vulkan"}})
            c.detect_vulkan([HD520])
            self.assertNotIn("ReGPU", c.intel_vulkan_warning())
            self.assertEqual(c.intel_vulkan_fix(), {"gpu_backend": "d3d12"})
            c.settings["gpu_backend"] = "regpu"
            self.assertEqual((c.intel_vulkan_warning(), c.intel_vulkan_fix()), ("", {}))
            # untested on Intel: said next to the choice and as the setting's warning
            self.assertEqual(gpu.choice_note(gpu.INTEL, "gpu_backend", "regpu"), "untested on Intel")
            self.assertIn("gpu_backend", [k for k, _, _ in gpu.warnings(gpu.INTEL, c.settings)])
            nvidia = [k for k, _, _ in gpu.warnings(gpu.NVIDIA, c.settings)]
            self.assertNotIn("gpu_backend", nvidia)   # not the SDK's Direct3D 12 (#46)

    def test_no_default_or_warning_elsewhere(self):
        cases = [([UHD_2020, GTX1650TI], gpu.NVIDIA), ([VEGA_12], gpu.AMD), ([IRIS_13], gpu.INTEL)]
        for devices, vendor in cases:
            with tempfile.TemporaryDirectory() as t, mock.patch.object(gpu, "IS_WINDOWS", True):
                c = self.cfg(t, vendor=vendor)
                c.detect_vulkan(devices)
                self.assertEqual(c.settings.get("gpu_backend", "vulkan"), "vulkan", devices)
                self.assertEqual(c.intel_vulkan_warning(), "", devices)

    def test_unknown_driver_keeps_what_was_known(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(gpu, "IS_WINDOWS", True), \
                mock.patch.object(gpu, "vulkan_devices", return_value=None):
            c = self.cfg(t)
            self.assertFalse(c.detect_vulkan())
            self.assertEqual((c.settings.get("gpu_backend", "vulkan"), c.intel_vulkan_warning()), ("vulkan", ""))
            c.gpu["vulkan"] = [HD520]
            self.assertFalse(c.detect_vulkan())
            self.assertTrue(c.intel_vulkan_warning())

    def test_a_driver_update_clears_the_warning(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(gpu, "IS_WINDOWS", True):
            c = self.cfg(t, {"settings": {}})
            c.detect_vulkan([HD520])
            self.assertTrue(c.intel_vulkan_warning())
            self.assertTrue(c.detect_vulkan([{**HD520, "api": "1.3.215"}]))
            self.assertEqual(c.intel_vulkan_warning(), "")


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Pages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, windows):
        from forza_launcher.ui.window import MainWindow
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        build_env.forget_tools()
        self.addCleanup(build_env.forget_tools)
        for p in (mock.patch("forza_launcher.paths.config_dir", return_value=root),
                  mock.patch.object(build_env, "_detect_tools", return_value=build_env.Tools()),
                  mock.patch("forza_launcher.game.find_game", return_value=(None, [])),
                  mock.patch.object(gpu, "IS_WINDOWS", windows),
                  mock.patch.object(gpu, "vulkan_devices", return_value=[HD520])):
            p.start()
            self.addCleanup(p.stop)
        (root / "launcher.json").write_text(json.dumps({"settings": {"gpu_backend": "vulkan"}}))
        cfg = Config(root / "launcher.json")
        cfg.gpu.update(vendor=gpu.INTEL, checked=True)
        cfg.d["check_updates"] = False
        cfg.advanced["data_root"] = str(root / "data")
        win = MainWindow(cfg)
        self.addCleanup(win.close)
        deadline = time.monotonic() + 10
        while win._vulkan_probe is not None and not cfg.gpu.get("vulkan") and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        win.play.refresh()
        win.setup.refresh()
        return win, cfg

    def test_windows_warning_and_use_direct3d_12(self):
        win, cfg = self.window(True)
        for notice in (win.play.intel_driver, win.setup.intel_driver):
            self.assertFalse(notice.isHidden())
            self.assertIn("intel.com", notice.text.text())
            self.assertFalse(notice.use_d3d12.isHidden())
        self.assertEqual(cfg.settings.get("gpu_backend", "vulkan"), "vulkan")      # nothing changed by itself
        win.setup.intel_driver.use_d3d12.click()
        self.assertEqual(cfg.settings.get("gpu_backend", "vulkan"), "d3d12")
        self.assertEqual(json.loads(cfg.path.read_text())["settings"]["gpu_backend"], "d3d12")   # saved
        self.assertTrue(win.play.intel_driver.isHidden())
        self.assertTrue(win.setup.intel_driver.isHidden())

    def test_regpu_chosen_hides_the_notice(self):
        win, cfg = self.window(True)
        cfg.settings["gpu_backend"] = "regpu"
        win.play.refresh()
        win.setup.refresh()
        self.assertTrue(win.play.intel_driver.isHidden())
        self.assertTrue(win.setup.intel_driver.isHidden())

    def test_linux_warning_has_no_button(self):
        win, cfg = self.window(False)
        notice = win.play.intel_driver
        self.assertFalse(notice.isHidden())
        self.assertIn("Mesa", notice.text.text())
        self.assertTrue(notice.use_d3d12.isHidden())


if __name__ == "__main__":
    unittest.main()
