"""Exercise the actual Play page warning and its Setup action without launching a game."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from forza_launcher import config, game, tuning_status
from forza_launcher.ui.pages import PlayPage


class TuningPlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = config.Config(self.root / "launcher.json")
        self.cfg.advanced.update(data_root=str(self.root), game_dir=str(self.root))
        self.cfg.d["game_root"] = str(self.root)
        (self.root / "default.xex").touch()
        self.install = game.GameInstall(self.root, self.root / game.EXE)
        self.window = mock.Mock(_crash_probe=None, _dlss_task=None, saves=None)
        self.window.running.return_value = False
        self.page = PlayPage(self.cfg, self.window)
        self.addCleanup(self.close_page)
        for name, value in (("find_game", (self.install, [])), ("installed_dlc", 0),
                            ("game_version", "test build")):
            p = mock.patch.object(game, name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        for name, value in (("installer.check_game_folder", (True, "Ready")),
                            ("builder.build_status", ("ready", "Ready"))):
            p = mock.patch("forza_launcher.ui.pages." + name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.dict(game.os.environ, {}, clear=True)
        p.start(); self.addCleanup(p.stop)

    def close_page(self):
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def refresh(self):
        self.page.refresh()
        self.page.show()
        self.app.processEvents()

    def test_missing_assets_warn_and_setup_action_works_without_blocking_play(self):
        self.cfg.settings["forza_car_tuning"] = True
        self.refresh()
        self.assertFalse(self.page.tuning_notice.isHidden())
        self.assertIn("Title Update 4", self.page.tuning_notice.text())
        self.assertIn("Build", self.page.tuning_setup.text())
        setup = mock.Mock()
        self.page.goto_setup.connect(setup)
        self.page.tuning_setup.click()
        setup.assert_called_once_with()
        self.assertEqual(self.page.mode, "play")
        self.assertTrue(self.page.play.isEnabled())

    def test_warning_updates_after_build_and_when_disabled_or_overridden(self):
        self.cfg.settings["forza_car_tuning"] = True
        self.refresh()
        self.assertFalse(self.page.tuning_notice.isHidden())
        (self.root / "update-native").mkdir()
        self.refresh()
        self.assertTrue(self.page.tuning_notice.isHidden())
        self.assertTrue(self.page.tuning_setup.isHidden())
        (self.root / "update-native").rmdir()
        self.cfg.settings["forza_car_tuning"] = False
        self.refresh()
        self.assertTrue(self.page.tuning_notice.isHidden())
        self.cfg.settings["forza_car_tuning"] = True
        self.cfg.advanced["extra_args"] = "--no-forza_car_tuning"
        self.refresh()
        self.assertTrue(self.page.tuning_notice.isHidden())

    def test_explicit_update_root_does_not_offer_an_ineffective_managed_rebuild(self):
        self.cfg.settings["forza_car_tuning"] = True
        external = self.root / "external-prepared"
        external.mkdir()
        self.assertFalse((self.install.dir / "update-native").exists())
        for flag in (f"--update_data_root={external}", f'--update_data_root "{external}"'):
            with self.subTest(flag=flag):
                self.cfg.advanced["extra_args"] = flag
                self.refresh()
                self.assertTrue(self.page.tuning_notice.isHidden())
                self.assertTrue(self.page.tuning_setup.isHidden())

    def test_persisted_reason_is_shown_and_clears_after_success(self):
        self.cfg.settings["forza_car_tuning"] = True
        tuning_status.write(self.root, tuning_status.failure(
            "missing_tools", "This launcher is missing the tuning preparation tool.",
            "Reinstall the current launcher release, then Build with Title Update 4 selected."))
        self.refresh()
        self.assertIn("missing the tuning preparation tool", self.page.tuning_notice.text())
        self.assertIn("Reinstall", self.page.tuning_notice.text())
        self.assertTrue(self.page.play.isEnabled())
        # Disk status is read again by a fresh page, not retained only in memory.
        another = PlayPage(self.cfg, self.window)
        try:
            another.refresh()
            self.assertIn("missing the tuning preparation tool", another.tuning_notice.text())
        finally:
            another.close()
            another.deleteLater()
        (self.root / "update-native").mkdir()
        self.refresh()
        self.assertTrue(self.page.tuning_notice.isHidden())

    def test_invalid_status_falls_back_to_safe_help(self):
        self.cfg.settings["forza_car_tuning"] = True
        (self.root / tuning_status.FILE).write_text('{"schema":1,"reason":[]}', encoding="utf-8")
        self.refresh()
        self.assertIn("Title Update 4", self.page.tuning_notice.text())
        self.assertTrue(self.page.play.isEnabled())


if __name__ == "__main__":
    unittest.main()
