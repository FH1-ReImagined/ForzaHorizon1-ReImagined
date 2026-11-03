"""The "VR headset (experimental)" setting (#333): the cvar, the launch arguments and the Flatpak runtime path."""
from pathlib import Path
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, paths, schema, vr

XR_SOURCE = Path(__file__).resolve().parent.parent / "forza-recomp" / "src" / "platform" / "xr.cpp"


class VrSettingTest(unittest.TestCase):
    def config(self):
        return config.Config(Path(tempfile.mkdtemp()) / "launcher.toml")

    def test_settings(self):
        mode = schema.BY_KEY["vr_mode"]
        self.assertEqual((mode.group, mode.section, mode.kind, mode.default), ("VR", "VR (experimental)", "choice", "cockpit"))
        self.assertEqual([v for v, _ in mode.choices], ["cockpit", "screen"])
        self.assertIn("VR", schema.GROUPS)
        for key in ("vr_picture", "vr_seat_height_cm"):
            self.assertEqual(schema.BY_KEY[key].group, "VR")
        self.assertEqual([v for v, _ in schema.BY_KEY["vr_picture"].choices], ["game", "sharp", "sharpest"])
        for key in ("vr_mode", "vr_picture", "vr_seat_height_cm"):   # never written to forza.toml
            self.assertIn(key, config.LAUNCHER_ONLY)

    def test_the_game_knows_the_values(self):
        # The cvar and its allowed values in the game (src/platform/xr.cpp).
        text = XR_SOURCE.read_text()
        m = re.search(r'REXCVAR_DEFINE_STRING\(vr_mode, "(\w+)",.*?\.allowed\(\{([^}]*)\}\)', text, re.S)
        if m is None:
            self.skipTest("the game source is not in this checkout")
        allowed = re.findall(r'"(\w+)"', m.group(2))
        for value, _ in schema.BY_KEY["vr_mode"].choices:
            self.assertIn(value, allowed)
        seat = re.search(r"REXCVAR_DEFINE_DOUBLE\(vr_seat_height_cm, (-?[\d.]+),", text)
        self.assertEqual(float(seat.group(1)), schema.BY_KEY["vr_seat_height_cm"].default)

    def test_play_stays_flat_and_the_mode_is_not_in_the_toml(self):
        cfg = self.config()
        cfg.settings["vr_mode"] = "screen"   # an older config: the chosen VR mode only
        self.assertNotIn("vr_mode", config.render_toml(cfg))
        env, args, notes = {}, ["--a"], []
        vr.configure(cfg, env, args, notes, in_flatpak=True)
        self.assertEqual((env, args, notes), ({}, ["--a"], []))

    def args_of(self, cfg, **kw):
        args, notes = ["--resolution_scale=1", "--frame_rate=30", "--x"], []
        with mock.patch.object(paths, "IS_WINDOWS", kw.pop("windows", False)):
            vr.configure(cfg, {}, args, notes, in_flatpak=False, environ={}, session=True)
        return args, notes

    def test_vr_session_args(self):
        cfg = self.config()
        cfg.settings["vr_mode"] = "cockpit"
        cfg.settings["frame_rate"] = 144
        args, notes = self.args_of(cfg)
        self.assertEqual(args, ["--x", "--frame_rate=72", "--scene_msaa=off", "--vr_mode=cockpit",
                                "--vr_stereo=true", "--vr_trace=false", "--vr_seat_height_cm=-4",
                                "--resolution_scale=2"])
        self.assertIn("Cockpit", notes[0])
        self.assertEqual(cfg.settings["frame_rate"], 144)   # the saved flat settings stay

    def test_screen_mode_and_old_off(self):
        cfg = self.config()
        cfg.settings["vr_mode"] = "screen"
        cfg.settings["vr_picture"] = "sharpest"   # cockpit only
        args, _ = self.args_of(cfg)
        self.assertIn("--vr_mode=screen", args)
        self.assertIn("--resolution_scale=2", args)
        self.assertFalse(any("draw_resolution" in a for a in args))
        cfg.settings["vr_mode"] = "off"
        self.assertEqual(vr.mode(cfg), "cockpit")

    def test_windows_forces_vulkan(self):
        args, _ = self.args_of(self.config(), windows=True)
        self.assertIn("--gpu_backend=vulkan", args)

    def test_extra_flags_win(self):
        cfg = self.config()
        cfg.advanced["extra_args"] = "--frame_rate=90"
        args, _ = self.args_of(cfg)
        self.assertNotIn("--frame_rate=72", args)
        self.assertNotIn("--frame_rate=30", args)

    def test_flatpak_names_the_hosts_runtime(self):
        cfg = self.config()
        home = Path(tempfile.mkdtemp())
        manifest = home / ".config" / "openxr" / "1" / "active_runtime.json"
        env = {}
        vr.configure(cfg, env, [], [], in_flatpak=True, home=home, environ={}, session=True)
        self.assertNotIn("XR_RUNTIME_JSON", env)  # none installed
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}")
        vr.configure(cfg, env, [], [], in_flatpak=True, home=home, environ={}, session=True)
        self.assertEqual(env["XR_RUNTIME_JSON"], str(manifest))
        env = {}
        vr.configure(cfg, env, [], [], in_flatpak=True, home=home, environ={"XR_RUNTIME_JSON": "/x.json"}, session=True)
        self.assertNotIn("XR_RUNTIME_JSON", env)
        vr.configure(cfg, env, [], [], in_flatpak=False, home=home, environ={}, session=True)
        self.assertNotIn("XR_RUNTIME_JSON", env)

    def test_picture_preset(self):
        cfg = self.config()
        cfg.settings["vr_picture"] = "sharp"
        args, _ = self.args_of(cfg)
        self.assertIn("--draw_resolution_scale_x=2", args)
        self.assertIn("--draw_resolution_scale_y=3", args)
        self.assertFalse(any(a.startswith("--resolution_scale") for a in args))
        cfg.settings["vr_picture"] = "sharpest"
        args, _ = self.args_of(cfg)
        self.assertIn("--draw_resolution_scale_x=3", args)
        # FSR 3 and DLSS keep their own scale.
        cfg.settings["present_effect"] = "fsr3"
        args, notes = self.args_of(cfg)
        self.assertFalse(any("resolution_scale" in a for a in args if a != "--resolution_scale=1"))
        self.assertTrue(any("FSR 3" in n for n in notes))

    def test_the_game_knows_the_scales(self):
        for x, y in vr.PICTURE_SCALES.values():
            self.assertTrue(1 <= x <= 7 and 1 <= y <= 7)

    def test_detect_linux(self):
        home = Path(tempfile.mkdtemp())
        self.assertIsNone(vr.detect(home, {}, windows=False))
        manifest = home / ".config" / "openxr" / "1" / "active_runtime.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text('{"runtime": {"name": "WiVRn", "library_path": "libopenxr_wivrn.so"}}')
        found = vr.detect(home, {"HOST_XDG_CONFIG_DIRS": "/nonexistent"}, windows=False)
        self.assertEqual((found.name, found.path), ("WiVRn", str(manifest)))
        manifest.write_text("not json")
        self.assertEqual(vr.detect(home, {}, windows=False).name, "OpenXR runtime")

    def test_detect_windows_registry(self):
        found = vr.detect(windows=True, registry=lambda: r"C:\Program Files\SteamVR\steamxr_win64.json")
        self.assertEqual(found.name, "SteamVR")
        self.assertEqual(vr.detect(windows=True, registry=lambda: r"C:\Oculus\oculus_openxr_64.json").name, "Meta Quest Link")
        self.assertIsNone(vr.detect(windows=True, registry=lambda: None))
        self.assertIsNone(vr.detect(windows=True, registry=lambda: ""))

        def broken():
            raise OSError("no registry")
        self.assertIsNone(vr.detect(windows=True, registry=broken))

    def test_windows_registry_read_is_safe(self):
        fake = mock.MagicMock()
        fake.KEY_READ, fake.KEY_WOW64_64KEY, fake.HKEY_LOCAL_MACHINE = 1, 2, 3
        fake.OpenKey.side_effect = OSError
        with mock.patch.dict(sys.modules, {"winreg": fake}):
            self.assertIsNone(vr.read_windows_runtime())
            fake.OpenKey.side_effect = None
            fake.OpenKey.return_value.__enter__.return_value = "key"
            fake.QueryValueEx.return_value = (r"C:\x.json", 1)
            self.assertEqual(vr.read_windows_runtime(), r"C:\x.json")
            self.assertEqual(fake.OpenKey.call_args.args[0:2], (3, vr.WINDOWS_KEY))

    def test_host_config_home(self):
        home, other = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        manifest = other / "openxr" / "1" / "active_runtime.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}")
        self.assertEqual(vr.active_runtime_manifest(home, {"HOST_XDG_CONFIG_HOME": str(other)}), manifest)

    def test_explicit_runtime_precedes_registry_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            chosen = home / "chosen.json"
            chosen.write_text('{"runtime":{"name":"WiVRn"}}')
            registry = mock.Mock(return_value="unused.json")
            for windows in (False, True):
                with self.subTest(windows=windows):
                    found = vr.detect(home, {"XR_RUNTIME_JSON": str(chosen)}, windows=windows, registry=registry)
                    self.assertIsNotNone(found)
                    self.assertEqual(found.path, str(chosen))
            registry.assert_not_called()
            default = home / ".config/openxr/1/active_runtime.json"
            default.parent.mkdir(parents=True)
            default.write_text("{}")
            for windows in (False, True):
                self.assertIsNone(vr.detect(home, {"XR_RUNTIME_JSON": str(home / "missing.json")},
                                            windows=windows, registry=registry))
            registry.assert_not_called()  # an invalid explicit choice never falls back

    def test_native_xdg_and_architecture_precedence(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("platform.machine", return_value="x86_64"):
            home = Path(tmp)
            first, second = home / "first", home / "second"
            manifests = []
            for folder in (first, second):
                manifest = folder / "openxr/1/active_runtime.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text("{}")
                manifests.append(manifest)
            env = {"XDG_CONFIG_HOME": str(first), "XDG_CONFIG_DIRS": str(second)}
            self.assertEqual(vr.active_runtime_manifest(home, env), manifests[0])
            decorated = first / "openxr/1/active_runtime.x86_64.json"
            decorated.write_text("{}")
            self.assertEqual(vr.active_runtime_manifest(home, env), decorated)
            decorated.unlink()
            manifests[0].unlink()
            self.assertEqual(vr.active_runtime_manifest(home, env), manifests[1])

    def test_flatpak_ignores_sandbox_xdg_and_uses_host(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            host, sandbox = home / "host", home / "sandbox"
            for folder in (host, sandbox):
                manifest = folder / "openxr/1/active_runtime.json"
                manifest.parent.mkdir(parents=True)
                manifest.write_text("{}")
            env = {"XDG_CONFIG_HOME": str(sandbox), "HOST_XDG_CONFIG_HOME": str(host)}
            with mock.patch.object(paths, "IN_FLATPAK", True):
                found = vr.detect(home, env, windows=False)
                self.assertEqual(found.path, str(host / "openxr/1/active_runtime.json"))
                default = home / ".config/openxr/1/active_runtime.json"
                default.parent.mkdir(parents=True)
                default.write_text("{}")
                found = vr.detect(home, {"XDG_CONFIG_HOME": str(sandbox)}, windows=False)
                self.assertEqual(found.path, str(default))
            cfg = self.config()
            for explicit in (str(host / "missing.json"), str(host / "openxr/1/active_runtime.json")):
                output = {}
                vr.configure(cfg, output, [], [], in_flatpak=True, home=home,
                             environ={**env, "XR_RUNTIME_JSON": explicit}, session=True)
                self.assertNotIn("XR_RUNTIME_JSON", output)  # keep the exact inherited selection
            output = {}
            vr.configure(cfg, output, [], [], in_flatpak=True, home=home,
                         environ={**env, "XR_RUNTIME_JSON": ""}, session=True)
            self.assertEqual(output["XR_RUNTIME_JSON"], str(host / "openxr/1/active_runtime.json"))

    def test_relative_xdg_home_matches_pinned_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            manifest = home / "relative/openxr/1/active_runtime.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}")
            previous = Path.cwd()
            try:
                os.chdir(home)
                self.assertEqual(vr.active_runtime_manifest(home, {"XDG_CONFIG_HOME": "relative"}),
                                 Path("relative/openxr/1/active_runtime.json"))
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
