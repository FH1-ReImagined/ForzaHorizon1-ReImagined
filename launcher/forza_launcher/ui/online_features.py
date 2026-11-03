"""The first-run "Online features" question (#497) where no Windows installer asked it: Linux,
Flatpak and source checkouts. The same choice as the installer's option (online_features.py)."""
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from .. import PRIVACY_URL
from .widgets import label

FEATURES = (
    "Online play on the public Forza Live server: Horizon World, friends, chat, leaderboards and "
    "Rivals. The server receives your gamertag, a random player ID and your IP address.",
    "After you play online: sharing shader lists with that server, and asking it for your co-op "
    "campaign results.",
    "A check for launcher updates on GitHub when the launcher opens.",
    "Your activity in the game, shown through the Discord desktop app if it is running.",
)


class OnlineFeaturesDialog(QDialog):
    """One checkbox, ticked like the installer's. Closing the window keeps what it shows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Online features")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel("Forza Horizon 1: ReImagined can use the network for:"))
        for text in FEATURES:
            lay.addWidget(label("- " + text))
        self.online = QCheckBox("Turn on online features")
        self.online.setChecked(True)
        lay.addWidget(self.online)
        lay.addWidget(label("Untick to turn them all off: the game plays offline and the launcher sends "
                            "nothing on its own. You can turn each one on or off later in Settings > Online "
                            "and on the About page.", "help"))
        policy = QPushButton("Read the privacy policy")
        policy.setObjectName("link")
        policy.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(PRIVACY_URL)))
        lay.addWidget(policy)
        buttons = QDialogButtonBox()
        go = buttons.addButton("Continue", QDialogButtonBox.AcceptRole)
        go.setDefault(True)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

    def reject(self):
        # Escape or the window's close button: the choice shown is the answer.
        self.accept()
