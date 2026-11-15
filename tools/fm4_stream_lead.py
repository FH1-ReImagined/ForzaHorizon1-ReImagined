#!/usr/bin/env python3
"""fm4_stream_lead.py TRACE CIRCUIT [--speed KMH] [--side M] [--from S]: how far ahead of the
car a converted FM4 circuit's scenery is ready (#824).

TRACE is the game's FORZA_FM4_STREAM_TRACE file of a drive (forza-recomp/src/hooks/fm4_track.cpp
lists its rows), CIRCUIT the imported circuit's folder (<data>/fm4-tracks/<name>). For every
instance the car passes within --side metres (60) at --speed km/h or more (200), four moments,
each as the metres of driving left until the car is beside the instance:

  listed    the camera's zone lists it: FH1 draws the camera's zone alone, so this is the
            furthest it can be drawn from (what the converter's zones give)
  zone      a zone that lists it is live: it has its instance object
  model     its model is made from its streamed file
  textures  every texture slot of its model holds its texture at full size: the largest that
            texture is seen at in the trace, by any model (the resident copy of a streamed
            texture is 32 x 32 or smaller). Only textures with a file of their own in the
            circuit's archive count: not FM4's atlas tiles, which the game draws from their
            atlas, nor a texture the archive has no file for

and how long after "listed" the other three came. "never": the model was unloaded again
before that. Instances listed in the first --from seconds (5) of the trace are left out: they
were loaded with the world, and so are the instances without a position of their own (FM4
places those by their model's matrix: roads, terrain).

Then the streamed textures themselves (the trace's texobj rows): each time the game asked for
one, whether it was loaded while asked for and how long that took, and the most that were
asked for and not loaded at one moment."""
import argparse
import bisect
import collections
import csv
import glob
import math
import os
import re
import statistics
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fm4_pvs  # noqa: E402
import fxobj_shaders  # noqa: E402

KINDS = (("trees and bushes", ("tree", "veg", "bush", "foliage", "leaf", "plant", "hedge")),
         ("barriers and fences", ("barr", "fence", "wall", "rail", "tire", "tyre", "armco")),
         ("buildings and stands", ("bldg", "build", "house", "stand", "roof", "struct", "bridge",
                                   "tower", "garage", "glass", "window")),
         ("signs and banners", ("sign", "banner", "bill", "flag", "advert", "logo")),
         ("road, kerbs, verges", ("road", "rdline", "rdedg", "shldr", "kerb", "curb", "grass",
                                  "terr", "gravel", "sand", "dirt")))


def kind_of(shader):
    for kind, parts in KINDS:
        if any(p in shader for p in parts):
            return kind
    return "other"


def read_circuit(folder):
    """The circuit's instances: {instance: (x, y, z)}, {instance: model}, {zone: set of
    instances}, {model: its first shader's name}, {model: its texture numbers by slot}, and the
    texture numbers whose file (_0x<id>.bix) the circuit's archive holds, and those of them
    that are whole textures (no atlas tiles)."""
    track = os.path.basename(os.path.normpath(folder))
    pvs = sorted(glob.glob(os.path.join(folder, "media", "tracks", track, "Ribbon_00", "*.pvs")))
    if not pvs:
        raise SystemExit(f"no PVS in {folder}")
    with open(pvs[0], "rb") as f:
        P = fm4_pvs.read_fh1(f.read())
    path = os.path.join(folder, "media", "tracks", track, "bin.zip")
    zones, where = {}, {}
    with zipfile.ZipFile(path) as z:
        files = {info.filename.split("/")[-1].lower() for info in z.infolist()}
        for info in z.infolist():
            m = re.match(r"__R00Z(\d+)\.pvsz$", info.filename.split("/")[-1])
            if not m:
                continue
            cell = fm4_pvs.read_cell(fm4_pvs.Reader(fxobj_shaders.read_zip_member(z, path, info)),
                                     P["g2"])
            ids = [i & 0x7FFFFFFF for i in cell["instances"]]
            zones[int(m.group(1))] = set(ids)
            for i, e in zip(ids, cell["placements"]):
                if any(e["pos"]):    # one placed by its model's own matrix has no place here
                    where[i] = tuple(e["pos"])
    model_of = {i: P["instances"][i]["model"] for i in where if i < len(P["instances"])}
    shader = {}
    for m in set(model_of.values()):
        names = P["models"][m]["shaders"]
        shader[m] = P["shaders"][names[0]].split("\\")[-1].lower() if names else "?"
    textures = {m: [t & 0xFFFFFF for t in P["models"][m]["textures"]] for m in set(model_of.values())}
    with_file = {n for n, record in enumerate(P["textures"]) if f"_0x{record[0]:08x}.bix" in files}
    # an atlas tile's record holds its rectangle in the atlas (a scale under 1)
    whole = {n for n in with_file if P["textures"][n][2] == 1.0 and P["textures"][n][3] == 1.0}
    return where, model_of, zones, shader, textures, with_file, whole


class Spans:
    """When something held: begin/end pairs in time order."""

    def __init__(self):
        self.spans = []          # [begin, end or None]

    def begin(self, t):
        if not self.spans or self.spans[-1][1] is not None:
            self.spans.append([t, None])

    def end(self, t):
        if self.spans and self.spans[-1][1] is None:
            self.spans[-1][1] = t

    def at(self, t):
        """The span (begin, end or None) that holds at t; the first one after t when none does
        (it came late); None when there is none."""
        for b, e in self.spans:
            if (b <= t and (e is None or t < e)) or b > t:
                return b, e
        return None

    def since(self, t):
        span = self.at(t)
        return span[0] if span else None


def merged(spans_list):
    """The union of several Spans, as one Spans."""
    parts = sorted((b, math.inf if e is None else e) for s in spans_list for b, e in s.spans)
    out = Spans()
    for b, e in parts:
        if out.spans and b <= out.spans[-1][1]:
            out.spans[-1][1] = max(out.spans[-1][1], e)
        else:
            out.spans.append([b, e])
    for s in out.spans:
        if s[1] == math.inf:
            s[1] = None
    return out


def read_trace(path):
    """The trace's rows: the car's samples [(t, x, y, z)], per zone its live Spans, the camera
    centre's zone over time [(t, zone)], per model its loaded Spans, per model
    {slot: [(t, pixels)]}, the pool samples [(t, blocks, of, objects, placements, textures
    held outside the game's heap, their KB)], per texture
    its states [(t, state)]. A last row cut short (the game stopped) is left out."""
    car, zone, camera, pool = [], collections.defaultdict(Spans), [], []
    model, slots = collections.defaultdict(Spans), collections.defaultdict(dict)
    states = collections.defaultdict(list)
    need = {"car": 5, "zone": 4, "want": 10, "model": 5, "gone": 3, "tex": 7, "pool": 6, "texobj": 4}
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if not row or len(row) < need.get(row[0], 99):
                continue
            what, t = row[0], float(row[1])
            if what == "car":
                car.append((t, float(row[2]), float(row[3]), float(row[4])))
            elif what == "zone":
                (zone[int(row[3])].begin if row[2] == "live" else zone[int(row[3])].end)(t)
            elif what == "want" and row[2] == "0":
                camera.append((t, int(row[3])))
            elif what == "model" and row[3] == "1":
                model[int(row[2])].begin(t)
            elif what == "gone":
                model[int(row[2])].end(t)
            elif what == "tex":
                slots[int(row[2])].setdefault(int(row[3]), []).append((t, int(row[5]) * int(row[6])))
            elif what == "pool":
                pool.append((t,) + tuple(int(v) for v in (row[2:8] + ["0", "0"])[:6]))
            elif what == "texobj":
                states[int(row[2])].append((t, int(row[3], 16)))
    return car, zone, camera, model, slots, pool, states


STREAMED, LOADED, NOT_ASKED, NO_OBJECT = 0x400, 0x200, 0x0C0, 0xFFE


def texture_requests(states, end):
    """From the texture objects' states: the times the game asked for a streamed texture, as
    [(texture, asked at, loaded at or None, let go at or None)], and the most textures asked
    for and not loaded at one moment as (count, when)."""
    asks, edges = [], []
    for texture, rows in states.items():
        cur = None
        for t, state in rows:
            asked = state != NO_OBJECT and state & STREAMED and not state & NOT_ASKED
            if not asked:
                if cur:
                    asks.append((texture, cur[0], cur[1], t))
                    cur = None
            elif cur is None:
                cur = [t, t if state & LOADED else None]
            elif state & LOADED and cur[1] is None:
                cur[1] = t
        if cur:
            asks.append((texture, cur[0], cur[1], None))
    for _, asked, loaded, gone in asks:
        until = loaded if loaded is not None else (gone if gone is not None else end)
        if until > asked:
            edges += [(asked, 1), (until, -1)]
    most, now, when = 0, 0, None
    for t, step in sorted(edges):
        now += step
        if now > most:
            most, when = now, t
    return asks, (most, when)


def full_sizes(slots, textures):
    """Per texture number, the most pixels any model's slot held of it in the trace."""
    full = collections.defaultdict(int)
    for m, by_slot in slots.items():
        numbers = textures.get(m, ())
        for slot, changes in by_slot.items():
            if slot < len(numbers):
                full[numbers[slot]] = max(full[numbers[slot]], max(px for _, px in changes))
    return full


def textures_full(by_slot, numbers, full, begin, end, counted=None):
    """When, in a model's loaded span (begin, end or None), every slot first held its texture
    at full size; math.inf when one never did before the model was unloaded, None when no slot
    was seen in the span. With `counted` (texture numbers), only the slots of those textures."""
    last = None
    for slot, changes in by_slot.items():
        if counted is not None and (slot >= len(numbers) or numbers[slot] not in counted):
            continue
        inside = [(at, px) for at, px in changes if at >= begin - 0.2 and (end is None or at < end)]
        if not inside:
            continue
        want = full.get(numbers[slot], 0) if slot < len(numbers) else 0
        at = next((at for at, px in inside if px >= want), math.inf)
        last = at if last is None else max(last, at)
    return last


def percentile(values, share):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * share))]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trace")
    ap.add_argument("circuit")
    ap.add_argument("--speed", type=float, default=200.0, help="km/h the car passes at, or more")
    ap.add_argument("--speed-max", type=float, default=1000.0)
    ap.add_argument("--side", type=float, default=60.0, help="metres from the car's path")
    ap.add_argument("--from", dest="start", type=float, default=5.0,
                    help="seconds of the trace left out")
    ap.add_argument("--kinds", action="store_true", help="a table for each kind of model")
    a = ap.parse_args()

    where, model_of, zones, shader, textures, with_file, whole = read_circuit(a.circuit)
    car, zone_live, camera, model_loaded, slots, pool, states = read_trace(a.trace)
    full = full_sizes(slots, textures)
    if len(car) < 20 or not camera:
        raise SystemExit("the trace has no drive in it (car and want rows)")
    t0 = car[0][0]
    times = [c[0] for c in car]
    along = [0.0]
    for p, q in zip(car, car[1:]):
        along.append(along[-1] + math.hypot(q[1] - p[1], q[3] - p[3]))

    def speed_at(k):           # km/h over a second round the sample
        lo, hi = max(0, k - 5), min(len(car) - 1, k + 5)
        dt = times[hi] - times[lo]
        return (along[hi] - along[lo]) / dt * 3.6 if dt > 0 else 0.0

    def along_at(t):
        k = min(len(car) - 1, max(0, bisect.bisect_left(times, t)))
        return along[k]

    listing = collections.defaultdict(Spans)       # per zone: while the camera's zone
    for (t, z), nxt in zip(camera, camera[1:] + [(None, None)]):
        if z >= 0:
            listing[z].begin(t)
            if nxt[0] is not None:
                listing[z].end(nxt[0])
    zones_of = collections.defaultdict(list)
    for z, ids in zones.items():
        for i in ids:
            zones_of[i].append(z)

    # a coarse grid over the car's samples, to find each instance's closest one
    cell = max(a.side, 50.0)
    buckets = collections.defaultdict(list)
    for k, c in enumerate(car):
        buckets[(int(c[1] // cell), int(c[3] // cell))].append(k)

    rows = []
    for i, (x, y, z) in where.items():
        if i not in model_of:
            continue
        bx, bz = int(x // cell), int(z // cell)
        near = [k for dx in (-1, 0, 1) for dz in (-1, 0, 1) for k in buckets.get((bx + dx, bz + dz), ())]
        if not near:
            continue
        k = min(near, key=lambda k: (car[k][1] - x) ** 2 + (car[k][3] - z) ** 2)
        if math.hypot(car[k][1] - x, car[k][3] - z) > a.side:
            continue
        kmh = speed_at(k)
        if not a.speed <= kmh < a.speed_max:
            continue
        t_pass = times[k]
        t_list = merged([listing[zz] for zz in zones_of[i] if zz in listing]).since(t_pass)
        if t_list is None or t_list - t0 < a.start:
            continue
        m = model_of[i]
        t_zone = merged([zone_live[zz] for zz in zones_of[i] if zz in zone_live]).since(t_pass)
        span = model_loaded[m].at(t_pass) if m in model_loaded else None
        t_model = span[0] if span else None
        t_tex = textures_full(slots.get(m, {}), textures.get(m, ()), full, *span, whole) if span else None
        if t_tex is None and span:
            t_tex = t_model      # no streamed texture of its own to wait for
        elif t_tex is not None:
            t_tex = max(t_tex, t_model)
        rows.append({"kind": kind_of(shader.get(m, "?")), "kmh": kmh, "pass": t_pass,
                     "listed": t_list, "zone": t_zone, "model": t_model, "textures": t_tex})

    def table(title, sel):
        print(f"{title}: {len(sel)} instances")
        if not sel:
            return
        print(f"  {'':9s} {'known':>6s} {'metres ahead of the car when ready':>41s}   {'seconds':>7s}   "
              f"{'after it is listed':>32s}")
        print(f"  {'':9s} {'':>6s} {'min':>6s} {'p10':>6s} {'median':>6s} {'<100 m':>7s} {'<200 m':>7s}   {'median':>7s}   "
              f"{'late':>5s} {'p50 s':>6s} {'p90 s':>6s} {'max s':>6s} {'never':>6s}")
        for stage in ("listed", "zone", "model", "textures"):
            have = [r for r in sel if r[stage] is not None]
            if not have:
                print(f"  {stage:9s} {0:6d}")
                continue
            lead = [along_at(r["pass"]) - along_at(r[stage]) if r[stage] < r["pass"] else 0.0 for r in have]
            secs = [max(0.0, r["pass"] - r[stage]) for r in have]
            late = [max(0.0, r[stage] - r["listed"]) for r in have]
            came = [v for v in late if v != math.inf] or [0.0]
            print(f"  {stage:9s} {len(have):6d} {min(lead):6.0f} {percentile(lead, 0.1):6.0f} "
                  f"{statistics.median(lead):6.0f} {sum(v < 100 for v in lead) / len(lead) * 100:6.0f}% "
                  f"{sum(v < 200 for v in lead) / len(lead) * 100:6.0f}%   {statistics.median(secs):7.2f}   "
                  f"{sum(v > 0.15 for v in late) / len(late) * 100:4.0f}% {statistics.median(came):6.2f} "
                  f"{percentile(came, 0.9):6.2f} {max(came):6.2f} "
                  f"{sum(v == math.inf for v in late) / len(late) * 100:5.1f}%")

    span = f"{a.speed:.0f} km/h or more" if a.speed_max >= 1000 else f"{a.speed:.0f} to {a.speed_max:.0f} km/h"
    table(f"instances within {a.side:.0f} m of the car's path, passed at {span}", rows)
    if a.kinds:
        for kind in [k for k, _ in KINDS] + ["other"]:
            table(f"  {kind}", [r for r in rows if r["kind"] == kind])
    asks, (most, when) = texture_requests({n: rows for n, rows in states.items() if n in with_file},
                                          times[-1])
    asks = texture_requests(states, times[-1])[0]
    asks = [r for r in asks if r[1] - t0 >= a.start]
    for name, sel in (("after the start", asks),
                      (f"at {span}", [r for r in asks if a.speed <= speed_at(
                          min(len(car) - 1, bisect.bisect_left(times, r[1]))) < a.speed_max])):
        if not sel:
            continue
        took = sorted(r[2] - r[1] for r in sel if r[2] is not None)
        left = sorted((r[3] if r[3] is not None else times[-1]) - r[1] for r in sel if r[2] is None)
        line = (f"streamed textures asked for {name}: {len(sel)} times; loaded {len(took)} "
                f"({len(took) / len(sel) * 100:.0f}%)")
        if took:
            line += (f", in {statistics.median(took):.2f} s at the median, {percentile(took, 0.9):.2f} s at p90, "
                     f"{took[-1]:.2f} s at most")
        if left:
            no_file = sum(1 for r in sel if r[2] is None and r[0] not in with_file)
            line += (f"; not loaded while asked for {len(left)} ({len(left) / len(sel) * 100:.0f}%), asked for "
                     f"{statistics.median(left):.1f} s at the median, {left[-1]:.1f} s at most "
                     f"({no_file} of them have no file in the circuit's archive)")
        print(line)
    if states:
        print(f"streamed textures with a file, asked for and not loaded at one moment: {most} at most"
              + (f" ({when - t0:.0f} s into the trace)" if when is not None else ""))
    if pool:
        print(f"streaming blocks in use: at most {max(p[1] for p in pool)} of {pool[-1][2]} "
              f"({max(p[1] for p in pool) / max(1, pool[-1][2]) * 100:.0f}%); instance objects at most "
              f"{max(p[3] for p in pool)}, placements {max(p[4] for p in pool)}")
        if any(p[5] for p in pool):
            top = max(pool, key=lambda p: p[6])
            print(f"streamed textures' data outside the game's heap: at most {top[6] / 1024:.1f} MB "
                  f"({top[5]} textures), {pool[-1][6] / 1024:.1f} MB ({pool[-1][5]}) at the end")


if __name__ == "__main__":
    main()
