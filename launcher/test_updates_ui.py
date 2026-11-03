"""Qt integration for update state transitions and safe shutdown (no live downloads)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class UpdateUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.window import MainWindow
        from forza_launcher import updater, GITHUB_URL
        self.temp = tempfile.TemporaryDirectory()
        self.config_path_patch = patch("forza_launcher.paths.config_dir", return_value=Path(self.temp.name))
        self.config_path_patch.start()
        cfg = Config(Path(self.temp.name) / "launcher.json")
        cfg.gpu["checked"] = True
        cfg.loaded = True
        cfg.d["check_updates"] = False
        self.win = MainWindow(cfg)
        self.card = self.win.about.updates
        asset_name = ("ForzaHorizon1-ReImagined-Setup.exe" if sys.platform == "win32" else
                      "ForzaHorizon1-ReImagined-linux-x86_64.tar.gz")
        self.asset = updater.Asset(asset_name, GITHUB_URL, "0" * 64, 6)
        self.release = updater.Release("v99.0.0", (self.asset,))
        self.select = patch.object(updater.Release, "asset_for", return_value=self.asset)
        self.select.start()

    def tearDown(self):
        if self.card.task:
            self.card.cancel_task()
            self.wait_done()
        self.win.close()
        self.app.processEvents()
        self.select.stop()
        self.config_path_patch.stop()
        self.temp.cleanup()

    def wait_done(self):
        deadline = time.monotonic() + 5
        while self.card.busy() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        if self.card.busy():
            while self.card.busy() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.app.processEvents()
        self.assertFalse(self.card.busy(), "Update thread did not finish")

    def test_check_download_failure_retry_and_preference(self):
        from forza_launcher import updater
        with patch.object(updater, "check", return_value=self.release):
            self.card.check_button.click()
            self.assertFalse(self.card.check_button.isEnabled())
            self.wait_done()
        self.assertFalse(self.card.action.isHidden())
        self.assertFalse(self.win.update_notice.isHidden())
        with patch.object(updater, "download", side_effect=updater.UpdateError("Integrity check failed")):
            self.card.action.click()
            self.wait_done()
        self.assertIn("Integrity", self.card.message.text())
        self.assertIsNone(self.card.downloaded)
        dest = Path(self.temp.name) / self.asset.name
        with patch.object(updater, "download", return_value=dest):
            self.card.action.click()
            self.wait_done()
        self.assertEqual(self.card.downloaded, dest)
        self.assertIn("verified", self.card.message.text())
        self.assertEqual(self.card.action.text(), "Open installer" if sys.platform == "win32" else "Open download")
        self.card.automatic.setChecked(True)
        from forza_launcher.config import Config
        self.assertTrue(Config(self.win.cfg.path).d["check_updates"])

    def test_check_error_and_current_release(self):
        from forza_launcher import updater
        with patch.object(updater, "check", side_effect=updater.UpdateError("Offline. Try again.")):
            self.card.check()
            self.wait_done()
        self.assertIn("Offline", self.card.message.text())
        self.assertTrue(self.card.check_button.isEnabled())
        with patch.object(updater, "check", return_value=updater.Release("v0.0.0", ())):
            self.card.check()
            self.wait_done()
        self.assertIn("up to date", self.card.message.text())
        self.assertTrue(self.card.action.isHidden())
        self.assertTrue(self.win.update_notice.isHidden())

    def test_close_waits_for_worker(self):
        self.win.show()
        def work(task):
            while not task.isInterruptionRequested():
                time.sleep(.005)
            return None
        self.card._start(work, lambda result: None)
        self.win.close()
        self.assertTrue(self.win.isVisible())
        self.wait_done()
        self.app.processEvents()
        self.assertFalse(self.win.isVisible())

    def test_installer_is_blocked_during_game(self):
        self.card.downloaded = Path(self.temp.name) / "update.exe"
        with patch.object(self.win, "running", return_value=True), \
                patch("subprocess.Popen") as spawn:
            self.card.install()
            spawn.assert_not_called()
        self.assertIn("close the game", self.card.message.text())

    def test_declining_setup_quit_does_not_close_after_next_update(self):
        from PySide6.QtWidgets import QMessageBox
        self.win.show()
        def work(task):
            while not task.isInterruptionRequested():
                time.sleep(.005)
        with patch.object(self.win.setup, "busy", return_value=True), \
                patch.object(QMessageBox, "question", return_value=QMessageBox.No) as question:
            self.card._start(work, lambda result: None)
            self.win.close()
            self.wait_done()
            self.app.processEvents()
            self.assertTrue(self.win.isVisible())
            self.assertFalse(self.card.closing)
            self.assertEqual(question.call_count, 1)
            self.card._start(lambda task: None, lambda result: None)
            self.wait_done()
            self.app.processEvents()
            self.assertEqual(question.call_count, 1)
            self.assertTrue(self.win.isVisible())

    def test_linux_update_button_downloads_installs_and_closes(self):
        from forza_launcher import paths, updater, linux_update
        dest = Path(self.temp.name) / self.asset.name
        with patch("forza_launcher.ui.updates.sys.platform", "linux"), \
                patch.object(paths, "FROZEN", True), \
                patch.object(paths, "IN_FLATPAK", False), \
                patch.object(updater, "download", return_value=dest), \
                patch.object(linux_update, "install_archive") as install, \
                patch.object(self.win, "close") as close:
            self.card._checked(self.release)
            self.assertEqual(self.card.action.text(), "Update and restart")
            self.card.action.click()
            self.wait_done()
            self.wait_done()
            install.assert_called_once()
            self.assertEqual(install.call_args.args[:3], (dest, self.asset, self.release.tag))
            close.assert_called_once()
            self.assertFalse(self.card.installing)
            self.assertFalse(self.win.setup.isEnabled())  # stays disabled while handing off

    def test_flatpak_download_has_manual_instructions_without_host_execution(self):
        import shlex
        from forza_launcher import paths, updater
        from forza_launcher.ui.updates import QDesktopServices, QStandardPaths
        downloads = Path(self.temp.name) / "Player's Downloads"
        asset = updater.Asset("ForzaHorizon1-ReImagined-x86_64.flatpak", "https://example.test", "0" * 64, 6)
        dest = downloads / "ForzaHorizon1-ReImagined" / self.release.tag / asset.name
        with patch("forza_launcher.ui.updates.sys.platform", "linux"), \
                patch.object(paths, "IN_FLATPAK", True), \
                patch.object(paths, "FROZEN", True), \
                patch.object(updater.Release, "asset_for", return_value=asset), \
                patch.object(QStandardPaths, "writableLocation", return_value=str(downloads)), \
                patch.object(updater, "download", return_value=dest) as download, \
                patch("subprocess.Popen") as spawn, patch("subprocess.run") as run, \
                patch.object(self.win, "close") as close, \
                patch.object(QDesktopServices, "openUrl", return_value=False) as open_url:
            self.card._checked(self.release)
            self.assertEqual(self.card.action.text(), "Download update")
            self.card.action.click()
            self.wait_done()
            self.assertEqual(download.call_args.args[1], dest.parent)
            self.assertEqual(self.card.action.text(), "Open download folder")
            command = self.card.message.text().splitlines()[1]
            self.assertEqual(shlex.split(command), ["flatpak", "install", "--user", str(dest)])
            self.assertTrue(self.win.setup.isEnabled())
            self.assertFalse(self.card.install_requested)
            self.card.action.click()
            open_url.assert_called_once()
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), dest.parent)
            self.assertIn(command, self.card.message.text())  # folder errors retain installation instructions
            spawn.assert_not_called()
            run.assert_not_called()
            close.assert_not_called()

    def test_flatpak_download_failure_never_offers_installation(self):
        from forza_launcher import paths, updater
        from forza_launcher import game
        # game_version asks git for the build when a game is built in this checkout (a developer's PC,
        # not CI): keep that call out of the subprocess.run this test watches.
        with patch.object(paths, "IN_FLATPAK", True), \
                patch.object(game, "game_version", return_value="unknown build"), \
                patch.object(updater, "download", side_effect=updater.UpdateError("Integrity check failed")), \
                patch("subprocess.run") as run:
            self.card._checked(self.release)
            self.card.action.click()
            self.wait_done()
            self.assertIsNone(self.card.downloaded)
            self.assertIn("Integrity check failed", self.card.message.text())
            self.assertNotIn("flatpak install", self.card.message.text())
            self.assertFalse(self.card.installing)
            run.assert_not_called()

    def test_close_during_install_waits_for_install_completion(self):
        import threading
        release = threading.Event()
        self.win.show()
        self.card.installing = True
        self.card._start(lambda task: release.wait(3), lambda result: None)
        try:
            self.win.close()
            self.assertTrue(self.win.isVisible())
            self.assertFalse(self.card.closing)
            self.assertFalse(self.card.task.isInterruptionRequested())
        finally:
            release.set()
            self.wait_done()

    def test_long_manual_command_reserves_its_full_wrapped_height(self):
        self.win.resize(900, 620)
        self.win.show_page(3)
        self.win.show()
        self.app.processEvents()
        from forza_launcher import paths
        with patch.object(paths, "IN_FLATPAK", True):
            self.card._downloaded(Path("/home/player/Very long Downloads folder/ForzaHorizon1-ReImagined/"
                                       "v99.0.0/ForzaHorizon1-ReImagined-x86_64.flatpak"))
        for _ in range(3):
            self.app.processEvents()
        self.assertGreaterEqual(self.card.message.height(),
                                self.card.message.heightForWidth(self.card.message.width()))

    def test_source_checkout_opens_download_without_replacing_code(self):
        from forza_launcher import paths, linux_update
        from forza_launcher.ui.updates import QDesktopServices
        self.card.downloaded = Path(self.temp.name) / self.asset.name
        with patch("forza_launcher.ui.updates.sys.platform", "linux"), \
                patch.object(paths, "FROZEN", False), \
                patch.object(paths, "IN_FLATPAK", False), \
                patch.object(linux_update, "install_archive") as install, \
                patch.object(QDesktopServices, "openUrl", return_value=True) as open_url:
            self.card.install()
            install.assert_not_called()
            open_url.assert_called_once()

    def test_windows_installer_preserves_custom_install_folder(self):
        from forza_launcher import paths
        self.card.downloaded = Path(self.temp.name) / "update.exe"
        with patch("forza_launcher.ui.updates.sys.platform", "win32"), \
                patch.object(paths, "FROZEN", True), \
                patch.object(paths, "app_dir", return_value=Path("C:/Custom/Launcher")), \
                patch("subprocess.Popen") as spawn:
            self.card.install()
            spawn.assert_called_once_with([str(self.card.downloaded), "/DIR=" + str(Path("C:/Custom/Launcher"))])


if __name__ == "__main__":
    unittest.main()
