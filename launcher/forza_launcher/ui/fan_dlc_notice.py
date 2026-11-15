"""The notice on Play and in Setup's DLC step when the fan-made unicorn cars pack (offer 1975) is installed:
with it the first Single Player load never finishes (#66, #76, #115, #771). "Move the pack out" moves it
out of the installed DLC into a folder next to it (fan_dlc.move_out: nothing is deleted)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMessageBox, QPushButton

from .. import fan_dlc
from . import theme
from .widgets import label


class FanDlcNotice(QFrame):
    moved = Signal(str)   # what was moved and where; the page shows it

    def __init__(self, cfg, window):
        super().__init__()
        self.cfg, self.window = cfg, window
        self.packages = []
        self.setObjectName("fanDlcNotice")
        self.setStyleSheet(f"#fanDlcNotice {{ border: 1px solid {theme.ORANGE}; border-radius: 8px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        self.text = label("", "")
        self.text.setTextFormat(Qt.PlainText)
        row.addWidget(self.text, 1)
        self.move = QPushButton(fan_dlc.MOVE_BUTTON)
        self.move.setToolTip("Moves the package out of the installed DLC into a folder next to it; "
                             "nothing is deleted")
        self.move.clicked.connect(self.move_out)
        row.addWidget(self.move, 0, Qt.AlignVCenter)
        self.hide()   # refresh() shows it, once it is in a page

    def refresh(self, packages=None):
        self.packages = fan_dlc.fan_packages(self.cfg.data_home()) if packages is None else packages
        self.text.setText(fan_dlc.fan_text(self.packages) if self.packages else "")
        self.setVisible(bool(self.packages))

    def busy(self) -> bool:
        """Something else is changing the data folder: Install DLC, a build or a data folder move (Setup),
        or another task that holds the window busy."""
        setup = getattr(self.window, "setup", None)
        return bool((setup is not None and setup.busy()) or getattr(self.window, "_busy", False))

    def move_out(self):
        running = getattr(self.window, "running", lambda: False)
        if running():
            QMessageBox.information(self, fan_dlc.MOVE_BUTTON, "Close the game first: it is using its DLC.")
            return
        if self.busy():
            QMessageBox.information(self, fan_dlc.MOVE_BUTTON,
                                    "Wait for the task in Setup to finish (it is changing the data folder), "
                                    "then press it again.")
            return
        home = self.cfg.data_home()
        done = []
        for p in fan_dlc.fan_packages(home):   # read again: the installed DLC may have changed since
            try:
                done.append((p, fan_dlc.move_out(home, p.name)))
            except fan_dlc.MoveError as e:
                QMessageBox.warning(self, fan_dlc.MOVE_BUTTON, str(e))
                break
        text = " ".join(fan_dlc.moved_text([p], root, home) for p, root in done)
        for page in (getattr(self.window, "play", None), getattr(self.window, "setup", None)):
            if page is not None:
                page.refresh()
        self.refresh()
        if text:
            self.moved.emit(text)
