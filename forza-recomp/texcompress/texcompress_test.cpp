// forza-texcompress (#413): the DDS files it writes decode back close to the pictures they were
// made from. Cut-out alpha keeps its edges (no holes), smooth gradients do not band, opaque BC1
// blocks never decode transparent, odd sizes get every mip level, and the files have the layout
// the game's reader (SDK pack_format.cpp) expects.
//   cmake -S forza-recomp/texcompress -B build && cmake --build build && ctest --test-dir build
#include <algorithm>
#include <bit>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "texcompress.h"

#define CHECK(x)                                                                    \
  do {                                                                              \
    if (!(x)) {                                                                     \
      std::fprintf(stderr, "texcompress: FAIL: %s (line %d)\n", #x, __LINE__);     \
      std::exit(1);                                                                 \
    }                                                                               \
  } while (false)

namespace tc = texcompress;

namespace {

tc::Image Make(uint32_t width, uint32_t height, auto texel) {
  tc::Image image;
  image.width = width;
  image.height = height;
  image.rgba.resize(size_t(width) * height * 4);
  for (uint32_t y = 0; y < height; ++y) {
    for (uint32_t x = 0; x < width; ++x) texel(x, y, &image.rgba[(size_t(y) * width + x) * 4]);
  }
  return image;
}

uint32_t Hash(uint32_t x, uint32_t y) {
  uint32_t h = x * 374761393u + y * 668265263u;
  h = (h ^ (h >> 13)) * 1274126177u;
  return h ^ (h >> 16);
}

// Compresses, reads the file back as the game would and decodes its largest level and mip 1.
struct RoundTrip {
  tc::Result result;
  tc::DdsFile dds;
  tc::Image level0, level1;
};

RoundTrip Run(const tc::Image& image, tc::Choice choice) {
  RoundTrip out;
  out.result = tc::Compress(image, choice, tc::Settings());
  std::string error;
  CHECK(tc::ReadDds(out.result.dds.data(), out.result.dds.size(), out.dds, error));
  CHECK(out.dds.format == out.result.format);
  CHECK(out.dds.width == image.width && out.dds.height == image.height);
  // Every level down to 1x1, and nothing after the last.
  CHECK(out.dds.levels.size() == size_t(std::bit_width(std::max(image.width, image.height))));
  CHECK(out.dds.levels.back().first + out.dds.levels.back().second == out.result.dds.size());
  const uint8_t* data = out.result.dds.data();
  out.level0 = tc::Decode(out.dds.format, data + out.dds.levels[0].first, out.dds.levels[0].second,
                          image.width, image.height);
  if (out.dds.levels.size() > 1) {
    out.level1 = tc::Decode(out.dds.format, data + out.dds.levels[1].first,
                            out.dds.levels[1].second, std::max(image.width / 2, 1u),
                            std::max(image.height / 2, 1u));
  }
  return out;
}

uint32_t Read32(const std::vector<uint8_t>& data, size_t offset) {
  uint32_t value;
  std::memcpy(&value, data.data() + offset, 4);
  return value;
}

}  // namespace

int main() {
  // A sky: a vertical gradient with a slow drift of hue and the texel noise an upscaled texture
  // has. Whichever format auto picks, it does not band: every texel within 6 levels, at most 2%
  // of its smooth blocks off by 4 or more.
  {
    const tc::Image sky = Make(256, 256, [](uint32_t x, uint32_t y, uint8_t* t) {
      const double v = y / 255.0;
      const int noise = int(Hash(x, y) % 3) - 1;
      t[0] = uint8_t(90 + 120 * v + 6 * std::sin(x / 40.0) + noise);
      t[1] = uint8_t(130 + 90 * v + noise);
      t[2] = uint8_t(200 + 50 * v - 8 * std::sin(x / 25.0 + v * 3) + noise);
      t[3] = 255;
    });
    const RoundTrip r = Run(sky, tc::Choice::kAuto);
    const tc::Error e = tc::Compare(sky, r.level0);
    CHECK(e.max_difference <= 6);
    CHECK(e.rgb_psnr > 45);
    CHECK(e.smooth_blocks > 3000 && e.banded_blocks * 50 <= e.smooth_blocks);
  }

  // Smooth colour that changes in every direction within a block (the case BC1's two end points
  // a block cannot follow): BC1 bands most of its blocks, so auto picks BC7.
  {
    const tc::Image waves = Make(256, 256, [](uint32_t x, uint32_t y, uint8_t* t) {
      t[0] = uint8_t(100 + 10 * std::sin(x / 3.0));
      t[1] = uint8_t(80 + 10 * std::sin(y / 4.0 + x / 5.0));
      t[2] = uint8_t(60 + 10 * std::cos((x + y) / 3.5));
      t[3] = 255;
    });
    const RoundTrip r = Run(waves, tc::Choice::kAuto);
    CHECK(r.result.format == tc::Format::kBC7);
    const tc::Error bc1 = tc::Compare(waves, Run(waves, tc::Choice::kBC1).level0);
    CHECK(bc1.banded_blocks * 50 > bc1.smooth_blocks);
    CHECK(tc::Compare(waves, r.level0).rgb_psnr > bc1.rgb_psnr + 2);
  }

  // Busy opaque ground (noise over a colour): BC1 would lose too much of it, so auto picks BC7.
  // Forced, BC1's 3-colour blocks never decode as transparent black (the texture is opaque).
  {
    const tc::Image ground = Make(128, 128, [](uint32_t x, uint32_t y, uint8_t* t) {
      const uint32_t h = Hash(x / 2, y / 2);
      t[0] = uint8_t(90 + (h & 63));
      t[1] = uint8_t(80 + ((h >> 6) & 63));
      t[2] = uint8_t(50 + ((h >> 12) & 31));
      t[3] = 255;
    });
    const RoundTrip automatic = Run(ground, tc::Choice::kAuto);
    CHECK(automatic.result.format == tc::Format::kBC7);
    CHECK(tc::Compare(ground, automatic.level0).rgb_psnr > 36);
    const RoundTrip r = Run(ground, tc::Choice::kBC1);
    CHECK(r.result.format == tc::Format::kBC1);
    CHECK(tc::Compare(ground, r.level0).rgb_psnr > 28);
    for (size_t i = 3; i < r.level0.rgba.size(); i += 4) CHECK(r.level0.rgba[i] == 255);
    for (size_t i = 3; i < r.level1.rgba.size(); i += 4) CHECK(r.level1.rgba[i] == 255);
  }

  // Flat paint (a sign's colour fields): BC1 keeps it nearly exactly, so auto picks BC1, an
  // eighth of RGBA8's memory.
  {
    const tc::Image sign = Make(128, 64, [](uint32_t x, uint32_t y, uint8_t* t) {
      const bool text = (x / 8 + y / 8) % 3 == 0;
      t[0] = text ? 240 : 30;
      t[1] = text ? 240 : 90;
      t[2] = text ? 230 : 40;
      t[3] = 255;
    });
    const RoundTrip r = Run(sign, tc::Choice::kAuto);
    CHECK(r.result.format == tc::Format::kBC1);
    CHECK(tc::Compare(sign, r.level0).max_difference <= 4);
    CHECK(r.result.dds.size() == 128 + (32 * 16 + 16 * 8 + 8 * 4 + 4 * 2 + 2 * 1 + 3) * 8);  // 8 levels
  }

  // Cut-out foliage: a leaf (alpha 0 outside, 255 inside, a 3-texel ramp at the edge as the
  // upscaled alpha has) over a green whose colour also fills the transparent texels. BC7 keeps
  // alpha's edge where it was: no texel far from half alpha crosses it (no holes), transparent
  // stays transparent (within 6%: next to the ramp), the leaf keeps its colour.
  {
    const tc::Image leaf = Make(192, 96, [](uint32_t x, uint32_t y, uint8_t* t) {
      const double dx = (double(x) - 96) / 80, dy = (double(y) - 48) / 40;
      const double edge = (1.0 - std::sqrt(dx * dx + dy * dy)) * 40;  // texels inside the edge
      const double veins = (x / 6 + y / 6) % 2 ? 12 : 0;
      t[0] = uint8_t(40 + veins);
      t[1] = uint8_t(110 + (Hash(x, y) & 15) + veins);
      t[2] = uint8_t(30 + veins / 2);
      t[3] = uint8_t(std::lround(std::clamp(edge / 3.0 + 0.5, 0.0, 1.0) * 255));
    });
    const RoundTrip r = Run(leaf, tc::Choice::kAuto);
    CHECK(r.result.format == tc::Format::kBC7);
    const tc::Error e = tc::Compare(leaf, r.level0);
    CHECK(e.hard_alpha_flips == 0);
    CHECK(e.alpha_flips <= leaf.width * leaf.height / 500);
    CHECK(e.alpha_psnr > 40);
    CHECK(e.rgb_psnr > 38);
    for (size_t i = 3; i < leaf.rgba.size(); i += 4) {
      if (leaf.rgba[i] == 0) CHECK(r.level0.rgba[i] <= 16);
      if (leaf.rgba[i] == 255) CHECK(r.level0.rgba[i] >= 239);
    }
    // Mip 1 is the box filter of the picture (the game's), compressed.
    const tc::Error mip = tc::Compare(tc::MakeMips(leaf)[1], r.level1);
    CHECK(mip.hard_alpha_flips == 0 && mip.alpha_psnr > 38 && mip.rgb_psnr > 36);
  }

  // Odd sizes: blocks over the edge, every level (70x30 has 7: 70x30 ... 1x1), each the right
  // length; the mips are the game's box filter.
  {
    const tc::Image odd = Make(70, 30, [](uint32_t x, uint32_t y, uint8_t* t) {
      t[0] = uint8_t(x * 3);
      t[1] = uint8_t(y * 8);
      t[2] = uint8_t(Hash(x, y));
      t[3] = uint8_t(x < 35 ? 255 : 128);
    });
    const RoundTrip r = Run(odd, tc::Choice::kAuto);
    CHECK(r.dds.levels.size() == 7);
    CHECK(r.dds.levels[0].second == size_t(18 * 8 * 16));
    CHECK(r.dds.levels[6].second == 16);
    const std::vector<tc::Image> mips = tc::MakeMips(odd);
    CHECK(mips.size() == 7 && mips[1].width == 35 && mips[1].height == 15);
    CHECK(mips[6].width == 1 && mips[6].height == 1);
    // A texel of mip 1: the rounded mean of its 2x2 texels.
    const auto at = [&](const tc::Image& image, uint32_t x, uint32_t y, uint32_t c) {
      return uint32_t(image.rgba[(size_t(y) * image.width + x) * 4 + c]);
    };
    CHECK(at(mips[1], 3, 2, 0) == (at(odd, 6, 4, 0) + at(odd, 7, 4, 0) + at(odd, 6, 5, 0) +
                                   at(odd, 7, 5, 0) + 2) / 4);
  }

  // The header as the game reads it: BC7 with the DX10 extension (DXGI 98, 2D, one), BC1 as
  // DXT1; the mip count and its flags; the base level's linear size.
  {
    const tc::Image flat = Make(64, 32, [](uint32_t, uint32_t, uint8_t* t) {
      t[0] = 200, t[1] = 100, t[2] = 50, t[3] = 255;
    });
    const tc::Result bc7 = tc::Compress(flat, tc::Choice::kBC7, tc::Settings());
    CHECK(std::memcmp(bc7.dds.data(), "DDS ", 4) == 0);
    CHECK(Read32(bc7.dds, 4) == 124);
    CHECK(Read32(bc7.dds, 4 + 4) & 0x20000);           // DDSD_MIPMAPCOUNT
    CHECK(Read32(bc7.dds, 4 + 6 * 4) == 7);            // 64x32 ... 1x1
    CHECK(Read32(bc7.dds, 4 + 4 * 4) == 16 * 8 * 16);  // the linear size of level 0
    CHECK(Read32(bc7.dds, 4 + 19 * 4) == 0x4);         // four CC
    CHECK(std::memcmp(bc7.dds.data() + 4 + 20 * 4, "DX10", 4) == 0);
    CHECK(Read32(bc7.dds, 128) == 98 && Read32(bc7.dds, 132) == 3 && Read32(bc7.dds, 140) == 1);
    const tc::Result bc1 = tc::Compress(flat, tc::Choice::kBC1, tc::Settings());
    CHECK(std::memcmp(bc1.dds.data() + 4 + 20 * 4, "DXT1", 4) == 0);
    CHECK(bc1.dds.size() == 128 + 16 * 8 * 8 + 8 * 4 * 8 + 4 * 2 * 8 + 2 * 1 * 8 + 3 * 8);
    // A flat colour comes back exactly in either.
    std::string error;
    tc::DdsFile file;
    CHECK(tc::ReadDds(bc7.dds.data(), bc7.dds.size(), file, error));
    CHECK(tc::Compare(flat, tc::Decode(tc::Format::kBC7, bc7.dds.data() + file.levels[0].first,
                                       file.levels[0].second, 64, 32))
              .max_difference <= 1);
    // A truncated file is refused.
    CHECK(!tc::ReadDds(bc7.dds.data(), bc7.dds.size() - 1, file, error));
  }

  std::printf("texcompress: OK\n");
  return 0;
}
