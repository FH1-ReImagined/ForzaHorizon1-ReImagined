"""PC specs, tiers, recommended settings and warnings (#206). Run: python3 -m unittest launcher/test_specs.py"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import gpu, specs  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
from forza_launcher.specs import HIGH, LOW, MID, UNKNOWN, Specs  # noqa: E402

GIB = 1024 ** 3
POWERSHELL = json.dumps({
    "gpus": [{"Name": "NVIDIA GeForce GTX 1660 SUPER", "AdapterCompatibility": "NVIDIA",
              "PNPDeviceID": "PCI\\VEN_10DE&DEV_21C4", "AdapterRAM": 4293918720},
             {"Name": "AMD Radeon(TM) Graphics", "AdapterCompatibility": "Advanced Micro Devices, Inc.",
              "PNPDeviceID": "PCI\\VEN_1002&DEV_164E", "AdapterRAM": 536870912}],
    "board": "B650M", "cpus": [{"Name": "AMD Ryzen 5 7600X 6-Core Processor", "NumberOfCores": 6,
                                "NumberOfLogicalProcessors": 12}],
    "ram": 34244579328,
    "vram": [{"Desc": "NVIDIA GeForce GTX 1660 SUPER", "Mem": 6442450944},
             {"Desc": "AMD Radeon(TM) Graphics", "Mem": [0, 0, 0, 32, 0, 0, 0, 0]}]})
RYZEN = "processor\t: {n}\nmodel name\t: AMD Ryzen 5 7600X 6-Core Processor\nphysical id\t: 0\ncore id\t\t: {c}\n" \
        "flags\t\t: fpu sse4_2 avx avx2 fma\n\n"
IVY = "processor\t: {n}\nmodel name\t: Intel(R) Xeon(R) CPU E5-2670 v2 @ 2.50GHz\nphysical id\t: 0\ncore id\t\t: {c}\n" \
      "flags\t\t: fpu sse4_2 avx f16c\n\n"
LSPCI = ('03:00.0 "VGA compatible controller" "NVIDIA Corporation" "TU116 [GeForce GTX 1660 SUPER]" -ra1 "ASUS" "x"\n'
         '00:02.0 "Audio device" "Intel" "x"\n')


def cpuinfo(template, cores, threads):
    return "".join(template.format(n=i, c=i % cores) for i in range(threads))


def S(cores=8, avx2=True, ram=16, vram=8, integrated=False, gpu_name="NVIDIA GeForce RTX 3080", cpu="Ryzen"):
    return Specs(cpu_name=cpu, cpu_cores=cores, cpu_threads=cores * 2, avx2=avx2, ram_gb=ram,
                 gpu_name=gpu_name, vram_gb=vram, integrated=integrated)


GTX1660 = S(6, True, 32, 6, False, "NVIDIA GeForce GTX 1660 SUPER", "AMD Ryzen 5 7600X 6-Core Processor")
GTX1650TI = S(4, True, 7.8, 4, False, "NVIDIA GeForce GTX 1650 Ti", "Intel(R) Core(TM) i5-10300H CPU @ 2.50GHz")
IRIS = S(4, True, 16, 0, True, "Intel(R) Iris(R) Xe Graphics", "11th Gen Intel(R) Core(TM) i5-1135G7")
DECK = S(4, True, 16, 1, True, "Steam Deck", "AMD Custom APU 0405")


class Parsers(unittest.TestCase):
    def test_powershell_vram_comes_from_the_registry_not_adapterram(self):
        s = specs.parse_powershell(POWERSHELL)
        self.assertEqual((s.gpu_name, s.vram_gb, s.integrated), ("NVIDIA GeForce GTX 1660 SUPER", 6.0, False))
        self.assertEqual((s.cpu_name, s.cpu_cores, s.cpu_threads), ("AMD Ryzen 5 7600X 6-Core Processor", 6, 12))
        self.assertAlmostEqual(s.ram_gb, 31.9, 1)

    def test_powershell_falls_back_on_adapterram_and_survives_junk(self):
        data = json.loads(POWERSHELL)
        data["vram"] = []
        self.assertAlmostEqual(specs.parse_powershell(json.dumps(data)).vram_gb, 4.0, 1)
        for junk in ("", "nope", "[]", "{}", '{"gpus": 5, "cpus": "x"}'):
            self.assertIsInstance(specs.parse_powershell(junk), Specs)

    def test_powershell_picks_the_discrete_card_and_the_deck(self):
        data = json.loads(POWERSHELL)
        data["gpus"].reverse()
        self.assertFalse(specs.parse_powershell(json.dumps(data)).integrated)
        data["board"] = "Jupiter"
        self.assertEqual(specs.parse_powershell(json.dumps(data)).gpu_name, "Steam Deck")

    def test_the_query_still_feeds_the_gpu_parser(self):
        names, board = gpu.parse_powershell(POWERSHELL)
        self.assertEqual((len(names), board), (2, "B650M"))
        self.assertIn("qwMemorySize", gpu.PS_QUERY)

    def test_cpuinfo(self):
        name, cores, threads, avx2 = specs.parse_cpuinfo(cpuinfo(RYZEN, 6, 12))
        self.assertEqual((name, cores, threads, avx2), ("AMD Ryzen 5 7600X 6-Core Processor", 6, 12, True))
        name, cores, threads, avx2 = specs.parse_cpuinfo(cpuinfo(IVY, 10, 20))
        self.assertEqual((cores, threads, avx2), (10, 20, False))
        self.assertIn("Xeon", name)
        self.assertEqual(specs.parse_cpuinfo(""), ("", 0, 0, None))

    def test_meminfo(self):
        self.assertAlmostEqual(specs.parse_meminfo("MemTotal:       32616212 kB\nMemFree: 1 kB\n"), 31.1, 1)
        self.assertEqual(specs.parse_meminfo("nothing"), 0.0)

    def test_nvidia_smi_and_lspci(self):
        self.assertEqual(specs.parse_nvidia_smi("NVIDIA GeForce GTX 1660 SUPER, 6144\n"),
                         [("NVIDIA GeForce GTX 1660 SUPER", 6.0)])
        self.assertEqual(specs.parse_nvidia_smi("garbage"), [])
        self.assertEqual(specs.parse_lspci(LSPCI), {"03:00.0": "GeForce GTX 1660 SUPER"})

    def test_amdgpu_sysfs(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t, "class/drm/card0/device")
            d.mkdir(parents=True)
            (d / "vendor").write_text("0x1002\n")
            (d / "uevent").write_text("PCI_SLOT_NAME=0000:03:00.0\n")
            (d / "mem_info_vram_total").write_text(str(16 * GIB) + "\n")
            lspci = '03:00.0 "VGA compatible controller" "AMD" "Navi 44 [Radeon RX 9060 XT]" -rc0\n'
            card = specs.linux_cards(t, lspci)[0]
            self.assertEqual((card.name, card.vram_gb, card.integrated), ("Radeon RX 9060 XT", 16.0, False))
            (d / "mem_info_vram_total").write_text(str(512 * 1024 ** 2) + "\n")
            self.assertTrue(specs.linux_cards(t, "")[0].integrated)    # an APU's carve-out

    def test_nvidia_smi_gives_the_name_and_memory_on_linux(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t, "class/drm/card0/device")
            d.mkdir(parents=True)
            (d / "vendor").write_text("0x10de\n")
            card = specs.linux_cards(t, "", "NVIDIA GeForce GTX 1660 SUPER, 6144\n")[0]
            self.assertEqual((card.name, card.vram_gb, card.integrated), ("NVIDIA GeForce GTX 1660 SUPER", 6.0, False))

    def test_detect_linux_from_fixtures(self):
        with tempfile.TemporaryDirectory() as t:
            proc = Path(t, "proc")
            proc.mkdir()
            (proc / "cpuinfo").write_text(cpuinfo(IVY, 10, 20))
            (proc / "meminfo").write_text("MemTotal: 16777216 kB\n")
            s = specs.detect_linux(Path(t, "sys").as_posix(), proc.as_posix(), "", "")
            self.assertEqual((s.cpu_cores, s.avx2, s.ram_gb, s.gpu_name), (10, False, 16.0, ""))

    def test_override_and_never_raises(self):
        s = specs.apply_override(Specs(), "cpu_cores=4,avx2=0,ram=8,vram=4,integrated=0,bogus=1,ram=x")
        self.assertEqual((s.cpu_cores, s.avx2, s.ram_gb, s.vram_gb, s.integrated), (4, False, 8.0, 4.0, False))
        with mock.patch.object(specs, "detect_linux", side_effect=RuntimeError), \
                mock.patch.object(specs, "IS_WINDOWS", False):
            self.assertFalse(specs.detect().known())

    def test_cache_round_trip(self):
        from dataclasses import asdict
        self.assertEqual(specs.specs_from_dict(asdict(GTX1660)), GTX1660)
        self.assertFalse(specs.specs_from_dict({"cpu_cores": "x", "avx2": 3}).known())


class Tiers(unittest.TestCase):
    def tiers(self, s):
        return specs.gpu_tier(s), specs.cpu_tier(s)

    def test_setups(self):
        self.assertEqual(self.tiers(GTX1660), (MID, HIGH))
        self.assertEqual(self.tiers(S(4, False, 16, 0, None, "", "Intel Xeon E5-2670 v2")), (UNKNOWN, LOW))
        self.assertEqual(self.tiers(DECK), (LOW, LOW))
        self.assertEqual(self.tiers(IRIS)[0], LOW)
        self.assertEqual(self.tiers(S(8)), (HIGH, HIGH))
        self.assertEqual(self.tiers(Specs()), (UNKNOWN, UNKNOWN))

    def test_edges(self):
        self.assertEqual(specs.cpu_tier(S(3)), LOW)
        self.assertEqual(specs.cpu_tier(S(8, ram=4)), LOW)
        self.assertEqual(specs.cpu_tier(S(4)), MID)
        self.assertEqual(specs.gpu_tier(S(vram=3, integrated=False)), LOW)
        self.assertEqual(specs.gpu_tier(S(vram=11, gpu_name="NVIDIA GeForce GTX 1080 Ti")), MID)
        self.assertEqual(specs.gpu_tier(S(vram=12, gpu_name="AMD Radeon RX 6700 XT")), HIGH)
        self.assertEqual(specs.gpu_tier(S(vram=0, integrated=None, gpu_name="NVIDIA GeForce RTX 3060")), MID)
        self.assertEqual(specs.gpu_tier(S(vram=0, integrated=None, gpu_name="Matrox G200")), UNKNOWN)

    def test_integrated_names(self):
        self.assertTrue(specs.is_integrated(gpu.AMD, "AMD Radeon 780M Graphics"))
        self.assertFalse(specs.is_integrated(gpu.AMD, "AMD Radeon RX 480", 8))
        self.assertFalse(specs.is_integrated(gpu.AMD, "Radeon RX Vega 64", 8))
        self.assertTrue(specs.is_integrated(gpu.INTEL, "Intel(R) UHD Graphics 770"))
        self.assertFalse(specs.is_integrated(gpu.INTEL, "Intel(R) Arc(TM) A770 Graphics", 16))
        self.assertFalse(specs.is_integrated(gpu.NVIDIA, "GeForce RTX 3080"))

    def test_description(self):
        self.assertEqual(specs.describe(GTX1660), "This PC: Ryzen 5 7600X (6 cores), GeForce GTX 1660 SUPER (6 GB), "
                                                  "32 GB RAM: mid-range graphics, fast CPU")
        self.assertIn("not detected", specs.describe(Specs()))


class Recommendations(unittest.TestCase):
    def rec(self, s, vendor=gpu.NVIDIA):
        base = gpu.settings_for(vendor)
        return {**base, **specs.recommended(s, base, vendor)}

    def test_per_tier(self):
        self.assertEqual(self.rec(S(8)).get("resolution_scale"), 2)
        r = self.rec(GTX1660)
        self.assertEqual((r["frame_rate"], r["resolution_scale"]), (60, 1))
        self.assertEqual(self.rec(DECK, gpu.DECK)["frame_rate"], 30)
        self.assertEqual(self.rec(S(2, ram=4))["frame_rate"], 30)
        self.assertEqual(self.rec(Specs()), gpu.settings_for(gpu.NVIDIA))     # unknown: no change

    def test_entry_cards_get_the_540p_scene(self):
        self.assertEqual(self.rec(GTX1650TI)["scene_resolution"], "540")          # #640
        self.assertEqual(self.rec(IRIS, gpu.INTEL)["scene_resolution"], "540")
        self.assertNotIn("scene_resolution", self.rec(GTX1660))                   # 6 GB
        self.assertNotIn("scene_resolution", self.rec(S(4, vram=4, gpu_name="NVIDIA GeForce RTX 3050 Laptop GPU")))
        self.assertEqual(self.rec(DECK, gpu.DECK)["scene_resolution"], "720")    # its CPU is the limit
        r = specs.recommended(GTX1650TI, {"present_effect": "fsr3", "frame_rate": 60, "resolution_scale": 1}, "")
        self.assertNotIn("scene_resolution", r)                                    # FSR 3 keeps 720p (a mid card)

    def test_vendor_known_problems_win(self):
        self.assertEqual(self.rec(S(8), gpu.INTEL)["resolution_scale"], 1)     # Intel warns at 2
        self.assertEqual(self.rec(S(8), gpu.INTEL)["frame_rate"], 30)          # never above the preset's
        self.assertEqual(self.rec(S(8), gpu.DECK)["frame_rate"], 30)

    def test_no_fsr3_or_smaa_default_on_a_low_card(self):
        r = specs.recommended(IRIS, {"present_effect": "fsr3", "frame_rate": 60, "resolution_scale": 1}, "")
        self.assertEqual(r["present_effect"], "fsr")


class Warnings(unittest.TestCase):
    def texts(self, s, **settings):
        return {w.key: w.text for w in specs.warnings(s, {"frame_rate": 60, "resolution_scale": 1, **settings})}

    def test_issue_204(self):
        t = self.texts(GTX1660, resolution_scale=2)
        self.assertEqual(t["resolution_scale"], "Render scale 2 renders 2560x1440. GeForce GTX 1660 SUPER (6 GB) "
                                                "usually can't hold 60 fps there; try 1 with FSR 1.")
        self.assertIn("Above 60 fps the graphics card is usually the limit on GeForce GTX 1660 SUPER (6 GB)",
                      self.texts(GTX1660, frame_rate=76)["frame_rate"])
        self.assertEqual(self.texts(GTX1660), {})

    def test_cpu_frame_rate(self):
        t = self.texts(S(4, cpu="Core i5 7400"), frame_rate=120)["frame_rate"]
        self.assertIn("120 fps needs a fast CPU", t)
        self.assertIn("(4 cores)", t)
        self.assertNotIn("frame_rate", self.texts(S(8), frame_rate=144))

    def test_heavy_options_on_a_low_card(self):
        self.assertIn("present_effect", self.texts(IRIS, present_effect="fsr3"))
        self.assertIn("forza_antialiasing", self.texts(IRIS, forza_antialiasing="smaa"))
        self.assertNotIn("forza_antialiasing", self.texts(S(8), forza_antialiasing="smaa"))
        self.assertNotIn("resolution_scale", self.texts(IRIS, resolution_scale=2, present_effect="fsr3"))

    def test_no_540p_warning(self):
        # #640: an existing launcher.json gets the 540p scene once (Flow below); a player who sets 720p again
        # gets no banner about it
        self.assertEqual(self.texts(GTX1650TI), {})
        self.assertNotIn("scene_resolution", self.texts(IRIS))

    def test_old_cpu_and_low_ram(self):
        self.assertTrue([w for w in specs.warnings(S(4, avx2=False), {}) if "without AVX2" in w.text])
        self.assertTrue([w for w in specs.warnings(S(8, ram=4), {}) if "RAM" in w.text])

    def test_high_card_warns_only_far_up(self):
        self.assertEqual(self.texts(S(8), resolution_scale=2), {})
        self.assertIn("3840x2160", self.texts(S(8), resolution_scale=3)["resolution_scale"])


class Flow(unittest.TestCase):
    def cfg(self, tmp, content=None):
        path = Path(tmp) / "launcher.json"
        if content is not None:
            path.write_text(json.dumps(content))
        return Config(path)

    def test_new_launcher_gets_the_recommended_settings(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected(gpu.NVIDIA, "x"))
            c.detect_specs(S(2, ram=4))
            self.assertEqual(c.settings["frame_rate"], 30)
            c.save()
            self.assertEqual(Config(c.path).pc_specs().cpu_cores, 2)       # cached
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected(gpu.NVIDIA, "x"))
            c.detect_specs(S(8))
            self.assertEqual((c.settings["frame_rate"], c.settings["resolution_scale"]), (60, 2))
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_gpu(gpu.Detected(gpu.DECK, "Steam Deck"))
            c.detect_specs(DECK)
            self.assertEqual((c.settings["frame_rate"], c.settings["resolution_scale"]), (30, 1))

    def test_existing_launcher_is_unchanged(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"settings": {"frame_rate": 120, "resolution_scale": 2}})
            c.detect_specs(GTX1660)
            self.assertEqual((c.settings["frame_rate"], c.settings["resolution_scale"]), (120, 2))
            self.assertTrue(c.specs_banner_due())

    def test_cached_specs_are_not_detected_again_until_the_card_changes(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            c.detect_specs(S(8))
            with mock.patch.object(specs, "detect", side_effect=AssertionError("asked again")):
                self.assertFalse(c.detect_specs())
            with mock.patch.object(specs, "fingerprint", return_value="another card"), \
                    mock.patch.object(specs, "detect", return_value=GTX1660):
                self.assertTrue(c.detect_specs())
            self.assertEqual(c.pc_specs().gpu_name, GTX1660.gpu_name)
            with mock.patch.object(specs, "detect", return_value=S(8)):
                self.assertTrue(c.detect_specs(force=True))

    def test_banner_apply_dismiss_and_reset_on_new_hardware(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"settings": {"frame_rate": 120, "resolution_scale": 2, "present_effect": "fsr",
                                          "anisotropic_override": 5}})
            c.detect_specs(GTX1660)
            self.assertTrue(c.specs_banner_due())
            c.dismiss_specs_banner()
            self.assertFalse(c.specs_banner_due())
            c.save()
            self.assertFalse(Config(c.path).specs_banner_due())               # remembered
            c.detect_specs(S(4, ram=16, vram=6, gpu_name="NVIDIA GeForce GTX 1060"), force=True)   # new hardware
            self.assertTrue(c.specs_banner_due())
            changed = c.use_recommended()
            self.assertEqual(sorted(changed), ["frame_rate", "resolution_scale"])
            self.assertEqual((c.settings["frame_rate"], c.settings["resolution_scale"]), (60, 1))
            self.assertEqual(c.settings["anisotropic_override"], 5)           # only the keys that warn
            self.assertFalse(c.specs_banner_due())

    def test_entry_card_gets_the_540p_scene_once(self):
        old = {"gpu": {"vendor": "nvidia", "checked": True},
               "specs": {"checked": True, "fingerprint": "x", "values": GTX1650TI.__dict__},
               "settings": {"scene_resolution": "720", "frame_rate": 60, "present_effect": "fsr"},
               "migrated": {"discord_application_id": True}}           # a launcher.json of 0.2.6
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, old)
            self.assertEqual(c.settings["scene_resolution"], "540")
            self.assertEqual(len(c.notices), 1)
            self.assertIn("Settings > Graphics > 3D scene resolution", c.notices[0])
            self.assertIn("GeForce GTX 1650 Ti", c.notices[0])
            c.save()
            c = Config(c.path)
            self.assertEqual((c.settings["scene_resolution"], c.notices), ("540", []))
            c.settings["scene_resolution"] = "720"                     # the player goes back: it stays
            c.save()
            c = Config(c.path)
            self.assertEqual((c.settings["scene_resolution"], c.notices), ("720", []))
            self.assertFalse(c.specs_banner_due())
        for key, value in (("present_effect", "fsr3"), ("fh1_upscaler", "quality"),
                           ("forza_antialiasing", "smaa"), ("forza_ultrawide", True)):
            with tempfile.TemporaryDirectory() as t:                    # 540p is not drawn with these
                c = self.cfg(t, {**old, "settings": {**old["settings"], key: value}})
                if c.settings.get(key) != value:
                    continue   # not a setting on this platform (DLSS: Windows only)
                self.assertEqual((c.settings["scene_resolution"], c.notices), ("720", []), key)
                self.assertTrue(c.d["migrated"]["scene_540"])
        for card in (GTX1660, DECK):
            with tempfile.TemporaryDirectory() as t:
                c = self.cfg(t, {**old, "specs": {"checked": True, "fingerprint": "x", "values": card.__dict__}})
                self.assertEqual((c.settings["scene_resolution"], c.notices), ("720", []), card.gpu_name)

    def test_540p_migration_waits_for_the_specs(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"settings": {"scene_resolution": "720"}})   # from before the specs were detected
            self.assertEqual(c.settings["scene_resolution"], "720")
            self.assertFalse(c.d["migrated"]["scene_540"])
            c.detect_specs(GTX1650TI)
            self.assertEqual(c.settings["scene_resolution"], "540")
            self.assertEqual(len(c.notices), 1)

    def test_new_launcher_needs_no_540p_migration(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t)
            self.assertTrue(c.d["migrated"]["scene_540"])
            c.detect_gpu(gpu.Detected(gpu.NVIDIA, "x"))
            c.detect_specs(GTX1650TI)
            self.assertEqual((c.settings["scene_resolution"], c.notices), ("540", []))   # the recommendation

    def test_no_banner_without_warnings_or_specs(self):
        with tempfile.TemporaryDirectory() as t:
            c = self.cfg(t, {"settings": {"frame_rate": 60}})
            c.detect_specs(Specs())
            self.assertFalse(c.specs_banner_due())
            c.detect_specs(S(8), force=True)
            self.assertFalse(c.specs_banner_due())


class SettingsPageTests(unittest.TestCase):
    def test_warnings_banner_and_buttons(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            self.skipTest("PySide6 is not installed")
        from forza_launcher.ui.settings_page import SettingsPage
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "launcher.json").write_text(json.dumps({"settings": {"frame_rate": 76, "resolution_scale": 2}}))
            c = Config(Path(t) / "launcher.json")
            c.advanced["data_root"] = t
            c.gpu.update(checked=True, vendor="nvidia")
            c.detect_specs(GTX1660)
            window = mock.Mock()
            window.preview_text.return_value = "game.exe"
            page = SettingsPage(c, window)
            page.show()
            app.processEvents()
            self.assertTrue(page.banner.isVisibleTo(page))
            self.assertIn("2560x1440", page.rows["resolution_scale"].warning.text())
            self.assertIn("GTX 1660 SUPER (6 GB)", page.specs_line.text())
            page._use_recommended()
            page._save_timer.stop()
            self.assertEqual((c.settings["frame_rate"], c.settings["resolution_scale"]), (60, 1))
            self.assertFalse(page.banner.isVisibleTo(page))
            self.assertEqual(page.rows["resolution_scale"].warning.text(), "")
            page.close()


if __name__ == "__main__":
    unittest.main()
