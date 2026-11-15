"""Synthetic tests of the drag tire builder (#924): no game file is read or included."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drag_tires  # noqa: E402
import fm4_cars  # noqa: E402

HIGH = 0x7091  # FH1's upgrades.str references: "_&N", N = HIGH << 16 | key
PART_COLUMNS = ("Id", "Ordinal", "Level", "IsStock", "TireCompoundID", "ManufacturerID", "Price", "MassDiff",
                "DragScale", "WindInstabilityScale")


def table_sql(name, cols):
    return "create table [%s] (%s)" % (name, ", ".join("[%s]" % c for c in cols))


class FakeFH1:
    """The parts of fm4_cars.FH1 the builder uses: the database, the DLC patches, the string tables."""

    def __init__(self, db, patches, tables):
        self._db, self._patches, self._tables, self.update = db, patches, tables, None

    def db(self):
        return self._db

    def dlc_patches(self):
        return self._patches

    def languages(self):
        return {lang: "%s.zip" % lang for lang in self._tables}

    def strings_raw(self, table, lang):
        assert table == "Upgrades"
        t = fm4_cars.StringTable()
        t.entries = {k: list(v) for k, v in self._tables[lang].items()}
        return b"", t


class DragTireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = drag_tires.load()

    def game_db(self, path=":memory:", cars=(100, 101)):
        db = sqlite3.connect(path)
        db.execute("create table Data_Car (Id INTEGER, MediaName TEXT)")
        db.executemany("insert into Data_Car values (?, ?)", [(c, "CAR_%d" % c) for c in cars])
        db.execute(table_sql("List_UpgradeTireCompound", PART_COLUMNS))
        db.execute("insert into List_UpgradeTireCompound (Id, Ordinal, Level, TireCompoundID) values (100005, 100, 5, 9)")
        db.execute(table_sql("Upgrades", ("id", "TypeId", "IsException", "Name", "Level", "Description", "IconPath",
                                          "ImagePath")))
        db.execute("insert into Upgrades (id, TypeId, Name, Level) values (78, '21', ?, 0)", ("_&%d" % (HIGH << 16 | 5),))
        db.execute(table_sql("List_TireCompound", list(self.data["compound"])))
        for t in drag_tires.CURVES:
            db.execute(table_sql(t, list(self.data["curves"][t][0])))
        db.commit()
        return db

    def patch(self, name, cars, tires=()):
        path = os.path.join(self.tmp, name)
        db = sqlite3.connect(path)
        db.execute("create table Data_Car (Id INTEGER, MediaName TEXT)")
        db.executemany("insert into Data_Car values (?, ?)", [(c, "DLC_%d" % c) for c in cars])
        db.execute(table_sql("List_UpgradeTireCompound", PART_COLUMNS))
        db.executemany("insert into List_UpgradeTireCompound (Id, Ordinal, Level) values (?, ?, 6)", [(t, t // 1000) for t in tires])
        db.commit()
        db.close()
        return path

    def build(self, tables=None, fh1db=None, patches=None, extra=()):
        tables = tables or {"en": {5: ["Stock", "IDS_Stock", 0, 0]}, "de": {5: ["Serie", "IDS_Stock", 0, 0]}}
        fh1 = FakeFH1(fh1db or self.game_db(), patches if patches is not None else [], tables)
        out = os.path.join(self.tmp, "mod")
        return drag_tires.build(fh1, out, list(extra), self.data), out

    def test_every_car_gets_the_drag_tire(self):
        """FH1's cars, a DLC pack's (but the one that has a level-6 tire already) and an FM4 cars
        import's: each a level-6 tire of compound 10, car id x 1000 + 6, the data's values."""
        dlc = self.patch("dlc.slt", [1600, 1601], tires=[1601006])
        fm4 = self.patch("fm4.slt", [100, 56541])  # an import's database holds FH1's cars too
        rep, out = self.build(patches=[dlc], extra=[fm4])
        self.assertEqual(rep["cars"], 4)
        db = sqlite3.connect(os.path.join(out, *drag_tires.PATCH.split("/")))
        self.addCleanup(db.close)
        rows = db.execute("select Id, Ordinal, Level, IsStock, TireCompoundID, Price from List_UpgradeTireCompound "
                          "order by Id").fetchall()
        self.assertEqual(rows, [(100006, 100, 6, 0, 10, 10000), (101006, 101, 6, 0, 10, 10000),
                                (1600006, 1600, 6, 0, 10, 10000), (56541006, 56541, 6, 0, 10, 10000)])
        # the compound, its curves and its upgrade, as the data has them
        compound = db.execute("select * from List_TireCompound").fetchall()
        self.assertEqual(compound, [tuple(self.data["compound"].values())])
        for t in drag_tires.CURVES:
            self.assertEqual(sorted(db.execute("select * from [%s]" % t).fetchall()),
                             sorted(tuple(r.values()) for r in self.data["curves"][t]))
        up = db.execute("select id, TypeId, Level, Name, Description, IconPath from Upgrades").fetchall()
        self.assertEqual(up, [(273, "21", 6, "_&%d" % (HIGH << 16 | 32624), "_&%d" % (HIGH << 16 | 63249),
                               self.data["upgrade"]["IconPath"])])

    def test_the_strings_in_every_language(self):
        """The name and description under the data's keys and IDS_ names, in every language; a key
        another string has in any language moves for all (the upgrade's reference is one)."""
        tables = {"en": {5: ["Stock", "IDS_Stock", 0, 0]},
                  "de": {5: ["Serie", "IDS_Stock", 0, 0], 32624: ["Andere", "IDS_Other", 1, 0]}}
        rep, out = self.build(tables=tables)
        self.assertEqual(rep["keys"], {"Name": 32625, "Description": 63249})
        self.assertEqual(rep["languages"], ["de", "en"])
        for lang, stock in (("en", "Stock"), ("de", "Serie")):
            with open(os.path.join(out, "media", "stringtables", lang, "upgrades.str"), "rb") as f:
                t = fm4_cars.StringTable(f.read())
            self.assertEqual(t.entries[5][:2], [stock, "IDS_Stock"])  # FH1's own, as they were
            self.assertEqual(t.entries[32625][:2], ["Drag Tire Compound", "IDS_Name_DragTire"])
            self.assertEqual(t.entries[63249][1], "IDS_Description_DragTire")
            self.assertTrue(t.entries[63249][0].startswith("Install drag racing tires"))
        self.assertEqual(t.entries[32624][:2], ["Andere", "IDS_Other"])
        db = sqlite3.connect(os.path.join(out, *drag_tires.PATCH.split("/")))
        self.addCleanup(db.close)
        self.assertEqual(db.execute("select Name from Upgrades").fetchone()[0], "_&%d" % (HIGH << 16 | 32625))

    def test_strings_the_table_has_already_keep_their_keys(self):
        """The mod installed another way: its strings are there, under their keys; nothing moves."""
        both = {5: ["Stock", "IDS_Stock", 0, 0], 32624: ["Drag Tire Compound", "IDS_Name_DragTire", 1, 0],
                63249: ["Install...", "IDS_Description_DragTire", 2, 0]}
        rep, _ = self.build(tables={"en": dict(both), "fr": dict(both)})
        self.assertEqual(rep["keys"], {"Name": 32624, "Description": 63249})

    def test_an_id_another_row_has_stops_it(self):
        db = self.game_db()
        db.execute("insert into List_TireCompound (TireCompoundID, DisplayName) values (10, 'SOMETHING_ELSE')")
        with self.assertRaises(drag_tires.DragError):
            self.build(fh1db=db)

    def test_the_same_rows_installed_another_way_are_fine(self):
        db = self.game_db()
        c = self.data["compound"]
        db.execute("insert into List_TireCompound (%s) values (%s)" % (",".join("[%s]" % k for k in c),
                                                                        ",".join("?" * len(c))), list(c.values()))
        rep, _ = self.build(fh1db=db)
        self.assertEqual(rep["cars"], 2)

    def test_the_data_file_is_whole(self):
        """Every curve the compound and its multi curves name is in the data; the upgrade is a tire
        compound one at the part's level; the strings have names and texts."""
        d = self.data
        affect = {r["AffectCurveID"] for r in d["curves"]["List_TireAffectCurve"]}
        multi = {r["FrictionMultiCurveID"]: r for r in d["curves"]["List_TireFrictionMultiCurve"]}
        friction = {r["FrictionCurveID"] for r in d["curves"]["List_TireFrictionCurve"]}
        for col, v in d["compound"].items():
            if col.startswith("AffectCurve") and col.endswith("ID"):
                self.assertIn(v, affect, col)
            elif col.startswith("FrictionMultiCurve"):
                self.assertIn(v, multi, col)
        for r in multi.values():
            self.assertTrue({r["TireFrictionCurveID0"], r["TireFrictionCurveID1"]} <= friction)
        self.assertEqual((d["upgrade"]["TypeId"], d["upgrade"]["Level"]), ("21", d["part"]["Level"]))
        self.assertEqual((d["part"]["Level"], d["part"]["TireCompoundID"]), (6, d["compound"]["TireCompoundID"]))
        for s in d["strings"].values():
            self.assertTrue(s["ids"].startswith("IDS_") and s["text"])


if __name__ == "__main__":
    unittest.main()
