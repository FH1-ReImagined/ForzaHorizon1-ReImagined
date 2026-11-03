// FSR 1 (AMD FidelityFX Super Resolution 1.0, MIT): EASU upscale and RCAS sharpen as
// compute shaders. Flattened with the FidelityFX headers into fsr1_hlsl.h at build time
// (tools/embed_hlsl.py) and compiled at startup.
#define FFX_GPU 1
#define FFX_HLSL 1
#define FFX_HALF 0
#include "ffx_core.h"

cbuffer Constants : register(b0)
{
    uint4 con0, con1, con2, con3; // EASU
    uint4 rcasCon;                // RCAS
    uint4 outSize;                // xy output size
};
Texture2D<float4> src : register(t0);
RWTexture2D<float4> dst : register(u0);
SamplerState linearClamp : register(s0);

#define FFX_FSR_EASU_FLOAT 1
FfxFloat32x4 FsrEasuRF(FfxFloat32x2 p) { return src.GatherRed(linearClamp, p, int2(0, 0)); }
FfxFloat32x4 FsrEasuGF(FfxFloat32x2 p) { return src.GatherGreen(linearClamp, p, int2(0, 0)); }
FfxFloat32x4 FsrEasuBF(FfxFloat32x2 p) { return src.GatherBlue(linearClamp, p, int2(0, 0)); }

#define FSR_RCAS_F 1
FfxFloat32x4 FsrRcasLoadF(FfxInt32x2 p) { return src.Load(int3(p, 0)); }
void FsrRcasInputF(inout FfxFloat32 r, inout FfxFloat32 g, inout FfxFloat32 b) {}

#include "ffx_fsr1.h"

void EasuPixel(uint2 p)
{
    if (any(p >= outSize.xy))
        return;
    FfxFloat32x3 c;
    ffxFsrEasuFloat(c, p, con0, con1, con2, con3);
    dst[p] = float4(c, 1);
}

void RcasPixel(uint2 p)
{
    if (any(p >= outSize.xy))
        return;
    FfxFloat32 r, g, b;
    FsrRcasF(r, g, b, p, rcasCon);
    dst[p] = float4(r, g, b, 1);
}

// The scene's depth (a depth-stencil surface) as a plain R32 texture for the temporal
// upscalers: con0.xy offset of the rectangle, outSize its size.
[numthreads(8, 8, 1)]
void DepthCopyMain(uint3 id : SV_DispatchThreadID)
{
    if (any(id.xy >= outSize.xy))
        return;
    const uint2 p = id.xy + con0.xy;
    dst[p] = src.Load(int3(p, 0)).rrrr;
}

// The display gamma table, as the console's display applies it to the front buffer: 17
// points (con0..con3, rcasCon.x; floats) at 0, 1/16 .. 1, linear between; outSize the size.
float GammaCurve(float x, float p[17])
{
    const float f = saturate(x) * 16.0;
    const uint i = min(uint(f), 15u);
    return lerp(p[i], p[i + 1], f - float(i));
}

[numthreads(8, 8, 1)]
void GammaMain(uint3 id : SV_DispatchThreadID)
{
    if (any(id.xy >= outSize.xy))
        return;
    const uint4 q[5] = { con0, con1, con2, con3, rcasCon };
    float p[17];
    [unroll] for (uint k = 0; k < 17; k++)
        p[k] = asfloat(q[k / 4][k % 4]);
    const float4 c = src.Load(int3(id.xy, 0));
    dst[id.xy] = float4(GammaCurve(c.r, p), GammaCurve(c.g, p), GammaCurve(c.b, p), c.a);
}

// Enhanced lighting (experimental): local contrast on the finished frame. rcasCon.x is the
// strength (float bits). Each pixel is pushed away from the average of its four neighbours,
// so shading steps, edges and highlights read more clearly.
[numthreads(8, 8, 1)]
void EnhanceMain(uint3 id : SV_DispatchThreadID)
{
    if (any(id.xy >= outSize.xy))
        return;
    const int2 p = int2(id.xy);
    const int2 last = int2(outSize.xy) - 1;
    const float strength = asfloat(rcasCon.x);
    const float4 c = src.Load(int3(p, 0));
    const float4 l = src.Load(int3(max(p.x - 1, 0), p.y, 0));
    const float4 r = src.Load(int3(min(p.x + 1, last.x), p.y, 0));
    const float4 u = src.Load(int3(p.x, max(p.y - 1, 0), 0));
    const float4 d = src.Load(int3(p.x, min(p.y + 1, last.y), 0));
    const float4 blur = (l + r + u + d) * 0.25;
    dst[id.xy] = float4(saturate(c.rgb + strength * (c.rgb - blur.rgb)), c.a);
}

// 64 threads, each a 2x2 quad of a 16x16 tile (the layout FSR 1 is written for).
[numthreads(64, 1, 1)]
void EasuMain(uint3 lid : SV_GroupThreadID, uint3 gid : SV_GroupID)
{
    uint2 p = ffxRemapForQuad(lid.x) + uint2(gid.x << 4, gid.y << 4);
    EasuPixel(p);
    p.x += 8u;
    EasuPixel(p);
    p.y += 8u;
    EasuPixel(p);
    p.x -= 8u;
    EasuPixel(p);
}

[numthreads(64, 1, 1)]
void RcasMain(uint3 lid : SV_GroupThreadID, uint3 gid : SV_GroupID)
{
    uint2 p = ffxRemapForQuad(lid.x) + uint2(gid.x << 4, gid.y << 4);
    RcasPixel(p);
    p.x += 8u;
    RcasPixel(p);
    p.y += 8u;
    RcasPixel(p);
    p.x -= 8u;
    RcasPixel(p);
}
