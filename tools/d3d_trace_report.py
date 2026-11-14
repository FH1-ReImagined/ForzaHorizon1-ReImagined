#!/usr/bin/env python3
"""Tables from a D3D/PM4 trace (docs/native-renderer.md "Tools").

  d3d_trace_report.py PM4.txt [--blocks A-B] [--opcodes] [--top N] [--json OUT]

PM4.txt is the file the GPU plugin's PM4 trace writes (FH1_PM4_STATS, ReIGlue built with
REXGLUE_FH1_PM4_TRACE) in a game built with FORZA_D3D_TRACE: one block every
FH1_PM4_STATS_EVERY swaps. --blocks picks blocks (0-based, inclusive; default all but the first,
which holds the load). Function names and roles come from
forza-recomp/src/debug/d3d_trace_hooks.inc.

Shares are of what the command processor read (packets predicated off, padding and tile replays
included), not CPU or GPU time.
"""
import argparse
import collections
import json
import os
import re
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HOOKS = os.path.join(ROOT, "forza-recomp", "src", "debug", "d3d_trace_hooks.inc")
CLASSES = {0: "(gap: untagged in a D3D buffer)", 249: "(foreign buffer with SET_CONSTANT)",
           250: "(foreign buffer)", 251: "(primary ring)", 252: "(no guest address)"}
OPCODES = {0x10: "NOP", 0x21: "REG_RMW", 0x22: "DRAW_INDX", 0x23: "VIZ_QUERY", 0x25: "SET_STATE",
           0x26: "WAIT_FOR_IDLE", 0x27: "IM_LOAD", 0x2B: "IM_LOAD_IMMEDIATE", 0x2D: "SET_CONSTANT",
           0x2E: "LOAD_CONSTANT_CONTEXT", 0x2F: "LOAD_ALU_CONSTANT", 0x36: "DRAW_INDX_2",
           0x37: "INDIRECT_BUFFER_PFD", 0x3B: "INVALIDATE_STATE", 0x3C: "WAIT_REG_MEM",
           0x3D: "MEM_WRITE", 0x3E: "REG_TO_MEM", 0x3F: "INDIRECT_BUFFER", 0x44: "COND_EXEC",
           0x45: "COND_WRITE", 0x46: "EVENT_WRITE", 0x48: "ME_INIT", 0x4A: "SET_SHADER_BASES",
           0x4B: "SET_BIN_BASE_OFFSET", 0x50: "SET_BIN_MASK", 0x51: "SET_BIN_SELECT",
           0x54: "INTERRUPT", 0x55: "SET_CONSTANT2", 0x56: "SET_SHADER_CONSTANTS",
           0x58: "EVENT_WRITE_SHD", 0x59: "EVENT_WRITE_CFL", 0x5A: "EVENT_WRITE_EXT",
           0x5B: "EVENT_WRITE_ZPD", 0x5C: "WAIT_UNTIL_READ", 0x5E: "CONTEXT_UPDATE",
           0x60: "SET_BIN_MASK_LO", 0x61: "SET_BIN_MASK_HI", 0x62: "SET_BIN_SELECT_LO",
           0x63: "SET_BIN_SELECT_HI", 0x64: "XE_SWAP", 129: "type-1", 130: "type-0 ALU constants",
           131: "type-0 fetch constants", 132: "type-0 registers"}
DRAWS = (0x22, 0x36)


def hooks():
    names = {}
    for line in open(HOOKS):
        m = re.match(r'D3D_TRACE_HOOK\((\d+), (\w+), (\w+), "(.*)"\)', line)
        if m:
            names[int(m.group(1))] = (m.group(2), m.group(4))
    return names


def parse(path):
    blocks, cur = [], None
    for line in open(path):
        line = line.rstrip("\n")
        if line.startswith("BLOCK"):
            kv = dict(x.split("=") for x in line.split()[1:])
            cur = {"frames": int(kv["frames"]), "game": kv.get("game", "traced"), "T": {}, "TO": {},
                   "G": {}, "P": [], "F": [], "IB": {}, "LR": ""}
        elif cur is None:
            continue
        elif line.startswith(("T ", "TO ")):
            p = line.split()
            pk = collections.Counter({int(k): int(v) for k, v in (x.split(":") for x in p[4:])})
            cur[p[0]][int(p[1])] = (int(p[2][3:]) + int(p[3][4:]), pk)
        elif line.startswith("G "):
            p = line.split()
            cur["G"][int(p[1])] = {k: v for k, v in (x.split("=") for x in p[2:])}
        elif line.startswith("IB "):
            for x in line.split()[1:]:
                k, v = x.split("=")
                cur["IB"][k] = tuple(int(n) for n in v.split("/")) if "/" in v else int(v)
        elif line.startswith("P "):
            p = line.split()
            cur["P"].append((p[1], int(p[2]), int(p[3][5:]), int(p[4][3:])))
        elif line.startswith("F "):
            cur["F"].append(line[2:])
        elif line.startswith("LR"):
            cur["LR"] = line[2:]
        elif line == "END":
            blocks.append(cur)
            cur = None
    return blocks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pm4")
    ap.add_argument("--blocks")
    ap.add_argument("--opcodes", action="store_true", help="packets per frame by opcode and class")
    ap.add_argument("--top", type=int, default=25, help="rows of the function tables (0: all)")
    ap.add_argument("--json")
    a = ap.parse_args()
    names = hooks()
    blocks = parse(a.pm4)
    if not blocks:
        sys.exit("no complete block in %s" % a.pm4)
    lo, hi = (1 if len(blocks) > 1 else 0), len(blocks) - 1
    if a.blocks:
        lo, hi = (int(x) for x in a.blocks.split("-"))
    sel = blocks[lo:hi + 1]
    frames = sum(b["frames"] for b in sel)
    if any(b["game"] != "traced" for b in sel):
        print("note: the game was not a FORZA_D3D_TRACE build in some blocks: no tags, no calls")

    def total(key):
        t = collections.defaultdict(lambda: [0, collections.Counter()])
        for b in sel:
            for tag, (dw, pk) in b[key].items():
                t[tag][0] += dw
                t[tag][1].update(pk)
        return t

    inner, outer = total("T"), total("TO")
    tdw = sum(v[0] for v in inner.values()) or 1
    tpk = sum(sum(v[1].values()) for v in inner.values()) or 1
    draws = sum(v[1][d] for v in inner.values() for d in DRAWS)
    calls = collections.defaultdict(collections.Counter)
    for b in sel:
        for i, kv in b["G"].items():
            for k in ("calls", "outer", "bytes", "outer_bytes", "disc"):
                calls[i][k] += int(kv[k])
    out = {"blocks": [lo, hi], "frames": frames, "dwords_per_frame": tdw / frames,
           "packets_per_frame": tpk / frames, "draw_packets_per_frame": draws / frames}
    print("blocks %d-%d of %d, %d frames: %.0f dwords, %.0f packets, %.0f draw packets a frame"
          % (lo, hi, len(blocks), frames, tdw / frames, tpk / frames, draws / frames))

    # Classes.
    print("\nWho wrote what the command processor read:")
    cls = collections.Counter()
    for tag, (dw, pk) in inner.items():
        c = "written by D3D (tagged)" if 1 <= tag <= 247 else CLASSES.get(tag, str(tag))
        cls[c, "dw"] += dw
        cls[c, "pk"] += sum(pk.values())
        cls[c, "draws"] += sum(pk[d] for d in DRAWS)
    out["classes"] = {}
    for c in sorted({k[0] for k in cls}, key=lambda c: -cls[c, "dw"]):
        print("  %-36s %5.1f%% dwords %5.1f%% packets %5.1f%% draws"
              % (c, 100 * cls[c, "dw"] / tdw, 100 * cls[c, "pk"] / tpk, 100 * cls[c, "draws"] / max(draws, 1)))
        out["classes"][c] = {"dw_pct": 100 * cls[c, "dw"] / tdw, "pk_pct": 100 * cls[c, "pk"] / tpk}

    def table(title, counts, use_outer_calls):
        print("\n" + title)
        print("  %3s %-13s %9s %9s %7s %7s %8s  %s" % ("id", "function", "calls/f", "outer/f", "dw%", "pk%", "draws/f", "role"))
        rows = []
        for tag in set(counts) | set(calls):
            if not 1 <= tag <= 247:
                continue
            dw, pk = counts.get(tag, (0, collections.Counter()))
            g = calls.get(tag, collections.Counter())
            rows.append((tag, dw, pk, g))
        rows.sort(key=lambda r: (-r[1], -r[3]["calls"]))
        res = []
        for tag, dw, pk, g in rows:
            if not dw and g["calls"] / frames < 1:
                continue
            if a.top and len(res) >= a.top:
                break
            fn, role = names.get(tag, ("?", ""))
            print("  %3d %-13s %9.1f %9.1f %7.2f %7.2f %8.1f  %s"
                  % (tag, fn, g["calls"] / frames, g["outer"] / frames, 100 * dw / tdw,
                     100 * sum(pk.values()) / tpk, sum(pk[d] for d in DRAWS) / frames, role))
            res.append({"id": tag, "function": fn, "role": role, "calls_per_frame": g["calls"] / frames,
                        "outer_calls_per_frame": g["outer"] / frames, "dw_pct": 100 * dw / tdw,
                        "pk_pct": 100 * sum(pk.values()) / tpk})
        return res

    out["entry_points"] = table("Entry points (the outermost wrapped call; what each one's calls made the CP read):", outer, True)
    out["writers"] = table("Writers (the innermost wrapped call):", inner, False)

    # Indirect buffers and their parents.
    ib = collections.Counter()
    for b in sel:
        for k, v in b["IB"].items():
            if isinstance(v, tuple):
                ib[k, "runs"] += v[0]
                ib[k, "dw"] += v[1]
            else:
                ib[k] += v
    print("\nIndirect buffers a frame (runs, dwords): " + ", ".join(
        "%s %.0f / %.0f" % (k, ib[k, "runs"] / frames, ib[k, "dw"] / frames)
        for k in ("written", "mixed", "foreign", "setconst")) + ", nested %.0f" % (ib["nested"] / frames))
    par = collections.Counter()
    for b in sel:
        for kind, tag, runs, dw in b["P"]:
            par[kind, tag, "runs"] += runs
            par[kind, tag, "dw"] += dw
    print("Foreign buffers by the writer of the INDIRECT_BUFFER packet that ran them:")
    out["foreign_parents"] = []
    for kind, tag in sorted({k[:2] for k in par}, key=lambda k: -par[k[0], k[1], "dw"]):
        who = names.get(tag, (CLASSES.get(tag, "?"), ""))
        print("  %-9s %3d %-36s %8.1f runs %9.0f dwords a frame"
              % (kind, tag, " ".join(who).strip(), par[kind, tag, "runs"] / frames, par[kind, tag, "dw"] / frames))
        out["foreign_parents"].append({"class": kind, "parent": tag, "parent_name": who[0],
                                       "runs_per_frame": par[kind, tag, "runs"] / frames,
                                       "dwords_per_frame": par[kind, tag, "dw"] / frames})
    print("Largest foreign buffers of the last block (address, dwords, runs, depth, SET_CONSTANT, parent):")
    for f in sel[-1]["F"][:10]:
        print("  " + f)

    if a.opcodes:
        print("\nPackets a frame by opcode and class:")
        agg = collections.defaultdict(collections.Counter)
        for tag, (dw, pk) in inner.items():
            agg["D3D" if 1 <= tag <= 247 else CLASSES.get(tag, str(tag))].update(pk)
        for op in sorted({k for c in agg.values() for k in c}):
            print("  %-24s " % OPCODES.get(op, "0x%02X" % op) + "  ".join(
                "%s %.1f" % (c, agg[c][op] / frames) for c in sorted(agg) if agg[c][op]))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
