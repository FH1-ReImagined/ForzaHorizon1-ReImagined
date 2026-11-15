#!/usr/bin/env python3
"""A drag tyre for every car (#924), as a mod of its own: tyre compound 10, from warshack4874's drag
tyre mod.

FH1's own cars have no drag tyre. Their tyres stop at level 3 (street, sport, race), apart from the
Rally Expansion's rally tyre (level 5, compound 9), and the FM4 import leaves FM4's level-4 drag
tyre out (docs/modding/fm4-cars.md). This gives every car of the game a drag tyre upgrade, the way
warshack4874's mod does (docs/modding/drag-tyres.md):

- drag_tyre.json, next to this script, holds:
  - the compound (List_TireCompound 10) with its own friction values;
  - its 18 curves (List_TireFrictionMultiCurve 19-20, List_TireFrictionCurve 37-40,
    List_TireAffectCurve 109-120);
  - its upgrade (Upgrades 273: tyre compound, level 6, with FM4's drag tyre icons, which FH1's UI
    textures have);
  - its English name and description.
- Every car the game has gets a level-6 upgrade to it (List_UpgradeTireCompound, car id x 1000 + 6,
  10,000 Cr): FH1's own, every installed DLC or fan pack's, and those of an FM4 cars import.

The rows go into a database patch, PATCH, which the game merges over its database as it does a DLC
pack's. The two strings go into each language's upgrades.str: FH1's table as the game merges it,
with the strings added, in English in every language as in the mod. The launcher builds this as the
hidden mod .reimagined-drag-tyres when Mods > Drag tyres is on (forza_launcher/drag_tyres.py).

    python tools/drag_tyres.py --fh1 GAME [--update TU4] [--dlc DLC] [--cars-db GAMEDB ...] --out MOD
"""
import argparse
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import fm4_cars  # noqa: E402  (FH1's files: its database, the DLC patches, the string tables)

DATA = os.path.join(HERE, "drag_tyre.json")
PATCH = "media/db/patch/924006_merge.slt"
STRING_TABLE = "Upgrades"
CURVES = ("List_TireFrictionCurve", "List_TireAffectCurve", "List_TireFrictionMultiCurve")
TABLES = CURVES + ("List_TireCompound", "Upgrades", "List_UpgradeTireCompound")


class DragError(Exception):
    pass


def load(path=None):
    """The data file: the compound, its curves, its upgrade, its strings and the part's values."""
    with open(path or DATA, encoding="utf-8") as f:
        data = json.load(f)
    for key in ("compound", "curves", "upgrade", "strings", "part"):
        if key not in data:
            raise DragError("%s has no %r" % (path or DATA, key))
    return data


def _connect(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def _tables(db):
    return {r[0] for r in db.execute("select name from sqlite_master where type='table'")}


def _values(db, table, col):
    """Every value of col in table, or none when db lacks the table."""
    if table not in _tables(db):
        return set()
    return {v for (v,) in db.execute("select [%s] from [%s]" % (col, table))}


def car_ids(dbs):
    """Every car id of the given databases (FH1's, its DLC packs' patches, an FM4 cars import's)."""
    out = set()
    for db in dbs:
        out |= {v for v in _values(db, "Data_Car", "Id") if isinstance(v, int)}
    return sorted(out)


def check_ids(data, dbs):
    """The rows the data adds under fixed ids (the compound, its curves, its upgrade) must not
    replace another row of FH1's or a DLC pack's: one that is the same (the mod installed another
    way) is fine."""
    rows = [(t, r) for t in CURVES for r in data["curves"][t]]
    rows += [("List_TireCompound", data["compound"]), ("Upgrades", data["upgrade"])]
    for db in dbs:
        have = _tables(db)
        for table, row in rows:
            if table not in have:
                continue
            pk = fm4_cars.columns(db, table)[0]
            got = db.execute("select * from [%s] where [%s]=?" % (table, pk), (row[pk],)).fetchone()
            if got is None:
                continue
            got = dict(zip(fm4_cars.columns(db, table), got))
            if any(got.get(c) != v for c, v in row.items() if c in got and c not in ("Name", "Description")):
                raise DragError("%s %s %s is another row in the game's database already" % (table, pk, row[pk]))


def update_patches(fh1):
    """Paths of the title update's own database patches (db/patch/*.slt in its media.zip: TU4 has
    1401000_merge.slt, rally parts of 18 DLC cars), which the game merges like the DLC packs'."""
    if not fh1.update:
        return []
    z = fm4_cars.ZIPS.get(fh1.update)
    return [fh1._temp(fm4_cars.Member(z, fh1.update, i).read()) for i in z.infolist()
            if re.match(r"(?i)db/patch/[^/]+\.slt$", i.filename)]


def string_tables(fh1):
    """{language: StringTable} of upgrades.str as the game merges it (the title update's copy, then
    the installed DLC's)."""
    out = {}
    for lang in sorted(l for l in fh1.languages() if l not in ("dev", "loc")):
        _, table = fh1.strings_raw(STRING_TABLE, lang)
        if table.entries:
            out[lang] = table
    return out


def string_keys(tables, data):
    """{field: key} of the name and description. A string of that IDS_ name keeps its key; a new
    one takes the data's key when it is free in every language's table, else the next key free in
    all of them (the upgrade's references are the same for every language)."""
    taken = set()
    for t in tables.values():
        taken |= set(t.entries)
    out = {}
    for field, s in sorted(data["strings"].items()):
        have = {t.by_name(s["ids"])[0] for t in tables.values()}
        if len(have) == 1 and None not in have:
            out[field] = have.pop()
            continue
        key = s["key"]
        while key in taken or key in (0, 0xFFFF):
            key = (key + 1) & 0xFFFF
        taken.add(key)
        out[field] = key
    return out


def reference_high(fh1db):
    """The upper 16 bits of FH1's "_&N" references to upgrades.str (N & 0xFFFF is the key)."""
    seen = {}
    for (ref,) in fh1db.execute("select Name from Upgrades"):
        if isinstance(ref, str) and re.match(r"_&\d+$", ref):
            hi = int(ref[2:]) >> 16
            seen[hi] = seen.get(hi, 0) + 1
    if not seen:
        raise DragError("FH1's Upgrades table names no string")
    return max(seen.items(), key=lambda kv: kv[1])[0]


def write_patch(path, fh1db, cars, data, keys, hi, taken=()):
    """The database patch: the compound, its curves and its upgrade, and each car's level-6 tyre
    (skipping a car whose key some row has already). Tables as FH1 makes them. Returns the count
    of cars."""
    if os.path.exists(path):
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = sqlite3.connect(path)
    try:
        for t in TABLES:
            sql = fh1db.execute("select sql from sqlite_master where type='table' and name=?", (t,)).fetchone()
            if not sql:
                raise DragError("FH1's database has no %s table" % t)
            out.execute(sql[0])
        cols = {t: fm4_cars.columns(fh1db, t) for t in TABLES}

        def put(table, row):
            extra = [c for c in row if c not in cols[table]]
            if extra:
                raise DragError("FH1's %s has no column %s" % (table, ", ".join(extra)))
            out.execute("insert into [%s] (%s) values (%s)" % (table, ",".join("[%s]" % c for c in row),
                                                               ",".join("?" * len(row))), list(row.values()))

        for t in CURVES:
            for r in data["curves"][t]:
                put(t, r)
        put("List_TireCompound", data["compound"])
        put("Upgrades", dict(data["upgrade"], Name="_&%d" % (hi << 16 | keys["Name"]),
                             Description="_&%d" % (hi << 16 | keys["Description"])))
        n = 0
        level = data["part"]["Level"]
        for car in cars:
            key = car * 1000 + level
            if key in taken:
                continue
            put("List_UpgradeTireCompound", dict(data["part"], Id=key, Ordinal=car))
            n += 1
        out.commit()
        return n
    finally:
        out.close()


def build(fh1, out, extra=(), data=None):
    """The mod's files in out: PATCH and media/stringtables/<lang>/upgrades.str. fh1: an fm4_cars.FH1;
    extra: paths of more databases whose cars get the tyre (an FM4 cars import's gamedb.slt).
    Returns {"cars", "languages", "keys"}."""
    data = data or load()
    fh1db = fh1.db()
    patches = [_connect(p) for p in update_patches(fh1) + fh1.dlc_patches()]
    extras = [_connect(p) for p in extra]
    try:
        dbs = [fh1db] + patches + extras
        check_ids(data, [fh1db] + patches)
        cars = car_ids(dbs)
        taken = set()
        for db in dbs:
            taken |= _values(db, "List_UpgradeTireCompound", "Id")
        tables = string_tables(fh1)
        if not tables:
            raise DragError("no upgrades.str in the game's files")
        keys = string_keys(tables, data)
        n = write_patch(os.path.join(out, *PATCH.split("/")), fh1db, cars, data, keys, reference_high(fh1db), taken)
    finally:
        for db in patches + extras:
            db.close()
    for lang, table in tables.items():
        for field, s in data["strings"].items():
            if table.add(s["ids"], s["text"], s["sec4"], keys[field]) != keys[field]:
                raise DragError("%s's upgrades.str has %s under another key" % (lang, s["ids"]))
        path = os.path.join(out, "media", "stringtables", lang, STRING_TABLE.lower() + ".str")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(table.encode())
    return {"cars": n, "languages": sorted(tables), "keys": keys}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fh1", required=True, help="FH1's game folder")
    ap.add_argument("--update", help="the title update folder (with media.zip)")
    ap.add_argument("--dlc", help="the folder of installed DLC packages")
    ap.add_argument("--cars-db", action="append", default=[], help="another database whose cars get the tyre "
                    "(an FM4 cars import's media/db/gamedb.slt)")
    ap.add_argument("--out", required=True, help="the mod folder to write")
    a = ap.parse_args(argv)
    fh1 = fm4_cars.FH1(a.fh1, a.update, a.dlc)
    try:
        rep = build(fh1, a.out, a.cars_db)
    except (DragError, fm4_cars.ConvertError) as e:
        print("error:", e)
        return 1
    finally:
        fh1.close()
    print("drag tyres for %d cars, strings in %d languages (keys %s)" % (rep["cars"], len(rep["languages"]), rep["keys"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
