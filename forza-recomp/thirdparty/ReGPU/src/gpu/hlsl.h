#pragma once
// Xenos microcode to HLSL (Shader Model 5.1).
//
// Vertex shaders fetch their own vertices: the host draws a non-indexed
// triangle list whose vertex IDs index a per-draw list of guest indices
// (already endian-swapped and expanded from fans, strips, quads and
// rectangles). Rectangle corners are marked and computed as v1 + v2 - v0.
//
// Bindings (one root signature for everything):
//   b0  per-draw constants for the vertex shader: c[256], bool constants,
//       vertex fetch table, index list offset, viewport mode
//   b1  per-draw constants for the pixel shader: c[256], bool constants,
//       alpha test, texture sizes / sampler modes
//   t0  guest data (vertex buffers and index lists) as a raw buffer
//   t1+ textures for fetch slots 0-31
//   s0 linear wrap, s1 linear clamp, s2 point clamp, s3 point wrap
#include <cstdint>
#include <string>
#include <array>
#include <vector>

#include "gpu/ucode.h"

struct TranslatedShader
{
    bool ok = false;
    std::string error;
    std::string hlsl;
    uint32_t usedTextureSlots = 0;  // bit per fetch slot (pixel shaders)
    bool memExport = false;         // a vertex shader writing eA/eM (memory export): not translated, its writes are lost
    std::vector<uint32_t> vertexFetchConstants; // vertex shaders
    std::vector<std::array<uint32_t, 4>> vertexFetchLayout; // vfl[k] for each vertex fetch (see hlsl.cpp)
    // Float constants the code reads: c[0] up to this (exclusive), 256 with relative
    // addressing. Only these are uploaded per draw.
    uint32_t constantsUsed = 0;
    // The same as a bit per constant (all of them with relative addressing): a draw copies only
    // these (shaders read a few constants spread up to c[255]: ~440 per draw were copied).
    std::array<uint64_t, 4> constantMask{};
    // Vertex shaders: bit per interpolator (o0-o15) the code exports.
    uint32_t interpolatorsWritten = 0;
};

TranslatedShader TranslateShader(const ucode::Shader& shader);
// Only the vertex fetch layout and constants of a vertex shader, without generating HLSL:
// for copies of an already translated shader that differ only in their fetch fields (see
// VFetchK). False when a fetch would have to be baked into the code (translate in full).
bool TranslateVertexFetchLayout(const ucode::Shader& shader, TranslatedShader& out);
// Vertex fetch layouts written into each shader as literals (the default): the compiler folds the
// unpacking (FM2 port: 12.7 -> 10.0 ms of GPU time a frame on a GTX 970), at the cost of each patched
// copy of a shader compiling on its own. REGPU_DYNAMIC_VFETCH=1 reads them from the constants instead.
bool BakedVertexFetch();

// Shared HLSL declarations both stages compile against.
const char* HlslPreamble();
// REGPU_LEAN_FETCH=1: lean 2D pixel fetches through a sampler table (root parameter 4, space1).
bool LeanFetch();
