"""The Play page's "Challenges" card (forza-live #1, challenges.py): the main server's daily and
weekly objectives with this player's progress. Asked off the GUI thread a moment after the card
is on screen, then at most every 5 minutes while the launcher is open, and again after each game
session (the session's results are on the server then). Nothing is asked unless challenges.state
is 'on'."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from .. import challenges
from . import theme
from .widgets import card, label
from .workers import Probe

FIRST_DELAY_MS = 2000   # after the card shows: opening the launcher is not held up


class ChallengesCard(QWidget):
    def __init__(self, cfg, parent=None, load=None):
        super().__init__(parent)
        self.cfg = cfg
        self._load = load            # None: challenges.load (tests give their own)
        self._probe = None
        self._fetched_at = None      # monotonic time of the last answer
        self._due = True
        self.sets, self.note_text, self.gift_text = [], "", ""
        self.fetches = 0             # finished fetches (tests)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame, lay = card()
        outer.addWidget(self.frame)
        lay.addWidget(label("Challenges", "cardTitle"))
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(0, 1)
        lay.addWidget(self.grid_host)
        self.gift = label("", "muted")
        self.gift.setTextFormat(Qt.PlainText)
        lay.addWidget(self.gift)
        self.note = label("", "muted")
        lay.addWidget(self.note)
        self.help = label("Set by the main Forza Live server, the same for every player. Your "
                          "races, speed traps and zones, 1000 Club medals and Big Shots count when "
                          "you play signed in on it. A community server's challenges are its own.",
                          "help")
        lay.addWidget(self.help)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._fetch_if_shown)
        self.show_state()

    # ---- what the card says ------------------------------------------------------------------
    def show_state(self):
        st = challenges.state(self.cfg)
        self.setVisible(st != "hidden")
        if st == "other":
            self._fill([], "Challenges count on the main server only: choose Public server (Settings > "
                           "Online, or the Servers page) to take part.")
        elif st == "off":
            self._fill([], 'Daily and weekly challenges are off: turn on "Daily and weekly challenges" '
                           "in Settings > Online to see them. Nothing is asked of the server meanwhile.")
        elif st == "on":
            if self.busy() and not self.sets:
                self._fill([], "Asking the main server…")
            else:
                self._fill(self.sets, self.note_text)
        return st

    def _fill(self, sets, note):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        row = 0
        now = time.time()
        for s in sets:
            head = label(f"{challenges.KIND_NAMES[s.kind]} · {challenges.time_left(s.ends, now)}", "section")
            self.grid.addWidget(head, row, 0, 1, 2)
            row += 1
            for o in s.objectives:
                title = label(("✓ " if o.done else "") + o.title)
                title.setTextFormat(Qt.PlainText)
                if o.completed:
                    title.setToolTip(f"{o.completed} player(s) finished it")
                count = QLabel("Done" if o.done else f"{o.progress} / {o.target}")
                count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if o.done:
                    count.setStyleSheet(f"color: {theme.OK}; font-weight: 700;")
                self.grid.addWidget(title, row, 0)
                self.grid.addWidget(count, row, 1)
                row += 1
        self.grid_host.setVisible(row > 0)
        gift = self.gift_text if sets else ""
        self.gift.setText(gift)
        self.gift.setVisible(bool(gift))
        self.note.setText(note)
        self.note.setVisible(bool(note))

    # ---- asking the server -------------------------------------------------------------------
    def busy(self):
        return self._probe is not None

    def refresh(self, force=False):
        """The Play page was refreshed (force: a game session ended). Schedules a fetch when one is
        due and the card may ask the server."""
        st = self.show_state()
        if st != "on":
            self.timer.stop()
            return
        stale = self._fetched_at is None or time.monotonic() - self._fetched_at >= challenges.FETCH_EVERY
        if force or stale:
            self._due = True
        if self._due and not self.busy() and not self.timer.isActive():
            self.timer.start(FIRST_DELAY_MS)

    def _fetch_if_shown(self):
        """Only a card on screen asks: a window built and never shown (tests) sends nothing."""
        if not self.isVisible() or self.busy() or challenges.state(self.cfg) != "on":
            return
        self._due = False
        cfg, load = self.cfg, self._load or challenges.load
        self._probe = Probe(lambda: load(cfg), "challenges", self)
        self._probe.result.connect(self._fetched)
        self._probe.start()
        self.show_state()

    def _fetched(self, result):
        self._probe = None
        self.fetches += 1
        self._fetched_at = time.monotonic()
        if result is None:     # raised (logged)
            self.sets, self.note_text, self.gift_text = (
                [], "Could not read the challenges. The launcher log has the details.", "")
        else:
            self.sets, self.note_text, self.gift_text = result
        self.show_state()
