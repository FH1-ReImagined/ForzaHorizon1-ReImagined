// Texture pack DDS files (#384): src/graphics/pipeline/texture/pack_format.cpp of the patched SDK
// source (patch 0280). Padded rows of uncompressed images (the header's pitch, per mip level),
// headers that do not match their data, the DX10 header, and every format written by the dump
// read back unchanged. The DDS files are made here. Also the texture_packs order (patch 0310).
// Build with the patched SDK source:
//   c++ -std=c++20 -I ~/rexglue-sdk-src-forza/sdk/include
//       ~/rexglue-sdk-src-forza/sdk/src/graphics/pipeline/texture/pack_format.cpp
//       tools/test_texture_pack_dds.cpp -o test_texture_pack_dds
#include <rex/graphics/pipeline/texture/pack_format.h>

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace tp = rex::graphics::texture_pack;

#define CHECK(x)                                                \
  do {                                                          \
    if (!(x)) {                                                 \
      std::fprintf(stderr, "texture pack DDS: FAIL: %s (line %d)\n", #x, __LINE__); \
      std::exit(1);                                             \
    }                                                           \
  } while (false)

namespace {

constexpr uint32_t FourCC(char a, char b, char c, char d) {
  return uint32_t(uint8_t(a)) | uint32_t(uint8_t(b)) << 8 | uint32_t(uint8_t(c)) << 16 |
         uint32_t(uint8_t(d)) << 24;
}

// A DDS header as writers fill it in.
struct Header {
  uint32_t width = 0, height = 0;
  uint32_t flags = 0x1 | 0x2 | 0x4 | 0x1000;  // caps, height, width, pixel format
  uint32_t pitch_or_linear_size = 0;
  uint32_t mips = 1;
  uint32_t pixel_flags = 0, four_cc = 0, bits = 0, r = 0, g = 0, b = 0, a = 0;
  uint32_t caps2 = 0;
  bool dx10 = false;
  uint32_t dxgi = 0, dimension = 3, misc = 0, array_size = 1;
};

std::vector<uint8_t> File(const Header& h, const std::vector<uint8_t>& payload) {
  uint32_t words[31] = {};
  words[0] = 124;
  words[1] = h.flags | (h.mips > 1 ? 0x20000 : 0);
  words[2] = h.height;
  words[3] = h.width;
  words[4] = h.pitch_or_linear_size;
  words[6] = h.mips;
  words[18] = 32;
  words[19] = h.dx10 ? 0x4 : h.pixel_flags;
  words[20] = h.dx10 ? FourCC('D', 'X', '1', '0') : h.four_cc;
  words[21] = h.bits;
  words[22] = h.r;
  words[23] = h.g;
  words[24] = h.b;
  words[25] = h.a;
  words[26] = 0x1000;
  words[27] = h.caps2;
  std::vector<uint8_t> file(4 + sizeof(words));
  std::memcpy(file.data(), "DDS ", 4);
  std::memcpy(file.data() + 4, words, sizeof(words));
  if (h.dx10) {
    const uint32_t extension[5] = {h.dxgi, h.dimension, h.misc, h.array_size, 0};
    file.insert(file.end(), reinterpret_cast<const uint8_t*>(extension),
                reinterpret_cast<const uint8_t*>(extension) + sizeof(extension));
  }
  file.insert(file.end(), payload.begin(), payload.end());
  return file;
}

// Levels of width x height with element_bytes per texel, each row padded to its pitch with 0xEE.
// pitch(level width) gives the pitch; texel values count up from 1 across the whole chain.
template <typename Pitch>
std::vector<uint8_t> Levels(uint32_t width, uint32_t height, uint32_t mips, uint32_t element_bytes,
                            Pitch pitch) {
  std::vector<uint8_t> out;
  uint8_t value = 1;
  for (uint32_t level = 0; level < mips; ++level) {
    const uint32_t w = width >> level ? width >> level : 1, h = height >> level ? height >> level : 1;
    const uint32_t row = w * element_bytes, stride = pitch(w, row);
    for (uint32_t y = 0; y < h; ++y) {
      for (uint32_t x = 0; x < row; ++x) out.push_back(value++);
      for (uint32_t x = row; x < stride; ++x) out.push_back(0xEE);
    }
  }
  return out;
}
uint32_t Packed(uint32_t, uint32_t row) { return row; }
uint32_t FourBytes(uint32_t, uint32_t row) { return (row + 3) & ~3u; }

bool Decode(const std::vector<uint8_t>& file, tp::Decoded& out, std::string& error) {
  out = tp::Decoded();
  error.clear();
  return tp::DecodeDds(file.data(), file.size(), out, error);
}

bool Same(const tp::Decoded& a, const tp::Decoded& b) {
  if (a.format != b.format || a.width != b.width || a.height != b.height ||
      a.levels.size() != b.levels.size() || a.data != b.data) {
    return false;
  }
  for (size_t i = 0; i < a.levels.size(); ++i) {
    if (a.levels[i].offset != b.levels[i].offset || a.levels[i].size != b.levels[i].size ||
        a.levels[i].width != b.levels[i].width || a.levels[i].height != b.levels[i].height) {
      return false;
    }
  }
  return true;
}

// The padded file must decode to exactly what the packed one does.
void CheckPaddedMatchesPacked(Header padded, std::vector<uint8_t> padded_payload, Header packed,
                              std::vector<uint8_t> packed_payload) {
  tp::Decoded a, b;
  std::string error;
  CHECK(Decode(File(packed, packed_payload), b, error));
  CHECK(Decode(File(padded, padded_payload), a, error));
  CHECK(Same(a, b));
  for (uint8_t v : a.data) CHECK(v != 0xEE);  // no padding byte became a texel
}

Header Luminance8(uint32_t width, uint32_t height) {
  Header h;
  h.width = width;
  h.height = height;
  h.pixel_flags = 0x20000;
  h.bits = 8;
  h.r = 0xFF;
  return h;
}

}  // namespace

int main() {
  tp::Decoded decoded;
  std::string error;

  // The review's case: R8 3x2 rows padded to 4 bytes, said by the pitch (DDSD_PITCH).
  {
    Header h = Luminance8(3, 2);
    h.flags |= 0x8;
    h.pitch_or_linear_size = 4;
    CHECK(Decode(File(h, {1, 2, 3, 99, 4, 5, 6, 99}), decoded, error));
    CHECK(decoded.format == tp::Format::kR8);
    CHECK((decoded.data == std::vector<uint8_t>{1, 2, 3, 4, 5, 6}));
    CHECK(decoded.levels.size() == 1 && decoded.levels[0].size == 6);
  }

  // Rows padded to 4 bytes in every mip level (Direct3D 9 surfaces): R8 6x5, 3 levels.
  {
    Header padded = Luminance8(6, 5), packed = padded;
    padded.flags |= 0x8;
    padded.pitch_or_linear_size = 8;
    padded.mips = packed.mips = 3;
    packed.flags |= 0x8;
    packed.pitch_or_linear_size = 6;
    CheckPaddedMatchesPacked(padded, Levels(6, 5, 3, 1, FourBytes), packed,
                             Levels(6, 5, 3, 1, Packed));
  }

  // The DX10 header: RG8 (DXGI 49) 5x4 with mips, rows padded to 4 bytes.
  {
    Header padded;
    padded.width = 5;
    padded.height = 4;
    padded.mips = 3;
    padded.dx10 = true;
    padded.dxgi = 49;
    Header packed = padded;
    padded.flags |= 0x8;
    padded.pitch_or_linear_size = 12;
    CheckPaddedMatchesPacked(padded, Levels(5, 4, 3, 2, FourBytes), packed,
                             Levels(5, 4, 3, 2, Packed));
    tp::Decoded rg;
    CHECK(Decode(File(packed, Levels(5, 4, 3, 2, Packed)), rg, error));
    CHECK(rg.format == tp::Format::kRG8 && rg.levels.size() == 3);
  }

  // Level 0 needs no padding (8 bytes), the small levels do: a file exactly the size of 4-byte
  // rows has them, one of packed rows has not.
  {
    Header h = Luminance8(8, 4);
    h.mips = 4;
    h.flags |= 0x8;
    h.pitch_or_linear_size = 8;
    CheckPaddedMatchesPacked(h, Levels(8, 4, 4, 1, FourBytes), h, Levels(8, 4, 4, 1, Packed));
  }

  // Any pitch in a file without mips: RGBA8 (masks) 3x3 rows of 16 bytes, BGRA8 (DX10) 2x2 of 12.
  {
    Header padded;
    padded.width = padded.height = 3;
    padded.pixel_flags = 0x40 | 0x1;
    padded.bits = 32;
    padded.r = 0xFF;
    padded.g = 0xFF00;
    padded.b = 0xFF0000;
    padded.a = 0xFF000000;
    Header packed = padded;
    padded.flags |= 0x8;
    padded.pitch_or_linear_size = 16;
    CheckPaddedMatchesPacked(padded, Levels(3, 3, 1, 4, [](uint32_t, uint32_t) { return 16u; }),
                             packed, Levels(3, 3, 1, 4, Packed));
    Header bgra;
    bgra.width = bgra.height = 2;
    bgra.dx10 = true;
    bgra.dxgi = 87;
    Header bgra_packed = bgra;
    bgra.flags |= 0x8;
    bgra.pitch_or_linear_size = 12;
    CheckPaddedMatchesPacked(bgra, Levels(2, 2, 1, 4, [](uint32_t, uint32_t) { return 12u; }),
                             bgra_packed, Levels(2, 2, 1, 4, Packed));
  }

  // Headers that do not match their data are refused, with the reason.
  {
    Header h = Luminance8(3, 2);
    h.flags |= 0x8;
    h.pitch_or_linear_size = 2;  // shorter than a row
    CHECK(!Decode(File(h, {1, 2, 3, 4, 5, 6}), decoded, error));
    CHECK(error.find("shorter than a row") != std::string::npos);
    h.pitch_or_linear_size = 5;  // not 4-byte rows, and mips
    h.mips = 2;
    CHECK(!Decode(File(h, Levels(3, 2, 2, 1, [](uint32_t, uint32_t) { return 5u; })), decoded,
                  error));
    CHECK(error.find("mip levels") != std::string::npos);
    Header short_file = Luminance8(4, 4);
    short_file.mips = 3;
    std::vector<uint8_t> payload = Levels(4, 4, 3, 1, Packed);
    payload.pop_back();  // the last level a byte short
    CHECK(!Decode(File(short_file, payload), decoded, error));
    CHECK(error.find("shorter than its header says") != std::string::npos);
    Header padded_short = Luminance8(3, 2);
    padded_short.flags |= 0x8;
    padded_short.pitch_or_linear_size = 4;
    CHECK(!Decode(File(padded_short, {1, 2, 3, 99, 4, 5, 6}), decoded, error));  // no last pad
    CHECK(error.find("shorter than its header says") != std::string::npos);
    Header array;
    array.width = array.height = 4;
    array.dx10 = true;
    array.dxgi = 28;
    array.array_size = 2;
    CHECK(!Decode(File(array, Levels(4, 4, 1, 4, Packed)), decoded, error));
    Header cube = Luminance8(4, 4);
    cube.caps2 = 0x200 | 0xFC00;
    CHECK(!Decode(File(cube, Levels(4, 4, 1, 1, Packed)), decoded, error));
    CHECK(!Decode(std::vector<uint8_t>(100, 0), decoded, error));
  }

  // Block-compressed data has no row padding; a linear size that is not the level's size (as
  // some writers set it) is not used.
  {
    Header h;
    h.width = 10;
    h.height = 6;
    h.flags |= 0x80000;
    h.pitch_or_linear_size = (10 + 3) * 4;
    h.pixel_flags = 0x4;
    h.four_cc = FourCC('D', 'X', 'T', '1');
    h.mips = 2;
    std::vector<uint8_t> payload(3 * 2 * 8 + 2 * 1 * 8);
    for (size_t i = 0; i < payload.size(); ++i) payload[i] = uint8_t(i);
    CHECK(Decode(File(h, payload), decoded, error));
    CHECK(decoded.format == tp::Format::kBC1 && decoded.levels.size() == 2);
    CHECK(decoded.levels[0].size == 48 && decoded.levels[1].size == 16);
    CHECK(std::memcmp(decoded.data.data(), payload.data(), 48) == 0);
    CHECK(std::memcmp(decoded.data.data() + decoded.levels[1].offset, payload.data() + 48, 16) == 0);
  }

  // What the dump writes reads back unchanged, every format, with mips.
  {
    const tp::Format formats[] = {tp::Format::kR8,  tp::Format::kRG8, tp::Format::kRGBA8,
                                  tp::Format::kBGRA8, tp::Format::kBC1, tp::Format::kBC2,
                                  tp::Format::kBC3, tp::Format::kBC4, tp::Format::kBC5,
                                  tp::Format::kBC7};
    for (tp::Format format : formats) {
      const uint32_t width = 13, height = 7;
      std::vector<std::vector<uint8_t>> levels;
      uint32_t seed = 7;
      for (uint32_t w = width, h = height;; w = w > 1 ? w / 2 : 1, h = h > 1 ? h / 2 : 1) {
        std::vector<uint8_t> level(size_t(tp::LevelBytes(format, w, h)));
        for (uint8_t& v : level) v = uint8_t((seed = seed * 1103515245u + 12345u) >> 16);
        levels.push_back(level);
        if (w == 1 && h == 1) break;
      }
      const std::vector<uint8_t> file = tp::EncodeDds(format, width, height, levels);
      CHECK(!file.empty());
      CHECK(Decode(file, decoded, error));
      CHECK(decoded.format == format && decoded.width == width && decoded.height == height);
      CHECK(decoded.levels.size() == levels.size());
      for (size_t i = 0; i < levels.size(); ++i) {
        CHECK(decoded.levels[i].size == levels[i].size());
        CHECK(std::memcmp(decoded.data.data() + decoded.levels[i].offset, levels[i].data(),
                          levels[i].size()) == 0);
      }
    }
  }

  // texture_packs (patch 0310): folder names in order, "." for the loose files; paths, "dump"
  // and repeats left out.
  {
    size_t refused = 0;
    CHECK(tp::ParsePackOrder("|", refused).empty() && refused == 0);
    CHECK(tp::ParsePackOrder("", refused).empty());
    const auto names = tp::ParsePackOrder(".|hd-pack|Rocks v2||hd-pack|../up|a/b|c\\d|C:|.|..|Dump|x", refused);
    CHECK((names == std::vector<std::string>{".", "hd-pack", "Rocks v2", "x"}));
    CHECK(refused == 6);
  }

  std::printf("texture pack DDS: PASS\n");
  return 0;
}
