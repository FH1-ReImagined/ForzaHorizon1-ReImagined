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

// The size of a copy's layout: the size its source layout was saved at when that is the scale's
// (a driving paint job drawn at 2x), else the game's (a save of unknown size included).
inline uint32_t CopyLayoutSize(uint32_t scale, uint32_t saved_size) {
  return scale > 1 && saved_size == ScaledSize(kGameSize, scale) ? saved_size : kGameSize;
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
