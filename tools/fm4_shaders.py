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
import os
import struct
import sys

PS_MAGIC, VS_MAGIC = 0x102A1100, 0x102A1101
# projTrackLightmap (FM4's projected track lightmap, sampler 13 in SHLDR_BLND_SKIDSPEC_2 and
# CO_DIFF_1_noLM): FH1 binds its shadow mask there, so those surfaces took FH1's shadows
# through FM4's lightmap lookup; it is made to return 1 as well.
SHADOW_SAMPLERS = ("s_grayscaleShadowMap", "projTrackLightmap")
# FM4_SHADERS_LIGHTMAP=one makes the baked lightmap read 1 too (a test, see LIGHTMAP_UV).
if __import__("os").environ.get("FM4_SHADERS_LIGHTMAP") == "one":
    SHADOW_SAMPLERS += ("Light_MapSampler",)
MUL, MAX = 1, 3
# FH1's shadows on FM4's surfaces: each s_grayscaleShadowMap fetch is pointed at fetch constant
# 13, where FH1 binds its screen-space shadow mask for track draws, and marked (texel offsets x
# -16 and y 15, raw; no real shader uses them). The SDK's Vulkan shader translator (ReIGlue,
# SpirvShaderTranslator::Fh1Fm4ShadowFetch) samples the mask at the pixel's position for a
# marked fetch; elsewhere (D3D12, or an SDK without it) the fetch still reads 1, no shadows.
# shad_color is then 0: the shadow takes the sun's light away, not the lightmap's.
# A test, off by default (FM4_SHADERS_FH1_SHADOWS=1): FH1 fills its mask in a collector pass
# over its own track meshes, which FM4's models do not have, so the mask holds no right shadow
# for FM4's surfaces: the car's shadow drew as a black silhouette in front of the car and part
# of the road white (Suzuka, 9 spots, midday and evening).
FH1_SHADOWS = __import__("os").environ.get("FM4_SHADERS_FH1_SHADOWS", "0") == "1"
FH1_SHADOW_MASK = 13


def blobs(data, kind=PS_MAGIC):
    """(offset of the microcode, its size in bytes, samplers {name: register}, float constants
    {name: (register, count)}, literal constants {register: 4 floats}) of each pixel shader
    blob (kind VS_MAGIC: each vertex shader blob)."""
    out = []
    pos = 0
    tag = struct.pack(">I", kind)
    while True:
        pos = data.find(tag, pos)
        if pos < 0 or pos + 0x24 > len(data):
            return out
        magic, vsize, psize, dbg, ct, _dt, desc = struct.unpack_from(">7I", data, pos)
        if magic == kind and dbg in (0, 0x24) and pos + vsize + psize <= len(data):
            samplers, floats, literals = {}, {}, {}
            c = pos + ct + 4
            try:
                n, info = struct.unpack_from(">II", data, c + 12)
                for k in range(n):
                    nm, regset, reg, cnt = struct.unpack_from(">IHHH", data, c + info + 20 * k)
                    name = data[c + nm:c + nm + 64].split(b"\0")[0].decode("latin1")
                    if regset == 3:
                        samplers[name] = reg
                    elif regset == 2:
                        floats[name] = (reg, cnt)
            except struct.error:
                pass
            start, size = pos + vsize, psize
            if _dt:  # literal constants first: the microcode's offset and size in the table
                off, size = struct.unpack_from(">II", data, pos + _dt + 0x28)
                # One block (word 1 of the table) at word 5: u16 first register (256 + the
                # constant in a pixel shader, the constant in a vertex shader), u16 dwords;
                # its values start the physical part.
                count, block = struct.unpack_from(">I", data, pos + _dt + 4)[0], \
                    struct.unpack_from(">I", data, pos + _dt + 0x14)[0]
                first, dwords = block >> 16, block & 0xFFFF
                base = 256 if kind == PS_MAGIC else 0
                if count == 1 and base <= first < base + 256 and dwords % 4 == 0 and dwords * 4 <= off:
                    for i in range(dwords // 4):
                        literals[first - base + i] = struct.unpack_from(">4f", data, start + 16 * i)
                start += off
            out.append((start, size, samplers, floats, literals))
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
# differently from run to run. The mad becomes lm * 1 + 0, lm itself: the 1 and the 0 of one
# of the shader's own literal constants (one_and_zero; c255.x and .y were taken before, which
# holds 1 and 0 only in some of the shaders).
# FM4_SHADERS_LIGHTMAP_COLOURS=0 leaves them.
LIGHTMAP_COLOURS = __import__("os").environ.get("FM4_SHADERS_LIGHTMAP_COLOURS", "1") == "1"
SWZ_X, SWZ_Y = 0x6C, 0xB1  # .xxxx and .yyyy as Xenos relative swizzles


def swizzle(k):
    """.kkkk (component k replicated) as a Xenos relative swizzle (SWZ_X for 0, SWZ_Y for 1)."""
    return sum(((k - j) & 3) << (2 * j) for j in range(4))


def one_and_zero(literals):
    """(register, component that is 1, component that is 0) of a literal constant holding both.
    The shaders' literals differ: c255 is (1, 0, 0, 0) in most, but (0, 1, 0, 0), (0, 0, 0, 0),
    (1.5, 0, 0, 0) or (2, 0.5, 10000, 1) in others, so none can be assumed."""
    for reg in sorted(literals, reverse=True):
        v = literals[reg]
        if 1.0 in v and 0.0 in v:
            return reg, v.index(1.0), v.index(0.0)
    return None


def lightmap_colours(data, start, alus, literals):
    done = []
    diffs = set()
    unit = one_and_zero(literals)
    if unit is None:
        return done
    lit, one, zero = unit
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
        # The difference operand becomes the literal's 1, src3 its 0; their negate bits cleared.
        shift = (16, 8)[slot]
        w2 = (w2 & ~(0xFF << shift)) | (lit << shift)
        w2 &= ~(1 << (31 - slot))          # a constant
        w1 = (w1 & ~(0xFF << shift)) | (swizzle(one) << shift)
        w1 &= ~(1 << (26 - slot))
        w2 = (w2 & ~0xFF) | lit
        w1 = (w1 & ~0xFF) | swizzle(zero)
        w1 &= ~(1 << 24)
        struct.pack_into(">3I", data, start + 12 * s, w[0], w1, w2)
        done.append(f"slot {s}: lightmap colour lerp(c247, c248) -> the lightmap")
    return done


# shad_color (the colour in FM4's shadows, c240 in the road, shoulder, grass and barrier
# shaders): FH1 never sets it for these draws, and its own data in that register reads as
# (denormal, NaN, 2.99, denormal), so the lerp(shad_color, 1, shadow) these shaders end with
# gave NaN and the road drew flat teal or another colour. With the shadow map read as 1 as
# well, it is made 1 (the literal's 1, replicated): no shadow tint. The negate bits stay, so
# 1 - shad_color is 0. FM4_SHADERS_SHADOW_COLOUR=0 leaves it.
SHADOW_COLOUR = __import__("os").environ.get("FM4_SHADERS_SHADOW_COLOUR", "1") == "1"


def constant_reads(data, start, alus, regs, lit, comp, what, value):
    """Every ALU read of a constant in regs becomes lit.comp, replicated (negate bits kept)."""
    done = []
    for s, f, w in alus:
        w1, w2 = w[1], w[2]
        hit = []
        for slot, (r, _, is_reg) in enumerate(f["src"]):
            if not is_reg and r in regs:
                shift = (16, 8, 0)[slot]
                w2 = (w2 & ~(0xFF << shift)) | (lit << shift)
                w1 = (w1 & ~(0xFF << shift)) | (swizzle(comp) << shift)
                hit.append(r)
        if hit:
            struct.pack_into(">3I", data, start + 12 * s, w[0], w1, w2)
            done.append(f"slot {s}: {what} c{hit[0]} -> {value}")
    return done


def shadow_colour(data, start, alus, floats, literals):
    reg = floats.get("shad_color")
    unit = one_and_zero(literals)
    if reg is None or unit is None:
        return []
    lit, one, zero = unit
    if FH1_SHADOWS:
        return constant_reads(data, start, alus, {reg[0]}, lit, zero, "shad_color", 0)
    return constant_reads(data, start, alus, {reg[0]}, lit, one, "shad_color", 1)


# FM4's shadow clip constants in the vertex shaders (shad_clip_near, c149-c151, and
# shad_clip_misc, c220, in the road, shoulder and barrier shaders): FH1 never sets them
# either, and its data there reads as NaNs, denormals and 2.99 or 25.18 depending on place and
# run. The vertex shader turns them into the shadow coordinates the pixel shader selects its
# shadow lookups with; with NaN there the road drew flat black at one spot in one run of two.
# The shadow term is 1 anyway (the shadow map read as 1, shad_color 1), so they are made 0
# (the literal's 0, replicated). FM4_SHADERS_SHADOW_CLIP=0 leaves them.
SHADOW_CLIP = __import__("os").environ.get("FM4_SHADERS_SHADOW_CLIP", "1") == "1"
SHADOW_CLIP_NAMES = ("shad_clip_near", "shad_clip_misc")


def shadow_clip(data, start, alus, floats, literals):
    regs = set()
    for name in SHADOW_CLIP_NAMES:
        if name in floats:
            reg, cnt = floats[name]
            regs.update(range(reg, reg + max(cnt, 1)))
    unit = one_and_zero(literals)
    if not regs or unit is None:
        return []
    lit, _, zero = unit
    return constant_reads(data, start, alus, regs, lit, zero, "shadow clip", 0)


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


def atlas_mask_one(data):
    """The file with the Blend_ValueSampler fetch read as 1 in each pixel shader that has no
    Blend_Value_uvOS (see ATLAS_MASK_SHADERS)."""
    data = bytearray(data)
    for start, size, samplers, floats, _ in blobs(bytes(data)):
        if "Blend_ValueSampler" not in samplers or "Blend_Value_uvOS" in floats:
            continue
        reg = samplers["Blend_ValueSampler"]
        for s, kind, w in instructions(bytes(data), start, size):
            if kind == "fetch" and (w[0] & 0x1F) == 1 and ((w[0] >> 20) & 0x1F) == reg:
                swz = w[1] & 0xFFF
                out = 0
                for c in range(4):
                    sel = (swz >> (3 * c)) & 7
                    out |= (7 if sel == 7 else 5) << (3 * c)
                struct.pack_into(">3I", data, start + 12 * s, w[0], (w[1] & ~0xFFF) | out, w[2])
    return bytes(data)


def one_layer(data, second):
    """The shader file with every pixel shader's Blend_ValueSampler fetch read as 0 (the first
    blend layer) or 1 (second): a terrain blend drawn with one layer (the ground, fm4_pvs.py)."""
    data = bytearray(data)
    for start, size, samplers, _, _ in blobs(bytes(data)):
        if "Blend_ValueSampler" not in samplers:
            continue
        reg = samplers["Blend_ValueSampler"]
        for s, kind, w in instructions(bytes(data), start, size):
            if kind == "fetch" and (w[0] & 0x1F) == 1 and ((w[0] >> 20) & 0x1F) == reg:
                swz = w[1] & 0xFFF
                out = 0
                for c in range(4):
                    sel = (swz >> (3 * c)) & 7
                    out |= (7 if sel == 7 else (5 if second else 4)) << (3 * c)
                struct.pack_into(">3I", data, start + 12 * s, w[0], (w[1] & ~0xFFF) | out, w[2])
    return bytes(data)


# Blend shaders (upper-case file names, no .fxobj) whose models take their blend mask from an
# FM4 atlas (a texture record with flag 0x8: a rectangle of a bigger texture), set by
# tools/fm4_pvs.py before it patches the track's shaders. FH1 has no atlas records: it binds
# the whole texture, and a pixel shader that samples the mask without a UV offset and scale of
# its own (no Blend_Value_uvOS) reads another part of it, mostly 0. Laguna Seca's road
# (ROAD_BLND_TRILIN_2) drew black that way. Those pixel shaders read the mask as 1 (the
# second blend layer, the asphalt) instead: no blend patches on that road, no black. The
# atlas tiles are 1/128 of their texture (8 x 8 texels of a 1024 one); FM4 must point the
# fetch at the tile itself, which FH1's records cannot say.
ATLAS_MASK_SHADERS = set()


def patch_fxobj(name, data):
    """The patched file and a list of what was done."""
    data = bytearray(data)
    done = []
    base = name.split("/")[-1].split("\\")[-1].upper()
    if os.environ.get("FM4_SHADERS_NO_LIGHTMAP") == "1":  # a test: every lightmap fetch reads 1
        for start, size, samplers, _, _ in blobs(bytes(data)):
            if "Light_MapSampler" not in samplers:
                continue
            reg = samplers["Light_MapSampler"]
            for s, kind, w in instructions(bytes(data), start, size):
                if kind == "fetch" and (w[0] & 0x1F) == 1 and ((w[0] >> 20) & 0x1F) == reg:
                    swz = w[1] & 0xFFF
                    out = 0
                    for c in range(4):
                        sel = (swz >> (3 * c)) & 7
                        out |= (7 if sel == 7 else 5) << (3 * c)
                    struct.pack_into(">3I", data, start + 12 * s, w[0], (w[1] & ~0xFFF) | out, w[2])
        done.append("lightmap -> 1 (test)")
    if base.endswith(".FXOBJ") and base[:-6] in ATLAS_MASK_SHADERS:
        data = bytearray(atlas_mask_one(bytes(data)))
        done.append("atlas blend mask -> 1")
    for start, size, samplers, floats, literals in blobs(bytes(data)):
        shadow = {r for n, r in samplers.items() if n in SHADOW_SAMPLERS}
        fm4_shadow = samplers.get("s_grayscaleShadowMap")
        ins = instructions(bytes(data), start, size)
        for s, kind, w in ins:
            if kind == "fetch" and (w[0] & 0x1F) == 1 and ((w[0] >> 20) & 0x1F) in shadow:
                swz = w[1] & 0xFFF
                one = 0
                for c in range(4):
                    sel = (swz >> (3 * c)) & 7
                    one |= (7 if sel == 7 else 5) << (3 * c)
                w0, w2 = w[0], w[2]
                marked = FH1_SHADOWS and ((w[0] >> 20) & 0x1F) == fm4_shadow
                if marked:
                    w0 = (w0 & ~(0x1F << 20)) | (FH1_SHADOW_MASK << 20)
                    w2 = (w2 & ~(0x3FF << 16)) | (0x10 << 16) | (0x0F << 21)
                w = [w0, (w[1] & ~0xFFF) | one, w2]
                struct.pack_into(">3I", data, start + 12 * s, *w)
                done.append(f"slot {s}: shadow map fetch -> 1" + (", FH1's mask (marked)" if marked else ""))
        alus = [(s, alu_fields(w), w) for s, kind, w in ins if kind == "alu"]
        if LIGHTMAP_COLOURS:
            done += lightmap_colours(data, start, alus, literals)
        if SHADOW_COLOUR:
            done += shadow_colour(data, start, alus, floats, literals)
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
    if SHADOW_CLIP:
        for start, size, _, floats, literals in blobs(bytes(data), VS_MAGIC):
            ins = instructions(bytes(data), start, size)
            alus = [(s, alu_fields(w), w) for s, kind, w in ins if kind == "alu"]
            done += ["VS " + d for d in shadow_clip(data, start, alus, floats, literals)]
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


def lightmap_uv_identity(data):
    """The shader file with every pixel shader's lightmap coordinate uv * Light_Map_uvOS.zw +
    Light_Map_uvOS.xy (a mad whose B and C are c116) made uv itself, as the identity (0, 0, 1, 1)
    would give: the mad becomes max(uv, uv) (B takes A's register, swizzle and sign), so no
    literal is needed (most of the passes have none holding a 1 and a 0). For copies drawn by
    models whose instance lightmaps are whole textures and whose command buffers never set the
    constant (tools/fm4_pvs.py, lightmap_uv_copies)."""
    data = bytearray(data)
    done = []
    for start, size, samplers, floats, literals in blobs(bytes(data)):
        reg = floats.get("Light_Map_uvOS")
        if reg is None:
            continue
        for s, kind, w in instructions(bytes(data), start, size):
            if kind != "alu":
                continue
            f = alu_fields(w)
            (ra, sa, ia), (rb, sb, ib), (rc, sc, ic) = f["src"]
            if f["vop"] != 11 or not ia or ib or ic or rb != reg[0] or rc != reg[0] or f["smask"]:
                continue
            # B must read only c116's scale (z, w) and C only its offset (x, y)
            if any(((j + ((sb >> (2 * j)) & 3)) & 3) < 2 or ((j + ((sc >> (2 * j)) & 3)) & 3) > 1
                   for j in range(4)):
                continue
            w1 = (w[1] & ~((0xFF << 8) | (1 << 25))) | (sa << 8) | (((w[1] >> 26) & 1) << 25)
            w2 = (w[2] & ~((0xFF << 8) | (0x1F << 24) | (1 << 30))) | (ra << 8) | (2 << 24) | (1 << 30)
            struct.pack_into(">3I", data, start + 12 * s, w[0], w1, w2)
            done.append(s)
    return bytes(data), done


def lightmap_uv_register(data, reg):
    """The shader file with the lightmap coordinate's mad (as in lightmap_uv_identity) reading
    its offset and scale from pixel constant reg instead of Light_Map_uvOS (c116): for copies
    drawn by models whose command buffers set reg to their instance lightmap's rectangle
    (tools/fm4_pvs.py, lightmap_rect_copies). Also the number of c116 reads left."""
    data = bytearray(data)
    done, left = [], 0
    for start, size, samplers, floats, literals in blobs(bytes(data)):
        uv = floats.get("Light_Map_uvOS")
        if uv is None:
            continue
        for s, kind, w in instructions(bytes(data), start, size):
            if kind != "alu":
                continue
            f = alu_fields(w)
            (ra, sa, ia), (rb, sb, ib), (rc, sc, ic) = f["src"]
            reads = sum(1 for r, _, i in f["src"] if not i and r == uv[0])
            if not reads:
                continue
            if (f["vop"] != 11 or not ia or ib or ic or rb != uv[0] or rc != uv[0] or f["smask"] or
                    any(((j + ((sb >> (2 * j)) & 3)) & 3) < 2 or ((j + ((sc >> (2 * j)) & 3)) & 3) > 1
                        for j in range(4))):
                left += reads
                continue
            w2 = (w[2] & ~0xFFFF) | (reg << 8) | reg
            struct.pack_into(">3I", data, start + 12 * s, w[0], w[1], w2)
            done.append(s)
    return bytes(data), done, left
