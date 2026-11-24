"""Setup's DLC step: an installed package whose folder name is a known package's name with the end cut off
gets a button that renames it, and its .header, to the full name (#951). Only on the click, after asking."""
from PySide6.QtWidgets import QMessageBox, QPushButton, QVBoxLayout, QWidget

from .. import fan_dlc


class DlcRename(QWidget):
    def __init__(self, cfg, window, on_done):
        super().__init__()
        self.cfg, self.window, self.on_done = cfg, window, on_done
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.buttons = []
        self.hide()

    def refresh(self, names):
        """One button per installed folder name in `names` that has a known full name."""
        for b in self.buttons:
            self.lay.removeWidget(b)
            b.deleteLater()
        self.buttons = []
        for name in names:
            known = fan_dlc.suggestion(name)
            if known is None or fan_dlc._entry_exists(fan_dlc.dlc_dirs(self.cfg.data_home())[0], known):
                continue   # a copy with the full name is installed already: the click would only refuse
            b = QPushButton(f"Fix the folder name of {fan_dlc.install_dlc.KNOWN_NAMES[known]}")
            b.setToolTip(f"Renames the folder {name} and its .header file to {known}. Nothing is deleted.")
            b.clicked.connect(lambda _=False, n=name, k=known: self.rename(n, k))
            self.lay.addWidget(b)
            self.buttons.append(b)
        self.setVisible(bool(self.buttons))

    def rename(self, name, known):
        title = "Rename DLC package"
        if getattr(self.window, "running", lambda: False)():
            QMessageBox.information(self, title, "Close the game first: it is using its DLC.")
            return
        setup = getattr(self.window, "setup", None)
        if (setup is not None and setup.busy()) or getattr(self.window, "_busy", False):
            QMessageBox.information(self, title, "Wait for the task in Setup to finish, then press it again.")
            return
        if QMessageBox.question(
                self, title,
                f"Rename the folder\n{name}\nto\n{known}\n({fan_dlc.install_dlc.KNOWN_NAMES[known]}), "
                "and its .header file with it? Nothing is deleted.") != QMessageBox.Yes:
            return
        try:
            fan_dlc.rename_package(self.cfg.data_home(), name, known)
        except fan_dlc.RenameError as e:
            QMessageBox.warning(self, title, str(e))
            return
        self.on_done(fan_dlc.renamed_text(name, known))
