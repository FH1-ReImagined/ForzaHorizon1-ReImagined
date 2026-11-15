"""One id for every car an import can bring, the same for every player (#924).

Online and in saves one id has to mean one car for every player, whatever each of them imported or
has installed. So the importers take their ids from this table (car_ids.json, next to this script)
and never work them out from a player's own files. The table only grows: an id in it never changes,
and an id that had to change from an older release is listed under "renumbered", for the launcher's
save migration.

Cars. A car is its MediaName: a car in several games has one id. The order of authority is FH1,
FH1's official DLC (every pack, installed or not; they are never imported), FH2, Fast & Furious,
then FM4 with its DLC. A car keeps its own game's id unless a car earlier in the order has it; it
then takes 55000 + its id (a 55 in front of a three-digit id). FH1's 18 traffic cars
(FH1_TRAFFIC_CARS) that a later game has in full get a copy of their own: 55000 + id and a 5 after
the make in the MediaName (VW_Beetle_04 -> VW_5_Beetle_04); FH1's traffic car stays as it is. A car
id stays under 65536: with two car ids above that in the database, no imported car loaded.

Parts (FM4's). An engine, drivetrain or stock wheel of an FM4 car takes 55000 + its id when FH1 or
its DLC has that id for another one (an engine or wheel of another MediaName, a drivetrain that only
other cars use) or has it only on traffic cars, which FH1 cut down. An engine whose MediaName FH1
has keeps it with "_FM4" after it: the game finds the EngineTuning by that name, and FH1's file of
the old name would win. A traffic car's wheel copy takes the car copy's name. A part FH1 keeps with
no car using it is shared, as the importer always did (an imported car's saved parts keep their ids).

    python tools/car_ids.py build --fh1 GAME --update TU4 --dlc FH1_DLC --fh2 FH2 --ff FH2FF
                                  --fm4 FM4_DISC1 [--fm4-content PACKS] [--fm4-dlc FM4_DLC]
                                  [--old-ids "RELEASE=fm4_car_ids.json"]
    python tools/car_ids.py check

`build` needs every game (only who maintains the table runs it): `--dlc` is the folder holding all
of FH1's official DLC packages (folders named by their 42-digit hash; other packs there are left
out). The importers only read the table.
"""
import argparse
import json
import os
import re
import sqlite3
import sys

TABLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "car_ids.json")
BASE = 55000
CAR_ID_LIMIT = 65536
PART_KINDS = ("engine", "drivetrain", "wheel")
OLD_IMPORT = "ReImagined FM4 import"
# FH1's traffic cars. FH1 marks them as cars you can't pick (IsSelectable 0), and so it marks its
# barn finds, unicorns and showcase cars (1565-1568, 1626-1635), which are FH1's own cars to find or
# win, not traffic; nothing in the database tells them apart. The 18 are those the Traffic Car
# Unlocker mod (package 732) makes buyable.
FH1_TRAFFIC_CARS = ("VW_Beetle_04", "CHR_PTCruiser_04", "FOR_TaurusSHO_10", "TOY_Prius_11", "NIS_Leaf_11",
                    "BUS_TOUR_12", "TOY_RAV4Sport_04", "AUD_A4Avant_04", "TOY_Camry_07", "FOR_Explorer_10",
                    "FOR_F350SuperDuty_08", "FOR_FocusZX4_05", "TOY_CorollaDX_95", "CHE_Express2500Cargo_05",
                    "DOD_Durango_99", "GMC_YukonXL_01", "VOL_XC70_12", "KEN_T440_12")


class TableError(Exception):
    pass


def new_id(n):
    """The id of something an import needs a copy of: 55000 + its id (#924)."""
    return BASE + n


def twin_name(name):
    """The MediaName of an FM4 copy of FH1's traffic car: a 5 after the make's part,
    VW_Beetle_04 -> VW_5_Beetle_04 (#841). Make and year stay where they are."""
    make, sep, rest = str(name).partition("_")
    return "%s_5_%s" % (make, rest) if sep else "%s_5" % name


class Table:
    """car_ids.json, read: cars by MediaName, the parts of a source that take new ids, the
    renumbered ids of older releases."""

    def __init__(self, data):
        self.data = data
        self.cars = {c["name"].lower(): c for c in data.get("cars", [])}
        self.parts = {}  # (source, kind) -> {source id: (new id, new MediaName or None)}
        for source, kinds in data.get("parts", {}).items():
            for kind, rows in kinds.items():
                self.parts[(source, kind)] = {r["source_id"]: (r["id"], r.get("name")) for r in rows}
        self.renumbered = list(data.get("renumbered", []))

    def car(self, name):
        return self.cars.get(str(name).lower())

    def source_cars(self, source):
        """{the source's car id: table entry} for every car the source can bring: its own, those an
        earlier source has too, its copies of FH1's traffic cars."""
        out = {}
        for c in self.cars.values():
            if c["from"] == source:
                out[c["source_id"]] = c
            elif source in c.get("also", {}):
                out[c["also"][source]] = c
        return out

    def part(self, source, kind, sid):
        """(new id, new MediaName or None) of a source's part that takes a new id, else None."""
        return self.parts.get((source, kind), {}).get(sid)


def load(path=None):
    """The table: car_ids.json next to this script, or in a tools folder beside it (the launcher's
    frozen build keeps its tools there, its modules elsewhere)."""
    if path is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = next((p for p in (TABLE, os.path.join(here, "tools", "car_ids.json")) if os.path.isfile(p)), TABLE)
    with open(path, encoding="utf-8") as f:
        return Table(json.load(f))


def check(data):
    """Problems of a table that need no game file to see: ids or names used twice, car ids out of
    range. [] when none."""
    problems, seen = [], {}
    for c in data.get("cars", []):
        if c["id"] in seen:
            problems.append("car id %d is %s and %s" % (c["id"], seen[c["id"]], c["name"]))
        seen[c["id"]] = c["name"]
        if not 0 < c["id"] < CAR_ID_LIMIT:
            problems.append("%s: car id %d is not under %d" % (c["name"], c["id"], CAR_ID_LIMIT))
    names = [c["name"].lower() for c in data.get("cars", [])]
    if len(names) != len(set(names)):
        problems.append("a MediaName is listed twice")
    for source, kinds in data.get("parts", {}).items():
        for kind, rows in kinds.items():
            ids = [r["id"] for r in rows]
            if len(ids) != len(set(ids)):
                problems.append("%s %s: a new id is used twice" % (source, kind))
    return problems


# ----------------------------------------------------------------------------- build

def _rows(db, sql, args=()):
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def _cars(db, selectable=False):
    return [tuple(r) for r in _rows(db, "select Id, MediaName%s from Data_Car" % (", IsSelectable" if selectable else ""))]


def _users(db, table, col, key):
    """MediaNames (lowercase) of the cars whose rows of table name key in col."""
    return {str(n).lower() for (n,) in _rows(
        db, "select c.MediaName from [%s] u join Data_Car c on c.Id=u.Ordinal where u.[%s]=?" % (table, col), (key,))}


def _traffic(fh1db):
    """{MediaName (lowercase): id} of FH1's traffic cars (FH1_TRAFFIC_CARS) in FH1's own database."""
    rows = _rows(fh1db, "select Id, MediaName, IsSelectable from base_cars") or _cars(fh1db, True)
    names = {n.lower() for n in FH1_TRAFFIC_CARS}
    return {str(n).lower(): i for i, n, s in rows if not s and str(n).lower() in names}




def official(path):
    """True for a file of one of FH1's official DLC packages: their folders are named by a 42-digit
    hash (a fan pack such as UDLC is 4D5309C9 and its offer number)."""
    return any(re.fullmatch(r"[0-9A-Fa-f]{42}", p) for p in re.split(r"[\\/]", path))


def merged_official(fh1):
    """FH1's database with every official DLC pack's patch merged in, as the game merges them (a row
    of a pack replaces the row of its key), in memory; FH1's own car ids in a base_cars table."""
    import fm4_cars  # the importer's readers of the games' files
    out = sqlite3.connect(":memory:")
    fh1.db().backup(out)
    # FH1's own cars as FH1 has them: a DLC patch may give a base car another IsSelectable
    out.execute("create table base_cars (Id INTEGER, MediaName TEXT, IsSelectable INT)")
    out.execute("insert into base_cars select Id, MediaName, IsSelectable from Data_Car")
    out.execute("create table base_engines (EngineID INTEGER, MediaName TEXT)")
    out.execute("insert into base_engines select EngineID, MediaName from Data_Engine")
    patches = []
    for _, path in fh1.dlc_zips:
        if official(path):
            z = fm4_cars.ZIPS.get(path)
            patches += [fh1._temp(fm4_cars.Member(z, path, i).read()) for i in z.infolist()
                        if re.search(r"(?i)/db/patch/[^/]+\.slt$", i.filename)]
    patches += [p for p in fh1.dlc_loose_patches if official(p)]
    for p in patches:
        patch = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
        for (t,) in _rows(patch, "select name from sqlite_master where type='table'"):
            mine = [r[1] for r in _rows(out, "pragma table_info([%s])" % t)]
            if not mine or t == "base_cars":
                continue
            cols = [c for c in (r[1] for r in _rows(patch, "pragma table_info([%s])" % t)) if c in mine]
            rows = _rows(patch, "select %s from [%s]" % (",".join("[%s]" % c for c in cols), t))
            out.executemany("insert or replace into [%s] (%s) values (%s)"
                            % (t, ",".join("[%s]" % c for c in cols), ",".join("?" * len(cols))), rows)
        patch.close()
    return out


def build(data, fh1db, sources):
    """The table with the cars and parts of sources added; an entry already in it never changes.
    fh1db: FH1's database with every official DLC patch merged (merged_official); sources: [(name,
    database)] in the order of authority (FH2, FF, FM4)."""
    data = json.loads(json.dumps(data or {}))
    cars = data.setdefault("cars", [])
    fixed = _cars(fh1db, True)
    fixed_names = {str(n).lower() for _, n, _ in fixed}
    traffic = _traffic(fh1db)
    by_id = {i: str(n).lower() for i, n, _ in fixed}
    known = {c["name"].lower(): c for c in cars}
    by_id.update({c["id"]: c["name"].lower() for c in cars})

    def also(c, source, sid):
        if c["from"] != source:
            c.setdefault("also", {})[source] = sid

    for source, db in sources:
        for sid, name in sorted(_cars(db)):
            low = str(name).lower()
            if low.startswith("nul_"):
                continue
            if low in traffic:  # FH1 has it only as a traffic car: a copy of its own
                copy = twin_name(name)
                if copy.lower() in known:
                    also(known[copy.lower()], source, sid)
                    continue
                want = new_id(traffic[low])
                if want in by_id or want >= CAR_ID_LIMIT:
                    raise TableError("%s %s: its copy's id %d is taken or too high" % (source, name, want))
                c = {"id": want, "name": copy, "from": source, "source_id": sid, "twin_of": traffic[low]}
            elif low in fixed_names:
                continue  # FH1's or its DLC's: never imported
            elif low in known:
                also(known[low], source, sid)
                continue
            else:
                want = sid if sid not in by_id else new_id(sid)
                if want in by_id or want >= CAR_ID_LIMIT:
                    raise TableError("%s %s: no free id (%d is taken or too high)" % (source, name, want))
                c = {"id": want, "name": name, "from": source, "source_id": sid}
            cars.append(c)
            known[c["name"].lower()], by_id[c["id"]] = c, c["name"].lower()
    cars.sort(key=lambda c: c["id"])
    fm4 = dict(sources).get("FM4")
    if fm4 is not None:
        fm4_parts(data, fh1db, fm4)
    return data


def fm4_parts(data, fh1db, fm4db):
    """The FM4 engines, drivetrains and stock wheels that take new ids (the module's notes)."""
    mine = Table(data).source_cars("FM4")  # FM4 car id -> table entry
    parts = data.setdefault("parts", {}).setdefault("FM4", {})
    have = {kind: {r["source_id"]: r for r in parts.get(kind, [])} for kind in PART_KINDS}
    taken = {kind: {r["id"] for r in parts.get(kind, [])} for kind in PART_KINDS}
    for kind, table, col in (("engine", "Data_Engine", "EngineID"), ("drivetrain", "Data_Drivetrain", "DrivetrainID"),
                             ("wheel", "List_Wheels", "ID")):
        taken[kind] |= {r[0] for r in _rows(fh1db, "select [%s] from [%s]" % (col, table))}
    traffic = set(_traffic(fh1db))

    def add(kind, sid, name=None):
        if sid in have[kind]:
            return
        want = new_id(sid)
        if want in taken[kind]:
            raise TableError("FM4 %s %d: its new id %d is taken" % (kind, sid, want))
        r = {"source_id": sid, "id": want}
        if name:
            r["name"] = name
        parts.setdefault(kind, []).append(r)
        have[kind][sid] = r
        taken[kind].add(want)

    def traffic_only(users):
        return bool(users) and users <= traffic

    fh_engine_names = {str(n).lower() for (n,) in _rows(fh1db, "select MediaName from Data_Engine")}
    engines, drivetrains, wheels = set(), set(), {}
    for cid, c in mine.items():
        engines |= {r[0] for r in _rows(fm4db, "select EngineID from List_UpgradeEngine where Ordinal=?", (cid,))}
        engines |= {r[0] for r in _rows(fm4db, "select EngineID from Combo_Engines where Ordinal=?", (cid,))}
        drivetrains |= {r[0] for r in _rows(fm4db, "select DrivetrainID from List_UpgradeDrivetrain where Ordinal=?", (cid,))}
        for (w,) in _rows(fm4db, "select StockWheelID from Data_Car where Id=?", (cid,)):
            if w is not None:
                wheels.setdefault(w, []).append(c)
    for e in sorted(x for x in engines if x is not None):
        ours = _rows(fm4db, "select MediaName from Data_Engine where EngineID=?", (e,))
        theirs = _rows(fh1db, "select MediaName from Data_Engine where EngineID=?", (e,))
        if not ours or not theirs:
            continue
        name = str(ours[0][0])
        users = _users(fh1db, "List_UpgradeEngine", "EngineID", e) | _users(fh1db, "Combo_Engines", "EngineID", e)
        if str(theirs[0][0]).lower() != name.lower() or traffic_only(users):
            add("engine", e, name + "_FM4" if name.lower() in fh_engine_names else None)
    fm4_drivetrains = {}  # FM4 car (lowercase MediaName) -> the drivetrains it has in FM4
    for name, d in _rows(fm4db, "select c.MediaName, u.DrivetrainID from List_UpgradeDrivetrain u "
                                "join Data_Car c on c.Id=u.Ordinal"):
        fm4_drivetrains.setdefault(str(name).lower(), set()).add(d)
    for d in sorted(x for x in drivetrains if x is not None):
        # another car's when none of the FH1 cars using it uses it in FM4 too (FH1 gave the id to a
        # car of its own); one both games share for several cars, such as Ford's 504, stays shared
        users = _users(fh1db, "List_UpgradeDrivetrain", "DrivetrainID", d)
        shared = any(d in fm4_drivetrains.get(u, ()) for u in users)
        if traffic_only(users) or (users and not shared):
            add("drivetrain", d)
    for w, users in sorted(wheels.items()):
        ours = _rows(fm4db, "select MediaName from List_Wheels where ID=?", (w,))
        theirs = _rows(fh1db, "select MediaName from List_Wheels where ID=?", (w,))
        if ours and any(c.get("twin_of") for c in users):
            add("wheel", w, twin_name(ours[0][0]))
        elif ours and theirs and str(theirs[0][0]).lower() != str(ours[0][0]).lower():
            add("wheel", w)
    for kind in parts:
        parts[kind].sort(key=lambda r: r["source_id"])


def renumbered(data, fh1db, fm4db=None, old=None):
    """The ids an older release used that the table moves, for the launcher's save migration:
    - cars ReImagined's FM4 import gave their FM4 id where FH1 itself left it free (so a player
      without the FH1 DLC pack that uses it got the car under that id), not those it left out for
      an engine id FH1 gives another engine;
    - parts that move of the cars it imported under their own ids (a saved car's installed parts
      are rows made from them: id x 1000 + n), with the cars that use them;
    - old: {release: Forza Imported DLC Maker's fm4_car_ids.json ({FM4 id: {"id", "media"}})}: each
      car goes to its id in the table, or to FH1's or its DLC's own id when the table leaves it out
      (an import brings no copy of FH1's cars but its traffic cars)."""
    t = Table(data)
    base_ids = {r[0] for r in _rows(fh1db, "select Id from base_cars")}
    base_engines = {i: str(n).lower() for i, n in _rows(fh1db, "select EngineID, MediaName from base_engines")}
    fixed = {str(n).lower(): i for i, n in _cars(fh1db)}
    out = {(r["by"], r.get("kind", "car"), r["old"]): r for r in data.get("renumbered", [])}
    mine = t.source_cars("FM4")

    def stock_engine_clash(sid):
        for e, name in _rows(fm4db, "select u.EngineID, e.MediaName from List_UpgradeEngine u join Data_Engine e "
                                    "on e.EngineID=u.EngineID where u.Ordinal=? and u.IsStock=1", (sid,)) if fm4db else []:
            if e in base_engines and base_engines[e] != str(name).lower():
                return True
        return False

    for sid, c in mine.items():
        if c["id"] != sid and not c.get("twin_of") and sid not in base_ids and not stock_engine_clash(sid):
            out.setdefault((OLD_IMPORT, "car", sid), {"old": sid, "name": c["name"], "new": c["id"], "by": OLD_IMPORT})
    if fm4db is not None:
        kept = {sid: c for sid, c in mine.items() if c["id"] == sid and not c.get("twin_of")}
        for kind, table, col in (("engine", "List_UpgradeEngine", "EngineID"),
                                 ("drivetrain", "List_UpgradeDrivetrain", "DrivetrainID")):
            for part, (new, _) in sorted(t.parts.get(("FM4", kind), {}).items()):
                if kind == "engine" and part in base_engines and _rows(
                        fm4db, "select 1 from Data_Engine where EngineID=? and lower(MediaName)<>?", (part, base_engines[part])):
                    continue  # an engine FH1 gives another engine: the import left those rows out
                cars = sorted(sid for sid in kept if _rows(
                    fm4db, "select 1 from [%s] where Ordinal=? and [%s]=?" % (table, col), (sid, part)))
                if cars:
                    out.setdefault((OLD_IMPORT, kind, part), {"kind": kind, "old": part, "new": new, "cars": cars,
                                                             "by": OLD_IMPORT})
    for release, ids in (old or {}).items():
        for v in ids.values():
            low = str(v["media"]).lower()
            entry = t.car(low) or t.car(twin_name(v["media"]))
            new = entry["id"] if entry else fixed.get(low)
            if new is not None and new != v["id"]:
                out.setdefault((release, "car", v["id"]), {"old": v["id"], "name": v["media"], "new": new, "by": release})
    data["renumbered"] = sorted(out.values(), key=lambda r: (r["by"], r.get("kind", "car"), r["old"]))
    return data


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["build", "check"])
    ap.add_argument("--table", default=TABLE)
    ap.add_argument("--fh1")
    ap.add_argument("--update")
    ap.add_argument("--dlc", help="the folder holding all of FH1's official DLC packages")
    ap.add_argument("--fh2", help="FH2's game folder")
    ap.add_argument("--ff", help="FH2 Presents Fast & Furious's game folder")
    ap.add_argument("--fm4")
    ap.add_argument("--fm4-content")
    ap.add_argument("--fm4-dlc")
    ap.add_argument("--old-ids", action="append", default=[], help="RELEASE=fm4_car_ids.json of an older release")
    a = ap.parse_args(argv)
    data = {}
    if os.path.exists(a.table):
        with open(a.table, encoding="utf-8") as f:
            data = json.load(f)
    if a.cmd == "check":
        problems = check(data)
        for p in problems:
            print(p)
        print("%d cars, %d problem(s)" % (len(data.get("cars", [])), len(problems)))
        return 1 if problems else 0
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fm4_cars  # the importer's readers of the games' files
    fh1 = fm4_cars.FH1(a.fh1, a.update, a.dlc)
    fh1db = merged_official(fh1)
    sources = []
    for name, folder in (("FH2", a.fh2), ("FF", a.ff)):
        if folder:
            path = fm4_cars.find_ci(fm4_cars.find_ci(folder, "media") or folder, "db", "gamedb.slt")
            sources.append((name, sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)))
    fm4 = fm4_cars.FM4(a.fm4, a.fm4_content, a.fm4_dlc) if a.fm4 else None
    if fm4:
        sources.append(("FM4", fm4.db()))
    data = build(data, fh1db, sources)
    old = {}
    for spec in a.old_ids:
        release, _, path = spec.partition("=")
        with open(path, encoding="utf-8") as f:
            old[release] = json.load(f)
    data = renumbered(data, fh1db, dict(sources).get("FM4"), old)
    data = dict(about=__doc__.split("\n\n")[0].strip(), **{k: v for k, v in data.items() if k != "about"})
    problems = check(data)
    if problems:
        raise TableError("; ".join(problems[:5]))
    with open(a.table, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print("%d cars, %s, %d renumbered from older releases"
          % (len(data["cars"]), ", ".join("%d FM4 %ss" % (len(v), k) for k, v in data.get("parts", {}).get("FM4", {}).items()),
             len(data.get("renumbered", []))))
    fh1.close()
    if fm4:
        fm4.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
