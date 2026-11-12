"""Opening links and folders from the frozen Linux launcher (issue #815):
python3 -m unittest launcher/test_browser.py

Nothing here starts a real opener: the commands are mocked, or are scripts in a temporary folder
that only write down what they were given."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import browser, cleanenv  # noqa: E402

INTERNAL = "/opt/forza-launcher/_internal"
USER_LIBS = "/home/me/lib"
LINK = "https://forza.dyotson.dev/account.html#link=abc"


def frozen_env(original=USER_LIBS):
    env = {"PATH": "/usr/bin", "HOME": "/home/me",
           "LD_LIBRARY_PATH": INTERNAL + (os.pathsep + original if original else ""),
           "QT_PLUGIN_PATH": INTERNAL + "/PySide6/Qt/plugins", "QML2_IMPORT_PATH": INTERNAL + "/PySide6/Qt/qml",
           "_PYI_APPLICATION_HOME_DIR": INTERNAL, "_MEIPASS2": INTERNAL}
    if original is not None:
        env["LD_LIBRARY_PATH_ORIG"] = original
    return env


def frozen(on=True, platform="linux"):
    patches = [mock.patch.object(sys, "platform", platform)]
    if on:
        patches += [mock.patch.object(sys, "frozen", True, create=True),
                    mock.patch.object(sys, "_MEIPASS", INTERNAL, create=True)]
    else:
        patches += [mock.patch.object(sys, "frozen", False, create=True)]
    return patches


class Patched(unittest.TestCase):
    def use(self, patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class Environment(Patched):
    def opened_env(self, env):
        seen = []
        browser.open_with_commands(LINK, env=cleanenv.clean_environment(env),
                                   run=lambda cmd, e: seen.append(e) or True)
        return seen[0]

    def test_frozen_linux_uses_the_openers(self):
        self.use(frozen(True))
        self.assertTrue(browser.external())

    def test_not_frozen_or_not_linux_is_qt(self):
        with mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(browser.external())
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "platform", "win32"):
            self.assertFalse(browser.external())

    def test_the_players_library_path_comes_back(self):
        self.use(frozen(True))
        env = self.opened_env(frozen_env(USER_LIBS))
        self.assertEqual(env["LD_LIBRARY_PATH"], USER_LIBS)
        for name in ("LD_LIBRARY_PATH_ORIG", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "_PYI_APPLICATION_HOME_DIR",
                     "_MEIPASS2"):
            self.assertNotIn(name, env)

    def test_without_a_saved_path_the_bundle_is_dropped(self):
        self.use(frozen(True))
        env = self.opened_env(frozen_env(None))
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertNotIn("QT_PLUGIN_PATH", env)

    def test_the_default_environment_is_the_clean_one(self):
        self.use(frozen(True))
        seen = []
        with mock.patch.dict(os.environ, frozen_env(USER_LIBS), clear=True):
            browser.open_with_commands(LINK, run=lambda cmd, e: seen.append(e) or True)
        self.assertEqual(seen[0]["LD_LIBRARY_PATH"], USER_LIBS)
        self.assertNotIn("QT_PLUGIN_PATH", seen[0])


class Order(Patched):
    def setUp(self):
        self.tried = []
        self.answers = {}
        self.qt = mock.patch.object(browser.QDesktopServices, "openUrl", return_value=True)
        self.qt_open = self.qt.start()
        self.addCleanup(self.qt.stop)
        self.use([mock.patch.object(browser, "run_command", self.fake_run)])

    def fake_run(self, cmd, env):
        self.tried.append(cmd[0] if cmd[0] != "gdbus" else "portal")
        return self.answers.get(self.tried[-1], False)

    def test_xdg_open_first(self):
        self.use(frozen(True))
        self.answers = {"xdg-open": True}
        self.assertTrue(browser.open_url(LINK))
        self.assertEqual(self.tried, ["xdg-open"])
        self.qt_open.assert_not_called()

    def test_then_gio_then_the_portal(self):
        self.use(frozen(True))
        self.answers = {"portal": True}
        self.assertTrue(browser.open_url(LINK))
        self.assertEqual(self.tried, ["xdg-open", "gio", "portal"])
        self.qt_open.assert_not_called()

    def test_qt_last_and_its_answer_is_the_answer(self):
        self.use(frozen(True))
        self.qt_open.return_value = False
        self.assertFalse(browser.open_url(LINK))
        self.assertEqual(self.tried, ["xdg-open", "gio", "portal"])
        self.qt_open.assert_called_once()
        self.assertEqual(self.qt_open.call_args.args[0].toString(), LINK)

    def test_a_folder_never_goes_to_the_portal(self):
        from PySide6.QtCore import QUrl
        self.use(frozen(True))
        self.assertTrue(browser.open_url(QUrl.fromLocalFile("/home/me/saves")))
        self.assertEqual(self.tried, ["xdg-open", "gio"])
        cmds = browser.commands(QUrl.fromLocalFile("/home/me/saves"))
        self.assertEqual(cmds[0], ["xdg-open", "/home/me/saves"])

    def test_the_portal_call(self):
        from PySide6.QtCore import QUrl
        portal = browser.commands(QUrl(LINK))[-1]
        self.assertEqual(portal[:2], ["gdbus", "call"])
        self.assertIn("org.freedesktop.portal.OpenURI.OpenURI", portal)
        self.assertEqual(portal[-3:], ["", LINK, "@a{sv} {}"])

    def test_not_frozen_is_qt_only(self):
        self.use(frozen(False))
        self.assertTrue(browser.open_url(LINK))
        self.assertEqual(self.tried, [])
        self.qt_open.assert_called_once()


class Commands(unittest.TestCase):
    """run_command with scripts that write down their arguments and environment."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.log = Path(self.tmp.name) / "log"

    def script(self, name, status=0, sleep=0):
        p = self.bin / name
        p.write_text(textwrap.dedent(f"""\
            #!/bin/sh
            echo "$0 $* LD_LIBRARY_PATH=${{LD_LIBRARY_PATH-unset}} QT_PLUGIN_PATH=${{QT_PLUGIN_PATH-unset}}" >> {self.log}
            sleep {sleep}
            exit {status}
            """))
        p.chmod(0o755)

    def env(self, **more):
        return {"PATH": str(self.bin), **more}

    @unittest.skipIf(sys.platform == "win32", "shell scripts")
    def test_exit_status(self):
        self.script("good")
        self.script("bad", status=4)
        self.assertTrue(browser.run_command(["good", LINK], self.env(LD_LIBRARY_PATH=USER_LIBS)))
        self.assertFalse(browser.run_command(["bad", LINK], self.env()))
        lines = self.log.read_text().splitlines()
        self.assertIn(f"good {LINK} LD_LIBRARY_PATH={USER_LIBS} QT_PLUGIN_PATH=unset", lines[0])

    @unittest.skipIf(sys.platform == "win32", "shell scripts")
    def test_missing_opener(self):
        self.assertFalse(browser.run_command(["xdg-open", LINK], self.env()))

    @unittest.skipIf(sys.platform == "win32", "shell scripts")
    def test_an_opener_still_running_has_started(self):
        self.script("slow", sleep=3)
        self.assertTrue(browser.run_command(["slow", LINK], self.env(), wait=0.2))

    @unittest.skipIf(sys.platform == "win32", "shell scripts")
    def test_frozen_openers_get_the_players_environment(self):
        # xdg-open fails, gio opens: both ran with the player's library path and no bundle Qt
        self.script("xdg-open", status=3)
        self.script("gio")
        env = frozen_env(USER_LIBS)
        env["PATH"] = str(self.bin)
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", INTERNAL, create=True), \
                mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(browser.QDesktopServices, "openUrl", return_value=False) as qt:
            self.assertTrue(browser.open_url(LINK))
        qt.assert_not_called()
        lines = self.log.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith(f"{self.bin}/xdg-open {LINK} LD_LIBRARY_PATH={USER_LIBS} "
                                            "QT_PLUGIN_PATH=unset"), lines[0])
        self.assertTrue(lines[1].startswith(f"{self.bin}/gio open {LINK} LD_LIBRARY_PATH={USER_LIBS}"), lines[1])

    def test_popen_failure_is_not_opened(self):
        def popen(*a, **k):
            raise OSError("exec format error")
        with mock.patch.object(browser.shutil, "which", return_value="/usr/bin/xdg-open"):
            self.assertFalse(browser.run_command(["xdg-open", LINK], {"PATH": "/usr/bin"}, popen=popen))

    def test_a_new_session_and_no_terminal(self):
        proc = mock.Mock()
        proc.wait.return_value = 0
        popen = mock.Mock(return_value=proc)
        with mock.patch.object(browser.shutil, "which", return_value="/usr/bin/xdg-open"):
            browser.run_command(["xdg-open", LINK], {"PATH": "/usr/bin"}, popen=popen)
        kw = popen.call_args.kwargs
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(kw["stdin"], subprocess.DEVNULL)
        self.assertEqual(kw["env"], {"PATH": "/usr/bin"})


class Handler(Patched):
    """install(): Qt's own openUrl of a link (labels, the web view) takes the same way."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        if browser._handler is not None:
            for scheme in browser.SCHEMES:
                browser.QDesktopServices.unsetUrlHandler(scheme)
            browser._handler = None

    def test_not_installed_outside_the_frozen_linux_launcher(self):
        self.use(frozen(False))
        browser.install()
        self.assertIsNone(browser._handler)

    def test_qt_links_go_through_the_openers(self):
        self.use(frozen(True))
        browser.install()
        self.assertIsNotNone(browser._handler)
        with mock.patch.object(browser, "open_with_commands", return_value=True) as opened:
            self.assertTrue(browser.QDesktopServices.openUrl(browser.as_qurl(LINK)))
        opened.assert_called_once()
        self.assertEqual(opened.call_args.args[0].toString(), LINK)

    def test_open_url_still_gets_qts_answer(self):
        self.use(frozen(True))
        browser.install()
        self.use([mock.patch.object(browser, "run_command", return_value=False)])
        seen = []
        real_unset = browser.QDesktopServices.unsetUrlHandler

        def unset(scheme):
            seen.append(scheme)
            real_unset(scheme)
        with mock.patch.object(browser.QDesktopServices, "unsetUrlHandler", side_effect=unset), \
                mock.patch.object(browser.QDesktopServices, "openUrl", return_value=False) as qt:
            self.assertFalse(browser.open_url(LINK))
        qt.assert_called_once()
        self.assertEqual(seen, list(browser.SCHEMES))
        self.assertIsNotNone(browser._handler)


if __name__ == "__main__":
    unittest.main()
