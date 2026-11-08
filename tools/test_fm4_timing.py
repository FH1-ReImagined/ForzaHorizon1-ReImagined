"""tools/fm4_timing.py (#648): an FM4 circuit's lap line, sectors and length, from a synthetic
Disc 1 folder (an AI line and a game database; no game files)."""
import os
import sqlite3
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_timing  # noqa: E402


def write_geo(path, points):
    """An FM4 AI line (MLPData) with the waypoints (x, y, z), as tools/fm4_spawn.py reads it."""
    n = len(points)
    head = "MLPDataStart\n" + "".join(f"fWaypoint{a}:float:{n}:1:{{{a}:>8}}\n" for a in "XYZ") + "MLPDataEnd\n"
    size = len(head.format(X=0, Y=0, Z=0).encode())
    offs = {a: size + k * 4 * n for k, a in enumerate("XYZ")}
    data = head.format(**offs).encode()
    data += b"".join(struct.pack(f">{n}f", *[p[k] for p in points]) for k in range(3))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class Timing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.disc = os.path.join(self.tmp.name, "Disc 1")
        # a 400 m square loop at height 50 (FM4's Z), 20 points
        side = [(x, 0) for x in range(0, 100, 20)] + [(100, y) for y in range(0, 100, 20)] + \
               [(x, 100) for x in range(100, 0, -20)] + [(0, y) for y in range(100, 0, -20)]
        write_geo(os.path.join(self.disc, "Media", "AI", "Tracks", "ring_00.geo"),
                  [(x, y, 50.0) for x, y in side])
        db = os.path.join(self.disc, "Media", "db")
        os.makedirs(db)
        con = sqlite3.connect(os.path.join(db, "gamedb.slt"))
        con.execute("CREATE TABLE Tracks (id INTEGER, MediaName TEXT, RibbonIndex INTEGER, Length REAL, IsReverse INTEGER)")
        con.execute("CREATE TABLE SplitPoints (ID INTEGER, TrackId INTEGER, Split REAL)")
        con.execute("INSERT INTO Tracks VALUES (7, 'Ring', 0, 401, 0)")
        con.executemany("INSERT INTO SplitPoints VALUES (?, 7, ?)", [(1, 0.5), (2, 0.25), (3, 1.0)])
        con.commit()
        con.close()

    def test_lap_file(self):
        text = fm4_timing.build(self.disc, "Ring", 0, "The Ring")
        rows = text.splitlines()
        self.assertEqual(rows[:4], ["name The Ring", "length 401.0", "splits 0.2500 0.5000", "points 20"])
        # FM4's (x, y, z) is FH1's (x, z + 1, y), as the spawn
        self.assertEqual(rows[4], "0.00 51.00 0.00")
        self.assertEqual(rows[9], "100.00 51.00 0.00")
        self.assertEqual(len(rows), 24)

    def test_without_its_row_or_line(self):
        os.remove(os.path.join(self.disc, "Media", "db", "gamedb.slt"))
        rows = fm4_timing.build(self.disc, "ring", 0).splitlines()
        self.assertEqual(rows[1], "length 400.0")         # the line's own length
        self.assertEqual(rows[2], "splits 0.3333 0.6667")  # thirds
        self.assertIsNone(fm4_timing.build(self.disc, "Other", 0))

    def test_another_layout_falls_back_to_the_first_line(self):
        self.assertEqual(fm4_timing.geo_path(self.disc, "Ring", 3)[-11:], "ring_00.geo")


if __name__ == "__main__":
    unittest.main()
