"""Play in VR in the launcher (#333): the button, the checklist, the arguments, the VR tab."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class PlayInVrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from forza_launcher.config import Config
        from forza_launcher import build_env, vr
        from forza_launcher.ui.window import MainWindow
        self.vr = vr
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "game").mkdir()
        (self.root / "game" / "default.xex").write_text("x")
        self.cfg = Config(self.root / "launcher.json")
        self.cfg.loaded = True
        self.cfg.advanced["data_root"] = str(self.root)
        self.cfg.d["game_root"] = str(self.root / "game")
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        self.cfg.d["check_updates"] = False
        self.patches = [
            patch("forza_launcher.paths.config_dir", return_value=self.root),
            patch("forza_launcher.game.find_game", return_value=(SimpleNamespace(dir=self.root / "bin", exe=self.root / "bin" / "forza", lib_dirs=[]), [])),
            patch("forza_launcher.game.game_version", return_value="test"),
            patch("forza_launcher.game.native_tuning_needs_build", return_value=False),
            patch("forza_launcher.installer.check_game_folder", return_value=(True, "Ready")),
            patch("forza_launcher.builder.build_status", return_value=("built", "")),
            # These tests exercise Play's VR choice, not the NVIDIA runtime download.
            patch("forza_launcher.dlss.menu_runtime_enabled", return_value=False),
            patch("forza_launcher.build_env._detect_tools", return_value=build_env.Tools()),
        ]
        for p in self.patches:
            p.start()
        self.runtime = patch("forza_launcher.vr.detect", return_value=None)
        self.detect = self.runtime.start()
        self.window = MainWindow(self.cfg)
        self.wait_for(lambda: not self.window.setup._probes)

    def tearDown(self):
        self.window.close()
        self.wait_for(lambda: not self.window.setup._probes)
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.runtime.stop()
        for p in self.patches:
            p.stop()
        self.temp.cleanup()

    def wait_for(self, predicate):
        until = time.monotonic() + 4
        while not predicate() and time.monotonic() < until:
            self.app.processEvents()

    def refresh(self):
        self.window.play.refresh()
        self.app.processEvents()
        return self.window.play

    def test_button_needs_a_runtime(self):
        play = self.refresh()
        self.assertEqual(play.mode, "play")
        self.assertTrue(play.play.isEnabled())
        self.assertTrue(play.play_vr.isHidden())
        self.detect.return_value = self.vr.Runtime("WiVRn", "/x.json")
        play = self.refresh()
        self.assertFalse(play.play_vr.isHidden())
        self.assertEqual(play.play_vr.text(), "Play in VR")

    def test_play_is_flat_and_play_in_vr_is_not(self):
        calls = []
        with patch("forza_launcher.game.build_plan", side_effect=lambda *a, **k: calls.append(k.get("vr")) or
                   (_ for _ in ()).throw(RuntimeError("stop"))):
            self.window.start_game(False)
            self.cfg.d["vr"]["checklist_hidden"] = True
            self.window.play_in_vr()
        self.assertEqual(calls, [False, True])

    def test_vr_choice_survives_dlss_preparation(self):
        calls = []
        self.cfg.d["vr"]["checklist_hidden"] = True
        with patch("forza_launcher.dlss.menu_runtime_enabled", return_value=True), \
                patch.object(self.window, "_prepare_dlss") as prepare, \
                patch("forza_launcher.game.build_plan", side_effect=lambda *a, **k: calls.append(k.get("vr")) or
                      (_ for _ in ()).throw(RuntimeError("stop"))):
            for vr in (False, True):
                self.window.start_game(vr)
                self.assertEqual(calls, [] if not vr else [False])
                # The download's completion resumes start_game without a new choice.
                self.window._dlss_prepared = (self.cfg.data_home(), self.root / "bin",
                                              self.cfg.settings.get("fh1_upscaler"))
                self.window.start_game()
        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(calls, [False, True])

    def test_checklist_dont_show_again(self):
        from forza_launcher.ui.window import VrChecklist
        started = []
        self.window.start_game = lambda vr=None: started.append(vr)
        shown = []

        def answer(accept, tick):
            def exec_(dialog):
                shown.append(1)
                dialog.dont_show.setChecked(tick)
                return accept
            return exec_
        with patch.object(VrChecklist, "exec", answer(False, True)):
            self.window.play_in_vr()          # cancelled: nothing starts, nothing remembered
        self.assertEqual((started, self.cfg.d["vr"]["checklist_hidden"]), ([], False))
        with patch.object(VrChecklist, "exec", answer(True, False)):
            self.window.play_in_vr()
        self.assertEqual((started, self.cfg.d["vr"]["checklist_hidden"]), ([True], False))
        with patch.object(VrChecklist, "exec", answer(True, True)):
            self.window.play_in_vr()
        self.assertTrue(self.cfg.d["vr"]["checklist_hidden"])
        with patch.object(VrChecklist, "exec", answer(True, False)):
            self.window.play_in_vr()          # not shown again
        self.assertEqual((len(shown), started), (3, [True, True, True]))

    def test_checklist_steps(self):
        from forza_launcher.ui.window import VrChecklist
        text = " ".join(w.text() for w in VrChecklist(self.window).findChildren(type(self.window.play.msg)))
        for word in ("WiVRn", "SteamVR", "Put the headset on", "Press Play in VR"):
            self.assertIn(word, text)

    def test_vr_tab(self):
        from forza_launcher import schema
        self.assertIn("VR", schema.GROUPS)
        settings = self.window.settings
        self.assertIn("vr_mode", settings.rows)
        self.assertIn("No VR app found", settings.vr_status.text())
        self.assertIn("WiVRn", settings.vr_status.text())
        self.assertNotIn("vr_mode", [r for r in self.window.settings.rows if schema.BY_KEY[r].group == "Display"])
        self.detect.return_value = self.vr.Runtime("WiVRn", "/x.json")
        settings.rebuild()
        self.assertIn("✓ WiVRn found", settings.vr_status.text())


if __name__ == "__main__":
    unittest.main()
