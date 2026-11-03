"""Station editor for independently built local radio overlays."""
from pathlib import Path
import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .. import radio
from .widgets import label
from .workers import Task


class RadioPage(QWidget):
    idle = Signal()

    def __init__(self, cfg, window):
        super().__init__()
        self.cfg, self.window, self.task = cfg, window, None
        self._loading = False
        self._slot = radio.SLOTS[0]
        load_error = ""
        try:
            self.spec = radio.settings(cfg)
        except radio.RadioError as exc:
            self.spec = {"version": 1, "ffmpeg": "", "stations": {}}
            load_error = str(exc)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 16)
        lay.addWidget(label("Custom radio", "title"))
        lay.addWidget(label("Give the three radio stations your own names, logos and music. "
                            "Add songs to the original playlist or replace it. Build while the game is closed; "
                            "changes apply at the next start.", "muted"))
        self.editor = QWidget()
        form = QFormLayout(self.editor)
        self.slot = QComboBox()
        self.slot.addItems(radio.TITLES)
        self.slot.currentIndexChanged.connect(self._select)
        form.addRow("Station slot", self.slot)
        self.name = QLineEdit()
        self.name.setMaxLength(64)
        self.name.editingFinished.connect(self._save)
        form.addRow("Station name", self.name)
        self.mode = QComboBox()
        self.mode.addItem("Replace the station's songs", "replace")
        self.mode.addItem("Keep the original songs and add mine", "add")
        self.mode.currentIndexChanged.connect(self._save)
        form.addRow("Playlist", self.mode)
        self.logo = QLabel("Original station logo")
        self.logo.setMinimumSize(210, 100)
        form.addRow("Logo preview", self.logo)
        logos = QHBoxLayout()
        choose = QPushButton("Choose PNG…")
        choose.clicked.connect(self._logo)
        clear = QPushButton("Use original logo")
        clear.clicked.connect(lambda: self._set_logo(""))
        logos.addWidget(choose)
        logos.addWidget(clear)
        form.addRow("", logos)
        self.songs = QListWidget()
        self.songs.setSelectionMode(QListWidget.ExtendedSelection)
        form.addRow("Songs", self.songs)
        buttons = QHBoxLayout()
        for text, callback in (("Add songs…", self._songs), ("Add folder…", self._folder),
                               ("Remove selected", self._remove)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        form.addRow("", buttons)
        remove = QPushButton("Restore this station on next build")
        remove.clicked.connect(self._restore)
        form.addRow("", remove)
        self.ffmpeg = QLineEdit(self.spec["ffmpeg"])
        self.ffmpeg.setPlaceholderText("Use FFmpeg from PATH")
        self.ffmpeg.editingFinished.connect(self._save)
        tools = QHBoxLayout()
        tools.addWidget(self.ffmpeg)
        locate = QPushButton("Locate FFmpeg…")
        locate.clicked.connect(self._ffmpeg)
        tools.addWidget(locate)
        form.addRow("Encoder", tools)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.editor)
        lay.addWidget(scroll, 1)
        actions = QHBoxLayout()
        self.build_button = QPushButton("Build stations")
        self.build_button.clicked.connect(self._build)
        actions.addWidget(self.build_button)
        self.cancel_button = QPushButton("Cancel build")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.hide()
        actions.addWidget(self.cancel_button)
        lay.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.hide()
        lay.addWidget(self.progress)
        self.message = label("Built stations are stored in your data folder and survive game updates. "
                             "Your music files are never modified. Songs longer than 30 minutes are limited to 30 minutes.", "muted")
        lay.addWidget(self.message)
        self._load()
        if load_error:
            self.message.setText(load_error)

    def busy(self):
        return self.task is not None

    def refresh(self):
        self.build_button.setEnabled(self._can_build())

    def _can_build(self):
        if self.busy() or self.window.running() or getattr(self.window, "_busy", False):
            return False
        for page in ("setup", "catalogs", "saves", "texture_packs"):
            other = getattr(self.window, page, None)
            if other is not None and other.busy():
                return False
        updates = getattr(getattr(self.window, "about", None), "updates", None)
        return updates is None or not updates.busy()

    def _load(self):
        self._loading = True
        station = self.spec["stations"].get(self._slot, {})
        self.name.setText(station.get("name", radio.TITLES[radio.SLOTS.index(self._slot)]))
        self.mode.setCurrentIndex(1 if station.get("mode") == "add" else 0)
        self.songs.clear()
        self.songs.addItems(station.get("songs", []))
        self._logo_path = station.get("logo", "")
        self.logo.clear()
        if self._logo_path:
            pm = QPixmap(self._logo_path)
            if pm.isNull():
                self.logo.setText("Logo missing or unreadable; choose another PNG")
            else:
                self.logo.setPixmap(pm.scaled(210, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.logo.setText("Original station logo")
        self._loading = False

    def _save(self, *_):
        if self._loading:
            return True
        previous = self.cfg.d.get("custom_radio")
        self.spec["ffmpeg"] = self.ffmpeg.text().strip()
        # Visiting a stock slot alone does not create a custom station.
        if (self.songs.count() or self._slot in self.spec["stations"] or self._logo_path
                or self.mode.currentData() == "add"
                or self.name.text().strip() != radio.TITLES[radio.SLOTS.index(self._slot)]):
            self.spec["stations"][self._slot] = {
                "name": self.name.text().strip(), "mode": self.mode.currentData(),
                "songs": [self.songs.item(i).text() for i in range(self.songs.count())],
                "logo": self._logo_path}
        try:
            self.cfg.d["custom_radio"] = self.spec
            self.spec = radio.settings(self.cfg)
            self.cfg.save()
            return True
        except (OSError, ValueError) as exc:
            if previous is None:
                self.cfg.d.pop("custom_radio", None)
            else:
                self.cfg.d["custom_radio"] = previous
            self.message.setText(str(exc))
            return False

    def _select(self, index):
        if not self._save():
            self.slot.blockSignals(True)
            self.slot.setCurrentIndex(radio.SLOTS.index(self._slot))
            self.slot.blockSignals(False)
            return
        self._slot = radio.SLOTS[index]
        self._load()

    def _logo(self):
        path, _ = QFileDialog.getOpenFileName(self, "Station logo", str(Path.home()), "PNG images (*.png)")
        if path:
            self._set_logo(path)

    def _set_logo(self, path):
        self._logo_path = path
        self._save()
        self._load()

    def _songs(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add music", str(Path.home()),
                                               "Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.opus);;All files (*)")
        self._add(files)

    def _folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Music folder", str(Path.home()))
        if folder:
            extensions = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
            self._add([str(p) for p in sorted(Path(folder).glob("*")) if p.is_file() and p.suffix.lower() in extensions])

    def _add(self, files):
        existing = {self.songs.item(i).text() for i in range(self.songs.count())}
        self.songs.addItems([f for f in dict.fromkeys(files) if f not in existing])
        self._save()

    def _remove(self):
        for item in self.songs.selectedItems():
            self.songs.takeItem(self.songs.row(item))
        self._save()

    def _restore(self):
        previous = copy.deepcopy(self.spec)
        changed = copy.deepcopy(self.spec)
        changed["stations"].pop(self._slot, None)
        self.cfg.d["custom_radio"] = changed
        try:
            self.cfg.save()
        except OSError as exc:
            self.cfg.d["custom_radio"] = previous
            self.message.setText(str(exc))
            return
        self.spec = changed
        self._load()
        self.message.setText("This slot will use its original music, name and logo after the next build. "
                             "To restore every station immediately, disable Custom radio on the Mods page.")

    def _ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(self, "Locate FFmpeg executable", str(Path.home()))
        if path:
            self.ffmpeg.setText(path)
            self._save()

    def _build(self):
        if not self._can_build() or not self._save():
            return
        def job(task):
            try:
                return radio.build(self.cfg, lambda n, total, text: task.progress.emit(
                    n / total if total else 0, text), task.cancelled)
            except (OSError, ValueError) as exc:
                from ..installer import InstallError
                raise InstallError(f"Radio build stopped: {exc}") from exc
        self.task = Task(job, "build custom radio")
        if hasattr(self.window, "lock_radio_navigation"):
            self.window.lock_radio_navigation(True)
        self.task.progress.connect(self._progress)
        self.task.done.connect(self._done)
        self.editor.setEnabled(False)
        self.build_button.setEnabled(False)
        self.cancel_button.show()
        self.progress.setValue(0)
        self.progress.show()
        self.message.setText("Building stations… Your previous radio stays installed until this finishes.")
        self.task.start()

    def _progress(self, fraction, text):
        self.progress.setValue(int(fraction * 100))
        self.message.setText(text)

    def _done(self, ok, message):
        self.task.wait()
        self.task.deleteLater()
        self.task = None
        if hasattr(self.window, "lock_radio_navigation"):
            self.window.lock_radio_navigation(False)
        self.editor.setEnabled(True)
        self.cancel_button.hide()
        self.progress.hide()
        self.message.setText(message)
        self.refresh()
        self.idle.emit()

    def cancel(self):
        if self.task:
            self.task.cancel()
