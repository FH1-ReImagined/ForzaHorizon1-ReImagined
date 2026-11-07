#!/usr/bin/env python3
"""Dump Forza Horizon (2012, Xbox 360) .carbin car models: sections, meshes, materials,
the textures each material's shader samples, and the damage (morph) data.

The format is described in docs/modding/carbin-format.md. In short: a .carbin is the binary
output of the game's serializer (big-endian, no alignment, every group starts with a u32
version, strings are a u32 length and the bytes). A section ("hooda", "bumperFa") holds a
vertex buffer and meshes; each mesh names its material, which is the name of a shader
technique. Textures are not in the file: the shader's sampler names pick them, through
media/cars/Shared.zip textures.xml and a few rules in the game's code.

Usage:
  carbin_dump.py FILE...                  summary of each file
  carbin_dump.py --meshes FILE...         every mesh: material, LOD, draw list, UV factors
  carbin_dump.py --morph 5 FILE...        damage morph statistics and the first 5 entries
  carbin_dump.py --media DIR FILE...      also each material's effect, samplers and textures
  carbin_dump.py --json FILE...           everything as JSON (no vertex data)
FILE is a .carbin, or a car archive from media/cars (its .carbin members are read; the
archives' LZX members use tools/unxmem, built from tools/unxmem.c, or else the slower Python
decoder in tools/fxobj_shaders.py). DIR is the game's
media folder (with shaders/Cars and cars/Shared.zip).

Nothing is written anywhere; vertex data is not printed.
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


class Reader:
    def __init__(self, data):
        self.d, self.o = data, 0

    def u32(self):
        v = struct.unpack_from(">I", self.d, self.o)[0]
        self.o += 4
        return v

    def u8(self):
        v = self.d[self.o]
        self.o += 1
        return v

    def f32(self, n):
        v = struct.unpack_from(">%df" % n, self.d, self.o)
        self.o += 4 * n
        return list(v)

    def skip(self, n):
        if self.o + n > len(self.d):
            raise ValueError("truncated at 0x%x (+%d)" % (self.o, n))
        self.o += n

    def string(self):
        n = self.u32()
        if n > 4096:
            raise ValueError("bad string length %d at 0x%x" % (n, self.o - 4))
        s = self.d[self.o:self.o + n].decode("latin1")
        self.o += n
        return s


# ---------------------------------------------------------------- structure

def vertex_buffer(r):
    """VertexBuffer group (versions 2 and 3; version 1 is not in the shipped files)."""
    vb = {"at": r.o, "version": r.u32()}
    if vb["version"] < 2:
        raise ValueError("vertex buffer version %d at 0x%x" % (vb["version"], vb["at"]))
    vb["count"], vb["stride"] = r.u32(), r.u32()
    vb["flags"] = vb["key"] = None
    if vb["version"] >= 3:
        vb["flags"] = r.u32()
        if vb["flags"] & 1:
            vb["key"] = r.u32()
    # Flag 2: the data is not in this file (stripped_*.carbin); the game takes the buffer
    # it already loaded with the same key.
    vb["has_data"] = not (vb["flags"] or 0) & 2
    vb["data_at"] = r.o if vb["has_data"] else None
    if vb["has_data"]:
        r.skip(vb["count"] * vb["stride"])
    return vb


def index_buffer(r):
    ib = {"at": r.o, "version": r.u32(), "flags": None, "key": None}
    if ib["version"] < 3:
        raise ValueError("index buffer version %d at 0x%x" % (ib["version"], ib["at"]))
    if ib["version"] > 3:
        ib["flags"] = r.u32()
        if ib["flags"] & 1:
            ib["key"] = r.u32()
    ib["count"], ib["format"] = r.u32(), r.u32()
    ib["index_size"] = 4 if ib["format"] == 4 else 2
    ib["has_data"] = not (ib["flags"] or 0) & 2
    ib["data_at"] = r.o if ib["has_data"] else None
    if ib["has_data"]:
        r.skip(ib["count"] * ib["index_size"])
    return ib


def mesh(r):
    m = {"at": r.o}
    # CCarMaterialData
    m["material_data_version"] = v = r.u32()
    m["remove_from_shadow"] = bool(r.u8()) if v > 1 else False
    params = r.f32(16) if v > 2 else [0.0] * 16
    m["uv1_factors"], m["uv2_factors"], m["params_rest"] = params[0:4], params[4:8], params[8:16]
    # CMesh
    m["version"] = r.u32()
    m["material"] = r.string()
    m["lod"], m["primitive"], m["material_index"] = r.u32(), r.u32(), r.u32()
    r.u32()  # UnpackingData version
    m["position_offset"], m["position_scale"], m["uv_offset_scale"] = r.f32(4), r.f32(4), r.f32(4)
    m["index_buffer"] = index_buffer(r)
    m["mesh_flags"] = r.u32() if m["version"] > 1 else 0
    return m


def section(r):
    """CCarSubModel: CCarPartData, then the model part (name, buffers, meshes, morphs)."""
    s = {"at": r.o}
    s["version"] = v = r.u32()
    s["center"], s["bounds_min"], s["bounds_max"] = r.f32(3), r.f32(3), r.f32(3)
    s["part_float"], s["part_vec3"] = r.f32(1)[0], r.f32(3)
    s["part_u32"] = r.u32()
    r.u32()  # GlassParticles version
    r.u32(); s["glass_vertices"] = r.u32(); r.skip(16 * s["glass_vertices"])
    r.u32(); s["glass_indices"] = r.u32(); r.skip(2 * s["glass_indices"])
    s["part_flag"] = r.u8()
    if v > 2:
        if v == 3:
            raise ValueError("section version 3 (Triggers) is not handled")
        s["part_u32b"] = r.u32()
    r.u32()  # model part group version
    s["name"] = r.string()
    s["vertex_buffer"] = vertex_buffer(r)
    r.u32()  # Mesh array version
    s["meshes"] = [mesh(r) for _ in range(r.u32())]
    s["vertex_buffer_lod0"] = vertex_buffer(r) if v > 1 else None
    s["has_morphs"], s["morph"], s["morph_lod0"] = False, None, None
    if v > 4:
        r.u32()  # MorphTargets version
        s["has_morphs"] = bool(r.u8())
        s["morph"], s["morph_lod0"] = vertex_buffer(r), vertex_buffer(r)
    return s


def base_model(r):
    """CBaseCarModelData."""
    b = {"at": r.o, "version": r.u32()}
    v = b["version"]
    b["shader_version"] = r.u32() if v >= 9 else v
    r.u32()  # Emitters version
    b["emitters"] = r.u32()
    for _ in range(b["emitters"]):
        ev = r.u32()
        if ev != 2:
            raise ValueError("emitter version %d at 0x%x" % (ev, r.o - 4))
        r.skip(136)
    dv = r.u32()  # DriverControls
    r.skip({1: 0x60, 2: 0xd0, 3: 0x100}[dv])
    if v >= 9:
        b["start_lod"], b["end_lod"] = r.u32(), r.u32()
    r.skip((15 if v >= 16 else 4) * 36)  # Gauges: 2 vec4 + u32 each
    if v >= 8:
        r.skip(8)
    if v > 13:
        r.u32()
    b["bones"] = []
    if v > 14:
        r.u32()
        b["bones"] = [r.string() for _ in range(r.u32())]
    return b


def parse(data):
    r = Reader(data)
    out = {"size": len(data), "kind": r.u32()}
    if out["kind"] == 5:  # CCarModelData (body, _lod0, _cockpit)
        r.skip(0x100 + 16 + 0x30 + 16)
        out["lights"] = r.u32()
        r.skip(32 * out["lights"])
    elif out["kind"] == 3:  # wheel model (media/wheels): 0x44 bytes of wheel data
        out["wheel_data"] = r.f32(17)
    elif out["kind"] != 1:  # 1: a part model (calipers, rotors, media/brakes)
        raise ValueError("unknown root version %d" % out["kind"])
    out["base"] = base_model(r)
    out["draw_list_header"] = (r.u32(), r.u32())
    n = r.u32()
    lists = []
    for _ in range(2 * n):
        c = r.u32()
        lists.append([struct.unpack_from(">HH", data, r.o + 4 * k) for k in range(c)])
        r.o += 4 * c
    out["lods_in_lists"] = n
    out["draw_lists"] = {"alpha": lists[:n], "opaque": lists[n:]}
    r.u32()  # SubModel array version
    out["sections"] = [section(r) for _ in range(r.u32())]
    out["tail"] = data[r.o:].hex()
    out["parsed_to_end"] = r.o + 24 == len(data) and data[r.o:] == bytes.fromhex(
        "000000010000000000000001000000000000000100000000")
    for li, lod_lists in enumerate((out["draw_lists"]["alpha"], out["draw_lists"]["opaque"])):
        for lod, entries in enumerate(lod_lists):
            for si, mi in entries:
                out["sections"][si]["meshes"][mi]["draw_list"] = (
                    ("alpha", "opaque")[li], lod)
    return out


# ---------------------------------------------------------------- damage morphs

def morph_entry(v):
    """One Morph entry of a car model (stride 4; wheel models use 16-byte entries, not decoded): Xenos FMT_10_11_11, signed normalized. x: bits 0-10, y: 11-21,
    z: 22-31, each two's complement; -max-1 clamps to -1."""
    def snorm(bits, width):
        if bits & (1 << (width - 1)):
            bits -= 1 << width
        return max(-1.0, bits / float((1 << (width - 1)) - 1))
    return (snorm(v & 0x7FF, 11), snorm((v >> 11) & 0x7FF, 11), snorm((v >> 22) & 0x3FF, 10))


def morph_raw(v):
    def sx(bits, width):
        return bits - (1 << width) if bits & (1 << (width - 1)) else bits
    return (sx(v & 0x7FF, 11), sx((v >> 11) & 0x7FF, 11), sx((v >> 22) & 0x3FF, 10))


def morph_stats(data, vb, first):
    vals = [struct.unpack_from(">I", data, vb["data_at"] + 4 * k)[0] for k in range(vb["count"])]
    raw = [morph_raw(v) for v in vals]
    nz = [t for t in raw if any(t)]
    st = {"entries": len(vals), "nonzero": len(nz)}
    if nz:
        st["raw_min"] = [min(t[a] for t in nz) for a in range(3)]
        st["raw_max"] = [max(t[a] for t in nz) for a in range(3)]
    st["first"] = [("%08x" % v, [round(c, 4) for c in morph_entry(v)]) for v in vals[:first]]
    return st


# ---------------------------------------------------------------- materials

SAMPLER_RULES = {
    # Bound by the car code, not by textures.xml (strings in the executable next to the
    # loader: "\nodamage.xds", "\damage.xds", "_LOD0.xds", "lights_nrm_s_LOD0.xds", ...).
    # The tyre and rim rows were confirmed at run time (docs/modding/carbin-format.md).
    "baseSampler": "nodamage.xds (LOD1-5) / nodamage_LOD0.xds (LOD0), car folder "
                   "(rim: wheel.xds / wheel_LOD0.xds; brake: rotor0.xds ...)",
    "damageSampler": "damage.xds (LOD1-5) / damage_LOD0.xds (LOD0), car folder",
    "lightsNormalSampler": "lights_nrm_s_LOD0.xds, car folder",
    "treadSampler": "treadA_CLR.xds (A, B or C by the tyre), media/wheels/tire?.zip",
    "treadNormalSampler": "treadA_NRM.xds (A, B or C by the tyre), media/wheels/tire?.zip",
    "sidewallSampler": "sidewallA_CLR.xds (by the tyre), media/wheels/Tire?<car>.zip",
    "sidewallNormalSampler": "sidewallA_NRM.xds (by the tyre), media/wheels/Tire?<car>.zip",
    "blurTireSampler": "tireA0.xds (A, B or C by the tyre), cars/Shared.zip",
    "blurTireSampler2": "tireA1.xds, cars/Shared.zip",
    "blurTireSampler3": "tireA2.xds, cars/Shared.zip",
    "blurTireSampler4": "none (a 32x32 placeholder of the engine)",
    "blurRimSampler1": "wheel0.xds, media/wheels/<wheel>.zip",
    "blurRimSampler2": "wheel1.xds, media/wheels/<wheel>.zip",
    "blurRimSampler3": "wheel3.xds, media/wheels/<wheel>.zip",
    "blurRimSampler4": "none (a 32x32 placeholder of the engine)",
}
ENGINE_SAMPLERS = re.compile(
    r"^(env|blurredCubemap|specularCubemap|ambientCubemap|shLookupCubemap|trackLightmap|"
    r"s_|mirror|windshieldReflection|digitalGauge|nullSampler|shadowMap|rimShadow|"
    r"tireShadow|ShadowMaskSamp|CSMShadowMap)")


def unxmem_member(zip_path, name, out_dir):
    zf = zipfile.ZipFile(zip_path)
    info = zf.getinfo(name)
    with open(zip_path, "rb") as f:
        f.seek(info.header_offset)
        h = f.read(30)
        n, m = struct.unpack("<HH", h[26:30])
        f.seek(info.header_offset + 30 + n + m)
        raw = f.read(info.compress_size)
    if info.compress_type == 0:
        return raw
    if info.compress_type != 21:
        return zf.read(name)
    tool = os.path.join(HERE, "unxmem")
    if not os.path.exists(tool):  # no tools/unxmem (players): the Python decoder, slower
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import fxobj_shaders
        return fxobj_shaders.decode_xmem(raw, info.file_size)
    src = os.path.join(out_dir, "m.lzx")
    dst = os.path.join(out_dir, "m.out")
    with open(src, "wb") as f:
        f.write(raw)
    subprocess.run([tool, src, dst, str(info.file_size)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(dst, "rb") as f:
        return f.read()


def shader_blob_samplers(data):
    """Samplers (name -> texture register) of each D3D shader blob in an .fxobj, from the
    blob's own constant table, so only what that shader really samples."""
    out = []
    pos = 0
    while True:
        pos = data.find(b"\x10\x2a\x11", pos)
        if pos < 0 or pos + 0x24 > len(data):
            return out
        start, pos = pos, pos + 3
        magic, vsize, psize, dbg, ct, _dt, desc = struct.unpack_from(">7I", data, start)
        if magic not in (0x102A1100, 0x102A1101) or dbg not in (0, 0x24):
            continue
        if not (0x24 <= desc and desc + 8 <= vsize and start + vsize + psize <= len(data)):
            continue
        c = start + ct + 4
        try:
            n, info = struct.unpack_from(">I I", data, c + 12)
            samplers = {}
            for k in range(n):
                nm, regset, reg = struct.unpack_from(">IHH", data, c + info + 20 * k)
                if regset == 3:
                    name = data[c + nm:c + nm + 64].split(b"\0")[0].decode("latin1")
                    samplers[name] = reg
        except struct.error:
            continue
        out.append(("ps" if magic == 0x102A1100 else "vs", samplers))
        pos = start + vsize + psize

# The in-race library (shaders_v16.fxobj) stores its shader blobs in technique order: the
# pixel shaders (240) follow the techniques (254 names; the pass names "p0", "p1" and "Cleanup"
# are strings in the same list). The first 186 pixel shaders belong to the techniques up to
# brake_badge, one each, except that cockpit_ghostInterior shares the previous one and the three
# detail_glass_* techniques have two passes; the shadow techniques (shadowMap to dropShadowBrake)
# share 15 shaders in a way not decoded, and the last 39 pixel shaders belong one each to the
# techniques tire to missingBrakePink. Checked at run time against the shaders and textures of
# the draws of a car (docs/modding/carbin-format.md); another file whose counts differ gets no
# answer.
RACE_PASS_NAMES = ("p0", "p1", "Cleanup")
RACE_TWO_PASS = ("detail_glass_red", "detail_glass_clear", "detail_glass_amber")
RACE_SHARES_PREVIOUS = ("cockpit_ghostInterior",)


def race_library_samplers(data):
    """{technique: [pixel shader sampler dict (name -> slot), per pass]} from shaders_v16.fxobj,
    or {} when the file does not have the layout described above."""
    try:
        first = data.index(b"body_traffic\0")
        names, pos = [], first
        while True:
            end = data.index(b"\0", pos)
            text = data[pos:end].decode("latin1")
            pos = end + 1
            if text == "D3DX9.1 Effect Compiler":
                break
            if text not in RACE_PASS_NAMES:
                names.append(text)
        front_end, tail_first = names.index("shadowMap"), names.index("tire")
        shadow_last = names.index("dropShadowBrake")
    except ValueError:
        return {}
    blobs = [t for kind, t in shader_blob_samplers(data) if kind == "ps"]
    front = names[:front_end]
    count = sum(0 if n in RACE_SHARES_PREVIOUS else 2 if n in RACE_TWO_PASS else 1 for n in front)
    tail = names[tail_first:]
    if shadow_last + 1 != tail_first or count + 15 + len(tail) != len(blobs):
        return {}
    out, i = {}, 0
    for n in front:
        if n in RACE_SHARES_PREVIOUS:
            out[n] = out[front[front.index(n) - 1]]
        else:
            k = 2 if n in RACE_TWO_PASS else 1
            out[n] = blobs[i:i + k]
            i += k
    for n, t in zip(tail, blobs[len(blobs) - len(tail):]):
        out[n] = [t]
    return out


class Materials:
    def __init__(self, media):
        cars = os.path.join(media, "shaders", "Cars")
        self.cars = cars
        xml = open(os.path.join(cars, "CustomCarShaders.xml"), encoding="latin1").read()
        self.custom = dict(re.findall(r'name="([^"]+)"[^>]*technique="([^"]+)"', xml))
        lib = open(os.path.join(cars, "shaders_v16.fxobj"), "rb").read()
        self.library = set(m.group().decode() for m in re.finditer(
            rb"(?<=\x00)[A-Za-z_][A-Za-z0-9_]{1,63}(?=\x00)", lib))
        self.race = race_library_samplers(lib)
        self.textures = {}
        shared = os.path.join(media, "cars", "Shared.zip")
        if os.path.exists(shared):
            with tempfile.TemporaryDirectory() as tmp:
                text = unxmem_member(shared, "textures.xml", tmp).decode("latin1")
            for name, body in re.findall(r"<(\w+Sampler)>(.*?)</\1>", text, re.S):
                self.textures[name] = [
                    "%s (%s, %s)" % (v, p.strip("%").lower(), f)
                    for v, p, f in re.findall(
                        r'value="([^"]+)"\s+searchPath="([^"]+)"[^>]*?loadFlags="([^"]+)"', body)
                    + [(v, p, "-") for v, p in re.findall(
                        r'value="([^"]+)"\s+searchPath="([^"]+)"(?![^>]*loadFlags)', body)]]
        self.cache = {}

    def resolve(self, material, cockpit):
        if (material, cockpit) in self.cache:
            return self.cache[material, cockpit]
        names = (["cockpit_" + material] if cockpit else []) + [material]
        tech = next((n for n in names if n in self.library), None)
        ui = next((n for n in names if n in self.custom), None)
        res = {"race_technique": ("shaders_v16.fxobj:" + tech) if tech else None,
               "ui_effect": None, "samplers": {}, "race_samplers": {}}
        for k, smp in enumerate(self.race.get(tech, [])):
            for s, slot in smp.items():
                res["race_samplers"]["%s%s (s%d)" % ("pass %d " % k if k else "", s, slot)] = \
                    self.texture_for(s)
        if ui:
            path = os.path.join(self.cars, ui + ".fxobj")
            if os.path.exists(path):
                res["ui_effect"] = os.path.basename(path)
                for kind, smp in shader_blob_samplers(open(path, "rb").read()):
                    for s, reg in smp.items():
                        res["samplers"]["%s %s (s%d)" % (kind, s, reg)] = self.texture_for(s)
        self.cache[material, cockpit] = res
        return res

    def texture_for(self, sampler):
        if sampler in self.textures:
            return "; ".join(self.textures[sampler])
        if sampler in SAMPLER_RULES:
            return SAMPLER_RULES[sampler]
        if ENGINE_SAMPLERS.match(sampler):
            return "engine (environment, lighting or shadow; not a car file)"
        return "?"


# ---------------------------------------------------------------- output

def load_inputs(paths):
    for p in paths:
        if p.lower().endswith(".zip"):
            zf = zipfile.ZipFile(p)
            with tempfile.TemporaryDirectory() as tmp:
                for n in zf.namelist():
                    if n.lower().endswith(".carbin"):
                        yield "%s:%s" % (os.path.basename(p), n), unxmem_member(p, n, tmp)
        else:
            with open(p, "rb") as f:
                yield p, f.read()


def strip(obj):
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items() if k not in ("tail",)}
    if isinstance(obj, (list, tuple)):
        return [strip(v) for v in obj]
    return obj


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--meshes", action="store_true", help="list every mesh")
    ap.add_argument("--morph", type=int, metavar="N", default=-1,
                    help="damage morph statistics per section and the first N entries")
    ap.add_argument("--media", metavar="DIR", help="game media folder, to resolve materials")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    mats = Materials(a.media) if a.media else None
    allj = []
    bad = 0
    for name, data in load_inputs(a.files):
        try:
            m = parse(data)
        except (ValueError, KeyError, struct.error) as e:
            print("%s: cannot parse: %s" % (name, e))
            bad += 1
            continue
        if not m["parsed_to_end"]:
            bad += 1
        cockpit = "_cockpit" in name.lower()
        used = {}
        for s in m["sections"]:
            for me in s["meshes"]:
                used.setdefault(me["material"], 0)
                used[me["material"]] += 1
        if a.json:
            j = strip(m)
            j["file"] = name
            if mats:
                j["materials"] = {k: mats.resolve(k, cockpit) for k in sorted(used)}
            if a.morph >= 0:
                for s in j["sections"]:
                    for k in ("morph", "morph_lod0"):
                        if s[k] and s[k]["count"] and s[k]["has_data"] and s[k]["stride"] == 4:
                            s[k]["stats"] = morph_stats(data, s[k], a.morph)
            allj.append(j)
            continue
        b = m["base"]
        print("%s: %d bytes, root %d (%s), base v%d shader v%d, LOD %s-%s, %d sections, "
              "%d meshes, %d materials, %s" % (
                  name, m["size"], m["kind"], {5: "car model", 3: "wheel", 1: "part"}[m["kind"]],
                  b["version"], b["shader_version"], b.get("start_lod"), b.get("end_lod"),
                  len(m["sections"]), sum(len(s["meshes"]) for s in m["sections"]), len(used),
                  "parsed to the end" if m["parsed_to_end"] else "TAIL NOT AS EXPECTED: " + m["tail"][:48]))
        for i, s in enumerate(m["sections"]):
            vb, vb0 = s["vertex_buffer"], s["vertex_buffer_lod0"]
            print("  [%2d] %-18s verts %5d/%5d (LOD1-5/LOD0, stride %d)  meshes %3d  morph %s" % (
                i, s["name"], vb["count"], vb0["count"] if vb0 else 0,
                vb["stride"] or (vb0["stride"] if vb0 else 0), len(s["meshes"]),
                ("%d/%d" % (s["morph"]["count"], s["morph_lod0"]["count"])) if s["has_morphs"] else "-"))
            if a.meshes:
                for me in s["meshes"]:
                    dl = me.get("draw_list", ("?", 0))
                    print("        %-26s LOD%d %-6s %s idx %6d  uv1 %s uv2 %s%s" % (
                        me["material"], me["lod"], dl[0],
                        {4: "list ", 5: "fan  ", 6: "strip"}.get(me["primitive"], str(me["primitive"])),
                        me["index_buffer"]["count"],
                        " ".join("%.4g" % x for x in me["uv1_factors"]),
                        " ".join("%.4g" % x for x in me["uv2_factors"]),
                        "  no-shadow" if me["remove_from_shadow"] else ""))
            if a.morph >= 0:
                for k in ("morph", "morph_lod0"):
                    if s[k] and s[k]["count"] and s[k]["has_data"] and s[k]["stride"] == 4:
                        st = morph_stats(data, s[k], a.morph)
                        print("        %s: %d entries, %d nonzero, raw x %s..%s y %s..%s z %s..%s" % (
                            k, st["entries"], st["nonzero"],
                            *[x for p in zip(st.get("raw_min", [0] * 3), st.get("raw_max", [0] * 3)) for x in p]))
                        for hexv, dec in st["first"]:
                            print("          %s -> %s" % (hexv, dec))
        if mats:
            print("  materials:")
            for k in sorted(used):
                r = mats.resolve(k, cockpit)
                print("    %-26s x%-3d race: %s  ui: %s" % (
                    k, used[k], r["race_technique"] or "not found", r["ui_effect"] or "-"))
                for smp, tex in sorted(r["race_samplers"].items(), key=lambda kv: kv[0].split("(s")[-1].rstrip(")").zfill(3)):
                    print("      race %-34s %s" % (smp, tex))
                for smp, tex in sorted(r["samplers"].items()):
                    print("        %-38s %s" % (smp, tex))
    if a.json:
        json.dump(allj if len(allj) != 1 else allj[0], sys.stdout, indent=1)
        print()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
