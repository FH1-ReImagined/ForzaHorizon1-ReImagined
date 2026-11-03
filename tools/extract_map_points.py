#!/usr/bin/env python3
"""Positions and names for the website's leaderboard map (#316), from the player's own game files.

Writes only coordinates and the in-game names of the speed traps, speed zones, events (Rivals)
and a few landmarks (the festival, the outposts, the dam, the airport, towns and regions):

    {"points": [{"id": "speed-trap-230", "kind": "speed-trap", "name": "Rt. 29 Speed Trap",
                 "x": 161, "z": -3351}, ...]}

x and z are the game's world axes in metres: x grows to the east, z to the north (the "East
Rt. 27" trap lies east of the "West Rt. 27" one, "South Beaumont" south of Beaumont). Board
ids are the ones server/forza_live/competition/leaderboards.py uses (speed-trap-<scoreboard_id>,
speed-zone-<scoreboard_id>, event-<Events.Id>).

With --names it also writes the leaderboard names file the server reads from its data folder
(DATA_DIR/leaderboard_names.json): {"boards": {board id: name}, "cars": {car id: name}}.

Where everything comes from (all read-only):
  media/tracks/colorado/Ribbon_00/GameObjs.xml  world objects by GameplayID: speed_camera_NN_left
      and _right (a trap is the middle of its pair), average_speed_NN_left1/right1 (a zone's
      start), an event's activation (its HorizonEventID), OUTPOST_NNN_NODE, festival_NN,
      airport
  media/tracks/colorado/Ribbon_00/TrackRouteNNN.xml  start_location_00 of a route: the start
      of an event without an activation object (street races); the Colorado tracks from 1100 up
      are named places (towns; track 1000 + n is the starting spot of area n)
  media/tracks/colorado/Ribbon_00/ParticleEmitters.xml  the dam (the emitter at its foot)
  media/gamemodes.zip Colorado/speed_camera.xml, average_speed.xml  board ids
      (scoreboard_id); gas_stations.xml  the outposts and their names
  media/db/gamedb.slt (the title update's when given)  Events, Races, Tracks, Data_Car
  media/stringtables/EN.zip  List_SpeedCameras, Events, Tracks, Environments, Activities,
      Data_Car, List_CarMake (LSB2 string tables; the title update's when it has one)
  DLC packages (--dlc, the installed or extracted content folder): the cars of each car pack
      (media/db/patch/*_merge.slt in its <offer>_pri_<n>.zip, Data_Car rows) and its string
      tables (StringTables_pri_<n>.zip EN/*.str, mounted over the disc's: the highest
      priority wins), for the car names

Usage:
  tools/extract_map_points.py --game extracted/game [--update extracted/tu4] \\
      [--dlc DLC_CONTENT_FOLDER ...] --out ../website/data/map_points.json [--names leaderboard_names.json]
"""
import argparse
import json
import os
import re
import sqlite3
import struct
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
LZX_WINDOW = 1 << 17
RIBBON = os.path.join("tracks", "colorado", "Ribbon_00")



# ---- archives -------------------------------------------------------------------------------

def read_member(path, name):
    """Bytes of a member of one of the game's zips (stored, deflate or method 21, XMemCompress
    LZX, decoded with tools/lzx_delta.py); None when it is not there. Names match without case."""
    with zipfile.ZipFile(path) as zf:
        info = next((i for i in zf.infolist() if i.filename.lower() == name.lower()), None)
        if info is None:
            return None
        if info.compress_type != 21:
            return zf.read(info)
        with open(path, "rb") as f:
            f.seek(info.header_offset)
            h = f.read(30)
            start = info.header_offset
            if h[:4] == b"PK\x03\x04":
                n, m = struct.unpack("<HH", h[26:30])
                start += 30 + n + m
            f.seek(start)
            raw = f.read(info.compress_size)
    sys.path.insert(0, HERE)
    import lzx_delta
    # Blocks: FF, u16 uncompressed size, u16 compressed size, data; or u16 compressed size,
    # data (a full 32 KB block). The data of all blocks is one LZX stream.
    stream, pos = bytearray(), 0
    while pos < len(raw):
        if raw[pos] == 0xFF:
            comp = (raw[pos + 3] << 8) | raw[pos + 4]
            pos += 5
        else:
            comp = (raw[pos] << 8) | raw[pos + 1]
            pos += 2
        if comp == 0 or pos + comp > len(raw):
            break
        stream += raw[pos:pos + comp]
        pos += comp
    return bytes(lzx_delta.decompress(bytes(stream), info.file_size, LZX_WINDOW))


class Game:
    """The base game's media folder, and the title update's media.zip over it when given."""

    def __init__(self, game_root, update_root=None, dlc_roots=()):
        self.media = os.path.join(game_root, "media")
        if not os.path.isdir(self.media):
            raise SystemExit("no media folder in %s" % game_root)
        self.update = None
        if update_root:
            z = os.path.join(update_root, "media.zip")
            self.update = z if os.path.isfile(z) else None
        self.dlc_zips = dlc_zips(dlc_roots)

    def file(self, *parts):
        with open(os.path.join(self.media, *parts), "rb") as f:
            return f.read()

    def member(self, archive, name, update_name=None):
        """A file from media/<archive>, or the title update's copy (update_name) when it has one."""
        if self.update and update_name:
            data = read_member(self.update, update_name)
            if data is not None:
                return data
        return read_member(os.path.join(self.media, archive), name)

    def strings(self, table):
        """The table as the game sees it: the disc's (or the title update's), with the DLC
        packages' copies over it in the order of their priority."""
        data = self.member(os.path.join("stringtables", "EN.zip"), table + ".str",
                           "stringtables/en/%s.str" % table.lower())
        merged = StringTable(data) if data else StringTable(None)
        for _, path in self.dlc_zips:
            if re.match(r"(?i)stringtables_pri_\d+\.zip$", os.path.basename(path)):
                extra = read_member(path, "EN/%s.str" % table)
                if extra:
                    merged.update(StringTable(extra))
        return merged

    def dlc_cars(self):
        """Data_Car rows (Id, Year, DisplayName, MakeID) of the DLC packages' database patches."""
        rows = {}
        for _, path in self.dlc_zips:
            with zipfile.ZipFile(path) as zf:
                patches = [i.filename for i in zf.infolist() if re.search(r"(?i)/db/patch/[^/]*\.slt$", i.filename)]
            for name in patches:
                data = read_member(path, name)
                tmp = tempfile.NamedTemporaryFile(suffix=".slt", delete=False)
                try:
                    tmp.write(data)
                    tmp.close()
                    db = sqlite3.connect("file:%s?mode=ro" % tmp.name, uri=True)
                    try:
                        for row in db.execute("select Id, Year, DisplayName, MakeID from Data_Car"):
                            rows[row[0]] = row
                    except sqlite3.Error:
                        pass
                    finally:
                        db.close()
                finally:
                    os.unlink(tmp.name)
        return rows

    def database(self):
        """A sqlite connection to gamedb.slt (the title update's when it has one)."""
        data = read_member(self.update, "db/gamedb.slt") if self.update else None
        if data is None:
            path = os.path.join(self.media, "db", "gamedb.slt")
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".slt", delete=False)
            tmp.write(data)
            tmp.close()
            path = tmp.name
            self._tmp = path
        return sqlite3.connect("file:%s?mode=ro" % path, uri=True)

    def close(self):
        if getattr(self, "_tmp", None):
            os.unlink(self._tmp)
            self._tmp = None


# ---- LSB2 string tables ---------------------------------------------------------------------

class StringTable:
    """An LSB2 string table: "LSB2", two version words, the header size, the number of sections
    and their offsets (big-endian). Section 1: size, count, count + 1 entries (u16 key, u32
    offset in UTF-16 units; the last one ends the list), the UTF-16BE text. Section 2: size,
    count, count entries (u16 key, u16 index, u32 byte offset), the ASCII ids (IDS_...).
    The game database names a string "_&N": N's low 16 bits are its key in the table."""

    def __init__(self, data):
        self.text, self.ids = {}, {}
        if not data:
            return
        if data[:4] != b"LSB2":
            raise ValueError("not an LSB2 string table")
        nsec = struct.unpack(">I", data[16:20])[0]
        offs = struct.unpack(">%dI" % nsec, data[20:20 + 4 * nsec])
        s1 = offs[0]
        _, count = struct.unpack(">II", data[s1:s1 + 8])
        pool = s1 + 8 + 6 * (count + 1)
        for i in range(count):
            key, off = struct.unpack(">HI", data[s1 + 8 + 6 * i:s1 + 14 + 6 * i])
            p = pool + 2 * off
            e = p
            while e + 1 < len(data) and data[e:e + 2] != b"\0\0":
                e += 2
            self.text[key] = data[p:e].decode("utf-16-be", "replace")
        if nsec > 1 and offs[1]:
            s2 = offs[1]
            _, count2 = struct.unpack(">II", data[s2:s2 + 8])
            pool2 = s2 + 8 + 8 * count2
            for i in range(count2):
                key, _, off = struct.unpack(">HHI", data[s2 + 8 + 8 * i:s2 + 16 + 8 * i])
                e = data.find(b"\0", pool2 + off)
                self.ids[data[pool2 + off:e].decode("latin-1")] = key

    def update(self, other):
        """Another copy of the table over this one (a DLC package's)."""
        self.text.update(other.text)
        self.ids.update(other.ids)

    def ref(self, ref):
        """The text of a database reference "_&N" (None when not here)."""
        if not isinstance(ref, str) or not ref.startswith("_&") or not ref[2:].isdigit():
            return None
        return self.text.get(int(ref[2:]) & 0xFFFF) or None

    def by_id(self, ident):
        key = self.ids.get(ident)
        return self.text.get(key) or None if key is not None else None


# ---- world positions ------------------------------------------------------------------------

def game_objects(data):
    """GameplayID -> (x, z) of GameObjs.xml (the first one of each id)."""
    out = {}
    for obj in ET.fromstring(data):
        gid, pos = obj.get("GameplayID"), obj.find("Pos")
        if gid and pos is not None and gid not in out:
            x, z = float(pos.get("x")), float(pos.get("z"))
            if x or z:
                out[gid] = (x, z)
    return out


def route_start(game, route_id):
    """start_location_00 (or the first start location) of TrackRoute<route_id>.xml."""
    try:
        root = ET.fromstring(game.file(RIBBON, "TrackRoute%03d.xml" % route_id))
    except (OSError, ET.ParseError):
        return None
    best = None
    for nt in root.iter("NamedTransform"):
        name = nt.get("name") or ""
        if name.startswith("start_location_"):
            t = nt.find("Transform")
            pos = (float(t.get("pos.x")), float(t.get("pos.z")))
            if name == "start_location_00":
                return pos
            best = best or pos
    return best


def dam_position(game, objs):
    """The emitter at the dam's foot (its effect is named for the dam). The emitters' file
    stores z the other way round from GameObjs; the sign comes from the emitters that share a
    GameplayID with an object (the speed camera flashes)."""
    try:
        root = ET.fromstring(game.file(RIBBON, "ParticleEmitters.xml"))
    except (OSError, ET.ParseError):
        return None
    flips, dam = [], None
    for em in root:
        p = em.find("Position")
        if p is None:
            continue
        x, z = float(p.get("v.x")), float(p.get("v.z"))
        gid = em.find("GameplayID")
        if gid is not None and gid.get("value") in objs:
            ox, oz = objs[gid.get("value")]
            if abs(abs(oz) - abs(z)) < 50 and abs(ox - x) < 50 and abs(oz) > 50:
                flips.append(1 if (oz > 0) == (z > 0) else -1)
        eff = em.find("Effect")
        if dam is None and eff is not None and re.search(r"(?i)_dam_", eff.get("value") or ""):
            dam = (x, z)
    if dam is None:
        return None
    sign = -1 if flips and sum(flips) < 0 else 1
    return dam[0], dam[1] * sign


def mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def title(text):
    """'SOUTH BEAUMONT' -> 'South Beaumont' (the game shows these in capitals)."""
    if text != text.upper():
        return text
    return " ".join(w[:1] + w[1:].lower() if w.isalpha() else w for w in text.split())


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---- the extraction -------------------------------------------------------------------------

def activities(game, name):
    data = game.member("gamemodes.zip", "Colorado/" + name)
    return ET.fromstring(data) if data else None


def extract(game):
    """(points, board names, car names)."""
    objs = game_objects(game.file(RIBBON, "GameObjs.xml"))
    cams = game.strings("List_SpeedCameras")
    points, boards, cars = [], {}, {}

    def add(pid, kind, name, pos):
        if pos and name:
            points.append({"id": pid, "kind": kind, "name": name,
                           "x": int(round(pos[0])), "z": int(round(pos[1]))})

    # Speed traps and zones: the activity's name gives the objects, scoreboard_id the board.
    for fname, kind, ends in (("speed_camera.xml", "speed-trap", ("_left", "_right")),
                              ("average_speed.xml", "speed-zone", ("_left1", "_right1"))):
        root = activities(game, fname)
        for act in root.iter("Activity") if root is not None else ():
            board = act.get("scoreboard_id")
            if not board or not board.isdigit():
                continue
            name = cams.by_id("IDS_SpeedCamera_%s" % board)
            a, b = (objs.get(act.get("name") + e) for e in ends)
            pos = mid(a, b) if a and b else a or b
            bid = "%s-%s" % (kind, board)
            if name:
                boards[bid] = name
            add(bid, kind, name, pos)
    db = game.database()
    try:
        ev_names, tr_names = game.strings("Events"), game.strings("Tracks")
        routes = {}
        for tid, dn, rid, media in db.execute("select id, DisplayName, RouteId, MediaName from Tracks"):
            routes[tid] = (rid, media, tr_names.ref(dn))
        # Events: the board is Events.Id; the pin is the event's activation in the world, or
        # (street races) the start of its route.
        for eid, hid, name in db.execute("select Id, HorizonEventID, Name from Events order by Id"):
            text = ev_names.ref(name)
            if not text or hid == "FREE_ROAM":
                continue
            boards["event-%d" % eid] = text
            race = db.execute("select TrackId from Races where EventId = ? order by RaceNumber",
                              (eid,)).fetchone()
            rid, media, _ = routes.get(race[0], (None, None, None)) if race else (None, None, None)
            if media != "Colorado":
                continue
            pos = objs.get(hid) or (route_start(game, rid) if rid is not None else None)
            add("event-%d" % eid, "event", text, pos)
        # Named places: the Colorado tracks from 1100 up are single spots (no route), track
        # 1000 + n the starting spot of area (Environments) n.
        places, starts = {}, {}
        for tid, (rid, media, text) in sorted(routes.items()):
            if media == "Colorado" and tid >= 1100 and text:
                pos = route_start(game, rid)
                if pos:
                    places[text] = pos
                    starts[tid - 1000] = pos
        # Cars: "<year> <make> <model>", for the names file: the database's and the DLC
        # packages' (a car the database has without a name in the disc's strings, the Rally
        # cars of the title update, has it in its package's strings).
        makes = game.strings("List_CarMake")
        car_strings = game.strings("Data_Car")
        make_names = {mid: makes.ref(dn) for mid, dn in db.execute("select ID, DisplayName from List_CarMake")}
        rows = {r[0]: r for r in db.execute("select Id, Year, DisplayName, MakeID from Data_Car")}
        rows.update(game.dlc_cars())
        for cid, year, dn, make in rows.values():
            model = car_strings.ref(dn) or car_strings.by_id("IDS_DisplayName_%d" % cid)
            if model:
                maker = make_names.get(make) or makes.by_id("IDS_DisplayName_%s" % make)
                cars[str(cid)] = " ".join(str(p) for p in (year, maker, model) if p)
        envs = game.strings("Environments")
        areas = {}  # area id -> (its kind, e.g. "Mountains", its name, e.g. "The Rockies")
        for eid, dn in db.execute("select Id, DisplayName from Environments where MediaName = 'Colorado'"):
            name = envs.ref(dn) or envs.by_id("IDS_DisplayName_%d" % eid)
            if name:
                areas[eid] = (envs.by_id("IDS_Location_%d" % eid), name)
    finally:
        db.close()

    # Landmarks.
    fest = [objs[k] for k in sorted(objs) if re.match(r"festival_\d+$", k)]
    if fest:
        add("festival", "festival", next((n for k, n in areas.values() if k == "Festival"), None),
            (sum(p[0] for p in fest) / len(fest), sum(p[1] for p in fest) / len(fest)))
    acts = game.strings("Activities")
    root = activities(game, "gas_stations.xml")
    for act in root.iter("Activity") if root is not None else ():
        text = ET.tostring(act, encoding="unicode")
        node = re.search(r'object_name="(OUTPOST_(\d+)_NODE)"', text)
        label = re.search(r'mapDescription="(IDS_GasStationName_\d+)"', text)
        if not node or not label:
            continue
        name, pos = acts.by_id(label.group(1)), objs.get(node.group(1))
        add("outpost-%d" % int(node.group(2)), "outpost", name and title(name), pos)
    dam_name = next((n for n in places if n.endswith(" Dam")), None)
    add("dam", "dam", dam_name, dam_position(game, objs) or places.get(dam_name))
    airport = acts.by_id("IDS_Description_Airport")
    add("airport", "airfield", airport and title(airport), objs.get("airport"))
    for name, pos in places.items():
        if name in ("Festival", "Media Center") or name == dam_name:
            continue
        add("town-" + slug(name), "town", name, pos)
    # Regions: the areas that are not a town of the same name, at their starting spot.
    towns = {p["name"] for p in points if p["kind"] == "town"}
    for eid, (kind, name) in sorted(areas.items()):
        if kind != "Festival" and name not in towns:
            add("region-" + slug(name), "region", name, starts.get(eid))
    order = {k: i for i, k in enumerate(("speed-trap", "speed-zone", "event", "festival", "outpost",
                                          "dam", "airfield", "town", "region"))}
    points.sort(key=lambda p: (order.get(p["kind"], 99), natural(p["id"])))
    return points, boards, cars


def dlc_zips(roots):
    """[(priority, path)] of the DLC packages' zips under these folders (each package's
    Media/DLCZips/*_pri_<n>.zip), lowest priority first."""
    out = []
    for root in roots:
        for folder, _, files in os.walk(root):
            if os.path.basename(folder).lower() != "dlczips":
                continue
            for name in files:
                m = re.search(r"(?i)_pri_(\d+)\.zip$", name)
                if m:
                    out.append((int(m.group(1)), os.path.join(folder, name)))
    out.sort()
    return out


def natural(text):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", text)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", required=True, help="the extracted game (the folder with media/)")
    ap.add_argument("--update", help="the title update's folder (with media.zip), e.g. extracted/tu4")
    ap.add_argument("--dlc", action="append", default=[],
                    help="a folder with DLC packages (installed or extracted content), for their cars' names")
    ap.add_argument("--out", required=True, help="the points JSON (the website's data/map_points.json)")
    ap.add_argument("--names", help="also write the server's leaderboard_names.json here")
    a = ap.parse_args(argv)
    game = Game(a.game, a.update, a.dlc)
    try:
        points, boards, cars = extract(game)
    finally:
        game.close()
    doc = {"about": "Positions (metres; x east, z north) and in-game names of the leaderboard "
                    "map's points, from tools/extract_map_points.py.",
           "points": points}
    with open(a.out, "w") as f:
        f.write(dump_points(doc))
    kinds = {}
    for p in points:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print("%s: %s" % (a.out, ", ".join("%d %s" % (n, k) for k, n in kinds.items())))
    if a.names:
        with open(a.names, "w") as f:
            json.dump({"boards": dict(sorted(boards.items(), key=lambda kv: natural(kv[0]))),
                       "cars": dict(sorted(cars.items(), key=lambda kv: int(kv[0])))},
                      f, indent=1, ensure_ascii=False)
            f.write("\n")
        print("%s: %d boards, %d cars" % (a.names, len(boards), len(cars)))


def dump_points(doc):
    """One point per line: small diffs when the extraction changes."""
    lines = ['{"about": %s,' % json.dumps(doc["about"]), ' "points": [']
    lines += ["  %s%s" % (json.dumps(p, ensure_ascii=False), "," if i < len(doc["points"]) - 1 else "")
              for i, p in enumerate(doc["points"])]
    lines += [" ]}", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
