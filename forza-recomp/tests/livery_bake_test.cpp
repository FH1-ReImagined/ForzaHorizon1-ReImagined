// livery_bake_scale (src/hooks/livery_bake.cpp): the size of a car's paint job textures at
// each scale (src/platform/livery_bake.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/livery_bake_test.cpp
#include "platform/livery_bake.h"

#include <cstdio>
#include <cstdlib>
#include <initializer_list>
#include <vector>

namespace l = forza::livery_bake;

#define CHECK(x)                                              \
  do {                                                        \
    if (!(x)) {                                               \
      std::fprintf(stderr, "livery bake: FAIL: %s\n", #x);    \
      std::exit(1);                                           \
    }                                                         \
  } while (false)

static bool Same(l::Texture a, l::Texture b) {
  return a.width == b.width && a.height == b.height && a.levels == b.levels;
}

int main() {
  CHECK(l::ParseScale(1) == 1);
  CHECK(l::ParseScale(2) == 2);
  // 4096x4096 does not fit the game's memory for render textures: 4 is not a scale.
  for (const int bad : {0, -1, 3, 4, 8, 16}) CHECK(l::ParseScale(bad) == 1);

  CHECK(l::IsLiveryFormat(l::kRgbaFormat));
  CHECK(l::IsLiveryFormat(l::kDxt5Format));
  CHECK(!l::IsLiveryFormat(0x18280186));  // plain A8R8G8B8 (gauges, mirrors)
  CHECK(!l::IsLiveryFormat(0x18287F86));  // the sides (LivInt)

  const l::Texture n{1024, 1024, 6};       // LivRenderPristineN/DamageN, LivPristineN/DamageN
  const l::Texture layout{1024, 1024, 1};  // LivRenderTexture
  // Scale 1 is the game's.
  CHECK(Same(l::Scaled(n, 1), n));
  CHECK(Same(l::Scaled(layout, 1), layout));
  // 2: twice the side, the smallest mip level still 32x32 (Scaled also takes larger factors).
  CHECK(Same(l::Scaled(n, 2), {2048, 2048, 7}));
  CHECK(Same(l::Scaled(n, 4), {4096, 4096, 8}));
  // A texture without mips keeps one level.
  CHECK(Same(l::Scaled(layout, 2), {2048, 2048, 1}));
  CHECK(Same(l::Scaled(layout, 4), {4096, 4096, 1}));
  // The Paint Shop's 2048x2048, the "0" variants and other sizes stay as they are.
  CHECK(Same(l::Scaled({2048, 2048, 1}, 4), {2048, 2048, 1}));
  CHECK(Same(l::Scaled({2048, 2048, 7}, 2), {2048, 2048, 7}));
  CHECK(Same(l::Scaled({1882, 724, 1}, 4), {1882, 724, 1}));
  CHECK(Same(l::Scaled({1024, 512, 6}, 4), {1024, 512, 6}));
  CHECK(Same(l::Scaled({1024, 1024, 0}, 4), {1024, 1024, 0}));
  CHECK(l::ScaledSize(1024, 2) == 2048);

  // Photo mode's copy of a paint job loads the layout its driving paint job saved, as many bytes
  // as its own layout holds: at 2x it reads the whole 2048x2048 save and halves it (#680).
  CHECK(l::HalvesSave(2, 1024, 2048));
  CHECK(!l::HalvesSave(1, 1024, 1024));
  CHECK(!l::HalvesSave(2, 1024, 1024));  // saved before the scale applied
  CHECK(!l::HalvesSave(2, 1024, 0));     // a save it has no size for
  CHECK(!l::HalvesSave(1, 1024, 2048));  // the scale is off
  CHECK(!l::HalvesSave(2, 2048, 4096));
  CHECK(!l::HalvesSave(2, 1024, 4096));
  CHECK(l::kCopy == 2);

  // The tiled layout: every texel of a 2048x2048 and a 1024x1024 picture at its own 4 bytes.
  for (const uint32_t size : {1024u, 2048u}) {
    std::vector<bool> used(size_t(size) * size, false);
    bool ok = true;
    for (uint32_t y = 0; y < size && ok; ++y) {
      for (uint32_t x = 0; x < size; ++x) {
        const uint32_t off = l::TiledOffset32(x, y, size);
        if (off % 4 || off / 4 >= used.size() || used[off / 4]) { ok = false; break; }
        used[off / 4] = true;
      }
    }
    CHECK(ok);
  }
  CHECK(l::TiledOffset32(0, 0, 1024) == 0);
  CHECK(l::TiledOffset32(1, 0, 1024) == 4);

  // Halving: each texel of the half is the rounded average of its 2x2, read through the tiling.
  {
    const uint32_t size = 64, half = 32;
    std::vector<uint8_t> src(size_t(size) * size * 4), dst(size_t(half) * half * 4);
    auto value = [](uint32_t x, uint32_t y, int i) { return uint8_t((x * 3 + y * 5 + i * 40) & 0xFF); };
    for (uint32_t y = 0; y < size; ++y)
      for (uint32_t x = 0; x < size; ++x)
        for (int i = 0; i < 4; ++i) src[l::TiledOffset32(x, y, size) + i] = value(x, y, i);
    l::HalveTiled32(src.data(), size, dst.data());
    bool ok = true;
    for (uint32_t y = 0; y < half; ++y)
      for (uint32_t x = 0; x < half; ++x)
        for (int i = 0; i < 4; ++i) {
          const int sum = value(2 * x, 2 * y, i) + value(2 * x + 1, 2 * y, i) +
                          value(2 * x, 2 * y + 1, i) + value(2 * x + 1, 2 * y + 1, i);
          if (dst[l::TiledOffset32(x, y, half) + i] != uint8_t((sum + 2) / 4)) ok = false;
        }
    CHECK(ok);
  }

  // Memory: an 8:8:8:8 1024x1024 with 6 levels is 5.33 MB, its DXT5 copy a quarter of that.
  const uint64_t rgba_n = 4u * (1024 * 1024 + 512 * 512 + 256 * 256 + 128 * 128 + 64 * 64 + 32 * 32);
  CHECK(l::Bytes(n, l::kRgbaFormat) == rgba_n);
  CHECK(l::Bytes(n, l::kDxt5Format) == rgba_n / 4);
  CHECK(l::Bytes(l::Scaled(n, 2), l::kRgbaFormat) == 4u * 2048 * 2048 + rgba_n);
  CHECK(l::Bytes(layout, l::kRgbaFormat) == 4u * 1024 * 1024);

  std::puts("livery bake: ok");
  return 0;
}
