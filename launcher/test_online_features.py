"""The "Online features" choice (#497, PRIVACY.md): the Windows installer's install_options.json,
the first-run question on Linux and Flatpak, and what turning the features off changes."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import config, coop_grant, online_features, paths, shader_share
from forza_launcher.config import Config

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

REPO = Path(__file__).resolve().parent.parent


def features(cfg):
    return (cfg.online["mode"], cfg.settings["share_shader_list"], cfg.settings["coop_results"],
            cfg.settings["discord_presence"], cfg.d["check_updates"])


ALL_ON = ("public", True, True, True, True)
ALL_OFF = ("offline", False, False, False, False)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")

    def seed(self, on, stamp="20261028120000", name="install_options.json"):
        path = self.root / name
        path.write_text(json.dumps({"online_features": on, "stamp": stamp}), encoding="utf-8")
        return path


class ChoiceTests(Base):
    def test_defaults_are_on_and_nothing_is_chosen_yet(self):
        self.assertEqual(features(self.cfg), ALL_ON)
        self.assertEqual(self.cfg.d["online_features"], {"on": None, "ask": False, "installer": ""})

    def test_installer_unticked_turns_everything_off_on_a_new_config(self):
        self.assertTrue(online_features.take_installer_choice(self.cfg, True, self.seed(False)))
        self.assertEqual(features(self.cfg), ALL_OFF)
        self.assertIs(self.cfg.d["online_features"]["on"], False)
        self.assertFalse(online_features.question_due(self.cfg, True))
        self.assertEqual(config.render_toml(self.cfg).count("live_server"), 0)

    def test_installer_ticked_keeps_the_defaults_and_asks_nothing(self):
        self.assertTrue(online_features.take_installer_choice(self.cfg, True, self.seed(True)))
        self.assertEqual(features(self.cfg), ALL_ON)
        self.assertIs(self.cfg.d["online_features"]["on"], True)
        self.assertFalse(online_features.question_due(self.cfg, True))

    def test_an_installer_choice_is_applied_once(self):
        seed = self.seed(False)
        online_features.take_installer_choice(self.cfg, True, seed)
        self.cfg.online["mode"] = "public"     # the player turns online play back on
        self.assertFalse(online_features.take_installer_choice(self.cfg, False, seed))
        self.assertEqual(self.cfg.online["mode"], "public")
        # the next installer run (an update) with the option unticked again
        self.assertTrue(online_features.take_installer_choice(self.cfg, False, self.seed(False, "20261101090000")))
        self.assertEqual(self.cfg.online["mode"], "offline")

    def test_an_update_with_the_option_ticked_keeps_the_players_settings(self):
        self.cfg.online["mode"] = "local"
        self.cfg.settings["share_shader_list"] = False
        self.assertTrue(online_features.take_installer_choice(self.cfg, False, self.seed(True)))
        self.assertEqual(self.cfg.online["mode"], "local")
        self.assertFalse(self.cfg.settings["share_shader_list"])
        self.assertIsNone(self.cfg.d["online_features"]["on"])

    def test_an_update_with_the_option_unticked_turns_them_off(self):
        self.assertTrue(online_features.take_installer_choice(self.cfg, False, self.seed(False)))
        self.assertEqual(features(self.cfg), ALL_OFF)

    def test_a_missing_or_broken_file_changes_nothing(self):
        bad = self.root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        wrong = self.root / "wrong.json"
        wrong.write_text(json.dumps({"online_features": "no"}), encoding="utf-8")
        for path in (self.root / "missing.json", bad, wrong):
            self.assertFalse(online_features.take_installer_choice(self.cfg, True, path))
        self.assertEqual(features(self.cfg), ALL_ON)

    def test_the_installers_file_with_a_byte_order_mark_is_read(self):
        path = self.root / "bom.json"
        path.write_bytes(b"\xef\xbb\xbf" + b'{"online_features": false, "stamp": "1"}')
        self.assertEqual(online_features.read_seed(path), (False, "1"))

    def test_the_file_is_looked_for_next_to_the_packaged_launcher_only(self):
        with patch.object(paths, "FROZEN", False):
            self.assertIsNone(online_features.seed_path())
        with patch.object(paths, "FROZEN", True), patch.object(paths, "app_dir", return_value=self.root):
            self.assertEqual(online_features.seed_path(), self.root / "install_options.json")

    def test_the_first_run_question_without_an_installer(self):
        self.assertTrue(online_features.question_due(self.cfg, True))
        self.cfg.save()
        again = Config(self.cfg.path)      # closed before answering: asked again
        self.assertTrue(online_features.question_due(again, False))
        online_features.answer(again, False)
        self.assertEqual(features(again), ALL_OFF)
        self.assertFalse(online_features.question_due(again, False))

    def test_answering_on_changes_nothing(self):
        online_features.question_due(self.cfg, True)
        self.cfg.online["mode"] = "custom"     # an imported forza.toml named a server
        online_features.answer(self.cfg, True)
        self.assertEqual(self.cfg.online["mode"], "custom")
        self.assertIs(self.cfg.d["online_features"]["on"], True)

    def test_an_existing_config_is_not_asked(self):
        self.assertFalse(online_features.question_due(self.cfg, False))

    def test_a_settings_reset_keeps_the_features_off(self):
        online_features.answer(self.cfg, False)
        self.cfg.d["check_updates"] = True    # turned back on, on the About page
        self.cfg.d["online"] = {"mode": "public", "custom_server": ""}
        self.cfg.settings.update(share_shader_list=True, coop_results=True, discord_presence=True)
        online_features.keep_off_after_reset(self.cfg)
        self.assertEqual(features(self.cfg), ("offline", False, False, False, True))

    def test_a_reset_leaves_a_player_who_kept_them_on_alone(self):
        online_features.answer(self.cfg, True)
        online_features.keep_off_after_reset(self.cfg)
        self.assertEqual(features(self.cfg), ALL_ON)

    def test_the_choice_is_saved(self):
        online_features.take_installer_choice(self.cfg, True, self.seed(False, "abc"))
        self.cfg.save()
        loaded = Config(self.cfg.path)
        self.assertEqual(features(loaded), ALL_OFF)
        self.assertEqual(loaded.d["online_features"], {"on": False, "ask": False, "installer": "abc"})


class NetworkGateTests(Base):
    def test_coop_results_off_asks_the_server_nothing(self):
        self.cfg.settings["coop_results"] = False
        with patch.object(coop_grant, "fetch", side_effect=AssertionError("no request")):
            self.assertEqual(coop_grant.after_session(self.cfg, object(), base="http://x:1"), "off")
            self.assertIsNone(coop_grant.after_session_in_background(self.cfg, object()))

    def test_offline_stops_sharing_and_coop_results(self):
        online_features.turn_off(self.cfg)
        self.assertEqual(shader_share.server_base(self.cfg), "")
        self.assertFalse(shader_share.sharing_on(self.cfg))
        self.assertIsNone(coop_grant.after_session_in_background(self.cfg, object()))

    def test_coop_results_is_a_launcher_setting(self):
        self.assertIn("coop_results", config.LAUNCHER_ONLY)
        self.cfg.settings["coop_results"] = False
        self.assertNotIn("coop_results", config.render_toml(self.cfg))


class PolicyTextTests(unittest.TestCase):
    def test_the_policy_is_plain_ascii_for_the_installer_page(self):
        # Inno Setup shows PRIVACY.md (as licenses/PRIVACY.txt) on a plain-text page.
        text = (REPO / "PRIVACY.md").read_bytes()
        self.assertTrue(text.isascii())
        self.assertNotIn(b"|", text)     # no tables

    def test_the_policy_names_the_settings_that_exist(self):
        from forza_launcher import schema
        text = (REPO / "PRIVACY.md").read_text(encoding="utf-8")
        for key in ("share_shader_list", "coop_results", "discord_presence", "discord_presence_details",
                    "live_signin", "world_challenges", "live_accept_invites"):
            self.assertIn(f'"{schema.BY_KEY[key].label}"', text)
        self.assertIn("Settings > Profile > " + schema.BY_KEY["gamertag"].label, text)
        self.assertIn('"Check for updates when the launcher opens"', text)
        self.assertIn('"Graphics API" set to Direct3D 12', text)     # Windows-only settings
        self.assertIn('"NVIDIA DLSS" and "NVIDIA DLAA" off', text)

    def test_the_package_ships_the_policy_for_the_installer(self):
        make = (REPO / "launcher" / "packaging" / "make_package.py").read_text(encoding="utf-8")
        iss = (REPO / "launcher" / "packaging" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn('"PRIVACY.md"', make)
        self.assertIn(r"InfoBeforeFile={#Stage}\licenses\PRIVACY.txt", iss)
        self.assertIn(online_features.SEED_NAME, iss)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class FirstRunWindowTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        from forza_launcher import build_env
        self.cfg.advanced["data_root"] = str(self.root)
        self.cfg.gpu.update(checked=True, vendor="amd")
        self.seed_file = self.root / "no-installer.json"
        for target, kw in (("forza_launcher.paths.config_dir", {"return_value": self.root}),
                           ("forza_launcher.game.find_game", {"return_value": (None, [])}),
                           ("forza_launcher.build_env._detect_tools", {"return_value": build_env.Tools()}),
                           ("forza_launcher.paths.in_checkout", {"return_value": False}),
                           ("forza_launcher.online_features.seed_path", {"side_effect": lambda: self.seed_file})):
            p = patch(target, **kw)
            p.start()
            self.addCleanup(p.stop)

    def window(self, answer=None):
        from forza_launcher.ui.online_features import OnlineFeaturesDialog
        from forza_launcher.ui.window import MainWindow
        asked = []

        def exec_(dialog):
            asked.append(True)
            dialog.online.setChecked(answer)
            return 1
        with patch.object(OnlineFeaturesDialog, "exec", exec_):
            win = MainWindow(self.cfg)
            self.addCleanup(win.close)
            checks = []
            win.about.updates.check = lambda: checks.append(True)
            self.wait(lambda: asked or answer is None, 1)
        return win, asked, checks

    def wait(self, predicate, seconds):
        until = time.monotonic() + seconds
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()

    def test_unticked_turns_everything_off_and_skips_the_update_check(self):
        win, asked, checks = self.window(answer=False)
        self.assertEqual(asked, [True])
        self.assertEqual(features(self.cfg), ALL_OFF)
        self.assertTrue(win.settings.radios["offline"].isChecked())
        self.assertFalse(win.about.updates.automatic.isChecked())
        self.wait(lambda: checks, 2)
        self.assertEqual(checks, [])
        saved = Config(self.cfg.path)
        self.assertEqual(features(saved), ALL_OFF)
        self.assertFalse(saved.d["online_features"]["ask"])

    def test_ticked_keeps_the_defaults_and_then_checks_for_updates(self):
        win, asked, checks = self.window(answer=True)
        self.assertEqual(asked, [True])
        self.assertEqual(features(self.cfg), ALL_ON)
        self.wait(lambda: checks, 3)
        self.assertEqual(checks, [True])

    def test_the_installers_choice_is_not_asked_again(self):
        self.seed_file = self.seed(False)
        win, asked, checks = self.window(answer=None)
        self.app.processEvents()
        self.assertEqual(asked, [])
        self.assertEqual(features(self.cfg), ALL_OFF)
        self.assertEqual(Config(self.cfg.path).online["mode"], "offline")
        self.assertTrue(win.settings.radios["offline"].isChecked())
        self.assertFalse(win.about.updates.automatic.isChecked())

    def test_the_dialog_closes_with_the_choice_it_shows(self):
        from forza_launcher.ui.online_features import OnlineFeaturesDialog
        dialog = OnlineFeaturesDialog()
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.online.isChecked())
        dialog.reject()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)


if __name__ == "__main__":
    unittest.main()
