"""Import FM4 tracks (#648, experimental): the disc's circuits, the import with a stand-in
converter, removal, the Play page's choice and what a start on a circuit passes to the game.
Synthetic files only (no game file is read)."""
import os
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import fm4tracks, game, mods  # noqa: E402
from forza_launcher.config import Config  # noqa: E402
from forza_launcher.fetch import Cancelled  # noqa: E402
from test_fm4_import import xex  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication
except ImportError:
    QApplication = None

FM4_TITLE = 0x4D530910


def geo(path, points):
    """An FM4 AI line (MLPData) with the waypoints (x, y, z), as tools/fm4_spawn.py reads it."""
    n = len(points)
    head = (f"MLPDataStart\nfWaypointX:float:{n}:1:   {{x}}\nfWaypointY:float:{n}:1:   {{y}}\n"
            f"fWaypointZ:float:{n}:1:   {{z}}\nMLPDataEnd\n")
    size = len(head.format(x=99999, y=99999, z=99999).encode())
    offs = [size + k * 4 * n for k in range(3)]
    text = head.format(x=str(offs[0]).rjust(5), y=str(offs[1]).rjust(5), z=str(offs[2]).rjust(5)).encode()
    text = text.ljust(size, b"\n")
    data = text + b"".join(struct.pack(f">{n}f", *[p[k] for p in points]) for k in range(3))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def gamedb(path, free_roam=("Colorado", 1, 0), env=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE Tracks (id INTEGER, MediaName TEXT, EnvironmentId INTEGER, RibbonIndex INTEGER)")
    db.execute("CREATE TABLE Environments (Id INTEGER PRIMARY KEY, MediaName TEXT, ShowOnMap INTEGER)")
    db.execute("CREATE TABLE Cars (Id INTEGER)")
    db.execute("INSERT INTO Tracks VALUES (317, ?, ?, ?)", free_roam)
    db.execute("INSERT INTO Environments VALUES (1, 'Colorado', 1)")
    if env:
        db.execute("INSERT INTO Environments VALUES (?, ?, 0)", env)
    db.commit()
    db.close()


def fake_converter(calls):
    """A stand-in for tools/fm4_track_mod.py and tools/fm4_pvs.py: writes what they write."""
    def run(cmd, env, on_line, cancelled, cwd=None):
        calls.append((cmd, env))
        script = Path(cmd[1] if cmd[1] != "--run-script" else cmd[2]).name
        args = cmd[cmd.index(next(a for a in cmd if a.endswith(script))) + 1:]
        if script == "fm4_track_mod.py":
            track_dir, mod = Path(args[0]), Path(args[2])
            t = track_dir.name.lower()
            loose = Path(args[args.index("--loose") + 1])
            (mod / "media" / "tracks" / t / "Ribbon_00").mkdir(parents=True)
            (mod / "media" / "tracks" / t / "TrackSettings.xml").write_text("<x/>")
            (mod / "media" / "tracks" / t / "Ribbon_00" / "a.pvs").write_bytes(b"pvs")
            gamedb(mod / "media" / "db" / "gamedb.slt", (track_dir.name, 50, 0), (50, track_dir.name))
            (mod / "media" / "gamemodes" / t).mkdir(parents=True)
            (mod / "media" / "gamemodes" / t / "activities.xml").write_text("<a/>")
            (loose / "media" / "audio" / "tracks" / track_dir.name).mkdir(parents=True)
            (loose / "media" / "audio" / "tracks" / track_dir.name / "amb.fev").write_bytes(b"fev")
            on_line("4932 bin.zip members")
        elif script == "fm4_pvs.py":
            mod, out = Path(args[1]), Path(args[3])
            t = next((mod / "media" / "tracks").iterdir()).name
            (out / "media" / "tracks" / t).mkdir(parents=True)
            (out / "media" / "tracks" / t / "bin.zip").write_bytes(b"PK" + b"\0" * 100)
            (out / "media" / "tracks" / t / "fm4_stream_blocks.txt").write_text("42783")
            (out / "media" / "zipmanifest.xml").write_text(f"<manifest>{t}</manifest>")
            (out / f"{t}_stx_numbers.bin").write_bytes(b"\1\2")
            (out / f"{t}_stx_files.bin").write_bytes(b"\3")
        else:
            raise AssertionError(script)
    return run


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = t = Path(self.tmp.name)
        self.cfg = Config(t / "cfg" / "launcher.json")
        self.cfg.advanced["data_root"] = str(t / "home")
        self.cfg.online["mode"] = "public"
        # FH1's game folder and Title Update 4
        g = t / "game"
        self.cfg.d["game_root"] = str(g)
        (g / "media" / "tracks" / "colorado").mkdir(parents=True)
        (g / "default.xex").write_bytes(b"xex")
        (g / "media" / "zipmanifest.xml").write_text("<manifest/>")
        (g / "media" / "cars" / "abc").mkdir(parents=True)
        (g / "media" / "cars" / "abc" / "car.zip").write_bytes(b"car")
        (g / "media" / "tracks" / "colorado" / "bin.zip").write_bytes(b"colorado")
        (g / "media" / "audio" / "tracks" / "colorado").mkdir(parents=True)
        (g / "media" / "audio" / "tracks" / "colorado" / "amb.fev").write_bytes(b"co")
        (t / "tu4").mkdir()
        (t / "tu4" / "media.zip").write_bytes(b"PK")
        self.cfg.advanced["extra_args"] = f"--update_data_root={t / 'tu4'}"
        # FM4's Disc 1, as a folder
        d = self.disc = t / "FM4 Disc 1"
        d.mkdir()
        (d / "default.xex").write_bytes(xex(FM4_TITLE))
        gamedb(d / "Media" / "db" / "gamedb.slt")
        con = sqlite3.connect(d / "Media" / "db" / "gamedb.slt")  # FM4's: Suzuka's row and sectors
        con.execute("ALTER TABLE Tracks ADD COLUMN Length REAL")
        con.execute("ALTER TABLE Tracks ADD COLUMN IsReverse INTEGER DEFAULT 0")
        con.execute("INSERT INTO Tracks (id, MediaName, EnvironmentId, RibbonIndex, Length) VALUES (37, 'Suzuka', 20, 0, 5809)")
        con.execute("CREATE TABLE SplitPoints (ID INTEGER, TrackId INTEGER, Split REAL)")
        con.executemany("INSERT INTO SplitPoints VALUES (?, 37, ?)", [(97, 0.25), (98, 0.5), (99, 0.75)])
        con.commit()
        con.close()
        for name in ("Suzuka", "LeMans", "HomeSpace02", "UIThumbnail", "NoBin"):
            (d / "Media" / "tracks" / name / ("Ribbon_01" if name == "LeMans" else "Ribbon_00")).mkdir(parents=True)
            if name != "NoBin":
                (d / "Media" / "tracks" / name / "bin.zip").write_bytes(b"x" * 1000)
        geo(d / "Media" / "AI" / "Tracks" / "suzuka_00.geo", [(0, 0, 10), (0, 100, 10)] + [(0, 200, 10)] * 6)
        geo(d / "Media" / "AI" / "Tracks" / "lemans_01.geo", [(5, 5, 1)] * 8)
        self.install = game.GameInstall(t / "rt", t / "rt" / game.EXE)

    def build(self, names=("Suzuka",)):
        calls = []
        disc = fm4tracks.open_disc(str(self.disc), self.t / "work")
        with mock.patch.object(fm4tracks, "tool_command", lambda s, *a: ["python", s] + [str(x) for x in a]):
            msg = fm4tracks.build(self.cfg, disc, list(names), runner=fake_converter(calls))
        return msg, calls

    def plan(self, write=False):
        return game.build_plan(self.cfg, self.install, write=write, log=lambda _t: None)


class Disc(Base):
    def test_lists_the_circuits_only(self):
        disc = fm4tracks.open_disc(str(self.disc), self.t / "work")
        self.assertEqual(disc.circuits, ["LeMans", "Suzuka"])
        self.assertEqual(disc.root, self.disc)
        self.assertGreaterEqual(disc.size_of("Suzuka"), 1000)

    def test_another_disc_is_named(self):
        (self.disc / "default.xex").write_bytes(xex(0x4D5309C9))
        with self.assertRaises(fm4tracks.Fm4Error) as e:
            fm4tracks.open_disc(str(self.disc), self.t / "work")
        self.assertIn("Forza Horizon", str(e.exception))

    def test_names_and_estimates(self):
        self.assertEqual(fm4tracks.display_name("Infin"), "Infineon")
        self.assertEqual(fm4tracks.display_name("RoadAmerica"), "Road America")
        self.assertEqual(fm4tracks.display_name("Mugello"), "Mugello")
        disc = fm4tracks.Disc(self.disc, circuits=["A", "B"], sizes={"a": 100 << 20, "b": 50 << 20})
        kept, need = fm4tracks.estimate(disc, ["A", "B"])
        self.assertGreater(need, kept)
        self.assertAlmostEqual(kept, int((100 << 20) * 3.2) + int((50 << 20) * 3.2))


class Import(Base):
    def test_installs_the_circuit_and_its_database_mod(self):
        msg, calls = self.build()
        self.assertIn("Suzuka", msg)
        self.assertEqual([Path(c[0][1]).name for c in calls], ["fm4_track_mod.py", "fm4_pvs.py"])
        mod_cmd, env = calls[0]
        self.assertIn("--free-roam", mod_cmd)
        self.assertEqual(mod_cmd[mod_cmd.index("--free-roam") + 1], "317")
        self.assertEqual(env["FM4_DISC"], str(self.disc))
        self.assertFalse([k for k in env if k.startswith("FM4_PVS")])
        info = fm4tracks.find(self.cfg, "suzuka")
        self.assertEqual((info["folder"], info["ribbon"]), ("Suzuka", 0))
        # 15 m behind waypoint 0, facing along the line (FM4's (x, y, z) is FH1's (x, z + 1, y))
        self.assertEqual(info["spawn"], "0.0,11.0,-15.0,0.000,1.000")
        root = Path(info["path"])
        objs = ET.parse(root / "media/tracks/suzuka/Ribbon_00/gameobjs.xml")
        node = objs.find("Obj0[@GameplayID='FESTIVAL_SECOND_NODE']")
        self.assertIsNotNone(node)
        self.assertEqual(node.find("Pos").attrib, {"x": "0.0", "y": "11.0", "z": "-15.0"})
        self.assertEqual(node.find("Orientation/ZAxis").get("z"), "1.000000")
        for rel in ("media/tracks/suzuka/bin.zip", "media/tracks/suzuka/fm4_stream_blocks.txt",
                    "media/tracks/suzuka/TrackSettings.xml", "media/tracks/suzuka/Ribbon_00/a.pvs",
                    "media/zipmanifest.xml", "media/audio/tracks/Suzuka/amb.fev",
                    "stx/suzuka_stx_numbers.bin", "stx/suzuka_stx_files.bin", "spawn.txt"):
            self.assertTrue((root / rel).is_file(), rel)
        m = mods.mods_dir(self.cfg) / ".fm4-track-suzuka"
        self.assertTrue((m / "media" / "db" / "gamedb.slt").is_file())
        self.assertTrue((m / "media" / "gamemodes" / "suzuka" / "activities.xml").is_file())
        self.assertGreater(info["size"], 0)
        # the Mods page does not list it, and no mod is turned on by it
        self.assertEqual(mods.scan(mods.mods_dir(self.cfg)), [])
        self.assertFalse(list(fm4tracks.store(self.cfg).glob(".work-*")))

    def test_the_lap_line_and_the_laps(self):
        self.build()
        lap = (fm4tracks.store(self.cfg) / "suzuka" / "lap.txt").read_text().splitlines()
        self.assertEqual(lap[:4], ["name Suzuka", "length 5809.0", "splits 0.2500 0.5000 0.7500", "points 8"])
        self.assertEqual(lap[4], "0.00 11.00 0.00")  # FM4's (x, y, z) as FH1's (x, z + 1, y)
        # the game's laps file: one JSON object a line, a broken line skipped
        laps = fm4tracks.store(self.cfg) / "laps" / "suzuka.jsonl"
        laps.parent.mkdir(parents=True)
        laps.write_text('{"time": 151.234, "sectors": [40.1, 80.2, 120.3], "lap": 1, "best": true}\n'
                        'not json\n{"time": 149.5, "sectors": [], "lap": 2, "best": true}\n', encoding="utf-8")
        self.assertEqual([x["lap"] for x in fm4tracks.laps(self.cfg, "suzuka")], [1, 2])
        self.assertEqual(fm4tracks.best_lap(self.cfg, "suzuka"), 149.5)
        self.assertEqual(fm4tracks.lap_text(149.5), "2:29.50")
        self.assertEqual(fm4tracks.lap_text(None), "")
        self.assertIsNone(fm4tracks.best_lap(self.cfg, "lemans"))
        # the laps stay when the circuit is removed (an import again keeps its times)
        fm4tracks.remove(self.cfg, "suzuka")
        self.assertEqual(fm4tracks.best_lap(self.cfg, "suzuka"), 149.5)
        self.assertEqual(fm4tracks.installed(self.cfg), [])

    def test_a_circuit_without_ribbon_0_spawns_on_its_first(self):
        self.build(["LeMans"])
        info = fm4tracks.find(self.cfg, "lemans")
        self.assertEqual(info["ribbon"], 1)
        self.assertTrue(info["spawn"].startswith("5.0,2.0,"))

    def test_import_again_replaces_and_remove_deletes(self):
        self.build()
        self.build()
        self.assertEqual([i["key"] for i in fm4tracks.installed(self.cfg)], ["suzuka"])
        fm4tracks.select(self.cfg, "suzuka")
        fm4tracks.remove(self.cfg, "suzuka")
        self.assertEqual(fm4tracks.installed(self.cfg), [])
        self.assertFalse((mods.mods_dir(self.cfg) / ".fm4-track-suzuka").exists())
        self.assertEqual(fm4tracks.selected(self.cfg), "")

    def test_a_failed_circuit_keeps_the_others(self):
        def broken(cmd, env, on_line, cancelled, cwd=None):
            if "LeMans" in " ".join(cmd):
                raise fm4tracks.Fm4TrackError("boom")
            return run(cmd, env, on_line, cancelled)
        run = fake_converter([])
        disc = fm4tracks.open_disc(str(self.disc), self.t / "work")
        with mock.patch.object(fm4tracks, "tool_command", lambda s, *a: ["python", s] + [str(x) for x in a]):
            msg = fm4tracks.build(self.cfg, disc, ["LeMans", "Suzuka"], runner=broken)
        self.assertIn("1 could not be converted", msg)
        self.assertEqual([i["key"] for i in fm4tracks.installed(self.cfg)], ["suzuka"])

    def test_not_set_up_is_said(self):
        (self.t / "game" / "default.xex").unlink()
        disc = fm4tracks.open_disc(str(self.disc), self.t / "work")
        with self.assertRaises(fm4tracks.Fm4TrackError):
            fm4tracks.build(self.cfg, disc, ["Suzuka"], runner=fake_converter([]))

    def test_cancel_stops_the_converter(self):
        start = time.monotonic()
        with self.assertRaises(Cancelled):
            fm4tracks.run_tool([sys.executable, "-c", "import time; print('x', flush=True); time.sleep(60)"],
                               dict(os.environ), lambda _l: None, lambda: time.monotonic() - start > 0.5)
        self.assertLess(time.monotonic() - start, 15)

    def test_a_converter_error_names_its_last_lines(self):
        with self.assertRaises(fm4tracks.Fm4TrackError) as e:
            fm4tracks.run_tool([sys.executable, "-c", "print('reading'); raise SystemExit('no PVS')"],
                               dict(os.environ), lambda _l: None, lambda: False)
        self.assertIn("no PVS", str(e.exception))


class Start(Base):
    def test_colorado_changes_nothing(self):
        before = self.plan()
        self.build()
        after = self.plan()  # imported, not chosen
        self.assertEqual(before.argv, after.argv)
        self.assertEqual(before.env, after.env)
        self.assertEqual(after.fm4_track, "")
        self.assertIn(f"--game_data_root={self.t / 'game'}", after.argv)
        self.assertFalse(any(k.startswith(("FORZA_FM4", "FORZA_TRACK", "FORZA_INSECURE")) for k in after.env))
        self.assertFalse(any(a.startswith(("--fm4_track_dir", "--discord_presence")) for a in after.argv))

    def test_a_circuit_start(self):
        self.build()
        fm4tracks.select(self.cfg, "suzuka")
        self.assertEqual(Config(self.cfg.path).d["fm4_tracks"]["selected"], "suzuka")
        folder = fm4tracks.store(self.cfg) / "suzuka"
        (folder / "spawn.txt").unlink()  # an import made before spawn.txt: written at the start
        objs = folder / "media/tracks/suzuka/Ribbon_00/gameobjs.xml"
        old_objects = b'<GameObjs><Obj0 GameplayID="KEEP_ME" /></GameObjs>'
        objs.write_bytes(old_objects)  # an import made before the grid node was generated
        self.plan(write=False)
        self.assertEqual(objs.read_bytes(), old_objects)
        plan = self.plan(write=True)
        repaired = objs.read_bytes()
        root = ET.fromstring(repaired)
        self.assertIsNotNone(root.find("Obj0[@GameplayID='KEEP_ME']"))
        self.assertIsNotNone(root.find("Obj1[@GameplayID='FESTIVAL_SECOND_NODE']"))
        self.plan(write=True)
        self.assertEqual(objs.read_bytes(), repaired)
        self.assertEqual(plan.fm4_track, "suzuka")
        # the game mounts the circuit itself: the game folder stays the player's
        self.assertIn(f"--game_data_root={self.t / 'game'}", plan.argv)
        self.assertIn(f"--fm4_track_dir={folder}", plan.argv)
        self.assertIn("--forza_mods=.fm4-track-suzuka", plan.argv)
        self.assertEqual((folder / "spawn.txt").read_text(), "0.0,11.0,-15.0,0.000,1.000\n")
        # the converted track's switches are the game's defaults now, not the launcher's
        self.assertFalse(any(k.startswith(("FORZA_FM4", "FORZA_TRACK", "FORZA_INSECURE")) for k in plan.env))
        self.assertEqual(plan.env["FORZA_SERVER"], "")  # offline
        self.assertIn("--discord_presence=false", plan.argv)  # nothing of the circuit goes online
        self.assertNotIn("FORZA_SERVER", plan.unset)
        self.assertTrue(any("Suzuka" in n and "offline" in n for n in plan.notes))
        self.assertFalse((fm4tracks.store(self.cfg) / "world").exists())
        # a developer's switch in the extra environment still reaches the game
        self.cfg.advanced["extra_env"] = "FORZA_TRACK_SPAWN=1,2,3"
        self.assertEqual(self.plan().env["FORZA_TRACK_SPAWN"], "1,2,3")
        # back to Colorado: the plain plan again
        fm4tracks.select(self.cfg, "")
        self.assertNotIn("--fm4_track_dir", " ".join(self.plan().argv))
        fm4tracks.remove(self.cfg, "suzuka")
        self.assertFalse(folder.exists())
        self.assertEqual((self.t / "game" / "media" / "cars" / "abc" / "car.zip").read_bytes(), b"car")

    def test_the_track_mod_loads_last(self):
        self.build()
        for name in ("Alpha", "Beta"):
            p = mods.mods_dir(self.cfg) / name / "media" / "x.xml"
            p.parent.mkdir(parents=True)
            p.write_text("x")
        self.cfg.d["mods"] = {"order": ["Beta", "Alpha"], "disabled": []}
        fm4tracks.select(self.cfg, "suzuka")
        self.assertIn("--forza_mods=Beta|Alpha|.fm4-track-suzuka", self.plan().argv)

    def test_another_database_mod_gets_the_circuit_rows_merged(self):
        self.build()
        cars = mods.mods_dir(self.cfg) / "reimagined-fm4-cars" / "media" / "db" / "gamedb.slt"
        gamedb(cars)
        con = sqlite3.connect(cars)
        con.execute("INSERT INTO Cars VALUES (9001)")
        con.commit()
        con.close()
        fm4tracks.select(self.cfg, "suzuka")
        plan = self.plan(write=True)
        self.assertIn("--forza_mods=reimagined-fm4-cars|.fm4-track-suzuka|.fm4-track-db", plan.argv)
        merged = sqlite3.connect(mods.mods_dir(self.cfg) / ".fm4-track-db" / "media" / "db" / "gamedb.slt")
        self.assertEqual(merged.execute("SELECT * FROM Tracks WHERE id=317").fetchone(), (317, "Suzuka", 50, 0))
        self.assertEqual(merged.execute("SELECT MediaName FROM Environments WHERE Id=50").fetchone(), ("Suzuka",))
        self.assertEqual(merged.execute("SELECT Id FROM Cars").fetchall(), [(9001,)])  # the cars kept
        merged.close()
        # Colorado again: the merged copy goes at the next circuit start, and is never loaded
        fm4tracks.select(self.cfg, "")
        self.assertNotIn(".fm4-track-db", " ".join(self.plan().argv))

    def test_a_gone_choice_is_colorado(self):
        self.cfg.d["fm4_tracks"] = {"selected": "suzuka"}
        self.assertEqual(fm4tracks.selected(self.cfg), "")
        with self.assertRaises(fm4tracks.Fm4Error):
            fm4tracks.select(self.cfg, "nowhere")
        self.assertIn(f"--game_data_root={self.t / 'game'}", self.plan().argv)


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class Ui(Base):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def own(self, widget):
        """Delete the widget when the test ends: left to Python's exit, PySide 6.12 deletes it
        after the application and the process aborts (CI's launcher-tests)."""
        import shiboken6
        self.addCleanup(lambda: shiboken6.isValid(widget) and shiboken6.delete(widget))
        return widget

    def test_the_picker_shows_only_with_an_import(self):
        from forza_launcher.ui.fm4_tracks import TrackPicker
        picker = self.own(TrackPicker(self.cfg))
        self.assertFalse(picker.isVisibleTo(None) and picker.isVisible())
        self.assertEqual(picker.combo.count(), 1)
        self.assertTrue(picker.isHidden())
        self.build()
        picker.refresh()
        self.assertFalse(picker.isHidden())
        self.assertEqual([picker.combo.itemText(i) for i in range(picker.combo.count())],
                         ["FH1's Colorado (default)", "Suzuka (FM4, experimental)"])
        self.assertEqual(picker.combo.currentIndex(), 0)
        self.assertTrue(picker.note.isHidden())
        picker.combo.setCurrentIndex(1)
        self.assertEqual(fm4tracks.selected(self.cfg), "suzuka")
        self.assertFalse(picker.note.isHidden())
        self.assertIn("Experimental", picker.note.text())
        self.assertIn("offline", picker.note.text())

    def test_the_laps_page(self):
        from forza_launcher.ui.fm4_tracks import Fm4LapsPage
        page = self.own(Fm4LapsPage(self.cfg, None))
        self.assertFalse(page.tab_visible())
        self.assertFalse(page.empty.isHidden())
        self.build()
        laps = fm4tracks.store(self.cfg) / "laps" / "suzuka.jsonl"
        laps.parent.mkdir(parents=True)
        laps.write_text('{"time": 430.0, "sectors": [110.0, 222.0, 325.0], "lap": 1, "date": "d1"}\n'
                        '{"time": 425.877, "sectors": [108.088, 220.673, 321.853], "lap": 2, "date": "d2"}\n'
                        '{"time": 440.5, "sectors": [], "lap": 3, "date": "d3"}\n', encoding="utf-8")
        page.refresh()
        self.assertTrue(page.tab_visible())
        self.assertTrue(page.empty.isHidden())
        from PySide6.QtWidgets import QTableWidget
        table = page.findChildren(QTableWidget)[-1]
        rows = [[table.item(r, c).text() for c in range(4)] for r in range(table.rowCount())]
        # the best first, then the newest; each sector's own time
        self.assertEqual(rows[0], ["7:05.88", "1:48.09   1:52.58   1:41.18   1:44.02", "d2", "best"])
        self.assertEqual([r[2] for r in rows], ["d2", "d3", "d1"])
        self.assertEqual(rows[1][1], "7:20.50")

    def test_the_card_lists_sizes_and_the_choice_dialog(self):
        from forza_launcher.ui.fm4_tracks import ChooseCircuits, Fm4TracksCard
        page = mock.Mock()
        page._game_running.return_value = False
        card = self.own(Fm4TracksCard(self.cfg, page))
        self.assertTrue(card.table.isHidden())
        self.build()
        card.refresh()
        self.assertEqual(card.table.rowCount(), 1)
        self.assertEqual(card.table.item(0, 0).text(), "Suzuka")
        self.assertIn("1 circuit(s) imported", card.message.text())
        disc = fm4tracks.open_disc(str(self.disc), self.t / "work")
        dialog = self.own(ChooseCircuits(None, disc, {"suzuka"}, 10 << 30))
        self.assertEqual(dialog.chosen(), [])  # Suzuka is imported already: nothing ticked
        self.assertIn("imported", dialog.list.item(1).text())
        self.assertFalse(dialog.go.isEnabled())
        from PySide6.QtCore import Qt
        dialog._all(Qt.Checked)
        self.assertEqual(dialog.chosen(), ["LeMans", "Suzuka"])
        self.assertTrue(dialog.go.isEnabled())
        small = self.own(ChooseCircuits(None, disc, set(), 1000))
        self.assertFalse(small.go.isEnabled())  # not enough room


if __name__ == "__main__":
    unittest.main()
