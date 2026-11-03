"""The experimental native pause menu uses the ordinary launcher setting/TOML path."""
from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import config, schema, game  # noqa: E402


class NativeMenuSettings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "launcher.json"
        self.cfg = config.Config(self.path)
        self.cfg.advanced["data_root"] = self.temp.name

    def test_opt_in_setting_is_available_in_advanced_menu_on_every_platform(self):
        setting = schema.BY_KEY["forza_native_pause"]
        self.assertEqual((setting.group, setting.section, setting.kind), ("Advanced", "Menu", "bool"))
        self.assertEqual(setting.label, "Native pause menu (experimental)")
        self.assertTrue(setting.restart)
        self.assertEqual(setting.platform, "")
        self.assertIn(setting, schema.for_group("Advanced"))
        self.assertIs(self.cfg.settings[setting.key], False)
        self.assertNotIn(setting.key, config.parse_toml(config.render_toml(self.cfg)))

    def test_enable_survives_save_and_reaches_game_configuration(self):
        self.cfg.settings["forza_native_pause"] = True
        self.cfg.save()
        restored = config.Config(self.path)
        self.assertIs(restored.settings["forza_native_pause"], True)
        rendered = config.parse_toml(config.render_toml(restored))
        self.assertIs(rendered["forza_native_pause"], True)
        self.assertFalse(any(key.startswith("fh2_") for key in rendered))

    def test_tuning_is_independently_opt_in_and_reaches_game_configuration(self):
        setting = schema.BY_KEY["forza_car_tuning"]
        self.assertIs(self.cfg.settings[setting.key], False)
        self.assertNotIn(setting.key, config.parse_toml(config.render_toml(self.cfg)))
        self.assertTrue(setting.restart)
        self.cfg.settings[setting.key] = True
        self.cfg.save()
        restored = config.Config(self.path)
        self.assertIs(config.parse_toml(config.render_toml(restored))[setting.key], True)

    def test_restart_toggles_select_stock_or_prepared_overlay_without_rebuild(self):
        folder = Path(self.temp.name) / "runtime"
        install = game.GameInstall(folder, folder / game.EXE)
        for name in ("update", "update-native", "update-menu"):
            (folder / name).mkdir(parents=True)
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for pause, tuning in ((False, False), (True, False), (False, True), (True, True), (False, False)):
                with self.subTest(pause=pause, tuning=tuning):
                    self.cfg.settings.update(forza_native_pause=pause, forza_car_tuning=tuning)
                    chosen = game.update_data_root(self.cfg, install, config.render_toml(self.cfg))
                    self.assertEqual(chosen.name, "update-menu" if pause else "update-native" if tuning else "update")
            (folder / "update-menu").rmdir()
            self.cfg.settings["forza_native_pause"] = True
            self.assertEqual(game.update_data_root(self.cfg, install, config.render_toml(self.cfg)).name, "update")

    def test_cli_and_environment_disable_override_enabled_toml(self):
        text = "forza_native_pause = true\n"
        with mock.patch.dict(game.os.environ, {}, clear=True):
            self.assertTrue(game.native_update_requested(self.cfg, text, ("forza_native_pause",)))
            self.cfg.advanced["extra_env"] = "REX_FORZA_NATIVE_PAUSE=false"
            self.assertFalse(game.native_update_requested(self.cfg, text, ("forza_native_pause",)))
            self.cfg.advanced["extra_args"] = "--forza_native_pause=true"
            self.assertTrue(game.native_update_requested(self.cfg, text, ("forza_native_pause",)))
            self.cfg.advanced["extra_args"] = "--forza_native_pause=false"
            self.assertFalse(game.native_update_requested(self.cfg, text, ("forza_native_pause",)))
            self.cfg.advanced["extra_args"] = "--forza_native_pause false"
            self.assertTrue(game.native_update_requested(self.cfg, text, ("forza_native_pause",))) # Bare flag, positional value.
            self.cfg.advanced["extra_args"] = "--no-forza_native_pause"
            self.assertFalse(game.native_update_requested(self.cfg, text, ("forza_native_pause",)))
            self.cfg.advanced["extra_args"] = "--no-forza_native_pause=true"
            self.assertFalse(game.native_update_requested(self.cfg, text, ("forza_native_pause",)))
            self.cfg.advanced["extra_args"] = "--no-forza_native_pause=false"
            self.assertTrue(game.native_update_requested(self.cfg, text, ("forza_native_pause",)))

    def test_import_is_managed_setting_and_disabling_restores_default(self):
        config.import_toml(self.cfg, "forza_native_pause = true\n")
        self.assertIs(self.cfg.settings["forza_native_pause"], True)
        self.assertNotIn("forza_native_pause", self.cfg.advanced["extras_toml"])
        config.import_toml(self.cfg, "forza_native_pause = false\n")
        self.cfg.save()
        restored = config.Config(self.path)
        self.assertIs(restored.settings["forza_native_pause"], False)
        self.assertNotIn("forza_native_pause", config.parse_toml(config.render_toml(restored)))

    def test_effective_menu_flag_is_independent_of_tuning_cli_and_environment(self):
        folder = Path(self.temp.name) / "runtime"
        install = game.GameInstall(folder, folder / game.EXE)
        for name in ("update", "update-native", "update-menu"):
            (folder / name).mkdir(parents=True)
        self.cfg.settings.update(forza_native_pause=True, forza_car_tuning=True)
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for env, flags, expected in (
                ("REX_FORZA_NATIVE_PAUSE=false", "", "update-native"),
                ("REX_FORZA_NATIVE_PAUSE=yes", "--no-forza_car_tuning", "update-menu"),
                ("REX_FORZA_NATIVE_PAUSE=TRUE", "", "update-native"),
                ("", "--no-forza_native_pause --forza_car_tuning=yes", "update-native"),
                ("", "--forza_native_pause --forza_native_pause=0 --no-forza_car_tuning", "update-menu"),
            ):
                with self.subTest(env=env, flags=flags):
                    self.cfg.advanced.update(extra_env=env, extra_args=flags)
                    self.assertEqual(game.update_data_root(self.cfg, install, config.render_toml(self.cfg)).name,
                                     expected)

    def test_missing_menu_never_mounts_tuning_overlay_as_a_full_menu(self):
        folder = Path(self.temp.name) / "runtime"
        install = game.GameInstall(folder, folder / game.EXE)
        for name in ("update", "update-native"):
            (folder / name).mkdir(parents=True)
        self.cfg.settings.update(forza_native_pause=True, forza_car_tuning=True)
        with mock.patch.dict(game.os.environ, {}, clear=True):
            self.assertEqual(game.update_data_root(self.cfg, install, config.render_toml(self.cfg)), folder / "update")
            self.assertTrue(game.native_tuning_needs_build(self.cfg, install))
            self.cfg.advanced["extra_args"] = "--update_data_root=external"
            self.assertFalse(game.native_tuning_needs_build(self.cfg, install))

    def test_explicit_update_root_remains_authoritative_with_both_options(self):
        folder = Path(self.temp.name) / "runtime"
        install = game.GameInstall(folder, folder / game.EXE)
        for name in ("update", "update-native", "update-menu"):
            (folder / name).mkdir(parents=True)
        self.cfg.settings.update(forza_native_pause=True, forza_car_tuning=True)
        self.cfg.online["mode"] = "offline"
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for flags in ("--update_data_root=external", '--update_data_root "external"'):
                with self.subTest(flags=flags):
                    self.cfg.advanced["extra_args"] = flags
                    plan = game.build_plan(self.cfg, install, write=False, battery_saver=False)
                    self.assertEqual(sum(arg.startswith("--update_data_root") for arg in plan.argv), 1)
                    self.assertFalse(any("update-menu" in arg for arg in plan.argv))


if __name__ == "__main__":
    unittest.main()
