#!/usr/bin/env python3
"""Report of a pass ledger run (docs/rendering.md, "Pass ledger"; ReIGlue 0670).

The game run with --fh1_pass_cost_csv=<file> writes <file>: per window of fh1_pass_cost_every
frames, a row per segment label and draw class (and a "segment" row per label, a "frame" row per
window). With --fh1_gpu_profile and --fh1_gpu_profile_csv=<gpu> the GPU profile's CSV has rows
"pass|<label>|<class>|<layer>|<kind>" too. This prints, a frame on average over the windows
chosen:

  - per label (in stream order): the render targets of its first draw, segments, the render
    thread's CPU and flush time, the decoder's and the recorder's time, IssueDraw's time for the
    draws and the resolves, the draws submitted by the game, decoded, skipped by a predicate and
    issued, and the GPU's time;
  - the same per label and draw class;
  - with --delta, the change-mask counter: for each register group and recorder stage, the share
    of draws with it unchanged since the previous draw;
  - the checks: the issued draws and resolves of all labels equal the frame cost's draws in every
    window (exact); the decoder's time adds up to its thread clock (within 5%); with --run, each
    thread's time against /proc (the run's snaps.jsonl, within 5%: the decoder, the recorder's
    busy time plus its time between batches, the render thread).

  tools/pass_cost_report.py PASS.csv [--gpu GPU.csv] [--run RUN_DIR] [--windows A-B]
                            [--delta] [--check] [--json OUT]

--check exits 1 when a check fails. --run uses the window between snaps.jsonl's "start" and
"end" snapshots (scratchpad/cpu-profile/run.sh) and its cost.csv for the frame count.
"""
import argparse
import collections
import csv
import json
import re
import sys

GROUPS = ["render_targets", "resolve", "dynamic_state", "pipeline_state", "draw_setup",
          "other_context", "vertex_constants", "pixel_constants", "bool_loop", "textures",
          "samplers", "vertex_fetch"]
STAGES = ["shaders", "pipeline", "render_pass", "texture_sets", "samplers", "constant_set",
          "dynamic", "system_constants"]
SUMS = ["segments", "rdr_cpu_ms", "other_device", "rdr_flush_ms", "rdr_flush_calls", "submitted",
        "runs", "dec_ms", "dec_draws", "dec_skipped", "rec_ms", "rec_draws", "rec_issue_ms",
        "rec_copies", "rec_copy_ms", "rec_empty", "delta_draws", "stage_draws"] + \
       ["same_" + g for g in GROUPS] + ["stage_same_" + s for s in STAGES]
FRAME = ["frames", "frame_draws", "dec_thread_ms", "rec_thread_ms", "rec_idle_ms", "marks",
         "stale_marks"]
TOLERANCE = 0.05


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def read_ledger(path, windows=None, epochs=None):
    """Rows of the windows chosen: {label: {"name", "rt", "sums", "classes": {(cls, layer): sums}}},
    the frame totals, the windows' own checks and the order of the labels."""
    labels = collections.OrderedDict()
    frame = collections.Counter()
    per_window = collections.defaultdict(lambda: collections.Counter())
    split = None
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            w = int(num(r["window"]))
            if windows and not (windows[0] <= w <= windows[1]):
                continue
            if epochs and not (epochs[0] <= num(r["epoch"]) <= epochs[1]):
                continue
            split = r.get("split") == "1"
            cls = r["class"]
            if cls == "frame":
                for k in FRAME:
                    frame[k] += num(r[k])
                per_window[w]["frame_draws"] += num(r["frame_draws"])
                per_window[w]["dec_thread_ms"] += num(r["dec_thread_ms"])
                continue
            label = r["label"]
            e = labels.setdefault(label, {"name": r["name"], "rt": "", "sums": collections.Counter(),
                                          "classes": collections.OrderedDict()})
            if cls == "segment":
                for k in ("segments", "rdr_cpu_ms", "other_device"):
                    e["sums"][k] += num(r[k])
                e["sums"]["order_x_frames"] += num(r["order"]) * num(r["frames"])
                e["sums"]["order_frames"] += num(r["frames"])
                if not e["rt"] and r["rt_surface"] != "00000000":
                    e["rt"] = rt_name(r["rt_surface"], r["rt_color"], r["rt_depth"])
                continue
            c = e["classes"].setdefault((cls, int(num(r["layer"]))), collections.Counter())
            for k in SUMS:
                if k in ("segments", "rdr_cpu_ms", "other_device"):
                    continue
                c[k] += num(r[k])
                e["sums"][k] += num(r[k])
            per_window[w]["issued"] += num(r["rec_draws"]) + num(r["rec_copies"])
            per_window[w]["dec_ms"] += num(r["dec_ms"])
    # In the order of the frame (a label's mean place among the frame's labels).
    ordered = sorted(labels.items(), key=lambda kv: kv[1]["sums"]["order_x_frames"] /
                     max(kv[1]["sums"]["order_frames"], 1))
    return collections.OrderedDict(ordered), frame, per_window, split


def rt_name(surface, color, depth):
    """RB_SURFACE_INFO / RB_COLOR_INFO / RB_DEPTH_INFO as pitch, MSAA, formats, EDRAM bases."""
    s, c, d = int(surface, 16), int(color, 16), int(depth, 16)
    pitch, msaa = s & 0x3FFF, 1 << ((s >> 16) & 3)
    return "%u %ux c%u@%u d%u@%u" % (pitch, msaa, (c >> 16) & 0xF, c & 0xFFF, (d >> 16) & 1,
                                     d & 0xFFF)


def read_gpu(path, epochs=None, windows=None):
    """The GPU profile's pass rows: {label name: {kind: ms a frame}}, averaged over its windows."""
    sums = collections.defaultdict(collections.Counter)
    seen = set()
    frames = 0.0
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            zone = r["zone"]
            if not zone.startswith("pass|"):
                continue
            w = int(num(r["window"]))
            if windows and not (windows[0] <= w <= windows[1]):
                continue
            parts = zone.split("|")
            if len(parts) != 5:
                continue
            if w not in seen:
                seen.add(w)
                frames += num(r["frames"])
            sums[parts[1]][parts[4]] += num(r["ms_per_frame"]) * num(r["frames"])
    return {k: {kind: v / frames for kind, v in kinds.items()} for k, kinds in sums.items()} \
        if frames else {}


def read_proc(run):
    """Per-thread CPU ms and frames between the run's start and end snapshots."""
    with open(run + "/snaps.jsonl") as f:
        snaps = [json.loads(line) for line in f]
    a = next(s for s in snaps if s["label"] == "start")
    b = next(s for s in snaps if s["label"] == "end")
    epochs = []
    with open(run + "/cost.csv", newline="") as f:
        for r in csv.DictReader(f):
            e = num(r.get("epoch"))
            if a["t"] <= e <= b["t"]:
                epochs.append(e)
    frames = max(0, len(epochs) - 1)
    guest = sorted((int(t) for t, v in b["th"].items() if v[0].startswith("XThread")))
    threads = {}
    for tid, rb in b["th"].items():
        ra = a["th"].get(tid, [rb[0], 0])
        name = rb[0]
        if name.startswith("XThread"):
            name = "g%02d" % guest.index(int(tid))
        threads[name] = threads.get(name, 0.0) + (rb[1] - ra[1]) / 1e6
    return threads, frames, (a["t"], b["t"])


def checks(labels, frame, per_window, proc=None, render="g11", split=True):
    """[(name, ok, text)]."""
    out = []
    bad = [w for w, c in per_window.items() if c["frame_draws"] and c["issued"] != c["frame_draws"]]
    out.append(("draws add up", not bad and frame["frames"] > 0,
                "issued draws and resolves of all labels = the frame cost's draws in %d of %d "
                "windows%s" % (len(per_window) - len(bad), len(per_window),
                               (" (first off: window %d)" % min(bad)) if bad else "")))
    dec = sum(e["sums"]["dec_ms"] for e in labels.values())
    clock = frame["dec_thread_ms"]
    ok = clock > 0 and abs(dec - clock) <= TOLERANCE * clock
    out.append(("decoder sums to its clock", ok,
                "decoder ms over the labels %.1f, its thread clock %.1f" % (dec, clock)))
    if proc:
        threads, frames, _ = proc
        fr = frame["frames"]
        if fr and frames:
            pairs = [("GPU Commands", dec)]
            if split:
                pairs.append(("GPU Recorder", sum(e["sums"]["rec_ms"] for e in labels.values()) +
                              frame["rec_idle_ms"]))
            pairs.append((render, sum(e["sums"]["rdr_cpu_ms"] for e in labels.values())))
            for name, ledger in pairs:
                mine = ledger / fr
                theirs = threads.get(name, 0.0) / frames
                ok = theirs > 0 and abs(mine - theirs) <= TOLERANCE * theirs
                if name == "GPU Recorder":
                    # Its time between batches is spinning (CPU) or asleep (not): an upper bound.
                    ok = theirs > 0 and mine >= theirs * (1 - TOLERANCE)
                out.append(("%s against /proc" % name, ok,
                            "ledger %.3f ms a frame, /proc %.3f" % (mine, theirs)))
    return out


def fmt(v, digits=3):
    return ("%%.%df" % digits) % v if v else "-"


def report(labels, frame, per_window, gpu, delta, proc, split, render):
    fr = frame["frames"] or 1
    lines = []
    lines.append("%d frames in %d windows (%s); marks %.0f a frame (%.0f read again)" % (
        frame["frames"], len(per_window), "split" if split else "one command processor thread",
        frame["marks"] / fr, frame["stale_marks"] / fr))
    lines.append("")
    lines.append("ms a frame (draws a frame). rdr = render thread (CPU, in flush points); dec = "
                 "decoder%s; rec = recorder busy; issue/copy = IssueDraw's draws/resolves; "
                 "sub/dec/skip/iss = draws submitted, decoded, skipped by a predicate, issued; gpu "
                 "= GPU profile" % ("" if split else " (the whole command processor)"))
    lines.append("")
    head = ("| label | targets | seg | rdr cpu | rdr flush | dec | rec | issue | copy | sub | dec "
            "draws | skip | iss | gpu |")
    lines.append(head)
    lines.append("|" + " --- |" * (head.count("|") - 1))
    total = collections.Counter()
    for label, e in labels.items():
        s = e["sums"]
        g = gpu.get(e["name"], {}) if gpu else {}
        gms = sum(g.values())
        total.update(s)
        total["gpu"] += gms
        lines.append("| %s | %s | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            e["name"], e["rt"] or "-", s["segments"] / fr, fmt(s["rdr_cpu_ms"] / fr),
            fmt(s["rdr_flush_ms"] / fr), fmt(s["dec_ms"] / fr), fmt(s["rec_ms"] / fr),
            fmt(s["rec_issue_ms"] / fr), fmt(s["rec_copy_ms"] / fr), fmt(s["submitted"] / fr, 0),
            fmt(s["dec_draws"] / fr, 0), fmt(s["dec_skipped"] / fr, 0),
            fmt((s["rec_draws"] + s["rec_copies"]) / fr, 0), fmt(gms) if gpu else "-"))
    lines.append("| **all** | | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        total["segments"] / fr, fmt(total["rdr_cpu_ms"] / fr), fmt(total["rdr_flush_ms"] / fr),
        fmt(total["dec_ms"] / fr), fmt(total["rec_ms"] / fr), fmt(total["rec_issue_ms"] / fr),
        fmt(total["rec_copy_ms"] / fr), fmt(total["submitted"] / fr, 0),
        fmt(total["dec_draws"] / fr, 0), fmt(total["dec_skipped"] / fr, 0),
        fmt((total["rec_draws"] + total["rec_copies"]) / fr, 0),
        fmt(total["gpu"]) if gpu else "-"))
    lines.append("")
    lines.append("By draw class (the render thread's CPU and the GPU are per label only):")
    lines.append("")
    head = "| label | class | rdr flush | dec | rec | issue | sub | dec draws | iss | gpu |"
    lines.append(head)
    lines.append("|" + " --- |" * (head.count("|") - 1))
    for label, e in labels.items():
        for (cls, layer), c in e["classes"].items():
            name = cls + (" L%d" % layer if cls in ("track", "procedural") else "")
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                e["name"], name, fmt(c["rdr_flush_ms"] / fr), fmt(c["dec_ms"] / fr),
                fmt(c["rec_ms"] / fr), fmt(c["rec_issue_ms"] / fr), fmt(c["submitted"] / fr, 0),
                fmt(c["dec_draws"] / fr, 0), fmt((c["rec_draws"] + c["rec_copies"]) / fr, 0),
                "-" if not gpu else "(label)"))
    if gpu:
        lines.append("")
        lines.append("GPU ms a frame by kind of work:")
        lines.append("")
        kinds = sorted({k for g in gpu.values() for k in g})
        head = "| label | " + " | ".join(kinds) + " |"
        lines.append(head)
        lines.append("|" + " --- |" * (len(kinds) + 1))
        for name in [e["name"] for e in labels.values()]:
            if name not in gpu:
                continue
            lines.append("| %s | %s |" % (name, " | ".join(fmt(gpu[name].get(k, 0)) for k in kinds)))
    if delta:
        lines.append("")
        lines.append("Change-mask counter: share of draws with the group or stage unchanged since "
                     "the previous draw (all labels, then the labels with the most draws):")
        lines.append("")
        d = sum(e["sums"]["delta_draws"] for e in labels.values())
        sd = sum(e["sums"]["stage_draws"] for e in labels.values())
        if d:
            lines.append("| groups | " + " | ".join(GROUPS) + " |")
            lines.append("|" + " --- |" * (len(GROUPS) + 1))
            lines.append("| all (%.0f draws a frame) | %s |" % (d / fr, " | ".join(
                "%.1f%%" % (100 * sum(e["sums"]["same_" + g] for e in labels.values()) / d)
                for g in GROUPS)))
            top = sorted(labels.values(), key=lambda e: -e["sums"]["delta_draws"])[:8]
            for e in top:
                n = e["sums"]["delta_draws"]
                if n:
                    lines.append("| %s (%.0f) | %s |" % (e["name"], n / fr, " | ".join(
                        "%.1f%%" % (100 * e["sums"]["same_" + g] / n) for g in GROUPS)))
        if sd:
            lines.append("")
            lines.append("| stages | " + " | ".join(STAGES) + " |")
            lines.append("|" + " --- |" * (len(STAGES) + 1))
            lines.append("| all (%.0f draws a frame) | %s |" % (sd / fr, " | ".join(
                "%.1f%%" % (100 * sum(e["sums"]["stage_same_" + s] for e in labels.values()) / sd)
                for s in STAGES)))
            top = sorted(labels.values(), key=lambda e: -e["sums"]["stage_draws"])[:8]
            for e in top:
                n = e["sums"]["stage_draws"]
                if n:
                    lines.append("| %s (%.0f) | %s |" % (e["name"], n / fr, " | ".join(
                        "%.1f%%" % (100 * e["sums"]["stage_same_" + s] / n) for s in STAGES)))
    results = checks(labels, frame, per_window, proc, render, split)
    lines.append("")
    lines.append("Checks:")
    for name, ok, text in results:
        lines.append("- %s: %s (%s)" % (name, "ok" if ok else "FAILED", text))
    return "\n".join(lines) + "\n", results


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv")
    p.add_argument("--gpu")
    p.add_argument("--run")
    p.add_argument("--render", default="g11", help="the render thread's name in /proc (g11)")
    p.add_argument("--windows")
    p.add_argument("--delta", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--json")
    a = p.parse_args(argv)
    windows = None
    if a.windows:
        m = re.match(r"^(\d+)-(\d+)$", a.windows)
        if not m:
            p.error("--windows A-B")
        windows = (int(m.group(1)), int(m.group(2)))
    proc = read_proc(a.run) if a.run else None
    labels, frame, per_window, split = read_ledger(a.csv, windows, proc[2] if proc else None)
    gpu = read_gpu(a.gpu, windows=None) if a.gpu else {}
    text, results = report(labels, frame, per_window, gpu, a.delta, proc, split, a.render)
    sys.stdout.write(text)
    if a.json:
        with open(a.json, "w") as f:
            json.dump({"frames": frame["frames"], "labels": {
                e["name"]: {"rt": e["rt"], "sums": e["sums"],
                            "classes": {"%s/%d" % k: v for k, v in e["classes"].items()}}
                for e in labels.values()}, "gpu": gpu,
                "checks": [{"name": n, "ok": ok, "text": t} for n, ok, t in results]}, f, indent=1)
    if a.check and not all(ok for _, ok, _ in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
