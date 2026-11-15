"""Synthetic tests of the one id table for every imported car (#924): no game file is read."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import car_ids  # noqa: E402
import fm4_cars  # noqa: E402


def fh1_and_fm4(case):
    """FH1's database (as car_ids.merged_official makes it, with base_cars and base_engines) and
    FM4's: FH1's traffic Beetle (282), its Bora (283, a car you can drive) and RAV4 (1530, traffic);
    FM4's Beetle, Coupe (1530, the RAV4's id), A1 (100, FH1 lacks it) and NUL_Car_00."""
    fh1, fm4 = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    case.addCleanup(fh1.close)
    case.addCleanup(fm4.close)
    for db in (fh1, fm4):
        db.execute("create table Data_Car (Id INTEGER, MediaName TEXT, StockWheelID INT, IsSelectable INT)")
        db.execute("create table Data_Engine (EngineID INTEGER, MediaName TEXT)")
        db.execute("create table List_UpgradeEngine (Id INTEGER, Ordinal INT, EngineID INT, IsStock INT)")
        db.execute("create table Combo_Engines (EngineComboID INTEGER, Ordinal INT, EngineID INT)")
        db.execute("create table Data_Drivetrain (DrivetrainID INTEGER)")
        db.execute("create table List_UpgradeDrivetrain (Id INTEGER, Ordinal INT, DrivetrainID INT)")
        db.execute("create table List_Wheels (ID INTEGER, MediaName TEXT)")
    fh1.executemany("insert into Data_Car values (?,?,?,?)", [
        (282, "VW_Beetle_04", 30, 0), (283, "VW_BoraVR6_03", 61, 1), (1530, "TOY_RAV4Sport_04", 41, 0),
        (1215, "NUL_Car_00", 1, 0)])
    fh1.executemany("insert into Data_Engine values (?,?)", [(36, "VW_Beetle_04"), (37, "VW_BoraVR6_03"),
                                                            (990, "TOY_RAV4Sport_04")])
    fh1.executemany("insert into List_UpgradeEngine values (?,?,?,1)", [(282000, 282, 36), (283000, 283, 37),
                                                                         (1530000, 1530, 990)])
    fh1.executemany("insert into Data_Drivetrain values (?)", [(35,), (504,), (937,)])
    fh1.executemany("insert into List_UpgradeDrivetrain values (?,?,?)", [(282000, 282, 35), (283000, 283, 504),
                                                                           (1530000, 1530, 937)])
    fh1.executemany("insert into List_Wheels values (?,?)", [(30, "VW_Beetle_04"), (41, "TOY_RAV4Sport_04")])
    fh1.execute("create table base_cars (Id INTEGER, MediaName TEXT, IsSelectable INT)")
    fh1.execute("insert into base_cars select Id, MediaName, IsSelectable from Data_Car")
    fh1.execute("create table base_engines (EngineID INTEGER, MediaName TEXT)")
    fh1.execute("insert into base_engines select EngineID, MediaName from Data_Engine")
    fm4.executemany("insert into Data_Car values (?,?,?,1)", [
        (282, "VW_Beetle_04", 30), (1530, "FOR_Coupe_40", 41), (100, "AUD_A1_10", 50), (1215, "NUL_Car_00", 1),
        (283, "VW_BoraVR6_03", 61)])
    fm4.executemany("insert into Data_Engine values (?,?)", [(36, "VW_Beetle_04"), (37, "VW_BoraVR6_03"),
                                                            (990, "FOR_Coupe_40")])
    fm4.executemany("insert into List_UpgradeEngine values (?,?,?,?)", [
        (282000, 282, 36, 1), (282001, 282, 37, 0), (1530000, 1530, 990, 1), (100000, 100, 37, 1)])
    fm4.executemany("insert into Data_Drivetrain values (?)", [(35,), (504,), (937,)])
    fm4.executemany("insert into List_UpgradeDrivetrain values (?,?,?)", [
        (282000, 282, 35), (1530000, 1530, 937), (100000, 100, 504), (283000, 283, 504)])
    fm4.executemany("insert into List_Wheels values (?,?)", [(30, "VW_Beetle_04"), (41, "FOR_Coupe_40"),
                                                            (50, "AUD_A1_10")])
    return fh1, fm4


class TableTests(unittest.TestCase):

    def test_ids(self):
        self.assertEqual(car_ids.new_id(935), 55935)
        self.assertEqual(car_ids.new_id(1241), 56241)  # not 551241: a car id stays under 65536
        self.assertEqual(car_ids.twin_name("VW_Beetle_04"), "VW_5_Beetle_04")
        self.assertTrue(car_ids.official(r"C:\x\00000002\0390E8868FD0F18657144FA6F6B1329D980E8D024D\Media\a.zip"))
        self.assertFalse(car_ids.official(r"C:\x\00000002\4D5309C900000729\Media\DLCZips\729_pri_99.zip"))

    def test_cars(self):
        fh1, fm4 = fh1_and_fm4(self)
        data = car_ids.build({}, fh1, [("FM4", fm4)])
        cars = {c["name"]: c for c in data["cars"]}
        # the traffic Beetle: a copy of its own; the Coupe: FH1's RAV4 has 1530; the A1 keeps its
        # id; the Bora is FH1's own car and NUL_Car_00 a placeholder
        self.assertEqual(cars["VW_5_Beetle_04"], {"id": 55282, "name": "VW_5_Beetle_04", "from": "FM4",
                                                  "source_id": 282, "twin_of": 282})
        self.assertEqual(cars["FOR_Coupe_40"]["id"], 56530)
        self.assertEqual(cars["AUD_A1_10"]["id"], 100)
        self.assertEqual(sorted(cars), ["AUD_A1_10", "FOR_Coupe_40", "VW_5_Beetle_04"])
        self.assertEqual([c["id"] for c in data["cars"]], sorted(c["id"] for c in data["cars"]))
        self.assertEqual(car_ids.check(data), [])

    def test_a_car_in_two_games_has_one_id(self):
        fh1, fm4 = fh1_and_fm4(self)
        fh2 = sqlite3.connect(":memory:")
        self.addCleanup(fh2.close)
        fh2.execute("create table Data_Car (Id INTEGER, MediaName TEXT)")
        fh2.executemany("insert into Data_Car values (?,?)", [(51530, "FOR_Coupe_40"), (2010, "FH2_Only_14")])
        data = car_ids.build({}, fh1, [("FH2", fh2), ("FM4", fm4)])
        cars = {c["name"]: c for c in data["cars"]}
        # FH2 has the Coupe under 51530: FM4's version is the same car, with FH2's id
        self.assertEqual((cars["FOR_Coupe_40"]["id"], cars["FOR_Coupe_40"]["from"], cars["FOR_Coupe_40"]["also"]),
                         (51530, "FH2", {"FM4": 1530}))
        self.assertEqual(car_ids.Table(data).source_cars("FM4")[1530]["id"], 51530)
        self.assertEqual(cars["FH2_Only_14"]["id"], 2010)

    def test_an_id_once_in_the_table_never_changes(self):
        fh1, fm4 = fh1_and_fm4(self)
        old = {"cars": [{"id": 100, "name": "OTHER_Car_99", "from": "FM4", "source_id": 100}]}
        data = car_ids.build(old, fh1, [("FM4", fm4)])
        cars = {c["name"]: c["id"] for c in data["cars"]}
        self.assertEqual(cars["OTHER_Car_99"], 100)
        self.assertEqual(cars["AUD_A1_10"], 55100)  # 100 is taken now

    def test_parts(self):
        fh1, fm4 = fh1_and_fm4(self)
        t = car_ids.Table(car_ids.build({}, fh1, [("FM4", fm4)]))
        # engine 36: FH1's traffic Beetle's only, and FH1 has its name: a copy with "_FM4"
        self.assertEqual(t.part("FM4", "engine", 36), (55036, "VW_Beetle_04_FM4"))
        # engine 990: FH1's RAV4's, another engine
        self.assertEqual(t.part("FM4", "engine", 990), (55990, None))
        self.assertIsNone(t.part("FM4", "engine", 37))  # FH1's Bora's, the same engine: shared
        # drivetrain 35: the traffic car's only; 937: the RAV4's (no FH1 car using it uses it in
        # FM4); 504: FH1's Bora uses it in both games, shared
        self.assertEqual(t.part("FM4", "drivetrain", 35), (55035, None))
        self.assertEqual(t.part("FM4", "drivetrain", 937), (55937, None))
        self.assertIsNone(t.part("FM4", "drivetrain", 504))
        # wheels: the Beetle copy's, under its name; 41: FH1's is the RAV4's
        self.assertEqual(t.part("FM4", "wheel", 30), (55030, "VW_5_Beetle_04"))
        self.assertEqual(t.part("FM4", "wheel", 41), (55041, None))
        self.assertIsNone(t.part("FM4", "wheel", 50))

    def test_renumbered(self):
        fh1, fm4 = fh1_and_fm4(self)
        fh1.execute("delete from base_cars where Id=1530")  # the RAV4 as an FH1 DLC car, its engine too
        fh1.execute("delete from base_engines where EngineID=990")
        data = car_ids.build({}, fh1, [("FM4", fm4)])
        old = {"Forza Imported DLC Maker": {"282": {"id": 50282, "media": "VW_Beetle_04"},
                                            "1530": {"id": 51530, "media": "FOR_Coupe_40"},
                                            "283": {"id": 50283, "media": "VW_BoraVR6_03"},
                                            "100": {"id": 100, "media": "AUD_A1_10"}}}
        rows = car_ids.renumbered(data, fh1, fm4, old)["renumbered"]
        got = {(r["by"], r.get("kind", "car"), r["old"]): r["new"] for r in rows}
        self.assertEqual(got, {
            # the old import gave the Coupe 1530 to a player without the RAV4's DLC pack
            ("ReImagined FM4 import", "car", 1530): 56530,
            # the DLC maker's copies: the Beetle to the table's, the Bora (FH1's own car) to FH1's
            ("Forza Imported DLC Maker", "car", 50282): 55282,
            ("Forza Imported DLC Maker", "car", 51530): 56530,
            ("Forza Imported DLC Maker", "car", 50283): 283})
        # an imported car that keeps its id but whose part moves: the A1's swap... none here; the
        # old import used drivetrain 504 for the A1, which stays shared
        self.assertFalse([r for r in rows if r.get("kind") == "drivetrain"])

    def test_check(self):
        bad = {"cars": [{"id": 5, "name": "A"}, {"id": 5, "name": "B"}, {"id": 70000, "name": "a"}],
               "parts": {"FM4": {"engine": [{"source_id": 1, "id": 9}, {"source_id": 2, "id": 9}]}}}
        problems = car_ids.check(bad)
        self.assertTrue(any("car id 5" in p for p in problems))
        self.assertTrue(any("not under 65536" in p for p in problems))
        self.assertTrue(any("listed twice" in p for p in problems))
        self.assertTrue(any("engine" in p for p in problems))

    def test_the_table_in_the_repo_is_sound(self):
        t = car_ids.load()
        self.assertEqual(car_ids.check(t.data), [])
        self.assertEqual(t.car("VW_5_Beetle_04")["id"], 55282)
        self.assertEqual(t.car("MER_SLK55AMG_12")["id"], 51525)  # FH2's id for the same car
        self.assertGreater(len(t.cars), 500)


class LooseDlcPatchTests(unittest.TestCase):
    """A fan pack such as UDLC keeps its database patch next to its zip, not in it: the importer
    reads it too, so it does not import UDLC's cars a second time (#924)."""

    def test_a_loose_patch_is_read(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "game", "media"))
        patch_dir = os.path.join(root, "dlc", "4D5309C900000729", "Media", "DLCZips", "729_pri_99", "Media", "db", "patch")
        os.makedirs(patch_dir)
        db = sqlite3.connect(os.path.join(patch_dir, "72900_merge.slt"))
        db.execute("create table Data_Car (Id INTEGER, MediaName TEXT)")
        db.execute("insert into Data_Car values (257, 'FER_575_02')")
        db.commit()
        db.close()
        fh1 = fm4_cars.FH1(os.path.join(root, "game"), None, os.path.join(root, "dlc"))
        self.addCleanup(fh1.close)
        self.assertEqual(fh1.dlc_car_names(), ({"fer_575_02"}, {257}))


if __name__ == "__main__":
    unittest.main()
