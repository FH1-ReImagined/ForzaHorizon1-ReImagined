"""Station editor for independently built local radio overlays."""
from pathlib import Path
import copy

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

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
            self.spec = {"version": 1, "ffmpeg": "", "level": True, "stations": {}}
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
        export = QPushButton("Export original logos…")
        export.setToolTip("Saves the game's own station logos from your game files as PNGs, "
                          "with their size, format and transparency, to match your logo to them.")
        export.clicked.connect(self._export_logos)
        logos.addWidget(choose)
        logos.addWidget(clear)
        logos.addWidget(export)
        form.addRow("", logos)
        self.songs = QListWidget()
        self.songs.setSelectionMode(QListWidget.ExtendedSelection)
        self.songs.itemDoubleClicked.connect(self._edit)
        form.addRow("Songs", self.songs)
        buttons = QHBoxLayout()
        for text, callback in (("Add songs…", self._songs), ("Add folder…", self._folder),
                               ("Edit artist and title…", self._edit), ("Remove selected", self._remove)):
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
        self.level = QCheckBox("Level songs to the game's loudness")
        self.level.setChecked(self.spec["level"])
        self.level.setToolTip("Turns each song down (or up) to the level of the game's own soundtrack, "
                              "so loud songs don't distort at a high radio volume. "
                              "Off keeps every song at its original level.")
        self.level.toggled.connect(self._level)
        form.addRow("Loudness", self.level)
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
        else:
            self._stale_notice()

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
        self._tags = copy.deepcopy(station.get("tags", {}))
        self.songs.clear()
        for song in station.get("songs", []):
            self._add_item(song)
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
        self.spec["level"] = self.level.isChecked()
        # Visiting a stock slot alone does not create a custom station.
        if (self.songs.count() or self._slot in self.spec["stations"] or self._logo_path
                or self.mode.currentData() == "add"
                or self.name.text().strip() != radio.TITLES[radio.SLOTS.index(self._slot)]):
            self.spec["stations"][self._slot] = {
                "name": self.name.text().strip(), "mode": self.mode.currentData(),
                "songs": self._paths(),
                "logo": self._logo_path, "tags": copy.deepcopy(self._tags)}
        try:
            self.cfg.d["custom_radio"] = self.spec
            self.spec = radio.settings(self.cfg)
            self.cfg.save()
            for i in range(self.songs.count()):  # A new station name is the default artist.
                self._show(self.songs.item(i))
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

    def _paths(self):
        return [self.songs.item(i).data(Qt.UserRole) for i in range(self.songs.count())]

    def _add_item(self, song):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, song)
        self.songs.addItem(item)
        self._show(item)

    def _show(self, item):
        song = item.data(Qt.UserRole)
        tag = self._tags.get(song)
        if tag:
            station = {"name": self.name.text().strip(), "tags": self._tags}
            title, artist = radio.song_names(station, song)
            item.setText(f"{artist} – {title}    ({Path(song).name})")
        else:
            item.setText(song)
        item.setToolTip(song)

    def _add(self, files):
        existing = set(self._paths())
        for song in dict.fromkeys(files):
            if song not in existing:
                self._add_item(song)
        self._save()

    def _edit(self, item=None):
        """Artist and title the in-game radio shows for one song (#678)."""
        if not isinstance(item, QListWidgetItem):
            items = self.songs.selectedItems() or ([self.songs.currentItem()] if self.songs.currentItem() else [])
            if len(items) != 1:
                self.message.setText("Select one song to edit its artist and title.")
                return
            item = items[0]
        song = item.data(Qt.UserRole)
        names = self._tags.get(song) or radio.read_tags(song, radio.ffprobe_path(self.ffmpeg.text().strip()))
        dialog = QDialog(self)
        dialog.setWindowTitle("Artist and title")
        form = QFormLayout(dialog)
        form.addRow(label(f"Shown on the in-game radio for {Path(song).name}. "
                          "Leave a field empty to use the default.", "muted"))
        artist, title = QLineEdit(names.get("artist", "")), QLineEdit(names.get("title", ""))
        for field, default in ((artist, self.name.text().strip()), (title, Path(song).stem)):
            field.setMaxLength(radio.MAX_TAG)
            field.setPlaceholderText(default)
        form.addRow("Artist", artist)
        form.addRow("Title", title)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        self._edit_dialog = dialog  # Tests answer the dialog through this handle.
        accepted = dialog.exec() == QDialog.Accepted
        self._edit_dialog = None
        if accepted:
            self._set_tags(song, artist.text(), title.text())
        dialog.deleteLater()

    def _set_tags(self, song, artist, title):
        previous = copy.deepcopy(self._tags)
        tag = {k: " ".join(v.split()) for k, v in (("artist", artist), ("title", title)) if v.strip()}
        if tag:
            self._tags[song] = tag
        else:
            self._tags.pop(song, None)
        if not self._save():
            self._tags = previous
            for i in range(self.songs.count()):
                self._show(self.songs.item(i))

    def _export_logos(self):
        folder = QFileDialog.getExistingDirectory(self, "Folder for the original logos", str(Path.home()))
        if not folder:
            return
        try:
            count = radio.export_stock_logos(self.cfg, folder)
        except (OSError, ValueError) as exc:
            self.message.setText(f"Could not export the logos: {exc}")
            return
        self.message.setText(f"Saved {count} original logos and stock-logos.json (sizes, format and "
                             f"transparency) to {folder}. They come from your game files: keep them to yourself.")

    def _remove(self):
        for item in self.songs.selectedItems():
            self._tags.pop(item.data(Qt.UserRole), None)
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

    def _level(self, checked):
        if self._save():
            self._stale_notice()
        else:
            self.level.blockSignals(True)
            self.level.setChecked(not checked)
            self.level.blockSignals(False)

    def _stale_notice(self):
        # Stations built before leveling (#662), or with the other setting, need one rebuild.
        if radio.stale(self.cfg):
            self.message.setText("Build stations again to " + (
                "level your songs to the game's loudness." if self.spec["level"]
                else "keep your songs at their original level."))

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
