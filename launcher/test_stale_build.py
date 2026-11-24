"""Play and the FM4 imports with a game built by an older launcher (#976): a clear choice on Play
(Build now / Play anyway), a notice after an import, nothing for a fresh or missing build."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.pop("WAYLAND_DISPLAY", None)
os.environ.pop("DISPLAY", None)
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:
    QApplication = None

class StopStart(Exception):
    pass


STALE = ("stale", "A new version of the project needs a rebuild.")


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class StaleBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher import build_env, game
        from forza_launcher.ui.window import MainWindow
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.loaded = True
        self.cfg.advanced["data_root"] = str(self.root)
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        self.cfg.d["check_updates"] = False
        (self.root / "version.txt").write_text("fixture-game")
        self.install = game.GameInstall(self.root, Path(sys.executable))
        self.patches = [
            patch("forza_launcher.paths.config_dir", return_value=self.root),
            patch("forza_launcher.game.find_game", return_value=(self.install, [])),
            patch("forza_launcher.game.outside_build", return_value=None),
            patch("forza_launcher.build_env._detect_tools", return_value=build_env.Tools()),
            patch("forza_launcher.installer.check_game_folder", return_value=(True, "Ready")),
            patch("forza_launcher.builder.cpu_blocker", return_value=""),
            patch("forza_launcher.dlss.menu_runtime_enabled", return_value=False),
        ]
        for p in self.patches:
            p.start()
        self.window = MainWindow(self.cfg)
        self.wait_for(lambda: not self.window.setup._probes)
        self.wait_for(lambda: not self.window.saves.busy() and not self.window._busy)

    def tearDown(self):
        self.window.close()
        self.wait_for(lambda: not self.window.setup._probes)
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def wait_for(self, predicate):
        until = time.monotonic() + 4
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.01)

    def play(self, status, choice):
        """Press Play with a build status; returns (times asked, whether the game plan was built)."""
        win = self.window
        asked = []

        def ask(detail):
            asked.append(detail)
            return choice
        with patch("forza_launcher.builder.build_status", return_value=status), \
                patch.object(win, "_stale_build_choice", side_effect=ask), \
                patch.object(win.settings, "flush", side_effect=StopStart) as flush:  # first step after the checks
            try:
                win.start_game()
            except StopStart:
                pass
        self.assertIsNone(win.session)
        return asked, flush.called

    def test_stale_build_asks_and_build_now_opens_setup(self):
        self.window.show_page(0)
        asked, started = self.play(STALE, "build")
        self.assertEqual(asked, [STALE[1]])
        self.assertFalse(started)
        self.assertIs(self.window.stack.currentWidget(), self.window.setup)

    def test_stale_build_play_anyway_continues(self):
        asked, started = self.play(STALE, "play")
        self.assertEqual(len(asked), 1)
        self.assertTrue(started)

    def test_stale_build_closed_dialog_does_not_start(self):
        asked, started = self.play(STALE, "")
        self.assertEqual(len(asked), 1)
        self.assertFalse(started)

    def test_fresh_and_missing_builds_do_not_ask(self):
        for status in (("built", "Built today"), ("none", "The game has not been built yet."),
                       ("partial", "x"), ("nosource", "x")):
            asked, started = self.play(status, "build")
            self.assertEqual(asked, [], status)
            self.assertTrue(started, status)

    def test_after_the_dialog_or_a_download_it_is_not_asked_again(self):
        win = self.window
        with patch("forza_launcher.builder.build_status", return_value=STALE), \
                patch.object(win, "_stale_build_choice") as ask, \
                patch.object(win.settings, "flush", side_effect=StopStart):
            try:
                win.start_game(stale_ok=True)
            except StopStart:
                pass
        ask.assert_not_called()

    def test_outside_build_does_not_ask(self):
        with patch("forza_launcher.game.outside_build", return_value=self.install):
            asked, started = self.play(STALE, "build")
        self.assertEqual(asked, [])
        self.assertTrue(started)

    def test_dialog_has_both_choices(self):
        win = self.window
        seen = {}

        def fake_exec(box):
            seen["buttons"] = [b.text() for b in box.buttons()]
            seen["text"] = box.informativeText()
            seen["pick"] = box.buttons()[1]
            box._pick = seen["pick"]
        with patch.object(QMessageBox, "exec", fake_exec), \
                patch.object(QMessageBox, "clickedButton", lambda box: box._pick):
            result = win._stale_build_choice(STALE[1])
        self.assertEqual(seen["buttons"], ["Build now", "Play anyway"])
        self.assertIn("Setup > Build", seen["text"])
        self.assertEqual(result, "play")

    def test_fm4_imports_say_the_build_is_old(self):
        from forza_launcher import builder
        mods = self.window.mods
        for status, noted in ((STALE, True), (("built", "x"), False), (("none", "x"), False)):
            with patch("forza_launcher.builder.build_status", return_value=status):
                mods.fm4_task = type("T", (), {"wait": lambda s: None, "deleteLater": lambda s: None})()
                mods._fm4_done(True, "Imported 3 cars.")
                cars = mods.fm4_message.text()
                card = mods.fm4_tracks
                card.task = type("T", (), {"wait": lambda s: None, "deleteLater": lambda s: None})()
                card._build_done(True, "Imported 1 circuit.")
                tracks = card.message.text()
            self.assertEqual(builder.STALE_IMPORT_NOTE in cars, noted, status)
            self.assertEqual(builder.STALE_IMPORT_NOTE in tracks, noted, status)
            self.assertIn("Imported", cars)
            self.assertIn("Imported", tracks)

    def test_failed_import_gets_no_build_note(self):
        from forza_launcher import builder
        mods = self.window.mods
        with patch("forza_launcher.builder.build_status", return_value=STALE):
            mods.fm4_task = type("T", (), {"wait": lambda s: None, "deleteLater": lambda s: None})()
            mods._fm4_done(False, "Import FM4 cars stopped: x")
        self.assertNotIn(builder.STALE_IMPORT_NOTE, mods.fm4_message.text())


if __name__ == "__main__":
    unittest.main()
