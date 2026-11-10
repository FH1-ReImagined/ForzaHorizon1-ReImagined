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
        self.assertIn("Play online on the public server once", str(e.exception))

    def test_refusals_become_the_player_s_messages(self):
        for status, error, says in ((409, "play online first", "Play online on the public server once"),
                                    (403, "not available", "cannot link"),
                                    (429, "changed in the past day", "Try again tomorrow"),
                                    (503, "linking is not available", "not available right now"),
                                    (500, "something new", "answered 500")):
            self.svc.fail_with = (status, {"error": error})
            with self.assertRaises(web_account.WebAccountError) as e:
                self.start()
            self.assertIn(says, str(e.exception))

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

    def dialog(self):
        from forza_launcher.ui import web_account as ui
        opened = []
        patcher = mock.patch.object(ui.QDesktopServices, "openUrl", side_effect=lambda url: opened.append(url) or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        fast = mock.patch.object(web_account, "POLL_SECONDS", 0.1)
        fast.start()
        self.addCleanup(fast.stop)
        d = ui.LinkDialog(IDENTITY, None, self.base)
        self.addCleanup(d.deleteLater)
        return d, opened

    def setUp(self):
        super().setUp()
        p = mock.patch.object(web_account.fetch, "urlopen", self.opener)
        p.start()
        self.addCleanup(p.stop)

    def test_the_dialog_links_after_both_sides_approve(self):
        d, opened = self.dialog()
        self.spin(lambda: d.request is not None and d.code.text())
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].toString().startswith(self.base[:-4] + "account.html#link="))
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
        self.svc.fail_with = (409, {"error": "play online first"})
        d, opened = self.dialog()
        self.spin(lambda: "Play online" in d.text.text())
        self.assertEqual(opened, [])
        self.assertEqual(d.close_button.text(), "Close")

    def test_the_card_shows_only_for_the_public_server_and_asks_only_after_a_link(self):
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
                self.assertTrue(page.web_account_frame.isVisibleTo(online))
                asked.assert_not_called()  # never linked: nothing is sent
                cfg.online["mode"] = "offline"   # as Servers' Offline row does
                page.sync_server()
                self.assertFalse(page.web_account_frame.isVisibleTo(online))
                cfg.online["mode"] = "public"
                page.sync_server()
                self.assertTrue(page.web_account_frame.isVisibleTo(online))
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


if __name__ == "__main__":
    unittest.main()
