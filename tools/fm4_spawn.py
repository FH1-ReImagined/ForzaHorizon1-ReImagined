#!/usr/bin/env python3
"""Start positions on a converted FM4 circuit, from FM4's own AI line (#648): the grid spawn
for FORZA_TRACK_SPAWN and test spots round the lap.

FM4's media/AI/Tracks/<track>_NN.geo (NN the ribbon) is an "MLPData" file: a text header of
named arrays ("fWaypointX:float:2904:1:   513": name, type, count, offset) then the arrays,
big-endian. The waypoints run round the lap from the start line; in FH1's terms an FM4
waypoint (X, Y, Z) is the point (X, Z + 1, Y): Y is FH1's z, Z the height (1 m added to land
the car on the road). Checked on Suzuka: waypoint 0 and 5 give the facing the grid spawn was
found with by hand (0.61, -0.79), 15 m back from waypoint 0 its place (605.6, 598.5, -134.1).

Usage: tools/fm4_spawn.py TRACK_NN.geo [SPOTS]
  prints the grid spawn (15 m behind the start line) as x,y,z,fx,fz, then with SPOTS
  that many spots spread round the lap (the first the grid spawn), one a line.
"""
import math
import re
import struct
import sys


def waypoints(path):
    d = open(path, "rb").read()
    end = d.find(b"MLPDataEnd")
    if not d.startswith(b"MLPDataStart") or end < 0:
        raise SystemExit(f"{path}: not an MLPData file")
    arrays = {}
    for m in re.finditer(r"(\w+):(\w+):(\d+):\d+:\s+(\d+)", d[:end].decode("latin1")):
        arrays[m.group(1)] = (m.group(2), int(m.group(3)), int(m.group(4)))

    def floats(name):
        typ, n, off = arrays[name]
        if typ != "float":
            raise SystemExit(f"{path}: {name} is {typ}")
        return struct.unpack_from(f">{n}f", d, off)
    xs, ys, zs = floats("fWaypointX"), floats("fWaypointY"), floats("fWaypointZ")
    return [(x, z + 1.0, y) for x, y, z in zip(xs, ys, zs)]


def place(pts, i, back=0.0, ahead=5):
    """x, y, z, fx, fz at waypoint i, facing waypoint i + ahead, back metres behind it."""
    x, y, z = pts[i]
    nx, _, nz = pts[(i + ahead) % len(pts)]
    fx, fz = nx - x, nz - z
    n = math.hypot(fx, fz) or 1.0
    fx, fz = fx / n, fz / n
    return x - back * fx, y, z - back * fz, fx, fz


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    pts = waypoints(argv[1])
    spots = int(argv[2]) if len(argv) > 2 else 1
    for k in range(spots):
        x, y, z, fx, fz = place(pts, k * len(pts) // spots, 15.0 if k == 0 else 0.0)
        print(f"{x:.1f},{y:.1f},{z:.1f},{fx:.3f},{fz:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
