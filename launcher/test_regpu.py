"""ReGPU, the experimental Direct3D 12 renderer: the Graphics API setting's "regpu" value in forza.toml,
the launch plan's fallback when the build has no plugin, the shader prebuild's check and the Windows
build's fallback when only the plugin fails to compile
(python3 -m unittest launcher/test_regpu.py from the repository root)."""
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import builder, config, paths, regpu, schema, shader_prebuild  # noqa: E402
from forza_launcher.config import Config  # noqa: E402


def windows_schema(test):
    """schema as on Windows (the Graphics API setting exists only there), restored after the test."""
    with mock.patch.object(paths, "IS_WINDOWS", True):
        importlib.reload(schema)
    test.addCleanup(importlib.reload, schema)


class RegpuSettingTests(unittest.TestCase):
    def setUp(self):
        windows_schema(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.advanced["data_root"] = str(self.root)

    def test_choice_is_offered_on_windows_only_and_off_by_default(self):
        s = schema.BY_KEY["gpu_backend"]
        self.assertEqual(s.default, "vulkan")
        self.assertEqual(s.platform, "windows")
        self.assertIn(("regpu", "ReGPU (experimental)"), s.choices)
        self.assertTrue(s.restart)
        self.assertIn("experimental", s.help)
        importlib.reload(schema)   # this platform's own schema
        if not paths.IS_WINDOWS:
            self.assertNotIn("gpu_backend", schema.BY_KEY)

    def toml(self):
        return config.flatten(config.parse_toml(config.render_toml(self.cfg)))

    def test_regpu_writes_the_d3d12_backend_and_the_plugin(self):
        self.cfg.settings["gpu_backend"] = "regpu"
        flat = self.toml()
        self.assertEqual((flat.get("gpu_backend"), flat.get("gpu_plugin")), ("d3d12", "regpu"))

    def test_other_backends_load_no_plugin(self):
        for backend, written in (("vulkan", None), ("d3d12", "d3d12")):
            self.cfg.settings["gpu_backend"] = backend
            flat = self.toml()
            self.assertEqual(flat.get("gpu_backend"), written, backend)
            self.assertNotIn("gpu_plugin", flat, backend)

    def test_an_existing_regpu_toml_is_read_back_as_the_choice(self):
        config.import_toml(self.cfg, 'gpu_backend = "d3d12"\ngpu_plugin = "regpu"\n')
        self.assertEqual(self.cfg.settings["gpu_backend"], "regpu")
        self.assertNotIn("gpu_plugin", self.cfg.advanced.get("extras_toml", ""))


class RegpuFallbackTests(unittest.TestCase):
    def test_a_build_without_the_plugin_plays_with_vulkan(self):
        with tempfile.TemporaryDirectory() as d:
            settings = {"gpu_backend": "regpu"}
            note = regpu.fall_back(settings, Path(d))
            self.assertEqual(settings["gpu_backend"], "vulkan")
            self.assertIn("Vulkan", note)
            settings = {"gpu_backend": "regpu"}
            (Path(d) / regpu.PLUGIN).write_bytes(b"MZ")
            self.assertEqual(regpu.fall_back(settings, Path(d)), "")
            self.assertEqual(settings["gpu_backend"], "regpu")

    def test_other_backends_are_left_alone(self):
        with tempfile.TemporaryDirectory() as d:
            for backend in ("vulkan", "d3d12"):
                settings = {"gpu_backend": backend}
                self.assertEqual(regpu.fall_back(settings, Path(d)), "")
                self.assertEqual(settings["gpu_backend"], backend)


class RegpuLaunchPlanTests(unittest.TestCase):
    """Play: forza.toml loads the plugin when the build has it, else this start plays with Vulkan."""

    def setUp(self):
        windows_schema(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.cfg.online["mode"] = "offline"
        self.cfg.settings["gpu_backend"] = "regpu"
        from forza_launcher import game
        self.game = game
        (t / "rt").mkdir()
        self.install = game.GameInstall(t / "rt", t / "rt" / game.EXE)

    def plan(self):
        return self.game.build_plan(self.cfg, self.install, write=False, log=lambda _t: None)

    def test_with_the_plugin(self):
        (self.install.dir / regpu.PLUGIN).write_bytes(b"MZ")
        plan = self.plan()
        flat = config.parse_toml(plan.toml_text)
        self.assertEqual((flat.get("gpu_backend"), flat.get("gpu_plugin")), ("d3d12", "regpu"))
        self.assertFalse([n for n in plan.notes if "ReGPU" in n])

    def test_without_the_plugin(self):
        plan = self.plan()
        flat = config.parse_toml(plan.toml_text)
        self.assertNotIn("gpu_plugin", flat)
        self.assertNotIn("gpu_backend", flat)   # Vulkan, the default
        self.assertTrue([n for n in plan.notes if "ReGPU" in n and "Vulkan" in n])
        self.assertEqual(self.cfg.settings["gpu_backend"], "regpu")   # the player's choice stays


class PrebuildBackendTests(unittest.TestCase):
    def plan(self, argv=(), toml=""):
        return types.SimpleNamespace(argv=["forza.exe", *argv], toml_text=toml)

    def test_the_backend_in_forza_toml_counts(self):
        self.assertTrue(shader_prebuild.uses_vulkan(self.plan()))
        self.assertFalse(shader_prebuild.uses_vulkan(self.plan(toml='gpu_backend = "d3d12"\ngpu_plugin = "regpu"\n')))
        self.assertFalse(shader_prebuild.uses_vulkan(self.plan(toml='gpu_backend = "d3d12"\n')))

    def test_the_command_line_wins(self):
        self.assertTrue(shader_prebuild.uses_vulkan(self.plan(["--gpu_backend=vulkan"], 'gpu_backend = "d3d12"\n')))
        self.assertFalse(shader_prebuild.uses_vulkan(self.plan(["--gpu_backend=d3d12"])))


class PluginBuildFailureTests(unittest.TestCase):
    def test_only_plugin_outputs_fall_back(self):
        self.assertTrue(builder.regpu_only_failure([
            "FAILED: regpu-plugin/regpu/CMakeFiles/regpu.dir/src/gpu/renderer.cpp.obj ",
            "FAILED: [code=1] regpu-plugin/CMakeFiles/rexgpu-regpu.dir/src/plugin_main.cpp.obj ",
            "FAILED: rexgpu-regpu.dll ",
        ]))

    def test_windows_paths(self):   # ninja on Windows prints its outputs with backslashes
        self.assertTrue(builder.regpu_only_failure([
            "FAILED: regpu-plugin\\regpu\\CMakeFiles\\regpu.dir\\src\\gpu\\renderer.cpp.obj ",
        ]))
        self.assertFalse(builder.regpu_only_failure([
            "FAILED: CMakeFiles\\forza.dir\\src\\main.cpp.obj ",
        ]))

    def test_a_game_failure_does_not(self):
        self.assertFalse(builder.regpu_only_failure([]))
        self.assertFalse(builder.regpu_only_failure(["FAILED: CMakeFiles/forza.dir/src/main.cpp.obj "]))
        self.assertFalse(builder.regpu_only_failure([
            "FAILED: regpu-plugin/CMakeFiles/rexgpu-regpu.dir/src/plugin_main.cpp.obj ",
            "FAILED: forza.exe ",
        ]))


if __name__ == "__main__":
    unittest.main()
