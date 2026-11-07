"""Online > Online > Website account (web_account.py): the link's status, Link, Unlink and Sign
out everywhere, and the link dialog. Network calls run off the GUI thread: the dialog's start,
polling and confirm in workers.Task threads, the card's status and buttons in workers.Probe.

The status is asked only after this launcher made a link (cfg.web_account["linked"]), when the
card is first shown in a session and after each change: a player who never links sends nothing."""
from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .. import shader_share, web_account
from .widgets import label
from .workers import Probe, Task

_running = set()  # threads still running after their dialog closed: kept until they finish


def _store(task, fn):
    """A Task's work: fn's answer in task.result, or the player's message in task.error."""
    task.result, task.error = None, ""
    try:
        task.result = fn(task)
    except web_account.WebAccountError as e:
        task.error = str(e)
    return ""


def _keep(thread):
    _running.add(thread)
    thread.finished.connect(lambda t=thread: _running.discard(t))
    return thread


def identity_of(cfg):
    """The game's Forza Live identity, or None before the game made one."""
    return shader_share.read_identity(shader_share.identity_dirs(cfg))


NO_IDENTITY = ("Play the game online on the public server once first: the launcher signs with the "
               "game's key, which the game makes at its first online start.")


class LinkDialog(QDialog):
    """Start a link, open the browser, wait for the website account's claim, then ask the player
    to confirm the account it shows (or decline it: Not me)."""

    def __init__(self, identity, parent=None, base=web_account.SERVICE):
        super().__init__(parent)
        self.identity, self.base = identity, base
        self.request = None      # the service's answer to start
        self.claimed = None      # the poll answer once claimed
        self.linked = None       # the account id once linked
        self.waiter = None
        self.setWindowTitle("Link to a website account")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        self.text = label("Asking forza.dyotson.dev for a link...")
        lay.addWidget(self.text)
        self.code = label("", "help")
        self.code.hide()
        lay.addWidget(self.code)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.link_button = QPushButton("Link")
        self.not_me = QPushButton("Not me")
        self.close_button = QPushButton("Cancel")
        for b in (self.link_button, self.not_me, self.close_button):
            b.setAutoDefault(False)
            buttons.addWidget(b)
        self.link_button.hide()
        self.not_me.hide()
        self.link_button.clicked.connect(self._confirm)
        self.not_me.clicked.connect(self.reject)
        self.close_button.clicked.connect(self._close)
        lay.addLayout(buttons)
        self._run(self._start_call, self._started, "web account link")

    def _run(self, fn, then, name):
        task = _keep(Task(lambda t: _store(t, fn), name))
        task.done.connect(lambda ok, msg, t=task: then(t, ok and not t.error, t.error or msg))
        task.start()
        return task

    def _close(self):
        self.accept() if self.linked else self.reject()

    def _fail(self, msg):
        self.text.setText(msg)
        self.link_button.hide()
        self.not_me.hide()
        self.close_button.setText("Close")
        self.close_button.show()

    # start -> open the browser -> wait
    def _start_call(self, task):
        return web_account.start(self.identity, self.base)

    def _started(self, task, ok, msg):
        if not ok:
            return self._fail(msg)
        self.request = task.result
        opened = QDesktopServices.openUrl(QUrl(self.request["url"]))
        first = ("Your browser opened forza.dyotson.dev. Sign in there and click Link." if opened else
                 "Open forza.dyotson.dev/account.html in your browser, sign in and enter the code below.")
        self.text.setText(first + " Then come back here to confirm.")
        self.code.setText(f"On another device, open {web_account.SITE}, sign in and enter the code "
                          f"{self.request['code']}. It works for 10 minutes.")
        self.code.show()
        rid = self.request["id"]
        self.waiter = self._run(lambda t: web_account.wait(self.identity, rid, t.cancelled, self.base, limit=660),
                                self._waited, "web account link wait")

    def _waited(self, task, ok, msg):
        if not ok:
            return self._fail(msg)
        doc = task.result
        if doc is None:
            return  # closed meanwhile
        state = doc.get("state")
        if state == "claimed" and isinstance(doc.get("account"), dict):
            self.claimed = doc
            self.text.setText(web_account.confirm_question(doc))
            self.code.hide()
            self.close_button.hide()
            self.link_button.show()
            self.not_me.show()
            self.link_button.setDefault(True)
        elif state == "declined":
            self._fail("The link was declined.")
        else:
            self._fail("The link is over (it lasts 10 minutes). Start again.")

    # confirm or decline
    def _confirm(self):
        self.link_button.setEnabled(False)
        self.not_me.setEnabled(False)
        account_id = self.claimed["account"]["id"]
        self._run(lambda t: web_account.confirm(self.identity, self.request["id"], account_id, self.base),
                  self._confirmed, "web account confirm")

    def _confirmed(self, task, ok, msg):
        self.link_button.setEnabled(True)
        self.not_me.setEnabled(True)
        if not ok:
            return self._fail(msg)
        self.linked = self.claimed["account"]["id"]
        self.text.setText(f"Linked to website account {web_account.describe(self.claimed['account'])}.")
        self.link_button.hide()
        self.not_me.hide()
        self.close_button.setText("Close")
        self.close_button.show()

    def reject(self):
        """Closing before the link is made ends the request, so the website shows it declined."""
        if self.waiter is not None:
            self.waiter.cancel()
        if self.request is not None and self.linked is None:
            rid, identity, base = self.request["id"], self.identity, self.base
            _keep(Task(lambda t: _store(t, lambda _: web_account.cancel(identity, rid, base)),
                       "web account cancel")).start()
            self.request = None
        super().reject()


class WebAccountCard(QWidget):
    """The status row and its buttons. `changed` after a link, an unlink or a sign-out."""
    changed = Signal()

    def __init__(self, cfg, parent=None, base=web_account.SERVICE):
        super().__init__(parent)
        self.cfg, self.base = cfg, base
        self.asked = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(label("Link this game to your account on forza.dyotson.dev (Discord, GitHub or "
                            "email). The website's features for players need a linked game. You "
                            "confirm every link here, so nobody can link your game without you.", "help"))
        self.status = label("Not linked." if not self.state()["linked"] else "Linked to a website account.")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.link_button = QPushButton("Link to website account")
        self.unlink_button = QPushButton("Unlink")
        self.signout_button = QPushButton("Sign out everywhere")
        self.signout_button.setToolTip("Signs the linked website account out on every browser")
        for b in (self.link_button, self.unlink_button, self.signout_button):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.link_button.clicked.connect(self.link)
        self.unlink_button.clicked.connect(self.unlink)
        self.signout_button.clicked.connect(self.signout)
        self._buttons()

    def state(self):
        return self.cfg.d.setdefault("web_account", {"linked": False})

    def _buttons(self, busy=False):
        linked = bool(self.state()["linked"])
        self.link_button.setText("Link another website account" if linked else "Link to website account")
        self.link_button.setEnabled(not busy)
        self.unlink_button.setVisible(linked)
        self.signout_button.setVisible(linked)
        self.unlink_button.setEnabled(not busy)
        self.signout_button.setEnabled(not busy)

    def _save(self, linked):
        if bool(self.state()["linked"]) != linked:
            self.state()["linked"] = linked
            self.cfg.save()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.asked and self.state()["linked"]:
            self.asked = True
            self.refresh()

    def _call(self, fn, then):
        identity = identity_of(self.cfg)
        if identity is None:
            self.status.setText(NO_IDENTITY)
            return
        self._buttons(busy=True)

        def run():
            try:
                return fn(identity)
            except web_account.WebAccountError as e:
                return e
        probe = Probe(run, "web account", self)
        probe.result.connect(lambda value: (self._buttons(), then(value)))
        probe.start()

    def refresh(self):
        self._call(lambda ident: web_account.account(ident, self.base), self._shown)

    def _shown(self, doc):
        if isinstance(doc, web_account.WebAccountError) or doc is None:
            self.status.setText(f"Could not check the link: {doc}" if doc else "Could not check the link.")
            return
        self._save(bool(doc.get("linked")))
        self.status.setText(web_account.status_text(doc))
        self._buttons()

    def link(self):
        identity = identity_of(self.cfg)
        if identity is None:
            self.status.setText(NO_IDENTITY)
            return
        dialog = LinkDialog(identity, self, self.base)
        if dialog.exec() == QDialog.Accepted and dialog.linked:
            self._save(True)
            self.status.setText(f"Linked to website account {web_account.describe(dialog.claimed['account'])}.")
            self._buttons()
            self.changed.emit()

    def unlink(self):
        if QMessageBox.question(self, "Unlink", "Unlink this game from its website account? The account "
                                "stays, but can only browse until a game is linked again.") != QMessageBox.Yes:
            return
        self._call(lambda ident: web_account.unlink(ident, self.base), self._unlinked)

    def _unlinked(self, doc):
        if isinstance(doc, web_account.WebAccountError) or doc is None:
            self.status.setText(str(doc or "Could not unlink. Try again."))
            return
        self._save(False)
        self.status.setText("Not linked.")
        self._buttons()
        self.changed.emit()

    def signout(self):
        if QMessageBox.question(self, "Sign out everywhere", "Sign the linked website account out on every "
                                "browser? Use this if someone else may be using it.") != QMessageBox.Yes:
            return
        self._call(lambda ident: web_account.signout(ident, self.base), self._signed_out)

    def _signed_out(self, doc):
        if isinstance(doc, web_account.WebAccountError) or doc is None:
            self.status.setText(str(doc or "Could not sign out. Try again."))
            return
        ended = doc.get("ended", 0)
        self.status.setText(f"The website account is signed out everywhere ({ended} browser"
                            f"{'' if ended == 1 else 's'}).")
