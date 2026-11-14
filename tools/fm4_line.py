#!/usr/bin/env python3
"""A driving line round a converted FM4 circuit, for the dev autopilot (FORZA_AUTOPILOT,
forza-recomp/src/kernel/input_autopress.cpp) that drives test laps (#648).

The line runs through the track's asphalt (surface 0, Asphalt, in the converted collision
of tools/fm4_col.py): the shortest way over the asphalt triangles (neighbours share a corner)
from each trackside camera of the ribbon's tracksegcams.xml, in their order round the lap, to
the next (a camera that would make a detour is dropped), moved to the middle of the asphalt
across it. It is resampled every STEP metres and given a speed: at most VMAX, at most what
LAT_ACC allows in its bends, and slowing down in time at DECEL.

Usage: tools/fm4_line.py TRACK.col TRACKSEGCAMS.xml|TRACK_NN.geo OUT.txt [VMAX_KMH]
  with FM4's AI line (.geo) in place of the cameras, the line follows it
  OUT.txt: a line per point, "x y z speed_m_s"
"""
import heapq
import math
import os
import re
import struct
import sys

STEP = 4.0
LAT_ACC = 5.0
DECEL = 4.0
CLEARANCE = 3.5
# Surfaces the line may use and what a metre on them costs: asphalt (0) first, then the
# second and slow asphalt (5; 36, and 26 in a collision converted before #876) and kerbs (6),
# the pit lane (20, walled off the start-finish straight) last.
ROAD = {0: 1.0, 5: 1.5, 36: 1.5, 26: 1.5, 6: 2.0, 20: 4.0}


WALLS = []  # (x1, z1, x2, z2, y low, y high) of the walls' triangles, seen from above


def road_triangles(col):
    d = open(col, "rb").read()
    n = struct.unpack_from(">I", d, 0x4C)[0]
    tris = {}
    for i in range(n):
        _, _, off, size, _, _ = struct.unpack_from(">6I", d, 0x58 + 24 * i)
        c = d[off:off + size]
        _, nt, _, toff = struct.unpack_from(">4I", c, 0)
        for t in range(nt):
            fl, a, b, cc = struct.unpack_from(">IIII", c, toff + 32 * t)
            ny = struct.unpack_from(">f", c, toff + 32 * t + 20)[0]
            if abs(ny) < 0.5:
                w = [struct.unpack_from(">fff", c, o) for o in (a, b, cc)]
                xs, zs = [v[0] for v in w], [v[2] for v in w]
                i0 = max(range(3), key=lambda i: (xs[i] - xs[i - 1]) ** 2 + (zs[i] - zs[i - 1]) ** 2)
                WALLS.append((xs[i0 - 1], zs[i0 - 1], xs[i0], zs[i0],
                              min(v[1] for v in w), max(v[1] for v in w)))
            if ny < 0.8 or (fl >> 16) & 0xFF not in ROAD:
                continue
            v = tuple(tuple(round(q, 1) for q in struct.unpack_from(">fff", c, o)) for o in (a, b, cc))
            tris[frozenset(v)] = (v, ROAD[(fl >> 16) & 0xFF])  # cells overlap: once each
    return list(tris.values())


def graph(tris):
    by_vertex = {}
    cost = [w for _, w in tris]
    tris = [t for t, _ in tris]
    for k, t in enumerate(tris):
        for v in t:
            by_vertex.setdefault(v, []).append(k)
    cen = [tuple(sum(v[i] for v in t) / 3 for i in range(3)) for t in tris]
    nb = [set() for _ in tris]
    for ks in by_vertex.values():
        for a in ks:
            nb[a].update(ks)
    # Pieces of asphalt FM4 left apart (painted lines, seams): near centres of the same
    # surface join too (not across the wall between two surfaces, as the pit lane's).
    grid = {}
    for k, c in enumerate(cen):
        grid.setdefault((int(c[0] // 3), int(c[2] // 3)), []).append(k)
    for (gx, gz), ks in grid.items():
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for b in grid.get((gx + dx, gz + dz), []):
                    for a in ks:
                        if a != b and cost[a] == cost[b] and math.dist(cen[a], cen[b]) < 3.0:
                            nb[a].add(b)
    for a in range(len(tris)):
        nb[a].discard(a)
    graph.cost = cost
    return cen, nb


def shortest(cen, nb, a, b):
    dist = {a: 0.0}
    prev = {}
    q = [(0.0, a)]
    while q:
        d, u = heapq.heappop(q)
        if u == b:
            break
        if d > dist[u]:
            continue
        for v in nb[u]:
            nd = d + math.dist(cen[u], cen[v]) * graph.cost[v]
            if nd < dist.get(v, 1e18):
                dist[v], prev[v] = nd, u
                heapq.heappush(q, (nd, v))
    if b not in dist:
        return None
    path = [b]
    while path[-1] != a:
        path.append(prev[path[-1]])
    return path[::-1]


def main(argv):
    if len(argv) < 4:
        print(__doc__)
        return 2
    vmax = (float(argv[4]) if len(argv) > 4 else 90.0) / 3.6
    tris = road_triangles(argv[1])
    if argv[2].lower().endswith(".geo"):
        # FM4's own AI line (media/AI/Tracks/<track>_NN.geo, tools/fm4_spawn.py): the way its
        # drivers take, so no asphalt search between cameras and no centring. For circuits
        # whose collision has no asphalt path between the cameras (Fujimi Kaido's roads are
        # another surface, Sunset Infield's oval is walled off its infield cameras).
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fm4_spawn
        return finish(argv, vmax, fm4_spawn.waypoints(argv[2]), tris, 0, centre=False)
    cen, nb = graph(tris)
    xml = open(argv[2], encoding="utf-8", errors="replace").read()
    cams = sorted(tuple(map(float, m.groups())) for m in re.finditer(
        r'<Cam [^>]*StartCut="([\d.]+)".*?<Key Time="[\d.]+" PosX="([-\d.]+)" '
        r'PosY="([-\d.]+)" PosZ="([-\d.]+)"', xml, re.S))
    # In-car cameras have local positions (within metres of 0, 0); the trackside ones are
    # placed in the world. (A height test, over 100 m, held for Suzuka at 600 m only.)
    cams = [c for c in cams if math.hypot(c[1], c[3]) > 20]
    anchors = []
    for _, x, y, z in cams:
        k = min((i for i in range(len(cen)) if graph.cost[i] == 1.0),
                key=lambda i: (cen[i][0] - x) ** 2 + (cen[i][2] - z) ** 2
                + (2 * (cen[i][1] - y)) ** 2)
        if not anchors or anchors[-1] != k:
            anchors.append(k)
    # A camera that films the track from another part of it (its nearest asphalt is not on
    # the lap's way there) shows as a detour: drop it.
    memo = {}

    def length(a, b):
        if (a, b) not in memo:
            p = shortest(cen, nb, a, b)
            memo[(a, b)] = (sum(math.dist(cen[u], cen[v]) for u, v in zip(p, p[1:]))
                            if p else 1e9)
        return memo[(a, b)]

    changed = True
    while changed and len(anchors) > 3:
        changed = False
        for i in range(len(anchors)):
            a, k, b = anchors[i - 1], anchors[i], anchors[(i + 1) % len(anchors)]
            if length(a, b) < length(a, k) + length(k, b) - 150:
                del anchors[i]
                changed = True
                break
    pts = []
    for a, b in zip(anchors, anchors[1:] + anchors[:1]):
        p = shortest(cen, nb, a, b)
        if p is None:
            print(f"no asphalt path from {cen[a]} to {cen[b]}")
            return 1
        pts += [cen[k] for k in p[:-1]]
    return finish(argv, vmax, pts, tris, len(anchors))


def finish(argv, vmax, pts, tris, anchors, centre=True):
    # Resample every STEP m, smoothed over a few points.
    out, acc = [pts[0]], 0.0
    for p, q in zip(pts, pts[1:] + pts[:1]):
        seg = math.dist(p, q)
        while acc + seg >= STEP and seg > 0:
            t = (STEP - acc) / seg
            p = tuple(p[i] + (q[i] - p[i]) * t for i in range(3))
            out.append(p)
            seg = math.dist(p, q)
            acc = 0.0
        acc += seg
    n = len(out)

    def smooth(pts, times):
        for _ in range(times):
            pts = [tuple((pts[i - 2][k] + pts[i - 1][k] + pts[i][k] + pts[(i + 1) % n][k]
                          + pts[(i + 2) % n][k]) / 5 for k in range(3)) for i in range(n)]
        return pts

    out = smooth(out, 3)
    # The shortest way hugs the insides of bends and the walls beside the pit lane: move each
    # point to the middle of the asphalt across the line (the run of surface-0 triangles under
    # it, probed every 0.5 m to 12 m each side, within 1.5 m of its height).
    road = [t for t, w in tris if w == 1.0]
    bins = {}
    for k, t in enumerate(road):
        xs, zs = [v[0] for v in t], [v[2] for v in t]
        for gx in range(int(min(xs) // 4), int(max(xs) // 4) + 1):
            for gz in range(int(min(zs) // 4), int(max(zs) // 4) + 1):
                bins.setdefault((gx, gz), []).append(k)

    def on_road(x, y, z):
        for k in bins.get((int(x // 4), int(z // 4)), ()):
            (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = road[k]
            den = (z2 - z3) * (x1 - x3) + (x3 - x2) * (z1 - z3)
            if abs(den) < 1e-9:
                continue
            a = ((z2 - z3) * (x - x3) + (x3 - x2) * (z - z3)) / den
            b = ((z3 - z1) * (x - x3) + (x1 - x3) * (z - z3)) / den
            if min(a, b, 1 - a - b) >= 0 and abs(a * y1 + b * y2 + (1 - a - b) * y3 - y) < 1.5:
                return True
        return False

    centred = []
    for i in range(n if centre else 0):
        p, a, b = out[i], out[i - 1], out[(i + 1) % n]
        dx, dz = b[0] - a[0], b[2] - a[2]
        m = math.hypot(dx, dz) or 1.0
        px, pz = -dz / m, dx / m
        hit = [o / 2 for o in range(-24, 25) if on_road(p[0] + px * o / 2, p[1], p[2] + pz * o / 2)]
        if not hit:
            centred.append(p)
            continue
        # The run of road nearest the point.
        runs, run = [], [hit[0]]
        for o in hit[1:]:
            if o - run[-1] > 0.6:
                runs.append(run)
                run = [o]
            else:
                run.append(o)
        runs.append(run)
        best = min(runs, key=lambda r: min(abs(o) for o in r))
        mid = (best[0] + best[-1]) / 2
        centred.append((p[0] + px * mid, p[1], p[2] + pz * mid))
    out = smooth(centred if centre else out, 4)
    # Keep CLEARANCE m from walls at the car's height (the pit wall's end beside the straight).
    wbins = {}
    for w in WALLS:
        for gx in range(int(min(w[0], w[2]) // 8) - 1, int(max(w[0], w[2]) // 8) + 2):
            for gz in range(int(min(w[1], w[3]) // 8) - 1, int(max(w[1], w[3]) // 8) + 2):
                wbins.setdefault((gx, gz), []).append(w)
    pushed = []
    for p in out:
        x, y, z = p
        for _ in range(3):
            near = None
            for x1, z1, x2, z2, lo, hi in wbins.get((int(x // 8), int(z // 8)), ()):
                if hi < y + 0.2 or lo > y + 1.5:
                    continue
                dx, dz = x2 - x1, z2 - z1
                ll = dx * dx + dz * dz or 1e-9
                t = max(0.0, min(1.0, ((x - x1) * dx + (z - z1) * dz) / ll))
                qx, qz = x1 + t * dx, z1 + t * dz
                dd = math.hypot(x - qx, z - qz)
                if dd < CLEARANCE and (near is None or dd < near[0]):
                    near = (dd, qx, qz)
            if near is None:
                break
            dd, qx, qz = near
            if dd < 1e-3:
                break
            x, z = qx + (x - qx) / dd * CLEARANCE, qz + (z - qz) / dd * CLEARANCE
        pushed.append((x, y, z))
    out = smooth(pushed, 2)
    speed = []
    for i in range(n):
        a, b, c = out[i - 3], out[i], out[(i + 3) % n]
        h1 = math.atan2(b[2] - a[2], b[0] - a[0])
        h2 = math.atan2(c[2] - b[2], c[0] - b[0])
        turn = abs((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)
        curv = turn / (3 * STEP)
        speed.append(min(vmax, math.sqrt(LAT_ACC / curv) if curv > 1e-4 else vmax))
    for _ in range(2):  # brake in time (round the lap twice)
        for i in range(n - 1, -1, -1):
            nxt = speed[(i + 1) % n]
            speed[i] = min(speed[i], math.sqrt(nxt * nxt + 2 * DECEL * STEP))
    with open(argv[3], "w") as f:
        for p, v in zip(out, speed):
            f.write(f"{p[0]:.2f} {p[1]:.2f} {p[2]:.2f} {v:.2f}\n")
    length = sum(math.dist(out[i - 1], out[i]) for i in range(n))
    print(f"{argv[3]}: {n} points, {length:.0f} m, {anchors} cameras" if centre else
          f"{argv[3]}: {n} points, {length:.0f} m, from FM4's AI line")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
