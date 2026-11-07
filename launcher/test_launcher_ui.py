"""Launcher UI behavior: details, failure visibility, and setting dependencies."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtCore import QCoreApplication, QEvent, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class LauncherUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher import build_env
        from forza_launcher.ui.window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.loaded = True
        self.cfg.advanced["data_root"] = str(self.root)
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        self.cfg.d["check_updates"] = False
        self.log_patch = patch("forza_launcher.paths.config_dir", return_value=self.root)
        self.find_patch = patch("forza_launcher.game.find_game", return_value=(None, []))
        self.tools_patch = patch("forza_launcher.build_env._detect_tools", return_value=build_env.Tools())
        self.log_patch.start()
        self.find_patch.start()
        self.tools_patch.start()
        self.window = MainWindow(self.cfg)
        self.wait_for(lambda: not self.window.setup._probes)

    def tearDown(self):
        self.window.close()
        self.wait_for(lambda: not self.window.setup._probes)
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.find_patch.stop()
        self.tools_patch.stop()
        self.log_patch.stop()
        self.temp.cleanup()

    def wait_for(self, predicate):
        until = time.monotonic() + 4
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def test_co_op_results_are_listed_after_the_session(self):
        """#309: what the co-op results thread added, and what waits, under "The game closed."
        once the thread is done (it runs after the game, in the background)."""
        win = self.window
        release = threading.Event()
        win._coop_notes = ["Co-op results: FR69 at place 8 (was not raced): +70 XP, +2,100 CR",
                           "Co-op results: FR05 (8th in DriverA's world) waits: ..."]
        win._coop_thread = threading.Thread(target=release.wait, daemon=True)
        win._coop_thread.start()
        win.play.show_message("The game closed.")
        win._coop_timer.start()
        self.app.processEvents()
        self.assertEqual(win.play.msg.text(), "The game closed.")  # still applying
        release.set()
        self.wait_for(lambda: not win._coop_timer.isActive())
        self.assertEqual(win.play.msg.text().splitlines(), [
            "The game closed.", "Co-op results: FR69 at place 8 (was not raced): +70 XP, +2,100 CR",
            "Co-op results: FR05 (8th in DriverA's world) waits: ..."])

    def test_catalog_recovery_warning_stays_visible_after_successful_start(self):
        from forza_launcher import game, catalog_import, ugc
        plan = game.LaunchPlan(game.GameInstall(self.root, Path('forza.exe')), self.root,
                              ['forza'], {}, [], self.root/'forza.toml', '', log_dir=self.root)
        win = self.window
        self.wait_for(lambda: not win.saves.busy() and not win._busy)
        with patch.object(catalog_import, 'recover', side_effect=ugc.CatalogError('Invalid import journal')), \
                patch('subprocess.Popen') as spawn:
            spawn.return_value.poll.return_value = None
            try:
                win._start_plan(plan)
                self.assertIsNotNone(win.session)
                self.assertIn('The game is running', win.play.warn.text())
                self.assertIn('Invalid import journal', win.play.warn.text())
                self.assertIn('save-backups', win.play.warn.text())
                self.assertTrue(win.play._keep_warn)
                win.play.refresh()
                self.assertFalse(win.play.warn.isHidden())
            finally:
                if win.session:
                    win.session.cleanup()
                win.session = None
                win.poll.stop()

    def test_crashing_child_offers_review_and_preserves_report_after_log_changes(self):
        import sys
        from forza_launcher import crash_report, game
        from test_crash_report import FAULT, CHAIN
        log = self.root / "fixture-crash.log"
        (self.root / "version.txt").write_text("fixture-game")
        script = "from pathlib import Path; import sys; Path(sys.argv[1]).write_text(sys.argv[2]); sys.exit(134)"
        plan = game.LaunchPlan(game.GameInstall(self.root, Path(sys.executable)), self.root,
                              [sys.executable, "-c", script, str(log), FAULT + "\n" + CHAIN,
                               f"--log_file={log}"], {}, [], self.root / "forza.toml", "frame_rate = 120")
        win = self.window
        self.wait_for(lambda: not win.saves.busy() and not win._busy)
        with patch.object(game, "find_game", return_value=(plan.game, [])), \
                patch("forza_launcher.installer.check_game_folder", return_value=(True, "Ready")), \
                patch("forza_launcher.dlss.menu_runtime_enabled", return_value=False), \
                patch.object(game, "build_plan", return_value=plan):
            win.start_game()
            self.assertIsNotNone(win.session)
            self.wait_for(lambda: win.session.poll() is not None)
            win._poll()
            self.wait_for(lambda: win._crash_probe is None)
        self.assertIsNone(win.session)
        self.assertFalse(win.play.crash_report.isHidden())
        self.assertIn("82056780", win._crash_report.body)
        log.write_text("Another run replaced the log")
        win.review_crash_report()
        dialog = win._crash_dialog
        self.assertIn("82056780", dialog.body.toPlainText())
        dialog.body.setPlainText("My reviewed report")
        dialog.reject()
        win.review_crash_report()
        self.assertEqual(win._crash_dialog.body.toPlainText(), "My reviewed report")
        win._crash_dialog.reject()

    def test_slow_crash_collection_keeps_ui_responsive_and_prevents_log_overwrite(self):
        from forza_launcher import crash_report, game
        from PySide6.QtCore import QTimer
        entered, release = threading.Event(), threading.Event()
        plan = game.LaunchPlan(None, self.root, ["forza"], {}, [], self.root / "t.toml", "")

        def collect(*_args):
            entered.set()
            release.wait(3)
            return crash_report.CrashReport("Fixture", "Fixture report", None)

        with patch.object(crash_report, "collect", side_effect=collect):
            try:
                self.window._prepare_crash_report(plan, 134)
                self.window.play.refresh()
                self.assertTrue(entered.wait(1))
                self.assertFalse(self.window.play.play.isEnabled())
                self.window.start_game()
                self.assertIsNone(self.window.session)
                self.window.show_page(2)
                heartbeat = []
                QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.wait_for(lambda: bool(heartbeat))
                self.assertFalse(release.is_set())
                self.window.close()
                self.assertFalse(self.window.isVisible())
            finally:
                release.set()
                self.wait_for(lambda: self.window._crash_probe is None)

    def test_slow_tool_probe_does_not_block_navigation_or_event_loop(self):
        from forza_launcher import build_env
        entered, release = threading.Event(), threading.Event()
        build_env.forget_tools()
        self.window.setup._probe_done.clear()

        def slow_probe(root, prefer_downloaded=False):
            entered.set()
            release.wait(3)
            return build_env.Tools()

        with patch.object(build_env, '_detect_tools', side_effect=slow_probe):
            self.window.show()
            self.window.show_page(4)
            self.wait_for(lambda: self.window.gallery.task is None)
            try:
                start = time.monotonic()
                self.window.show_page(1)
                self.assertLess(time.monotonic() - start, .25)
                self.assertTrue(entered.wait(1))
                self.window.show_page(2)
                heartbeat = []
                QTimer.singleShot(0, lambda: heartbeat.append(True))
                self.wait_for(lambda: bool(heartbeat))
                self.assertIs(self.window.stack.currentWidget(), self.window.settings)
                self.assertFalse(release.is_set())
                self.window.close()
                self.assertFalse(self.window.isVisible())
            finally:
                release.set()
                self.wait_for(lambda: not self.window.setup._probes)
            self.wait_for(lambda: not self.window.isVisible())

    def test_tool_probe_discards_results_after_data_folder_changes(self):
        from forza_launcher import build_env
        entered, release = threading.Event(), threading.Event()
        roots = []
        build_env.forget_tools()
        self.window.setup._probe_done.clear()

        def probe(root, prefer_downloaded=False):
            roots.append(root)
            result = build_env.Tools()
            if len(roots) == 1:
                entered.set()
                release.wait(3)
                result.source['compiler'] = 'obsolete result'
            else:
                result.source['compiler'] = 'new folder result'
            return result

        with patch.object(build_env, '_detect_tools', side_effect=probe):
            self.window.show()
            try:
                self.window.show_page(1)
                self.assertTrue(entered.wait(1))
                new_root = self.root / 'new-data'
                self.cfg.advanced['data_root'] = str(new_root)
                self.window.setup.refresh()
            finally:
                release.set()
                self.wait_for(lambda: not self.window.setup._probes)
            self.assertEqual(roots, [self.root, new_root])
            self.assertIn('new folder result', self.window.setup.tools_msg.text())
            self.assertNotIn('obsolete', self.window.setup.tools_msg.text())

    def test_play_keeps_paths_in_details_and_preserves_disclosure_state(self):
        from forza_launcher import game
        game_root = self.cfg.game_files()
        game_root.mkdir(parents=True)
        (game_root / "default.xex").write_bytes(b"test fixture")
        install = game.GameInstall(self.root / "runtime", self.root / "runtime/forza.exe")
        with patch("forza_launcher.game.find_game", return_value=(install, [])), \
                patch("forza_launcher.installer.check_game_folder", return_value=(True, "Valid")), \
                patch("forza_launcher.builder.build_status", return_value=("built", "Ready")):
            page = self.window.play
            page.refresh()
            self.assertEqual(page.play.text(), "Play")
            self.assertEqual(page.r_files.detail.text(), "Ready")
            self.assertNotIn(str(self.root), page.r_build.detail.text())
            self.assertIn(str(install.exe), page.install_paths.text())
            self.assertFalse(page.install_details.toggle.isChecked())
            page.install_details.toggle.click()
            page.refresh()
            self.assertTrue(page.install_details.toggle.isChecked())

    def test_failed_build_reveals_log_without_losing_error(self):
        page = self.window.setup
        self.assertFalse(page.build_details.toggle.isChecked())
        page.logview.appendPlainText("Compiler diagnostics retained here")
        page.task = Mock(blocked=False)
        page.task.isRunning.return_value = False
        page._bar, page._cancel_btn, page._msg = page.bar3, page.cancel3, page.status3
        with patch.object(QMessageBox, "warning"):
            page._finished(False, "Build failed")
        self.assertTrue(page.build_details.toggle.isChecked())
        self.assertEqual(page.tabs.currentIndex(), 2)
        self.assertEqual(page.stack.currentIndex(), 2)
        self.assertIn("Compiler diagnostics", page.logview.toPlainText())
        self.assertIn("Build failed", page.status3.text())
        page.build_details.set_expanded(False)
        page.task = Mock(blocked=False)
        page.task.isRunning.return_value = False
        page._finished(False, "Cancelled.")
        self.assertFalse(page.build_details.toggle.isChecked())

    def test_graphics_dependency_reasons_follow_selected_upscaler(self):
        from forza_launcher.paths import IS_WINDOWS
        page = self.window.settings
        # Linux has a fixed Vulkan backend and no Graphics API selector.
        self.assertEqual("gpu_backend" in page.rows, IS_WINDOWS)
        backend_keys = ("gpu_backend",) if IS_WINDOWS else ()
        self.cfg.settings.update(fh1_upscaler="quality", forza_ultrawide=False)
        page._apply_dependencies()
        for key in ("resolution", "resolution_scale", "present_effect") + backend_keys:
            self.assertFalse(page.rows[key].control.isEnabled())
            self.assertIn("NVIDIA", page.rows[key].dependency.text())
        self.cfg.settings.update(fh1_upscaler="off", present_effect="fsr3")
        page._apply_dependencies()
        self.assertTrue(page.rows["fsr3_mode"].control.isEnabled())
        self.assertIn("AMD FSR 3", page.rows["resolution"].dependency.text())
        self.cfg.settings.update(present_effect="fsr", forza_ultrawide=True)
        page._apply_dependencies()
        for key in ("resolution_scale",) + backend_keys:
            self.assertFalse(page.rows[key].control.isEnabled())
            self.assertIn("Ultrawide", page.rows[key].dependency.text())
        self.cfg.settings["forza_ultrawide"] = False
        page._apply_dependencies()
        for key in ("resolution_scale",) + backend_keys:
            self.assertTrue(page.rows[key].control.isEnabled())
            self.assertEqual(page.rows[key].dependency.text(), "")

    def test_settings_tabs_preserve_values_and_selected_page_after_rebuild(self):
        page = self.window.settings
        page.set_value("vsync", False)
        for i in range(page.tabs.count()):
            page.tabs.setCurrentIndex(i)
            self.assertEqual(page.stack.currentIndex(), i)
        page.rebuild()
        self.assertEqual(page.stack.currentIndex(), page.tabs.count() - 1)
        page.tabs.setCurrentIndex(0)
        self.assertFalse(page.rows["vsync"].control.isChecked())

    def test_setup_tasks_reveal_their_page_without_resetting_inputs(self):
        page = self.window.setup
        page.pkg.setText("my disc.iso")
        page.dlc.setText("my dlc")
        for bar, button, msg, expected in (
                (page.bar2, None, page.msg2, 1),
                (page.bar3, page.cancel3, page.status3, 2),
                (page.bar1, page.cancel1, page.msg1, 0)):
            task = Mock()
            page._start(task, bar, button, msg)
            self.assertEqual(page.stack.currentIndex(), expected)
            task.start.assert_called_once()
            page.task = None
        self.assertEqual(page.pkg.text(), "my disc.iso")
        self.assertEqual(page.dlc.text(), "my dlc")
        self.window.set_busy(False)


if __name__ == "__main__":
    unittest.main()
