"""Non-blocking release checks and an explicit download/install handoff."""
import platform
from pathlib import Path
import shlex
import subprocess
import sys

from PySide6.QtCore import QThread, QTimer, QUrl, QStandardPaths, Signal, Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QWidget

from .. import GITHUB_URL, __version__, browser, fetch, paths, updater
from .widgets import card, label


class UpdateMessage(QLabel):
    """Reserve the full wrapped height when an asynchronous status message changes."""

    def _fit_height(self):
        self.setMinimumHeight(max(0, self.heightForWidth(self.width())))

    def setText(self, text):
        super().setText(text)
        self._fit_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()


class UpdateTask(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.result.emit(self.fn(self))
        except fetch.Cancelled:
            self.error.emit("Download cancelled. You can try again whenever you're ready.")
        except (updater.UpdateError, OSError) as exc:
            self.error.emit(str(exc))
        except Exception as exc:
            paths.log_error("launcher update", exc)
            self.error.emit("The update could not finish. Try again or open Releases.")


class UpdateCard(QWidget):
    available = Signal(str)

    def __init__(self, cfg, window):
        super().__init__()
        from PySide6.QtWidgets import QVBoxLayout
        self.cfg, self.window = cfg, window
        self.task = None
        self.closing = False
        self.installing = self.install_requested = self.restart_ready = False
        self.restarting = False
        self.release = self.asset = self.downloaded = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        box, lay = card()
        outer.addWidget(box)
        lay.addWidget(label("Launcher updates", "cardTitle"))
        self.message = UpdateMessage("Check for a newer launcher release.")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.message.setTextFormat(Qt.PlainText)
        lay.addWidget(self.message)
        if paths.in_checkout():
            lay.addWidget(label("Running from source. Update your checkout to use new code; "
                                "downloads below are packaged releases.", "help"))
        self.automatic = QCheckBox("Check for updates when the launcher opens")
        self.automatic.setChecked(bool(cfg.d["check_updates"]))
        if paths.in_checkout():
            self.automatic.setText("Check on startup (packaged launchers only)")
            self.automatic.setEnabled(False)
            self.automatic.setToolTip("Automatic checks run in packaged launchers. Use Check for updates in a source checkout.")
        self.automatic.toggled.connect(self._save_preference)
        lay.addWidget(self.automatic)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        self.check_button = QPushButton("Check for updates")
        self.check_button.clicked.connect(self.check)
        self.action = QPushButton("Download update")
        self.action.clicked.connect(self.act)
        self.action.hide()
        self.cancel = QPushButton("Cancel download")
        self.cancel.clicked.connect(self.cancel_task)
        self.cancel.hide()
        self.notes = QPushButton("Releases")
        self.notes.setObjectName("link")
        self.notes.clicked.connect(self.open_release)
        for button in (self.check_button, self.action, self.cancel, self.notes):
            row.addWidget(button)
        row.addStretch()
        lay.addLayout(row)

    def _save_preference(self, checked):
        self.cfg.d["check_updates"] = checked
        try:
            self.cfg.save()
        except OSError as exc:
            self.message.setText(f"Could not save the update preference: {exc}")

    def busy(self):
        return self.task is not None

    def automatic_install_supported(self):
        return sys.platform.startswith("linux") and paths.FROZEN and not paths.IN_FLATPAK

    def cancel_task(self):
        if self.task:
            if self.installing:
                self.message.setText("Installing the update. The launcher will restart when it's ready.")
                return
            self.task.requestInterruption()
            self.cancel.setEnabled(False)
            self.message.setText("Stopping the download…")

    def _start(self, fn, callback):
        self.task = UpdateTask(fn, self)
        self.task.result.connect(callback)
        self.task.error.connect(self.message.setText)
        self.task.progress.connect(self.bar.setValue)
        self.task.finished.connect(self._finished)
        self.check_button.setEnabled(False)
        self.action.setEnabled(False)
        self.task.start()

    def _finished(self):
        task, self.task = self.task, None
        task.deleteLater()
        self.check_button.setEnabled(True)
        self.action.setEnabled(True)
        self.cancel.hide()
        self.bar.hide()
        if self.installing:
            self.installing = False
            if not self.restart_ready:
                self._enable_other_pages(True)
        if self.closing:
            self.closing = False
            self.install_requested = False
            QTimer.singleShot(0, self.window.close)
        elif self.restart_ready:
            self.restart_ready = False
            self.restarting = True
            QTimer.singleShot(0, self.window.close)
        elif self.install_requested:
            self.install_requested = False
            if self.downloaded:
                QTimer.singleShot(0, self.install)

    def check(self):
        if self.busy():
            return
        self.release = self.asset = self.downloaded = None
        self.available.emit("")
        self.action.hide()
        self.message.setText("Checking for updates…")
        self._start(lambda t: updater.check(), self._checked)

    def _checked(self, release):
        self.release = release
        self.action.setText("Update and restart" if self.automatic_install_supported() else "Download update")
        if updater.version_key(__version__) is None:
            self.message.setText(f"Latest stable release: {release.tag}. This is a development build; "
                                 "its version cannot be compared automatically.")
            return
        if not release.newer_than():
            self.message.setText(f"You're up to date. Latest stable release: {release.tag}.")
            return
        self.available.emit(release.tag)
        self.asset = release.asset_for(sys.platform, paths.IN_FLATPAK, platform.machine())
        self.message.setText(f"{release.tag} is available. Installed launcher: {__version__}. " +
                             (("Update and restart to install it automatically. Your saves and game files are kept."
                               if self.automatic_install_supported() else
                               "Download the packaged release. Your saves and game files are kept.")
                              if self.asset else "Open Releases for installation options; no verified download "
                              "is available for this platform."))
        self.action.setVisible(self.asset is not None)

    def open_release(self):
        browser.open_url(QUrl(self.release.url if self.release else GITHUB_URL + "/releases/latest"))

    def act(self):
        if self.busy() or not self.asset:
            return
        if self.downloaded:
            self.install()
            return
        if self.automatic_install_supported():
            if self.window.setup.busy() or self.window.running():
                self.message.setText("Finish the current task and close the game before installing the update.")
                return
            self.install_requested = True
        self.message.setText(f"Downloading {self.release.tag}…")
        self.bar.setValue(0)
        self.bar.setRange(0, 100)
        self.bar.show()
        self.cancel.setEnabled(True)
        self.cancel.show()
        if paths.IN_FLATPAK:
            downloads = QStandardPaths.writableLocation(QStandardPaths.DownloadLocation)
            directory = Path(downloads or Path.home() / "Downloads") / "ForzaHorizon1-ReImagined" / self.release.tag
        else:
            directory = updater.downloads_dir() / self.release.tag
        self._start(lambda t: updater.download(
            self.asset, directory, lambda d, total: t.progress.emit(int(d * 100 / total)),
            t.isInterruptionRequested), self._downloaded)

    def _downloaded(self, path):
        self.downloaded = path
        if paths.IN_FLATPAK:
            self.action.setText("Open download folder")
            self.message.setText(self.flatpak_instructions())
            return
        self.action.setText("Open installer" if sys.platform == "win32" else
                            "Install and restart" if self.automatic_install_supported() else "Open download")
        self.message.setText("Download verified. " + ("Open the installer to update the launcher."
                             if sys.platform == "win32" else "Ready to install and restart."
                             if self.automatic_install_supported() else "Open the downloaded package to install it."))

    def _enable_other_pages(self, enabled):
        for page in (self.window.play, self.window.setup, self.window.settings):
            page.setEnabled(enabled)

    def _installed(self, _result):
        self.restart_ready = True
        self.message.setText("Update ready. Restarting the launcher…")

    def flatpak_instructions(self):
        command = shlex.join(["flatpak", "install", "--user", str(self.downloaded)])
        return ("Download verified. To install for your user, close this launcher, then run in a terminal:\n"
                + command + "\nOpen the launcher again after installation finishes.")

    def install(self):
        if self.busy() or self.downloaded is None:
            return
        if paths.IN_FLATPAK:
            self.message.setText(self.flatpak_instructions())
            if not browser.open_url(QUrl.fromLocalFile(str(self.downloaded.parent))):
                self.message.setText(self.flatpak_instructions() + "\nDownload folder: " + str(self.downloaded.parent))
            return
        if self.window.setup.busy() or self.window.running():
            self.message.setText("Finish the current task and close the game before installing the update.")
            return
        if sys.platform == "win32":
            # Inno Setup handles replacing the app and prompting to close running copies.
            # Pin the existing install folder for packaged launches (including custom paths).
            argv = [str(self.downloaded)]
            if paths.FROZEN:
                argv.append("/DIR=" + str(paths.app_dir()))
            try:
                subprocess.Popen(argv)
            except OSError as exc:
                self.message.setText(f"Could not open the installer: {exc}. Open Releases to retry.")
                return
            self.message.setText("Installer opened. Close this launcher before continuing in the installer. "
                                 "Open the updated launcher afterwards; Setup will offer a rebuild if needed.")
        elif self.automatic_install_supported():
            from .. import linux_update
            self.window.settings.flush()
            self.installing = True
            self._enable_other_pages(False)
            self.message.setText("Installing the update. The launcher will restart when it's ready…")
            self.bar.setRange(0, 0)
            self.bar.show()
            self.cancel.hide()
            protected = [self.cfg.data_home(), self.cfg.game_files()]
            if self.cfg.advanced.get("game_dir"):
                protected.append(self.cfg.advanced["game_dir"])
            fn = lambda t: linux_update.install_archive(
                self.downloaded, self.asset, self.release.tag, paths.app_dir(), paths.config_dir(), protected,
                t.isInterruptionRequested)
            self._start(fn, self._installed)
        else:
            if not browser.open_url(QUrl.fromLocalFile(str(self.downloaded.parent))):
                self.message.setText("Open the downloaded update at: " + str(self.downloaded))
