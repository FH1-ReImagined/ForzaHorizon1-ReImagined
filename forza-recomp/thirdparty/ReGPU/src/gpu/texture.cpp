// Xenos texture decoding on the CPU: untiling, endian swaps, swizzle and the
// formats the frontend uses. The renderer will decode on the GPU later;
// this is the reference implementation and the first-pixels path.
#include <cstdio>
#include "gpu/texture.h"

#include <algorithm>
#include <cmath>

#include "gpu/regs.h"
#include "live_switch.h"

namespace
{
enum Format : uint32_t
{
    k_8 = 2,
    k_1_5_5_5 = 3,
    k_5_6_5 = 4,
    k_8_8_8_8 = 6,
    k_8_8 = 10,
    k_4_4_4_4 = 15,
    k_DXT1 = 18,
    k_DXT2_3 = 19,
    k_DXT4_5 = 20,
    k_16_16_16_16_FLOAT = 32, // half floats; uploaded as they are (lookup tables, HDR data)
    k_DXN = 49,   // two interpolated channels (BC5): normal maps
    k_DXT5A = 59, // one interpolated channel (BC4-like), 8-byte blocks
};

struct FormatInfo
{
    uint32_t blockSize;  // texels per side of a block (1 or 4)
    uint32_t log2Bytes;  // bytes per block
};

bool Info(uint32_t format, FormatInfo& out)
{
    switch (format)
    {
    case k_8: out = { 1, 0 }; return true;
    case k_1_5_5_5:
    case k_5_6_5:
    case k_8_8:
    case k_4_4_4_4: out = { 1, 1 }; return true;
    case k_8_8_8_8: out = { 1, 2 }; return true;
    case k_DXT1: out = { 4, 3 }; return true;
    case k_DXT2_3:
    case k_DXT4_5: out = { 4, 4 }; return true;
    case k_DXT5A: out = { 4, 3 }; return true;
    case k_DXN: out = { 4, 4 }; return true;
    case k_16_16_16_16_FLOAT: out = { 1, 3 }; return true;
    default: return false;
    }
}

// Xenos 2D tiling (as documented by Xenia's texture_util).
int32_t TiledOffset2DOuter(uint32_t y, uint32_t width, uint32_t log2Bpp)
{
    const uint32_t macro = ((y >> 5) * (width >> 5)) << (log2Bpp + 7);
    const uint32_t micro = ((y & 6) << 2) << log2Bpp;
    return int32_t(macro + ((micro & ~0xFu) << 1) + (micro & 0xF) + ((y & 8) << (3 + log2Bpp)) + ((y & 1) << 4));
}

int32_t TiledOffset2DInner(uint32_t x, uint32_t y, uint32_t log2Bpp, int32_t baseOffset)
{
    const uint32_t macro = (x >> 5) << (log2Bpp + 7);
    const uint32_t micro = (x & 7) << log2Bpp;
    const uint32_t offset = uint32_t(baseOffset) + (macro + ((micro & ~0xFu) << 1) + (micro & 0xF));
    return int32_t(((offset & ~0x1FFu) << 3) + ((offset & 0x1C0) << 2) + (offset & 0x3F) + ((y & 16) << 7) +
        (((((y & 8) >> 2) + (x >> 3)) & 3) << 6));
}

// Xenos 3D tiling (Xenia's texture_address.h Tiled3D): macro tiles of 32x16x4 blocks; pitch
// and height in blocks, aligned to 32. Returns the byte offset of block (x, y, z).
int64_t TiledOffset3D(int32_t x, int32_t y, int32_t z, uint32_t pitchAligned, uint32_t heightAligned, uint32_t log2Bpp)
{
    const int32_t outerBlocks = ((((z >> 2) * int32_t(heightAligned >> 4) + (y >> 4)) * int32_t(pitchAligned >> 5)) + (x >> 5)) << 7;
    const int32_t innerBlocks = ((z & 3) << 5) | (((y >> 1) & 3) << 3) | (x & 7);
    const int64_t outerInnerBytes = int64_t(outerBlocks | innerBlocks) << log2Bpp;
    const uint32_t bank = uint32_t((y >> 3) ^ (z >> 2)) & 1;
    const uint32_t pipe = (uint32_t(x >> 3) & 3) ^ (bank << 1);
    const uint32_t yLsb = uint32_t(y) & 1;
    return int64_t((yLsb << 4) | (pipe << 6) | (bank << 11)) | (outerInnerBytes & 0xF) | (((outerInnerBytes >> 4) & 1) << 5) |
        (((outerInnerBytes >> 5) & 7) << 8) | (outerInnerBytes >> 8 << 12);
}

void Swap(uint8_t* p, uint32_t bytes, uint32_t endian)
{
    switch (endian)
    {
    case 1: // 8 in 16
        for (uint32_t i = 0; i + 1 < bytes; i += 2)
            std::swap(p[i], p[i + 1]);
        break;
    case 2: // 8 in 32
        for (uint32_t i = 0; i + 3 < bytes; i += 4)
        {
            std::swap(p[i], p[i + 3]);
            std::swap(p[i + 1], p[i + 2]);
        }
        break;
    case 3: // 16 in 32
        for (uint32_t i = 0; i + 3 < bytes; i += 4)
        {
            std::swap(p[i], p[i + 2]);
            std::swap(p[i + 1], p[i + 3]);
        }
        break;
    default:
        break;
    }
}

void Rgb565(uint16_t c, uint8_t* out)
{
    out[0] = uint8_t(((c >> 11) & 31) * 255 / 31);
    out[1] = uint8_t(((c >> 5) & 63) * 255 / 63);
    out[2] = uint8_t((c & 31) * 255 / 31);
    out[3] = 255;
}

// BC1 colour block (8 bytes, little-endian after the endian swap).
void DecodeColorBlock(const uint8_t* b, uint8_t out[16][4], bool bc1)
{
    const uint16_t c0 = uint16_t(b[0] | (b[1] << 8)), c1 = uint16_t(b[2] | (b[3] << 8));
    uint8_t palette[4][4];
    Rgb565(c0, palette[0]);
    Rgb565(c1, palette[1]);
    for (int k = 0; k < 3; k++)
    {
        if (c0 > c1 || !bc1)
        {
            palette[2][k] = uint8_t((2 * palette[0][k] + palette[1][k]) / 3);
            palette[3][k] = uint8_t((palette[0][k] + 2 * palette[1][k]) / 3);
        }
        else
        {
            palette[2][k] = uint8_t((palette[0][k] + palette[1][k]) / 2);
            palette[3][k] = 0;
        }
    }
    palette[2][3] = 255;
    palette[3][3] = (c0 > c1 || !bc1) ? 255 : 0;
    const uint32_t bits = uint32_t(b[4]) | (uint32_t(b[5]) << 8) | (uint32_t(b[6]) << 16) | (uint32_t(b[7]) << 24);
    for (int i = 0; i < 16; i++)
        memcpy(out[i], palette[(bits >> (i * 2)) & 3], 4);
}

void DecodeAlphaBlock(const uint8_t* b, uint8_t out[16][4]);

void DecodeBlock(uint32_t format, const uint8_t* b, uint8_t out[16][4])
{
    if (format == k_DXT5A)
    {
        // Single channel: replicated, the fetch swizzle picks what the shader sees.
        DecodeAlphaBlock(b, out);
        for (int i = 0; i < 16; i++)
            out[i][0] = out[i][1] = out[i][2] = out[i][3];
        return;
    }
    if (format == k_DXN)
    {
        // Red block, then green (as BC5).
        uint8_t red[16][4];
        DecodeAlphaBlock(b, red);
        DecodeAlphaBlock(b + 8, out);
        for (int i = 0; i < 16; i++)
        {
            out[i][1] = out[i][3];
            out[i][0] = red[i][3];
            out[i][2] = 0;
            out[i][3] = 255;
        }
        return;
    }
    if (format == k_DXT1)
    {
        DecodeColorBlock(b, out, true);
        return;
    }
    DecodeColorBlock(b + 8, out, false);
    if (format == k_DXT2_3)
    {
        for (int i = 0; i < 16; i++)
            out[i][3] = uint8_t(((b[i / 2] >> ((i & 1) * 4)) & 15) * 17);
        return;
    }
    DecodeAlphaBlock(b, out);
}

// DXT5 interpolated alpha block (8 bytes) into channel 3.
void DecodeAlphaBlock(const uint8_t* b, uint8_t out[16][4])
{
    uint8_t a[8];
    a[0] = b[0];
    a[1] = b[1];
    if (a[0] > a[1])
        for (int i = 1; i < 7; i++)
            a[i + 1] = uint8_t(((7 - i) * a[0] + i * a[1]) / 7);
    else
    {
        for (int i = 1; i < 5; i++)
            a[i + 1] = uint8_t(((5 - i) * a[0] + i * a[1]) / 5);
        a[6] = 0;
        a[7] = 255;
    }
    uint64_t bits = 0;
    for (int i = 0; i < 6; i++)
        bits |= uint64_t(b[2 + i]) << (8 * i);
    for (int i = 0; i < 16; i++)
        out[i][3] = a[(bits >> (3 * i)) & 7];
}

void DecodeTexel(uint32_t format, const uint8_t* t, uint8_t* out)
{
    switch (format)
    {
    case k_16_16_16_16_FLOAT: // the 8-bit fallback (the native path keeps the floats): clamped to 0-1
        for (int c = 0; c < 4; c++)
        {
            const uint16_t h = uint16_t(t[c * 2] | (t[c * 2 + 1] << 8));
            const int e = (h >> 10) & 31;
            const float f = e ? std::ldexp(1.0f + float(h & 1023) / 1024.0f, e - 15) : std::ldexp(float(h & 1023) / 1024.0f, -14);
            out[c] = (h & 0x8000) ? 0 : uint8_t(std::min(f, 1.0f) * 255.0f + 0.5f);
        }
        break;
    case k_8:
        out[0] = out[1] = out[2] = t[0];
        out[3] = 255;
        break;
    case k_8_8:
        out[0] = t[0];
        out[1] = t[1];
        out[2] = 0;
        out[3] = 255;
        break;
    case k_5_6_5:
        Rgb565(uint16_t(t[0] | (t[1] << 8)), out);
        break;
    case k_1_5_5_5:
    {
        const uint16_t c = uint16_t(t[0] | (t[1] << 8));
        out[0] = uint8_t(((c >> 10) & 31) * 255 / 31);
        out[1] = uint8_t(((c >> 5) & 31) * 255 / 31);
        out[2] = uint8_t((c & 31) * 255 / 31);
        out[3] = (c & 0x8000) ? 255 : 0;
        break;
    }
    case k_4_4_4_4:
    {
        // R in the low nibble, as k_8_8_8_8 has R in the low byte (after the endian swap). With R and B
        // the other way round, a font atlas with a glyph in each channel drew glyphs from the wrong one
        // (FM2's Japanese text). REGPU_4444_R_HIGH=1: R in the third nibble, as before (A/B).
        static const bool rHigh = getenv("REGPU_4444_R_HIGH") != nullptr;
        const uint16_t c = uint16_t(t[0] | (t[1] << 8));
        out[0] = uint8_t(((c >> (rHigh ? 8 : 0)) & 15) * 17);
        out[1] = uint8_t(((c >> 4) & 15) * 17);
        out[2] = uint8_t(((c >> (rHigh ? 0 : 8)) & 15) * 17);
        out[3] = uint8_t(((c >> 12) & 15) * 17);
        break;
    }
    case k_8_8_8_8:
        // The channels in memory order after the swap (R G B A). The title's fetch constants for
        // these textures carry the BGRA swizzle (applied below), so what it samples is B G R A of
        // the word: a red texture stays red (it came out blue with the order reversed).
        out[0] = t[0];
        out[1] = t[1];
        out[2] = t[2];
        out[3] = t[3];
        break;
    default:
        memset(out, 255, 4);
        break;
    }
}
}

TextureFetch ReadTextureFetch(uint32_t slot)
{
    TextureFetch f;
    for (int i = 0; i < 6; i++)
        f.dwords[i] = gpureg::Get(gpureg::FetchConstants + slot * 6 + i);
    // A 1D texture (dimension 0: a 24-bit width in dword 2, no height) is the same memory as a 2D
    // texture one row high, which is how it is bound (the translator samples a 1D fetch at the
    // row's centre). Wider than a 2D texture can be, it stays 1D and is not bound (white, as before).
    // From the FM4 port (55 1D fetches there, most of them data lookups). REGPU_NO_1D_TEXTURES=1: off.
    static LiveSwitch no1d("REGPU_NO_1D_TEXTURES");
    if (f.Type() == 2 && f.Dimension() == 0 && !no1d)
    {
        const uint32_t width = (f.dwords[2] & 0xFFFFFF) + 1;
        if (width <= 8192)
        {
            f.dwords[2] = (f.dwords[2] & ~0x3FFFFFFu) | (width - 1);
            f.dwords[5] = (f.dwords[5] & ~(3u << 9)) | (1u << 9);
            if (!f.Pitch()) // one row: any pitch addresses it, but the decoder wants one
                f.dwords[0] |= ((((width + 31) / 32) & 0x1FF) << 22);
        }
    }
    return f;
}

bool TextureFormatSupported(uint32_t format)
{
    FormatInfo info;
    return Info(format, info);
}

const char* TextureFormatName(uint32_t format)
{
    switch (format)
    {
    case k_8: return "8";
    case k_1_5_5_5: return "1555";
    case k_5_6_5: return "565";
    case k_8_8_8_8: return "8888";
    case k_8_8: return "88";
    case k_4_4_4_4: return "4444";
    case k_DXT1: return "DXT1";
    case k_DXT2_3: return "DXT3";
    case k_DXT4_5: return "DXT5";
    case k_DXT5A: return "DXT5A";
    case k_DXN: return "DXN";
    case k_16_16_16_16_FLOAT: return "16161616F";
    default:
    {
        static thread_local char name[8];
        snprintf(name, sizeof(name), "?%u", format);
        return name;
    }
    }
}

bool DecodeTexture(const TextureFetch& fetch, std::vector<uint8_t>& rgba)
{
    return DecodeTexture(fetch, GuestPtr<uint8_t>(GpuPhysToGuest(fetch.Address())), rgba);
}

// Packed mips (fetch dword 5 bit 11): a texture whose smaller side is 16 texels or
// less lies inside one 32x32-texel tile, at an offset (as Xenia's GetPackedMipOffset): the
// wider side's first 16 texels, then the image. Returns the offset of mip 0 in blocks.
void PackedMipOffset(const TextureFetch& fetch, uint32_t blockSize, uint32_t& xBlocks, uint32_t& yBlocks)
{
    xBlocks = yBlocks = 0;
    if (fetch.tailX != ~0u)
    {
        xBlocks = fetch.tailX;
        yBlocks = fetch.tailY;
        return;
    }
    // Linear textures too: PGR4's 8x6 car damage tables are linear with packed mips, their texels
    // 16 to the right of the base (read from the base, every car looked fully damaged).
    static LiveSwitch tiledOnly("REGPU_PACKED_TILED_ONLY"); // A/B: the old rule
    if ((tiledOnly && !fetch.Tiled()) || !((fetch.dwords[5] >> 11) & 1))
        return;
    auto log2Ceil = [](uint32_t v) {
        uint32_t l = 0;
        while ((1u << l) < v)
            l++;
        return l;
    };
    const uint32_t lw = log2Ceil(fetch.Width()), lh = log2Ceil(fetch.Height());
    if (std::min(lw, lh) > 4)
        return;
    if (lw > lh)
        yBlocks = 16 / blockSize;
    else
        xBlocks = 16 / blockSize;
}

bool DecodeTexture(const TextureFetch& fetch, const uint8_t* source, std::vector<uint8_t>& rgba)
{
    FormatInfo info;
    if (fetch.Type() != 2 || !Info(fetch.Format(), info))
        return false;
    const uint32_t width = fetch.Width(), height = fetch.Height();
    if (width > 4096 || height > 4096)
        return false;
    const uint32_t blocksX = (width + info.blockSize - 1) / info.blockSize;
    const uint32_t blocksY = (height + info.blockSize - 1) / info.blockSize;
    const uint32_t blockBytes = 1u << info.log2Bytes;
    // Rows are padded to the pitch, and tiled surfaces to 32 blocks.
    uint32_t pitchBlocks = std::max(fetch.Pitch() / info.blockSize, blocksX);
    if (fetch.Tiled())
        pitchBlocks = (pitchBlocks + 31) & ~31u;

    uint32_t packX, packY;
    PackedMipOffset(fetch, info.blockSize, packX, packY);
    rgba.assign(size_t(width) * height * 4, 0);
    uint8_t block[32];
    uint8_t texels[16][4];
    for (uint32_t by = 0; by < blocksY; by++)
    {
        const int32_t rowOffset = fetch.Tiled() ? TiledOffset2DOuter(by + packY, pitchBlocks, info.log2Bytes) : 0;
        for (uint32_t bx = 0; bx < blocksX; bx++)
        {
            size_t offset;
            if (fetch.Tiled())
                offset = size_t(TiledOffset2DInner(bx + packX, by + packY, info.log2Bytes, rowOffset) >> info.log2Bytes) * blockBytes;
            else
                offset = (size_t(by + packY) * pitchBlocks + bx + packX) * blockBytes;
            memcpy(block, source + offset, blockBytes);
            Swap(block, blockBytes, fetch.Endian());
            if (info.blockSize == 4)
            {
                DecodeBlock(fetch.Format(), block, texels);
                for (uint32_t i = 0; i < 16; i++)
                {
                    const uint32_t x = bx * 4 + (i & 3), y = by * 4 + (i >> 2);
                    if (x < width && y < height)
                        memcpy(&rgba[(size_t(y) * width + x) * 4], texels[i], 4);
                }
            }
            else
                DecodeTexel(fetch.Format(), block, &rgba[(size_t(by) * width + bx) * 4]);
        }
    }

    // Swizzle: per output channel, 0-3 select X/Y/Z/W, 4 = 0, 5 = 1.
    const uint32_t swizzle = fetch.Swizzle();
    if (swizzle != 0x688) // XYZW
    {
        for (size_t i = 0; i < rgba.size(); i += 4)
        {
            uint8_t in[4];
            memcpy(in, &rgba[i], 4);
            for (int c = 0; c < 4; c++)
            {
                const uint32_t s = (swizzle >> (c * 3)) & 7;
                rgba[i + c] = s < 4 ? in[s] : (s == 5 ? 255 : 0);
            }
        }
    }
    return true;
}

uint32_t TextureTiledOffset(uint32_t x, uint32_t y, uint32_t pitchBlocks, uint32_t log2Bytes)
{
    const int32_t rowOffset = TiledOffset2DOuter(y, pitchBlocks, log2Bytes);
    return uint32_t(TiledOffset2DInner(x, y, log2Bytes, rowOffset) >> log2Bytes) << log2Bytes;
}

bool DecodeTexture3D(const TextureFetch& fetch, std::vector<uint8_t>& rgba)
{
    FormatInfo info;
    if (fetch.Type() != 2 || fetch.Dimension() != 2 || !Info(fetch.Format(), info))
        return false;
    const uint32_t width = fetch.Width(), height = fetch.Height(), depth = fetch.Depth();
    const uint8_t* source = GuestPtr<uint8_t>(GpuPhysToGuest(fetch.Address()));
    const uint32_t bs = info.blockSize;
    const uint32_t blocksX = (width + bs - 1) / bs, blocksY = (height + bs - 1) / bs;
    const uint32_t blockBytes = 1u << info.log2Bytes;
    // Storage is aligned to 32x32x4 blocks, tiled or linear (Xenia's texture_address.h).
    const uint32_t pitchBlocks = (std::max(fetch.Pitch() / bs, blocksX) + 31) & ~31u;
    const uint32_t heightBlocks = (blocksY + 31) & ~31u;
    rgba.assign(size_t(width) * height * depth * 4, 0);
    uint8_t block[32];
    uint8_t texels[16][4];
    for (uint32_t z = 0; z < depth; z++)
    {
        uint8_t* slice = &rgba[size_t(z) * width * height * 4];
        for (uint32_t by = 0; by < blocksY; by++)
            for (uint32_t bx = 0; bx < blocksX; bx++)
            {
                const size_t offset = fetch.Tiled() ? size_t(TiledOffset3D(int32_t(bx), int32_t(by), int32_t(z), pitchBlocks, heightBlocks, info.log2Bytes))
                                                    : ((size_t(z) * heightBlocks + by) * pitchBlocks + bx) * blockBytes;
                memcpy(block, source + offset, blockBytes);
                Swap(block, blockBytes, fetch.Endian());
                if (bs == 4)
                {
                    DecodeBlock(fetch.Format(), block, texels);
                    for (uint32_t i = 0; i < 16; i++)
                    {
                        const uint32_t x = bx * 4 + (i & 3), y = by * 4 + (i >> 2);
                        if (x < width && y < height)
                            memcpy(&slice[(size_t(y) * width + x) * 4], texels[i], 4);
                    }
                }
                else
                    DecodeTexel(fetch.Format(), block, &slice[(size_t(by) * width + bx) * 4]);
            }
    }
    const uint32_t swizzle = fetch.Swizzle();
    if (swizzle != 0x688) // XYZW
        for (size_t i = 0; i < rgba.size(); i += 4)
        {
            uint8_t in[4];
            memcpy(in, &rgba[i], 4);
            for (int c = 0; c < 4; c++)
            {
                const uint32_t sel = (swizzle >> (c * 3)) & 7;
                rgba[i + c] = sel < 4 ? in[sel] : (sel == 5 ? 255 : 0);
            }
        }
    return true;
}

size_t TextureSourceSize(const TextureFetch& fetch)
{
    FormatInfo info;
    const uint32_t width = fetch.Width(), height = fetch.Height();
    if (!Info(fetch.Format(), info) || !width || !height || width > 4096 || height > 4096)
        return 0;
    const uint32_t blocksX = (width + info.blockSize - 1) / info.blockSize;
    const uint32_t blocksY = (height + info.blockSize - 1) / info.blockSize;
    uint32_t pitchBlocks = std::max(fetch.Pitch() / info.blockSize, blocksX);
    uint32_t rows = blocksY;
    if (fetch.Dimension() == 2) // 3D: 32x32x4-block storage
        return size_t((pitchBlocks + 31) & ~31u) * ((rows + 31) & ~31u) * ((fetch.Depth() + 3) & ~3u) * (size_t(1) << info.log2Bytes);
    if (fetch.Tiled())
    {
        pitchBlocks = (pitchBlocks + 31) & ~31u;
        rows = (rows + 31) & ~31u;
    }
    else
    {
        uint32_t packX, packY; // a packed linear image lies below or right of the base
        PackedMipOffset(fetch, info.blockSize, packX, packY);
        rows += packY;
    }
    return size_t(pitchBlocks) * rows * (size_t(1) << info.log2Bytes);
}

uint32_t TextureBCKind(uint32_t format)
{
    switch (format)
    {
    case k_DXT1: return 1;
    case k_DXT2_3: return 2;
    case k_DXT4_5: return 3;
    case k_DXT5A: return 4;
    case k_DXN: return 5;
    case k_16_16_16_16_FLOAT: return 6; // not compressed: 1x1 "blocks" of 8 bytes
    default: return 0;
    }
}

bool CopyTextureBlocks(const TextureFetch& fetch, uint8_t* dst, uint32_t dstRowPitch)
{
    FormatInfo info;
    if (fetch.Type() != 2 || !TextureBCKind(fetch.Format()) || !Info(fetch.Format(), info))
        return false;
    const uint8_t* source = GuestPtr<uint8_t>(GpuPhysToGuest(fetch.Address()));
    const uint32_t bs = info.blockSize;
    const uint32_t blocksX = (fetch.Width() + bs - 1) / bs, blocksY = (fetch.Height() + bs - 1) / bs;
    const uint32_t blockBytes = 1u << info.log2Bytes;
    uint32_t pitchBlocks = std::max(fetch.Pitch() / bs, blocksX);
    if (fetch.Tiled())
        pitchBlocks = (pitchBlocks + 31) & ~31u;
    uint32_t packX, packY;
    PackedMipOffset(fetch, bs, packX, packY);
    for (uint32_t by = 0; by < blocksY; by++)
    {
        uint8_t* row = dst + size_t(by) * dstRowPitch;
        const int32_t rowOffset = fetch.Tiled() ? TiledOffset2DOuter(by + packY, pitchBlocks, info.log2Bytes) : 0;
        for (uint32_t bx = 0; bx < blocksX; bx++)
        {
            const size_t offset = fetch.Tiled() ? size_t(TiledOffset2DInner(bx + packX, by + packY, info.log2Bytes, rowOffset) >> info.log2Bytes) * blockBytes
                                                : (size_t(by + packY) * pitchBlocks + bx + packX) * blockBytes;
            // Swapped here, then written once: dst is upload memory (write-combined, never read).
            uint8_t block[16];
            memcpy(block, source + offset, blockBytes);
            Swap(block, blockBytes, fetch.Endian());
            memcpy(row + size_t(bx) * blockBytes, block, blockBytes);
        }
    }
    return true;
}

bool TextureMipLevel(const TextureFetch& base, uint32_t level, TextureFetch& out)
{
    FormatInfo info;
    if (!Info(base.Format(), info))
        return false;
    if (level == 0)
    {
        out = base;
        return true;
    }
    const uint32_t w = base.Width(), h = base.Height();
    // Non-power-of-two images have levels too (FH1's 768x256 and 2048x768 ground textures): each is
    // stored at the power-of-two size (next_pow2(base) >> level, as Xenia's GetGuestMipBlocks), its
    // texels (base >> level, D3D's level size) in the top-left. Without them the distant ground read
    // the full-size image: too sharp, and it shimmered as the camera moved. REGPU_NO_NPOT_MIPS=1: none.
    static const bool noNpot = getenv("REGPU_NO_NPOT_MIPS") != nullptr;
    const bool pow2 = !(w & (w - 1)) && !(h & (h - 1));
    if (!base.MipAddress() || level > base.MipMaxLevel() || !base.Tiled() || (!pow2 && noNpot))
        return false;
    auto nextPow2 = [](uint32_t v) { uint32_t p = 1; while (p < v) p <<= 1; return p; };
    const uint32_t sw0 = nextPow2(w), sh0 = nextPow2(h);
    uint32_t address = base.MipAddress();
    for (uint32_t l = 1;; l++)
    {
        const uint32_t lw = std::max(w >> l, 1u), lh = std::max(h >> l, 1u);
        const uint32_t sw = std::max(sw0 >> l, 1u), sh = std::max(sh0 >> l, 1u); // stored size
        if (std::min(sw, sh) <= 16)
        {
            // The packed tail: this level and the smaller ones share one tile at this address, each at
            // an offset (Xenia's GetPackedMipOffset). Unread, chains stopped at 32 texels and distant
            // surfaces sampled a level more detailed than the console's. Opt-in, REGPU_MIP_TAIL=1: the
            // offsets are Xenia's and unverified against a console dump.
            static const bool tail = getenv("REGPU_MIP_TAIL") != nullptr;
            if (!tail)
                return false;
            auto log2Ceil = [](uint32_t v) { uint32_t r = 0; while ((1u << r) < v) r++; return r; };
            const uint32_t lw2 = log2Ceil(w), lh2 = log2Ceil(h), size2 = std::min(lw2, lh2);
            const uint32_t tailBase = size2 > 4 ? size2 - 4 : 0;
            const uint32_t packed = level - tailBase;
            uint32_t x = 0, y = 0; // texels
            if (packed < 3)
                (lw2 > lh2 ? y : x) = 16u >> packed;
            else
                (lw2 > lh2 ? x : y) = (1u << ((lw2 > lh2 ? lw2 : lh2) - tailBase)) >> (packed - 2);
            out = base;
            out.dwords[0] = (out.dwords[0] & ~(0x1FFu << 22)) | (1u << 22); // pitch: one tile
            out.dwords[1] = (out.dwords[1] & 0xFFF) | address;
            out.dwords[2] = (out.dwords[2] & ~0x3FFFFFFu) | (std::max(w >> level, 1u) - 1) | ((std::max(h >> level, 1u) - 1) << 13);
            out.tailX = x / info.blockSize;
            out.tailY = y / info.blockSize;
            return true;
        }
        if (l == level)
        {
            out = base;
            // Pitch: the stored width, in 32-texel units (the level's own width when a power of two).
            out.dwords[0] = (out.dwords[0] & ~(0x1FFu << 22)) | (pow2 ? 0u : ((sw + 31) / 32) << 22);
            out.dwords[1] = (out.dwords[1] & 0xFFF) | address;
            out.dwords[2] = (out.dwords[2] & ~0x3FFFFFFu) | (lw - 1) | ((lh - 1) << 13);
            return true;
        }
        // Each level is padded to 32x32 blocks (a tile) and 4 KB.
        const uint32_t bx = (sw + info.blockSize - 1) / info.blockSize, by = (sh + info.blockSize - 1) / info.blockSize;
        const uint32_t bytes = (((bx + 31) & ~31u) * ((by + 31) & ~31u)) << info.log2Bytes;
        address += (bytes + 4095) & ~4095u;
    }
}

uint32_t TextureFaceStride(const TextureFetch& fetch)
{
    FormatInfo info;
    if (!Info(fetch.Format(), info))
    {
        // Formats with no decoder here are render targets the title resolved into: their faces sit
        // as far apart as their texels are wide. 64 bpp: k_16_16_16_16_EDRAM 21, k_16_16_16_16 26,
        // k_16_16_16_16_EXPAND 29, k_32_32_FLOAT 37 (PGR4's car reflection cube is 29: read at
        // 32 bpp, faces 1-5 came from the wrong places and the car body reflected black);
        // 16 bpp: k_16 24, k_16_EXPAND 27, k_16_FLOAT 30. Otherwise 32 bpp.
        // REGPU_NO_FACE_STRIDE_BPP=1: 32 bpp for all of them (A/B).
        static LiveSwitch oldStride("REGPU_NO_FACE_STRIDE_BPP");
        const uint32_t f = fetch.Format();
        info = { 1, oldStride ? 2u : f == 21 || f == 26 || f == 29 || f == 37 ? 3u : f == 24 || f == 27 || f == 30 ? 1u : 2u };
    }
    const uint32_t blocksX = (fetch.Width() + info.blockSize - 1) / info.blockSize;
    uint32_t blocksY = (fetch.Height() + info.blockSize - 1) / info.blockSize;
    uint32_t pitchBlocks = std::max(fetch.Pitch() / info.blockSize, blocksX);
    if (fetch.Tiled())
    {
        pitchBlocks = (pitchBlocks + 31) & ~31u;
        blocksY = (blocksY + 31) & ~31u;
    }
    return ((pitchBlocks * blocksY << info.log2Bytes) + 4095) & ~4095u;
}
