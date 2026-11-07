#!/usr/bin/env python3
"""Lap timing data for a converted FM4 circuit (#648): the start/finish line, the sectors and
the lap's way round, for the game's lap timer (forza-recomp/src/hooks/fm4_lap_timer.cpp).

Where FM4 keeps them:
  - the lap's way round: FM4's AI line, media/AI/Tracks/<track>_NN.geo (tools/fm4_spawn.py),
    whose waypoint 0 is on the start/finish line (the grid is behind it, the waypoints run
    round the lap from it);
  - the sectors: the game database's SplitPoints table (Split, the share of the lap where a
    sector ends: Suzuka 0.25, 0.5, 0.75), by the Tracks row of the layout;
  - the lap's length: that Tracks row's Length (metres; Suzuka's full circuit 5809).
The AI line's .seg file next to the .geo holds the corners (start, apex and end waypoints,
curvature, type), not the sectors; the ribbon's tracksegcams.xml the replay cameras in their
order round the lap. Neither is needed for timing.

Output (lap.txt, plain text the game reads):
  name <display name>
  length <metres, the database's>
  splits <share> <share> ...
  points <n>
  x y z                       n lines, FH1's world coordinates (as FORZA_TRACK_SPAWN)

Usage: tools/fm4_timing.py FM4_DISC1 TRACK_FOLDER RIBBON OUT.txt [NAME]
"""
import math
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_spawn  # noqa: E402


def _find_ci(root, *parts):
    p = root
    for part in parts:
        try:
            p = next((os.path.join(p, c) for c in os.listdir(p) if c.lower() == part.lower()), None)
        except OSError:
            return None
        if p is None:
            return None
    return p


def geo_path(disc, folder, ribbon):
    """FM4's AI line of the layout (<track>_NN.geo, any case), else the first of the track's."""
    ai = _find_ci(disc, "Media", "AI", "Tracks")
    if not ai:
        return None
    geos = {n.lower(): os.path.join(ai, n) for n in os.listdir(ai) if n.lower().endswith(".geo")}
    want = f"{folder.lower()}_{ribbon:02d}.geo"
    if want in geos:
        return geos[want]
    same = sorted(n for n in geos if re.fullmatch(re.escape(folder.lower()) + r"_\d\d\.geo", n))
    return geos[same[0]] if same else None


def track_row(disc, folder, ribbon):
    """(Length, [split shares]) of the layout's Tracks row in FM4's database, or (None, [])."""
    db = _find_ci(disc, "Media", "db", "gamedb.slt")
    if not db:
        return None, []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT id, Length FROM Tracks WHERE MediaName LIKE ? AND RibbonIndex=? "
                          "ORDER BY IsReverse, id LIMIT 1", (folder, ribbon)).fetchone()
        if not row:
            return None, []
        splits = [r[0] for r in con.execute("SELECT Split FROM SplitPoints WHERE TrackId=? ORDER BY Split",
                                            (row[0],))]
        return row[1], [s for s in splits if 0 < s < 1]
    finally:
        con.close()


def line_length(points):
    return sum(math.dist(points[i], points[(i + 1) % len(points)]) for i in range(len(points)))


def build(disc, folder, ribbon, name=None):
    """The lap.txt text, or None when the circuit has no AI line."""
    geo = geo_path(disc, folder, ribbon)
    if not geo:
        return None
    points = fm4_spawn.waypoints(geo)
    length, splits = track_row(disc, folder, ribbon)
    if not length:
        length = line_length(points)
    if not splits:
        splits = [1 / 3, 2 / 3]
    lines = [f"name {name or folder}", f"length {float(length):.1f}",
             "splits " + " ".join(f"{s:.4f}" for s in splits), f"points {len(points)}"]
    lines += [f"{x:.2f} {y:.2f} {z:.2f}" for x, y, z in points]
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) < 5:
        print(__doc__)
        return 2
    text = build(argv[1], argv[2], int(argv[3]), argv[5] if len(argv) > 5 else None)
    if text is None:
        print(f"{argv[2]}: no AI line on the disc")
        return 1
    with open(argv[4], "w", encoding="utf-8") as f:
        f.write(text)
    head = text.split("\n", 4)
    print(f"{argv[4]}: {head[1]}, {head[2]}, {head[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
