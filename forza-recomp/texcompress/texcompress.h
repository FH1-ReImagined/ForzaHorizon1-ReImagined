// Texture pack compression (#413, docs/texture_packs.md): RGBA8 pictures to BC1 or BC7 DDS
// files with their mip levels, which the renderer uploads as they are (SDK patch 0280,
// pack_format.cpp). A BC7 texture takes a quarter of the graphics memory of the RGBA8 one the game
// makes from a PNG, a BC1 texture an eighth.
//
// The encoders are Rich Geldreich's bc7enc_rdo (thirdparty/bc7enc_rdo, MIT or public domain):
// bc7enc.cpp for BC7 (modes 1 and 6 for opaque blocks, 5, 6 and 7 with alpha) and rgbcx for BC1.
// The mip levels are made the way the game makes them for a PNG (a 2x2 box filter, the same
// rounding), so a compressed pack differs from its PNGs only by the compression.
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace texcompress {

enum class Format { kBC1, kBC7 };

const char* FormatName(Format format);

// RGBA8, rows packed (4 * width bytes).
struct Image {
  uint32_t width = 0;
  uint32_t height = 0;
  std::vector<uint8_t> rgba;
};

// Every level down to 1x1, the first being `base`: each the 2x2 box filter of the one before
// (odd sizes clamp at the edge), as the game's GenerateMips (pack.cpp) does for a PNG.
std::vector<Image> MakeMips(const Image& base);

bool IsOpaque(const Image& image);

// Measured on a race's dump upscaled 2x (docs/texture_packs.md, "Compression"): bc7enc's higher
// uber levels gain under 0.1 dB at 1.5-3 times the time; alpha weighted twice as much as luma
// keeps 4 times fewer texels from crossing an alpha test's cut (0.6 dB of RGB PSNR less).
struct Settings {
  uint32_t bc7_uber_level = 0;     // bc7enc: 0 (fastest) to 4
  // bc7enc's perceptual weights (Y 128, Cb 64, Cr 16) and this for alpha.
  uint32_t bc7_alpha_weight = 256;
  uint32_t bc1_level = 12;         // rgbcx: 0 (fastest) to 18
  // An opaque texture under kAuto is BC1 only when BC1 keeps it nearly as well as BC7: its
  // largest level at least min_bc1_psnr (RGB, dB), no 4x4 block worse than max_bc1_block_rmse,
  // and at most max_bc1_banded of its smooth blocks banded. Otherwise BC7.
  double min_bc1_psnr = 40.0;
  double max_bc1_block_rmse = 12.0;
  double max_bc1_banded = 0.02;
};

// The blocks of one level (blocks at the right and bottom edge repeat the last texel).
std::vector<uint8_t> Encode(Format format, const Image& image, const Settings& settings);
Image Decode(Format format, const uint8_t* blocks, size_t size, uint32_t width, uint32_t height);

struct Error {
  double rgb_psnr = 0;       // over all texels, dB (999 when equal)
  double alpha_psnr = 0;
  double worst_block_rmse = 0;  // RGB, of the worst 4x4 block
  uint32_t max_difference = 0;  // of any channel
  // Texels on the other side of half alpha (an alpha test's usual cut) than in the original:
  // holes in or growth of cut-out foliage.
  uint64_t alpha_flips = 0;
  // Of those, texels whose original alpha was far (32 or more) from half: holes, not an edge moved.
  uint64_t hard_alpha_flips = 0;
  // Smooth 4x4 blocks (every channel within kSmoothRange in the original: gradients, sky, flat
  // paint) and those of them the encoder banded (a texel off by kBandError or more).
  uint64_t smooth_blocks = 0;
  uint64_t banded_blocks = 0;
};
Error Compare(const Image& original, const Image& decoded);

// A DDS file: BC1 as DXT1, BC7 with the DX10 header (DXGI_FORMAT_BC7_UNORM), with its levels.
std::vector<uint8_t> WriteDds(Format format, uint32_t width, uint32_t height,
                              const std::vector<std::vector<uint8_t>>& levels);

struct DdsFile {
  Format format = Format::kBC1;
  uint32_t width = 0;
  uint32_t height = 0;
  std::vector<std::pair<size_t, size_t>> levels;  // offset, size in the file
};
// Reads what WriteDds writes (BC1/DXT1 and BC7); false and why for anything else.
bool ReadDds(const uint8_t* data, size_t size, DdsFile& out, std::string& error);

enum class Choice { kBC1, kBC7, kAuto };

struct Result {
  Format format = Format::kBC7;
  uint32_t levels = 0;
  Error error;  // of the largest level
  std::vector<uint8_t> dds;
};
// The whole texture: its levels, each encoded; kAuto picks BC1 for an opaque texture that BC1
// keeps well enough (Settings), else BC7.
Result Compress(const Image& base, Choice choice, const Settings& settings);

}  // namespace texcompress
