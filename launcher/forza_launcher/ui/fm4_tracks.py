"""Forza Motorsport 4 circuits (#648, experimental) and Forza Horizon 2's worlds (#821,
experimental): their import cards on Mods > Mods, next to the FM4 cars, and the choice of where
Play starts (TrackPicker, on the Play page once one is imported). fm4tracks.py and fh2ffworld.py
do the work."""
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout,
                               QHeaderView, QLabel, QListWidget, QListWidgetItem, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import fh2ffworld, fm4tracks
from .widgets import card, label

EXPERIMENTAL_TAG = "Experimental"


class ChooseCircuits(QDialog):
    """Which of Disc 1's circuits to import, with the space they take."""

    def __init__(self, parent, disc, have, free):
        super().__init__(parent)
        self.disc, self.free = disc, free
        self.setWindowTitle("Import FM4 tracks")
        lay = QVBoxLayout(self)
        lay.addWidget(label(f"Your Forza Motorsport 4 Disc 1 has {len(disc.circuits)} circuits. Each is "
                            "converted on this PC (under a minute for most) and takes about 3 times its "
                            "size on the disc. Imported ones are replaced.", "muted"))
        self.list = QListWidget()
        for folder in disc.circuits:
            kept, _ = fm4tracks.estimate(disc, [folder])
            name = fm4tracks.display_name(folder)
            text = f"{name}   (about {fm4tracks.size_text(kept)})"
            if fm4tracks.key_of(folder) in have:
                text += "   imported: import again to replace it"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, folder)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if folder == "Suzuka" and "suzuka" not in have else Qt.Unchecked)
            self.list.addItem(item)
        self.list.itemChanged.connect(self._changed)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, state in (("All", Qt.Checked), ("None", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self._all(s))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.total = label("", "muted")
        lay.addWidget(self.total)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.go = self.buttons.addButton("Import", QDialogButtonBox.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.resize(520, 560)
        self._changed()

    def _all(self, state):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self._changed()

    def chosen(self) -> list:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def _changed(self, *_):
        names = self.chosen()
        kept, need = fm4tracks.estimate(self.disc, names)
        free = f", {fm4tracks.size_text(self.free)} free" if self.free is not None else ""
        self.total.setText(f"{len(names)} circuit(s): about {fm4tracks.size_text(kept)} once imported, "
                           f"{fm4tracks.size_text(need)} while converting{free}." if names else
                           "Tick the circuits to import.")
        self.go.setEnabled(bool(names) and (self.free is None or need < self.free))


class Fm4TracksCard(QWidget):
    """Mods > Mods: Import FM4 tracks, the imported circuits with their sizes, Remove.
    (Fh2WorldsCard below is the same card for Forza Horizon 2's worlds.)"""
    changed = Signal()
    TITLE = f"Forza Motorsport 4 tracks ({EXPERIMENTAL_TAG.lower()})"
    INTRO = ("Drive the circuits of your own Forza Motorsport 4 Disc 1 (a disc image, a Games "
             "on Demand package, an archive or a folder with its files) in FH1. Choose all of "
             "them or a few; they are converted on this PC. Then pick one on the Play page "
             "before you press Play: FH1's Colorado stays the default.")
    WARNING = fm4tracks.EXPERIMENTAL
    COLUMNS = ["Circuit", "Size", "Imported", "Best lap"]
    THING = "circuit"

    def _mine(self, info) -> bool:
        """Whether an import belongs in this card's list."""
        return info.get("kind") not in fh2ffworld.KINDS

    def _import_buttons(self) -> list:
        self.import_button = QPushButton("Import FM4 tracks…")
        self.import_button.clicked.connect(self._import)
        return [self.import_button]

    def __init__(self, cfg, page):
        super().__init__()
        self.cfg, self.page = cfg, page
        self.task = None
        self.disc = None
        self.work = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        c, cl = card(spacing=10)
        cl.addWidget(label(self.TITLE, "cardTitle"))
        cl.addWidget(label(self.INTRO, "muted"))
        cl.addWidget(label(self.WARNING, "warn"))
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, len(self.COLUMNS)):      # sizes, dates and laps shown whole
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.table.setMaximumHeight(220)
        self.table.setSizeAdjustPolicy(QTableWidget.AdjustToContents)
        cl.addWidget(self.table)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.import_buttons = self._import_buttons()
        for button in self.import_buttons:
            row.addWidget(button)
        self.remove_button = QPushButton(f"Remove {self.THING}")
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.remove_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(lambda: self.task and self.task.cancel())
        self.cancel_button.hide()
        row.addWidget(self.cancel_button)
        row.addStretch()
        cl.addLayout(row)
        self.progress = QProgressBar()
        self.progress.hide()
        cl.addWidget(self.progress)
        self.message = label("", "muted")
        cl.addWidget(self.message)
        outer.addWidget(c)
        self.refresh()

    def refresh(self):
        tracks = [info for info in fm4tracks.installed(self.cfg) if self._mine(info)]
        self.table.setRowCount(len(tracks))
        for r, info in enumerate(tracks):
            name = QTableWidgetItem(info.get("name", info["key"]))
            name.setData(Qt.UserRole, info["key"])
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(fm4tracks.size_text(int(info.get("size", 0)))))
            self.table.setItem(r, 2, QTableWidgetItem(info.get("imported", "")))
            if len(self.COLUMNS) > 3:
                self.table.setItem(r, 3, QTableWidgetItem(fm4tracks.lap_text(fm4tracks.best_lap(self.cfg, info["key"]))))
        self.table.setVisible(bool(tracks))
        if self.task is None:
            size = fm4tracks.size_text(sum(int(info.get("size", 0)) for info in tracks))
            self.message.setText(f"{len(tracks)} {self.THING}(s) imported, {size} in all." if tracks else "")
        self._buttons()

    def _buttons(self):
        idle = self.task is None
        for button in self.import_buttons:
            button.setEnabled(idle)
        self.remove_button.setEnabled(idle and bool(self.table.selectedItems()))
        self.remove_button.setVisible(self.table.rowCount() > 0)

    def _selected_key(self):
        rows = self.table.selectionModel().selectedRows()
        return self.table.item(rows[0].row(), 0).data(Qt.UserRole) if rows else None

    def _remove(self):
        key = self._selected_key()
        if not key:
            return
        if self.page._game_running():
            self.message.setText(f"Close the game before removing a {self.THING}.")
            return
        info = fm4tracks.find(self.cfg, key) or {"name": key, "size": 0}
        if not self.page._confirm(f"Remove {self.THING}", f"Remove {info['name']} "
                                  f"({fm4tracks.size_text(int(info.get('size', 0)))})? You can import it "
                                  "again from your own files."):
            return
        try:
            fm4tracks.remove(self.cfg, key)
        except (fm4tracks.Fm4Error, OSError) as e:
            self.message.setText(str(e))
            return
        self.refresh()
        self.changed.emit()
        self.message.setText(f"Removed {info['name']}.")

    def _start(self, job, name, text, done):
        from .workers import Task
        self.task = Task(job, name)
        self.task.progress.connect(self._progress)
        self.task.done.connect(done)
        self.cancel_button.show()
        self.progress.setValue(0)
        self.progress.show()
        self.message.setText(text)
        self._buttons()
        self.task.start()

    def _progress(self, fraction, text):
        self.progress.setValue(int(fraction * 100))
        self.message.setText(text)

    def _end(self):
        self.task.wait()
        self.task.deleteLater()
        self.task = None
        self.cancel_button.hide()
        self.progress.hide()

    def _drop_work(self):
        if self.work is not None:
            shutil.rmtree(self.work, ignore_errors=True)
        self.work, self.disc = None, None

    def _import(self):
        if self.page._game_running():
            self.message.setText("Close the game before importing circuits.")
            return
        disc1 = self.page._fm4_pick("Forza Motorsport 4 Disc 1")
        if not disc1:
            return
        root = fm4tracks.store(self.cfg)
        root.mkdir(parents=True, exist_ok=True)
        self._drop_work()
        self.work = Path(tempfile.mkdtemp(prefix=".disc-", dir=root))

        def job(task):
            try:
                self.disc = fm4tracks.open_disc(disc1, self.work, lambda n, total, text: task.progress.emit(
                    n / total if total else 0, text), task.cancelled)
            except (OSError, ValueError) as exc:
                from ..installer import InstallError
                raise InstallError(f"Import FM4 tracks stopped: {exc}") from exc
            return ""
        self._start(job, "read FM4 Disc 1", "Reading Disc 1…", self._read_done)

    def _read_done(self, ok, message):
        self._end()
        if not ok or self.disc is None:
            self._drop_work()
            self.refresh()
            self.message.setText(message)
            return
        if not self.disc.circuits:
            self._drop_work()
            self.refresh()
            self.message.setText("This Disc 1 holds no circuit the import knows.")
            return
        try:
            free = shutil.disk_usage(fm4tracks.store(self.cfg)).free
        except OSError:
            free = None
        have = {i["key"] for i in fm4tracks.installed(self.cfg)}
        dialog = ChooseCircuits(self, self.disc, have, free)
        if dialog.exec() != QDialog.Accepted or not dialog.chosen():
            self._drop_work()
            self.refresh()
            return
        names, disc = dialog.chosen(), self.disc

        def job(task):
            try:
                return fm4tracks.build(self.cfg, disc, names, lambda f, text: task.progress.emit(f, text),
                                       task.cancelled)
            except (OSError, ValueError) as exc:
                from ..installer import InstallError
                raise InstallError(f"Import FM4 tracks stopped: {exc}") from exc
        self._start(job, "import FM4 tracks", f"Converting {len(names)} circuit(s)… Circuits imported "
                    "before stay until each new one is complete.", self._build_done)

    def _build_done(self, ok, message):
        self._end()
        self._drop_work()
        self.refresh()
        self.changed.emit()
        self.message.setText(message)


class Fh2WorldsCard(Fm4TracksCard):
    """Mods > Mods: Forza Horizon 2's worlds (#821, fh2ffworld.py): the imports, the imported
    worlds with their sizes, Remove. They share the circuits' store and the Play page's picker."""
    TITLE = f"Forza Horizon 2 worlds ({EXPERIMENTAL_TAG.lower()})"
    INTRO = ("Free roam in the worlds of Forza Horizon 2 (Xbox 360) and Forza Horizon 2 Presents Fast & "
             "Furious (Xbox 360), each from a folder with the files of your own copy. A world is converted "
             "on this PC. Then pick it on the Play page before you press Play: FH1's Colorado stays the "
             "default.")
    WARNING = fh2ffworld.EXPERIMENTAL
    COLUMNS = ["World", "Size", "Imported"]
    THING = "world"

    def _mine(self, info) -> bool:
        return info.get("kind") in fh2ffworld.KINDS

    def _import_buttons(self) -> list:
        self.ff_button = QPushButton("Import Fast && Furious…")   # a button's single & is a shortcut mark, not shown
        self.ff_button.setToolTip("Free roam in Forza Horizon 2 Presents Fast & Furious's world, from your own "
                                  "copy's files (experimental). " + fh2ffworld.EXPERIMENTAL)
        self.ff_button.clicked.connect(self._import_ff)
        self.fh2_button = QPushButton("Import Forza Horizon 2…")
        self.fh2_button.setToolTip("Free roam in Forza Horizon 2's whole world, from your own copy's files "
                                   "(experimental). " + fh2ffworld.EXPERIMENTAL)
        self.fh2_button.clicked.connect(self._import_fh2)
        return [self.ff_button, self.fh2_button]

    def _import_ff(self):
        """Fast & Furious's world (fh2ffworld.py): its files' folder, then FH2's (optional)."""
        from PySide6.QtWidgets import QFileDialog
        if self.page._game_running():
            self.message.setText("Close the game before importing.")
            return
        ff = QFileDialog.getExistingDirectory(self, "Forza Horizon 2 Presents Fast & Furious: the folder of its files")
        if not ff:
            return
        try:
            fh2ffworld.disc_root(ff)
        except fh2ffworld.Fh2ffError as e:
            self.message.setText(str(e))
            return
        fh2 = None
        if self.page._confirm("Forza Horizon 2 (optional)", "Do you have Forza Horizon 2's files too? With them "
                              "the full map shows FH2's map art and the roads Fast & Furious closed get "
                              "FH2's road types. Choose No to import without them."):
            fh2 = QFileDialog.getExistingDirectory(self, "Forza Horizon 2: the folder of its files") or None
            try:
                fh2ffworld.fh2_root(fh2)
            except fh2ffworld.Fh2ffError as e:
                self.message.setText(str(e))
                return

        def job(task):
            try:
                return fh2ffworld.build(self.cfg, ff, fh2, lambda f, text: task.progress.emit(f, text), task.cancelled)
            except (OSError, ValueError) as exc:
                from ..installer import InstallError
                raise InstallError(f"Import Fast & Furious stopped: {exc}") from exc
        self._start(job, "import Fast & Furious", "Converting Fast & Furious's world… An earlier import stays "
                    "until the new one is complete.", self._build_done)

    def _import_fh2(self):
        """Forza Horizon 2's own world (fh2ffworld.py, key fh2): its files' folder."""
        from PySide6.QtWidgets import QFileDialog
        if self.page._game_running():
            self.message.setText("Close the game before importing.")
            return
        fh2 = QFileDialog.getExistingDirectory(self, "Forza Horizon 2: the folder of its files")
        if not fh2:
            return
        try:
            fh2ffworld.fh2_root(fh2, optional=False)
        except fh2ffworld.Fh2ffError as e:
            self.message.setText(str(e))
            return

        def job(task):
            try:
                return fh2ffworld.build(self.cfg, fh2, None, lambda f, text: task.progress.emit(f, text), task.cancelled,
                                        key="fh2")
            except (OSError, ValueError) as exc:
                from ..installer import InstallError
                raise InstallError(f"Import Forza Horizon 2 stopped: {exc}") from exc
        self._start(job, "import Forza Horizon 2", "Converting Forza Horizon 2's world (about 5 minutes)… An "
                    "earlier import stays until the new one is complete.", self._build_done)

class TrackPicker(QWidget):
    """Play page: where Play starts. Shown only once an FM4 circuit is imported."""

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("Drive in"))
        self.combo = QComboBox()
        self.combo.setAccessibleName("Where Play starts")
        self.combo.setToolTip("FH1's own Colorado, or a Forza Motorsport 4 circuit or a Forza Horizon 2 world "
                              "you imported (Mods > Mods).")
        self.combo.currentIndexChanged.connect(self._chosen)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)
        self.note = label(fm4tracks.EXPERIMENTAL_SHORT, "warn")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self._filling = False
        self.refresh()

    def refresh(self):
        items = fm4tracks.choices(self.cfg)
        current = fm4tracks.selected(self.cfg)
        shown = [(self.combo.itemData(i), self.combo.itemText(i)) for i in range(self.combo.count())]
        self._filling = True
        if shown != items:  # the Play page refreshes often: an open list is left alone
            self.combo.clear()
            for key, text in items:
                self.combo.addItem(text, key)
        if self.combo.currentData() != current:
            self.combo.setCurrentIndex(max(0, self.combo.findData(current)))
        self._filling = False
        self.setVisible(len(items) > 1)
        self._note(current)

    def _note(self, key):
        """The chosen place's own note: a circuit's or a world's."""
        info = fm4tracks.find(self.cfg, key) if key else None
        world = bool(info) and info.get("kind") in fh2ffworld.KINDS
        self.note.setText(fh2ffworld.EXPERIMENTAL_SHORT if world else fm4tracks.EXPERIMENTAL_SHORT)
        self.note.setVisible(bool(key))

    def _chosen(self, _index):
        if self._filling:
            return
        key = self.combo.currentData() or ""
        try:
            fm4tracks.select(self.cfg, key)
        except fm4tracks.Fm4Error:
            key = ""
        self._note(key)


class Fm4LapsPage(QWidget):
    """Mods > FM4 laps: the laps the game timed on each FM4 circuit (fm4tracks.laps), best first
    then newest. The tab shows only once a circuit is imported or has laps."""

    def __init__(self, cfg, window):
        super().__init__()
        self.cfg, self.window = cfg, window
        from .pages import scroll_page
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc, self.page_layout = scroll_page("FM4 laps", "Your timed laps on the Forza Motorsport 4 circuits "
                                                       "(experimental): a lap counts when it passes every "
                                                       "sector in order, without a rewind or a shortcut.")
        outer.addWidget(sc)
        self.cards = QVBoxLayout()
        self.cards.setSpacing(14)
        self.page_layout.addLayout(self.cards)
        self.empty = label("No laps yet. Import a circuit under Mods > Mods > Forza Motorsport 4 tracks, "
                           "choose it on the Play page and cross its start line.", "muted")
        self.page_layout.addWidget(self.empty)
        self.page_layout.addStretch(1)
        self.refresh()

    def tab_visible(self) -> bool:
        return bool(fm4tracks.lap_circuits(self.cfg))

    def refresh(self):
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        any_laps = False
        for key, name in fm4tracks.lap_circuits(self.cfg):
            laps = fm4tracks.laps(self.cfg, key)
            c, cl = card(spacing=8)
            best = min(laps, key=lambda lap: lap["time"]) if laps else None
            title = name + (f"   best {fm4tracks.lap_text(best['time'])}" if best else "")
            cl.addWidget(label(title, "cardTitle"))
            if not laps:
                cl.addWidget(label("No timed lap here yet.", "muted"))
                self.cards.addWidget(c)
                continue
            any_laps = True
            table = QTableWidget(0, 4)
            table.setHorizontalHeaderLabels(["Lap time", "Sectors", "Date", ""])
            table.verticalHeader().hide()
            table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            table.setSelectionMode(QAbstractItemView.NoSelection)
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
            rows = [best] + [lap for lap in reversed(laps) if lap is not best][:49]
            table.setRowCount(len(rows))
            for r, lap in enumerate(rows):
                sectors = lap.get("sectors") or []
                # the sector times as each sector's own time (the file keeps the lap time at each end)
                ends = [x for x in sectors if isinstance(x, (int, float))] + [lap["time"]]
                own = [b - a for a, b in zip([0.0] + ends[:-1], ends)]
                cells = [fm4tracks.lap_text(lap["time"]), "   ".join(fm4tracks.lap_text(x) for x in own),
                         str(lap.get("date", "")), "best" if lap is best else ""]
                for col, text in enumerate(cells):
                    table.setItem(r, col, QTableWidgetItem(text))
            table.setMaximumHeight(320)
            table.setSizeAdjustPolicy(QTableWidget.AdjustToContents)
            cl.addWidget(table)
            self.cards.addWidget(c)
        self.empty.setVisible(not any_laps)
