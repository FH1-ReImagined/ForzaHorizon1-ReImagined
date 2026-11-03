"""Standalone stock-menu tuning uses the ordinary launcher config/TOML path."""
from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import config, schema, game  # noqa: E402


class NativeTuningSettings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "launcher.json"
        self.cfg = config.Config(self.path)
        self.cfg.advanced["data_root"] = self.temp.name

    def test_tuning_is_independently_opt_in_and_reaches_game_configuration(self):
        setting = schema.BY_KEY["forza_car_tuning"]
        self.assertEqual((setting.label, setting.group, setting.section, setting.platform),
                         ("Native car tuning", "Gameplay", "Car tuning", ""))
        self.assertIs(self.cfg.settings["forza_native_pause"], False)
        self.assertNotIn("forza_native_pause", config.parse_toml(config.render_toml(self.cfg)))
        self.assertIs(self.cfg.settings[setting.key], False)
        self.assertNotIn(setting.key, config.parse_toml(config.render_toml(self.cfg)))
        self.assertTrue(setting.restart)
        self.cfg.settings[setting.key] = True
        self.cfg.save()
        restored = config.Config(self.path)
        self.assertIs(config.parse_toml(config.render_toml(restored))[setting.key], True)

    def test_switch_selects_stock_or_tuning_overlay_without_menu_dependency(self):
        folder = Path(self.temp.name) / "runtime"
        install = game.GameInstall(folder, folder / game.EXE)
        for name in ("update", "update-native", "update-menu"):
            (folder / name).mkdir(parents=True)
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for tuning, menu in ((False, False), (True, False), (True, True), (False, True), (False, False)):
                with self.subTest(tuning=tuning, menu=menu):
                    self.cfg.settings["forza_car_tuning"] = tuning
                    self.cfg.advanced["extras_toml"] = f"forza_native_pause = {str(menu).lower()}"
                    chosen = game.update_data_root(self.cfg, install, config.render_toml(self.cfg))
                    self.assertEqual(chosen.name, "update-menu" if menu else "update-native" if tuning else "update")
            self.cfg.advanced["extras_toml"] = ""
            (folder / "update-native").rmdir()
            self.cfg.settings["forza_car_tuning"] = True
            self.assertEqual(game.update_data_root(self.cfg, install, config.render_toml(self.cfg)).name, "update")

    def test_cli_and_environment_disable_override_enabled_toml(self):
        text = "forza_car_tuning = true\n"
        with mock.patch.dict(game.os.environ, {}, clear=True):
            self.assertTrue(game.native_update_requested(self.cfg, text))
            self.cfg.advanced["extra_env"] = "REX_FORZA_CAR_TUNING=false"
            self.assertFalse(game.native_update_requested(self.cfg, text))
            self.cfg.advanced["extra_args"] = "--forza_car_tuning=true"
            self.assertTrue(game.native_update_requested(self.cfg, text))
            self.cfg.advanced["extra_args"] = "--forza_car_tuning=false"
            self.assertFalse(game.native_update_requested(self.cfg, text))
            self.cfg.advanced["extra_args"] = "--forza_car_tuning false"
            self.assertTrue(game.native_update_requested(self.cfg, text)) # Bare flag, positional value.
            self.cfg.advanced["extra_args"] = "--no-forza_car_tuning"
            self.assertFalse(game.native_update_requested(self.cfg, text))
            self.cfg.advanced["extra_args"] = "--no-forza_car_tuning=true"
            self.assertFalse(game.native_update_requested(self.cfg, text))
            self.cfg.advanced["extra_args"] = "--no-forza_car_tuning=false"
            self.assertTrue(game.native_update_requested(self.cfg, text))

    def test_launch_plan_selects_overlay_and_respects_explicit_update_path(self):
        folder = Path(self.temp.name) / "runtime"
        for name in ("update", "update-native", "update-menu"):
            (folder / name).mkdir(parents=True)
        install = game.GameInstall(folder, folder / game.EXE)
        self.cfg.online["mode"] = "offline"
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for tuning in (False, True, False):
                self.cfg.settings["forza_car_tuning"] = tuning
                plan = game.build_plan(self.cfg, install, write=False, battery_saver=False)
                updates = [arg for arg in plan.argv if arg.startswith("--update_data_root=")]
                self.assertEqual(updates, [f"--update_data_root={folder / ('update-native' if tuning else 'update')}"])
            self.cfg.settings["forza_car_tuning"] = True
            self.cfg.advanced["extra_args"] = "--update_data_root=explicit-update"
            plan = game.build_plan(self.cfg, install, write=False, battery_saver=False)
            self.assertEqual([arg for arg in plan.argv if arg.startswith("--update_data_root=")],
                             ["--update_data_root=explicit-update"])

    def test_process_environment_and_extra_environment_follow_runtime_precedence(self):
        with mock.patch.dict(game.os.environ, {"REX_FORZA_CAR_TUNING": "true"}, clear=True):
            self.assertTrue(game.native_update_requested(self.cfg, ""))
            self.cfg.advanced["extra_env"] = "REX_FORZA_CAR_TUNING=0"
            self.assertFalse(game.native_update_requested(self.cfg, ""))
            self.cfg.advanced["extra_args"] = "--forza_car_tuning=true"
            self.assertTrue(game.native_update_requested(self.cfg, ""))

    def test_environment_bool_matches_case_sensitive_sdk_conversion(self):
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for value, expected in (("yes", True), ("true", True), ("1", True),
                                    ("YES", False), ("TRUE", False), ("on", False),
                                    ("false", False), ("0", False)):
                with self.subTest(value=value):
                    self.cfg.advanced["extra_env"] = "REX_FORZA_CAR_TUNING=" + value
                    self.assertEqual(game.native_update_requested(self.cfg, ""), expected)

    def test_registered_cli_bool_is_case_insensitive_and_separate_from_environment(self):
        with mock.patch.dict(game.os.environ, {}, clear=True):
            self.cfg.advanced["extra_env"] = "REX_FORZA_CAR_TUNING=FALSE"
            for value in ("yes", "YES", "TRUE", "on", "ENABLE", "t", "Y", "+"):
                with self.subTest(value=value):
                    self.cfg.advanced["extra_args"] = "--forza_car_tuning=" + value
                    self.assertTrue(game.native_update_requested(self.cfg, ""))
            for flags, expected in (("--forza_car_tuning=2", True),
                                    ("--forza_car_tuning=false --forza_car_tuning=true", False),
                                    ("--forza_car_tuning=2 --no-forza_car_tuning", True),
                                    ("--forza_car_tuning=-2", False)):
                self.cfg.advanced["extra_args"] = flags
                self.assertEqual(game.native_update_requested(self.cfg, ""), expected)

    def test_registered_cli_counts_match_cli11_zero_numeric_and_empty_overrides(self):
        # Checked with the SDK's CLI11 2.6.1 add_flag_function registration,
        # including its Sum policy and negated get_flag_value transformation.
        cases = (
            ("--forza_car_tuning --forza_car_tuning=0", True),
            ("--forza_car_tuning --forza_car_tuning=00", True),
            ("--forza_car_tuning=0", False),
            ("--no-forza_car_tuning=0", True),
            ("--no-forza_car_tuning=00", False),
            ("--forza_car_tuning=08", True),
            ("--forza_car_tuning=0x10", True),
            ("--forza_car_tuning=0b10", True),
            ("--forza_car_tuning=1_000", True),
            ("--forza_car_tuning=2.5", True),
            ("--forza_car_tuning=0.5", False),
            ("--forza_car_tuning=0.5 --forza_car_tuning=0.5", True),
            ("--no-forza_car_tuning=2.5", True),
            ("--forza_car_tuning=1e2", True),
            ("--no-forza_car_tuning=1e2", True),
            ("--forza_car_tuning=1e-2", False),
            ("--forza_car_tuning=", True),
            ("--forza_car_tuning={}", True),
            ("--no-forza_car_tuning=", False),
            ("--no-forza_car_tuning={}", False),
        )
        with mock.patch.dict(game.os.environ, {}, clear=True):
            for flags, expected in cases:
                with self.subTest(flags=flags):
                    self.cfg.advanced["extra_args"] = flags
                    self.assertEqual(game.native_update_requested(self.cfg, ""), expected)

    def test_cli_end_of_options_preserves_config_and_environment_selection(self):
        with mock.patch.dict(game.os.environ, {}, clear=True):
            self.cfg.advanced["extra_args"] = "-- --forza_car_tuning"
            self.assertFalse(game.native_update_requested(self.cfg, ""))
            self.cfg.advanced["extra_args"] = "-- --no-forza_car_tuning"
            self.assertTrue(game.native_update_requested(self.cfg, "forza_car_tuning = true\n"))

    def test_invalid_cli_count_does_not_preserve_enabled_environment(self):
        with mock.patch.dict(game.os.environ, {"REX_FORZA_CAR_TUNING": "yes"}, clear=True):
            for flags in ("--forza_car_tuning=nonsense",
                          "--forza_car_tuning=yes --forza_car_tuning=nonsense"):
                with self.subTest(flags=flags):
                    self.cfg.advanced["extra_args"] = flags
                    self.assertFalse(game.native_update_requested(self.cfg, ""))

    def test_import_is_managed_setting_and_disabling_restores_default(self):
        config.import_toml(self.cfg, "forza_car_tuning = true\n")
        self.assertIs(self.cfg.settings["forza_car_tuning"], True)
        self.assertNotIn("forza_car_tuning", self.cfg.advanced["extras_toml"])
        config.import_toml(self.cfg, "forza_car_tuning = false\n")
        self.cfg.save()
        restored = config.Config(self.path)
        self.assertIs(restored.settings["forza_car_tuning"], False)
        self.assertNotIn("forza_car_tuning", config.parse_toml(config.render_toml(restored)))


if __name__ == "__main__":
    unittest.main()
