"""Can't get online (connection_test.py, offline_notice.py): Online > Servers > Test connection and
what each result tells the player, the UDP probe against a fake Forza Live UDP port on 127.0.0.1, and
the banner when Play is Offline because "Online features" was turned off, with its "Go online".
No test reaches a real server: the network is replaced or on 127.0.0.1."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import PUBLIC_SERVER, connection_test as ct, online_features, server_list
from forza_launcher.config import Config

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

UP = server_list.Status(True, 3, 42)
DOWN = server_list.Status(False, error="timed out")


class FakeUdp:
    """A Forza Live UDP port: answers each datagram with error 1 "not registered", as the server
    answers an address it has no player for; `silent` drops everything (a firewall)."""

    def __init__(self, silent=False):
        self.silent, self.got = silent, []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.port = self.sock.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            self.got.append(data)
            if not self.silent:
                text = b"not registered"
                self.sock.sendto(ct.MAGIC + bytes([3, 1]) + len(text).to_bytes(2, "big") + text, addr)

    def close(self):
        self.running = False
        self.thread.join(2)
        self.sock.close()


def network(resolved="203.0.113.5", status=UP, udp=True):
    """run()'s network: `resolved` None for a name that does not resolve."""
    calls = []

    def resolve(host, port):
        calls.append(("resolve", host, port))
        if resolved is None:
            raise socket.gaierror(-2, "Name or service not known")
        return resolved

    def ping(address, timeout):
        calls.append(("status", address))
        return status

    def answers(ip, port):
        calls.append(("udp", ip, port))
        return udp

    return dict(resolve=resolve, ping=ping, udp=answers), calls


class Results(unittest.TestCase):
    def test_each_outcome(self):
        for kw, kind in ((dict(), ct.REACHABLE),
                         (dict(udp=False), ct.UDP_BLOCKED),
                         (dict(udp=False, status=DOWN), ct.DOWN),
                         (dict(status=DOWN), ct.WEB_BLOCKED),
                         (dict(resolved=None), ct.NO_DNS)):
            with self.subTest(kind):
                net, _ = network(**kw)
                self.assertEqual(ct.run("example.org:21200", **net).kind, kind)

    def test_what_is_asked(self):
        net, calls = network()
        r = ct.run(PUBLIC_SERVER, **net)
        self.assertEqual((r.host, r.port, r.ping_ms), ("forza-reimagined.playit.plus", 56677, 42))
        self.assertIn(("resolve", "forza-reimagined.playit.plus", 56677), calls)
        self.assertIn(("status", PUBLIC_SERVER), calls)        # its HTTP port, server_list.ping
        self.assertIn(("udp", "203.0.113.5", 56677), calls)    # the game's port, at the IPv4 address

    def test_a_name_that_does_not_resolve_is_not_asked_further(self):
        net, calls = network(resolved=None)
        r = ct.run("blocked.example", **net)
        self.assertEqual((r.kind, r.port), (ct.NO_DNS, 21200))
        self.assertEqual([c[0] for c in calls], ["resolve"])

    def test_not_an_address(self):
        net, calls = network()
        self.assertEqual(ct.run("not an address", **net).kind, ct.NOT_AN_ADDRESS)
        self.assertEqual(calls, [])

    def test_addresses(self):
        self.assertEqual(ct.split("example.org"), ("example.org", 21200))
        self.assertEqual(ct.split("Example.org:5000"), ("Example.org", 5000))
        self.assertEqual(ct.split("[::1]:5000"), ("::1", 5000))
        self.assertIsNone(ct.split("a:b:c"))

    def test_each_result_says_what_it_is_and_how_to_fix_it(self):
        words = {ct.REACHABLE: ("Reachable", "42 ms"), ct.UDP_BLOCKED: ("UDP blocked", "firewall", "VPN"),
                 ct.WEB_BLOCKED: ("TCP 21201", "firewall"), ct.NO_DNS: ("doesn't resolve", "DNS"),
                 ct.DOWN: ("Server down", "another server"), ct.NOT_AN_ADDRESS: ("host:port",)}
        self.assertEqual(set(words), set(ct.RESULTS))
        for kind, expected in words.items():
            with self.subTest(kind):
                headline, fix = ct.message(ct.Result(kind, "example.org", "example.org", 21200, 42))
                self.assertTrue(headline and fix)
                self.assertNotIn("\n", headline + fix)
                for word in expected:
                    self.assertIn(word, headline + " " + fix)

    def test_a_lookup_is_ipv4_like_the_game(self):
        self.assertEqual(ct.resolve("127.0.0.1", 21200), "127.0.0.1")
        with self.assertRaises(OSError):
            ct.resolve("::1", 21200)


class Udp(unittest.TestCase):
    def test_a_server_that_answers(self):
        server = FakeUdp()
        self.addCleanup(server.close)
        self.assertTrue(ct.udp_answers("127.0.0.1", server.port, tries=3, wait=1))
        self.assertEqual(server.got[0], ct.MAGIC + bytes([ct.BYE]))   # a BYE: nobody signs in

    def test_no_answer_after_the_tries(self):
        server = FakeUdp(silent=True)
        self.addCleanup(server.close)
        start = time.monotonic()
        self.assertFalse(ct.udp_answers("127.0.0.1", server.port, tries=2, wait=0.2))
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual(len(server.got), 2)

    def test_nothing_listening(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertFalse(ct.udp_answers("127.0.0.1", port, tries=2, wait=0.2))


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / "launcher.json")


class Banner(Base):
    def test_shown_only_when_offline_because_online_features_is_off(self):
        self.assertFalse(online_features.offline_by_choice(self.cfg))       # the defaults
        online_features.answer(self.cfg, False)
        self.assertTrue(online_features.offline_by_choice(self.cfg))
        self.cfg.online["mode"] = "public"     # features off, a server picked by hand
        self.assertFalse(online_features.offline_by_choice(self.cfg))
        other = Config(self.root / "other.json")
        other.online["mode"] = "offline"       # Offline picked on the Servers page: the player's choice
        self.assertFalse(online_features.offline_by_choice(other))

    def test_go_online_turns_online_features_on_and_picks_the_public_server(self):
        online_features.answer(self.cfg, False)
        online_features.go_online(self.cfg)
        self.assertEqual(self.cfg.online["mode"], "public")
        self.assertEqual(self.cfg.d["online_features"]["on"], True)
        self.assertFalse(online_features.offline_by_choice(self.cfg))
        self.assertEqual(server_list.chosen(self.cfg), PUBLIC_SERVER)
        self.assertFalse(self.cfg.settings["share_shader_list"])   # the others keep their settings


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Page(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def page(self, test=None):
        from forza_launcher.ui.servers_page import ServersPage
        self.threads = set()

        def run(address):
            self.threads.add(threading.current_thread())
            return (test or (lambda a: ct.Result(ct.UDP_BLOCKED, a, "h", 21200)))(address)

        page = ServersPage(self.cfg, None, fetch=lambda: [],
                           ping_all=lambda addresses: {a: DOWN for a in addresses}, test=run)
        self.addCleanup(page.deleteLater)
        self.changed = []
        page.changed.connect(lambda: self.changed.append(dict(self.cfg.online)))
        return page

    def wait(self, page):
        until = time.monotonic() + 8
        while page._tester is not None and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertIsNone(page._tester)

    def test_the_banner_and_go_online(self):
        online_features.answer(self.cfg, False)
        page = self.page()
        page.fill()
        self.assertFalse(page.offline_notice.isHidden())
        self.assertIn("Online features", page.offline_notice.text.text())
        page.offline_notice.go.click()
        self.assertTrue(page.offline_notice.isHidden())
        self.assertEqual([c["mode"] for c in self.changed], ["public"])
        self.assertEqual(page.rows[0].name + "  (in use)", page.table.item(0, 0).text())
        self.assertIn("Public server", page.message.text())

    def test_no_banner_with_online_features_on(self):
        page = self.page()
        page.fill()
        self.assertTrue(page.offline_notice.isHidden())

    def test_test_connection_reports_off_the_gui_thread(self):
        page = self.page()
        page.fill()
        page._select(PUBLIC_SERVER)
        self.assertTrue(page.test_button.isEnabled())
        page.test_button.click()
        self.assertFalse(page.test_button.isEnabled())
        self.assertIn("Testing", page.test_result.text())
        self.wait(page)
        self.assertEqual(page.tested.kind, ct.UDP_BLOCKED)
        self.assertEqual(page.tested.address, PUBLIC_SERVER)
        self.assertTrue(page.test_result.text().startswith("UDP blocked"))
        self.assertIn("firewall", page.test_fix.text())
        self.assertTrue(page.test_button.isEnabled())
        self.assertNotIn(threading.main_thread(), self.threads)
        page._select(server_list.LOCAL)   # another server: the result was about the one tested
        self.assertTrue(page.test_result.isHidden())
        self.assertFalse(page.test_button.isEnabled())   # This PC starts with the game: nothing to test

    def test_a_test_that_raises(self):
        def boom(_address):
            raise RuntimeError("no")
        page = self.page(boom)
        page.fill()
        page._select(PUBLIC_SERVER)
        page.test_selected()
        self.wait(page)
        self.assertIsNone(page.tested)
        self.assertIn("launcher log", page.test_result.text())


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class Window(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_go_online_on_play_saves_and_shows_it(self):
        import json
        from unittest.mock import patch
        from forza_launcher import build_env
        from forza_launcher.ui.window import MainWindow
        online_features.answer(self.cfg, False)
        self.cfg.loaded = True
        self.cfg.advanced["data_root"] = str(self.root)
        self.cfg.gpu.update(checked=True, vendor="nvidia")
        for p in (patch("forza_launcher.paths.config_dir", return_value=self.root),
                  patch("forza_launcher.game.find_game", return_value=(None, [])),
                  patch("forza_launcher.build_env._detect_tools", return_value=build_env.Tools()),
                  patch.object(server_list, "fetch_list", lambda: []),
                  patch.object(server_list, "ping_all", lambda addresses: {})):
            p.start()
            self.addCleanup(p.stop)
        win = MainWindow(self.cfg)
        self.addCleanup(win.deleteLater)
        self.addCleanup(win.close)
        self.assertFalse(win.play.offline_notice.isHidden())
        self.assertEqual(win.settings.server_status.text(), "Play uses: Offline")
        win.play.offline_notice.go.click()
        self.assertTrue(win.play.offline_notice.isHidden())
        self.assertTrue(win.servers.offline_notice.isHidden())
        self.assertEqual(win.settings.server_status.text(), "Play uses: Public server")
        saved = json.loads((self.root / "launcher.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["online"]["mode"], saved["online_features"]["on"]), ("public", True))
        self.assertIn("Public server", win.play.msg.text())


if __name__ == "__main__":
    unittest.main()
