"""Mods (#149): add and remove mods, turn them on or off, and set their load order.

FolderListPage is the list both this page and the Texture packs page (#384) show: folders in
the data folder, ticked to use, in an order where the lower one wins, added from a .zip or a
folder (or dropped on the page) and removed."""
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import GITHUB_URL, mods
from .pages import open_folder, scroll_page
from .widgets import card, label
from . import theme

DOCS_URL = f"{GITHUB_URL}/blob/main/docs/modding.md"
GUIDE_LINK = f'<a href="{DOCS_URL}" style="color: {theme.ORANGE};">the mod guide</a>'


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def _link_label(text, name="muted"):
    lb = label(text, name)
    lb.setTextFormat(Qt.RichText)
    lb.setOpenExternalLinks(True)
    lb.setTextInteractionFlags(Qt.TextBrowserInteraction)
    return lb


class FolderListPage(QWidget):
    """A page listing folders (mods, texture packs) to tick, order, add and remove. A subclass
    sets the words and COLUMNS, and implements the hooks below (scan, install, ...)."""
    COLUMNS = []
    NOUN = "mod"                # "Added …", "Remove mod"
    PLURAL = "mods"
    VERB = "load"               # "3 of 4 mod(s) will load"
    CONFLICT_UNIT = "file(s)"   # "Same file(s) as …"
    ZIP_FILTER = "Mod archives (*.zip)"

    def __init__(self, cfg, window, title, lead):
        super().__init__()
        self.cfg, self.window = cfg, window
        self.items = []
        self.clashes = {}
        self._filling = False
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc, self.page_layout = scroll_page(title, lead)
        outer.addWidget(sc)
        self.running_notice = label("The game is running: changes here apply the next time it starts.", "warn")
        self.running_notice.hide()
        self.page_layout.addWidget(self.running_notice)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._running_changed)
        self.timer.start()

    # ---- hooks --------------------------------------------------------------------------------
    def folder(self) -> Path:
        raise NotImplementedError

    def scan(self) -> list:
        """The items in their order, each on or off."""
        raise NotImplementedError

    def conflicts(self, items) -> dict:
        return mods.conflicts(items)

    def remember(self, items):
        raise NotImplementedError

    def install(self, path: Path, replace: bool):
        raise NotImplementedError

    def remove_folder(self, folder: str):
        raise NotImplementedError

    def row_texts(self, item) -> list:
        """The texts of the columns after the first (the title)."""
        raise NotImplementedError

    def empty_text(self) -> str:
        raise NotImplementedError

    def clash_name(self, name: str) -> str:
        return name

    def movable(self, item) -> bool:
        """Whether the item can change places (and others can take its place)."""
        return True

    def removable(self, item) -> bool:
        return True

    def detail_lines(self, item) -> list:
        """Lines shown under the summary of the selected item, before its problems and clashes."""
        return [item.description] if getattr(item, "description", "") else []

    def added_text(self, item) -> str:
        return f"Added {item.summary()} ({item.files} file(s))."

    def remove_question(self, item) -> str:
        return f'Remove "{item.title}"? Its folder {item.folder} is deleted from the {self.PLURAL} folder.'

    # ---- building -----------------------------------------------------------------------------
    def build_list(self, cl, add_text, folder_text):
        """The buttons, the table and the details of the list, into the card layout `cl`."""
        row = QHBoxLayout()
        row.setSpacing(12)
        self.add_button = QPushButton(add_text)
        self.add_button.clicked.connect(self.add_dialog)
        row.addWidget(self.add_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)
        row.addStretch()
        self.folder_button = QPushButton(folder_text)
        self.folder_button.clicked.connect(lambda: open_folder(self.folder()))
        row.addWidget(self.folder_button)
        cl.addLayout(row)

        self.empty = _link_label("")
        cl.addWidget(self.empty)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setAccessibleName(self.PLURAL.capitalize())
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(self.COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(220)
        self.table.setStyleSheet(f'''
            QTableWidget {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER};
                           gridline-color: {theme.BORDER}; selection-background-color: {theme.BORDER}; }}
            QTableWidget::item {{ padding: 8px; }}
            QTableWidget::item:selected {{ background: {theme.BORDER}; color: {theme.CREAM}; }}
            QHeaderView::section {{ padding: 8px; background: {theme.PANEL2}; border: none;
                                    border-bottom: 1px solid {theme.BORDER}; color: {theme.MUTED}; }}
        ''')
        self.table.itemChanged.connect(self._toggled)
        self.table.itemSelectionChanged.connect(self._selected)
        # A controller's A arrives as Return (ui/gamepad.py), which a check box in a table
        # does not take: Return or Enter turns the selected item on or off.
        self.table.installEventFilter(self)
        cl.addWidget(self.table)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.up_button = QPushButton("Move up")
        self.up_button.clicked.connect(lambda: self.move(-1))
        row.addWidget(self.up_button)
        self.down_button = QPushButton("Move down")
        self.down_button.clicked.connect(lambda: self.move(1))
        row.addWidget(self.down_button)
        row.addStretch()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        row.addWidget(self.refresh_button)
        cl.addLayout(row)
        self.details = label("", "muted")
        cl.addWidget(self.details)
        self.message = label("", "muted")
        cl.addWidget(self.message)

    # ---- state ------------------------------------------------------------------------------
    def _game_running(self) -> bool:
        running = getattr(self.window, "running", None)
        return bool(running and running())

    def _running_changed(self):
        on = self._game_running()
        if self.running_notice.isHidden() == on:
            self.running_notice.setVisible(on)
            self._selected()

    def _say(self, text, error=False):
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self.message.setStyleSheet(f"color: {theme.ERR};" if error else "")

    # ---- the list ---------------------------------------------------------------------------
    def refresh(self):
        selected = self._current()
        self.items = self.scan()
        self.clashes = self.conflicts(self.items)
        self._fill(selected.folder if selected else None)
        self.empty.setText(self.empty_text())
        self.empty.setVisible(not self.items)
        for w in (self.table, self.up_button, self.down_button, self.details):
            w.setVisible(bool(self.items))
        if self.items:
            on = sum(1 for m in self.items if m.enabled and not m.problem)
            self._say(f"{on} of {len(self.items)} {self.NOUN}(s) will {self.VERB}. Folder: {self.folder()}")
        else:
            self._say("")
        self._running_changed()

    def _fill(self, select_folder=None):
        self._filling = True
        self.table.setRowCount(len(self.items))
        for r, m in enumerate(self.items):
            name = QTableWidgetItem(m.title + (f"  (will not {self.VERB})" if m.problem else ""))
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            name.setCheckState(Qt.Checked if m.enabled else Qt.Unchecked)
            name.setToolTip(m.problem or m.description or m.folder)
            self.table.setItem(r, 0, name)
            for col, text in enumerate(self.row_texts(m), start=1):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, col, item)
        self._filling = False
        rows = [i for i, m in enumerate(self.items) if m.folder == select_folder]
        if rows:
            self.table.selectRow(rows[0])
        self._selected()

    def _current(self):
        r = self.table.currentRow()
        return self.items[r] if 0 <= r < len(self.items) else None

    def _toggled(self, item):
        if self._filling or item.column() != 0:
            return
        m = self.items[item.row()]
        m.enabled = item.checkState() == Qt.Checked
        self._save()
        self.refresh()

    def eventFilter(self, obj, event):
        if (obj is self.table and event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)):
            item = self.table.item(self.table.currentRow(), 0)
            if item is not None:
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
            return True
        return super().eventFilter(obj, event)

    def move(self, step):
        r = self.table.currentRow()
        to = r + step
        if not (0 <= r < len(self.items) and 0 <= to < len(self.items)):
            return
        if not (self.movable(self.items[r]) and self.movable(self.items[to])):
            return
        self.items[r], self.items[to] = self.items[to], self.items[r]
        self._save()
        self.clashes = self.conflicts(self.items)
        self._fill(self.items[to].folder)

    def _save(self):
        try:
            self.remember(self.items)
        except OSError as e:
            self._say(f"Could not save the {self.NOUN} list: {e}", error=True)

    def _selected(self):
        m = self._current()
        r = self.table.currentRow()
        self.up_button.setEnabled(m is not None and r > 0 and self.movable(m) and self.movable(self.items[r - 1]))
        self.down_button.setEnabled(m is not None and r < len(self.items) - 1 and self.movable(m)
                                    and self.movable(self.items[r + 1]))
        self.remove_button.setEnabled(m is not None and not self._game_running() and self.removable(m))
        if m is None:
            self.details.setText(f"Select a {self.NOUN} to see its details." if self.items else "")
            return
        lines = [m.summary()] + self.detail_lines(m)
        lines.append(f"Folder: {m.folder}")
        if m.problem:
            lines.append(f"Will not {self.VERB}: {m.problem}")
        elif not m.enabled:
            lines.append(f"Turned off: tick it to {self.VERB} it.")
        if m.note:
            lines.append(m.note)
        clashes = self.clashes.get(m.folder, [])
        if clashes:
            titles = {x.folder: x.title for x in self.items}
            # The one whose copy is used, per file: with three or more, maybe neither of a pair.
            winner = {name: folder for folder, entries in self.clashes.items()
                      for name, _other, wins in entries if wins}
            by_other = {}
            for name, other, wins in clashes:
                by_other.setdefault((other, m.folder if wins else winner.get(name, other)), []).append(name)
            for (other, used), names in by_other.items():
                shown = ", ".join(self.clash_name(n) for n in names[:3]) + (", …" if len(names) > 3 else "")
                count = f" ({len(names)})" if len(names) > 3 else ""
                if used == m.folder:
                    who = f"this {self.NOUN}'s copy is used (it is lower than {titles[other]})"
                elif used == other:
                    who = f"{titles[other]}'s copy is used (it is lower in the list)"
                else:
                    who = f"{titles[used]}'s copy is used (it is the lowest in the list)"
                lines.append(f"Same {self.CONFLICT_UNIT} as {titles[other]}{count}: {shown}; {who}.")
        self.details.setText("\n".join(lines))

    # ---- adding and removing ----------------------------------------------------------------
    def _confirm(self, title, text) -> bool:
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def add_dialog(self):
        noun = self.NOUN
        box = QMessageBox(self)
        box.setWindowTitle(f"Add {noun}")
        box.setText(f"Add a {noun} from a .zip file or from a folder?")
        zip_button = box.addButton("Zip file…", QMessageBox.AcceptRole)
        folder_button = box.addButton("Folder…", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is zip_button:
            path, _ = QFileDialog.getOpenFileName(self, f"{noun.capitalize()} to add", str(Path.home()),
                                                  self.ZIP_FILTER)
        elif box.clickedButton() is folder_button:
            path = QFileDialog.getExistingDirectory(self, f"{noun.capitalize()} folder to add", str(Path.home()))
        else:
            return
        if path:
            self.add_path(Path(path))

    def add_path(self, path: Path) -> bool:
        """Adds the item in `path` (a .zip or a folder); says how it went on the page."""
        noun = self.NOUN
        try:
            try:
                item = self.install(path, False)
            except mods.ModExists as clash:
                if self._game_running():
                    raise mods.InstallError(f'A {noun} named "{clash.folder}" is there already and the game '
                                            "is running: close the game to replace it.") from None
                if not self._confirm(f"Replace {noun}", f'A {noun} named "{clash.folder}" is there already. '
                                                        "Replace it with this one?"):
                    self._say(f'"{path.name}" was not added: "{clash.folder}" is kept.')
                    return False
                item = self.install(path, True)
        except mods.InstallError as e:
            self.refresh()
            self._say(f"Not added: {e}", error=True)
            return False
        self.refresh()
        rows = [i for i, m in enumerate(self.items) if m.folder == item.folder]
        if rows:
            self.table.selectRow(rows[0])
        later = " It loads the next time the game starts." if self._game_running() else ""
        self._say(self.added_text(item) + later)
        return True

    def remove_selected(self):
        m = self._current()
        if m is None:
            return
        if self._game_running():
            self._say(f"Close the game before removing a {self.NOUN}: its files are in use.", error=True)
            return
        if not self._confirm(f"Remove {self.NOUN}", self.remove_question(m)):
            return
        try:
            self.remove_folder(m.folder)
        except (mods.InstallError, OSError) as e:
            self._say(str(e), error=True)
            return
        self.refresh()
        self._say(f"Removed {m.title}.")

    # ---- drag and drop ----------------------------------------------------------------------
    def dragEnterEvent(self, event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and all(u.isLocalFile() for u in urls):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.add_path(Path(url.toLocalFile()))
        event.acceptProposedAction()


class ModsPage(FolderListPage):
    COLUMNS = ["Mod", "Version", "Author", "Files"]

    def __init__(self, cfg, window):
        super().__init__(cfg, window, "Mods", "Change the game's files without editing them: each mod's "
                                              "files are laid over the game's archives when it starts.")
        lay = self.page_layout
        c, cl = card(spacing=14)
        cl.addWidget(label("Your mods", "cardTitle"))
        cl.addWidget(label("Ticked mods load from top to bottom. When two mods change the same file, "
                           "the one lower in the list wins. You can also drop a mod's .zip or folder "
                           "on this page.", "muted"))
        self.build_list(cl, "Add mod…", "Open mods folder")
        lay.addWidget(c)

        c, cl = card(spacing=8)
        cl.addWidget(label("How mods work", "cardTitle"))
        cl.addWidget(label("A mod is a folder (or a .zip of one) with its files under media/, at the "
                           "game's own paths, and usually a mod.toml with its name. Changes apply the "
                           "next time you press Play. The game files stay as they are, so a rebuild or "
                           "an update keeps your mods.", "muted"))
        cl.addWidget(_link_label(f"Installing, load order, what to do when a mod will not load, and making "
                                 f"your own: {GUIDE_LINK}."))
        lay.addWidget(c)
        lay.addStretch()
        self.refresh()

    @property
    def mods(self):
        return self.items

    def folder(self):
        return mods.mods_dir(self.cfg)

    def scan(self):
        return mods.ordered(self.cfg, mods.scan(mods.mods_dir(self.cfg), mods.game_media(self.cfg)))

    def remember(self, items):
        mods.remember(self.cfg, items)

    def install(self, path, replace):
        return mods.install(path, mods.mods_dir(self.cfg), replace=replace, media_of_game=mods.game_media(self.cfg))

    def remove_folder(self, folder):
        mods.remove(self.cfg, folder)

    def row_texts(self, m):
        return [m.version, m.author, f"{m.files} ({_size(m.size)})"]

    def empty_text(self):
        return (f"No mods yet. Use <b>Add mod…</b> or put a mod folder in {mods.mods_dir(self.cfg)}. "
                f"How to install and make mods: {GUIDE_LINK}.")

    def clash_name(self, name):
        return f"media/{name}"
