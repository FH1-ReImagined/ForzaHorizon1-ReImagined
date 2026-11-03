#pragma once
#include <cstdint>
#include <vector>

// A Xenos texture fetch constant (6 dwords), the fields the decoder needs.
struct TextureFetch
{
    uint32_t dwords[6];
    // A mip level in the packed tail (TextureMipLevel): its place in the tail's tile, in blocks;
    // overrides the fetch's own packed-mip offset. ~0u: none.
    uint32_t tailX = ~0u, tailY = 0;

    uint32_t Type() const { return dwords[0] & 3; } // 2 = texture
    bool Tiled() const { return dwords[0] >> 31; }
    uint32_t Pitch() const { return ((dwords[0] >> 22) & 0x1FF) << 5; } // texels
    uint32_t RawFormat() const { return dwords[1] & 0x3F; }
    // The _AS_16_16_16_16 formats (50-56) store their base format's bytes; the suffix only asks
    // for 16-bit filtering. They decode as the base: 8888, DXT1, DXT2_3, DXT4_5, 2_10_10_10,
    // 10_11_11, 11_11_10.
    uint32_t Format() const
    {
        static constexpr uint8_t kAs16Base[] = { 6, 18, 19, 20, 7, 16, 17 };
        const uint32_t f = RawFormat();
        return f >= 50 && f <= 56 ? kAs16Base[f - 50] : f;
    }
    uint32_t Endian() const { return (dwords[1] >> 6) & 3; }
    uint32_t Address() const { return dwords[1] & 0xFFFFF000; } // physical
    // 3D textures pack the size as 11 + 11 + 10 bits (Xenia's size_3d), the others 13 + 13.
    uint32_t Width() const { return Dimension() == 2 ? (dwords[2] & 0x7FF) + 1 : (dwords[2] & 0x1FFF) + 1; }
    uint32_t Height() const { return Dimension() == 2 ? ((dwords[2] >> 11) & 0x7FF) + 1 : ((dwords[2] >> 13) & 0x1FFF) + 1; }
    uint32_t Depth() const { return Dimension() == 2 ? ((dwords[2] >> 22) & 0x3FF) + 1 : 1; }
    uint32_t Swizzle() const { return (dwords[3] >> 1) & 0xFFF; }
    uint32_t Dimension() const { return (dwords[5] >> 9) & 3; } // 1 = 2D
    int32_t ExpAdjust() const { return int32_t(dwords[3] << 13) >> 26; } // texels scaled by 2^n
    uint32_t MipMinLevel() const { return (dwords[4] >> 2) & 15; }
    uint32_t MipMaxLevel() const { return (dwords[4] >> 6) & 15; }
    float LodBias() const { return float(int32_t(dwords[4] << 10) >> 22) / 32.0f; }
    uint32_t MipAddress() const { return dwords[5] & 0xFFFFF000; } // physical, levels 1+
};

TextureFetch ReadTextureFetch(uint32_t slot);
bool TextureFormatSupported(uint32_t format);
const char* TextureFormatName(uint32_t format);

// Decodes mip 0 of a 2D texture to RGBA8 (row-major, width*height*4).
bool DecodeTexture(const TextureFetch& fetch, std::vector<uint8_t>& rgba);
// The same from texture data outside guest memory (e.g. an .xds file after its header).
bool DecodeTexture(const TextureFetch& fetch, const uint8_t* source, std::vector<uint8_t>& rgba);
// Mip 0 of a 3D texture to RGBA8, slice after slice (width*height*depth*4).
bool DecodeTexture3D(const TextureFetch& fetch, std::vector<uint8_t>& rgba);

// Block-compressed formats the GPU samples natively: 1-4 for BC1-BC4 (DXT1, DXT3, DXT5,
// DXT5A), 0 for formats that are decoded to RGBA8.
uint32_t TextureBCKind(uint32_t format);
// Copies mip 0 of a block-compressed texture as linear BC blocks (untiled, endian-swapped)
// to dst, rows of blocks dstRowPitch bytes apart. No swizzle is applied (the GPU does it).
bool CopyTextureBlocks(const TextureFetch& fetch, uint8_t* dst, uint32_t dstRowPitch);

// The fetch constant of mip level `level` (its address, size and pitch), for tiled
// power-of-two textures down to the packed tail (levels 32 texels and up); false otherwise.
bool TextureMipLevel(const TextureFetch& base, uint32_t level, TextureFetch& out);

// The bytes the base level occupies in memory (rows padded to the pitch, tiled surfaces to 32
// blocks); 0 for a format this does not know.
size_t TextureSourceSize(const TextureFetch& fetch);

// The byte offset of texel (x, y) in a tiled surface of pitchBlocks (a multiple of 32) texels per row.
uint32_t TextureTiledOffset(uint32_t x, uint32_t y, uint32_t pitchBlocks, uint32_t log2Bytes);

// Bytes between the faces of a cube texture (each face is a padded 2D slice).
uint32_t TextureFaceStride(const TextureFetch& fetch);
