#include <algorithm>
#include <bit>
#include <cmath>
#include "gpu/hlsl.h"
#include "live_switch.h"

#include <cstdio>

namespace
{
using namespace ucode;

const char kPreamble[] = R"(
cbuffer VSConstants : register(b0)
{
    float4 vc[256];
    uint4 vb[2];      // 256 bool constants
    uint4 vl[8];      // 32 loop constants: count | start << 8 | step << 16
    uint4 vf[96];     // vertex fetch: x byte offset (gmem, or gphys if z), y endian, w size in dwords
    uint4 vinfo;      // x index list byte offset, y position mode (0 clip, 1 screen) | 2 points, z/w target size
    float4 vpoint;    // xy constant point diameter (pixels), z/w min/max diameter
    float4 vscale;    // PA_CL_VPORT_X/Y/ZSCALE
    float4 voffset;   // PA_CL_VPORT_X/Y/ZOFFSET
    uint4 vte;        // x PA_CL_VTE_CNTL
    // Vertex fetch k of this shader (see VFetchK): x fetch constant | format << 8 | signed << 14
    // | normalized << 15 | round << 16, y stride, z offset (dwords), w dest swizzle | exp << 16.
    uint4 vfl[32];
    // The game's own constants for the shaders it rewrote (Host::rewriteShader, set per draw in
    // Host::onDraw); zero unless it sets them.
    float4 vuser[4];
    // x: the polygon offset's constant part (PA_SU_POLY_OFFSET_*_OFFSET), added to depth: the console
    // adds it as an absolute amount, D3D12's depth bias scales it by each primitive's depth.
    float4 vdepth;
    // xy: clip-space factors for perspective geometry in a wide pass (outputs wider than
    // 16:9; 1 otherwise): x narrows the view to the wider screen (Hor+), y zooms in when
    // REGPU_WIDE_FOV limits how far the view widens; z keeps 2D (HUD, menus) at 16:9; w is 0
    // for draws that sample a resolved render target (screen layers, never squeezed).
    float4 vwide;
};
cbuffer PSConstants : register(b1)
{
    float4 pc[256];
    uint4 pb[2];
    uint4 pl[8];
    uint4 pinfo;      // x alpha test function (7 = always), y generated-parameter register (0xFF none)
    float4 pinfo2;    // x alpha reference, y/z 1 / render scale (pixel positions back to game pixels), w colour ceiling (0 none)
    uint4 tinfo[32];  // x sampler (0 lin wrap, 1 lin clamp, 2 point clamp, 3 point wrap), y/z size, w dimension (3 cube)
    float4 tbias[32]; // x fetch-constant LOD bias, y (as uint) bindless descriptor index
};
ByteAddressBuffer gmem : register(t0);
ByteAddressBuffer gphys : register(t1);   // guest physical memory (large vertex buffers, indices)
// Bindless textures: the fetch slot's heap index is in tbias[slot].y.
Texture2D<float4> gtex[] : register(t0, space1);
TextureCube<float4> gcube[] : register(t0, space2);
Texture3D<float4> gvol[] : register(t0, space3);
SamplerState sLinearWrap : register(s0);
SamplerState sLinearClamp : register(s1);
SamplerState sPointClamp : register(s2);
SamplerState sPointWrap : register(s3);
SamplerState sAniso2Wrap : register(s4);
SamplerState sAniso4Wrap : register(s5);
SamplerState sAniso8Wrap : register(s6);
SamplerState sAniso16Wrap : register(s7);

static const float FLT_MAX = 3.402823466e+38;

struct VSOut
{
    float4 pos : SV_Position;
    float4 o[16] : TEXCOORD0;
    float2 pcoord : TEXCOORD16; // point sprite coordinate
};

uint Swap(uint v, uint e)
{
    if (e == 1) return ((v & 0x00FF00FFu) << 8) | ((v >> 8) & 0x00FF00FFu);
    if (e == 2) return (v >> 24) | ((v >> 8) & 0xFF00u) | ((v << 8) & 0xFF0000u) | (v << 24);
    if (e == 3) return (v << 16) | (v >> 16);
    return v;
}

uint LoadGuest(uint fetch, uint dword)
{
    // Past the end of the buffer the Xenos fetches 0. The car shadow volumes rely on it: their 10-vertex
    // buffer is indexed far beyond its end (the index picks a corner constant), and the memory after it
    // read as NaN positions, whose depth failed every test, so the shadows never reached the stencil.
    if (dword >= vf[fetch].w)
        return 0;
    uint a = vf[fetch].x + dword * 4;
    return Swap(vf[fetch].z ? gphys.Load(a) : gmem.Load(a), vf[fetch].y);
}

float SignExtend(uint v, uint bits) { return float(int(v << (32 - bits)) >> (32 - bits)); }

// Xenos 16-bit float: like IEEE half but exponent 31 is an ordinary exponent
// (no infinities or NaNs), so the range reaches 131008.
float XHalf(uint h)
{
    h &= 0xFFFF;
    if (((h >> 10) & 31) == 31)
    {
        float v = (1.0 + float(h & 1023) / 1024.0) * 65536.0;
        return (h & 0x8000) ? -v : v;
    }
    return f16tof32(h);
}

float4 Unpack(uint w0, uint w1, uint w2, uint w3, uint format, bool isSigned, bool norm)
{
    float4 r = float4(0, 0, 0, 1);
    switch (format)
    {
    case 6: // 8_8_8_8
    {
        uint4 u = uint4(w0 & 0xFF, (w0 >> 8) & 0xFF, (w0 >> 16) & 0xFF, w0 >> 24);
        if (isSigned) { r = float4(SignExtend(u.x, 8), SignExtend(u.y, 8), SignExtend(u.z, 8), SignExtend(u.w, 8)); if (norm) r = max(r / 127.0, -1.0); }
        else { r = float4(u); if (norm) r /= 255.0; }
        break;
    }
    case 7: // 2_10_10_10
    {
        uint4 u = uint4(w0 & 0x3FF, (w0 >> 10) & 0x3FF, (w0 >> 20) & 0x3FF, w0 >> 30);
        if (isSigned) { r = float4(SignExtend(u.x, 10), SignExtend(u.y, 10), SignExtend(u.z, 10), SignExtend(u.w, 2)); if (norm) r = max(r / float4(511, 511, 511, 1), -1.0); }
        else { r = float4(u); if (norm) r /= float4(1023, 1023, 1023, 3); }
        break;
    }
    case 25: // 16_16
    case 26: // 16_16_16_16
    {
        uint4 u = uint4(w0 & 0xFFFF, w0 >> 16, w1 & 0xFFFF, w1 >> 16);
        if (isSigned) { r = float4(SignExtend(u.x, 16), SignExtend(u.y, 16), SignExtend(u.z, 16), SignExtend(u.w, 16)); if (norm) r = max(r / 32767.0, -1.0); }
        else { r = float4(u); if (norm) r /= 65535.0; }
        if (format == 25) r.zw = float2(0, 1);
        break;
    }
    case 31: r = float4(XHalf(w0), XHalf(w0 >> 16), 0, 1); break;
    case 32: r = float4(XHalf(w0), XHalf(w0 >> 16), XHalf(w1), XHalf(w1 >> 16)); break;
    case 33: r = float4(isSigned ? float(int(w0)) : float(w0), 0, 0, 1); break;
    case 34: r = float4(isSigned ? float2(int2(w0, w1)) : float2(w0, w1), 0, 1); break;
    case 35: r = isSigned ? float4(int4(w0, w1, w2, w3)) : float4(w0, w1, w2, w3); break;
    case 36: r = float4(asfloat(w0), 0, 0, 1); break;
    case 37: r = float4(asfloat(w0), asfloat(w1), 0, 1); break;
    case 57: r = float4(asfloat(w0), asfloat(w1), asfloat(w2), 1); break;
    case 38: r = asfloat(uint4(w0, w1, w2, w3)); break;
    case 16: // 10_11_11: X 11 bits, Y 11 bits, Z 10 bits from the bottom
    case 17: // 11_11_10: X 10 bits, Y 11 bits, Z 11 bits
    {
        uint3 bits = format == 16 ? uint3(11, 11, 10) : uint3(10, 11, 11);
        uint3 u = uint3(w0 & ((1u << bits.x) - 1), (w0 >> bits.x) & ((1u << bits.y) - 1), w0 >> (bits.x + bits.y));
        if (isSigned)
        {
            r.xyz = float3(SignExtend(u.x, bits.x), SignExtend(u.y, bits.y), SignExtend(u.z, bits.z));
            if (norm) r.xyz = max(r.xyz / float3((1u << (bits - 1)) - 1), -1.0);
        }
        else
        {
            r.xyz = float3(u);
            if (norm) r.xyz /= float3((1u << bits) - 1);
        }
        break;
    }
    }
    return r;
}

float4 VFetch(uint fetch, float index, uint stride, uint offset, uint format, bool isSigned, bool norm)
{
    uint d = uint(max(index, 0.0)) * stride + offset; // index already rounded or floored
    return Unpack(LoadGuest(fetch, d), LoadGuest(fetch, d + 1), LoadGuest(fetch, d + 2), LoadGuest(fetch, d + 3), format, isSigned, norm);
}

// The title binds each vertex shader to the vertex layout it draws by patching its fetch
// instructions (constant slot, format, stride, offset, swizzle). Reading those fields from
// vfl instead of baking them in makes every patched copy of a shader the same HLSL, so they
// share one compile.
float4 VFetchK(uint k, float i)
{
    uint4 p = vfl[k];
    float idx = (p.x & 0x10000u) ? round(i) : floor(i);
    return VFetch(p.x & 0x7Fu, idx, p.y, p.z, (p.x >> 8) & 0x3Fu, (p.x & 0x4000u) != 0, (p.x & 0x8000u) != 0) * exp2(float(int(p.w << 10) >> 26));
}

float4 VMerge(uint k, float4 old, float4 t)
{
    uint sw = vfl[k].w & 0xFFFu;
    float4 r = old;
    [unroll] for (uint c = 0; c < 4; c++)
    {
        uint s = (sw >> (3 * c)) & 7u;
        r[c] = s < 4 ? t[s] : (s == 4 ? 0.0 : (s == 5 ? 1.0 : old[c]));
    }
    return r;
}

// Cube fetch coordinates are (s, t, face) as produced by the CUBE instruction
// plus the usual 1.5 bias: s, t in [1, 2]. Back to a direction for the host.
float3 CubeDirection(float3 stf)
{
    float sc = (stf.x - 1.5) * 2.0, tc = (stf.y - 1.5) * 2.0;
    uint face = uint(round(stf.z));
    switch (face)
    {
    case 0: return float3(1, -tc, -sc);
    case 1: return float3(-1, -tc, sc);
    case 2: return float3(sc, 1, tc);
    case 3: return float3(sc, -1, -tc);
    case 4: return float3(sc, -tc, 1);
    default: return float3(-sc, -tc, -1);
    }
}

float4 TFetchRaw(uint slot, float3 coord, bool denorm, bool vertexStage, float bias, float2 texel);
static int gFilterOverride = -1; // the fetch instruction's filter (0 point, 1 linear), -1 the constant's
// The sampler for a fetch: the constant's mode (0 linear wrap, 1 linear clamp, 2 point clamp, 3 point
// wrap), its filter replaced by the instruction's when it has one.
uint FetchMode(uint mode)
{
    const bool wrap = mode == 0u || mode == 3u;
    if (gFilterOverride == 0) return wrap ? 3u : 2u;
    if (gFilterOverride == 1) return wrap ? 0u : 1u;
    return mode;
}

// Sampling with a LOD bias is a pixel-shader-only operation: a vertex shader cannot compute an
// implicit LOD, and dxc rejects the module outright ("sampling with implicit lod is only allowed
// in fragment and compute shaders"), where fxc merely ignored the dead branch. The translator
// emits the same source for both stages, so the stage picks the form here. A macro, not a
// function: a call in the fetch path keeps fxc from inlining and costs half the frame rate.
#ifdef REGPU_VERTEX_STAGE
#define REGPU_SAMPLE_BIAS_2D(t, s, uv, bias) ((t).SampleLevel((s), (uv), max((bias), 0)))
#define REGPU_SAMPLE_BIAS_CUBE(t, s, dir, bias) ((t).SampleLevel((s), (dir), max((bias), 0)))
#else
#define REGPU_SAMPLE_BIAS_2D(t, s, uv, bias) ((t).SampleBias((s), (uv), (bias)))
#define REGPU_SAMPLE_BIAS_CUBE(t, s, dir, bias) ((t).SampleBias((s), (dir), (bias)))
#endif

// The Xenos texture fetch's gamma sign: the texel holds piecewise-linear gamma, converted to linear
// (Xenia's PWLGammaToLinear).
float PWLGammaToLinear(float g)
{
    g = saturate(g);
    float scale, offset;
    if (g >= 96.0 / 255.0)
    {
        if (g >= 192.0 / 255.0) { scale = 8.0 / 1024.0; offset = -1024.0; }
        else { scale = 4.0 / 1024.0; offset = -256.0; }
    }
    else
    {
        if (g >= 64.0 / 255.0) { scale = 2.0 / 1024.0; offset = -64.0; }
        else { scale = 1.0 / 1024.0; offset = 0.0; }
    }
    float l = g * ((255.0 * 1024.0) * scale) + offset;
    l += trunc(l * scale);
    return l / 1023.0;
}

float4 TFetch(uint slot, float3 coord, bool denorm, bool vertexStage, float bias, float2 texel)
{
    float4 v = TFetchRaw(slot, coord, denorm, vertexStage, bias, texel);
    // Gamma-signed components (tinfo.x bits 24-27, in v's order): before the exponent adjust.
    uint gamma = (tinfo[slot].x >> 24) & 0xFu;
    if (gamma != 0u)
    {
        [unroll] for (uint g = 0; g < 4; g++)
            if ((gamma >> g) & 1u)
                v[g] = PWLGammaToLinear(v[g]);
    }
    // Exponent adjust (fetch constant): texels scaled by 2^n, n in tinfo.x bits 8-13.
    int expAdjust = int(tinfo[slot].x << 18) >> 26;
    if (expAdjust != 0) v *= exp2(float(expAdjust));
    uint w = tinfo[slot].w;
    if (!(w >> 31))
        return v;
    float4 r;
    [unroll] for (uint c = 0; c < 4; c++)
    {
        uint sel = (w >> (8 + 3 * c)) & 7u;
        r[c] = sel < 4u ? v[sel] : (sel == 5u ? 1.0 : 0.0);
    }
    return r;
}

// The Xenos clamp modes that mirror (fetch dword 0 bits 10-15): 1 mirrored repeat folds every other
// tile back; 3, 5 and 7 mirror once about 0 and then clamp (the sampler or the axis clamp does that).
float2 Mirror(float2 uv, uint info)
{
    if ((info >> 28) & 1u) uv.x = ((info >> 30) & 1u) ? abs(uv.x) : 1.0 - abs(frac(uv.x * 0.5) * 2.0 - 1.0);
    if ((info >> 29) & 1u) uv.y = ((info >> 31) & 1u) ? abs(uv.y) : 1.0 - abs(frac(uv.y * 0.5) * 2.0 - 1.0);
    return uv;
}

float4 TFetchRaw(uint slot, float3 coord, bool denorm, bool vertexStage, float bias, float2 texel)
{
    uint index = asuint(tbias[slot].y);
    Texture2D<float4> t = gtex[index];
    TextureCube<float4> tc = gcube[index];
    if ((tinfo[slot].w & 0xFFu) == 2) // 3D
    {
        Texture3D<float4> tv = gvol[index];
        const uint vmode = tinfo[slot].x & 3u;
        if (vertexStage) return tv.SampleLevel(sLinearClamp, coord, max(bias, 0));
        if (vmode == 1) return tv.SampleBias(sLinearClamp, coord, bias);
        if (vmode == 2) return tv.SampleBias(sPointClamp, coord, bias);
        if (vmode == 3) return tv.SampleBias(sPointWrap, coord, bias);
        return tv.SampleBias(sLinearWrap, coord, bias);
    }
    if ((tinfo[slot].w & 0xFFu) == 3)
    {
        float3 dir = CubeDirection(coord);
        return vertexStage ? tc.SampleLevel(sLinearClamp, dir, max(bias, 0)) : REGPU_SAMPLE_BIAS_CUBE(tc, sLinearClamp, dir, bias);
    }
    float2 uv = coord.xy;
    const float2 texelSize = float2(max(tinfo[slot].yz & 0xFFFFu, uint2(1, 1)));
    if (denorm) uv = (uv + texel) / texelSize;
    else uv += texel / texelSize;
    // Mirrored (tinfo.x bits 28 U, 29 V: repeating; 30 U, 31 V: once, then clamped below).
    if ((tinfo[slot].x >> 28) & 1u) uv.x = 1.0 - abs(frac(uv.x * 0.5) * 2.0 - 1.0);
    if ((tinfo[slot].x >> 29) & 1u) uv.y = 1.0 - abs(frac(uv.y * 0.5) * 2.0 - 1.0);
    if ((tinfo[slot].x >> 30) & 1u) uv.x = abs(uv.x);
    if ((tinfo[slot].x >> 31) & 1u) uv.y = abs(uv.y);
    // Clamp to border (tinfo.x bit 16; bit 17 white): outside the image the border colour, as
    // shadow maps rely on (white: nothing casts a shadow beyond the map).
    if (((tinfo[slot].x >> 16) & 1u) && any(uv < 0.0 || uv > 1.0))
        return ((tinfo[slot].x >> 17) & 1u) ? float4(1, 1, 1, 1) : float4(0, 0, 0, 0);
    // One axis clamped, the other wrapping (tinfo.x bits 18 U, 19 V): clamp that axis here to
    // the edge texels' centres; the sampler wraps.
    if ((tinfo[slot].x >> 18) & 1u) uv.x = clamp(uv.x, 0.5 / texelSize.x, 1.0 - 0.5 / texelSize.x);
    if ((tinfo[slot].x >> 19) & 1u) uv.y = clamp(uv.y, 0.5 / texelSize.y, 1.0 - 0.5 / texelSize.y);
    if (tbias[slot].z > 0.0) uv *= tbias[slot].zw; // declared image inside a wider resolved texture (renderer.cpp)
    uint mode = FetchMode(tinfo[slot].x & 3u);
    // Vertex shaders read data tables this way (PGR4's riders: bone matrices, three texels a bone):
    // the fetch constant's filter applies, point sampling exactly one texel. Always linear here,
    // each matrix row was blended with its neighbours and the bodies came out mangled.
    // (fxc wants a literal sampler per call: one branch each.)
    if (vertexStage && mode == 2)
        return t.SampleLevel(sPointClamp, uv, max(bias, 0));
    if (vertexStage && mode == 3)
        return t.SampleLevel(sPointWrap, uv, max(bias, 0));
    if (vertexStage)
        return t.SampleLevel(sLinearClamp, uv, max(bias, 0));
    if (mode == 1) return REGPU_SAMPLE_BIAS_2D(t, sLinearClamp, uv, bias);
    if (mode == 2) return REGPU_SAMPLE_BIAS_2D(t, sPointClamp, uv, bias);
    if (mode == 3) return REGPU_SAMPLE_BIAS_2D(t, sPointWrap, uv, bias);
    // The fetch's anisotropy (renderer.cpp, tinfo.x bits 20-22): 1 2x, 2 4x, 3 8x, 4 16x.
    uint aniso = (tinfo[slot].x >> 20) & 7u;
    if (aniso == 1u) return REGPU_SAMPLE_BIAS_2D(t, sAniso2Wrap, uv, bias);
    if (aniso == 2u) return REGPU_SAMPLE_BIAS_2D(t, sAniso4Wrap, uv, bias);
    if (aniso == 3u) return REGPU_SAMPLE_BIAS_2D(t, sAniso8Wrap, uv, bias);
    if (aniso == 4u) return REGPU_SAMPLE_BIAS_2D(t, sAniso16Wrap, uv, bias);
    return REGPU_SAMPLE_BIAS_2D(t, sLinearWrap, uv, bias);
}

float4 CubeOp(float4 a, float4 b)
{
    float3 v = float3(a.z, a.w, a.x);
    float3 m = abs(v);
    float sc, tc, ma, face;
    if (m.z >= m.x && m.z >= m.y) { ma = v.z; face = v.z < 0 ? 5 : 4; sc = v.z < 0 ? -v.x : v.x; tc = -v.y; }
    else if (m.y >= m.x) { ma = v.y; face = v.y < 0 ? 3 : 2; sc = v.x; tc = v.y < 0 ? -v.z : v.z; }
    else { ma = v.x; face = v.x < 0 ? 1 : 0; sc = v.x < 0 ? v.z : -v.z; tc = -v.y; }
    return float4(tc, sc, 2.0 * abs(ma), face);
}

// Direct3D 9 multiplication, as the Xenos ALU does it: zero times anything (even
// infinity or NaN) is zero. Shaders rely on it to switch terms off by weight.
float4 Mul9(float4 a, float4 b) { return (a == 0 || b == 0) ? 0 : a * b; }
float Mul9(float a, float b) { return (a == 0 || b == 0) ? 0 : a * b; }
float Dot9(float4 a, float4 b) { float4 m = Mul9(a, b); return m.x + m.y + m.z + m.w; }

bool BoolConstant(uint4 b[2], uint index) { return (b[index >> 7][(index >> 5) & 3] >> (index & 31)) & 1; }
)";

struct Emitter
{
    const Shader& shader;
    bool pixel;
    std::string out;
    std::string error;
    TranslatedShader& result;
    bool layoutOnly = false;   // only collect the vertex fetch layout (no HLSL)
    bool literalFetch = false; // a fetch had to be baked into the code
    mutable bool exportsDepth = false; // a pixel shader writing depth (export 61)
    uint32_t lastStride = 0;
    uint32_t lastSrc = 0, lastSrcSwizzle = 0; // mini fetches reuse the full fetch's index
    uint32_t lastConst = 0; // and its fetch constant (a mini's own constant field is unused)
    bool saveFetchIndex = false; // minis read the index their full fetch kept (vfi), see ScanFetchIndex
    bool lastRound = false;
    int indent = 1;

    void Line(const std::string& s)
    {
        out.append(size_t(indent) * 4, ' ');
        out += s;
        out += '\n';
    }

    std::string Constant(uint32_t index, uint32_t relative = 0) const
    {
        result.constantsUsed = relative ? 256 : std::max(result.constantsUsed, (index & 255) + 1);
        if (relative)
            result.constantMask.fill(~0ull);
        else
            result.constantMask[(index & 255) >> 6] |= 1ull << (index & 63);
        char b[48];
        if (relative)
            snprintf(b, sizeof(b), "%s[(%u + %s) & 255]", pixel ? "pc" : "vc", index & 255, relative == 1 ? "a0" : "aL");
        else
            snprintf(b, sizeof(b), "%s[%u]", pixel ? "pc" : "vc", index & 255);
        return b;
    }

    std::string Src(const Source& s) const
    {
        static const char comp[] = "xyzw";
        std::string base = s.isConstant ? Constant(s.reg, s.relative) : "r[" + std::to_string(s.reg) + "]";
        if (s.abs)
            base = "abs(" + base + ")";
        std::string swz = ".";
        for (int c = 0; c < 4; c++)
            swz += comp[s.swizzle[c]];
        return std::string(s.negate ? "(-" : "(") + base + swz + ")";
    }

    // Export register to an HLSL lvalue, or empty if the write is ignored.
    std::string ExportTarget(uint32_t reg) const
    {
        if (pixel)
        {
            if (reg == 61) // depth, in X (Xenia's kPSDepth)
            {
                exportsDepth = true;
                return "oD";
            }
            return reg < 4 ? "oC[" + std::to_string(reg) + "]" : "";
        }
        if (reg >= 32 && reg <= 37)
            result.memExport = true; // eA, eM0-4 (dropped: captures mark these draws)
        if (reg == 63)
            return "opts";
        if (reg == 62)
            return "opos";
        if (reg < 16)
        {
            result.interpolatorsWritten |= 1u << reg;
            return "o[" + std::to_string(reg) + "]";
        }
        return "";
    }

    static std::string Masked(const std::string& target, const std::string& value, uint32_t mask)
    {
        // One swizzled write for the components in order (r.xyz = v.xyz), unless REGPU_FULL_ALU.
        if (!FullAlu() && std::popcount(mask & 15u) > 1)
        {
            std::string comps;
            for (int c = 0; c < 4; c++)
                if ((mask >> c) & 1)
                    comps += "xyzw"[c];
            return target + "." + comps + " = " + value + "." + comps + "; ";
        }
        std::string s;
        for (int c = 0; c < 4; c++)
        {
            if ((mask >> c) & 1)
            {
                const char comp = "xyzw"[c];
                s += target + "." + comp + " = " + value + "." + comp + "; ";
            }
        }
        return s;
    }

    std::string VectorExpr(const Alu& a, const std::string& A, const std::string& B, const std::string& C)
    {
        switch (a.vectorOp)
        {
        case 0: return A + " + " + B;
        case 1: return "Mul9(" + A + ", " + B + ")";
        case 2: return "max(" + A + ", " + B + ")";
        case 3: return "min(" + A + ", " + B + ")";
        case 4: return "float4(" + A + " == " + B + ")";
        case 5: return "float4(" + A + " > " + B + ")";
        case 6: return "float4(" + A + " >= " + B + ")";
        case 7: return "float4(" + A + " != " + B + ")";
        case 8: return "frac(" + A + ")";
        case 9: return "trunc(" + A + ")";
        case 10: return "floor(" + A + ")";
        case 11: return "Mul9(" + A + ", " + B + ") + " + C;
        case 12: return "(" + A + " == 0.0) ? " + B + " : " + C;
        case 13: return "(" + A + " >= 0.0) ? " + B + " : " + C;
        case 14: return "(" + A + " > 0.0) ? " + B + " : " + C;
        case 15: return "Dot9(" + A + ", " + B + ").xxxx";
        case 16: return "Dot9(float4((" + A + ").xyz, 0), float4((" + B + ").xyz, 0)).xxxx";
        case 17: return "(Dot9(float4((" + A + ").xy, 0, 0), float4((" + B + ").xy, 0, 0)) + (" + C + ").x).xxxx";
        case 18: // CUBE: operands are v.zzxy and v.yxzz; result (tc, sc, 2 * major axis, face)
            return "CubeOp(" + A + ", " + B + ")";
        case 19: { std::string t = A; return "max(max(" + t + ".x, " + t + ".y), max(" + t + ".z, " + t + ".w)).xxxx"; }
        case 28: return "float4(1.0, (" + A + ").y * (" + B + ").y, (" + A + ").z, (" + B + ").w)";
        case 29: return "max(" + A + ", " + B + ")"; // MAXAv: a0 is set by the caller
        default: return "float4(0, 0, 0, 0)";
        }
    }

    // Emits code computing the scalar result into `s`; may update p.
    std::string ScalarCode(const Alu& a)
    {
        const std::string sv = Src(a.src[2]);
        const std::string A = sv + ".w", B = sv + ".x";
        auto set = [](const std::string& e) { return "s = " + e + ";"; };
        switch (a.scalarOp)
        {
        case 0: return set(A + " + " + B);
        case 1: return set(A + " + ps");
        case 2: return set("Mul9(" + A + ", " + B + ")");
        case 3: case 4: return set("Mul9(" + A + ", ps)");
        case 5: return set("max(" + A + ", " + B + ")");
        case 6: return set("min(" + A + ", " + B + ")");
        case 7: return set("float(" + A + " == 0.0)");
        case 8: return set("float(" + A + " > 0.0)");
        case 9: return set("float(" + A + " >= 0.0)");
        case 10: return set("float(" + A + " != 0.0)");
        case 11: return set("frac(" + A + ")");
        case 12: return set("trunc(" + A + ")");
        case 13: return set("floor(" + A + ")");
        case 14: return set("exp2(" + A + ")");
        case 15: return "s = log2(" + A + "); if (isinf(s)) s = -FLT_MAX;";
        case 16: return set("log2(" + A + ")");
        case 17: return "s = 1.0 / " + A + "; s = clamp(s, -FLT_MAX, FLT_MAX);";
        case 18: return "s = 1.0 / " + A + "; if (isinf(s)) s = 0.0;";
        case 19: return set("1.0 / " + A);
        case 20: return "s = rsqrt(" + A + "); s = clamp(s, -FLT_MAX, FLT_MAX);";
        case 21: return "s = rsqrt(" + A + "); if (isinf(s)) s = 0.0;";
        case 22: return set("rsqrt(" + A + ")");
        // MAXAs / MAXAsf: also load the address register.
        case 23: return set("max(" + A + ", " + B + ")") + " a0 = clamp(int(floor(" + A + " + 0.5)), -256, 255);";
        case 24: return set("max(" + A + ", " + B + ")") + " a0 = clamp(int(floor(" + A + ")), -256, 255);";
        case 25: return set(A + " - " + B);
        case 26: return set(A + " - ps");
        case 27: return "p = (" + A + " == 0.0); s = p ? 0.0 : 1.0;";
        case 28: return "p = (" + A + " != 0.0); s = p ? 0.0 : 1.0;";
        case 29: return "p = (" + A + " > 0.0); s = p ? 0.0 : 1.0;";
        case 30: return "p = (" + A + " >= 0.0); s = p ? 0.0 : 1.0;";
        // setp_inv: src 1 sets the predicate (dest 0); otherwise it clears, dest 1 for 0 or src itself
        // (Xenia's ucode.h). The predicate was set for 0 instead: FH1's sun-shadow passes then ran their
        // far cascade's taps on pixels the near ones had already shaded, a dark band ahead of the camera.
        case 31: return "p = (" + A + " == 1.0); s = p ? 0.0 : ((" + A + " == 0.0) ? 1.0 : " + A + ");";
        case 32: return "p = (" + A + " - 1.0 <= 0.0); s = p ? 0.0 : " + A + " - 1.0;";
        case 33: return "p = false; s = FLT_MAX;";
        case 34: return "p = (" + A + " == 0.0); s = " + A + ";";
        case 35: case 36: case 37: case 38: case 39:
        {
            static const char* cmp[] = { "== 0.0", "> 0.0", ">= 0.0", "!= 0.0", "== 1.0" };
            const std::string c = A + " " + cmp[a.scalarOp - 35];
            return std::string(pixel ? "if ((" + c + ") && !(pinfo.z & 4u)) discard; " : "") + "s = float(" + c + ");";
        }
        case 40: return set("sqrt(" + A + ")");
        case 42: case 43: case 44: case 45: case 46: case 47:
        {
            // Constant operand (component W of src3's swizzle) with a temp
            // whose index is spread over the opcode and swizzle bits.
            const uint32_t temp = (a.scalarOp & 1) | (uint32_t(a.src3IsTemp) << 1) | (a.src3RawSwizzle & 0x3C);
            static const char comp[] = "xyzw";
            const std::string ca = Constant(a.src3RawReg) + "." + comp[((a.src3RawSwizzle >> 6) + 3) & 3];
            const std::string tb = "r[" + std::to_string(temp) + "]." + comp[a.src3RawSwizzle & 3];
            const char* op = a.scalarOp <= 43 ? " * " : (a.scalarOp <= 45 ? " + " : " - ");
            const char* neg = a.src[2].negate ? "-" : "";
            if (a.scalarOp <= 43)
                return set(std::string("Mul9(") + neg + ca + ", " + neg + tb + ")");
            static const int negMode = getenv("REGPU_SC_NEG") ? atoi(getenv("REGPU_SC_NEG")) : 0; // 0 both, 1 constant, 2 temp
            const char* negC = negMode == 2 ? "" : neg;
            const char* negT = negMode == 1 ? "" : neg;
            return set(std::string("(") + negC + ca + ")" + op + "(" + negT + tb + ")");
        }
        case 48: return set("sin(" + A + ")");
        case 49: return set("cos(" + A + ")");
        case 50: return "s = ps;";
        default:
            error = "scalar op " + std::to_string(a.scalarOp);
            return "s = 0.0;";
        }
    }

    // REGPU_FULL_ALU=1: every instruction computes both halves through v and s, and masked writes go one
    // component at a time, as before (A/B).
    static bool FullAlu()
    {
        static const bool full = getenv("REGPU_FULL_ALU") != nullptr;
        return full;
    }

    void EmitAlu(const Alu& a)
    {
        // Leaner code for the common cases, the same results: the scalar half "retain previous" (op 50,
        // unclamped, unwritten) leaves ps as it is, and a vector result nothing reads (no component
        // written, no predicate, kill or address side effect) isn't computed. Shaders compile without
        // optimisation, so all of it reached the GPU's compiler as written (FM2 port: 229 -> 240 fps).
        const bool lean = !FullAlu();
        const bool scalarKept = lean && a.scalarOp == 50 && !a.scalarClamp && !a.scalarMask;
        const bool vectorSideEffect = (a.vectorOp >= 20 && a.vectorOp <= 27) || a.vectorOp == 29;
        const bool vectorUnused = lean && !a.vectorMask && !vectorSideEffect &&
            !(a.exportData && a.scalarDestRelative); // (a relative export zeroes the unwritten components)
        if (scalarKept && vectorUnused && !a.exportData)
            return; // a no-op
        std::string body = std::string("{ ") + (vectorUnused ? "" : "float4 v = float4(0, 0, 0, 0); ") + (scalarKept ? "" : "float s = ps; ");
        const std::string A = Src(a.src[0]), B = Src(a.src[1]), C = Src(a.src[2]);
        if (vectorUnused)
            ;
        else if (a.vectorOp >= 20 && a.vectorOp <= 23)
        {
            static const char* cmp[] = { "== 0.0", "!= 0.0", "> 0.0", ">= 0.0" };
            // SETP_*_PUSH (Xenia ucode.h): p = src0.w == 0 && src1.w <op> 0; dest = 0 when
            // src0.x == 0 && src1.x <op> 0, else src0.x + 1. (The comparison was on the wrong
            // operand: skinned meshes' extra bones never applied, PGR4's riders came out pinched.)
            body += "{ p = (" + A + ".w == 0.0) && (" + B + ".w " + cmp[a.vectorOp - 20] + "); bool c = (" + A + ".x == 0.0) && (" + B + ".x " +
                cmp[a.vectorOp - 20] + "); v = c ? 0.0 : (" + A + ".x + 1.0).xxxx; } ";
        }
        else if (a.vectorOp >= 24 && a.vectorOp <= 27)
        {
            static const char* cmp[] = { "==", ">", ">=", "!=" };
            body += "{ bool k = any(" + A + " " + cmp[a.vectorOp - 24] + " " + B + "); v = float(k).xxxx; ";
            // (pinfo.z bit 2: kills skipped, renderer.cpp: the vertex shader wrote no interpolators.)
            if (pixel)
                body += "if (k && !(pinfo.z & 4u)) discard; ";
            body += "} ";
        }
        else
            body += "v = " + VectorExpr(a, A, B, C) + "; ";
        if (a.vectorClamp && !vectorUnused)
            body += "v = saturate(v); ";
        if (!scalarKept)
            body += ScalarCode(a) + " ";
        if (a.vectorOp == 29) // MAXAv: a0 from the first operand's W, after the reads
            body += "a0 = clamp(int(floor((" + A + ").w + 0.5)), -256, 255); ";
        if (a.scalarClamp)
            body += "s = saturate(s); ";
        if (!scalarKept)
            body += "ps = s; ";

        if (a.exportData)
        {
            const std::string target = ExportTarget(a.vectorDest);
            if (!target.empty())
            {
                const uint32_t vm = a.vectorMask & ~a.scalarMask, sm = a.scalarMask & ~a.vectorMask;
                const uint32_t one = a.vectorMask & a.scalarMask;
                const uint32_t zero = a.scalarDestRelative ? (~(a.vectorMask | a.scalarMask) & 15) : 0;
                body += Masked(target, "v", vm) + Masked(target, "s.xxxx", sm) + Masked(target, "float4(1, 1, 1, 1)", one) +
                    Masked(target, "float4(0, 0, 0, 0)", zero);
            }
        }
        else
        {
            body += Masked("r[" + std::to_string(a.vectorDest) + "]", "v", a.vectorMask);
            body += Masked("r[" + std::to_string(a.scalarDest) + "]", "s.xxxx", a.scalarMask);
        }
        body += "}";
        if (a.predicated)
            body = std::string("if (") + (a.predCondition ? "p" : "!p") + ") " + body;
        Line(body);
    }

    static std::string DestSwizzleWrite(uint32_t dest, uint32_t swizzle, const std::string& value)
    {
        std::string s;
        const std::string target = "r[" + std::to_string(dest) + "]";
        for (int c = 0; c < 4; c++)
        {
            const uint32_t sel = (swizzle >> (3 * c)) & 7;
            const char comp = "xyzw"[c];
            if (sel < 4)
                s += target + "." + comp + " = " + value + "." + "xyzw"[sel] + "; ";
            else if (sel == 4)
                s += target + "." + comp + " = 0.0; ";
            else if (sel == 5)
                s += target + "." + comp + " = 1.0; ";
        }
        return s;
    }

    void EmitFetch(const Fetch& f)
    {
        std::string body;
        if (f.opcode == 0)
        {
            if (pixel)
            {
                error = "vertex fetch in a pixel shader";
                return;
            }
            if (!f.isMini)
            {
                lastConst = f.constIndex;
                lastStride = f.stride;
                lastSrc = f.src;
                lastSrcSwizzle = f.srcSwizzle;
                lastRound = f.roundIndex;
            }
            if (getenv("REGPU_TRACE_VFETCH"))
                fprintf(stderr, "vfetch: fmt %u signed %d norm %d exp %d stride %u offset %u mini %d round %d\n", f.format, f.isSigned, f.isNormalized,
                    f.expAdjust, lastStride, f.offset, f.isMini, lastRound);
            const uint32_t k = uint32_t(result.vertexFetchLayout.size());
            // A mini fetch (vfetch_mini) reads the vertex its full fetch addressed, through the full fetch's
            // constant and index: a mini carries only its format, offset and destination (FM4 follows a vf94
            // table fetch with a mini whose own constant field says vf95: every car drawn damaged). From the
            // FM4 port. REGPU_NO_MINI_CONST=1 takes the mini's own field again (A/B; read at translation).
            static const bool miniOwnConst = getenv("REGPU_NO_MINI_CONST") != nullptr;
            const uint32_t constIndex = f.isMini && !miniOwnConst ? lastConst : f.constIndex;
            // The index register, or (ScanFetchIndex) the value the full fetch kept before overwriting it.
            const std::string indexReg = "r[" + std::to_string(lastSrc) + "]." + "xyzw"[lastSrcSwizzle & 3];
            const std::string indexSrc = saveFetchIndex ? "vfi" : indexReg;
            const std::string keep = saveFetchIndex && !f.isMini ? "vfi = " + indexReg + "; " : "";
            const bool dynamic = !BakedVertexFetch() && k < 32 && (f.isMini || (f.src == lastSrc && f.srcSwizzle == lastSrcSwizzle));
            literalFetch = literalFetch || !dynamic;
            if (layoutOnly)
            {
                if (dynamic)
                    result.vertexFetchLayout.push_back({ constIndex | (f.format << 8) | (f.isSigned ? 0x4000u : 0) | (f.isNormalized ? 0x8000u : 0) |
                            (lastRound ? 0x10000u : 0),
                        lastStride, f.offset, f.destSwizzle | ((uint32_t(f.expAdjust) & 0x3F) << 16) });
                if (std::find(result.vertexFetchConstants.begin(), result.vertexFetchConstants.end(), constIndex) ==
                    result.vertexFetchConstants.end())
                    result.vertexFetchConstants.push_back(constIndex);
                return;
            }
            if (dynamic)
            {
                // Layout from vfl[k]; only the registers stay in the code.
                result.vertexFetchLayout.push_back({ constIndex | (f.format << 8) | (f.isSigned ? 0x4000u : 0) | (f.isNormalized ? 0x8000u : 0) |
                        (lastRound ? 0x10000u : 0),
                    lastStride, f.offset, f.destSwizzle | ((uint32_t(f.expAdjust) & 0x3F) << 16) });
                const std::string dest = "r[" + std::to_string(f.dest) + "]";
                body = "{ " + keep + "float4 t = VFetchK(" + std::to_string(k) + ", " + indexSrc + "); " + dest + " = VMerge(" + std::to_string(k) + ", " + dest +
                    ", t); }";
            }
            else
            {
                char call[320];
                snprintf(call, sizeof(call), "VFetch(%u, %s(%s), %u, %u, %u, %s, %s) * %.9g", constIndex, lastRound ? "round" : "floor",
                    indexSrc.c_str(), lastStride, f.offset, f.format, f.isSigned ? "true" : "false",
                    f.isNormalized ? "true" : "false", std::ldexp(1.0, f.expAdjust));
                body = std::string("{ ") + keep + "float4 t = " + call + "; " + DestSwizzleWrite(f.dest, f.destSwizzle, "t") + "}";
            }
            bool known = false;
            for (uint32_t c : result.vertexFetchConstants)
                known = known || c == constIndex;
            if (!known)
                result.vertexFetchConstants.push_back(constIndex);
        }
        else if (f.opcode == 1)
        {
            static const char comp[] = "xyzw";
            const std::string reg = "r[" + std::to_string(f.src) + "].";
            // A 1D fetch (dimension 0) reads x only: its texture is bound one row high (texture.cpp),
            // sampled at that row's centre whatever the other components hold (from the FM4 port).
            static const bool no1d = getenv("REGPU_NO_1D_TEXTURES") != nullptr;
            const std::string uv = f.dimension == 0 && !no1d ? "float3(" + reg + comp[f.srcSwizzle & 3] + ", 0.5, 0)"
                : "float3(" + reg + comp[f.srcSwizzle & 3] + ", " + reg + comp[(f.srcSwizzle >> 2) & 3] + ", " + reg +
                comp[(f.srcSwizzle >> 4) & 3] + ")";
            char call[320];
            if (LeanFetch() && pixel && f.dimension == 1)
                snprintf(call, sizeof(call), "TFetch2D(%u, %s, %s, %.4f + tbias[%u].x, float2(%.1f, %.1f))", f.constIndex, uv.c_str(),
                    f.denormalized ? "true" : "false", f.lodBias, f.constIndex, f.offsetX, f.offsetY);
            else
                snprintf(call, sizeof(call), "TFetch(%u, %s, %s, %s, %.4f + tbias[%u].x, float2(%.1f, %.1f))", f.constIndex, uv.c_str(),
                    f.denormalized ? "true" : "false", pixel ? "false" : "true", f.lodBias, f.constIndex, f.offsetX, f.offsetY);
            // Declared, then assigned: fxc drops the value when the fetch initialises the declaration
            // in some shaders ("internal error: invalid access of unbound variable").
            // The instruction's own filter (mag filter 0 point, 1 linear) overrides the fetch constant's,
            // as on the console. PGR4's brake light glow reads an 8x6 visibility map in its vertex
            // shader with point filtering; filtered by the constant's linear filter, each bulb's glow
            // came and went as the car moved a fraction of a texel. REGPU_NO_FETCH_FILTER_OVERRIDE=1: ignored.
            static const bool noOverride = getenv("REGPU_NO_FETCH_FILTER_OVERRIDE") != nullptr;
            const bool overrides = !noOverride && (f.magFilter == 0 || f.magFilter == 1);
            body = std::string("{ float4 t; ") + (overrides ? "gFilterOverride = " + std::to_string(f.magFilter) + "; " : "") + "t = " + call + "; " +
                (overrides ? "gFilterOverride = -1; " : "") + DestSwizzleWrite(f.dest, f.destSwizzle, "t") + "}";
            result.usedTextureSlots |= 1u << f.constIndex;
        }
        else if (f.opcode == 18 && pixel)
        {
            // getGradients: screen-space derivatives of two source components,
            // (ddx(a), ddy(a), ddx(b), ddy(b)). Vector-font shaders use it for
            // anti-aliased curve edges.
            static const char comp[] = "xyzw";
            const std::string a = "r[" + std::to_string(f.src) + "]." + comp[f.srcSwizzle & 3];
            const std::string b = "r[" + std::to_string(f.src) + "]." + comp[(f.srcSwizzle >> 2) & 3];
            body = "{ float4 t = float4(ddx(" + a + "), ddy(" + a + "), ddx(" + b + "), ddy(" + b + ")); " +
                DestSwizzleWrite(f.dest, f.destSwizzle, "t") + "}";
        }
        else
            body = DestSwizzleWrite(f.dest, f.destSwizzle, "float4(0, 0, 0, 0)"); // other texture queries
        if (f.predicated)
            body = std::string("if (") + (f.predCondition ? "p" : "!p") + ") { " + body + " }";
        Line(body);
    }

    void EmitExec(const Cf& cf)
    {
        for (uint32_t k = 0; k < cf.count(); k++)
        {
            const uint32_t index = cf.address() + k;
            if ((index + 1) * 3 > shader.words.size())
            {
                error = "instruction out of range";
                return;
            }
            const uint32_t* w = &shader.words[index * 3];
            if (layoutOnly)
            {
                if ((cf.sequence() >> (2 * k)) & 1)
                    if (const Fetch f = DecodeFetch(w); f.opcode == 0)
                        EmitFetch(f);
                continue;
            }
            if ((cf.sequence() >> (2 * k)) & 1)
                EmitFetch(DecodeFetch(w));
            else
                EmitAlu(DecodeAlu(w));
        }
    }

    // Control flow as a program-counter state machine: one block per CF
    // instruction, blocks run in order within an iteration, jumps set pc.
    // A mini fetch reads the vertex its full fetch addressed: the hardware computes the address once,
    // at the full fetch. A full fetch that writes its own index register (FM4's tyre morph: vfetch r0,
    // r0, then a mini) left its minis reading the fetched value as the index. Where that happens the
    // full fetch keeps its index in vfi for the minis after it. From the FM4 port.
    // REGPU_NO_MINI_INDEX=1 re-reads the register (A/B; read at translation).
    void ScanFetchIndex()
    {
        if (pixel || getenv("REGPU_NO_MINI_INDEX"))
            return;
        bool clobbered = false;
        for (const Cf& cf : shader.cf)
        {
            if (!cf.IsExec())
                continue;
            for (uint32_t k = 0; k < cf.count(); k++)
            {
                const uint32_t index = cf.address() + k;
                if ((index + 1) * 3 > shader.words.size() || !((cf.sequence() >> (2 * k)) & 1))
                    continue;
                const Fetch f = DecodeFetch(&shader.words[index * 3]);
                if (f.opcode != 0)
                    continue;
                // The index component is overwritten when the destination swizzle writes it (0-3 a
                // fetched component, 4 zero, 5 one; 6 and 7 keep the register's value).
                if (!f.isMini)
                    clobbered = f.dest == f.src && ((f.destSwizzle >> (3 * (f.srcSwizzle & 3))) & 7) <= 5;
                else if (clobbered)
                    saveFetchIndex = true;
            }
        }
    }

    void EmitProgram()
    {
        ScanFetchIndex();
        bool linear = true, loops = false;
        for (const Cf& cf : shader.cf)
        {
            linear = linear && cf.op() != CfOp::CondJmp && cf.op() != CfOp::LoopStart && cf.op() != CfOp::LoopEnd &&
                cf.op() != CfOp::CondCall && cf.op() != CfOp::Return;
            loops = loops || cf.op() == CfOp::LoopStart;
        }
        // Only forward jumps (no loops, calls or returns: most pixel shaders with a predicated skip):
        // the blocks run once each in order, so they are emitted in sequence without the dispatch
        // loop; a jump sets cfpc past the blocks it skips. The loop kept fxc from flattening the
        // program (FH1's sun-shadow mask boxes, ~6 ms of a 31 ms frame on a GTX 970).
        // REGPU_NO_FORWARD_CF=1: the dispatch loop for these too (read at translation).
        bool forward = !linear && !getenv("REGPU_NO_FORWARD_CF");
        for (size_t i = 0; forward && i < shader.cf.size(); i++)
        {
            const CfOp op = shader.cf[i].op();
            if (op == CfOp::LoopStart || op == CfOp::LoopEnd || op == CfOp::CondCall || op == CfOp::Return)
                forward = false;
            else if (op == CfOp::CondJmp && (shader.cf[i].lo & 0x1FFF) <= i)
                forward = false;
        }
        if (forward)
            Line("uint cfpc = 0;");
        else if (!linear)
        {
            Line("uint cfpc = 0;");
            // Loops: each level keeps its counter and the aL it replaced. Calls: a 4-deep return stack.
            Line("uint loopCount[4] = { 0, 0, 0, 0 }; int loopSaved[4] = { 0, 0, 0, 0 }; uint loopDepth = 0;");
            Line("uint callRet[4] = { 0, 0, 0, 0 }; uint callDepth = 0;");
            // The guard only stops a program that never ends: a 255-iteration loop runs far past 1024 blocks.
            Line(std::string("[loop] for (uint guard = 0; guard < ") + (loops ? "65536" : "1024") + "u && cfpc != 0xFFFFu; guard++)");
            Line("{");
            indent++;
        }
        // The state machine gets a block only for the CF instructions a program can reach from its start:
        // through fall-through (none after an end, an unconditional jump or a return) and jump, call and
        // loop targets, subroutines after the end included. The rest of the CF area is padding or data
        // the compiler left there. From the FM4 port. REGPU_NO_REACHABLE_CF=1: every block up to the end,
        // as before (read at translation).
        std::vector<bool> reachable(shader.cf.size(), true);
        if (!linear && !getenv("REGPU_NO_REACHABLE_CF"))
        {
            reachable.assign(shader.cf.size(), false);
            std::vector<size_t> pending{ 0 };
            while (!pending.empty())
            {
                const size_t i = pending.back();
                pending.pop_back();
                if (i >= reachable.size() || reachable[i])
                    continue;
                reachable[i] = true;
                const Cf& cf = shader.cf[i];
                const CfOp op = cf.op();
                if (op == CfOp::CondJmp || op == CfOp::CondCall || op == CfOp::LoopStart || op == CfOp::LoopEnd)
                    pending.push_back(cf.lo & 0x1FFF);
                const bool unconditionalJump = op == CfOp::CondJmp && ((cf.lo >> 13) & 1);
                if (!cf.IsEnd() && op != CfOp::Return && !unconditionalJump)
                    pending.push_back(i + 1);
            }
        }
        for (size_t i = 0; i < shader.cf.size(); i++)
        {
            const Cf& cf = shader.cf[i];
            if (!reachable[i])
                continue;
            const std::string next = cf.IsEnd() ? "0xFFFFu" : std::to_string(i + 1);
            if (!linear)
            {
                Line("if (cfpc == " + std::to_string(i) + "u)");
                Line("{");
                indent++;
            }
            switch (cf.op())
            {
            case CfOp::Nop:
            case CfOp::Alloc:
            case CfOp::MarkVsFetchDone:
                break;
            case CfOp::Exec:
            case CfOp::ExecEnd:
                EmitExec(cf);
                break;
            case CfOp::CondExec:
            case CfOp::CondExecEnd:
            case CfOp::CondExecPredClean:
            case CfOp::CondExecPredCleanEnd:
            {
                char cond[96];
                snprintf(cond, sizeof(cond), "if (BoolConstant(%s, %u) == %s)", pixel ? "pb" : "vb", (cf.hi >> 2) & 0xFF,
                    cf.condition() ? "true" : "false");
                Line(std::string(cond) + " {");
                indent++;
                EmitExec(cf);
                indent--;
                Line("}");
                break;
            }
            case CfOp::CondExecPred:
            case CfOp::CondExecPredEnd:
                Line(std::string("if (") + (cf.condition() ? "p" : "!p") + ") {");
                indent++;
                EmitExec(cf);
                indent--;
                Line("}");
                break;
            case CfOp::CondJmp:
            {
                const uint32_t target = cf.lo & 0x1FFF;
                std::string cond;
                if ((cf.lo >> 13) & 1)
                    cond = "true";
                else if ((cf.lo >> 14) & 1)
                    cond = cf.condition() ? "p" : "!p";
                else
                    cond = std::string("BoolConstant(") + (pixel ? "pb" : "vb") + ", " + std::to_string((cf.hi >> 2) & 0xFF) + ") == " +
                        (cf.condition() ? "true" : "false");
                Line("cfpc = (" + cond + ") ? " + std::to_string(target) + "u : " + next + "u;");
                if (!forward)
                    Line("continue;");
                break;
            }
            // Loop constant (count | start << 8 | signed step << 16). loop_start sets aL to the
            // start (a repeat loop keeps it) and skips to its address past loop_end when the
            // count is 0; loop_end steps aL and jumps back to its address while iterations remain
            // (a predicated break leaves when p matches the condition).
            case CfOp::LoopStart:
            {
                const uint32_t target = cf.lo & 0x1FFF, id = (cf.lo >> 16) & 0x1F;
                const std::string k = std::string(pixel ? "pl" : "vl") + "[" + std::to_string(id >> 2) + "][" + std::to_string(id & 3) + "]";
                Line("{ uint lk = " + k + "; uint d = min(loopDepth, 3u); loopSaved[d] = aL; loopCount[d] = lk & 0xFFu;");
                if (!((cf.lo >> 13) & 1))
                    Line("  aL = int((lk >> 8) & 0xFFu);");
                Line("  loopDepth = min(loopDepth + 1, 4u);");
                Line("  if ((lk & 0xFFu) == 0) { loopDepth--; aL = loopSaved[min(loopDepth, 3u)]; cfpc = " + std::to_string(target) + "u; continue; } }");
                break;
            }
            case CfOp::LoopEnd:
            {
                const uint32_t target = cf.lo & 0x1FFF, id = (cf.lo >> 16) & 0x1F;
                const std::string k = std::string(pixel ? "pl" : "vl") + "[" + std::to_string(id >> 2) + "][" + std::to_string(id & 3) + "]";
                std::string brk = "false";
                if ((cf.lo >> 21) & 1)
                    brk = cf.condition() ? "p" : "!p";
                Line("{ uint d = loopDepth > 0 ? min(loopDepth, 4u) - 1u : 0u; loopCount[d]--; aL += int(" + k + " << 8) >> 24;");
                Line("  if (loopCount[d] != 0 && !(" + brk + ")) { cfpc = " + std::to_string(target) + "u; continue; }");
                Line("  loopDepth = d; aL = loopSaved[d]; }");
                break;
            }
            // ccall (conditions as condjmp) pushes the next block; ret pops it (an empty stack falls through).
            case CfOp::CondCall:
            {
                const uint32_t target = cf.lo & 0x1FFF;
                std::string cond;
                if ((cf.lo >> 13) & 1)
                    cond = "true";
                else if ((cf.lo >> 14) & 1)
                    cond = cf.condition() ? "p" : "!p";
                else
                    cond = std::string("BoolConstant(") + (pixel ? "pb" : "vb") + ", " + std::to_string((cf.hi >> 2) & 0xFF) + ") == " +
                        (cf.condition() ? "true" : "false");
                Line("if ((" + cond + ") && callDepth < 4) { callRet[callDepth++] = " + std::to_string(i + 1) + "u; cfpc = " +
                    std::to_string(target) + "u; continue; }");
                break;
            }
            case CfOp::Return:
                Line("if (callDepth > 0) { cfpc = callRet[--callDepth]; continue; }");
                break;
            default:
                error = std::string("control flow op ") + std::to_string(unsigned(cf.op())) + " not supported yet";
                return;
            }
            if (!error.empty())
                return;
            if (!linear)
            {
                if (cf.op() != CfOp::CondJmp)
                    Line("cfpc = " + next + (cf.IsEnd() ? ";" : "u;"));
                indent--;
                Line("}");
            }
            if (cf.IsEnd() && (linear || getenv("REGPU_NO_REACHABLE_CF")))
                break;
        }
        if (!linear && !forward)
        {
            indent--;
            Line("}");
        }
    }
};
}

const char* HlslPreamble()
{
    return kPreamble;
}

namespace
{
// REGPU_LEAN_FETCH=1 (opt-in, read at translation; its shaders cache apart, by their HLSL): a 2D
// fetch in a pixel shader (the instruction's dimension) samples once, through the static samplers
// as an array indexed by the fetch constant's filter, instead of one branch per sampler, and has
// no 3D or cube paths. Full-screen post shaders make a dozen fetches a pixel (FH1's shadow mask).

const char kLeanFetch[] = R"(
SamplerState gsamp[8] : register(s0, space1); // s0-s7 again, as a table (renderer.cpp)

float4 TFetch2D(uint slot, float3 coord, bool denorm, float bias, float2 texel)
{
    // A 3D or cube texture behind a 2D fetch: the full path (its heap index isn't a Texture2D's).
    if ((tinfo[slot].w & 0xFFu) >= 2u)
        return TFetch(slot, coord, denorm, false, bias, texel);
    const uint info = tinfo[slot].x;
    float2 uv = coord.xy;
    const float2 texelSize = float2(max(tinfo[slot].yz & 0xFFFFu, uint2(1, 1)));
    if (denorm) uv = (uv + texel) / texelSize;
    else uv += texel / texelSize;
    uv = Mirror(uv, info);
    if (((info >> 16) & 1u) && any(uv < 0.0 || uv > 1.0))
        return ((info >> 17) & 1u) ? float4(1, 1, 1, 1) : float4(0, 0, 0, 0);
    if ((info >> 18) & 1u) uv.x = clamp(uv.x, 0.5 / texelSize.x, 1.0 - 0.5 / texelSize.x);
    if ((info >> 19) & 1u) uv.y = clamp(uv.y, 0.5 / texelSize.y, 1.0 - 0.5 / texelSize.y);
    if (tbias[slot].z > 0.0) uv *= tbias[slot].zw;
    // Sampler index: the filter mode (1-3 its own sampler), else the anisotropy (s4-s7), else s0.
    const uint mode = info & 3u, aniso = (info >> 20) & 7u;
    const uint s = mode != 0u ? mode : (aniso >= 1u && aniso <= 4u ? aniso + 3u : 0u);
    float4 v = gtex[asuint(tbias[slot].y)].SampleBias(gsamp[s], uv, bias);
    uint gamma = (info >> 24) & 0xFu;
    if (gamma != 0u)
    {
        [unroll] for (uint g = 0; g < 4; g++)
            if ((gamma >> g) & 1u)
                v[g] = PWLGammaToLinear(v[g]);
    }
    int expAdjust = int(info << 18) >> 26;
    if (expAdjust != 0) v *= exp2(float(expAdjust));
    uint w = tinfo[slot].w;
    if (!(w >> 31))
        return v;
    float4 r;
    [unroll] for (uint c = 0; c < 4; c++)
    {
        uint sel = (w >> (8 + 3 * c)) & 7u;
        r[c] = sel < 4u ? v[sel] : (sel == 5u ? 1.0 : 0.0);
    }
    return r;
}
)";

std::string Preamble()
{
    std::string h = kPreamble;
    return LeanFetch() ? h + kLeanFetch : h;
}
} // namespace

// Read at first use (the renderer's root signature), not at load: a port may set it before Start.
bool LeanFetch()
{
    static const bool lean = getenv("REGPU_LEAN_FETCH") != nullptr;
    return lean;
}

bool BakedVertexFetch()
{
    static const bool baked = getenv("REGPU_DYNAMIC_VFETCH") == nullptr;
    return baked;
}

bool TranslateVertexFetchLayout(const Shader& shader, TranslatedShader& out)
{
    TranslatedShader result;
    if (!shader.valid || shader.pixel)
        return false;
    Emitter e{ shader, shader.pixel, {}, {}, result };
    e.layoutOnly = true;
    // The same walk as EmitProgram: control flow in order, each exec once, up to the end. (A program
    // with subroutines runs code after the end: translated in full instead.)
    for (const Cf& cf : shader.cf)
        if (cf.op() == CfOp::CondCall)
            return false;
    for (const Cf& cf : shader.cf)
    {
        if (cf.IsExec())
            e.EmitExec(cf);
        if (!e.error.empty() || e.literalFetch)
            return false;
        if (cf.IsEnd())
            break;
    }
    out.vertexFetchLayout = std::move(result.vertexFetchLayout);
    out.vertexFetchConstants = std::move(result.vertexFetchConstants);
    return true;
}

// The register file as separate variables (r0, r1, ...) rather than an array: registers are only
// ever indexed by a constant here, and fxc handles large local arrays badly (some shaders came out
// wrong: purple). Renames r[N] in a translated body and returns the registers it uses.
static std::vector<uint32_t> ScalarRegisters(std::string& body)
{
    std::string out;
    out.reserve(body.size());
    std::vector<bool> used(64, false);
    for (size_t i = 0; i < body.size(); i++)
    {
        const bool start = body[i] == 'r' && i + 1 < body.size() && body[i + 1] == '[' &&
            (i == 0 || !(isalnum(uint8_t(body[i - 1])) || body[i - 1] == '_' || body[i - 1] == '.'));
        size_t j = i + 2;
        while (start && j < body.size() && isdigit(uint8_t(body[j])))
            j++;
        if (start && j > i + 2 && j < body.size() && body[j] == ']')
        {
            const uint32_t n = uint32_t(std::stoul(body.substr(i + 2, j - i - 2)));
            if (n < 64)
            {
                used[n] = true;
                out += "r" + std::to_string(n);
                i = j;
                continue;
            }
        }
        out += body[i];
    }
    body.swap(out);
    std::vector<uint32_t> list;
    for (uint32_t n = 0; n < 64; n++)
        if (used[n])
            list.push_back(n);
    return list;
}

TranslatedShader TranslateShader(const Shader& shader)
{
    TranslatedShader result;
    if (!shader.valid)
    {
        result.error = "no end of program";
        return result;
    }
    Emitter e{ shader, shader.pixel, {}, {}, result };
    e.EmitProgram();
    if (!e.error.empty())
    {
        result.error = e.error;
        return result;
    }

    std::string h = Preamble();
    std::vector<uint32_t> regs = ScalarRegisters(e.out);
    std::string decl;
    if (!shader.pixel)
    {
        if (std::find(regs.begin(), regs.end(), 0u) == regs.end())
            regs.push_back(0); // the vertex index
        for (uint32_t n : regs)
            decl += "    float4 r" + std::to_string(n) + " = 0;\n";
        h += "void GuestVS(uint vindex, out float4 opos, out float4 o[16], out float4 opts)\n{\n" + decl +
             "    [unroll] for (int j = 0; j < 16; j++) o[j] = 0;\n"
             "    r0.x = float(vindex); opos = float4(0, 0, 0, 1); opts = 0; float ps = 0; bool p = false; int a0 = 0, aL = 0;\n" +
             std::string(e.saveFetchIndex ? "    float vfi = 0; // the index of the last full vertex fetch, for its mini fetches\n" : "");
        h += e.out;
        h += "}\n"
             "void GuestVS(uint vindex, out float4 opos, out float4 o[16])\n{\n"
             "    float4 opts; GuestVS(vindex, opos, o, opts);\n}\n"
             "VSOut main(uint id : SV_VertexID)\n{\n"
             "    VSOut r;\n"
             "    r.pcoord = 0;\n"
             "    uint e;\n"
             "    if (vte.y & 1u)\n    {\n"
             "        // Direct indices (triangle lists and restart-free strips): read the\n"
             "        // guest index buffer in place. y: 1 direct, 2 strip, 4 32-bit, 8 auto, endian in bits 4-5.\n"
             "        uint i = id;\n"
             "        if (vte.y & 2u)\n        {\n"
             "            uint t = id / 3u, k = id % 3u;\n"
             "            i = t + ((t & 1u) ? (k == 0u ? 1u : (k == 1u ? 0u : 2u)) : k);\n"
             "        }\n"
             "        else if (vte.y & 64u)\n        {\n"
             "            // Quad list: two triangles (0 1 2, 0 2 3) per quad.\n"
             "            uint q = id / 6u, k = id % 6u;\n"
             "            i = q * 4u + (k < 3u ? k : (k == 3u ? 0u : k - 2u));\n"
             "        }\n"
             "        uint endian = (vte.y >> 4) & 3u;\n"
             "        if (vte.y & 8u)\n            e = i;\n"
             "        else if (vte.y & 4u)\n        {\n"
             "            e = gphys.Load(vte.z + i * 4u);\n"
             "            if (endian == 2u) e = (e >> 24) | ((e >> 8) & 0xFF00u) | ((e << 8) & 0xFF0000u) | (e << 24);\n"
             "            else if (endian == 1u) e = ((e & 0x00FF00FFu) << 8) | ((e >> 8) & 0x00FF00FFu);\n"
             "        }\n"
             "        else\n        {\n"
             "            uint a = vte.z + i * 2u;\n"
             "            uint w = gphys.Load(a & ~3u);\n"
             "            e = (a & 2u) ? (w >> 16) : (w & 0xFFFFu);\n"
             "            if (endian == 1u || endian == 2u) e = ((e & 0xFFu) << 8) | (e >> 8);\n"
             "        }\n"
             "        e += vte.w;\n"
             "    }\n"
             "    else\n"
             "        e = gmem.Load(vinfo.x + id * 4);\n"
             "    float2 pointOffset = 0;\n"
             "    // fxc inlines every GuestVS call; a large shader inlined once per path (point,\n"
             "    // three rectangle corners, plain) took 15 s to compile. Plain vertices keep their\n"
             "    // own call; points and rectangles share one in a loop.\n"
             "    const bool isPoint = (vinfo.y & 2u) != 0, rect = !isPoint && (e & 0x80000000u);\n"
             "    [branch] if (!isPoint && !rect)\n"
             "        GuestVS(e, r.pos, r.o);\n"
             "    else\n"
             "    {\n"
             "        uint vi[3];\n"
             "        vi[0] = isPoint ? (e & 0x1FFFFFFFu) : e; vi[1] = 0; vi[2] = 0;\n"
             "        if (rect)\n"
             "        {\n"
             "            uint b = vinfo.x + (e & 0x1FFFFFFFu) * 4;\n"
             "            vi[0] = gmem.Load(b); vi[1] = gmem.Load(b + 4); vi[2] = gmem.Load(b + 8);\n"
             "        }\n"
             "        float4 vp[3]; float4 vq[3][16]; float4 opts = 0;\n"
             "        [loop] for (uint k = 0; k < (rect ? 3u : 1u); k++)\n"
             "        {\n"
             "            float4 kp, ko[16], kpts;\n"
             "            GuestVS(vi[k], kp, ko, kpts);\n"
             "            vp[k] = kp; vq[k] = ko;\n"
             "            if (k == 0u) opts = kpts;\n"
             "        }\n"
             "        if (isPoint)\n"
             "        {\n"
             "            // Point sprite: corner in bits 29-30, expanded around the point in screen space.\n"
             "            uint corner = (e >> 29) & 3u;\n"
             "            r.pos = vp[0]; r.o = vq[0];\n"
             "            float2 d = opts.x > 0 ? opts.xx : vpoint.xy;\n"
             "            d = clamp(d, vpoint.zz, vpoint.ww);\n"
             "            float2 c = float2(corner & 1u, corner >> 1);\n"
             "            r.pcoord = c;\n"
             "            pointOffset = (c * 2.0 - 1.0) * d * 0.5;\n"
             "        }\n"
             "        else\n"
             "        {\n"
             "            // Rectangle second triangle: role 0 is the computed fourth corner, roles 1 and 2 the\n"
             "            // ends of the hypotenuse (the corners next to the right angle).\n"
             "            uint role = (e >> 29) & 3u;\n"
             "            float d01 = distance(vp[0].xy / vp[0].w, vp[1].xy / vp[1].w), d02 = distance(vp[0].xy / vp[0].w, vp[2].xy / vp[2].w);\n"
             "            float d12 = distance(vp[1].xy / vp[1].w, vp[2].xy / vp[2].w);\n"
             "            uint ra = (d12 >= d01 && d12 >= d02) ? 0u : ((d02 >= d01) ? 1u : 2u);\n"
             "            uint e1 = (ra + 1u) % 3u, e2 = (ra + 2u) % 3u;\n"
             "            if (role == 1u) { r.pos = vp[e1]; r.o = vq[e1]; }\n"
             "            else if (role == 2u) { r.pos = vp[e2]; r.o = vq[e2]; }\n"
             "            else { r.pos = vp[e1] + vp[e2] - vp[ra]; [unroll] for (int i = 0; i < 16; i++) r.o[i] = vq[e1][i] + vq[e2][i] - vq[ra][i]; }\n"
             "        }\n"
             "    }\n"
             "    float2 size = float2(vinfo.zw);\n"
             "    // The Xenos viewport transform (PA_CL_VTE_CNTL): optional divide by w, then\n"
             "    // per-axis scale and offset to screen pixels; back to D3D clip space after.\n"
             "    // Hor+: on a wider-than-16:9 output a full-width pass is drawn wider; perspective\n"
             "    // geometry is narrowed to match, so the view widens instead of stretching.\n"
             "    const bool flat = abs(r.pos.w - 1.0) <= 1e-4;\n"
             "    if (!flat) r.pos.xy *= vwide.xy;\n"
             "    float w = r.pos.w;\n"
             "    // (VTX_W0_FMT set means w is the true W0, the normal case.)\n"
             "    float3 p = r.pos.xyz;\n"
             "    if (!(vte.x & (1u << 8))) p.xy /= w;\n"
             "    if (!(vte.x & (1u << 9))) p.z /= w;\n"
             "    if (vte.x & 1u) p.x *= vscale.x;\n"
             "    if (vte.x & 2u) p.x += voffset.x;\n"
             "    if (vte.x & 4u) p.y *= vscale.y;\n"
             "    if (vte.x & 8u) p.y += voffset.y;\n"
             "    if (vte.x & 16u) p.z *= vscale.z;\n"
             "    if (vte.x & 32u) p.z += voffset.z;\n"
             "    // Positions the title gives in screen space (no viewport scale or offset: its\n"
             "    // rectangle clears and filter passes, authored for D3D9's integer pixel centres,\n"
             "    // e.g. -0.5 .. 255.5) must cover the last row and column too: shift by half a pixel.\n"
             "    // vscale.w/voffset.w: pixel scale of MSAA clear rectangles (renderer.cpp), applied before the\n"
             "    // half-pixel shift: scaled after it, a 4x view's rectangle started a whole pixel in and left the\n"
             "    // first row and column uncleared (vte.x bit 31, REGPU_OLD_CLEAR_SHIFT: the old order).\n"
             "    // vte.x bit 30 (texture-less, upscaled surface): edges snapped to the guest pixels whose\n"
             "    // centres they cover instead, so each host pixel takes its guest pixel's coverage.\n"
             "    bool halfPixel = (vte.x & 0x30Fu) == 0x300u;\n"
             "    bool snap = halfPixel && (vte.x & (1u << 30));\n"
             "    if (snap) p.xy = ceil(p.xy);\n"
             "    if (halfPixel && !snap && (vte.x & (1u << 31))) p.xy += 0.5;\n"
             "    if (vscale.w > 0.0) p.xy *= float2(vscale.w, voffset.w);\n"
             "    if (halfPixel && !snap && !(vte.x & (1u << 31))) p.xy += 0.5;\n"
             "    // vte.x bit 28: a viewport-transformed draw with D3D9 pixel centres (PA_SU_VTX_CNTL bit 0\n"
             "    // clear) is shifted the same half pixel, as Xenia does: the console samples it at integer\n"
             "    // positions, and screen-space lookups that add half a texel then land on texel centres.\n"
             "    if (!halfPixel && (vte.x & (1u << 28))) p.xy += 0.5;\n"
             "    p.xy += pointOffset;\n"
             "    if (vte.x & (1u << 8)) w = 1.0;\n"
             "    float2 ndc = float2(p.x / size.x * 2.0 - 1.0, 1.0 - p.y / size.y * 2.0);\n"
             "    // Flat (2D) geometry in a wide pass keeps 16:9 proportions in a centred 16:9 area\n"
             "    // (HUD, menus, text); vertices on the left or right screen edge stay there, so\n"
             "    // full-screen layers (backgrounds, fades) still span the width. Screen-space passes\n"
             "    // (no viewport transform: the title's filter and post-processing passes) are left\n"
             "    // alone: squeezing their inner vertices warped the image outside 16:9.\n"
             "    if (flat && vwide.w > 0.5 && vwide.z < 1.0 && (vte.x & 0x30Fu) != 0x300u && abs(ndc.x) < 0.999) ndc.x *= vwide.z;\n"
             "    // Depth kept homogeneous (z * scale + offset * w, as Xenia does), not divided by w and multiplied\n"
             "    // back: the round trip moved geometry on the far plane (FH1's sky dome, z = w) a hair past it,\n"
             "    // where depth clipping dropped it (holes in the sky). vte.x bit 29: the old form (REGPU_OLD_DEPTH_W).\n"
             "    float zc = p.z * w;\n"
             "    if (!(vte.x & (1u << 9)) && !(vte.x & (1u << 8)) && !(vte.x & (1u << 29)))\n"
             "        zc = r.pos.z * ((vte.x & 16u) ? vscale.z : 1.0) + ((vte.x & 32u) ? voffset.z : 0.0) * w;\n"
             "    // The polygon offset's constant part (vdepth.x, renderer.cpp PolygonOffsetDepth).\n"
             "    zc += vdepth.x * w;\n"
             "    r.pos = float4(ndc * w, zc, w);\n"
             "    return r;\n}\n";
    }
    else
    {
        // A shader that writes depth outputs it too (a full-screen copy restoring a scene's depth
        // into another EDRAM view, which later draws depth-test against).
        if (e.exportsDepth)
            h += "struct PSOut { float4 c : SV_Target0; float d : SV_Depth; };\nPSOut main(VSOut input)\n{\n"
                 "    float4 oD = float4(input.pos.z, 0, 0, 0);\n";
        else
            h += "float4 main(VSOut input) : SV_Target0\n{\n";
        // Interpolators land in r0-r15; the generated parameter in the register pinfo.y names.
        std::string inputs;
        for (uint32_t n : regs)
            decl += "    float4 r" + std::to_string(n) + (n < 16 ? " = input.o[" + std::to_string(n) + "];\n" : " = 0;\n");
        for (uint32_t n : regs)
            if (n < 16)
                inputs += "    if (pinfo.y == " + std::to_string(n) + "u) r" + std::to_string(n) + " = float4(((pinfo.z & 65536u) ? floor(input.pos.xy) : input.pos.xy) * pinfo2.yz, input.pcoord);\n";
        h += decl +
             "    float4 oC[4]; [unroll] for (int k = 0; k < 4; k++) oC[k] = 0;\n"
             "    float ps = 0; bool p = false; int a0 = 0, aL = 0;\n" + inputs;
        h += e.out;
        h += "    switch (pinfo.x)\n    {\n"
             "    case 0: discard; break;\n"
             "    case 1: if (!(oC[0].a < pinfo2.x)) discard; break;\n"
             "    case 2: if (!(oC[0].a == pinfo2.x)) discard; break;\n"
             "    case 3: if (!(oC[0].a <= pinfo2.x)) discard; break;\n"
             "    case 4: if (!(oC[0].a > pinfo2.x)) discard; break;\n"
             "    case 5: if (!(oC[0].a != pinfo2.x)) discard; break;\n"
             "    case 6: if (!(oC[0].a >= pinfo2.x)) discard; break;\n"
             "    }\n"
             "    if (pinfo.z & 64u) { uint2 q = uint2(input.pos.xy) & 1u; if (!(oC[0].a >= 1.0 - float((pinfo.z >> (8u + ((q.x << 1) | q.y) * 2u)) & 3u) * 0.25)) discard; } // alpha to mask, dithered\n"
             "    oC[0] *= exp2(float(int(pinfo.w))); // colour exponent bias (RB_COLOR_INFO), after the alpha test\n"
             "    if (pinfo.z & 8u) oC[0] = clamp(oC[0], -32.0, 32.0); // 16_16 fixed range\n"
             "    if (pinfo.z & 32u) oC[0].a = round(saturate(oC[0].a) * 3.0) / 3.0; // 2-bit alpha surface (k_2_10_10_10_FLOAT)\n"
             "    if (pinfo2.w > 0.0) oC[0].rgb = clamp(oC[0].rgb, 0.0, pinfo2.w); // 7e3 target range\n"
             "    else if (pinfo2.w < 0.0) oC[0] = saturate(oC[0]); // fixed-point target\n"
             + std::string(e.exportsDepth ? "    PSOut o; o.c = oC[0]; o.d = saturate(oD.x); return o;\n}\n" : "    return oC[0];\n}\n");
    }
    // The colour exports as separate variables, not an array (from the FM4 port): fxc declares a local
    // array as an indexable temp, and a driver can reorder writes to one ahead of an earlier read of
    // the same element (a predicated alpha-fade overwrote an export whose old value was still needed).
    // Every index is a constant here. REGPU_COLOR_EXPORT_ARRAY=1 keeps the array (read at translation).
    static const bool exportArray = getenv("REGPU_COLOR_EXPORT_ARRAY") != nullptr;
    if (shader.pixel && !exportArray)
    {
        const std::string declArray = "float4 oC[4]; [unroll] for (int k = 0; k < 4; k++) oC[k] = 0;";
        if (const size_t at = h.find(declArray); at != std::string::npos)
        {
            h.replace(at, declArray.size(), "float4 oC0 = 0, oC1 = 0, oC2 = 0, oC3 = 0;");
            for (char k = '0'; k <= '3'; k++)
                for (size_t p = 0; (p = h.find(std::string("oC[") + k + "]", p)) != std::string::npos;)
                    h.replace(p, 5, std::string("oC") + k);
            // The return keeps its form: debug edits and the motion-vector variant patch "return oC[0];"
            // (a one-element array written once and read once has nothing to reorder).
            const std::string ret = "    return oC0;\n}\n";
            if (const size_t r = h.rfind(ret); r != std::string::npos)
                h.replace(r, ret.size(), "    float4 oC[1] = { oC0 };\n    return oC[0];\n}\n");
        }
    }
    result.hlsl = h;
    result.ok = true;
    return result;
}
