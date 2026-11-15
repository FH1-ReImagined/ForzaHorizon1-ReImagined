"""The system's file dialogs in place of the launcher's own (#884): the setting's default, the
Qt attribute at start and when the switch changes, and the Flatpak, which keeps the launcher's."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication, QLabel
except ImportError:
    QApplication = None

from forza_launcher import paths  # noqa: E402
from forza_launcher.config import Config  # noqa: E402


def own_dialogs():
    """Qt opens its own file dialogs (the launcher's), never the system's."""
    return QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class FileDialogsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Config(Path(self.temp.name) / "launcher.json")
        self.cfg.advanced["data_root"] = self.temp.name
        before = own_dialogs()
        self.addCleanup(lambda: QCoreApplication.setAttribute(
            Qt.ApplicationAttribute.AA_DontUseNativeDialogs, before))

    def flatpak(self, inside=True):
        patch = mock.patch.object(paths, "IN_FLATPAK", inside)
        patch.start()
        self.addCleanup(patch.stop)


class Setting(FileDialogsCase):
    def test_off_by_default_and_saved_in_the_launchers_own_file(self):
        from forza_launcher.config import render_toml
        from forza_launcher.ui import file_dialogs
        self.assertIs(self.cfg.advanced[file_dialogs.KEY], False)
        self.assertFalse(file_dialogs.system(self.cfg))
        self.cfg.advanced[file_dialogs.KEY] = True
        self.cfg.save()
        self.assertIs(Config(self.cfg.path).advanced[file_dialogs.KEY], True)
        self.assertNotIn(file_dialogs.KEY, render_toml(self.cfg))   # not a game setting

    def test_a_launcher_json_from_before_keeps_the_launchers_own(self):
        from forza_launcher.ui import file_dialogs
        self.cfg.save()
        data = json.loads(self.cfg.path.read_text(encoding="utf-8"))
        del data["advanced"][file_dialogs.KEY]
        self.cfg.path.write_text(json.dumps(data), encoding="utf-8")
        old = Config(self.cfg.path)
        self.assertIs(old.advanced[file_dialogs.KEY], False)
        file_dialogs.apply(old)
        self.assertTrue(own_dialogs())

    def test_the_attribute_follows_the_setting(self):
        from forza_launcher.ui import file_dialogs
        self.flatpak(False)
        file_dialogs.apply(self.cfg)
        self.assertTrue(own_dialogs())
        self.cfg.advanced[file_dialogs.KEY] = True
        file_dialogs.apply(self.cfg)
        self.assertFalse(own_dialogs())
        self.cfg.advanced[file_dialogs.KEY] = False
        file_dialogs.apply(self.cfg)
        self.assertTrue(own_dialogs())

    def test_the_flatpak_keeps_the_launchers_own(self):
        from forza_launcher.ui import file_dialogs
        self.flatpak()
        self.cfg.advanced[file_dialogs.KEY] = True
        self.assertFalse(file_dialogs.available())
        self.assertFalse(file_dialogs.system(self.cfg))
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, False)
        file_dialogs.apply(self.cfg)
        self.assertTrue(own_dialogs())
        self.assertIs(self.cfg.advanced[file_dialogs.KEY], True)   # the choice itself is kept

    def test_the_label_is_no_other_settings(self):
        from forza_launcher import schema
        from forza_launcher.ui import file_dialogs
        self.assertNotIn(file_dialogs.LABEL.lower(), {s.label.lower() for s in schema.S})
        self.assertNotIn(file_dialogs.KEY, schema.BY_KEY)


class SettingsRow(FileDialogsCase):
    def page(self):
        from forza_launcher.ui.settings_page import SettingsPage
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        window = mock.Mock()
        window.preview_text.return_value = "game.exe"
        page = SettingsPage(self.cfg, window)
        page.resize(1000, 720)
        page.show()
        self.app.processEvents()

        def close():
            page._save_timer.stop()
            page.close()
            page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.addCleanup(close)
        return page

    def target(self, page):
        from forza_launcher.ui import file_dialogs
        found = [t for t in page.search.targets if t.label == file_dialogs.LABEL]
        self.assertEqual(len(found), 1)
        return found[0]

    def test_in_advanced_launcher_off_and_found_by_search(self):
        from forza_launcher.ui import file_dialogs
        self.flatpak(False)
        page = self.page()
        switch = page.file_dialogs_switch
        self.assertFalse(switch.isChecked())
        self.assertTrue(switch.isEnabled())
        target = self.target(page)
        self.assertEqual((target.group, target.section), ("Advanced", "Launcher"))
        self.assertIs(target.focus, switch)
        self.assertEqual(switch.accessibleName(), file_dialogs.LABEL)
        # on the Advanced tab, in the Launcher card with the game folders
        advanced = page.stack.widget(page.tab_index("Advanced"))
        self.assertTrue(advanced.isAncestorOf(switch))
        for words in ("system file dialog", "file explorer", file_dialogs.KEY):
            page.search.edit.setText(words)
            self.app.processEvents()
            results = [page.search.results.item(i).data(Qt.UserRole) for i in range(page.search.results.count())]
            self.assertIn(target, results, words)
        # the help says what a controller loses
        texts = " ".join(w.text() for w in target.widget.findChildren(QLabel))
        self.assertIn("controller", texts)
        self.assertNotIn("Flatpak", texts)

    def test_the_switch_saves_and_applies_at_once(self):
        from forza_launcher.ui import file_dialogs
        self.flatpak(False)
        file_dialogs.apply(self.cfg)
        page = self.page()
        page._save_timer.stop()
        page.file_dialogs_switch.click()
        self.assertIs(self.cfg.advanced[file_dialogs.KEY], True)
        self.assertFalse(own_dialogs())
        self.assertTrue(page._save_timer.isActive())   # saved like every other setting
        page.file_dialogs_switch.click()
        self.assertIs(self.cfg.advanced[file_dialogs.KEY], False)
        self.assertTrue(own_dialogs())

    def test_a_saved_choice_shows_on(self):
        from forza_launcher.ui import file_dialogs
        self.flatpak(False)
        self.cfg.advanced[file_dialogs.KEY] = True
        self.assertTrue(self.page().file_dialogs_switch.isChecked())

    def test_locked_off_in_the_flatpak_with_the_reason(self):
        from forza_launcher.ui import file_dialogs
        self.flatpak()
        self.cfg.advanced[file_dialogs.KEY] = True
        file_dialogs.apply(self.cfg)
        page = self.page()
        switch = page.file_dialogs_switch
        self.assertFalse(switch.isEnabled())
        self.assertFalse(switch.isChecked())
        texts = [w.text() for w in self.target(page).widget.findChildren(QLabel)]
        self.assertIn(file_dialogs.FLATPAK_REASON, texts)
        self.assertTrue(own_dialogs())
        self.assertIs(self.cfg.advanced[file_dialogs.KEY], True)   # building the page changed nothing


# window.run() with the window and the controller left out: what the launcher's start leaves set.
START = r"""
import sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget
from forza_launcher.config import Config
from forza_launcher.ui import gamepad, window


class Window(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.play = mock.Mock()

    def show(self):
        QTimer.singleShot(0, QApplication.quit)


class Navigation:
    def __init__(self, win):
        pass

    def start(self):
        pass

    def close(self):
        pass


with mock.patch.object(window, "MainWindow", Window), mock.patch.object(gamepad, "Navigation", Navigation), \
        mock.patch.object(window, "Config", lambda: Config(Path(sys.argv[2]))):
    code = window.run([])
own = QCoreApplication.testAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
print("START", code, "own" if own else "system")
"""


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Start(unittest.TestCase):
    def start(self, saved=None, flatpak=False):
        with tempfile.TemporaryDirectory() as temp:
            t = Path(temp)
            path = t / "config" / "launcher.json"
            if saved is not None:
                cfg = Config(path)
                cfg.advanced["data_root"] = str(t / "data")
                cfg.advanced["system_file_dialogs"] = saved
                path.parent.mkdir(parents=True)
                cfg.save()
            # nothing of the person running the tests is read or written
            env = {k: v for k, v in os.environ.items() if k != "FLATPAK_ID"}
            env.update(QT_QPA_PLATFORM="offscreen", HOME=str(t), XDG_CONFIG_HOME=str(t / "config"),
                       XDG_DATA_HOME=str(t / "data"), XDG_CACHE_HOME=str(t / "cache"),
                       APPDATA=str(t / "config"), LOCALAPPDATA=str(t / "data"), USERPROFILE=str(t))
            if flatpak:
                env["FLATPAK_ID"] = "io.example.Launcher"
            out = subprocess.run([sys.executable, "-c", START, str(Path(__file__).resolve().parent), str(path)],
                                 env=env, cwd=temp, capture_output=True, text=True, timeout=180)
        lines = [line for line in out.stdout.splitlines() if line.startswith("START ")]
        self.assertEqual(len(lines), 1, out.stdout + out.stderr)
        return lines[0]

    def test_a_first_start_uses_the_launchers_own(self):
        self.assertEqual(self.start(), "START 0 own")

    def test_off_uses_the_launchers_own(self):
        self.assertEqual(self.start(saved=False), "START 0 own")

    def test_on_leaves_qt_free_to_use_the_systems(self):
        self.assertEqual(self.start(saved=True), "START 0 system")

    @unittest.skipIf(paths.IS_WINDOWS, "the Flatpak is Linux's")
    def test_on_in_the_flatpak_still_uses_the_launchers_own(self):
        self.assertEqual(self.start(saved=True, flatpak=True), "START 0 own")


if __name__ == "__main__":
    unittest.main()
