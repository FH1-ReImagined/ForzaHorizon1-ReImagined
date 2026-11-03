"""Play exposes game and launcher diagnostics in their distinct folders."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QPushButton
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class PlayLogFolderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_buttons_open_distinct_folders_including_custom_game_data(self):
        from forza_launcher.config import Config
        from forza_launcher.ui.pages import PlayPage

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = Config(root / "launcher.json")
            cfg.advanced["data_root"] = str(root / "custom game data")
            launcher_log = root / "launcher config" / "launcher.log"
            window = SimpleNamespace(stop_game=Mock(), review_crash_report=Mock())
            page = PlayPage(cfg, window)
            try:
                buttons = {button.text(): button for button in page.findChildren(QPushButton)}
                with patch("forza_launcher.ui.pages.paths.launcher_log", return_value=launcher_log), \
                     patch("forza_launcher.ui.pages.QDesktopServices.openUrl", return_value=True) as opened:
                    buttons["Open log folder"].click()
                    buttons["Open launcher log folder"].click()
                self.assertEqual([Path(call.args[0].toLocalFile()) for call in opened.call_args_list],
                                 [cfg.data_home(), launcher_log.parent])
                self.assertTrue(cfg.data_home().is_dir())
                self.assertTrue(launcher_log.parent.is_dir())
                self.assertNotEqual(cfg.data_home(), launcher_log.parent)
            finally:
                page.close()
                page.deleteLater()

    def test_start_failure_names_launcher_log_and_preserves_warning(self):
        from forza_launcher.ui.window import MainWindow

        window = SimpleNamespace(_last_start_error="", play=Mock())
        log = Path("separate config") / "launcher.log"
        with patch("forza_launcher.ui.window.paths.launcher_log", return_value=log), \
             patch("forza_launcher.ui.window.paths.log_error") as logged:
            MainWindow._start_failed(window, RuntimeError("test launch failure"))
        logged.assert_called_once()
        window.play.show_message.assert_called_once()
        call = window.play.show_message.call_args
        self.assertIn("Game could not start", call.args[0])
        self.assertIn("test launch failure", call.args[0])
        self.assertIn(f"Launcher log: {log}", call.args[0])
        self.assertIn("Next:", call.args[0])
        self.assertEqual(call.args[1], True)
        self.assertEqual(call.kwargs, {"keep": True})


if __name__ == "__main__":
    unittest.main()
