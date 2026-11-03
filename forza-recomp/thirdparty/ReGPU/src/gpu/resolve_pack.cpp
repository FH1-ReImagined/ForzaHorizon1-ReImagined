#include "gpu/resolve_pack.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace regpu
{
namespace resolve
{
namespace
{
// Unsigned normalized: saturated, scaled, rounded to nearest.
uint32_t Unorm(float v, uint32_t bits)
{
    const float x = std::isnan(v) ? 0.0f : std::clamp(v, 0.0f, 1.0f);
    return uint32_t(x * float((1u << bits) - 1) + 0.5f);
}

uint32_t Half(float v)
{
    // IEEE half, round to nearest even, as the GPU's f32tof16.
    uint32_t f;
    memcpy(&f, &v, 4);
    const uint32_t sign = (f >> 16) & 0x8000;
    const uint32_t absf = f & 0x7FFFFFFF;
    if (absf > 0x7F800000)
        return sign | 0x7E00; // NaN
    if (absf >= 0x477FF000)  // rounds to infinity
        return sign | 0x7C00;
    if (absf < 0x38800000) // denormal or zero
    {
        float a;
        memcpy(&a, &absf, 4);
        return sign | uint32_t(std::nearbyint(a * 16777216.0f)); // a / 2^-24
    }
    const uint32_t rounded = absf + 0x0FFF + ((absf >> 13) & 1);
    return sign | ((rounded - 0x38000000) >> 13);
}

uint32_t Bits(float v)
{
    uint32_t u;
    memcpy(&u, &v, 4);
    return u;
}
}

bool DestLog2Bytes(uint32_t format, uint32_t& log2Bytes)
{
    switch (format)
    {
    case 2: case 8: case 9: log2Bytes = 0; return true;                  // 8, 8_A, 8_B
    case 3: case 4: case 5: case 10: case 15: case 24: case 30:          // 1_5_5_5, 5_6_5, 6_5_5, 8_8, 4_4_4_4, 16, 16_FLOAT
        log2Bytes = 1;
        return true;
    case 6: case 7: case 14: case 16: case 17: case 25: case 31: case 36: // 8_8_8_8(_A), 2_10_10_10, 10_11_11, 11_11_10, 16_16, 16_16_FLOAT, 32_FLOAT
        log2Bytes = 2;
        return true;
    case 26: case 32: case 37: log2Bytes = 3; return true;               // 16_16_16_16, 16_16_16_16_FLOAT, 32_32_FLOAT
    case 38: log2Bytes = 4; return true;                                 // 32_32_32_32_FLOAT
    default: return false;
    }
}

bool PackPixel(uint32_t format, const float c[4], uint32_t out[4])
{
    out[0] = out[1] = out[2] = out[3] = 0;
    switch (format)
    {
    case 2: case 8: case 9: out[0] = Unorm(c[0], 8); return true;
    case 3: out[0] = Unorm(c[0], 5) | Unorm(c[1], 5) << 5 | Unorm(c[2], 5) << 10 | Unorm(c[3], 1) << 15; return true;
    case 4: out[0] = Unorm(c[0], 5) | Unorm(c[1], 6) << 5 | Unorm(c[2], 5) << 11; return true;
    case 5: out[0] = Unorm(c[0], 5) | Unorm(c[1], 5) << 5 | Unorm(c[2], 6) << 10; return true;
    case 10: out[0] = Unorm(c[0], 8) | Unorm(c[1], 8) << 8; return true;
    case 15: out[0] = Unorm(c[0], 4) | Unorm(c[1], 4) << 4 | Unorm(c[2], 4) << 8 | Unorm(c[3], 4) << 12; return true;
    case 24: out[0] = Unorm(c[0], 16); return true;
    case 30: out[0] = Half(c[0]); return true;
    case 6: case 14: out[0] = Unorm(c[0], 8) | Unorm(c[1], 8) << 8 | Unorm(c[2], 8) << 16 | Unorm(c[3], 8) << 24; return true;
    case 7: out[0] = Unorm(c[0], 10) | Unorm(c[1], 10) << 10 | Unorm(c[2], 10) << 20 | Unorm(c[3], 2) << 30; return true;
    case 16: out[0] = Unorm(c[0], 11) | Unorm(c[1], 11) << 11 | Unorm(c[2], 10) << 22; return true; // 10_11_11: red in the low 11 bits
    case 17: out[0] = Unorm(c[0], 10) | Unorm(c[1], 11) << 10 | Unorm(c[2], 11) << 21; return true; // 11_11_10
    case 25: out[0] = Unorm(c[0], 16) | Unorm(c[1], 16) << 16; return true;
    case 31: out[0] = Half(c[0]) | Half(c[1]) << 16; return true;
    case 36: out[0] = Bits(c[0]); return true;
    case 26:
        out[0] = Unorm(c[0], 16) | Unorm(c[1], 16) << 16;
        out[1] = Unorm(c[2], 16) | Unorm(c[3], 16) << 16;
        return true;
    case 32:
        out[0] = Half(c[0]) | Half(c[1]) << 16;
        out[1] = Half(c[2]) | Half(c[3]) << 16;
        return true;
    case 37: out[0] = Bits(c[0]), out[1] = Bits(c[1]); return true;
    case 38: out[0] = Bits(c[0]), out[1] = Bits(c[1]), out[2] = Bits(c[2]), out[3] = Bits(c[3]); return true;
    default: return false;
    }
}

size_t SwappedByte(size_t a, uint32_t endian)
{
    switch (endian)
    {
    case 1: return a ^ 1;
    case 2: return a ^ 3;
    case 3: return a ^ 2;
    case 4: return a ^ 7;
    case 5: return a ^ 15;
    default: return a;
    }
}

float HalfToFloat(uint16_t h)
{
    const uint32_t sign = uint32_t(h & 0x8000) << 16, exponent = (h >> 10) & 0x1F, mantissa = h & 0x3FF;
    if (exponent == 0)
    {
        const float v = std::ldexp(float(mantissa), -24);
        return sign ? -v : v;
    }
    const uint32_t bits = exponent == 31 ? sign | 0x7F800000 | mantissa << 13 : sign | (exponent + 112) << 23 | mantissa << 13;
    float f;
    memcpy(&f, &bits, 4);
    return f;
}

void SwapEndian(uint8_t* p, size_t bytes, uint32_t endian)
{
    switch (endian)
    {
    case 1: // 8-in-16
        for (size_t i = 0; i + 1 < bytes; i += 2)
            std::swap(p[i], p[i + 1]);
        break;
    case 2: // 8-in-32
        for (size_t i = 0; i + 3 < bytes; i += 4)
            std::swap(p[i], p[i + 3]), std::swap(p[i + 1], p[i + 2]);
        break;
    case 3: // 16-in-32
        for (size_t i = 0; i + 3 < bytes; i += 4)
            std::swap(p[i], p[i + 2]), std::swap(p[i + 1], p[i + 3]);
        break;
    case 4: // 8-in-64
        for (size_t i = 0; i + 7 < bytes; i += 8)
            std::reverse(p + i, p + i + 8);
        break;
    case 5: // 8-in-128
        for (size_t i = 0; i + 15 < bytes; i += 16)
            std::reverse(p + i, p + i + 16);
        break;
    default:
        break;
    }
}
} // namespace resolve
} // namespace regpu
