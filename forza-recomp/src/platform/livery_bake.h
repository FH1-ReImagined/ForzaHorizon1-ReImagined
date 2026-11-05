// forza - livery bake scale (#659, src/hooks/livery_bake.cpp): the size of the textures a
// car's paint job is drawn into for driving and photos.
#pragma once

#include <cstdint>

namespace forza::livery_bake {

// The chain's formats (D3DFORMAT): 8:8:8:8 sampled as 16:16:16:16 (LivRenderTexture,
// LivRenderPristineN/DamageN) and DXT5 sampled as 16:16:16:16 (LivPristineN/DamageN, the
// textures the car is drawn with).
constexpr uint32_t kRgbaFormat = 0x1A207FB2;
constexpr uint32_t kDxt5Format = 0x18287F75;
// The size the game draws a paint job at for driving (the Paint Shop's own is 2048).
constexpr uint32_t kGameSize = 1024;

inline bool IsLiveryFormat(uint32_t format) {
  return format == kRgbaFormat || format == kDxt5Format;
}

// 1 (the game's) or 2; anything else is 1. The game draws a side at double size at most (its
// section blit, sub_82D511B8, has a 2x path only), and 4096x4096 textures do not fit the
// memory the game sets aside for render textures (the car's livery is then missing and the game
// stops on it).
inline uint32_t ParseScale(int32_t value) { return value == 2 ? 2u : 1u; }

inline uint32_t ScaledSize(uint32_t size, uint32_t scale) { return size * scale; }

// +236 of a paint job: -1 the Paint Shop's editor, 1 a car's (its layout is saved once drawn,
// sub_82D4E7D0), 2 photo mode's copy, which draws no sides and loads that saved layout instead
// (sub_82D4E9F0, as many bytes as its own layout holds).
constexpr uint32_t kCopy = 2;

// Whether photo mode's copy, whose layout is `own` on a side, loads a save made at twice that
// (a driving paint job drawn at the scale): it then reads the whole save and halves it.
inline bool HalvesSave(uint32_t scale, uint32_t own, uint32_t saved_size) {
  return scale > 1 && own == kGameSize && saved_size == ScaledSize(kGameSize, scale) &&
         saved_size == 2 * own;
}

// Byte offset of texel (x, y) in a Xenos 2D-tiled 32-bit texture `width` texels wide
// (XGAddress2DTiledOffset), the layout's memory as the game saves and loads it.
inline uint32_t TiledOffset32(uint32_t x, uint32_t y, uint32_t width) {
  constexpr uint32_t kLogBpp = 2;
  const uint32_t aligned = (width + 31) & ~31u;
  uint32_t macro = ((y >> 5) * (aligned >> 5)) << (kLogBpp + 7);
  uint32_t micro = ((y & 6) << 2) << kLogBpp;
  const uint32_t outer = macro + ((micro & ~0xFu) << 1) + (micro & 0xF) +
                         ((y & 8) << (3 + kLogBpp)) + ((y & 1) << 4);
  macro = (x >> 5) << (kLogBpp + 7);
  micro = (x & 7) << kLogBpp;
  const uint32_t off = outer + macro + ((micro & ~0xFu) << 1) + (micro & 0xF);
  return ((off & ~0x1FFu) << 3) + ((off & 0x1C0) << 2) + (off & 0x3F) + ((y & 16) << 7) +
         (((((y & 8) >> 2) + (x >> 3)) & 3) << 6);
}

// Halves a tiled 8:8:8:8 picture `size` texels on a side (size x size x 4 bytes at src) into a
// tiled one of size / 2 at dst: each texel the rounded average of 2x2, per byte.
inline void HalveTiled32(const uint8_t* src, uint32_t size, uint8_t* dst) {
  const uint32_t half = size / 2;
  for (uint32_t y = 0; y < half; ++y) {
    for (uint32_t x = 0; x < half; ++x) {
      const uint8_t* a = src + TiledOffset32(2 * x, 2 * y, size);
      const uint8_t* b = src + TiledOffset32(2 * x + 1, 2 * y, size);
      const uint8_t* c = src + TiledOffset32(2 * x, 2 * y + 1, size);
      const uint8_t* d = src + TiledOffset32(2 * x + 1, 2 * y + 1, size);
      uint8_t* o = dst + TiledOffset32(x, y, half);
      for (int i = 0; i < 4; ++i) o[i] = uint8_t((a[i] + b[i] + c[i] + d[i] + 2) / 4);
    }
  }
}

struct Texture {
  uint32_t width;
  uint32_t height;
  uint32_t levels;
};

// The size a texture of the chain gets at `scale`: only the game's 1024x1024 ones change, to
// 1024 x scale on a side, with one more mip level per doubling where they have mips (so the
// smallest level stays 32x32 and a distant car filters as before). Others (the Paint Shop's
// 2048x2048, the unused "0" variants, the sides) stay as they are.
inline Texture Scaled(Texture t, uint32_t scale) {
  if (scale <= 1 || t.width != kGameSize || t.height != kGameSize || t.levels == 0) return t;
  Texture s{ScaledSize(t.width, scale), ScaledSize(t.height, scale), t.levels};
  if (t.levels > 1) {
    for (uint32_t x = 1; x < scale; x <<= 1) ++s.levels;
  }
  return s;
}

// Guest memory of a texture of the chain with its mip chain: 4 bytes a texel, 1 for DXT5
// (an upper bound for the levels below 32x32, which Xenos packs together).
inline uint64_t Bytes(Texture t, uint32_t format) {
  const uint64_t per_texel_x4 = format == kDxt5Format ? 4 : 16;  // quarter bytes
  uint64_t total = 0;
  uint64_t w = t.width, h = t.height;
  for (uint32_t i = 0; i < t.levels; ++i) {
    total += w * h * per_texel_x4 / 4;
    w = w > 1 ? w / 2 : 1;
    h = h > 1 ? h / 2 : 1;
  }
  return total;
}

}  // namespace forza::livery_bake
