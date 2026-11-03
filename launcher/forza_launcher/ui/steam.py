"""Steam shortcut card. Discovery and writes run outside the GUI thread."""
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QInputDialog, QPushButton, QVBoxLayout, QWidget

from .. import GITHUB_URL, paths, steam_shortcuts
from . import workers
from .widgets import label

GUIDE = GITHUB_URL + "/blob/main/docs/steam_deck.md#add-to-steam"


class SteamCard(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.task = self.probe = None
        self._result = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.addWidget(label("Steam", "cardTitle"))
        text = ("Add this Flatpak to Steam from Steam's Games > Add a Non-Steam Game menu."
                if paths.IN_FLATPAK else
                "Add this launcher to your Steam library with artwork. Exit Steam first, "
                "and keep it closed until setup finishes.")
        layout.addWidget(label(text, "muted"))
        self.button = QPushButton("Add to Steam manually" if paths.IN_FLATPAK else "Add to Steam")
        self.button.clicked.connect(self.add)
        layout.addWidget(self.button, 0, Qt.AlignLeft)
        self.message = label("", "help")
        self.message.setTextFormat(Qt.PlainText)
        self.message.hide()
        layout.addWidget(self.message)
        guide = QPushButton("Steam Deck setup guide")
        guide.setObjectName("link")
        guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GUIDE)))
        layout.addWidget(guide, 0, Qt.AlignLeft)

    def busy(self):
        return self.task is not None

    def _message(self, text):
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def add(self):
        if paths.IN_FLATPAK:
            QDesktopServices.openUrl(QUrl(GUIDE))
            return
        if self.task or self.probe:
            return
        self.button.setEnabled(False)
        self._message("Finding Steam accounts…")
        self.probe = workers.Probe(steam_shortcuts.accounts, "find Steam accounts", self)
        self.probe.result.connect(self._found)
        self.probe.start()

    def _found(self, accounts):
        self.probe.deleteLater()
        self.probe = None
        if not accounts:
            self._message("No readable Steam account was found. Open Steam and sign in once, exit Steam, then try again.")
            self.button.setEnabled(True)
            return
        account = accounts[0]
        if len(accounts) > 1:
            labels = [a.label for a in accounts]
            selected, accepted = QInputDialog.getItem(self, "Add to Steam", "Choose the Steam account:", labels, 0, False)
            if not accepted:
                self._message("Cancelled. No Steam files were changed.")
                self.button.setEnabled(True)
                return
            account = accounts[labels.index(selected)]
        self._message(f"Adding the shortcut for account {account.user}. Keep Steam closed…")

        def install(task):
            try:
                return steam_shortcuts.add(account, steam_shortcuts.launcher_command(), steam_shortcuts.bundled_artwork())
            except steam_shortcuts.SteamError as exc:
                return str(exc)

        self.task = workers.Task(install, "add Steam shortcut")
        self.task.done.connect(self._done)
        self.task.finished.connect(self._finished)
        self.task.start()

    def _done(self, _success, text):
        self._result = text

    def _finished(self):
        self.task.deleteLater()
        self.task = None
        self._message(self._result)
        self.button.setEnabled(True)
