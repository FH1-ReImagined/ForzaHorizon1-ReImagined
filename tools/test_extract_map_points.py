"""tools/extract_map_points.py on a small synthetic game folder (no game data in the repo):
python3 -m unittest discover -s tools -p 'test_extract_map_points.py'"""
import contextlib
import io
import json
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "server"))

import extract_map_points as emp  # noqa: E402


def lsb2(entries):
    """An LSB2 string table of (key, IDS_ name, text)."""
    pool, offs = b"", []
    for _, _, text in entries:
        offs.append(len(pool) // 2)
        pool += text.encode("utf-16-be") + b"\0\0"
    sec1 = struct.pack(">II", 0, len(entries))
    sec1 += b"".join(struct.pack(">HI", e[0], o) for e, o in zip(entries, offs))
    sec1 += struct.pack(">HI", 0xFFFF, len(pool) // 2) + pool
    ids, ioffs = b"", []
    for _, ident, _ in entries:
        ioffs.append(len(ids))
        ids += ident.encode() + b"\0"
    sec2 = struct.pack(">II", 0, len(entries))
    sec2 += b"".join(struct.pack(">HHI", e[0], i, o) for i, (e, o) in enumerate(zip(entries, ioffs))) + ids
    s1 = 36
    s2 = s1 + len(sec1)
    head = b"LSB2" + struct.pack(">4I", 0x01000000, 0x02000000, 36, 4) + struct.pack(">4I", s1, s2, 0, s2 + len(sec2))
    return head + sec1 + sec2


def obj(gid, x, z, y=10.0):
    return ('<Obj GameplayID="%s"><Pos x="%f" y="%f" z="%f"/><Orientation/></Obj>' % (gid, x, y, z))


def route(*starts):
    return "<TrackRoute><NamedTransforms>%s</NamedTransforms></TrackRoute>" % "".join(
        "<NamedTransform name='start_location_%02d'><Transform pos.x='%f' pos.y='1' pos.z='%f'/></NamedTransform>"
        % (i, x, z) for i, (x, z) in enumerate(starts))


def make_game(root):
    media = os.path.join(root, "media")
    ribbon = os.path.join(media, "tracks", "colorado", "Ribbon_00")
    os.makedirs(ribbon)
    os.makedirs(os.path.join(media, "db"))
    os.makedirs(os.path.join(media, "stringtables"))
    with open(os.path.join(ribbon, "GameObjs.xml"), "w") as f:
        f.write("<GameObjs>%s</GameObjs>" % "".join([
            obj("speed_camera_01_left", 100, 200), obj("speed_camera_01_right", 110, 220),
            obj("average_speed_04_left1", -500, 40), obj("average_speed_04_right1", -520, 60),
            obj("average_speed_04_left2", -900, 400), obj("FR07", 1500, -2500),
            obj("OUTPOST_001_NODE", 3000, 2500), obj("festival_01", -900, -200), obj("festival_02", -1000, -300),
            obj("airport", 5000, -2000), obj("unused", 0, 0)]))
    with open(os.path.join(ribbon, "TrackRoute012.xml"), "w") as f:
        f.write(route((-3000, -1400), (-3004, -1404)))
    with open(os.path.join(ribbon, "TrackRoute028.xml"), "w") as f:
        f.write(route((-4000, 700)))
    with open(os.path.join(ribbon, "TrackRoute021.xml"), "w") as f:
        f.write(route((-950, -120)))
    with open(os.path.join(ribbon, "TrackRoute029.xml"), "w") as f:
        f.write(route((-3400, -1350)))
    with open(os.path.join(ribbon, "ParticleEmitters.xml"), "w") as f:
        # The emitters' z is the other way round from GameObjs.
        f.write('<ParticleEmitters><SimpleEmitter><GameplayID value="speed_camera_01_left"/>'
                '<Effect value="AMB_Camera_Flash"/><Position v.x="100.5" v.y="10" v.z="-200.2"/></SimpleEmitter>'
                '<SimpleEmitter><Effect value="AMB_Dam_Rising_Mist_Base"/><Position v.x="6690" v.y="160" v.z="337"/>'
                '</SimpleEmitter></ParticleEmitters>')
    with zipfile.ZipFile(os.path.join(media, "gamemodes.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Colorado/speed_camera.xml", '<ActivityManager><Activity type="ActivitySpeedCamera" '
                   'name="speed_camera_01" scoreboard_id="201"/></ActivityManager>')
        z.writestr("Colorado/average_speed.xml", '<ActivityManager><Activity type="ActivityAverageSpeed" '
                   'name="average_speed_04" scoreboard_id="104"/></ActivityManager>')
        z.writestr("Colorado/gas_stations.xml", '<ActivityManager><Activity type="ActivityGasStation" name="GasStation_01">'
                   '<StateGroup><State><Behaviour id="CPlaceCarAtObject" object_name="OUTPOST_001_NODE"/></State></StateGroup>'
                   '<TriggerZone object="GASSTATION_001" mapDescription="IDS_GasStationName_01"/></Activity></ActivityManager>')
    with zipfile.ZipFile(os.path.join(media, "stringtables", "EN.zip"), "w") as z:
        z.writestr("List_SpeedCameras.str", lsb2([(0x1001, "IDS_SpeedCamera_201", "Synthetic Speed Trap"),
                                                  (0x1002, "IDS_SpeedCamera_104", "Synthetic Speed Zone")]))
        z.writestr("Events.str", lsb2([(0x2b8b, "IDS_Name_4", "Synthetic Blitz"), (0x2b8c, "IDS_Name_38", "Synthetic Street Run"),
                                       (0x2b8d, "IDS_Name_33", "Free Roam"), (0x2b8e, "IDS_Name_247", "Somewhere Else")]))
        z.writestr("Tracks.str", lsb2([(0x10, "IDS_DisplayName_1101", "Festival"), (0x11, "IDS_DisplayName_1108", "Carson"),
                                       (0x12, "IDS_DisplayName_1109", "Media Center"), (0x13, "IDS_DisplayName_96", "A Route")]))
        z.writestr("Environments.str", lsb2([(0x20, "IDS_DisplayName_101", "Festival"), (0x21, "IDS_Location_101", "Festival"),
                                             (0x22, "IDS_DisplayName_108", "Carson"), (0x23, "IDS_Location_108", "Main Town"),
                                             (0x24, "IDS_DisplayName_109", "Montano Plains"), (0x25, "IDS_Location_109", "Plains")]))
        z.writestr("Activities.str", lsb2([(0x30, "IDS_GasStationName_01", "SOUTH BEAUMONT"),
                                           (0x31, "IDS_Description_Airport", "AIRPORT")]))
        z.writestr("Data_Car.str", lsb2([(0x40, "IDS_DisplayName_1398", "Synthetic GT")]))
        z.writestr("List_CarMake.str", lsb2([(0x50, "IDS_DisplayName_21", "Maker")]))
    db = sqlite3.connect(os.path.join(media, "db", "gamedb.slt"))
    db.executescript("""
        create table Events (Id, Name, HorizonEventID);
        create table Races (Id, EventId, RaceNumber, TrackId);
        create table Tracks (id, DisplayName, RouteId, MediaName);
        create table Data_Car (Id, Year, DisplayName, MakeID);
        create table List_CarMake (ID, DisplayName);
        create table Environments (Id, DisplayName, MediaName);
    """)
    ref = lambda key: "_&%d" % ((0x0C15 << 16) | key)  # noqa: E731
    db.executemany("insert into Events values (?, ?, ?)", [
        (4, ref(0x2b8b), "FR07"), (38, ref(0x2b8c), "STREET_PLNS_005"), (33, ref(0x2b8d), "FREE_ROAM"),
        (247, ref(0x2b8e), "RALLY_1A")])
    db.executemany("insert into Races values (?, ?, ?, ?)", [(1, 4, 1, 96), (2, 38, 1, 1001), (3, 33, 1, 317), (4, 247, 1, 1124)])
    db.executemany("insert into Tracks values (?, ?, ?, ?)", [
        (96, ref(0x13), 96, "Colorado"), (1001, ref(0x13), 12, "Colorado"), (317, ref(0x13), 0, "Colorado"),
        (1124, ref(0x13), 11, "ColoradoDirt"), (1101, ref(0x10), 21, "Colorado"), (1108, ref(0x11), 28, "Colorado"),
        (1109, ref(0x12), 29, "Colorado")])
    db.execute("insert into Data_Car values (1398, 2012, ?, 21)", (ref(0x40),))
    # A car the database has but the disc's strings do not name (the title update's Rally cars).
    db.execute("insert into Data_Car values (1517, 1992, ?, 21)", (ref(0x41),))
    db.execute("insert into List_CarMake values (21, ?)", (ref(0x50),))
    db.executemany("insert into Environments values (?, ?, 'Colorado')", [(101, ref(0x20)), (108, ref(0x22)), (109, ref(0x24))])
    db.commit()
    db.close()


def make_dlc(root):
    """Two packages in the installed layout: a car pack (its database patch adds car 1600 of
    a make the disc's database lacks) and string tables of two priorities."""
    def package(name, files):
        folder = os.path.join(root, "0000000000000000", "4D5309C9", "00000002", name, "Media", "DLCZips")
        os.makedirs(folder, exist_ok=True)
        for zname, members in files.items():
            with zipfile.ZipFile(os.path.join(folder, zname), "w") as z:
                for mname, data in members.items():
                    z.writestr(mname, data)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "patch.slt")
        db = sqlite3.connect(path)
        db.executescript("create table Data_Car (Id, Year, MakeID, DisplayName, MediaName);")
        db.execute("insert into Data_Car values (1600, 2012, 77, ?, 'GUM_ApolloEnraged_12')", ("_&%d" % (0xC0DE << 16 | 0x60),))
        db.commit()
        db.close()
        with open(path, "rb") as f:
            patch = f.read()
    package("AAAA", {"1100_pri_65.zip": {"media/db/patch/13312000_merge.slt": patch},
                     "StringTables_pri_1250.zip": {"EN/Data_Car.str": lsb2([(0x60, "IDS_DisplayName_1600", "Old Name")])}})
    package("BBBB", {"StringTables_pri_2950.zip": {
        "EN/Data_Car.str": lsb2([(0x60, "IDS_DisplayName_1600", "Apollo Enraged"),
                                 (0x61, "IDS_DisplayName_1517", "Celica GT-Four RC ST185")]),
        "EN/List_CarMake.str": lsb2([(0x70, "IDS_DisplayName_77", "Gumpert")]),
        "br/Data_Car.str": lsb2([(0x60, "IDS_DisplayName_1600", "Outro nome")])}})


class ExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        make_game(os.path.join(cls.root, "game"))
        cls.out = os.path.join(cls.root, "map_points.json")
        cls.names = os.path.join(cls.root, "leaderboard_names.json")
        with contextlib.redirect_stdout(io.StringIO()):
            emp.main(["--game", os.path.join(cls.root, "game"), "--out", cls.out, "--names", cls.names])
        with open(cls.out) as f:
            cls.doc = json.load(f)
        cls.points = {p["id"]: p for p in cls.doc["points"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_string_table(self):
        t = emp.StringTable(lsb2([(0x2b8b, "IDS_Name_4", "Oak é"), (0x0001, "IDS_X", "")]))
        self.assertEqual(t.ref("_&202713995"), "Oak é")        # 202713995 & 0xFFFF == 0x2b8b
        self.assertEqual(t.by_id("IDS_Name_4"), "Oak é")
        self.assertIsNone(t.ref("_&1"))                              # empty text
        self.assertIsNone(t.ref("plain"))
        with self.assertRaises(ValueError):
            emp.StringTable(b"NOPE" + bytes(40))

    def test_only_coordinates_and_names(self):
        for p in self.doc["points"]:
            self.assertEqual(set(p), {"id", "kind", "name", "x", "z"})
            self.assertIsInstance(p["x"], int)
            self.assertIsInstance(p["z"], int)

    def test_speed_trap_is_the_middle_of_its_pair(self):
        self.assertEqual(self.points["speed-trap-201"],
                         {"id": "speed-trap-201", "kind": "speed-trap", "name": "Synthetic Speed Trap", "x": 105, "z": 210})

    def test_speed_zone_is_its_start(self):
        p = self.points["speed-zone-104"]
        self.assertEqual((p["name"], p["x"], p["z"]), ("Synthetic Speed Zone", -510, 50))

    def test_events(self):
        self.assertEqual((self.points["event-4"]["x"], self.points["event-4"]["z"]), (1500, -2500))  # activation
        street = self.points["event-38"]                                    # no activation: route start
        self.assertEqual((street["name"], street["x"], street["z"]), ("Synthetic Street Run", -3000, -1400))
        self.assertNotIn("event-33", self.points)    # free roam
        self.assertNotIn("event-247", self.points)   # not on the Colorado map

    def test_landmarks(self):
        self.assertEqual(self.points["outpost-1"]["name"], "South Beaumont")
        self.assertEqual((self.points["festival"]["x"], self.points["festival"]["z"]), (-950, -250))
        self.assertEqual(self.points["airport"]["name"], "Airport")
        self.assertEqual(self.points["town-carson"]["x"], -4000)
        self.assertNotIn("town-media-center", self.points)
        self.assertEqual(self.points["region-montano-plains"]["x"], -3400)   # area 109: track 1109's spot
        self.assertNotIn("region-carson", self.points)                       # a town already

    def test_dam_takes_the_emitters_z_sign_from_the_objects(self):
        self.assertNotIn("dam", self.points)  # no place named "... Dam" in this game folder
        emitters = os.path.join(self.root, "game")
        game = emp.Game(emitters)
        objs = emp.game_objects(game.file(emp.RIBBON, "GameObjs.xml"))
        self.assertEqual(emp.dam_position(game, objs), (6690.0, -337.0))

    def test_names_of_dlc_cars(self):
        make_dlc(os.path.join(self.root, "dlc"))
        names = os.path.join(self.root, "names_dlc.json")
        with contextlib.redirect_stdout(io.StringIO()):
            emp.main(["--game", os.path.join(self.root, "game"), "--dlc", os.path.join(self.root, "dlc"),
                      "--out", os.path.join(self.root, "points_dlc.json"), "--names", names])
        with open(names) as f:
            cars = json.load(f)["cars"]
        self.assertEqual(cars, {"1398": "2012 Maker Synthetic GT",
                                "1517": "1992 Maker Celica GT-Four RC ST185",   # named by a package
                                "1600": "2012 Gumpert Apollo Enraged"})         # the higher priority wins
        self.assertEqual([os.path.basename(p) for _, p in emp.dlc_zips([os.path.join(self.root, "dlc")])],
                         ["1100_pri_65.zip", "StringTables_pri_1250.zip", "StringTables_pri_2950.zip"])

    def test_names_file(self):
        with open(self.names) as f:
            names = json.load(f)
        self.assertEqual(set(names), {"boards", "cars"})
        self.assertEqual(names["boards"]["speed-trap-201"], "Synthetic Speed Trap")
        self.assertEqual(names["boards"]["speed-zone-104"], "Synthetic Speed Zone")
        self.assertEqual(names["boards"]["event-4"], "Synthetic Blitz")
        self.assertEqual(names["boards"]["event-247"], "Somewhere Else")
        self.assertNotIn("event-33", names["boards"])
        self.assertEqual(names["cars"], {"1398": "2012 Maker Synthetic GT"})

    def test_the_server_reads_the_names_file(self):
        from forza_live.competition import leaderboards as lb
        from forza_live.competition import scores
        old = scores.DATA_DIR
        try:
            scores.DATA_DIR = self.root
            lb.configure()
            self.assertEqual(lb.board_name(scores.SPEED_TRAP, 201, 0), "Synthetic Speed Trap")
            self.assertEqual(lb.board_name(scores.EVENT_TIME, 4, 0), "Synthetic Blitz")
            self.assertEqual(lb.board_name(scores.EVENT_TIME, 5, 0), "Event 5")
            self.assertEqual(lb.names()["cars"]["1398"], "2012 Maker Synthetic GT")
        finally:
            scores.DATA_DIR = old
            lb.configure()


if __name__ == "__main__":
    unittest.main()
