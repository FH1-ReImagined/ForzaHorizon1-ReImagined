#!/usr/bin/env python3
"""Converts every model command list of a track archive and proves the result, offline
(docs/native-renderer.md, "M4: the model lists").

  tools/native_list_check.py ARCHIVE [ARCHIVE ...] [--json OUT] [--jobs N] [--distinct]
      [--stride N] [--no-refusals] [--expect-reduction A,B,C,D,E] [--unxmem PATH]
      [--checker PATH | --build DIR]

  ARCHIVE   a track's bin.zip from the player's own copy of the game (read only), a DLC track
            zip, or the archive tools/fm4_pvs.py wrote for an FM4 circuit

Each .rmb.bin member is read (tools/rmb_lists.py) and its lists are handed to the checker
(tools/native_list_check.cpp, built here with $CXX or g++), which
  - counts what the lists hold: packets, registers, fixup records, places with sub-code 62/63;
  - converts each list with forza-recomp/src/platform/native_list.h, or counts the refusal;
  - checks the converter's slots against the game's own fixup records, and its tile-bound
    packets against scene_msaa's;
  - runs both views of each template beside a plain PM4 interpreter: the state before every
    draw, every draw's arguments and the state after the list, for each tile's bin select,
    without tiling, with a bin mask that excludes the bin, and untiled (in the mode Untiled()
    names for the words); once with the words as extracted and once with every place written;
  - checks the key: the same after fixups, with the fixed-up flag and untiled, another when a
    static word changes;
  - counts the distinct per-draw parts the lists reduce to.
The report holds counts only: no word of a list and no member name. Nothing is written but the
report (and the checker's binary, into --build or a temporary folder).

--distinct reads one member per (CRC, size), a quick pass; without it every member is read, the
archive's repeats too. Exit status 1 if anything disagrees (or, with --no-refusals, is refused).
"""
import argparse
import json
import multiprocessing
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fm4_track_mod  # noqa: E402  (member_data: the archives' members, LZX or stored)
import rmb_lists  # noqa: E402

STREAM_MAGIC = b"NLC1"
_worker = {}


def list_frames(model):
    """The checker's input for a model's lists (see native_list_check.cpp)."""
    out = []
    for lst in model.lists:
        header = lst.header.ljust(rmb_lists.HEADER_BYTES, b"\0")
        table = lst.table[:len(lst.table) // 4 * 4]
        area_c = lst.area_c[:len(lst.area_c) // 4 * 4]
        pm4 = lst.pm4[:len(lst.pm4) // 4 * 4]
        out.append(struct.pack("<8I", 1, lst.group, len(table) // 4, len(area_c) // 4,
                               len(pm4) // 4, len(lst.records) // 8, len(lst.entries) // 8,
                               (1 if lst.marker else 0) | (2 if lst.trailer else 0)))
        out += [header, table, area_c, pm4, lst.records, lst.entries]
    return b"".join(out)


def _init(unxmem, scratch):
    fm4_track_mod.UNXMEM = unxmem
    _worker["tmp"] = tempfile.mkdtemp(dir=scratch)  # one per reading process
    _worker["zips"] = {}


def _read(job):
    """One member: (bytes of it, lists found or -1, the checker's frames)."""
    path, index = job
    if path not in _worker["zips"]:
        _worker["zips"][path] = (zipfile.ZipFile(path), open(path, "rb"))
    zf, fh = _worker["zips"][path]
    info = zf.infolist()[index]
    data = fm4_track_mod.member_data(path, zf, info, fh, _worker["tmp"])
    model = rmb_lists.parse(data)
    if model is None:
        return len(data), -1, b""
    return len(data), len(model.lists), list_frames(model)


def members(path, distinct, stride):
    """The .rmb.bin members to read: (index in the archive's directory), and the counts."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
    picked, seen = [], set()
    count = 0
    for index, info in enumerate(infos):
        if not info.filename.lower().endswith(".rmb.bin"):
            continue
        count += 1
        key = (info.CRC, info.file_size)
        if key in seen and distinct:
            continue
        seen.add(key)
        picked.append(index)
    return picked[::stride], count, len(seen)


def build_checker(directory):
    compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        raise SystemExit("native_list_check: no C++ compiler (set CXX, or pass --checker)")
    root = os.path.dirname(HERE)
    binary = os.path.join(directory, "native_list_check" + (".exe" if os.name == "nt" else ""))
    subprocess.run([compiler, "-std=c++20", "-O2", "-I", os.path.join(root, "forza-recomp", "src"),
                    "-I", os.path.join(root, "forza-recomp", "tests"),
                    os.path.join(HERE, "native_list_check.cpp"), "-o", binary], check=True)
    return binary


def run(archives, checker, unxmem, jobs=1, distinct=False, stride=1):
    """The checker's counts for the archives' lists, with the reader's own under "archive"."""
    reader = dict(archives=len(archives), members=0, distinct_members=0, read=0, unparsed=0,
                  without_lists=0, bytes=0, lists=0)
    work = []
    for path in archives:
        picked, count, unique = members(path, distinct, stride)
        reader["members"] += count
        reader["distinct_members"] += unique
        work += [(path, index) for index in picked]
    process = subprocess.Popen([checker, "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    counts = None
    try:
        counts = _feed(process, work, reader, unxmem, jobs)
    except BrokenPipeError:  # the checker stopped reading: it has said why
        pass
    finally:
        try:
            process.stdin.close()
        except BrokenPipeError:
            pass
        process.stdout.close()
    if process.wait() != 0 or counts is None:
        raise SystemExit("native_list_check: the checker failed")
    counts["archive"] = reader
    return counts


def _feed(process, work, reader, unxmem, jobs):
    process.stdin.write(STREAM_MAGIC)

    def take(result):
        size, lists, frames = result
        reader["read"] += 1
        reader["bytes"] += size
        if lists < 0:
            reader["unparsed"] += 1
        else:
            reader["without_lists"] += lists == 0
            reader["lists"] += lists
            process.stdin.write(frames)

    with tempfile.TemporaryDirectory(prefix="native-list-check-") as scratch:
        if jobs > 1:
            with multiprocessing.Pool(jobs, _init, (unxmem, scratch)) as pool:
                for result in pool.imap(_read, work, chunksize=8):
                    take(result)
        else:
            _init(unxmem, scratch)
            try:
                for job in work:
                    take(_read(job))
            finally:
                for zf, fh in _worker["zips"].values():
                    zf.close()
                    fh.close()
    process.stdin.close()
    text = process.stdout.read().decode("ascii")
    return json.loads(text) if text else None


PM4_NAMES = {0x10: "NOP", 0x22: "DRAW_INDX", 0x27: "IM_LOAD", 0x2D: "SET_CONSTANT",
             0x2F: "LOAD_ALU_CONSTANT", 0x36: "DRAW_INDX_2", 0x3C: "WAIT_REG_MEM",
             0x55: "SET_CONSTANT2", 0x60: "SET_BIN_MASK_LO", 0x61: "SET_BIN_MASK_HI"}
FIXUP_NAMES = {1: "vertex buffer", 2: "index buffer", 3: "texture", 4: "viewport", 5: "surfaces",
               6: "scissor", 7: "constant block (7)", 8: "constant block", 9: "vertex shader",
               10: "pixel shader"}
PASSES = ("depth pre-pass", "256x256 pass 1", "256x256 pass 2", "scene, two tiles", "scene",
          "scene, one pass", "shadow map")


def failures(c, no_refusals=False, expect_reduction=None):
    """What does not hold in the counts: [] when the proof is whole."""
    out = []
    census, conv, key = c["census"], c["conversion"], c["key"]
    converted, tiled = conv["converted"], conv["tiled"]
    if c["archive"]["unparsed"]:
        out.append("%d member(s) without a readable list table" % c["archive"]["unparsed"])
    if census["lists"] != c["archive"]["lists"]:
        out.append("the checker saw %d lists of %d" % (census["lists"], c["archive"]["lists"]))
    for name in ("bad_walks", "table_bad", "marker_missing", "trailer_missing"):
        if census[name]:
            out.append("%s: %d" % (name, census[name]))
    refused = sum(conv["refused"].values())
    if converted + refused != census["lists"]:
        out.append("converted + refused is not the lists")
    if no_refusals and refused:
        out.append("%d list(s) refused" % refused)
    if conv["record_places_agree"] != conv["record_places_checked"]:
        out.append("slots differ from the game's records on %d place(s)"
                   % (conv["record_places_checked"] - conv["record_places_agree"]))
    if conv["slots_no_record_names"]:
        out.append("%d slot(s) no record names" % conv["slots_no_record_names"])
    if conv["tile_bound_agree"] != tiled:
        out.append("tile-bound packets differ on %d list(s)" % (tiled - conv["tile_bound_agree"]))
    if conv["untiled_told"] != tiled:
        out.append("Untiled() does not tell tiled from untiled on %d list(s)"
                   % (tiled - conv["untiled_told"]))
    for name, a in c["proof"].items():
        want = tiled if "/untiled/" in name else converted
        if a["runs"] != want:
            out.append("%s: %d runs of %d" % (name, a["runs"], want))
        for part in ("all", "interpreter", "draw_count", "ops_state", "ops_draws", "ops_end",
                     "view_state", "view_draws", "view_end"):
            if a[part] != a["runs"]:
                out.append("%s: %s holds on %d of %d" % (name, part, a[part], a["runs"]))
    for name, want in (("same_after_fixups", converted), ("same_template_after_fixups", converted),
                       ("same_template_with_0x2000", converted), ("same_untiled", tiled),
                       ("untiled_refused_as_input", tiled), ("sees_changed_pm4_word", converted),
                       ("sees_changed_table_word", converted), ("sees_changed_mask", converted)):
        if key[name] != want:
            out.append("key: %s on %d of %d" % (name, key[name], want))
    if expect_reduction:
        got = [c["reduction"][k] for k in ("register_deltas", "fetch_templates", "exit_sets",
                                           "vertex_constant_blobs", "pixel_constant_blobs")]
        if got != list(expect_reduction):
            out.append("reduction %s, expected %s" % (got, list(expect_reduction)))
    return out


def report(c, problems):
    a, census, conv = c["archive"], c["census"], c["conversion"]
    lines = []
    say = lines.append
    say("archives %d: %d .rmb.bin members (%d distinct by CRC and size), %d read, %d bytes"
        % (a["archives"], a["members"], a["distinct_members"], a["read"], a["bytes"]))
    say("  without a readable list table %d, without lists %d"
        % (a["unparsed"], a["without_lists"]))
    say("lists %d, %d dwords of PM4; walks that do not end on the list's end %d"
        % (census["lists"], census["dwords"], census["bad_walks"]))
    say("  per pass group: lists / dwords / draws / masks all ones, FFFFFFF7 in the third, other")
    for g in sorted(census["group_lists"], key=int):
        say("    %2s (%s, layer %d): %d / %d / %d / %d, %d, %d"
            % (g, PASSES[int(g) // 3], int(g) % 3, census["group_lists"][g],
               census["group_dwords"][g], census["group_draws"].get(g, 0),
               census["group_masks_all_ones"].get(g, 0),
               census["group_masks_f7"].get(g, 0), census["group_masks_other"].get(g, 0)))
    say("  header: resource type %s, flags %s, segments %s"
        % (census["resource_type"], census["flags"], census["segments"]))
    say("  packets: type 0 %d, type 1 %d, type 2 %d, type 3 %d"
        % (census["type0"], census["type1"], census["type2"], census["type3"]))
    say("    type 3 by opcode (predicated): " + ", ".join(
        "%s %d (%d)" % (PM4_NAMES.get(int(k, 16), k), v, census["type3_predicated"].get(k, 0))
        for k, v in census["type3_opcodes"].items()))
    say("    one-register (bit 15) type-0 packets by register: %s"
        % census["one_register_packets"])
    say("    registers written by type-0 packets: %d distinct" % len(census["registers"]))
    say("      " + " ".join("%s:%d" % kv for kv in census["registers"].items()))
    say("  draws %d: predicated %d, 32-bit indices %d, primitive types %s, source select %s"
        % (census["draws"], census["draws_predicated"], census["draws_32bit_indices"],
           census["primitive_types"], census["source_select"]))
    say("  fixup records by type: %s" % ", ".join(
        "%s %d" % (FIXUP_NAMES.get(int(k), k), v) for k, v in census["record_types"].items()))
    say("  buffer entries by type: %s" % census["entry_types"])
    say("  table entries %d; with sub-code 62 or 63: %d, in %d lists"
        % (census["table_entries"], census["subcode_62_63_entries"], census["subcode_62_63_lists"]))
    say("    entries of a shader load's address %d; their value word not a multiple of 4: %d"
        % (census["shader_address_entries"], census["shader_address_values_unaligned"]))
    refused = sum(conv["refused"].values())
    say("converted %d of %d, refused %d %s"
        % (conv["converted"], census["lists"], refused, conv["refused"] if refused else ""))
    if refused:
        say("  refused per pass group: %s" % conv["group_refused"])
    if conv["converted"]:
        dwords = census["dwords"] or 1
        say("  %d distinct keys; templates %d words (pool %d, %d ops of 4, %d register runs of 4, "
            "%d slots of 4); conversion %.1f ns a dword"
            % (conv["distinct_keys"], conv["template_words"], conv["pool_words"], conv["ops"],
               conv["delta_runs"], conv["slots"], conv["convert_ns"] / dwords))
        say("  slots %d: %s" % (conv["slots"], ", ".join(
            "%s %d" % (FIXUP_NAMES.get(int(k), k), v) for k, v in conv["slots_by_type"].items())))
        say("  against the game's records: %d of %d places are a slot of the record's type and "
            "length; slots no record names: %d"
            % (conv["record_places_agree"], conv["record_places_checked"],
               conv["slots_no_record_names"]))
        say("  lists with the same slots when the buffer fixups (area C) are left out: %d"
            % conv["same_slots_without_area_c"])
        say("  lists scene_msaa unties: %d; tile-bound packets as scene_tiling.h's on %d "
            "(%d packets); Untiled() tells the tiled words from the untiled on %d"
            % (conv["tiled"], conv["tile_bound_agree"], conv["tile_bound_packets"],
               conv["untiled_told"]))
        say("against the PM4 interpreter (runs: all agree / state before every draw, ops and draw "
            "view / draws compared):")
        for name, p in c["proof"].items():
            if p["runs"]:
                say("  %-38s %d: %d / %d, %d / %d"
                    % (name, p["runs"], p["all"], p["ops_state"], p["view_state"], p["draws"]))
        k = c["key"]
        say("key: same after fixups %d, same template after fixups %d and with flag 0x2000 %d; "
            "untiled: same key %d, refused as input %d"
            % (k["same_after_fixups"], k["same_template_after_fixups"],
               k["same_template_with_0x2000"], k["same_untiled"], k["untiled_refused_as_input"]))
        say("  another key after a changed PM4 word %d, table word %d, mask %d"
            % (k["sees_changed_pm4_word"], k["sees_changed_table_word"], k["sees_changed_mask"]))
        r = c["reduction"]
        say("distinct per-draw parts: register deltas %d, fetch templates %d, exit sets %d, vertex "
            "constant blobs %d, pixel constant blobs %d"
            % (r["register_deltas"], r["fetch_templates"], r["exit_sets"],
               r["vertex_constant_blobs"], r["pixel_constant_blobs"]))
        o = c["draws_without_vertex_shader"]
        say("draws that load no vertex shader: %d in %d lists (per group %s): one state register "
            "written %d, that register VGT_INDX_OFFSET %d, no fetch constant %d, 32-bit indices "
            "%d, first draw of its list %d, shader and vertex buffer carried from the draw before "
            "%d"
            % (o["draws"], o["lists"], o["by_group"], o["one_state_register"],
               o["only_vgt_indx_offset"],
               o["no_fetch_constant"], o["32bit_indices"], o["first_draw_of_list"],
               o["shader_and_vertex_buffer_carried"]))
    say("RESULT: " + ("ok" if not problems else "FAIL"))
    for p in problems:
        say("  " + p)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archives", nargs="+")
    ap.add_argument("--json", help="also write the counts as JSON")
    ap.add_argument("--jobs", type=int, default=1, help="processes reading members")
    ap.add_argument("--distinct", action="store_true", help="one member per (CRC, size)")
    ap.add_argument("--stride", type=int, default=1, help="every Nth member (a sample)")
    ap.add_argument("--no-refusals", action="store_true", help="a refused list fails the run")
    ap.add_argument("--expect-reduction", help="the five distinct counts the run must give")
    ap.add_argument("--unxmem", default=os.path.join(HERE, "unxmem"),
                    help="the LZX decoder (tools/unxmem.c); without it Python decodes, slowly")
    ap.add_argument("--checker", help="a built tools/native_list_check.cpp")
    ap.add_argument("--build", help="where to build the checker (default: a temporary folder)")
    args = ap.parse_args(argv)
    expect = [int(x) for x in args.expect_reduction.split(",")] if args.expect_reduction else None
    temporary = None
    try:
        checker = args.checker
        if not checker:
            directory = args.build
            if not directory:
                directory = temporary = tempfile.mkdtemp(prefix="native-list-check-")
            os.makedirs(directory, exist_ok=True)
            checker = build_checker(directory)
        counts = run(args.archives, checker, args.unxmem, args.jobs, args.distinct, args.stride)
    finally:
        if temporary:
            shutil.rmtree(temporary, ignore_errors=True)
    problems = failures(counts, args.no_refusals, expect)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(counts, f, indent=1, sort_keys=True)
    print(report(counts, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
