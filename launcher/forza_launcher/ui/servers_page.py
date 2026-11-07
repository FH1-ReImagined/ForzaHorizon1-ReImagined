"""Servers (#587): pick the Forza Live server Play uses, from the main server's signed list, This PC
or an address added by hand. Each server's players online, ping and whether it is up come from its
status page, asked off the GUI thread when the page opens and every 30 seconds while it stays open
(server_list.py)."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import server_list
from .pages import scroll_page
from .widgets import card, label
from .workers import Probe
from . import theme

COLUMNS = ["Server", "Region", "Players", "Ping", "Status"]
LEAD = ("The server the game connects to when you press Play, or Offline. The main server is the shared "
        "online world. Community servers are run by their owners and keep their own leaderboards: only the "
        "main server's are the official ones.")
OFFLINE = "(offline)"   # the Offline row's address: no server (cfg.online mode "offline")


class ServersPage(QWidget):
    changed = Signal()   # cfg.online changed (a server picked, added or removed): save it

    def __init__(self, cfg, window, fetch=None, ping_all=None):
        super().__init__()
        self.cfg, self.window = cfg, window
        self._fetch, self._ping_all = fetch, ping_all  # None: server_list's (looked up at each check)
        self.listed = []        # [Server] of the last list that checked out
        self.statuses = {}      # address -> Status of the last check
        self.list_note = ""
        self.rows = []
        self._probe = None      # the check running, if any
        self._list_due = True   # fetch the list again at the next check
        self.checks = 0         # finished checks (tests)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        sc, lay = scroll_page("Servers", LEAD)
        outer.addWidget(sc)

        frame, cl = card()
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setAccessibleName("Servers")
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(COLUMNS)):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(260)
        self.table.setStyleSheet(f'''
            QTableWidget {{ background: {theme.PANEL}; border: 1px solid {theme.BORDER};
                           gridline-color: {theme.BORDER}; selection-background-color: {theme.BORDER}; }}
            QTableWidget::item {{ padding: 8px; }}
            QTableWidget::item:selected {{ background: {theme.BORDER}; color: {theme.CREAM}; }}
            QHeaderView::section {{ padding: 8px; background: {theme.PANEL2}; border: none;
                                    border-bottom: 1px solid {theme.BORDER}; color: {theme.MUTED}; }}
        ''')
        self.table.itemSelectionChanged.connect(self._selected)
        self.table.itemDoubleClicked.connect(lambda _item: self.use_selected())
        cl.addWidget(self.table)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.use_button = QPushButton("Use this server")
        self.use_button.clicked.connect(self.use_selected)
        row.addWidget(self.use_button)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_button)
        row.addStretch()
        self.check_button = QPushButton("Check servers")
        self.check_button.clicked.connect(self.check_now)
        row.addWidget(self.check_button)
        cl.addLayout(row)
        self.details = label("", "muted")
        cl.addWidget(self.details)
        self.note = label("", "muted")
        cl.addWidget(self.note)
        lay.addWidget(frame)

        frame, cl = card()
        cl.addWidget(label("Add a server", "cardTitle"))
        cl.addWidget(label("A server on your network or a friend's: its address as host or host:port. "
                           "It is added to this list on this PC only.", "help"))
        row = QHBoxLayout()
        row.setSpacing(12)
        self.address = QLineEdit()
        self.address.setPlaceholderText("host[:port], for example example.org:21200")
        self.address.returnPressed.connect(self.add_typed)
        row.addWidget(self.address, 1)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self.add_typed)
        row.addWidget(self.add_button)
        cl.addLayout(row)
        self.message = label("", "muted")
        self.message.hide()
        cl.addWidget(self.message)
        lay.addWidget(frame)
        lay.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(server_list.PING_SECONDS * 1000)
        self.timer.timeout.connect(self.check)
        self.fill()

    # ---- opening and leaving ------------------------------------------------------------------
    def auto_check(self) -> bool:
        """Whether the page checks the servers on its own: not after "Online features" was turned
        off (PRIVACY.md), where "Check servers" starts it."""
        return self.cfg.d.get("online_features", {}).get("on") is not False

    def refresh(self):
        """The page was opened."""
        self.fill()
        if self.auto_check():
            self.check(with_list=True)
            self.timer.start()

    def leave(self):
        """The page was left: no more checks until it opens again."""
        self.timer.stop()

    def hideEvent(self, e):
        self.leave()
        super().hideEvent(e)

    # ---- checking ----------------------------------------------------------------------------
    def busy(self) -> bool:
        return self._probe is not None

    def check_now(self):
        """Check servers: also with Online features off, and then every 30 s while the page is open."""
        self.check(with_list=True)
        self.timer.start()

    def check(self, with_list=False):
        """Pings every server on the page in a background thread; with `with_list` (when the page
        opens, and Check servers) fetches the signed list first."""
        if self.busy():
            return
        with_list = with_list or self._list_due
        listed, added = list(self.listed), server_list.page_addresses(self.cfg)
        fetch = self._fetch or server_list.fetch_list
        ping_all = self._ping_all or server_list.ping_all

        def work():
            found, note = listed, None
            if with_list:
                try:
                    found, note = fetch(), ""
                except server_list.ListError as e:
                    note = str(e)
            rows = server_list.entries(found, added)
            return found, note, ping_all([r.address for r in rows])

        self.check_button.setEnabled(False)
        self.note.setText("Checking the servers…")
        self._probe = Probe(work, "server check", self)
        self._probe.result.connect(self._checked)
        self._probe.start()

    def _checked(self, result):
        self._probe = None
        self.checks += 1
        self.check_button.setEnabled(True)
        if result is None:   # raised (logged)
            self.note.setText("Could not check the servers. The launcher log has the details.")
            return
        found, note, statuses = result
        if note is not None:
            self.listed, self.list_note = found, note
            self._list_due = bool(note)
        self.statuses = statuses
        self.fill()

    # ---- the table ---------------------------------------------------------------------------
    def fill(self):
        keep = self._current()
        offline = self.cfg.online.get("mode") == "offline"
        chosen = OFFLINE if offline else server_list.chosen(self.cfg)
        self.rows = server_list.entries(self.listed, server_list.page_addresses(self.cfg))
        self.rows.append(server_list.Server("Offline", OFFLINE, kind="offline"))
        self.table.setRowCount(len(self.rows))
        self.table.setMinimumHeight(48 + 44 * min(len(self.rows), 8))  # up to 8 rows without scrolling
        for i, s in enumerate(self.rows):
            st = self.statuses.get(s.address)
            in_use = s.address == chosen or (s.kind == "main" and server_list.is_main(chosen))
            name = s.name + ("  (in use)" if in_use else "")
            if s.kind == "offline":
                players, ping, state = "", "", "No Xbox LIVE, no server"
            elif s.kind == "local":
                players, ping, state = "", "", "Starts with the game"
            elif st is None:
                players, ping, state = "", "", "Checking…" if self.busy() else ""
            elif st.up:
                players = "" if st.players is None else str(st.players)
                ping, state = f"{st.ping_ms} ms", "Up"
            else:
                players, ping, state = "", "", "Down"
            region = s.region or ("Added by you" if s.kind == "added" else "")
            for col, text in enumerate((name, region, players, ping, state)):
                item = QTableWidgetItem(text)
                item.setData(Qt.UserRole, s.address)
                if col == 0 and in_use:
                    font = QFont(item.font())
                    font.setBold(True)
                    item.setFont(font)
                if col == 4 and state in ("Up", "Down"):
                    item.setForeground(QColor(theme.OK if state == "Up" else theme.ERR))
                if col == 0:
                    item.setToolTip({"local": "A server on this PC, only for you",
                                     "offline": "Play without a server"}.get(s.kind, s.address))
                self.table.setItem(i, col, item)
        self._select(keep or chosen or self.rows[0].address)
        if self.busy():
            self.note.setText("Checking the servers…")
        elif self.list_note:
            self.note.setText(f"The server list is not available: {self.list_note}. "
                              "The main server and the servers you added are still shown.")
        elif not self.auto_check() and not self.statuses:
            self.note.setText('Online features are off: "Check servers" pings each server and gets the '
                              "list from the main server.")
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        self._selected()

    def _select(self, address):
        for i, s in enumerate(self.rows):
            if s.address == address:
                self.table.selectRow(i)
                return

    def _current(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if rows and rows[0].row() < len(self.rows):
            return self.rows[rows[0].row()].address
        return None

    def current_server(self):
        address = self._current()
        return next((s for s in self.rows if s.address == address), None)

    def _selected(self):
        s = self.current_server()
        # The server Play uses stays listed: pick another one before removing it.
        self.remove_button.setEnabled(bool(s and s.kind == "added" and s.address != server_list.chosen(self.cfg)))
        self.use_button.setEnabled(s is not None)
        if s is None:
            self.details.setText("")
            return
        if s.kind == "offline":
            text = "No Xbox LIVE and no server: online features are off in the game."
        elif s.kind == "local":
            text = "Starts a Forza Live server just for your session on this PC. Only you are in it."
        else:
            parts = [s.address]
            if s.kind == "main":
                parts.append("the main server: the official leaderboards")
            elif s.kind == "listed":
                parts.append("verified by the main server" if s.verified else "listed, not verified")
                parts.append("its own leaderboards")
            else:
                parts.append("added by you: its own leaderboards")
            if s.contact:
                parts.append(f"contact: {s.contact}")
            st = self.statuses.get(s.address)
            if st is not None and not st.up and st.error:
                parts.append(f"not answering ({st.error})")
            text = ", ".join(parts) + "."
        self.details.setText(text)

    # ---- actions -----------------------------------------------------------------------------
    def use_selected(self):
        s = self.current_server()
        if s is None:
            return
        # The main server is "Public server", at the address this launcher has for it.
        if s.kind == "offline":
            self.cfg.online["mode"] = "offline"
        else:
            server_list.choose(self.cfg, server_list.PUBLIC_SERVER if s.kind == "main" else s.address)
        self.changed.emit()
        self.fill()
        self._say("Play now starts the game offline." if s.kind == "offline" else f"Play now uses {s.name}.")

    def remove_selected(self):
        s = self.current_server()
        if s is None or s.kind != "added":
            return
        server_list.remove_address(self.cfg, s.address)
        self.statuses.pop(s.address, None)
        self.changed.emit()  # Play keeps the server it uses until another one is picked
        self.fill()
        self._say(f"Removed {s.address}.")

    def add_typed(self):
        try:
            address = server_list.add_address(self.cfg, self.address.text())
        except ValueError as e:
            self._say(str(e), True)
            return
        self.address.clear()
        self.changed.emit()
        self.fill()
        self._select(address)
        self._say(f"Added {address}. Select it and click Use this server to play on it.")
        if self.isVisible() or self.timer.isActive():
            self.check()

    def _say(self, text, error=False):
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self.message.setStyleSheet(f"color: {theme.ERR};" if error else "")
