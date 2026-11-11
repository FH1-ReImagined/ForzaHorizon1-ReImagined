"""The banner on Play and Online > Servers when Play is Offline because "Online features" was turned
off (online_features.offline_by_choice): the game never signs in then, and players take it for a
server that does not let them in. "Go online" turns online features back on and picks the Public
server."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton

from .. import online_features
from . import theme
from .widgets import label

TEXT = ('You are offline: "Online features" was turned off when the game was installed or first '
        "started, so the game does not connect to any server (no Horizon World, friends or leaderboards).")


class OfflineNotice(QFrame):
    went_online = Signal()   # cfg changed: the window saves it and refreshes the pages

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.setObjectName("offlineNotice")
        self.setStyleSheet(f"#offlineNotice {{ border: 1px solid {theme.ORANGE}; border-radius: 8px; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)
        self.text = label(TEXT, "")
        self.text.setTextFormat(Qt.PlainText)
        row.addWidget(self.text, 1)
        self.go = QPushButton("Go online")
        self.go.setToolTip("Turns online features back on and plays on the Public server")
        self.go.clicked.connect(self.go_online)
        row.addWidget(self.go, 0, Qt.AlignVCenter)
        self.hide()   # refresh() shows it, once it is in a page (no window of its own)

    def refresh(self):
        self.setVisible(online_features.offline_by_choice(self.cfg))

    def go_online(self):
        online_features.go_online(self.cfg)
        self.refresh()
        self.went_online.emit()
