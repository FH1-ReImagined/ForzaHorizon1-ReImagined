#!/usr/bin/env python3
"""Turn the SDK's stored Vulkan pipeline descriptions into a list we can ship, and build the
shader prebuild from it (#226, docs/shader_prebuild.md).

A pipeline storage file (<title>.fbo.vk.xpso / .fsi.vk.xpso) holds, per pipeline, the vertex
and pixel shader hashes, their translation modification bits and the render state. That is all
the list keeps: the render state as its decoded bit fields (`fields`, FIELDS order), which mean
the same whatever compiler laid out the stored bits. Shader microcode stays in the player's game
files: the list only says how to find each shader there (tools/fxobj_shaders.py):
  "file"   the shader is a blob in the game files with exactly this hash;
  "bound"  a vertex shader as the game's D3D runtime bound it to a vertex layout and a
           pixel shader: the blob with hash `base`, plus per changed instruction only
           the bound fields: vertex fetch layout (fetch slot, format, stride, offset,
           swizzle; FETCH_LAYOUT_MASK), export destination (EXPORT_MASK), or "nop"
           for an export the pixel shader does not read (replaced by ALU_NOP);
  "image"  microcode with no blob header in the executable image (the D3D library's own
           shaders, #614): `size` bytes at one of `offsets` (one per known executable image:
           Title Update 4, the disc), the one whose XXH3 is the hash;
  "missing" not found in the game files (engine/runtime shaders): built in-game as now.

Usage:
  pipeline_list.py make XPSO... [--xsh XSH]... [--sources SRC]... -o LIST.json
  pipeline_list.py merge LIST... [--mixed-versions] -o LIST.json
  pipeline_list.py check LIST.json        verify it holds only hashes, fields and
                                          layout values (no microcode)
  pipeline_list.py stats XPSO...          version, layout, count, and triangle fans
                                          (records every warm-up rejects; see the doc)
  pipeline_list.py prebuild LIST.json --sources SRC... --out DIR
                                          what the launcher's "Prebuild shaders" writes to
                                          <cache root>/shaders/prebuild: the listed shaders
                                          rebuilt from the player's files (.xsh) and the
                                          pipelines for the SDK (.xpl, patch 0113)
  pipeline_list.py write-storage LIST.json --sources SRC... --out DIR
                                          experiment: rebuild the listed shaders from the
                                          player's files and write SDK storage files
                                          (.xsh, .xpso) that the warm-up loads as is
--xsh and --sources (game data directories, executable image) enable shader recipes.
"""
import argparse
import json
import os
import re
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xxh3 import xxh3_64  # noqa: E402
from fxobj_shaders import (EXPORT_MASK, FETCH_LAYOUT_MASK, apply_binding,  # noqa: E402
                           derive_binding, patch_size)

# Format 2: each pipeline's render state is `fields`, the decoded bit fields in FIELDS order
# (the SDK's pipeline_prebuild_file.h reads them in the same order). Format 1 (phase 1) had them
# as named dicts; merge still reads it.
LIST_FORMAT = 2
XPSO_MAGIC = 0x53504558  # 'XEPS'
XPSO_FORMAT2 = 2

# PipelineDescription (sdk/include/rex/graphics/vulkan/pipeline_cache.h) after the four
# u64 (VS hash, VS modification, PS hash, PS modification) and the u32 RenderPassKey.
# Packed bit fields: Clang/GCC (Linux, macOS) pack them back to back (58-byte
# description); MSVC (Windows) starts the 13 bits after the first 32 in a new u32
# (60 bytes). The bit positions are the same; only the padding differs.
# (name, bits, valid values: an enum may have fewer values than its bits hold)
STATE_FIELDS = [
    ("geometry_shader", 2, 4), ("primitive_topology", 3, 8), ("primitive_restart", 1, 2),
    ("tessellation_mode", 2, 3), ("depth_clamp_enable", 1, 2), ("polygon_mode", 2, 3),
    ("cull_front", 1, 2), ("cull_back", 1, 2), ("front_face_clockwise", 1, 2),
    ("rasterizer_discard", 1, 2), ("depth_write_enable", 1, 2), ("depth_compare_op", 3, 8),
    ("stencil_test_enable", 1, 2), ("stencil_front_fail_op", 3, 8),
    ("stencil_front_pass_op", 3, 8), ("stencil_front_depth_fail_op", 3, 8),
    ("stencil_front_compare_op", 3, 8), ("stencil_back_fail_op", 3, 8),
    ("stencil_back_pass_op", 3, 8), ("stencil_back_depth_fail_op", 3, 8),
    ("stencil_back_compare_op", 3, 8), ("sample_rate_shading", 1, 2),
]
RENDER_PASS_FIELDS = [
    ("msaa_samples", 2, 3), ("depth_and_color_used", 5, 32), ("depth_format", 1, 2),
    ("color_0_view_format", 4, 16), ("color_1_view_format", 4, 16),
    ("color_2_view_format", 4, 16), ("color_3_view_format", 4, 16),
    ("color_rts_use_transfer_formats", 1, 2),
]
RT_FIELDS = [
    ("src_color_blend_factor", 4, 15), ("dst_color_blend_factor", 4, 15),
    ("color_blend_op", 3, 5), ("src_alpha_blend_factor", 4, 15),
    ("dst_alpha_blend_factor", 4, 15), ("alpha_blend_op", 3, 5), ("color_write_mask", 4, 16),
]
FIELDS = (RENDER_PASS_FIELDS + STATE_FIELDS +
          [(f"rt{i}_{n}", w, v) for i in range(4) for n, w, v in RT_FIELDS])
LAYOUTS = {"clang": 58, "msvc": 60}
TOPOLOGY_TRIANGLE_FAN = 5

# The SDK's prebuild list (pipeline_prebuild_file.h): <title>.<api>.vk.xpl.
XPL_MAGIC = 0x4C504558  # 'XEPL'
XPL_FORMAT = 1
XPL_RECORD = 96
PARTITION_FLAGS = {"main": 1, "ui": 2, "scene": 4}


def unpack_bits(value, fields):
    out, pos = [], 0
    for _name, width, _n in fields:
        out.append((value >> pos) & ((1 << width) - 1))
        pos += width
    return out


def pack_bits(values, fields):
    v, pos = 0, 0
    for value, (_name, width, _n) in zip(values, fields):
        v |= (value & ((1 << width) - 1)) << pos
        pos += width
    return v


def decode_description(d, layout):
    vs, vsm, ps, psm, rpk = struct.unpack_from("<QQQQI", d, 0)
    if layout == "clang":
        state = int.from_bytes(d[36:42], "little")
        rt_at = 42
    else:
        state = int.from_bytes(d[36:40], "little") | (int.from_bytes(d[40:44], "little") << 32)
        rt_at = 44
    fields = unpack_bits(rpk, RENDER_PASS_FIELDS) + unpack_bits(state, STATE_FIELDS)
    for i in range(4):
        fields += unpack_bits(struct.unpack_from("<I", d, rt_at + 4 * i)[0], RT_FIELDS)
    return {"vs": f"{vs:016X}", "vs_mod": f"{vsm:016X}", "ps": f"{ps:016X}",
            "ps_mod": f"{psm:016X}", "fields": fields}


def encode_description(p, layout):
    """Native PipelineDescription bytes (for re-creating a storage file locally)."""
    f = p["fields"]
    nrp, nst = len(RENDER_PASS_FIELDS), len(STATE_FIELDS)
    head = struct.pack("<QQQQI", int(p["vs"], 16), int(p["vs_mod"], 16), int(p["ps"], 16),
                       int(p["ps_mod"], 16), pack_bits(f[:nrp], RENDER_PASS_FIELDS))
    state = pack_bits(f[nrp:nrp + nst], STATE_FIELDS)
    if layout == "clang":
        mid = state.to_bytes(6, "little")
    else:
        mid = (state & 0xFFFFFFFF).to_bytes(4, "little") + (state >> 32).to_bytes(4, "little")
    rts = b"".join(struct.pack("<I", pack_bits(f[nrp + nst + 7 * i:nrp + nst + 7 * i + 7], RT_FIELDS))
                   for i in range(4))
    return head + mid + rts


def from_format1(p):
    """A format-1 pipeline (named field dicts) in format 2."""
    fields = [p["render_pass"][n] for n, _w, _v in RENDER_PASS_FIELDS]
    fields += [p["state"][n] for n, _w, _v in STATE_FIELDS]
    for rt in p["render_targets"]:
        fields += [rt[n] for n, _w, _v in RT_FIELDS]
    out = {k: p[k] for k in ("vs", "vs_mod", "ps", "ps_mod")}
    out["fields"] = fields
    out["partitions"] = list(p.get("partitions", []))
    return out


def read_xpso_header(d):
    """(api, version, header size) of a pipeline storage file's bytes. Format 2 (patch 0102, 20
    bytes: magic, API, then big-endian 2, the description and the modification version) gives
    "DDDDDDDD/MMMMMMMM"; the original 12-byte header only the larger of the two."""
    magic, api = struct.unpack_from("<II", d, 0)
    if magic != XPSO_MAGIC:
        raise ValueError("not a pipeline storage file")
    word = struct.unpack_from(">I", d, 8)[0]
    if word == XPSO_FORMAT2 and len(d) >= 20:
        desc, mod = struct.unpack_from(">II", d, 12)
        return ("fsi" if api else "fbo"), f"{desc:08X}/{mod:08X}", 20
    return ("fsi" if api else "fbo"), f"{word:08X}", 12


def read_xpso(path):
    """(header dict, layout, [description bytes]) of a pipeline storage file."""
    with open(path, "rb") as f:
        d = f.read()
    try:
        api, version, size = read_xpso_header(d)
    except ValueError:
        raise ValueError(f"{path}: not a pipeline storage file") from None
    header = {"api": api, "version": version}
    body = d[size:]
    best = None
    for layout, size in LAYOUTS.items():
        entry = 8 + size
        n = len(body) // entry
        ok = 0
        for i in range(n):
            e = body[i * entry:(i + 1) * entry]
            if struct.unpack_from("<Q", e)[0] != xxh3_64(e[8:]):
                break
            ok += 1
        if best is None or ok > best[1]:
            best = (layout, ok, entry)
    layout, n, entry = best
    return header, layout, [body[i * entry + 8:(i + 1) * entry] for i in range(n)]


def pipeline_key(p):
    """What identifies a pipeline: its shaders, their modifications and its render state."""
    return json.dumps([p["vs"], p["vs_mod"], p["ps"], p["ps_mod"], p["fields"]])


def partition_of(path):
    """Where the SDK keeps this file: 'ui' or 'scene' with split rendering, else 'main'."""
    parts = os.path.normpath(path).split(os.sep)
    i = parts.index("shaders") if "shaders" in parts else len(parts)
    parent = parts[i - 1] if i else ""
    return parent if parent in ("ui", "scene") else "main"


def image_sources(sources):
    """The executable images among `sources`: (name, bytes) pairs, and files that are neither
    effects (.fxobj) nor zips, nor directories."""
    out = []
    for src in sources:
        if isinstance(src, tuple):
            out.append(src[1])
        elif not os.path.isdir(src) and not src.lower().endswith((".fxobj", ".zip")):
            with open(src, "rb") as f:
                out.append(f.read())
    return out


def image_offsets(ucode, images):
    """The first 4-byte aligned offset of `ucode` in each image that holds it, in order."""
    offsets = []
    for img in images:
        i = img.find(ucode)
        while i >= 0 and i % 4:
            i = img.find(ucode, i + 1)
        if i >= 0 and i not in offsets:
            offsets.append(i)
    return offsets


def make_shader_recipes(hashes_types, xsh_paths, sources, images=False):
    """images=True: a shader found in no blob but verbatim in an executable image among the
    sources gets an "image" recipe (the shipped list; player uploads keep the older kinds)."""
    import fxobj_shaders as F
    stored = {}
    for p in xsh_paths:
        stored.update(F.read_xsh(p))
    shaders, _ = F.collect(sources) if sources else ({}, None)
    by_size = {}
    for s in shaders.values():
        if s.type == F.TYPE_VS:
            by_size.setdefault(len(s.ucode), []).append(s)
    recipes = {}
    for h in sorted(hashes_types):
        key = f"{h:016X}"
        if h in shaders:
            recipes[key] = {"type": F.TYPE_NAMES[shaders[h].type], "from": "file"}
            continue
        if h not in stored:
            recipes[key] = {"from": "missing", "why": "not in the given shader storage"}
            continue
        t, u = stored[h]
        best = None
        if t == F.TYPE_VS:
            for base in by_size.get(len(u), ()):
                patch = derive_binding(base.ucode, u)
                if patch is not None and (best is None or patch_size(patch) < patch_size(best[1])):
                    best = (base, patch)
        if best is not None:
            recipes[key] = dict({"type": "vs", "from": "bound", "base": f"{best[0].hash:016X}"},
                                **best[1])
            assert xxh3_64(apply_binding(best[0].ucode, best[1])) == h
            continue
        offsets = image_offsets(bytes(u), image_sources(sources)) if images else []
        if offsets:
            recipes[key] = {"type": F.TYPE_NAMES[t], "from": "image", "size": len(u),
                            "offsets": offsets}
            continue
        recipes[key] = {"type": F.TYPE_NAMES[t], "from": "missing",
                        "why": "not in the game files"}
    return recipes


def stores_to_pipelines(xpso_paths):
    """Pipelines of pipeline storage files, decoded: ({key: pipeline}, versions, {hash: 0 vs / 1 ps}).
    A pipeline's partitions are those of the stores it was found in."""
    pipelines, versions, hashes = {}, set(), {}
    for path in xpso_paths:
        header, layout, descs = read_xpso(path)
        versions.add((header["api"], header["version"]))
        part = partition_of(path)
        for d in descs:
            p = decode_description(d, layout)
            k = pipeline_key(p)
            entry = pipelines.setdefault(k, dict(p, partitions=[]))
            if part not in entry["partitions"]:
                entry["partitions"].append(part)
            hashes[int(p["vs"], 16)] = 0
            if int(p["ps"], 16):
                hashes[int(p["ps"], 16)] = 1
    for e in pipelines.values():
        e["partitions"].sort()
    return pipelines, versions, hashes


def cmd_make(a):
    pipelines, versions, hashes = stores_to_pipelines(a.xpso)
    out = {
        "format": LIST_FORMAT,
        "title": "4D5309C9",
        "storage_versions": sorted(f"{api}:{v}" for api, v in versions),
        "pipelines": sorted(pipelines.values(), key=pipeline_key),
    }
    if a.xsh:
        out["shaders"] = make_shader_recipes(hashes, a.xsh, a.sources or [], images=True)
    write_list(a.output, out)
    print(f"{len(out['pipelines'])} pipelines from {len(a.xpso)} files -> {a.output}")
    if "shaders" in out:
        c = Counter(r["from"] for r in out["shaders"].values())
        print("shaders:", dict(c))


def write_list(path, data):
    """One pipeline or shader per line: compact, and a changed list diffs line by line."""
    def one(v):
        return json.dumps(v, sort_keys=True, separators=(",", ":"))
    lines = ["{"]
    for k in ("format", "title", "storage_versions"):
        lines.append(f"{one(k)}:{one(data[k])},")
    lines.append('"pipelines":[')
    lines.append(",\n".join(one(p) for p in data["pipelines"]))
    lines.append("]" + ("," if "shaders" in data else ""))
    if "shaders" in data:
        lines.append('"shaders":{')
        lines.append(",\n".join(f"{one(h)}:{one(r)}" for h, r in sorted(data["shaders"].items())))
        lines.append("}")
    lines.append("}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def load_list(path):
    """A list in format 2 (format-1 lists are converted)."""
    if hasattr(path, "read"):
        lst = json.load(path)
    else:
        with open(path) as f:
            lst = json.load(f)
    if lst.get("format") == 1:
        lst = dict(lst, format=LIST_FORMAT, pipelines=[from_format1(p) for p in lst["pipelines"]])
    if lst.get("format") != LIST_FORMAT:
        raise SystemExit(f"{getattr(path, 'name', path)}: list format {lst.get('format')}")
    return lst


def merge_lists(lists, mixed_versions=False):
    """One list from several loaded lists (format 2): pipelines by key with their partitions joined,
    shaders by hash (a recipe that is not `missing` replaces `missing`; two different ones are an error).
    Raises ValueError."""
    pipelines, shaders, versions = {}, {}, set()
    for lst in lists:
        versions.update(lst["storage_versions"])
        for p in lst["pipelines"]:
            k = pipeline_key(p)
            e = pipelines.get(k)
            if e is None:
                pipelines[k] = dict(p, partitions=sorted(set(p.get("partitions", []))))
                continue
            e["partitions"] = sorted(set(e["partitions"]) | set(p.get("partitions", [])))
        for h, r in lst.get("shaders", {}).items():
            old = shaders.get(h)
            if old is None or old["from"] == "missing":
                shaders[h] = r
            elif r["from"] != "missing" and r != old:
                raise ValueError(f"conflicting recipes for shader {h}")
    if len(versions) > 1 and not mixed_versions:
        raise ValueError(f"lists from different SDK storage versions {sorted(versions)}: "
                         "descriptions may not mean the same (--mixed-versions to merge anyway)")
    # Shaders no listed pipeline uses (lists cut with --drop) are left out.
    used = {p[k] for p in pipelines.values() for k in ("vs", "ps")}
    return {"format": LIST_FORMAT, "title": "4D5309C9", "storage_versions": sorted(versions),
            "pipelines": sorted(pipelines.values(), key=pipeline_key),
            "shaders": {h: r for h, r in shaders.items() if h in used}}


def cmd_merge(a):
    try:
        out = merge_lists([load_list(path) for path in a.lists], a.mixed_versions)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    write_list(a.output, out)
    print(f"{len(out['pipelines'])} pipelines, {len(out['shaders'])} shaders -> {a.output}")


HEX16 = re.compile(r"^[0-9A-F]{16}$")


def check_list(lst):
    """Problems of a list: anything but hashes, decoded render state, vertex layout and export
    fields. Returns a list of strings (empty: fine)."""
    problems = []
    allowed_top = {"format", "title", "storage_versions", "pipelines", "shaders"}
    if set(lst) - allowed_top:
        problems.append(f"unexpected top-level keys {sorted(set(lst) - allowed_top)}")
    if lst.get("format") != LIST_FORMAT:
        problems.append(f"list format {lst.get('format')}")
    for v in lst.get("storage_versions", []):
        if not re.match(r"^(fbo|fsi):[0-9A-F]{8}(/[0-9A-F]{8})?$", str(v)):
            problems.append(f"storage version {v!r}")
    for i, p in enumerate(lst.get("pipelines", [])):
        w = f"pipeline {i}"
        if set(p) != {"vs", "vs_mod", "ps", "ps_mod", "fields", "partitions"}:
            problems.append(f"{w}: unexpected keys {sorted(p)}")
        for k in ("vs", "vs_mod", "ps", "ps_mod"):
            if not HEX16.match(str(p.get(k))):
                problems.append(f"{w}: {k} is not a 64-bit value")
        f = p.get("fields")
        if not isinstance(f, list) or len(f) != len(FIELDS):
            problems.append(f"{w}: fields must be {len(FIELDS)} values")
        else:
            for (n, _w, count), v in zip(FIELDS, f):
                if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < count:
                    problems.append(f"{w}: {n}={v!r} out of range")
        parts = p.get("partitions")
        if not parts or not isinstance(parts, list) or set(parts) - set(PARTITION_FLAGS):
            problems.append(f"{w}: partitions {parts!r}")
    for h, r in lst.get("shaders", {}).items():
        w = f"shader {h}"
        if not HEX16.match(h):
            problems.append(f"{w}: bad hash")
        if r.get("from") == "bound":
            if not HEX16.match(str(r.get("base"))):
                problems.append(f"{w}: bad base hash")
            for e in r.get("fetch", ()):
                if len(e) != 4 or any(not isinstance(v, int) or v & ~FETCH_LAYOUT_MASK[k]
                                      for k, v in enumerate(e[1:])):
                    problems.append(f"{w}: fetch entry {e} has bits outside the vertex layout")
            for e in r.get("export", ()):
                if len(e) != 2 or not isinstance(e[1], int) or e[1] & ~EXPORT_MASK:
                    problems.append(f"{w}: export entry {e} has bits outside the export fields")
            if not all(isinstance(k, int) for k in r.get("nop", ())):
                problems.append(f"{w}: nop entries must be instruction indices")
        elif r.get("from") == "image":
            # Where the shader is, never what it is: a size and up to 4 offsets.
            size, offsets = r.get("size"), r.get("offsets")
            if r.get("type") not in ("vs", "ps"):
                problems.append(f"{w}: image shader without its type")
            if not isinstance(size, int) or isinstance(size, bool) or not 4 <= size <= 65536 or size % 4:
                problems.append(f"{w}: image size {size!r}")
            if (not isinstance(offsets, list) or not 1 <= len(offsets) <= 4 or
                    any(not isinstance(o, int) or isinstance(o, bool) or o < 0 or o % 4 or o >= 1 << 28
                        for o in offsets)):
                problems.append(f"{w}: image offsets {offsets!r}")
        elif r.get("from") not in ("file", "missing"):
            problems.append(f"{w}: unknown source {r.get('from')}")
        extra = set(r) - {"type", "from", "base", "fetch", "export", "nop", "why", "size", "offsets"}
        if extra:
            problems.append(f"{w}: unexpected keys {sorted(extra)}")
        if isinstance(r.get("why"), str) and len(r["why"]) > 80:
            problems.append(f"{w}: long text")
    return problems


def cmd_check(a):
    with open(a.list) as f:
        lst = json.load(f)
    problems = check_list(lst)
    size = os.path.getsize(a.list)
    print(f"{a.list}: {len(lst.get('pipelines', []))} pipelines, {len(lst.get('shaders', {}))} "
          f"shaders, {size} bytes")
    if problems:
        for p in problems[:50]:
            print("PROBLEM:", p)
        raise SystemExit(1)
    print("ok: only hashes, render state, vertex layout and export fields, and image offsets")


def fnv1a64(data):
    h = 0xCBF29CE484222325
    for b in data:
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def list_versions(lst):
    """(api, description version, modification version) of a list from format-2 stores."""
    versions = lst.get("storage_versions", [])
    if len(versions) != 1 or "/" not in versions[0]:
        raise ValueError(f"the list needs pipelines of one storage version with both versions "
                         f"(patch 0102 stores), not {versions}")
    api, v = versions[0].split(":")
    desc, mod = v.split("/")
    return api, int(desc, 16), int(mod, 16)


def encode_xpl(pipelines, api, desc_version, mod_version):
    """The SDK's prebuild list file (pipeline_prebuild_file.h) for these pipelines."""
    body = bytearray()
    for p in pipelines:
        flags = 0
        for part in p["partitions"]:
            flags |= PARTITION_FLAGS[part]
        rec = struct.pack("<QQQQI", int(p["vs"], 16), int(p["vs_mod"], 16), int(p["ps"], 16),
                          int(p["ps_mod"], 16), flags) + bytes(p["fields"])
        body += rec + bytes(XPL_RECORD - len(rec))
    head = struct.pack("<8IQ", XPL_MAGIC, XPL_FORMAT, 1 if api == "fsi" else 0, desc_version,
                       mod_version, len(FIELDS), len(pipelines), XPL_RECORD, fnv1a64(body))
    return head + bytes(body)


def split_partitions(p):
    """A pipeline only seen without split rendering (main) is used by one of the split caches
    there too, which one is unknown: it goes to both. Those seen in split runs keep theirs."""
    if p["partitions"] == ["main"]:
        return dict(p, partitions=["main", "scene", "ui"])
    return p


def build_shaders(lst, shaders, images=()):
    """{hash: (type, ucode)} of the listed shaders that `shaders` (fxobj_shaders.collect of the
    player's files) and `images` (their executable images) provide, and a Counter of the others
    by reason."""
    import fxobj_shaders as F
    built, lost = {}, Counter()
    for key, r in lst.get("shaders", {}).items():
        h = int(key, 16)
        if r["from"] == "file" and h in shaders:
            built[h] = (shaders[h].type, shaders[h].ucode)
        elif r["from"] == "bound" and int(r["base"], 16) in shaders:
            u = apply_binding(shaders[int(r["base"], 16)].ucode, r)
            if xxh3_64(u) == h:
                built[h] = (F.TYPE_VS, u)
            else:
                lost["bound shader did not match its hash"] += 1
        elif r["from"] == "image" and images:
            u = next((img[o:o + r["size"]] for img in images for o in r["offsets"]
                      if xxh3_64(img[o:o + r["size"]]) == h), None)
            if u is not None:
                built[h] = (F.TYPE_VS if r["type"] == "vs" else F.TYPE_PS, u)
            else:
                lost["image shader not in this executable"] += 1
        else:
            lost[f"{r['from']} shader not available"] += 1
    return built, lost


def xsh_bytes(built):
    out = [struct.pack("<I", 0x48534558) + struct.pack(">I", 0x20201219)]
    for h, (t, u) in sorted(built.items()):
        out.append(struct.pack("<QI", h, (len(u) // 4) | (t << 31)) + u)
    return b"".join(out)


def _replace(path, data):
    tmp = path + ".new"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def write_prebuild(lst, sources, out_dir, status=None, cancelled=None):
    """The shader prebuild for the SDK (<cache root>/shaders/prebuild): <title>.xsh, the listed
    shaders rebuilt from the player's own files (`sources`: paths, or (name, bytes) pairs for
    an executable image), and <title>.<api>.vk.xpl, the listed pipelines whose shaders are
    there. Local only: the .xsh holds the game's microcode. Returns counts."""
    import fxobj_shaders as F
    api, desc_version, mod_version = list_versions(lst)
    shaders = {}
    for i, src in enumerate(sources):
        if cancelled and cancelled():
            raise InterruptedError
        if status:
            status(i, len(sources), src[0] if isinstance(src, tuple) else src)
        found, _ = F.collect([src])
        for h, s in found.items():
            shaders.setdefault(h, s)
    built, lost = build_shaders(lst, shaders, image_sources(sources))
    pipelines = [split_partitions(p) for p in lst["pipelines"]
                 if int(p["vs"], 16) in built and (not int(p["ps"], 16) or int(p["ps"], 16) in built)]
    title = lst.get("title", "4D5309C9")
    os.makedirs(out_dir, exist_ok=True)
    # The list goes last: the SDK reads nothing without it.
    _replace(os.path.join(out_dir, f"{title}.xsh"), xsh_bytes(built))
    _replace(os.path.join(out_dir, f"{title}.{api}.vk.xpl"),
             encode_xpl(pipelines, api, desc_version, mod_version))
    return {"shaders": len(built), "shaders_missing": sum(lost.values()),
            "pipelines": len(pipelines), "pipelines_listed": len(lst["pipelines"]),
            "api": api}


def cmd_prebuild(a):
    stats = write_prebuild(load_list(a.list), a.sources, a.out)
    print(f"{stats['shaders']} shaders ({stats['shaders_missing']} not available), "
          f"{stats['pipelines']} of {stats['pipelines_listed']} pipelines -> {a.out}")


def cmd_write_storage(a):
    """Write <title>.xsh and <title>.<api>.vk.xpso for the listed pipelines whose shaders
    the given sources (the player's own game files) provide: the SDK's own storage, as the
    game would have written it (phase 1 experiment). Local use only: the .xsh holds microcode."""
    import fxobj_shaders as F
    lst = load_list(a.list)
    shaders, _ = F.collect(a.sources)
    built, lost = build_shaders(lst, shaders, image_sources(a.sources))
    os.makedirs(a.out, exist_ok=True)
    title = lst.get("title", "4D5309C9")
    with open(os.path.join(a.out, f"{title}.xsh"), "wb") as f:
        f.write(xsh_bytes(built))
    n = 0
    if "/" in a.version:
        desc, mod = (int(v, 16) for v in a.version.split("/"))
        header = (struct.pack("<II", XPSO_MAGIC, 1 if a.api == "fsi" else 0) +
                  struct.pack(">III", XPSO_FORMAT2, desc, mod))
    else:
        header = (struct.pack("<II", XPSO_MAGIC, 1 if a.api == "fsi" else 0) +
                  struct.pack(">I", int(a.version, 16)))
    with open(os.path.join(a.out, f"{title}.{a.api}.vk.xpso"), "wb") as f:
        f.write(header)
        for p in lst["pipelines"]:
            if int(p["vs"], 16) not in built or (int(p["ps"], 16) and int(p["ps"], 16) not in built):
                lost["pipeline without its shaders"] += 1
                continue
            d = encode_description(p, a.layout)
            f.write(struct.pack("<Q", xxh3_64(d)) + d)
            n += 1
    print(f"{len(built)} shaders, {n} pipelines -> {a.out} ({a.layout} layout, version {a.version})")
    if lost:
        print("skipped:", dict(lost))


def cmd_stats(a):
    for path in a.xpso:
        header, layout, descs = read_xpso(path)
        topo = Counter()
        for d in descs:
            p = decode_description(d, layout)
            topo[p["fields"][len(RENDER_PASS_FIELDS) + 1]] += 1
        fans = topo.get(TOPOLOGY_TRIANGLE_FAN, 0)
        print(f"{path}: {header['api']} v{header['version']} {layout} {len(descs)} pipelines, "
              f"{fans} triangle-fan (rejected at load)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("make")
    p.add_argument("xpso", nargs="+")
    p.add_argument("--xsh", action="append")
    p.add_argument("--sources", action="append")
    p.add_argument("-o", "--output", required=True)
    p = sub.add_parser("merge")
    p.add_argument("lists", nargs="+")
    p.add_argument("--mixed-versions", action="store_true")
    p.add_argument("-o", "--output", required=True)
    p = sub.add_parser("check")
    p.add_argument("list")
    p = sub.add_parser("stats")
    p.add_argument("xpso", nargs="+")
    p = sub.add_parser("prebuild")
    p.add_argument("list")
    p.add_argument("--sources", action="append", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("write-storage")
    p.add_argument("list")
    p.add_argument("--sources", action="append", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--layout", choices=LAYOUTS, default="msvc" if sys.platform == "win32" else "clang")
    p.add_argument("--api", choices=("fbo", "fsi"), default="fbo")
    p.add_argument("--version", default="20260228/20261018",
                   help="PipelineDescription::kVersion/Modification::kVersion of the SDK (a "
                        "single value writes the original 12-byte header)")
    a = ap.parse_args()
    {"make": cmd_make, "merge": cmd_merge, "check": cmd_check, "stats": cmd_stats,
     "prebuild": cmd_prebuild, "write-storage": cmd_write_storage}[a.cmd](a)


if __name__ == "__main__":
    main()
