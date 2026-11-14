"""Import Fast & Furious (#821, experimental): the disc folders, the import with a stand-in
converter, what a start in the world passes to the game, removal. Synthetic files only."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import fh2ffworld, fm4tracks  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
from forza_launcher.fetch import Cancelled  # noqa: E402
from test_fm4_import import xex  # noqa: E402
from test_fm4_tracks import Base, QApplication, gamedb  # noqa: E402

SPAWN = "951.0,135.9,-1827.9,-0.976,-0.218"


def ff_disc(root: Path, horizon_zip=False, title=None):
    """A game's files as F&F's (or FH2's) disc holds them: its world and database (and its
    default.xex with the title id, when given)."""
    world = root / "media" / "tracks" / "Anthem"
    (world / "Ribbon_00").mkdir(parents=True)
    (world / "bin.zip").write_bytes(b"x" * 1000)
    gamedb(root / "media" / "db" / "gamedb.slt")
    if title:
        (root / "default.xex").write_bytes(xex(title))
    if horizon_zip:
        (root / "media" / "ui" / "textures").mkdir(parents=True)
        (root / "media" / "ui" / "textures" / "Horizon.zip").write_bytes(b"PK")
    return root


def fake_tool(calls, spawn=SPAWN):
    """A stand-in for tools/fh2ff_world.py build: writes what it writes."""
    def run(cmd, env, on_line, cancelled, cwd=None):
        calls.append((cmd, env))
        args = cmd[cmd.index("build") + 1:]
        out = Path(args[3])
        t = out / "fm4-tracks" / "anthem"
        (t / "media" / "tracks" / "Anthem" / "Ribbon_00").mkdir(parents=True)
        (t / "media" / "tracks" / "Anthem" / "bin.zip").write_bytes(b"PK")
        (t / "media" / "tracks" / "Anthem" / "Ribbon_00" / "GameObjs.xml").write_text("<GameObjs/>")
        (t / "media" / "zipmanifest.xml").write_text("<manifest/>")
        if spawn:
            (t / "spawn.txt").write_text(spawn + "\n")
        m = out / "mods" / "fh2ff-anthem"
        gamedb(m / "media" / "db" / "gamedb.slt", ("Anthem", 70, 0), (70, "Anthem"))
        (m / "mod.toml").write_text('name = "x"\n')
        on_line("written")
        if cancelled():
            raise Cancelled()
    return run


class WorldBase(Base):
    def setUp(self):
        super().setUp()
        self.ff = ff_disc(self.t / "FF" / "disc", title=fh2ffworld.FF_TITLE)
        self.fh2 = ff_disc(self.t / "FH2", horizon_zip=True, title=fh2ffworld.FH2_TITLE)

    def import_world(self, fh2=None, spawn=SPAWN, cancelled=lambda: False, key="anthem", src=None):
        calls = []
        with mock.patch.object(fm4tracks, "tool_command", lambda s, *a: ["python", s] + [str(x) for x in a]):
            msg = fh2ffworld.build(self.cfg, src or self.t / "FF", fh2, runner=fake_tool(calls, spawn),
                                   cancelled=cancelled, key=key)
        return msg, calls


class World(WorldBase):
    def test_the_folder_is_checked(self):
        self.assertEqual(fh2ffworld.disc_root(self.t / "FF"), self.ff)  # its folder or the one in it
        self.assertEqual(fh2ffworld.disc_root(self.ff), self.ff)
        with self.assertRaises(fh2ffworld.Fh2ffError):
            fh2ffworld.disc_root(self.t / "game")  # FH1's: no Anthem
        with self.assertRaises(fh2ffworld.Fh2ffError) as e:
            fh2ffworld.fh2_root(self.ff)  # F&F's, given as FH2's: its default.xex says so
        self.assertIn("Fast & Furious", str(e.exception))
        with self.assertRaises(fh2ffworld.Fh2ffError):
            fh2ffworld.disc_root(self.fh2)  # and the other way round
        self.assertEqual(fh2ffworld.fh2_root(self.fh2), self.fh2)
        self.assertIsNone(fh2ffworld.fh2_root(None))
        # both games side by side in the chosen folder: each import finds its own
        both = self.t / "both"
        fh2, ff = (ff_disc(both / "A FH2", title=fh2ffworld.FH2_TITLE),
                   ff_disc(both / "B FF", title=fh2ffworld.FF_TITLE))
        self.assertEqual((fh2ffworld.disc_root(both), fh2ffworld.disc_root(both, "fh2")), (ff, fh2))
        # FH2's own import has no "or none"
        with self.assertRaises(fh2ffworld.Fh2ffError) as e:
            fh2ffworld.fh2_root(fh2, optional=False)        # no Horizon.zip in this one
        self.assertNotIn("none", str(e.exception))

    def test_import_installs_the_world_beside_the_circuits(self):
        msg, calls = self.import_world(self.fh2)
        self.assertIn("Fast & Furious", msg)
        cmd, env = calls[0]
        self.assertEqual(Path(cmd[1]).name, "fh2ff_world.py")
        self.assertEqual(cmd[cmd.index("build") + 1:cmd.index("build") + 3], [str(self.ff), str(self.t / "game")])
        self.assertEqual(cmd[cmd.index("--fh2") + 1], str(self.fh2))
        self.assertFalse([k for k in env if k.startswith(("FORZA_", "FM4_"))])
        info = fh2ffworld.installed(self.cfg)
        self.assertEqual((info["kind"], info["key"], info["spawn"], info["fh2"]), ("fh2ff", "anthem", SPAWN, True))
        root = Path(info["path"])
        self.assertTrue((root / "media" / "tracks" / "Anthem" / "bin.zip").is_file())
        mod = self.cfg.data_home() / "mods" / ".fm4-track-anthem"
        self.assertTrue((mod / "media" / "db" / "gamedb.slt").is_file())
        self.assertIn("Never share", (mod / "mod.toml").read_text())
        self.assertFalse(list(fm4tracks.store(self.cfg).glob(".work-*")))
        self.assertIn(("anthem", "Fast & Furious (FH2 world, experimental)"), fm4tracks.choices(self.cfg))
        self.assertNotIn("anthem", dict(fm4tracks.lap_circuits(self.cfg)))  # no lap timer there
        # without FH2's files: no --fh2, and the message says what is missing
        msg, calls = self.import_world()
        self.assertNotIn("--fh2", calls[0][0])
        self.assertIn("without FH2's map art", msg)
        self.assertFalse(fh2ffworld.installed(self.cfg)["fh2"])

    def test_a_failed_or_cancelled_import_keeps_the_earlier_one(self):
        self.import_world()
        before = fh2ffworld.installed(self.cfg)["imported"]
        with self.assertRaises(fh2ffworld.Fh2ffError):
            self.import_world(spawn=None)  # no start found
        with self.assertRaises(Cancelled):
            self.import_world(cancelled=lambda: True)
        self.assertEqual(fh2ffworld.installed(self.cfg)["imported"], before)
        self.assertFalse(list(fm4tracks.store(self.cfg).glob(".work-*")))

    def test_not_set_up_is_said(self):
        self.cfg.d["game_root"] = str(self.t / "nowhere")
        with self.assertRaises(fh2ffworld.Fh2ffError) as e:
            self.import_world()
        self.assertIn("Setup", str(e.exception))

    def test_a_start_in_the_world(self):
        self.import_world(self.fh2)
        fm4tracks.select(self.cfg, "anthem")
        folder = fm4tracks.store(self.cfg) / "anthem"
        objs = folder / "media" / "tracks" / "Anthem" / "Ribbon_00" / "GameObjs.xml"
        plan = self.plan(write=True)
        self.assertEqual(objs.read_text(), "<GameObjs/>")  # no FM4 grid node added to F&F's
        self.assertIn(f"--fm4_track_dir={folder}", plan.argv)
        self.assertIn("--forza_mods=.fm4-track-anthem", plan.argv)
        # its own switches: the changed files open as plain ones, the start on its road; traffic
        # stays and FM4's collision and texture switches are not set
        self.assertEqual(plan.env["FORZA_INSECURE_FILES"], "zipmanifest.xml|\\tracks\\anthem\\")
        self.assertEqual(plan.env["FORZA_TRACK_SPAWN"], SPAWN)
        # the world's fm4_stream_blocks.txt (the streaming pool) would turn these on: off
        self.assertEqual((plan.env["FORZA_FM4_COL"], plan.env["FORZA_TRACK_NO_TRAFFIC"]), ("0", "0"))
        self.assertNotIn("FORZA_FM4_STX", plan.env)
        self.assertEqual(plan.env["FORZA_SERVER"], "")  # offline
        self.assertTrue(any("Fast & Furious" in n and "offline" in n for n in plan.notes))
        # the game reads those "0"s only with the world's block count: an import without it says so
        self.assertTrue(any("import it again" in n for n in plan.notes))
        (folder / "media" / "tracks" / "Anthem" / fh2ffworld.STREAM_BLOCKS).write_text("1000\n")
        self.assertFalse(any("import it again" in n for n in self.plan().notes))
        # a developer's switch in the extra environment still wins
        self.cfg.advanced["extra_env"] = "FORZA_TRACK_SPAWN=1,2,3"
        self.assertEqual(self.plan().env["FORZA_TRACK_SPAWN"], "1,2,3")
        fm4tracks.remove(self.cfg, "anthem")
        self.assertFalse(folder.exists())
        self.assertEqual(fm4tracks.selected(Config(self.cfg.path)), "")

    def test_the_worlds_races_reach_another_mods_database(self):
        import sqlite3
        from forza_launcher import mods
        self.import_world(self.fh2)
        own = mods.mods_dir(self.cfg) / ".fm4-track-anthem" / "media" / "db" / "gamedb.slt"
        con = sqlite3.connect(own)
        con.executemany("INSERT INTO Tracks VALUES (?, 'Anthem', 70, 0)", [(5000,), (5001,)])
        con.commit()
        con.close()
        cars = mods.mods_dir(self.cfg) / "reimagined-fm4-cars" / "media" / "db" / "gamedb.slt"
        gamedb(cars)
        con = sqlite3.connect(cars)
        con.execute("INSERT INTO Tracks VALUES (5001, 'Old', 9, 9)")   # an earlier copy of one: replaced
        con.execute("INSERT INTO Tracks VALUES (400, 'Colorado', 1, 3)")
        con.commit()
        con.close()
        fm4tracks.select(self.cfg, "anthem")
        self.plan(write=True)
        merged = sqlite3.connect(mods.mods_dir(self.cfg) / ".fm4-track-db" / "media" / "db" / "gamedb.slt")
        self.assertEqual(merged.execute("SELECT * FROM Tracks ORDER BY id").fetchall(),
                         [(317, "Anthem", 70, 0), (400, "Colorado", 1, 3), (5000, "Anthem", 70, 0),
                          (5001, "Anthem", 70, 0)])
        merged.close()

    def test_fh2s_own_world(self):
        msg, calls = self.import_world(key="fh2", src=self.t / "FH2")
        cmd = calls[0][0]
        self.assertEqual(cmd[cmd.index("build") + 1], str(self.fh2))
        self.assertEqual(cmd[cmd.index("--fh2") + 1], str(self.fh2))  # its own road types and map art
        self.assertIn("Forza Horizon 2", msg)
        info = fh2ffworld.installed(self.cfg, "fh2")
        self.assertEqual((info["kind"], info["name"]), ("fh2", "Forza Horizon 2"))
        self.assertTrue((self.cfg.data_home() / "mods" / ".fm4-track-fh2" / "media" / "db" / "gamedb.slt").is_file())
        self.import_world()  # F&F's beside it
        self.assertEqual({k for k, _ in fm4tracks.choices(self.cfg)}, {"", "anthem", "fh2"})
        fm4tracks.select(self.cfg, "fh2")
        plan = self.plan(write=True)
        self.assertIn(f"--fm4_track_dir={fm4tracks.store(self.cfg) / 'fh2'}", plan.argv)
        self.assertEqual(plan.env["FORZA_INSECURE_FILES"], "zipmanifest.xml|\\tracks\\anthem\\")
        self.assertTrue(any("Forza Horizon 2" in n for n in plan.notes))
        with self.assertRaises(fh2ffworld.Fh2ffError):
            self.import_world(key="fh2", src=self.t / "FF")  # F&F's files given as FH2's

    def test_a_circuit_named_anthem_is_not_replaced(self):
        self.import_world()
        info = fm4tracks.find(self.cfg, "anthem")
        marker = Path(info["path"]) / fm4tracks.MARKER
        marker.write_text(marker.read_text().replace('"fh2ff"', '"fm4"'))
        with self.assertRaises(fh2ffworld.Fh2ffError):
            self.import_world()


@unittest.skipUnless(QApplication, "PySide6 is not installed")
class Ui(WorldBase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_card_imports_the_world(self):
        import shiboken6
        from forza_launcher.ui.fm4_tracks import Fh2WorldsCard
        page = mock.Mock()
        page._game_running.return_value = False
        page._confirm.return_value = False  # no FH2 files
        card = Fh2WorldsCard(self.cfg, page)
        self.addCleanup(lambda: shiboken6.isValid(card) and shiboken6.delete(card))
        self.assertTrue(card.ff_button.isEnabled())
        # a button shows "&&" as "&" (a single one is a shortcut mark and is not drawn)
        self.assertEqual(card.ff_button.text().replace("&&", "&"), "Import Fast & Furious…")
        self.assertNotRegex(card.ff_button.text(), r"(?<!&)&(?!&)")
        started = []
        card._start = lambda job, name, text, done: started.append(job)
        with mock.patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value=str(self.t / "FF")),                 mock.patch.object(fh2ffworld, "build", return_value="done") as build:
            card._import_ff()
            self.assertEqual(len(started), 1)
            task = mock.Mock()
            task.cancelled.return_value = False
            self.assertEqual(started[0](task), "done")
        self.assertEqual(build.call_args[0][1:3], (str(self.t / "FF"), None))
        with mock.patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value=str(self.t / "game")):
            card._import_ff()  # not F&F's files: said, nothing started
        self.assertEqual(len(started), 1)
        self.assertIn("Fast & Furious", card.message.text())

    def test_worlds_have_a_card_of_their_own(self):
        import shiboken6
        from forza_launcher.ui.fm4_tracks import Fh2WorldsCard, Fm4TracksCard, TrackPicker
        self.import_world()
        page = mock.Mock()
        page._game_running.return_value = False
        page._confirm.return_value = True
        worlds, circuits = Fh2WorldsCard(self.cfg, page), Fm4TracksCard(self.cfg, page)
        picker = TrackPicker(self.cfg)
        for w in (worlds, circuits, picker):
            self.addCleanup(lambda w=w: shiboken6.isValid(w) and shiboken6.delete(w))
        self.assertEqual([worlds.table.item(r, 0).text() for r in range(worlds.table.rowCount())], ["Fast & Furious"])
        self.assertEqual(worlds.table.columnCount(), 3)                 # no lap times in a world
        self.assertIn("1 world(s) imported", worlds.message.text())
        self.assertEqual(worlds.remove_button.text(), "Remove world")
        self.assertEqual(circuits.table.rowCount(), 0)                  # and it is no circuit
        self.assertEqual(circuits.message.text(), "")
        self.assertFalse(hasattr(circuits, "ff_button"))
        # the Play page's note is the world's when a world is chosen
        fm4tracks.select(self.cfg, "anthem")
        picker.refresh()
        self.assertEqual(picker.note.text(), fh2ffworld.EXPERIMENTAL_SHORT)
        fm4tracks.select(self.cfg, "")
        picker.refresh()
        self.assertTrue(picker.note.isHidden())
        # removing it from its card
        worlds.table.selectRow(0)
        worlds._remove()
        self.assertEqual(worlds.table.rowCount(), 0)
        self.assertIsNone(fm4tracks.find(self.cfg, "anthem"))


if __name__ == "__main__":
    unittest.main()
