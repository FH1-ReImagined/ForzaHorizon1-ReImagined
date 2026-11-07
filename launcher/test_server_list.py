"""The Servers page (#587, server_list.py, ui/servers_page.py) against fake Forza Live servers on
127.0.0.1: the signed list (its signature, expiry and the server's own signing code), status and
ping, the rows the page shows, picking a server saves it as Online > Online shows it, and the page's
checks off the GUI thread, when it opens and every 30 s while it stays open. No test reaches a real
server: an address that is not a fake one is never pinged."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import PUBLIC_SERVER, config, ed25519, server_list
from forza_launcher.config import Config

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

REPO = Path(__file__).resolve().parent.parent
SEED = bytes([9] * 32)
KEY = ed25519.public_key(SEED).hex()


def sign(servers, seed=SEED, issued=None, expires=None):
    """A /servers.json document as the server makes it (server/forza_live/core/server_list.py)."""
    issued = int(time.time()) if issued is None else issued
    text = json.dumps({"issued": issued, "expires": issued + 3600 if expires is None else expires,
                       "servers": servers}, separators=(",", ":"))
    return {"format": server_list.FORMAT, "key": ed25519.public_key(seed).hex(), "list": text,
            "signature": ed25519.sign(seed, server_list.TAG + text.encode()).hex()}


class FakeServer:
    """A Forza Live server's HTTP port: /status and /servers.json. Its address (as players type
    it) is the port before the HTTP port."""

    def __init__(self, players=0, status=200, document=None, body=None, server="forza-live"):
        self.players, self.status, self.document, self.body = players, status, document, body
        self.server_field = server
        self.requests = []
        self.threads = set()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fake.requests.append(self.path)
                fake.threads.add(threading.current_thread().name)
                if self.path == "/status":
                    code, data = fake.status, {"server": fake.server_field, "players": fake.players}
                    payload = json.dumps(data).encode()
                elif self.path == "/servers.json" and (fake.document is not None or fake.body is not None):
                    code = 200
                    payload = fake.body if fake.body is not None else json.dumps(fake.document).encode()
                else:
                    code, payload = 404, b"not found"
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.address = f"127.0.0.1:{self.port - 1}"
        self.url = f"http://127.0.0.1:{self.port}/servers.json"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_address():
    """The address of a server that is not there (nothing listens on its HTTP port)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"127.0.0.1:{port - 1}"


def local_ping_all(addresses, timeout=2):
    """ping_all for tests: only 127.0.0.1 is pinged; anything else is down without a request."""
    out = {}
    for a in addresses:
        out[a] = (server_list.ping(a, timeout) if a.startswith("127.0.0.1:")
                  else server_list.Status(False, error="not pinged in tests"))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")

    def fake(self, **kw):
        server = FakeServer(**kw)
        self.addCleanup(server.close)
        return server


class SignedList(Base):
    def test_a_list_signed_by_the_key_is_taken(self):
        doc = sign([{"name": "Main", "address": "your.host:21200", "main": True, "verified": True},
                    {"name": "Community", "address": "192.0.2.10:21200", "region": "Europe"}])
        servers = server_list.parse_servers(server_list.check_document(doc, bytes.fromhex(KEY)))
        self.assertEqual([(s.name, s.kind, s.verified) for s in servers],
                         [("Main", "main", True), ("Community", "listed", False)])
        self.assertEqual(servers[1].region, "Europe")

    def test_a_changed_list_or_another_key_is_refused(self):
        doc = sign([{"name": "Community", "address": "192.0.2.10:21200"}])
        changed = dict(doc, list=doc["list"].replace("192.0.2.10", "192.0.2.66"))
        for document, key in ((changed, KEY), (doc, ed25519.public_key(bytes(32)).hex()),
                              (sign([], seed=bytes(32)), KEY), (dict(doc, signature="zz"), KEY),
                              (dict(doc, format="other"), KEY), ([doc], KEY), ({}, KEY)):
            with self.assertRaises(server_list.ListError):
                server_list.check_document(document, bytes.fromhex(key))

    def test_an_expired_list_is_refused(self):
        doc = sign([], issued=1000, expires=2000)
        with self.assertRaisesRegex(server_list.ListError, "expired"):
            server_list.check_document(doc, bytes.fromhex(KEY))
        self.assertEqual(server_list.check_document(doc, bytes.fromhex(KEY), now=1500), [])

    def test_bad_entries_are_left_out(self):
        servers = server_list.parse_servers([
            {"name": "", "address": "192.0.2.1"}, {"name": "x", "address": "http://192.0.2.2/"},
            "nothing", {"name": "Good", "address": "192.0.2.3:21200"},
            {"name": "Again", "address": "192.0.2.3:21200"},
            {"name": "Not first", "address": "192.0.2.4", "main": True}])
        self.assertEqual([(s.name, s.kind) for s in servers], [("Good", "listed"), ("Not first", "listed")])

    def test_the_servers_own_signing_code_makes_lists_the_launcher_takes(self):
        if not (REPO / "server" / "forza_live" / "core" / "server_list.py").is_file():
            self.skipTest("the server/ submodule is not checked out, or is before the server list")
        sys.path.insert(0, str(REPO / "server"))
        self.addCleanup(sys.path.remove, str(REPO / "server"))
        from forza_live.core import server_list as published
        entry = published.clean_entry({"name": "Example", "address": "192.0.2.10:21200",
                                       "region": "Europe", "verified": True})
        doc = json.loads(json.dumps(published.signed_document(SEED, [entry])))
        self.assertEqual(published.FORMAT, server_list.FORMAT)
        self.assertEqual(published.TAG, server_list.TAG)
        servers = server_list.parse_servers(server_list.check_document(doc, bytes.fromhex(KEY)))
        self.assertEqual([(s.name, s.address, s.verified) for s in servers],
                         [("Example", "192.0.2.10:21200", True)])


class Fetching(Base):
    def test_fetches_and_checks_the_list(self):
        server = self.fake(document=sign([{"name": "Main", "address": "192.0.2.1:21200", "main": True}]))
        servers = server_list.fetch_list(server.url, KEY)
        self.assertEqual([s.name for s in servers], ["Main"])
        self.assertEqual(server.requests, ["/servers.json"])

    def test_no_key_fetches_nothing(self):
        server = self.fake(document=sign([]))
        self.assertEqual(server_list.fetch_list(server.url, ""), [])
        self.assertEqual(server.requests, [])
        self.assertEqual(len(bytes.fromhex(server_list.LIST_KEY)), 32)  # the public server's

    def test_what_is_refused(self):
        bad = self.fake(document=sign([], seed=bytes(32)))
        with self.assertRaisesRegex(server_list.ListError, "signature"):
            server_list.fetch_list(bad.url, KEY)
        junk = self.fake(body=b"<html>")
        with self.assertRaisesRegex(server_list.ListError, "not a server list"):
            server_list.fetch_list(junk.url, KEY)
        big = self.fake(body=b" " * (server_list.MAX_LIST + 1))
        with self.assertRaisesRegex(server_list.ListError, "too large"):
            server_list.fetch_list(big.url, KEY)
        missing = self.fake()
        with self.assertRaisesRegex(server_list.ListError, "could not get"):
            server_list.fetch_list(missing.url, KEY)
        with self.assertRaisesRegex(server_list.ListError, "could not get"):
            server_list.fetch_list(f"http://{closed_address().rsplit(':', 1)[0]}:1/servers.json", KEY, timeout=2)

    def test_the_list_comes_from_the_main_servers_http_port(self):
        self.assertEqual(server_list.list_url(), "http://forza-reimagined.playit.plus:56678/servers.json")


class Ping(Base):
    def test_an_answering_server_is_up_with_its_players_and_ping(self):
        server = self.fake(players=3)
        st = server_list.ping(server.address)
        self.assertTrue(st.up)
        self.assertEqual(st.players, 3)
        self.assertGreaterEqual(st.ping_ms, 1)
        self.assertEqual(server.requests, ["/status"])

    def test_down(self):
        for st in (server_list.ping(closed_address(), 2), server_list.ping(self.fake(status=500).address),
                   server_list.ping(self.fake(server="something else").address),
                   server_list.ping("not an address")):
            self.assertFalse(st.up)
            self.assertIsNone(st.ping_ms)
            self.assertTrue(st.error)

    def test_ping_all_pings_every_address_once(self):
        a, b = self.fake(players=1), self.fake(players=2)
        down = closed_address()
        found = server_list.ping_all([a.address, b.address, a.address, down, server_list.LOCAL], 2)
        self.assertEqual(set(found), {a.address, b.address, down})
        self.assertEqual((found[a.address].players, found[b.address].players), (1, 2))
        self.assertFalse(found[down].up)
        self.assertEqual(a.requests, ["/status"])


class Choosing(Base):
    def test_rows_main_first_then_listed_this_pc_and_added(self):
        listed = [server_list.Server("Community", "192.0.2.10:21200")]
        rows = server_list.entries(listed, ["192.0.2.20", "192.0.2.10:21200", PUBLIC_SERVER])
        self.assertEqual([(r.kind, r.address) for r in rows],
                         [("main", PUBLIC_SERVER), ("listed", "192.0.2.10:21200"),
                          ("local", server_list.LOCAL), ("added", "192.0.2.20")])
        own = server_list.Server("Main", "your.host:21200", kind="main")
        self.assertEqual(server_list.entries([own], [])[0], own)

    def test_picking_saves_it_like_settings_online(self):
        server_list.choose(self.cfg, "192.0.2.10:21200")
        self.assertEqual((self.cfg.online["mode"], self.cfg.online["custom_server"]), ("custom", "192.0.2.10:21200"))
        self.assertEqual(self.cfg.server_spec(), "192.0.2.10:21200")
        self.assertIn('live_server = "192.0.2.10:21200"', config.render_toml(self.cfg))
        self.assertEqual(server_list.chosen(self.cfg), "192.0.2.10:21200")
        server_list.choose(self.cfg, PUBLIC_SERVER)
        self.assertEqual(self.cfg.online["mode"], "public")
        self.assertEqual(server_list.chosen(self.cfg), PUBLIC_SERVER)
        server_list.choose(self.cfg, server_list.LOCAL)
        self.assertEqual(self.cfg.online["mode"], "local")
        self.assertNotIn("live_server", config.render_toml(self.cfg))
        with self.assertRaises(ValueError):
            server_list.choose(self.cfg, "not an address")

    def test_added_addresses_are_kept(self):
        self.assertEqual(server_list.add_address(self.cfg, "  Your.Host:21200 "), "your.host:21200")
        server_list.add_address(self.cfg, "your.host:21200")
        with self.assertRaises(ValueError):
            server_list.add_address(self.cfg, "your host")
        self.cfg.save()
        again = Config(self.root / "launcher.json")
        self.assertEqual(server_list.added(again), ["your.host:21200"])
        server_list.remove_address(again, "your.host:21200")
        self.assertEqual(server_list.added(again), [])

    def test_my_own_server_from_settings_is_listed(self):
        self.cfg.online.update(mode="custom", custom_server="192.0.2.30:21200")
        self.assertEqual(server_list.page_addresses(self.cfg), ["192.0.2.30:21200"])
        self.cfg.online["custom_server"] = PUBLIC_SERVER
        self.assertEqual(server_list.page_addresses(self.cfg), [])


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Page(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        from forza_launcher.ui.servers_page import ServersPage
        self.main = self.fake(players=4)
        self.other = self.fake(players=1)
        self.down = closed_address()
        self.main.document = sign([
            {"name": "Test main", "address": self.main.address, "main": True, "verified": True},
            {"name": "Community", "address": self.other.address, "region": "Europe", "verified": True},
            {"name": "Gone", "address": self.down, "region": "Asia"}])
        self.ping_threads = set()

        def ping_all(addresses):
            self.ping_threads.add(threading.current_thread())
            return local_ping_all(addresses)

        self.page = ServersPage(self.cfg, None, fetch=lambda: server_list.fetch_list(self.main.url, KEY),
                                ping_all=ping_all)
        self.addCleanup(self.page.deleteLater)
        self.changed = []
        self.page.changed.connect(lambda: self.changed.append(dict(self.cfg.online)))

    def wait(self, checks=1):
        until = time.monotonic() + 8
        while self.page.checks < checks and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertGreaterEqual(self.page.checks, checks)

    def cells(self):
        t = self.page.table
        return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]

    def test_opening_lists_the_servers_with_players_ping_and_up_or_down(self):
        self.page.refresh()
        self.assertTrue(self.page.busy())
        self.wait()
        rows = self.cells()
        self.assertEqual([r[0] for r in rows], ["Test main  (in use)", "Community", "Gone", "This PC", "Offline"])
        main, community, gone, local, offline = rows
        self.assertEqual(main[2], "4")
        self.assertTrue(main[3].endswith(" ms"))
        self.assertEqual(main[4], "Up")
        self.assertEqual((community[1], community[2], community[4]), ("Europe", "1", "Up"))
        self.assertEqual((gone[1], gone[2], gone[3], gone[4]), ("Asia", "", "", "Down"))
        self.assertEqual(local[4], "Starts with the game")
        self.assertEqual(offline[4], "No Xbox LIVE, no server")
        self.assertEqual(self.main.requests.count("/servers.json"), 1)
        self.assertNotIn(threading.main_thread(), self.ping_threads)  # off the GUI thread

    def test_pings_every_30_seconds_while_open_and_stops_when_left(self):
        self.page.refresh()
        self.wait()
        self.assertTrue(self.page.timer.isActive())
        self.assertEqual(self.page.timer.interval(), 30000)
        self.page.timer.timeout.emit()
        self.wait(2)
        self.assertEqual(self.main.requests.count("/status"), 2)
        self.assertEqual(self.main.requests.count("/servers.json"), 1)  # the list once per opening
        self.page.leave()
        self.assertFalse(self.page.timer.isActive())
        self.page.show()
        self.page.refresh()
        self.page.hide()
        self.assertFalse(self.page.timer.isActive())
        self.wait(3)

    def test_a_server_failing_its_pings_stays_listed_as_down(self):
        self.page.refresh()
        self.wait()
        self.other.close()
        self.page.timer.timeout.emit()
        self.wait(2)
        rows = {r[0]: r for r in self.cells()}
        self.assertEqual(rows["Community"][4], "Down")
        self.main.document = None   # and the list is gone too: what was listed stays
        self.page.check()
        self.wait(3)
        self.assertIn("Community", [r[0] for r in self.cells()])

    def test_using_a_server_saves_it_for_play(self):
        self.page.refresh()
        self.wait()
        self.page.table.selectRow(1)
        self.page.use_selected()
        self.assertEqual(self.cfg.online["mode"], "custom")
        self.assertEqual(self.cfg.online["custom_server"], self.other.address)
        self.assertEqual(len(self.changed), 1)
        self.assertEqual(self.cells()[1][0], "Community  (in use)")
        self.page.table.selectRow(3)   # This PC
        self.page.use_selected()
        self.assertEqual(self.cfg.online["mode"], "local")
        self.page.table.selectRow(0)   # the main server from the list: Public server
        self.assertIn("official leaderboards", self.page.details.text())
        self.page.use_selected()
        self.assertEqual(self.cfg.online["mode"], "public")
        self.assertEqual(self.cells()[0][0], "Test main  (in use)")

    def test_the_built_in_main_server_is_public_server(self):
        self.main.document = None
        self.page.refresh()
        self.wait()
        self.assertTrue(self.page.list_note)
        self.assertIn("not available", self.page.note.text())
        self.assertEqual(self.page.rows[0].address, PUBLIC_SERVER)
        self.assertEqual(self.cells()[0][4], "Down")   # not pinged in tests
        self.cfg.online["mode"] = "offline"
        self.page.table.selectRow(0)
        self.page.use_selected()
        self.assertEqual(self.cfg.online["mode"], "public")

    def test_adding_an_address(self):
        self.page.address.setText("your host")
        self.page.add_typed()
        self.assertTrue(self.page.message.text().startswith("Type a server address"))
        self.assertEqual(self.changed, [])
        friend = self.fake(players=2)
        self.page.address.setText(friend.address)
        self.page.add_typed()
        self.assertEqual(server_list.added(self.cfg), [friend.address])
        self.assertEqual(len(self.changed), 1)
        self.assertEqual(self.page.current_server().address, friend.address)
        self.assertTrue(self.page.remove_button.isEnabled())
        self.page.refresh()
        self.wait()
        row = next(r for r in self.cells() if r[0] == friend.address)
        self.assertEqual((row[1], row[2], row[4]), ("Added by you", "2", "Up"))
        self.page.use_selected()
        self.assertFalse(self.page.remove_button.isEnabled())   # in use
        server_list.choose(self.cfg, server_list.LOCAL)
        self.page.fill()
        self.page._select(friend.address)
        self.page.remove_selected()
        self.assertEqual(server_list.added(self.cfg), [])

    def test_with_online_features_off_only_check_servers_checks(self):
        self.cfg.d["online_features"]["on"] = False
        self.page.refresh()
        for _ in range(20):
            self.app.processEvents()
        self.assertEqual((self.page.checks, self.main.requests), (0, []))
        self.assertFalse(self.page.timer.isActive())
        self.assertIn("Check servers", self.page.note.text())
        self.page.check_button.click()
        self.wait()
        self.assertTrue(self.page.timer.isActive())
        self.assertIn("/servers.json", self.main.requests)


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Window(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_server_picked_on_the_servers_page_is_saved_and_shown_in_settings(self):
        from forza_launcher import build_env
        from forza_launcher.ui.window import MainWindow, SERVERS_PAGE_ID
        community = self.fake(players=5)
        doc = sign([{"name": "Community", "address": community.address}])
        self.cfg.loaded = True
        self.cfg.advanced["data_root"] = str(self.root)
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        self.cfg.d["check_updates"] = False
        patches = [patch("forza_launcher.paths.config_dir", return_value=self.root),
                   patch("forza_launcher.game.find_game", return_value=(None, [])),
                   patch("forza_launcher.build_env._detect_tools", return_value=build_env.Tools()),
                   patch.object(server_list, "fetch_list",
                                lambda: server_list.parse_servers(
                                    server_list.check_document(doc, bytes.fromhex(KEY)))),
                   patch.object(server_list, "ping_all", local_ping_all)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        win = MainWindow(self.cfg)
        self.addCleanup(win.deleteLater)
        self.addCleanup(win.close)
        self.assertEqual(win.nav_button(SERVERS_PAGE_ID).text(), "Online")
        win.show_page(SERVERS_PAGE_ID)
        until = time.monotonic() + 8
        while win.servers.checks < 1 and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(win.servers.timer.isActive())
        win.servers._select(community.address)
        win.servers.use_selected()
        self.assertEqual(win.settings.server_status.text(), "Play uses: Your server: " + community.address)
        saved = json.loads((self.root / "launcher.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["online"]["mode"], saved["online"]["custom_server"]), ("custom", community.address))
        self.assertEqual(self.cfg.server_spec(), community.address)
        win.show_page(0)
        self.assertFalse(win.servers.timer.isActive())


if __name__ == "__main__":
    unittest.main()
