#!/usr/bin/env python3
"""Patches for FM4's track shaders (.fxobj) under FH1 (#648), applied by tools/fm4_pvs.py to
the converted track's archive. FM4's pixel shaders read inputs FH1 does not give them:

- s_grayscaleShadowMap (FM4's shadow map): FH1 binds no texture to it, so every lookup reads
  0, "in shadow", and the road and shoulders draw flat dark grey up to the shadows' range.
  Each fetch from it is made to return 1 (unshadowed). The same for projTrackLightmap, on the
  sampler FH1 gives its shadow mask.
- The vertex colour: TERR_BLND_SPEC_3 multiplies its colour by an interpolator that FH1's
  draws leave dark. Where a blend shader multiplies its colour (.xyz) by an interpolator it
  reads nowhere else, the multiply becomes a copy.
- The sky dome's brightness (SKY_DIFF_1: the texture times a value made of FM4's SkyScale and
  TimeGain constants, black under FH1): the texture is drawn as it is.

An .fxobj holds its shaders as blobs (magic 0x102A1100 for a pixel shader, 0x102A1101 for a
vertex shader; the microcode at blob + the word at +4, its size the word at +8, big-endian
dwords; a blob with a definitions table, the word at +0x14, has literal constants first and
the microcode's offset and size at table + 0x28). The microcode starts with control flow (exec: an address and count of 3-dword
instructions and 2 bits each saying fetch or ALU); the instruction fields are Xenos' (as in
Xenia's ucode.h).

Usage: tools/fm4_shaders.py FILE.fxobj ...   print what would be patched
"""
import re
import struct
import sys

PS_MAGIC = 0x102A1100
# projTrackLightmap (FM4's projected track lightmap, sampler 13 in SHLDR_BLND_SKIDSPEC_2 and
# CO_DIFF_1_noLM): FH1 binds its shadow mask there, so those surfaces took FH1's shadows
# through FM4's lightmap lookup; it is made to return 1 as well.
SHADOW_SAMPLERS = ("s_grayscaleShadowMap", "projTrackLightmap")
# FM4_SHADERS_LIGHTMAP=one makes the baked lightmap read 1 too (a test, see LIGHTMAP_UV).
if __import__("os").environ.get("FM4_SHADERS_LIGHTMAP") == "one":
    SHADOW_SAMPLERS += ("Light_MapSampler",)
MUL, MAX = 1, 3


def blobs(data):
    """(offset of the microcode, its size in bytes, samplers {name: register}) of each pixel
    shader blob."""
    out = []
    pos = 0
    while True:
        pos = data.find(b"\x10\x2a\x11\x00", pos)
        if pos < 0 or pos + 0x24 > len(data):
            return out
        magic, vsize, psize, dbg, ct, _dt, desc = struct.unpack_from(">7I", data, pos)
        if magic == PS_MAGIC and dbg in (0, 0x24) and pos + vsize + psize <= len(data):
            samplers = {}
            c = pos + ct + 4
            try:
                n, info = struct.unpack_from(">II", data, c + 12)
                for k in range(n):
                    nm, regset, reg = struct.unpack_from(">IHH", data, c + info + 20 * k)
                    if regset == 3:
                        samplers[data[c + nm:c + nm + 64].split(b"\0")[0].decode("latin1")] = reg
            except struct.error:
                pass
            start, size = pos + vsize, psize
            if _dt:  # literal constants first: the microcode's offset and size in the table
                off, size = struct.unpack_from(">II", data, pos + _dt + 0x28)
                start += off
            out.append((start, size, samplers))
            pos += vsize + psize
        else:
            pos += 4


def instructions(data, start, size):
    """(slot, kind 'alu'|'fetch', [3 words]) of a shader's microcode, from its exec clauses."""
    words = list(struct.unpack_from(f">{size // 4}I", data, start))
    nslots = len(words) // 3
    out = {}
    cf_end = nslots
    slot = 0
    while slot < cf_end and slot < nslots:
        w0, w1, w2 = words[3 * slot:3 * slot + 3]
        for cf in (w0 | (w1 & 0xFFFF) << 32, (w1 >> 16) | w2 << 16):
            op = (cf >> 44) & 0xF
            if op in (1, 2, 3, 4, 5, 6, 13, 14):  # the exec kinds
                address, count, seq = cf & 0xFFF, (cf >> 12) & 0x7, (cf >> 16) & 0xFFF
                cf_end = min(cf_end, address) if count else cf_end
                for i in range(count):
                    s = address + i
                    if s < nslots:
                        out[s] = ("fetch" if (seq >> (2 * i)) & 1 else "alu", words[3 * s:3 * s + 3])
        slot += 1
    return sorted((s, k, w) for s, (k, w) in out.items())


def alu_fields(w):
    return {"vdst": w[0] & 0x3F, "vmask": (w[0] >> 16) & 0xF, "export": (w[0] >> 15) & 1,
            "sdst": (w[0] >> 8) & 0x3F, "smask": (w[0] >> 20) & 0xF,
            "src": [((w[2] >> 16) & 0xFF, (w[1] >> 16) & 0xFF, (w[2] >> 31) & 1),
                    ((w[2] >> 8) & 0xFF, (w[1] >> 8) & 0xFF, (w[2] >> 30) & 1),
                    (w[2] & 0xFF, w[1] & 0xFF, (w[2] >> 29) & 1)],
            "vop": (w[2] >> 24) & 0x1F}


# The baked lightmap's colour: FM4's pixel shaders map the lightmap texel lm to
# lerp(V2LightmapColor1, V2LightmapColor2, lm) (c247, c248: add d = c248 - c247, then
# mad out = d * lm + c247). FH1 sets neither for these draws, so they kept whatever the last
# draw left there: roads, grass and walls drew dark, or flat blue, green, cyan or magenta,
# differently from run to run. The mad becomes lm * c255.x + c255.y, lm itself (c255 is the
# shaders' literal (1, 0, ...), read as 1 in their clamps and 0 in their maxes).
# FM4_SHADERS_LIGHTMAP_COLOURS=0 leaves them.
LIGHTMAP_COLOURS = __import__("os").environ.get("FM4_SHADERS_LIGHTMAP_COLOURS", "1") == "1"
SWZ_X, SWZ_Y = 0x6C, 0xB1  # .xxxx and .yyyy as Xenos relative swizzles


def lightmap_colours(data, start, alus):
    done = []
    diffs = set()
    for s, f, w in alus:
        consts = {r for r, _, is_reg in f["src"][:2] if not is_reg}
        if f["vop"] == 0 and consts == {247, 248} and not f["export"]:
            diffs.add(f["vdst"])
    for s, f, w in alus:
        a, b, c = f["src"]
        if f["vop"] != 11 or c[2] or c[0] != 247 or f["export"]:
            continue
        if a[2] and a[0] & 0x3F in diffs:
            slot = 0   # src1 is the colour difference, src2 the lightmap
        elif b[2] and b[0] & 0x3F in diffs:
            slot = 1
        else:
            continue
        w1, w2 = w[1], w[2]
        # The difference operand becomes c255.x, src3 c255.y; their negate bits cleared.
        shift = (16, 8)[slot]
        w2 = (w2 & ~(0xFF << shift)) | (255 << shift)
        w2 &= ~(1 << (31 - slot))          # a constant
        w1 = (w1 & ~(0xFF << shift)) | (SWZ_X << shift)
        w1 &= ~(1 << (26 - slot))
        w2 = (w2 & ~0xFF) | 255
        w1 = (w1 & ~0xFF) | SWZ_Y
        w1 &= ~(1 << 24)
        struct.pack_into(">3I", data, start + 12 * s, w[0], w1, w2)
        done.append(f"slot {s}: lightmap colour lerp(c247, c248) -> the lightmap")
    return done


# A texture's colour times a material constant (mul r.xyz = texel.xyz * cN.xyz, N 0 to 6,
# the c360_N slots FH1 fills from its own materials): under FH1 these hold another draw's
# values too, so blend surfaces (road, grass, shoulders) drew flat green or magenta. The
# constant becomes c255.x (1). FM4_SHADERS_TINTS=0 leaves them.
TINTS = __import__("os").environ.get("FM4_SHADERS_TINTS", "0") == "1"


def material_tints(data, start, ins, alus):
    done = []
    texels = {(w[0] >> 12) & 0x3F for s, kind, w in ins if kind == "fetch" and (w[0] & 0x1F) == 1}
    for s, f, w in alus:
        if f["vop"] != 1 or f["export"] or f["vmask"] & 0x7 != 0x7:
            continue
        a, b = f["src"][0], f["src"][1]
        for slot, (k, other) in enumerate(((a, b), (b, a))):
            if not k[2] and k[0] <= 6 and other[2] and other[0] & 0x3F in texels:
                shift = (16, 8)[slot]
                w1 = (w[1] & ~(0xFF << shift)) | (SWZ_X << shift)
                w1 &= ~(1 << (26 - slot))
                w2 = (w[2] & ~(0xFF << shift)) | (255 << shift)
                struct.pack_into(">3I", data, start + 12 * s, w[0], w1, w2)
                done.append(f"slot {s}: texel x c{k[0]} (material tint) -> texel")
                break
    return done


def patch_fxobj(name, data):
    """The patched file and a list of what was done."""
    data = bytearray(data)
    done = []
    base = name.split("/")[-1].split("\\")[-1].upper()
    for start, size, samplers in blobs(bytes(data)):
        shadow = {r for n, r in samplers.items() if n in SHADOW_SAMPLERS}
        ins = instructions(bytes(data), start, size)
        for s, kind, w in ins:
            if kind == "fetch" and (w[0] & 0x1F) == 1 and ((w[0] >> 20) & 0x1F) in shadow:
                swz = w[1] & 0xFFF
                one = 0
                for c in range(4):
                    sel = (swz >> (3 * c)) & 7
                    one |= (7 if sel == 7 else 5) << (3 * c)
                w = [w[0], (w[1] & ~0xFFF) | one, w[2]]
                struct.pack_into(">3I", data, start + 12 * s, *w)
                done.append(f"slot {s}: shadow map fetch -> 1")
        alus = [(s, alu_fields(w), w) for s, kind, w in ins if kind == "alu"]
        if LIGHTMAP_COLOURS:
            done += lightmap_colours(data, start, alus)
        if TINTS:
            done += material_tints(data, start, ins, alus)
        if "BLND" in base:
            # Inputs: registers read before any write; texture coordinates: fetch sources.
            written, inputs, reads = set(), set(), {}
            coords = {(w[0] >> 5) & 0x3F for s, kind, w in ins if kind == "fetch"}
            for s, kind, w in ins:
                if kind == "fetch":
                    src = (w[0] >> 5) & 0x3F
                    if src not in written:
                        inputs.add(src)
                    written.add((w[0] >> 12) & 0x3F)
                    continue
                f = alu_fields(w)
                n_src = 2 if f["vop"] in (MUL, MAX) else 3
                for reg, _, is_reg in f["src"][:n_src]:
                    if is_reg:
                        reads.setdefault(reg & 0x3F, []).append(s)
                        if reg & 0x3F not in written:
                            inputs.add(reg & 0x3F)
                if not f["export"] and f["vmask"]:
                    written.add(f["vdst"])
                if not f["export"] and f["smask"]:
                    written.add(f["sdst"])
            for s, f, w in alus:
                a, b = f["src"][0], f["src"][1]
                if f["vop"] != MUL or f["export"] or f["vmask"] != 0x7 or not (a[2] and b[2]):
                    continue
                for keep, other in ((a, b), (b, a)):
                    reg = other[0] & 0x3F
                    if (reg in inputs and reg not in coords and reads.get(reg) == [s]
                            and keep[0] & 0x3F == f["vdst"]):
                        # max(keep, keep): src2 := src1 (register and swizzle).
                        w2 = (w[2] & ~0x1F00FF00) | (MAX << 24) | ((keep[0] & 0xFF) << 8)
                        w1 = (w[1] & ~0xFF00) | ((keep[1] & 0xFF) << 8)
                        if keep is b:  # src1 := src2 as well
                            w2 = (w2 & ~0x00FF0000) | ((keep[0] & 0xFF) << 16)
                            w1 = (w1 & ~0x00FF0000) | ((keep[1] & 0xFF) << 16)
                        struct.pack_into(">3I", data, start + 12 * s, w[0], w1, w2)
                        done.append(f"slot {s}: colour x input r{reg} (vertex colour) -> copy")
                        break
        if base.startswith("SKY"):
            for s, f, w in alus:
                a, b = f["src"][0], f["src"][1]
                if (f["vop"] == MUL and not f["export"] and f["vmask"] == 0x7 and a[2] and b[2]
                        and f["vdst"] in (a[0] & 0x3F, b[0] & 0x3F) and a[0] != b[0]):
                    keep = a if a[0] & 0x3F == f["vdst"] else b
                    w2 = ((w[2] & ~0x1FFFFF00) | (MAX << 24) | ((keep[0] & 0xFF) << 16)
                          | ((keep[0] & 0xFF) << 8))
                    w1 = (w[1] & ~0x00FFFF00) | ((keep[1] & 0xFF) << 16) | ((keep[1] & 0xFF) << 8)
                    struct.pack_into(">3I", data, start + 12 * s, w[0], w1, w2)
                    done.append(f"slot {s}: sky brightness multiply -> copy")
                    break
    return bytes(data), done


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    for p in argv[1:]:
        _, done = patch_fxobj(p, open(p, "rb").read())
        print(p, done or "nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
