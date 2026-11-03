"""The "This PC" server gets the game's database, so it offers the Co-op Festival, whose
festival events have CPU drivers (#67): python3 -m unittest launcher/test_local_server.py"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import game  # noqa: E402
from forza_launcher.config import Config  # noqa: E402


class LocalServer(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = Path(tmp.name)
        self.cfg = Config(t / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.cfg.online["mode"] = "local"
        self.install = game.GameInstall(t / "rt", t / "rt" / game.EXE)
        (t / "rt" / "server").mkdir(parents=True)
        (t / "rt" / "server" / "forza_live_server.py").write_text("")
        self.db = self.cfg.game_files() / "media" / "db" / "gamedb.slt"

    def server_argv(self, listening=False):
        with mock.patch.object(game, "server_listening", return_value=listening):
            plan = game.build_plan(self.cfg, self.install, write=False, log=lambda _t: None)
        self.assertEqual(plan.env.get("FORZA_SERVER"), game.LOCAL_SERVER)
        return plan.server_argv

    def test_the_server_gets_the_game_database(self):
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b"SQLite format 3\0")
        argv = self.server_argv()
        self.assertEqual(argv[argv.index("--game-db") + 1], str(self.db))
        self.assertEqual(Path(argv[argv.index("--data-dir") + 1]), self.cfg.data_home() / "live_server")

    def test_the_game_folder_setting_is_followed(self):
        other = Path(self.cfg.data_home()).parent / "elsewhere"
        self.cfg.d["game_root"] = str(other)
        db = other / "media" / "db" / "gamedb.slt"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"SQLite format 3\0")
        argv = self.server_argv()
        self.assertEqual(argv[argv.index("--game-db") + 1], str(db))

    def test_without_the_game_files_the_server_starts_without_it(self):
        self.assertNotIn("--game-db", self.server_argv())

    def test_a_server_already_listening_is_used_as_it_is(self):
        self.assertIsNone(self.server_argv(listening=True))


if __name__ == "__main__":
    unittest.main()
