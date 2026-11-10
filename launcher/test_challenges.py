"""Daily and weekly challenges on the Play page (forza-live #1, challenges.py, ui/challenges_card.py):
when the launcher may ask the main server, the signed request, what it shows of the answer, and the
card asking only when it is on screen. No test reaches a real server: requests go to a fake one on
127.0.0.1, or to a stand-in for challenges.load."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import challenges, ed25519, live_auth, online_features
from forza_launcher.config import Config

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

REPO = Path(__file__).resolve().parent.parent
SEED = bytes([5] * 32)
IDENTITY = ("0009000000000A01", "00" * 16, SEED.hex())
NOW = 1793793600   # 2026-11-04 12:00 UTC


def doc(progress=None, now=NOW):
    """A /challenges/progress answer as the server makes it (objectives.document), made at `now`."""
    NOW = int(now)
    def obj(i, title, target, n=0, done=None):
        o = {"id": f"daily-2026-11-04.{i}", "type": "race", "title": title, "target": target,
             "completed": 2}
        if progress is not False:
            o.update(progress=n, done=done)
        return o
    return {"version": 1, "server": "main-1", "now": NOW, "sets": [
        {"kind": "daily", "id": "daily-2026-11-04", "starts": NOW - 43200, "ends": NOW + 43200,
         "objectives": [obj(0, "Win 2 races in a B class car", 2, 2, NOW - 60),
                        obj(1, "Hit 230 km/h at 3 different speed traps", 3, 1)]},
        {"kind": "weekly", "id": "weekly-2026-W45", "starts": NOW - 2 * 86400, "ends": NOW + 5 * 86400,
         "objectives": [obj(0, "Finish 25 races", 25, 4)]}]}


class FakeServer:
    """A Forza Live HTTP port on 127.0.0.1 answering /challenges/progress; keeps what it was sent."""

    def __init__(self, status=200, body=None):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append((self.command, self.path, dict(self.headers)))
                data = json.dumps(doc() if body is None else body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Config(Path(self.temp.name) / "launcher.json")


class WhenTests(Base):
    def test_only_the_main_server_with_the_setting_on(self):
        self.assertEqual(challenges.state(self.cfg), "on")
        for mode, state in (("offline", "hidden"), ("local", "hidden"), ("custom", "other")):
            self.cfg.online["mode"] = mode
            self.assertEqual(challenges.state(self.cfg), state)
        self.cfg.online["mode"] = "public"
        self.cfg.settings["challenges"] = False
        self.assertEqual(challenges.state(self.cfg), "off")

    def test_online_features_off_turns_them_off(self):
        online_features.answer(self.cfg, False)
        self.assertIs(self.cfg.settings["challenges"], False)
        self.cfg.online["mode"] = "public"   # online play turned back on: challenges stay off
        self.assertEqual(challenges.state(self.cfg), "off")

    def test_the_setting_is_the_launchers_own(self):
        from forza_launcher import config
        self.assertIn("challenges", config.LAUNCHER_ONLY)
        self.assertNotIn("challenges", config.render_toml(self.cfg))

    def test_nothing_is_asked_when_off_or_without_an_identity(self):
        def never(*a):
            raise AssertionError("asked the server")
        for mode in ("offline", "local", "custom"):
            self.cfg.online["mode"] = mode
            self.assertEqual(challenges.load(self.cfg, never), ([], "", ""))
        self.cfg.online["mode"] = "public"
        self.cfg.settings["challenges"] = False
        self.assertEqual(challenges.load(self.cfg, never), ([], "", ""))
        self.cfg.settings["challenges"] = True
        with patch.object(challenges, "identity", return_value=None):
            sets, note, gift = challenges.load(self.cfg, never)
        self.assertEqual(sets, [])
        self.assertIn("Play online once", note)


class FetchTests(Base):
    def server(self, **kw):
        s = FakeServer(**kw)
        self.addCleanup(s.close)
        return s

    def test_a_signed_get_of_the_players_progress(self):
        s = self.server()
        got = challenges.fetch(s.url, IDENTITY)
        self.assertEqual(got["server"], "main-1")
        (method, path, headers), = s.requests
        self.assertEqual((method, path), ("GET", "/challenges/progress"))
        self.assertEqual(headers["X-Xuid"], IDENTITY[0])
        self.assertNotIn("X-Friends-Key", headers)   # never a key
        fields = dict(part.split("=", 1) for part in
                      headers["X-Forza-Auth"].split(" ", 1)[1].split(", "))
        message = live_auth.canonical("GET", "/challenges/progress", "127.0.0.1", IDENTITY[0],
                                      fields["Time"], fields["Nonce"], b"")
        self.assertTrue(ed25519.verify(ed25519.public_key(SEED), message,
                                       bytes.fromhex(fields["Signature"])))

    def test_load_from_the_main_server(self):
        s = self.server()
        with patch.object(challenges, "base", return_value=s.url), \
                patch.object(challenges, "identity", return_value=IDENTITY):
            sets, note, gift = challenges.load(self.cfg)
        self.assertEqual((note, gift), ("", ""))
        self.assertEqual([x.kind for x in sets], ["daily", "weekly"])
        win, trap = sets[0].objectives
        self.assertEqual((win.title, win.progress, win.target, win.done, win.completed),
                         ("Win 2 races in a B class car", 2, 2, True, 2))
        self.assertEqual((trap.progress, trap.done), (1, False))

    def test_what_the_player_reads_when_it_fails(self):
        for status, words in ((403, "does not know this PC"), (404, "no challenges"),
                              (500, "not answering")):
            s = self.server(status=status, body={"error": "x"})
            with patch.object(challenges, "base", return_value=s.url), \
                    patch.object(challenges, "identity", return_value=IDENTITY):
                sets, note, gift = challenges.load(self.cfg)
            self.assertEqual(sets, [])
            self.assertIn(words, note)

        def refused(*a):
            raise urllib.error.URLError("refused")
        with patch.object(challenges, "identity", return_value=IDENTITY):
            self.assertIn("not answering", challenges.load(self.cfg, refused)[1])

    def test_the_main_server_is_public_server(self):
        from forza_launcher import PUBLIC_SERVER
        host, port = PUBLIC_SERVER.rsplit(":", 1)
        self.assertEqual(challenges.base(), f"http://{host}:{int(port) + 1}")


class ParseTests(unittest.TestCase):
    def test_bad_fields_are_left_out_and_text_is_plain(self):
        d = doc()
        objs = d["sets"][0]["objectives"]
        objs.append({"title": "<b>x</b>" + "y" * 300, "target": 1})
        objs.append({"title": "no target"})
        objs.append({"title": 5, "target": 1})
        objs.append({"title": "bad progress", "target": 2, "progress": -1})
        objs.append({"title": "bool target", "target": True})
        d["sets"].append({"kind": "monthly", "ends": NOW, "objectives": objs})
        d["sets"].append("nothing")
        sets = challenges.parse(d)
        self.assertEqual([s.kind for s in sets], ["daily", "weekly"])
        titles = [o.title for o in sets[0].objectives]
        self.assertEqual(len(titles), 3)
        self.assertEqual(len(titles[2]), challenges.MAX_TITLE)
        for bad in (None, [], {"sets": {}}, {"sets": [{"kind": "daily", "ends": "x"}]}):
            self.assertEqual(challenges.parse(bad), [])

    def test_the_public_document_has_no_progress(self):
        sets = challenges.parse(doc(progress=False))
        self.assertEqual([(o.progress, o.done) for o in sets[0].objectives], [(0, False), (0, False)])

    def test_the_weekly_gift(self):
        d = doc()
        self.assertEqual(challenges.gift_text(d), "")   # a server without gifts
        d["gift"] = {"credits": 10000, "after": 3}
        d["player"] = {"completed": {"daily": 1}, "gift": {"week": "weekly-2026-W45", "finished": 1,
                                                            "sent": None}}
        self.assertEqual(challenges.gift_text(d), "Finish 3 challenges this week, daily or weekly, "
                         "for a 10,000 CR gift in the Message Center: 1 of 3 so far.")
        d["player"]["gift"].update(finished=4, sent=NOW)
        self.assertEqual(challenges.gift_text(d), "This week's 10,000 CR gift was sent: collect it "
                         "in the game's Message Center.")
        for bad in ({"credits": "x", "after": 3}, {"credits": 10000}, None):
            self.assertEqual(challenges.gift_text(dict(d, gift=bad)), "")
        self.assertEqual(challenges.gift_text(dict(d, player=None)), "")

    def test_time_left(self):
        self.assertEqual(challenges.time_left(NOW + 5 * 3600 + 12 * 60, NOW), "5 h 12 min left")
        self.assertEqual(challenges.time_left(NOW + 5 * 86400, NOW), "5 days left")
        self.assertEqual(challenges.time_left(NOW + 600, NOW), "10 min left")
        self.assertEqual(challenges.time_left(NOW - 5, NOW), "ends now")

    def test_the_servers_own_document_reads(self):
        root = REPO / "server"
        if not (root / "forza_live" / "competition" / "objectives.py").is_file():
            self.skipTest("the server/ submodule is not checked out, or is before the challenges")
        sys.path.insert(0, str(root))
        self.addCleanup(sys.path.remove, str(root))
        from forza_live.competition import objectives, scores
        with tempfile.TemporaryDirectory() as d:
            scores.configure(d, "main-1")
            was = objectives.ENABLED, objectives.GIFTS, objectives.SEED
            objectives.configure(d, enabled=True, gifts=True)
            try:
                published = json.loads(json.dumps(objectives.document(0x0009000000000A01, NOW)))
            finally:
                objectives.STORE.__init__()
                objectives.ENABLED, objectives.GIFTS, objectives.SEED = was
        sets = challenges.parse(published)
        self.assertEqual([s.kind for s in sets], ["daily", "weekly"])
        self.assertEqual([len(s.objectives) for s in sets], [objectives.PER_SET] * 2)
        self.assertEqual(challenges.gift_text(published),
                         f"Finish {objectives.GIFT_AFTER} challenges this week, daily or weekly, for a "
                         f"{objectives.GIFT_CREDITS:,} CR gift in the Message Center: 0 of "
                         f"{objectives.GIFT_AFTER} so far.")


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class CardTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        from forza_launcher.ui import challenges_card
        self.calls = []

        def load(cfg):
            self.calls.append(threading.current_thread())
            d = doc(now=time.time())
            return challenges.parse(d), "", challenges.gift_text(d)
        patcher = patch.object(challenges_card, "FIRST_DELAY_MS", 10)
        patcher.start()
        self.addCleanup(patcher.stop)
        from PySide6.QtWidgets import QWidget
        self.host = QWidget()        # the Play page: the card is on screen when it is
        self.addCleanup(self.host.deleteLater)
        self.card = challenges_card.ChallengesCard(self.cfg, self.host, load=load)

    def spin(self, seconds=0.3, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not (until and until()):
            self.app.processEvents()
            time.sleep(0.01)

    def texts(self):
        from PySide6.QtWidgets import QLabel
        return [w.text() for w in self.card.findChildren(QLabel) if w.isVisibleTo(self.card) and w.text()]

    def test_a_card_on_screen_asks_off_the_gui_thread_and_shows_the_progress(self):
        self.host.show()
        self.card.refresh()
        self.spin(5, lambda: self.card.fetches)
        self.assertEqual(self.card.fetches, 1)
        self.assertNotIn(threading.main_thread(), self.calls)
        texts = self.texts()
        self.assertFalse(self.card.gift.isVisibleTo(self.card))   # a server without gifts
        self.assertIn("✓ Win 2 races in a B class car", texts)
        self.assertIn("Done", texts)
        self.assertIn("1 / 3", texts)
        self.assertTrue(any(t.startswith("Today · ") for t in texts))
        self.assertTrue(any(t.startswith("This week · ") and t.endswith("days left") for t in texts))
        self.card.refresh()          # again soon: no new request
        self.spin()
        self.assertEqual(len(self.calls), 1)
        self.card.refresh(force=True)  # after a game session
        self.spin(5, lambda: self.card.fetches == 2)
        self.assertEqual(len(self.calls), 2)

    def test_a_gift_line_on_the_card(self):
        from forza_launcher.ui import challenges_card
        card = challenges_card.ChallengesCard(
            self.cfg, self.host, load=lambda cfg: (challenges.parse(doc(now=time.time())), "",
                                                   "1 of 3 so far."))
        self.host.show()
        card.refresh()
        self.spin(5, lambda: card.fetches)
        self.assertEqual(card.gift.text(), "1 of 3 so far.")
        self.assertTrue(card.gift.isVisibleTo(card))

    def test_headless_the_real_server_is_never_asked(self):
        """The offscreen platform (tests, CI): the card's own loader never starts, even on screen."""
        from forza_launcher.ui import challenges_card
        with patch.object(challenges, "load", side_effect=AssertionError("asked")) as load:
            card = challenges_card.ChallengesCard(self.cfg, self.host)
            self.host.show()
            card.refresh()
            self.spin()
            self.assertFalse(load.called)
            self.assertFalse(card.busy())

    def test_the_request_thread_touches_no_widget(self):
        """The answer is picked up on the GUI thread, also when the card is closed meanwhile."""
        gate = threading.Event()
        seen = []

        def load(cfg):
            gate.wait(5)
            seen.append(threading.current_thread() is threading.main_thread())
            return challenges.parse(doc(now=time.time())), "", ""
        from forza_launcher.ui import challenges_card
        card = challenges_card.ChallengesCard(self.cfg, self.host, load=load)
        self.host.show()
        card.refresh()
        self.spin(5, card.busy)
        self.assertTrue(card.busy())
        self.host.hide()            # the window closes while the request runs
        gate.set()
        self.spin(5, lambda: card.fetches)
        self.assertEqual(seen, [False])
        self.assertEqual(card.fetches, 1)
        self.assertEqual(len(card.sets), 2)

    def test_a_card_not_on_screen_asks_nothing(self):
        self.card.refresh()
        self.spin()
        self.assertEqual(self.calls, [])

    def test_off_and_other_servers_ask_nothing(self):
        self.host.show()
        self.cfg.settings["challenges"] = False
        self.card.refresh()
        self.spin()
        self.assertTrue(self.card.isVisible())
        self.assertTrue(any("are off" in t for t in self.texts()))
        self.cfg.settings["challenges"] = True
        self.cfg.online["mode"] = "custom"
        self.card.refresh()
        self.spin()
        self.assertTrue(any("main server only" in t for t in self.texts()))
        for mode in ("offline", "local"):
            self.cfg.online["mode"] = mode
            self.card.refresh()
            self.spin()
            self.assertFalse(self.card.isVisible())
        self.assertEqual(self.calls, [])

    def test_a_failure_is_a_note(self):
        from forza_launcher.ui import challenges_card
        card = challenges_card.ChallengesCard(self.cfg, self.host, load=lambda cfg: 1 / 0)
        self.host.show()
        card.refresh()
        self.spin(5, lambda: card.fetches)
        self.assertIn("Could not read the challenges", card.note.text())

    def test_the_play_page_has_the_card_and_a_window_not_shown_asks_nothing(self):
        from forza_launcher import build_env
        from forza_launcher.ui.window import MainWindow
        root = Path(self.temp.name)
        self.cfg.loaded = True   # no first-run question
        self.cfg.advanced["data_root"] = str(root)
        self.cfg.d["check_updates"] = False
        with patch("forza_launcher.paths.config_dir", return_value=root), \
                patch("forza_launcher.game.find_game", return_value=(None, [])), \
                patch("forza_launcher.build_env._detect_tools", return_value=build_env.Tools()), \
                patch.object(challenges, "load", side_effect=AssertionError("asked")) as load:
            window = MainWindow(self.cfg)
            self.spin()
            self.assertFalse(load.called)
            self.assertFalse(window.play.challenges.isHidden())   # public server: on the page
            self.cfg.online["mode"] = "offline"
            window.play.refresh()
            self.assertTrue(window.play.challenges.isHidden())
            window.close()
            window.deleteLater()
            self.app.processEvents()

if __name__ == "__main__":
    unittest.main()
