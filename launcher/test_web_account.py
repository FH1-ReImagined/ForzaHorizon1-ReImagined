"""Linking the game to a website account (web_account.py, ui/web_account.py; website #4, M2),
against a fake accounts service on 127.0.0.1 that checks every call's signature the way the real
one does (forza-live's accounts/members/game.py with server/forza_live/core/auth.py). The fake's
website side (claim) stands in for the browser. No network beyond this machine."""
import http.server
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import ed25519, live_auth, web_account  # noqa: E402

KEY = bytes(range(16)).hex().upper()
SEED = bytes(range(32)).hex().upper()
XUID = "0009000000000001"
IDENTITY = (XUID, KEY, SEED)
PUB = ed25519.public_key(bytes.fromhex(SEED))


class FakeService(http.server.ThreadingHTTPServer):
    """The accounts service's game routes, enough for the launcher: requests, a claim by an
    account, confirm, cancel, the link, unlink and sign out."""

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.daemon_threads = True
        self.requests, self.link, self.calls, self.nonces = {}, None, [], set()
        self.clock = 0          # seconds the service's clock is ahead of the launcher's
        self.refuse_next = 0    # signed calls to refuse as if the key were unknown
        self.fail_with = None   # (status, error) for the next start
        self.base = "http://127.0.0.1:%d/api/" % self.server_address[1]
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def claim(self, rid, account=None):
        self.requests[rid].update(state="claimed", account=account or {
            "id": "K7Q2-XM4P", "method": "discord", "created": "2026-11-02"})


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self, status, doc):
        raw = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Forza-Time", str(int(time.time() + self.server.clock)))
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def checked(self, body):
        """The signature's check (auth.check_request): name, clock, key, nonce."""
        s = self.server
        value = self.headers.get("X-Forza-Auth", "")
        s.calls.append((self.command, self.path, dict(self.headers), body))
        if not value.startswith(live_auth.TAG + " "):
            return False
        fields = dict(p.strip().split("=", 1) for p in value[len(live_auth.TAG) + 1:].split(","))
        text = live_auth.canonical(self.command, self.path, fields["Server"], fields["Xuid"], int(fields["Time"]),
                                   fields["Nonce"], body)
        if s.refuse_next:
            s.refuse_next -= 1
            return False
        return (fields["Server"] == "127.0.0.1" and fields["Xuid"] == XUID
                and abs(int(fields["Time"]) - (time.time() + s.clock)) <= 120
                and fields["Nonce"] not in s.nonces
                and ed25519.verify(PUB, text, bytes.fromhex(fields["Signature"]))
                and s.nonces.add(fields["Nonce"]) is None)  # used once

    def route(self, body):
        s = self.server
        if not self.checked(body):
            return self.answer(401, {"error": "refused"})
        path = self.path[len("/api/"):]
        doc = json.loads(body) if body else None
        if path == "game/link/start":
            if s.fail_with:
                return self.answer(*s.fail_with)
            rid, token = secrets.token_hex(16), secrets.token_urlsafe(32)
            s.requests[rid] = {"state": "pending", "account": None}
            return self.answer(200, {"id": rid, "url": s.base.rsplit("/api/", 1)[0] + "/account.html#link=" + token,
                                     "code": "K7M2-QXPA", "expires": int(time.time()) + 600})
        if path.startswith("game/link/"):
            rid, _, action = path[len("game/link/"):].partition("/")
            req = s.requests.get(rid)
            if req is None:
                return self.answer(404, {"error": "not found"})
            if action == "":
                return self.answer(200, {"state": req["state"], "account": req["account"], "replaces": None})
            if action == "confirm":
                if req["state"] != "claimed":
                    return self.answer(409, {"error": "not claimed"})
                if doc != {"account": req["account"]["id"]}:
                    return self.answer(409, {"error": "the account does not match"})
                req["state"], s.link = "linked", req["account"]
                return self.answer(200, {"ok": True, "state": "linked"})
            if action == "cancel":
                req["state"] = "declined"
                return self.answer(200, {"ok": True, "state": "declined"})
        if path == "game/account":
            return self.answer(200, {"linked": bool(s.link), "state": "active" if s.link else None, "account": s.link})
        if path == "game/unlink":
            was, s.link = s.link, None
            return self.answer(200, {"ok": True, "unlinked": bool(was)})
        if path == "game/signout":
            return self.answer(200, {"ok": True, "ended": 2}) if s.link else self.answer(404, {"error": "not linked"})
        return self.answer(404, {"error": "not found"})

    def do_GET(self):
        self.route(b"")

    def do_POST(self):
        self.route(self.rfile.read(int(self.headers.get("Content-Length") or 0)))


class ServiceTest(unittest.TestCase):
    def setUp(self):
        live_auth._offsets.clear()
        self.svc = FakeService()
        self.addCleanup(self.svc.server_close)
        self.addCleanup(self.svc.shutdown)
        self.base = self.svc.base

    def opener(self, req, timeout=8):
        import urllib.request
        return urllib.request.urlopen(req, timeout=timeout)  # plain HTTP to the fake

    def start(self, **kw):
        return web_account.start(IDENTITY, self.base, self.opener, sleep=lambda s: None, **kw)


class Calls(ServiceTest):
    def test_the_whole_link(self):
        doc = self.start()
        self.assertTrue(doc["url"].startswith(self.base[:-4] + "account.html#link="))
        self.svc.claim(doc["id"])  # the player clicked Link in the browser
        claimed = web_account.wait(IDENTITY, doc["id"], base=self.base, opener=self.opener, sleep=lambda s: None)
        self.assertEqual(claimed["state"], "claimed")
        self.assertEqual(web_account.confirm_question(claimed),
                         "Link this game to website account K7Q2-XM4P (Discord, created 2 Nov 2026)? "
                         "Only if you just signed in to it yourself.")
        web_account.confirm(IDENTITY, doc["id"], claimed["account"]["id"], self.base, self.opener)
        status = web_account.account(IDENTITY, self.base, self.opener)
        self.assertEqual(web_account.status_text(status),
                         "Linked to website account K7Q2-XM4P (Discord, created 2 Nov 2026).")
        self.assertEqual(web_account.signout(IDENTITY, self.base, self.opener)["ended"], 2)
        self.assertTrue(web_account.unlink(IDENTITY, self.base, self.opener)["unlinked"])
        self.assertFalse(web_account.account(IDENTITY, self.base, self.opener)["linked"])

    def test_every_call_is_signed_and_carries_no_secret(self):
        doc = self.start()
        web_account.poll(IDENTITY, doc["id"], self.base, self.opener)
        web_account.cancel(IDENTITY, doc["id"], self.base, self.opener)
        self.assertEqual(len(self.svc.calls), 3)
        for method, path, headers, body in self.svc.calls:
            self.assertIn("X-Forza-Auth", headers)
            text = json.dumps(headers) + body.decode()
            self.assertNotIn(KEY, text.upper())          # never the friends key
            self.assertNotIn(SEED, text.upper())         # never the seed
            self.assertNotIn("X-Friends-Key", headers)   # nor a key made from it
        self.assertEqual([c[:2] for c in self.svc.calls],
                         [("POST", "/api/game/link/start"), ("GET", "/api/game/link/" + doc["id"]),
                          ("POST", "/api/game/link/%s/cancel" % doc["id"])])

    def test_the_main_server_gets_no_derived_key(self):
        headers = live_auth.headers(IDENTITY, "POST", web_account.SERVICE + "game/link/start", b"{}", old_server=True)
        self.assertNotIn("X-Friends-Key", headers)
        self.assertIn("Server=forza.dyotson.dev,", headers["X-Forza-Auth"])

    def test_the_service_address_is_built_in(self):
        self.assertEqual(web_account.SERVICE, "https://forza.dyotson.dev/api/")
        self.assertIn(live_auth.server_name("forza.dyotson.dev"), live_auth.MAIN_NAMES)

    def test_a_clock_that_is_off_is_corrected(self):
        self.svc.clock = 1000
        self.assertIn("id", self.start())
        self.assertEqual(len(self.svc.calls), 2)  # refused once, then with the service's clock

    def test_a_key_bound_a_moment_ago_is_tried_again(self):
        self.svc.refuse_next = 1
        slept = []
        doc = web_account.start(IDENTITY, self.base, self.opener, sleep=slept.append)
        self.assertIn("id", doc)
        self.assertEqual(slept, [web_account.RETRY_SECONDS])

    def test_a_key_the_service_does_not_know(self):
        self.svc.refuse_next = 10
        with self.assertRaises(web_account.WebAccountError) as e:
            self.start()
        self.assertIn("Start the game once with the Public server picked", str(e.exception))

    def test_refusals_become_the_player_s_messages(self):
        for status, error, says in ((409, "play online first", "Upload one speed trap first"),
                                    (403, "not available", "cannot link"),
                                    (429, "changed in the past day", "Try again tomorrow"),
                                    (503, "linking is not available", "not available right now"),
                                    (500, "something new", "answered 500")):
            self.svc.fail_with = (status, {"error": error})
            with self.assertRaises(web_account.WebAccountError) as e:
                self.start()
            self.assertIn(says, str(e.exception))

    def test_the_service_s_own_words_for_what_the_game_still_needs(self):
        said = "Upload one speed trap first: drive through any speed trap camera, then try again."
        for status, doc, says in ((409, {"error": "play online first", "message": said}, said),
                                  (409, {"error": "play online first", "message": " "}, "Upload one speed trap"),
                                  (409, {"error": "play online first", "message": 7}, "Upload one speed trap"),
                                  (403, {"error": "not available", "message": "internal detail"}, "cannot link")):
            self.svc.fail_with = (status, doc)
            with self.assertRaises(web_account.WebAccountError) as e:
                self.start()
            self.assertEqual(e.exception.code, doc["error"])
            self.assertIn(says, str(e.exception))
            self.assertNotIn("internal detail", str(e.exception))
        self.svc.fail_with = (409, {"error": "play online first", "message": "x" * 5000})
        with self.assertRaises(web_account.WebAccountError) as e:
            self.start()
        self.assertEqual(len(str(e.exception)), web_account.MESSAGE_CHARS)

    def test_a_confirm_before_the_claim(self):
        doc = self.start()
        with self.assertRaises(web_account.WebAccountError) as e:
            web_account.confirm(IDENTITY, doc["id"], "K7Q2-XM4P", self.base, self.opener)
        self.assertEqual(e.exception.code, "not claimed")

    def test_an_unreachable_service(self):
        with self.assertRaises(web_account.WebAccountError) as e:
            web_account.account(IDENTITY, "http://127.0.0.1:9/api/", self.opener)
        self.assertIn("Could not reach 127.0.0.1:9", str(e.exception))

    def test_waiting_stops_when_cancelled_or_too_long(self):
        doc = self.start()
        self.assertIsNone(web_account.wait(IDENTITY, doc["id"], lambda: True, self.base, self.opener))
        self.assertEqual(web_account.wait(IDENTITY, doc["id"], base=self.base, opener=self.opener,
                                          sleep=lambda s: None, limit=0)["state"], "expired")

    def test_a_start_must_send_the_browser_to_the_site(self):
        with mock.patch.object(web_account, "call", return_value={"id": "1", "code": "K7M2-QXPA",
                                                                  "url": "https://evil.example/x"}):
            with self.assertRaises(web_account.WebAccountError):
                web_account.start(IDENTITY)

    def test_live_auth_uses_the_opener(self):
        seen = []

        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return b"{}"
        live_auth.urlopen("https://forza.dyotson.dev/api/game/account", IDENTITY,
                          opener=lambda req, timeout: seen.append((req.full_url, timeout)) or Reply())
        self.assertEqual(seen, [("https://forza.dyotson.dev/api/game/account", 8)])
        with mock.patch.object(web_account.fetch, "urlopen", return_value=Reply()) as https:
            web_account.account(IDENTITY)
        https.assert_called_once()  # HTTPS through fetch.urlopen (the system's certificates)


class Interface(ServiceTest):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def spin(self, until, seconds=10):
        deadline = time.monotonic() + seconds
        while not until() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(until())

    def patch(self, *args, **kw):
        p = mock.patch.object(*args, **kw)
        value = p.start()
        self.addCleanup(p.stop)
        return value

    def dialog(self, view=None, opens=True, **kw):
        """A LinkDialog; `view` is what the launcher's window would be (None: no Qt WebEngine),
        `opens` whether the browser opens."""
        from forza_launcher.ui import web_account as ui
        from forza_launcher.ui import web_view
        opened = []
        self.patch(web_view, "open_in_browser", side_effect=lambda url: opened.append(url) or opens)
        self.patch(ui, "make_view", side_effect=lambda site, parent: view)
        self.patch(web_account, "POLL_SECONDS", 0.1)
        d = ui.LinkDialog(IDENTITY, None, self.base, **kw)
        self.addCleanup(d.deleteLater)
        self.addCleanup(self.ended, d)
        return d, opened

    def ended(self, d):
        """Close a dialog a test left open and let its threads finish (a QThread must not outlive
        the test run)."""
        if d.active:
            d.reject()
        self.spin(lambda: not ui_module()._running)

    def setUp(self):
        super().setUp()
        from forza_launcher.ui import web_view
        self.patch(web_account.fetch, "urlopen", self.opener)
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.storage = Path(root.name) / "web-account"
        self.patch(web_view, "storage_dir", return_value=self.storage)   # never the player's data folder

    def test_the_dialog_links_after_both_sides_approve(self):
        d, opened = self.dialog()
        self.spin(lambda: d.request is not None and d.code.text())
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].startswith(self.base[:-4] + "account.html#link="))
        self.assertIn("Your browser opened", d.text.text())
        self.assertIn("K7M2-QXPA", d.code.text())
        self.assertFalse(d.link_button.isVisibleTo(d))
        self.svc.claim(d.request["id"])
        self.spin(lambda: d.claimed is not None)
        self.assertIn("K7Q2-XM4P (Discord, created 2 Nov 2026)", d.text.text())
        self.assertTrue(d.link_button.isVisibleTo(d) and d.not_me.isVisibleTo(d))
        d.link_button.click()
        self.spin(lambda: d.linked == "K7Q2-XM4P")
        self.assertEqual(self.svc.link["id"], "K7Q2-XM4P")

    def test_not_me_declines(self):
        d, _ = self.dialog()
        self.spin(lambda: d.request is not None)
        rid = d.request["id"]
        self.svc.claim(rid)
        self.spin(lambda: d.claimed is not None)
        d.not_me.click()
        self.spin(lambda: self.svc.requests[rid]["state"] == "declined")
        self.assertIsNone(self.svc.link)

    def test_closing_while_waiting_ends_the_request(self):
        d, _ = self.dialog()
        self.spin(lambda: d.request is not None)
        rid = d.request["id"]
        d.reject()
        self.spin(lambda: self.svc.requests[rid]["state"] == "declined")
        self.spin(lambda: d.waiter.isFinished())

    def test_a_refusal_is_shown(self):
        self.svc.fail_with = (409, {"error": "play online first", "message": "Upload one speed trap first."})
        d, opened = self.dialog()
        self.spin(lambda: "Upload one speed trap first." in d.text.text())
        self.assertEqual(opened, [])
        self.assertEqual(d.close_button.text(), "Close")

    def test_service_words_are_plain_text(self):
        from PySide6.QtCore import Qt
        from forza_launcher.config import Config
        from forza_launcher.ui import web_account as ui
        self.svc.fail_with = (409, {"error": "play online first", "message": "<b>x</b>"})
        d, _ = self.dialog()
        self.spin(lambda: "<b>x</b>" in d.text.text())
        self.assertEqual(d.text.textFormat(), Qt.PlainText)
        self.assertEqual(d.code.textFormat(), Qt.PlainText)
        with tempfile.TemporaryDirectory() as root:
            cfg = Config(Path(root) / "launcher.json")
            card = ui.WebAccountCard(cfg, None, self.base)
            self.addCleanup(card.deleteLater)
            self.assertEqual(card.status.textFormat(), Qt.PlainText)

    def test_the_card_shows_for_every_server_and_asks_only_after_a_link(self):
        from forza_launcher.config import Config
        from forza_launcher.ui import web_account as ui
        from forza_launcher.ui.settings_page import SettingsPage
        with tempfile.TemporaryDirectory() as root:
            cfg = Config(Path(root) / "launcher.json")
            cfg.advanced["data_root"] = root
            cfg.gpu.update(checked=True, vendor="nvidia")
            window = mock.Mock()
            window.preview_text.return_value = "game.exe"
            with mock.patch.object(web_account, "account") as asked:
                page = SettingsPage(cfg, window)
                page.show()
                self.app.processEvents()
                online = page.online_page   # Online > Online in the window
                online.show()
                self.app.processEvents()
                card = page.web_account
                self.assertTrue(page.web_account_frame.isVisibleTo(online))
                self.assertFalse(card.public_button.isVisibleTo(card))
                asked.assert_not_called()  # never linked: nothing is sent
                for mode in ("offline", "local", "custom"):   # as Servers' other rows do
                    cfg.online["mode"] = mode
                    page.sync_server()
                    self.assertTrue(page.web_account_frame.isVisibleTo(online))
                    self.assertTrue(card.public_note.isVisibleTo(card) and card.public_button.isVisibleTo(card))
                    self.assertIn("Public server", card.public_note.text())
                    self.assertTrue(card.link_button.isEnabled())
                window.write_toml.reset_mock()
                card.public_button.click()   # Use the Public server: saved now
                self.assertEqual(cfg.online["mode"], "public")
                window.write_toml.assert_called_once()
                self.assertTrue((Path(root) / "launcher.json").exists())
                self.assertIn("Public server", page.server_status.text())
                self.assertTrue(page.web_account_frame.isVisibleTo(online))
                self.assertFalse(card.public_note.isVisibleTo(card) or card.public_button.isVisibleTo(card))
                with mock.patch.object(ui, "identity_of", return_value=None):
                    card.link_button.click()   # before the game made its key
                self.assertIn("Start the game once with the Public server picked", card.status.text())
                self.assertIn("speed trap", card.status.text())
                online.hide()
                page._save_timer.stop()
                page.deleteLater()
            # after a link, opening the card asks once
            cfg.d["web_account"]["linked"] = True
            card = ui.WebAccountCard(cfg, None, self.base)
            self.svc.link = {"id": "K7Q2-XM4P", "method": "github", "created": "2026-11-02"}
            with mock.patch.object(ui, "identity_of", return_value=IDENTITY):
                card.show()
                self.spin(lambda: "K7Q2-XM4P (GitHub" in card.status.text())
            self.assertTrue(card.unlink_button.isVisibleTo(card))
            card.deleteLater()

    # --- the website's page in the launcher's own window (ui/web_view.py) ---

    def fake_view(self):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QWidget

        class FakeView(QWidget):
            """Stands in for web_view.SignInView where the test needs no Qt WebEngine."""
            failed = Signal(str)
            notice = Signal(str)

            def __init__(self):
                super().__init__()
                self.loaded, self.closed = [], 0

            def load(self, url):
                self.loaded.append(url)

            def close_down(self):
                self.closed += 1
        view = FakeView()
        self.addCleanup(view.deleteLater)
        return view

    def test_the_page_opens_in_the_launcher_and_links_in_the_same_window(self):
        view = self.fake_view()
        d, opened = self.dialog(view)
        self.spin(lambda: view.loaded)
        self.assertEqual(opened, [])          # no browser
        self.assertEqual(len(view.loaded), 1)
        self.assertTrue(view.loaded[0].startswith(self.base[:-4] + "account.html#link="))
        self.assertTrue(view.isVisibleTo(d))
        self.assertIn("Sign in below with Discord, GitHub or an emailed code", d.text.text())
        self.assertIn("type the code on this page", d.note.text())
        self.assertTrue(d.browser_button.isVisibleTo(d))
        self.assertIn("K7M2-QXPA", d.code.text())       # another device still works
        self.svc.claim(d.request["id"])                 # the page's Link
        self.spin(lambda: d.claimed is not None)
        self.assertIn("K7Q2-XM4P (Discord, created 2 Nov 2026)", d.text.text())
        self.assertTrue(view.isVisibleTo(d))            # the page stays: it shows "linked" next
        self.assertFalse(d.browser_button.isVisibleTo(d))
        d.link_button.click()                           # the launcher's Link
        self.spin(lambda: d.linked == "K7Q2-XM4P")
        self.assertEqual(self.svc.link["id"], "K7Q2-XM4P")
        self.assertEqual(view.closed, 0)
        d.close_button.click()
        self.assertEqual(d.result(), d.DialogCode.Accepted)
        self.assertEqual(view.closed, 1)                # the page and its profile end with the dialog

    def test_a_browser_that_does_not_open_leaves_the_link_and_the_code(self):
        from PySide6.QtGui import QGuiApplication
        d, opened = self.dialog(None, opens=False)
        self.spin(lambda: d.request is not None and d.code.text())
        self.assertEqual(len(opened), 1)
        url = d.request["url"]
        self.assertEqual(opened, [url])
        self.assertIn("Your browser did not open", d.text.text())
        self.assertIn(url, d.text.text())
        self.assertNotIn("Your browser opened", d.text.text())
        self.assertIn("K7M2-QXPA", d.code.text())
        self.assertTrue(d.copy_button.isVisibleTo(d))
        d.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), url)
        self.assertEqual(d.copy_button.text(), "Copied")
        calls = len(self.svc.calls)
        self.spin(lambda: len(self.svc.calls) >= calls + 3)   # still waiting: polls later
        self.assertIsNone(d.claimed)
        self.assertTrue(d.copy_button.isVisibleTo(d))
        self.assertTrue(d.code.isVisibleTo(d))
        self.assertIn(url, d.text.text())
        self.svc.claim(d.request["id"])
        self.spin(lambda: d.claimed is not None)
        self.assertFalse(d.copy_button.isVisibleTo(d))   # claimed: Link or Not me now
        self.assertFalse(d.code.isVisibleTo(d))

    def test_copy_link_is_there_with_the_launcher_s_window_and_the_browser(self):
        from PySide6.QtGui import QGuiApplication
        view = self.fake_view()
        d, opened = self.dialog(view)
        self.spin(lambda: view.loaded)
        self.assertTrue(d.copy_button.isVisibleTo(d))
        d.browser_button.click()
        self.assertIn("Your browser opened", d.text.text())
        self.assertTrue(d.copy_button.isVisibleTo(d))
        d.copy_button.click()
        self.assertEqual(QGuiApplication.clipboard().text(), opened[0])
        d.close_button.click()

    def test_a_failed_link_has_no_copy_link(self):
        d, _ = self.dialog(None)
        self.spin(lambda: d.copy_button.isVisibleTo(d))
        d._fail("The link is over (it lasts 10 minutes). Start again.")
        self.assertFalse(d.copy_button.isVisibleTo(d))

    def test_the_window_falls_back_to_a_browser_that_does_not_open(self):
        view = self.fake_view()
        d, opened = self.dialog(view, opens=False)
        self.spin(lambda: view.loaded)
        view.failed.emit("The launcher's window stopped.")
        self.assertEqual(opened, view.loaded)
        self.assertTrue(d.text.text().startswith("The launcher's window stopped. Your browser did not open"))
        self.assertIn(d.request["url"], d.text.text())
        self.assertTrue(d.copy_button.isVisibleTo(d))

    def test_use_my_browser_instead(self):
        view = self.fake_view()
        d, opened = self.dialog(view)
        self.spin(lambda: view.loaded)
        d.browser_button.click()
        self.assertEqual(view.closed, 1)
        self.assertEqual(opened, view.loaded)           # the same link, now in the browser
        self.assertFalse(d.browser_button.isVisibleTo(d))
        self.assertIn("Your browser opened", d.text.text())
        self.assertIn("K7M2-QXPA", d.code.text())
        self.svc.claim(d.request["id"])                 # still waiting: the browser's claim works
        self.spin(lambda: d.claimed is not None)
        d.link_button.click()
        self.spin(lambda: d.linked == "K7Q2-XM4P")
        d.accept()
        self.assertEqual(view.closed, 1)

    def gap(self, d, above, below):
        """Pixels between the bottom of `above` and the top of `below` in the shown dialog."""
        d.layout().activate()
        self.app.processEvents()
        return below.mapTo(d, below.rect().topLeft()).y() - above.mapTo(d, above.rect().bottomLeft()).y()

    def test_after_use_my_browser_instead_the_buttons_are_under_the_question(self):
        # #856: the dialog was sized for the page; the buttons stayed at its bottom, far below
        view = self.fake_view()
        d, _ = self.dialog(view)
        d.show()
        self.spin(lambda: view.loaded)
        d.browser_button.click()
        self.app.processEvents()
        self.assertLess(d.height(), 520)                  # no longer the page's size
        self.assertLess(self.gap(d, d.text, d.close_button), 120)   # only the code in between
        self.svc.claim(d.request["id"])
        self.spin(lambda: d.claimed is not None)
        self.assertLess(self.gap(d, d.text, d.link_button), 40)
        d.resize(980, 820)                                # made big again: the room goes below
        self.assertLess(self.gap(d, d.text, d.link_button), 40)
        self.assertGreater(d.height() - d.link_button.mapTo(d, d.link_button.rect().bottomLeft()).y(), 300)

    def test_with_the_page_the_question_moves_down_to_the_buttons(self):
        view = self.fake_view()
        d, _ = self.dialog(view)
        d.show()
        self.spin(lambda: view.loaded)
        self.assertGreater(self.gap(d, d.text, view), -1)  # "Sign in below" stays above the page
        self.svc.claim(d.request["id"])
        self.spin(lambda: d.claimed is not None)
        self.assertTrue(view.isVisibleTo(d))
        self.assertGreater(self.gap(d, view, d.text), -1)  # the question now under the page
        self.assertLess(self.gap(d, d.text, d.link_button), 40)

    def test_without_the_page_the_buttons_are_under_the_text(self):
        d, opened = self.dialog(None)
        d.show()
        self.spin(lambda: d.request is not None and d.code.text())
        d.resize(980, 820)
        self.assertLess(self.gap(d, d.text, d.close_button), 120)

    def test_a_window_that_stops_falls_back_to_the_browser(self):
        view = self.fake_view()
        d, opened = self.dialog(view)
        self.spin(lambda: view.loaded)
        view.failed.emit("The launcher's window stopped.")
        self.assertEqual(opened, view.loaded)
        self.assertTrue(d.text.text().startswith("The launcher's window stopped. Your browser opened"))
        self.assertEqual(view.closed, 1)

    def test_closing_the_window_declines_and_ends_the_page(self):
        view = self.fake_view()
        d, _ = self.dialog(view)
        self.spin(lambda: view.loaded)
        rid = d.request["id"]
        d.close_button.click()
        self.assertEqual(view.closed, 1)
        self.spin(lambda: self.svc.requests[rid]["state"] == "declined")

    def test_a_security_key_s_prompt_is_shown(self):
        view = self.fake_view()
        d, _ = self.dialog(view)
        self.spin(lambda: view.loaded)
        view.notice.emit("Touch your security key.")
        self.assertEqual(d.note.text(), "Touch your security key.")
        self.assertTrue(d.note.isVisibleTo(d))
        view.notice.emit("")
        self.assertFalse(d.note.isVisibleTo(d))

    def test_the_browser_is_browser_open_url(self):
        from forza_launcher import browser
        wv = web_view_module()
        for answer in (True, False):
            with mock.patch.object(browser, "open_url", return_value=answer) as open_url:
                self.assertIs(wv.open_in_browser("https://example.com/help"), answer)
            open_url.assert_called_once_with("https://example.com/help")
        self.assertEqual(wv.not_opened("https://example.com/help"),
                         "Your browser did not open. Open this link in any browser: https://example.com/help")

    def test_without_qt_webengine_the_browser_opens(self):
        from forza_launcher.ui import web_account as ui
        from forza_launcher.ui import web_view
        with mock.patch.dict(os.environ, {"FORZA_NO_WEB_VIEW": "1"}):
            self.assertFalse(web_view.available())
            self.assertIsNone(web_view.create("https://forza.dyotson.dev"))
        web_view.available()   # loaded before: patch.dict would unload what is first imported inside it
        with mock.patch.dict(sys.modules, {"PySide6.QtWebEngineWidgets": None}):   # import fails
            self.assertFalse(web_view.available())
            self.assertIsNone(ui.make_view("https://forza.dyotson.dev", None))
        d, opened = self.dialog(None)
        self.spin(lambda: opened)
        self.assertIsNone(d.web)
        self.assertFalse(d.browser_button.isVisibleTo(d))

    def test_the_browser_on_request(self):
        from forza_launcher.ui import web_account as ui
        d, opened = self.dialog(self.fake_view(), browser=True)
        self.spin(lambda: opened)
        self.assertIsNone(d.web)
        ui.make_view.assert_not_called()

    def test_the_card_signs_in_and_signs_the_window_out(self):
        from forza_launcher.config import Config
        from forza_launcher.ui import web_account as ui
        with tempfile.TemporaryDirectory() as root:
            cfg = Config(Path(root) / "launcher.json")
            card = ui.WebAccountCard(cfg, None, self.base)
            self.addCleanup(card.deleteLater)
            self.assertEqual(card.link_button.text(), "Sign in or create an account")
            self.assertFalse(card.forget_button.isVisibleTo(card))   # never signed in there
            (self.storage / "Cookies").parent.mkdir(parents=True)
            (self.storage / "Cookies").write_bytes(b"x")
            card._buttons()
            self.assertTrue(card.forget_button.isVisibleTo(card))
            card.forget_button.click()
            self.assertFalse(self.storage.exists())
            self.assertFalse(card.forget_button.isVisibleTo(card))
            self.assertIn("signed out of the website, Discord and GitHub", card.status.text())

            view = self.fake_view()
            self.patch(ui, "make_view", side_effect=lambda site, parent: view)
            self.patch(ui, "identity_of", return_value=IDENTITY)
            self.patch(web_account, "POLL_SECONDS", 0.1)
            self.patch(web_view_module(), "open_in_browser", return_value=True)

            def player():
                d = card.findChild(ui.LinkDialog)
                if d is None or d.request is None:
                    return QTimer.singleShot(20, player)
                if d.claimed is None:
                    self.svc.requests[d.request["id"]]["state"] == "claimed" or self.svc.claim(d.request["id"])
                    return QTimer.singleShot(20, player)
                if d.linked is None:
                    if d.link_button.isEnabled():
                        d.link_button.click()
                    return QTimer.singleShot(20, player)
                d.close_button.click()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, player)
            card.link()
            self.assertTrue(cfg.d["web_account"]["linked"])
            self.assertIn("Linked to website account K7Q2-XM4P", card.status.text())
            self.assertEqual(card.link_button.text(), "Link another website account")
            self.assertEqual(view.closed, 1)


def ui_module():
    from forza_launcher.ui import web_account as ui
    return ui


def web_view_module():
    from forza_launcher.ui import web_view
    return web_view


class Navigation(unittest.TestCase):
    """web_view.decide: what the launcher's window may show itself."""

    def where(self, url, **kw):
        return web_view_module().decide(url, **kw)

    def test_the_site_and_the_providers_stay_in_the_window(self):
        for url in ("https://forza.dyotson.dev/account.html#link=abc", "https://FORZA.dyotson.dev:443/api/x",
                    "https://discord.com/oauth2/authorize?client_id=1", "https://discord.com/login",
                    "https://github.com/login/oauth/authorize?x=1", "https://github.com/login", "about:blank"):
            self.assertEqual(self.where(url), "view", url)
        # the site's sign-in form and the providers' redirects
        self.assertEqual(self.where("https://github.com/login", current="https://forza.dyotson.dev/account.html"),
                         "view")
        # a link the player clicks on the provider's own page (Create an account, Forgot password)
        self.assertEqual(self.where("https://github.com/signup", link_click=True, current="https://github.com/login"),
                         "view")
        self.assertEqual(self.where("https://discord.com/register", link_click=True,
                                    current="https://discord.com/login"), "view")
        self.assertEqual(self.where("https://forza.dyotson.dev/index.html", link_click=True,
                                    current="https://forza.dyotson.dev/account.html"), "view")

    def test_everything_else_goes_to_the_browser(self):
        site = "https://forza.dyotson.dev/account.html"
        for url, kw in (("https://github.com/FH1-ReImagined", {"link_click": True, "current": site}),
                        ("https://discord.com/invite/abc", {"link_click": True, "current": site}),
                        ("https://discord.com/x", {"link_click": True, "current": "https://github.com/login"}),
                        ("https://discord.gg/abc", {"link_click": True}),
                        ("https://docs.github.com/en/site-policy", {"link_click": True}),
                        ("https://gist.github.com/x", {}),
                        ("https://www.discord.com/", {}),
                        ("https://discord.com.evil.example/login", {}),
                        ("https://evil-github.com/login", {}),
                        ("https://forza.dyotson.dev@evil.example/account.html", {}),
                        ("https://forza.dyotson.dev.evil.example/account.html", {}),
                        ("https://forza.dyotson.dev:8443/account.html", {}),
                        ("https://github.com:444/login", {}),
                        ("http://example.com/", {"link_click": True}),
                        ("mailto:someone@example.com", {"link_click": True})):
            self.assertEqual(self.where(url, **kw), "browser", url)

    def test_refused(self):
        for url, kw in (("http://forza.dyotson.dev/account.html", {}),
                        ("http://github.com/login", {}),
                        ("http://example.com/", {}),               # a redirect to plain HTTP
                        ("file:///etc/passwd", {"link_click": True}),
                        ("data:text/html,<h1>x</h1>", {"link_click": True}),
                        ("javascript:alert(1)", {"link_click": True}),
                        ("view-source:https://forza.dyotson.dev/", {}),
                        ("chrome://settings", {"link_click": True}),
                        ("mailto:someone@example.com", {}),
                        ("steam://run/1", {"link_click": True}),
                        ("https:///nohost", {})):
            self.assertEqual(self.where(url, **kw), "refuse", url)

    def test_frames(self):
        for url, where in (("https://newassets.hcaptcha.com/captcha/v1/x", "view"),
                           ("https://octocaptcha.com/", "view"), ("about:blank", "view"),
                           ("about:srcdoc", "view"), ("blob:https://discord.com/1", "view"),
                           ("http://example.com/", "refuse"), ("file:///etc/passwd", "refuse"),
                           ("data:text/html,x", "refuse"), ("javascript:1", "refuse")):
            self.assertEqual(self.where(url, main_frame=False), where, url)

    def test_the_site_is_the_service_s(self):
        site = "http://127.0.0.1:8123"
        self.assertEqual(self.where("http://127.0.0.1:8123/account.html", site=site), "view")
        self.assertEqual(self.where("http://127.0.0.1:8124/account.html", site=site), "refuse")
        self.assertEqual(self.where("https://forza.dyotson.dev/account.html", site=site), "browser")


class Profile(unittest.TestCase):
    """The view's own profile folder and "Sign out of the website in the launcher"."""

    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.storage = Path(root.name) / "web-account"

    def test_it_lives_in_the_data_folder(self):
        from forza_launcher import paths
        self.assertEqual(web_view_module().storage_dir(), paths.data_home() / "web-account")

    def test_forget_deletes_it(self):
        wv = web_view_module()
        self.assertFalse(wv.has_profile(self.storage))
        (self.storage / "Local Storage").mkdir(parents=True)
        (self.storage / "Cookies").write_bytes(b"x")
        self.assertTrue(wv.has_profile(self.storage))
        self.assertTrue(wv.forget(self.storage))
        self.assertFalse(self.storage.exists())
        self.assertFalse(wv.has_profile(self.storage))

    def test_a_folder_still_in_use_goes_at_the_next_open(self):
        wv = web_view_module()
        (self.storage / "Cookies").parent.mkdir(parents=True)
        (self.storage / "Cookies").write_bytes(b"x")
        with mock.patch.object(wv.shutil, "rmtree"):      # Windows: a file still open
            self.assertFalse(wv.forget(self.storage))
        self.assertTrue((self.storage.parent / wv.FORGET_MARKER).exists())
        self.assertFalse(wv.has_profile(self.storage))    # already signed out for the card
        wv._forget_pending(self.storage)                  # what the next window does first
        self.assertFalse(self.storage.exists())
        self.assertFalse((self.storage.parent / wv.FORGET_MARKER).exists())


class Page(http.server.BaseHTTPRequestHandler):
    """A local stand-in for the account page, with links the launcher's window must keep out."""
    BODY = (b"<!doctype html><title>account</title>"
            b"<a id=out href='https://example.com/help'>help</a>"
            b"<a id=gh href='https://github.com/FH1-ReImagined'>source</a>"
            b"<a id=blank target=_blank href='https://example.org/new'>new</a>"
            b"<a id=file href='file:///etc/hostname'>file</a>"
            b"<a id=same href='/second.html'>second</a>")

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = self.BODY if self.path.startswith("/account.html") else b"<!doctype html><title>second</title>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StartCheck(unittest.TestCase):
    """web_view.start_mode (#852): the first window of a session is tried in a process of its own,
    then without the GPU, then the browser opens. Fake probes: nothing is started."""

    def setUp(self):
        wv = web_view_module()
        for name, value in (("_mode", []), ("_built", None)):
            p = mock.patch.object(wv, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.logged = []
        p = mock.patch.object(wv.paths, "log_error", side_effect=lambda what, exc=None: self.logged.append(what))
        p.start()
        self.addCleanup(p.stop)

    def mode(self, answers, platform="linux"):
        asked = []

        def check(software):
            asked.append(software)
            return answers[len(asked) - 1]
        return web_view_module().start_mode(check, platform), asked

    def test_the_gpu_first_then_without_it_then_the_browser(self):
        wv = web_view_module()
        for answers, expected, asked in (([True], wv.GPU, [False]),
                                         ([False, True], wv.SOFTWARE, [False, True]),
                                         ([False, False], None, [False, True])):
            wv._mode.clear()
            self.assertEqual(self.mode(answers), (expected, asked), answers)

    def test_once_per_session(self):
        wv = web_view_module()
        self.assertEqual(self.mode([False, True])[0], wv.SOFTWARE)
        self.assertEqual(self.mode([True]), (wv.SOFTWARE, []))   # not asked again

    def test_windows_starts_the_view_itself(self):
        wv = web_view_module()
        self.assertEqual(self.mode([], platform="win32"), (wv.GPU, []))

    def test_without_the_gpu(self):
        wv = web_view_module()
        env = wv.software_environment({"PATH": "/usr/bin", "QTWEBENGINE_CHROMIUM_FLAGS": "--lang=en"})
        self.assertEqual(env, {"PATH": "/usr/bin", "QTWEBENGINE_CHROMIUM_FLAGS": "--lang=en --disable-gpu",
                               "QT_QUICK_BACKEND": "software"})
        self.assertEqual(wv.software_environment(env), env)       # the flag only once
        self.assertEqual(wv.software_environment({})["QTWEBENGINE_CHROMIUM_FLAGS"], "--disable-gpu")

    def test_the_probe_is_the_launcher_itself(self):
        wv = web_view_module()
        with mock.patch.object(wv.paths, "FROZEN", True), mock.patch.object(sys, "executable", "/opt/fh1/forza-launcher"):
            self.assertEqual(wv.probe_command(), ["/opt/fh1/forza-launcher", "--web-view-probe"])
        cmd = wv.probe_command()
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(Path(cmd[1]), Path(__file__).resolve().parent / "main.py")
        self.assertEqual(cmd[2:], ["--web-view-probe"])

    def test_the_probe_s_answer(self):
        import subprocess
        wv = web_view_module()
        runs = []

        def run(code):
            def fake(cmd, **kw):
                runs.append((cmd, kw))
                if code == "timeout":
                    raise subprocess.TimeoutExpired(cmd, kw["timeout"])
                if code == "missing":
                    raise FileNotFoundError(cmd[0])
                return subprocess.CompletedProcess(cmd, code, "line\n" * 40 + "GLOzone not found for unknown\n")
            return fake
        self.assertTrue(wv.probe(run=run(0)))
        self.assertEqual(self.logged, [])
        self.assertEqual(runs[0][0], wv.probe_command())
        self.assertNotIn("QT_QUICK_BACKEND", runs[0][1]["env"])
        self.assertFalse(wv.probe(software=True, run=run(-6)))     # Chromium's abort
        self.assertEqual(runs[1][1]["env"]["QT_QUICK_BACKEND"], "software")
        self.assertIn("--disable-gpu", runs[1][1]["env"]["QTWEBENGINE_CHROMIUM_FLAGS"])
        self.assertIn("without the GPU: exit -6", self.logged[0])
        self.assertTrue(self.logged[0].endswith("GLOzone not found for unknown"))   # the output's end
        self.assertLess(self.logged[0].count("line"), 30)
        self.assertFalse(wv.probe(run=run(1)))
        self.assertFalse(wv.probe(run=run("timeout"), timeout=3))
        self.assertIn("no answer in 3 s", self.logged[-1])
        self.assertFalse(wv.probe(run=run("missing")))

    def test_create_uses_the_answer(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        wv = web_view_module()
        made = []

        class View:
            def __init__(self, site, parent, storage):
                made.append((site, dict(os.environ)))
        with mock.patch.object(wv, "available", return_value=True), \
                mock.patch.object(wv, "_classes", return_value=View), \
                mock.patch.object(wv, "_quit_hooked", True), \
                mock.patch.object(wv.sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"QTWEBENGINE_CHROMIUM_FLAGS": "", "QT_QUICK_BACKEND": ""}):
            with mock.patch.object(wv, "probe", return_value=False) as probe:
                self.assertIsNone(wv.create("https://forza.dyotson.dev"))      # the browser then
                self.assertIsNone(wv.create("https://forza.dyotson.dev"))
            self.assertEqual(probe.call_count, 2)                             # GPU, CPU; once a session
            self.assertEqual(made, [])
            wv._mode.clear()
            with mock.patch.object(wv, "probe", side_effect=lambda software: software):
                self.assertIsNotNone(wv.create("https://forza.dyotson.dev"))
            self.assertEqual(made[0][1]["QT_QUICK_BACKEND"], "software")
            self.assertEqual(made[0][1]["QTWEBENGINE_CHROMIUM_FLAGS"], "--disable-gpu")
            wv._mode[:] = [wv.GPU]
            os.environ.update(QTWEBENGINE_CHROMIUM_FLAGS="", QT_QUICK_BACKEND="")
            self.assertIsNotNone(wv.create("https://forza.dyotson.dev"))
            self.assertEqual(made[1][1]["QT_QUICK_BACKEND"], "")                # left alone


@unittest.skipUnless(web_view_module().available(), "Qt WebEngine is not installed")
class RealWindow(unittest.TestCase):
    """The real Qt WebEngine view against a page on 127.0.0.1: the allow-list, new windows, the
    profile's folder and the teardown. No network beyond this machine."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication, Qt
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        cls.app = QApplication.instance() or QApplication([])
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.site = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def spin(self, until, seconds=20):
        from PySide6.QtCore import QEvent
        deadline = time.monotonic() + seconds
        while not until() and time.monotonic() < deadline:
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)   # as the event loop does
            time.sleep(0.01)
        return until()

    def run_js(self, view, script):
        out = []
        view.page.runJavaScript(script, 0, out.append)
        self.assertTrue(self.spin(lambda: out), script)
        return out[0]

    def test_the_window(self):
        wv = web_view_module()
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        storage = Path(root.name) / "web-account"
        opened = []
        with mock.patch.object(wv, "open_in_browser", side_effect=lambda url: opened.append(
                url if isinstance(url, str) else url.toString()) or False):   # no browser opens
            view = wv.create(self.site, None, storage)
            self.assertIsNotNone(view)
            notices = []
            view.notice.connect(notices.append)
            self.addCleanup(view.close_down)
            self.assertEqual(Path(view.profile.persistentStoragePath()), storage)
            self.assertFalse(view.profile.isOffTheRecord())
            view.resize(800, 600)
            view.show()
            loaded = []
            view.view.loadFinished.connect(loaded.append)
            view.load(self.site + "/account.html")
            self.assertTrue(self.spin(lambda: loaded and view.view.title() == "account"))
            self.assertEqual(view.address.text(), self.site)
            for anchor, expected in (("out", "https://example.com/help"),
                                     ("gh", "https://github.com/FH1-ReImagined"),
                                     ("blank", "https://example.org/new")):
                self.run_js(view, f"document.getElementById('{anchor}').click(); 1")
                self.assertTrue(self.spin(lambda: expected in opened), anchor)
                self.assertIn(wv.not_opened(expected), notices)   # the link, to open by hand
            self.run_js(view, "document.getElementById('file').click(); 1")
            self.spin(lambda: False, 0.5)
            self.assertEqual(view.view.title(), "account")       # stayed: nothing loaded in the window
            self.assertNotIn("file:///etc/hostname", opened)
            # every page permission is denied
            self.run_js(view, "window.__n = 'asking'; Notification.requestPermission()"
                              ".then(function (p) { window.__n = p; }); 1")
            self.assertTrue(self.spin(lambda: self.run_js(view, "window.__n") != "asking"))
            self.assertEqual(self.run_js(view, "window.__n"), "denied")
            # the site's own links stay in the window
            self.run_js(view, "document.getElementById('same').click(); 1")
            self.assertTrue(self.spin(lambda: view.view.title() == "second"))
            self.assertEqual(len(opened), 3)
            page = view.page
            ended = []
            page.destroyed.connect(lambda *_: ended.append("page"))
            view.profile.destroyed.connect(lambda *_: ended.append("profile"))
            view.close_down()
            view.close_down()                                      # twice is harmless
            self.assertTrue(self.spin(lambda: len(ended) == 2))
            self.assertEqual(ended, ["page", "profile"])
            self.assertEqual(wv._closing, [])
            self.assertEqual(wv._live, [])
            view.deleteLater()
            self.assertTrue(storage.is_dir())
            # "Sign out of the website in the launcher", then the window again in the same session
            self.assertTrue(wv.forget(storage))
            again = wv.create(self.site, None, storage)
            self.addCleanup(again.close_down)
            loaded = []
            again.view.loadFinished.connect(loaded.append)
            again.load(self.site + "/account.html")
            self.assertTrue(self.spin(lambda: loaded and again.view.title() == "account"))
            self.assertTrue(storage.is_dir())
            again.close_down()
            self.assertTrue(self.spin(lambda: not wv._closing))
            again.deleteLater()


if __name__ == "__main__":
    unittest.main()
