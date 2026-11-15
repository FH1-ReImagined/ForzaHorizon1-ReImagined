#!/usr/bin/env python3
"""Report of a list trace run (docs/native-renderer.md, "M4", "List trace").

The game built with -DFORZA_LIST_TRACE=ON and ReIGlue with -DREXGLUE_FH1_LIST_TRACE=ON, run with
FH1_LIST_STATS=<file>, write <file> (a block every FH1_LIST_STATS_EVERY swaps) and <file>.lists
(every registered buffer's totals). This prints, per frame over the blocks chosen:

  - the decoder's time in the ring and in the indirect buffers by class, pass group and depth:
    runs, dwords, time (runs in which the decoder waited for the recorder are counted at the
    class's mean time of the others), register writes read and handed to the recorder;
  - the bin select and mask each list started with, SCRATCH_UMSK at their start, packets and
    CPU reads of the scratch registers;
  - the game's counters: RunCommandBuffer calls by device, mode, flags, class, group, caller; the
    queued path; the live header checks; the conversion; the key checks; shader loads; fixups;
  - from <file>.lists: runs per call of the model lists by group.

  tools/list_trace_report.py <file> [--blocks A-B | --window marks.txt] [--json OUT]
"""
import argparse
import collections
import json
import os
import sys

CLASSES = {0: "unregistered", 1: "model list", 2: "recorded (type 9)", 3: "type 10", 4: "other"}
CONTEXTS = {0: "none", 1: "track", 2: "procedural", 3: "car"}
REFUSE = ["none", "resource-type", "flags", "segments", "patch-table", "list-size", "place-entry",
          "place-subcode", "place-field", "place-conflict", "packet-type-1", "opcode", "untiled",
          "packet-cut", "register", "one-register", "fetch-range", "predicate", "tile-block",
          "window-mix", "draw", "draw-place", "shader", "shader-place", "literals",
          "literals-place"]
CPUREAD = ["SCRATCH_REG0", "SCRATCH_REG1", "SCRATCH_REG2", "SCRATCH_REG3", "SCRATCH_REG4",
           "SCRATCH_REG5", "SCRATCH_REG6", "SCRATCH_REG7", "FLUSH_FETCH_0", "FLUSH_FETCH_1",
           "FLUSH_FETCH_2", "SCRATCH_UMSK", "SCRATCH_ADDR", "-", "-", "other"]


def group_name(g):
    g = int(g)
    if g == 255:
        return "-"
    if g >= 0x80:
        return "car pass %d" % (g & 0x7F)
    return "%d (pass %d layer %d)" % (g, g // 3, g % 3)


def kv(fields):
    out = {}
    for f in fields:
        if "=" in f:
            k, v = f.split("=", 1)
            out[k] = v
    return out


def parse(path):
    blocks, cur = [], None
    for line in open(path):
        f = line.split()
        if not f:
            continue
        t = f[0]
        if t == "BLOCK":
            cur = {"head": kv(f[1:]), "C": [], "BIN": [], "UMSK": [], "READ": [], "U": [], "K": {},
                   "CPUREAD": []}
        elif cur is None:
            continue
        elif t == "PRIMARY":
            cur["PRIMARY"] = kv(f[1:])
        elif t == "C":
            cur["C"].append((int(f[1]), int(f[2]), int(f[3]), {k: int(v) for k, v in kv(f[4:]).items()}))
        elif t == "BIN":
            d = kv(f[3:])
            cur["BIN"].append((int(f[1]), int(f[2]), d["sel"], d["mask"], int(d["runs"])))
        elif t == "UMSK":
            cur["UMSK"].append((int(f[1]), f[2], int(kv(f[3:])["runs"])))
        elif t == "READ":
            d = kv(f[1:])
            cur["READ"].append((d["op"], d["reg"], d["mem"], int(d["n"])))
        elif t == "CPUREAD":
            cur["CPUREAD"] = [int(x) for x in f[1:]]
        elif t == "U":
            d = kv(f[2:])
            cur["U"].append((f[1], int(d["n"]), int(d["depth"]), int(d["runs"]), int(d["dw"]), int(d["ticks"])))
        elif t == "K":
            cur["K"][int(f[1], 16)] = int(f[2])
        elif t == "END":
            blocks.append(cur)
            cur = None
    return blocks


def parse_lists(path):
    rows = []
    if not os.path.exists(path):
        return rows
    for line in open(path):
        f = line.split()
        if not f or f[0] != "L":
            continue
        d = kv(f[2:])
        d["phys"] = f[1]
        for k in ("n", "cls", "group", "ctx", "binds", "calls", "runs", "skipped", "ticks", "changes"):
            d[k] = int(d[k])
        rows.append(d)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--blocks", help="A-B (1-based, inclusive)")
    ap.add_argument("--window", help="a marks.txt (\"<epoch> start\", \"<epoch> end\"): the blocks that end inside it")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    blocks = parse(a.file)
    if not blocks:
        sys.exit("no complete block in %s" % a.file)
    lo, hi = 1, len(blocks)
    if a.blocks:
        lo, hi = (int(x) for x in a.blocks.split("-"))
    elif a.window:
        t0, t1 = (float(l.split()[0]) for l in open(a.window))
        inside = [i + 1 for i, b in enumerate(blocks) if t0 <= float(b["head"].get("wall", 0)) <= t1]
        if not inside:
            sys.exit("no block ends inside the window")
        lo, hi = inside[0] + 1, inside[-1]  # the first one started before the window
    sel = blocks[lo - 1:hi]
    frames = sum(int(b["head"]["frames"]) for b in sel)
    ticks = sum(int(b["head"]["ticks"]) for b in sel)
    ns = sum(int(b["head"]["ns"]) for b in sel)
    ns_tick = ns / ticks
    ms = lambda t: t * ns_tick / 1e6 / frames
    pf = lambda n: n / frames
    out = {"blocks": [lo, hi], "frames": frames, "seconds": ns / 1e9}
    print("blocks %d-%d of %d: %d frames in %.1f s (%.1f fps), game: %s" % (
        lo, hi, len(blocks), frames, ns / 1e9, frames / (ns / 1e9), sel[0]["head"]["game"]))
    prim = collections.Counter()
    for b in sel:
        for k, v in b.get("PRIMARY", {}).items():
            prim[k] += int(v)
    wall = ms(prim["ticks"])
    decoder = prim["cpu_ns"] / 1e6 / frames
    print("\ndecoder in the ring (the buffers it runs included): %.3f ms of CPU a frame (%.3f ms wall,"
          " waits included), %.0f ring dwords, %.1f calls" % (decoder, wall, pf(prim["dw"]), pf(prim["calls"])))
    out["decoder_ms"] = decoder

    # Indirect buffers.
    agg = collections.defaultdict(collections.Counter)
    for b in sel:
        for cls, group, depth, d in b["C"]:
            agg[(cls, group, depth)].update(d)

    def est(c):  # sync-free time plus the sync runs at the mean of the others
        free = c["runs"] - c["sync_runs"]
        mean = c["ticks"] / free if free else 0
        return c["ticks"] + mean * c["sync_runs"]

    print("\nindirect buffers a frame by class, depth (self = without the buffers they run)")
    print("%-18s %5s %9s %10s %8s %8s %7s %9s %9s %8s" % (
        "class", "depth", "runs", "dwords", "ms", "self ms", "ns/dw", "regs in", "regs out", "skipped"))
    by_cd = collections.defaultdict(collections.Counter)
    for (cls, group, depth), c in agg.items():
        k = by_cd[(cls, depth)]
        k.update(c)
        k["est"] += est(c)
        free = c["runs"] - c["sync_runs"]
        k["self_est"] += c["self"] + (c["self"] / free if free else 0) * c["sync_runs"]
    rows = []
    for (cls, depth), c in sorted(by_cd.items()):
        r = dict(cls=CLASSES.get(cls, cls), depth=depth, runs=pf(c["runs"]), dwords=pf(c["dw"]),
                 ms=ms(c["est"]), self_ms=ms(c["self_est"]),
                 ns_dw=(c["est"] * ns_tick / c["dw"]) if c["dw"] else 0,
                 regs_in=pf(c["rin"]), regs_out=pf(c["rout"]), skipped=pf(c["skipped"]),
                 sync_runs=pf(c["sync_runs"]))
        rows.append(r)
        print("%-18s %5d %9.1f %10.0f %8.3f %8.3f %7.2f %9.0f %9.0f %8.1f" % (
            r["cls"], depth, r["runs"], r["dwords"], r["ms"], r["self_ms"], r["ns_dw"], r["regs_in"],
            r["regs_out"], r["skipped"]))
    out["classes"] = rows
    total_dw = pf(prim["dw"]) + sum(r["dwords"] for r in rows)
    print("dwords read a frame (ring + every buffer): %.0f" % total_dw)
    for r in rows:
        r["share_dwords"] = r["dwords"] / total_dw if total_dw else 0
        r["share_decoder"] = r["self_ms"] / decoder if decoder else 0
    # The ring's own packets are a few hundred dwords a frame; outside the buffers its time is waits.
    wall_self = sum(r["self_ms"] for r in rows)
    for r in rows:
        r["share_wall"] = r["self_ms"] / wall_self if wall_self else 0
    print("decoder time in the buffers (their own time, waits taken out): %.3f ms a frame; by class: %s" % (
        wall_self, "; ".join("%s d%d %.1f%%" % (r["cls"], r["depth"], 100 * r["share_wall"]) for r in rows)))
    out["decoder_wall_self_ms"] = wall_self
    print("\nshares: %s" % "; ".join("%s d%d %.1f%% of dwords, %.1f%% of decoder time (self)" % (
        r["cls"], r["depth"], 100 * r["share_dwords"], 100 * r["share_decoder"]) for r in rows))

    print("\nbuffers at depth 2+ by class and pass group")
    print("%-18s %-22s %9s %10s %8s %7s %9s %9s %8s %7s" % ("class", "group", "runs", "dwords", "ms", "ns/dw",
                                                            "regs in", "regs out", "skipped", "syncs"))
    groups = collections.defaultdict(collections.Counter)
    for (cls, group, depth), c in agg.items():
        if depth >= 2 or cls:
            groups[(cls, group)].update(c)
            groups[(cls, group)]["est"] += est(c)
    grows = []
    for (cls, g), c in sorted(groups.items()):
        r = dict(cls=cls, group=g, runs=pf(c["runs"]), dwords=pf(c["dw"]), ms=ms(c["est"]),
                 ns_dw=(c["est"] * ns_tick / c["dw"]) if c["dw"] else 0, regs_in=pf(c["rin"]),
                 regs_out=pf(c["rout"]), skipped=pf(c["skipped"]), sync_runs=pf(c["sync_runs"]))
        grows.append(r)
        print("%-18s %-22s %9.1f %10.0f %8.3f %7.2f %9.0f %9.0f %8.1f %7.2f" % (
            CLASSES.get(cls), group_name(g), r["runs"], r["dwords"], r["ms"], r["ns_dw"], r["regs_in"],
            r["regs_out"], r["skipped"], r["sync_runs"]))
    out["groups"] = grows

    bins = collections.Counter()
    for b in sel:
        for cls, group, s, m, n in b["BIN"]:
            bins[(cls, group, s, m)] += n
    print("\nbin select / mask when a buffer at depth 2+ starts (runs a frame, top %d per class)" % a.top)
    by_cls = collections.defaultdict(list)
    for (cls, group, s, m), n in bins.items():
        by_cls[cls].append((n, group, s, m))
    out["bins"] = []
    for cls, lst in sorted(by_cls.items()):
        for n, group, s, m in sorted(lst, reverse=True)[:a.top]:
            print("  %-18s %-22s select %s mask %s  %.1f" % (CLASSES.get(cls), group_name(group), s, m, pf(n)))
        for n, group, s, m in lst:
            out["bins"].append(dict(cls=cls, group=group, select=s, mask=m, runs=pf(n)))

    umsk = collections.Counter()
    reads = collections.Counter()
    cpu = [0] * 16
    for b in sel:
        for cls, v, n in b["UMSK"]:
            umsk[(cls, v)] += n
        for op, reg, mem, n in b["READ"]:
            reads[(op, reg, mem)] += n
        for i, v in enumerate(b["CPUREAD"]):
            cpu[i] += v
    print("\nSCRATCH_UMSK at the start of buffers at depth 2+: " + ", ".join(
        "%s %s: %.1f" % (CLASSES.get(c), v, pf(n)) for (c, v), n in sorted(umsk.items())))
    print("packets reading a register (a frame): " + (", ".join(
        "op %s reg %s%s: %.2f" % (op, reg, " (memory)" if mem == "1" else "", pf(n))
        for (op, reg, mem), n in sorted(reads.items())) or "none"))
    print("CPU reads of GPU registers (a frame): " + ", ".join(
        "%s %.2f" % (CPUREAD[i], pf(v)) for i, v in enumerate(cpu) if v))
    out["umsk"] = {"%d/%s" % k: pf(n) for k, n in umsk.items()}
    out["reads"] = {"%s/%s/%s" % k: pf(n) for k, n in reads.items()}
    out["cpu_reads"] = {CPUREAD[i]: pf(v) for i, v in enumerate(cpu) if v}

    unreg = collections.defaultdict(collections.Counter)
    for b in sel:
        for phys, n, depth, runs, dw, t in b["U"]:
            unreg[(phys, n, depth)].update(dict(runs=runs, dw=dw, ticks=t))
    print("\nunregistered buffers at depth 2+ (%d): %.1f runs, %.0f dwords, %.3f ms a frame" % (
        len(unreg), pf(sum(c["runs"] for c in unreg.values())), pf(sum(c["dw"] for c in unreg.values())),
        ms(sum(c["ticks"] for c in unreg.values()))))
    for (phys, n, depth), c in sorted(unreg.items(), key=lambda x: -x[1]["dw"])[:a.top]:
        print("  %s n=%d depth %d: %.2f runs, %.0f dwords a frame" % (phys, n, depth, pf(c["runs"]), pf(c["dw"])))

    # The game's counters.
    K = collections.Counter()
    for b in sel:
        for k, v in b["K"].items():
            K[k] += v
    out["game"] = report_game(K, frames, a.top)
    lists = parse_lists(a.file + ".lists")
    if lists:
        out["lists"] = report_lists(lists, a.top)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)


def report_game(K, frames, top):
    pf = lambda n: n / frames
    T = collections.defaultdict(dict)
    for k, v in K.items():
        T[k >> 56][k & ((1 << 56) - 1)] = v
    res = {}
    devs = {}
    for k, v in T[2].items():
        devs[(k >> 48) & 0xFF] = (k & 0xFFFFFFFF, v)
    print("\ngame: devices of RunCommandBuffer (slot: device, calls a frame): " + ", ".join(
        "%d: %08X %.1f" % (s, d, pf(v)) for s, (d, v) in sorted(devs.items())))
    res["devices"] = {"%d" % s: ["%08X" % d, pf(v)] for s, (d, v) in devs.items()}
    calls = T[1]
    total = sum(calls.values())
    print("RunCommandBuffer calls: %.1f a frame" % pf(total))

    def split(field, name, fmt=str):
        c = collections.Counter()
        for k, v in calls.items():
            c[field(k)] += v
        print("  by %s: %s" % (name, ", ".join("%s %.1f" % (fmt(x), pf(n)) for x, n in c.most_common(top))))
        res["calls_by_" + name.replace(" ", "_")] = {str(fmt(x)): pf(n) for x, n in c.items()}
    split(lambda k: (k >> 52) & 0xF, "device slot")
    split(lambda k: "%02X" % ((k >> 44) & 0xFF), "mode byte")
    split(lambda k: "%04X" % ((k >> 28) & 0xFFFF), "flags")
    split(lambda k: CLASSES.get((k >> 24) & 0xF), "class")
    split(lambda k: CONTEXTS.get((k >> 12) & 0xF), "context")
    split(lambda k: (k >> 4) & 0xF, "type")
    split(lambda k: "%s/%s" % (CLASSES.get((k >> 24) & 0xF), group_name((k >> 16) & 0xFF)), "class and group")
    split(lambda k: "%s %s %s" % (CLASSES.get((k >> 24) & 0xF), "slot%d" % ((k >> 52) & 0xF),
                                  "mode%02X" % ((k >> 44) & 0xFF)), "class, device and mode")
    split(lambda k: "%s untiled=%d" % (group_name((k >> 16) & 0xFF), (k >> 8) & 1), "group and untiled")

    def table(t, name, decode):
        c = collections.Counter()
        for k, v in T[t].items():
            c[decode(k)] += v
        print("%s: %s" % (name, ", ".join("%s %s" % (x, ("%.2f" % pf(n)) if frames else n)
                                         for x, n in c.most_common(top)) or "none"))
        res[name] = {str(x): n for x, n in c.items()}
        return c
    table(4, "RunCommandBuffer callers (a frame)", lambda k: "%08X %s type %d" % (
        (k >> 16) & 0xFFFFFFFF, CLASSES.get((k >> 8) & 0xF), (k >> 4) & 0xF))
    table(3, "sub_824467A8 callers (a frame)", lambda k: "%08X %s%s ctx %s%s" % (
        (k >> 16) & 0xFFFFFFFF, CLASSES.get((k >> 8) & 0xF), " QUEUED" if (k >> 15) & 1 else "",
        CONTEXTS.get((k >> 4) & 0xF), " flagged-thread" if (k >> 14) & 1 else ""))
    table(5, "sub_824467A8 device picked (a frame)", lambda k: "flag=%d slot=%d queued=%d" % (
        (k >> 16) & 1, (k >> 8) & 0xFF, k & 1))
    table(24, "calls on a recording device (a frame)", lambda k: "%s/%s" % (
        CLASSES.get((k >> 48) & 0xF), group_name((k >> 40) & 0xFF)))
    table(15, "sub_82D10908 calls by caller (a frame)", lambda k: "%08X" % (k & 0xFFFFFFFF))
    table(16, "sub_82CE9258 calls by caller (a frame)", lambda k: "%08X" % (k & 0xFFFFFFFF))
    table(14, "fixups by type, class, group (a frame)", lambda k: "type %d %s/%s" % (
        (k >> 48) & 0xFF, CLASSES.get((k >> 40) & 0xF), group_name((k >> 32) & 0xFF)))

    frames_saved = frames
    # Counts of command buffers, not per frame.
    def counts(t, name, decode):
        c = collections.Counter()
        for k, v in T[t].items():
            c[decode(k)] += v
        print("%s: %s" % (name, ", ".join("%s: %d" % (x, n) for x, n in sorted(c.items())) or "none"))
        res[name] = {str(x): n for x, n in c.items()}
    counts(21, "command buffers first seen", lambda k: "%s/%s" % (
        CLASSES.get((k >> 48) & 0xF), group_name((k >> 40) & 0xFF)))
    counts(17, "segments per command buffer (first looks)", lambda k: "%s %d" % (
        CLASSES.get((k >> 48) & 0xF), k & 0xFFFF))
    counts(6, "header at the first look (mask kind 0 all ones, 1 the files' passes 0/6, 2 other equal, 3 halves differ)",
           lambda k: "%s/%s kind %d flags %X" % (CLASSES.get((k >> 48) & 0xF), group_name((k >> 40) & 0xFF),
                                                 (k >> 32) & 0xFF, k & 0xFFFFFFFF))
    counts(7, "later calls: masks changed / flags xor against the first look",
           lambda k: "%s/%s masks %d xor %X" % (CLASSES.get((k >> 48) & 0xF), group_name((k >> 40) & 0xFF),
                                                (k >> 32) & 0xFF, k & 0xFFFFFFFF))
    counts(8, "conversion at the first look (refusal, untiled then)", lambda k: "%s/%s %s untiled=%d" % (
        CLASSES.get((k >> 48) & 0xF), group_name((k >> 40) & 0xFF), REFUSE[(k >> 32) & 0xFF]
        if ((k >> 32) & 0xFF) < len(REFUSE) else "?", k & 1))
    counts(9, "conversion sizes (0 lists, 1 PM4 dwords, 2 template words, 3 converted)",
           lambda k: "%s %d" % (group_name((k >> 40) & 0xFF), k & 0xFF))
    counts(10, "area C at the first look (address plausible, the header's +0x80)", lambda k: "%s ptr=%d words=%d" % (
        group_name((k >> 40) & 0xFF), (k >> 32) & 1, k & 0xFFFF))
    counts(11, "key checks (0 same, 1 differs; untiled)", lambda k: "%s %d untiled=%d" % (
        group_name((k >> 40) & 0xFF), (k >> 8) & 0xFF, k & 1))
    counts(12, "keys that differ: region (1 header, 2 patch table, 3 PM4, 4 lengths, 5 area C, 0 none)",
           lambda k: "%s region %d untiled=%d" % (group_name((k >> 40) & 0xFF), (k >> 8) & 0xFF, k & 1))
    counts(13, "shader loads: low two bits of the address word (first/second of a pair; 0 first look, 1 after a fixup)",
           lambda k: "%s pos %d bits %d when %d" % (group_name((k >> 40) & 0xFF), (k >> 16) & 1, (k >> 8) & 3, k & 0xFF))
    counts(25, "model lists' flags in memory at the first look", lambda k: "%s %X" % (
        group_name((k >> 40) & 0xFF), k & 0xFFFFFFFF))
    counts(26, "command buffers holding another list than at their first look (old class, new class)",
           lambda k: "%s -> %s" % (CLASSES.get((k >> 48) & 0xF), CLASSES.get((k >> 40) & 0xF)))
    counts(27, "keys that differ: group, region, first dword that differs",
           lambda k: "%s region %d dword %d" % (group_name((k >> 40) & 0xFF), (k >> 32) & 0xFF, k & 0xFFFFF))
    counts(28, "recorded buffers at their first look: the memcpy that wrote their start (caller; source 1 physical, 2 other, 0 none found)",
           lambda k: "%s %08X from %d" % (group_name((k >> 48) & 0xFF), (k >> 8) & 0xFFFFFFFF, k & 0xFF))
    counts(29, "D3D's command buffer clone (sub_82AA8220) calls: caller, the source's class, copy made",
           lambda k: "%08X source %s made %d" % ((k >> 16) & 0xFFFFFFFF, CLASSES.get((k >> 8) & 0xFF), k & 1))
    counts(30, "recorded buffers at their first look: made by the clone function (group, source class, source group, caller)",
           lambda k: "%s source %s/%s by %08X" % (group_name((k >> 48) & 0xFF),
                                                  CLASSES.get((k >> 40) & 0xFF, "not a clone"),
                                                  group_name((k >> 32) & 0xFF), k & 0xFFFFFFFF))
    counts(20, "buffers registered again with another command buffer or length",
           lambda k: CLASSES.get((k >> 48) & 0xF))
    return res


def report_lists(lists, top):
    res = {}
    print("\nregistered buffers (totals of the run): %d" % len(lists))
    by = collections.defaultdict(collections.Counter)
    for r in lists:
        c = by[(r["cls"], r["group"])]
        c["buffers"] += 1
        c["calls"] += r["calls"]
        c["runs"] += r["runs"]
        c["skipped"] += r["skipped"]
        c["dwords"] += r["runs"] * r["n"]
        c["changes"] += r["changes"]
        c["rebound"] += r["binds"] > 0
    print("%-18s %-22s %8s %10s %10s %10s %6s %7s %8s" % ("class", "group", "buffers", "calls", "runs",
                                                         "skipped", "r/call", "(r+s)/c", "changes"))
    res["by_class_group"] = []
    for (cls, g), c in sorted(by.items()):
        rc = c["runs"] / c["calls"] if c["calls"] else 0
        rsc = (c["runs"] + c["skipped"]) / c["calls"] if c["calls"] else 0
        print("%-18s %-22s %8d %10d %10d %10d %6.2f %7.2f %8d" % (CLASSES.get(cls), group_name(g), c["buffers"],
                                                               c["calls"], c["runs"], c["skipped"], rc, rsc,
                                                               c["changes"]))
        res["by_class_group"].append(dict(cls=cls, group=g, **c, runs_per_call=rc, read_per_call=rsc))
    # Runs per call of each tiled model list (pass 4).
    hist = collections.Counter()
    for r in lists:
        if r["cls"] == 1 and r["calls"]:
            hist[(r["group"], round(r["runs"] / r["calls"], 1), round((r["runs"] + r["skipped"]) / r["calls"], 1))] += 1
    print("model lists by runs per call (group: runs/call, (runs+skipped)/call -> lists), most common:")
    res["runs_per_call"] = []
    for (g, rc, rsc), n in sorted(hist.items(), key=lambda x: -x[1])[:top * 2]:
        print("  %-22s %4.1f %4.1f -> %d" % (group_name(g), rc, rsc, n))
    for (g, rc, rsc), n in hist.items():
        res["runs_per_call"].append(dict(group=g, runs_per_call=rc, read_per_call=rsc, lists=n))
    return res


if __name__ == "__main__":
    main()
