"""Qt flow with mocked discovery/writes, no Steam client or user config."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class SteamUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.ui.steam import SteamCard
        from forza_launcher.steam_shortcuts import Account
        self.card = SteamCard()
        self.accounts = [Account(Path("/fixture/Steam"), "123"), Account(Path("/fixture/Steam"), "456")]

    def tearDown(self):
        self.wait(lambda: not self.card.busy() and self.card.probe is None)
        self.card.close()
        self.card.deleteLater()
        self.app.processEvents()

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(condition())

    def test_choose_one_account_then_visible_completion(self):
        from forza_launcher.ui import steam
        with patch.object(steam.steam_shortcuts, "accounts", return_value=self.accounts), \
             patch.object(steam.QInputDialog, "getItem", return_value=(self.accounts[1].label, True)), \
             patch.object(steam.steam_shortcuts, "launcher_command", return_value="command"), \
             patch.object(steam.steam_shortcuts, "bundled_artwork", return_value={}), \
             patch.object(steam.steam_shortcuts, "add", return_value="Added successfully") as add:
            self.card.button.click()
            self.wait(lambda: self.card.probe is None and not self.card.busy())
            self.assertEqual(add.call_args.args, (self.accounts[1], "command", {}))
            self.assertEqual(self.card.message.text(), "Added successfully")
            self.assertTrue(self.card.button.isEnabled())

    def test_cancel_or_no_accounts_does_not_write(self):
        from forza_launcher.ui import steam
        for accounts in ([], self.accounts):
            with patch.object(steam.steam_shortcuts, "accounts", return_value=accounts), \
                 patch.object(steam.QInputDialog, "getItem", return_value=("", False)), \
                 patch.object(steam.steam_shortcuts, "add") as add:
                self.card.button.click()
                self.wait(lambda: self.card.probe is None)
                add.assert_not_called()
                self.assertTrue(self.card.button.isEnabled())

    def test_window_waits_for_write_and_error_stays_visible(self):
        from forza_launcher.ui import steam
        from forza_launcher.ui.window import MainWindow
        entered, release = threading.Event(), threading.Event()
        def write(*_args):
            entered.set()
            release.wait(5)
            raise steam.steam_shortcuts.SteamError("Exit Steam first.")
        with patch.object(steam.steam_shortcuts, "accounts", return_value=self.accounts[:1]), \
             patch.object(steam.steam_shortcuts, "launcher_command", return_value="command"), \
             patch.object(steam.steam_shortcuts, "bundled_artwork", return_value={}), \
             patch.object(steam.steam_shortcuts, "add", side_effect=write), \
             patch("forza_launcher.ui.window.QTimer.singleShot") as timer:
            self.card.button.click()
            self.wait(entered.is_set)
            proxy = SimpleNamespace(about=SimpleNamespace(steam=self.card), status=Mock(), close=Mock())
            event = Mock()
            try:
                MainWindow.closeEvent(proxy, event)
                event.ignore.assert_called_once()
                timer.assert_called_once_with(100, proxy.close)
                self.assertFalse(self.card.button.isEnabled())
            finally:
                release.set()
            self.wait(lambda: not self.card.busy())
            self.assertEqual(self.card.message.text(), "Exit Steam first.")

    def test_flatpak_opens_manual_guide_without_discovery_or_write(self):
        from forza_launcher.ui import steam
        with patch.object(steam.paths, "IN_FLATPAK", True), \
             patch.object(steam.QDesktopServices, "openUrl") as opened, \
             patch.object(steam.steam_shortcuts, "accounts") as accounts, \
             patch.object(steam.steam_shortcuts, "add") as add:
            self.card.button.click()
            opened.assert_called_once()
            self.assertEqual(opened.call_args.args[0].toString(), steam.GUIDE)
            accounts.assert_not_called()
            add.assert_not_called()

    def test_clock_notice_clears_when_auto_returns(self):
        from forza_launcher.config import Config
        from forza_launcher.deck_clock import Clock
        from forza_launcher.ui.pages import PlayPage
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            page = PlayPage(Config(Path(temp) / "launcher.json"),
                            SimpleNamespace(stop_game=Mock(), review_crash_report=Mock()))
            try:
                page._clock_probe = Mock()
                page._clock_result(Clock("manual", 200))
                self.assertFalse(page.clock_notice.isHidden())
                self.assertFalse(page.clock_guide.isHidden())
                self.assertNotIn("200", page.clock_notice.text())
                page._clock_probe = Mock()
                page._clock_result(Clock("auto", 200))
                self.assertTrue(page.clock_notice.isHidden())
                self.assertTrue(page.clock_guide.isHidden())
            finally:
                page.close()
                page.deleteLater()


if __name__ == "__main__":
    unittest.main()
