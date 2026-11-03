// Texture pack compression (#413): see texcompress.h.
#include "texcompress.h"

#include <algorithm>
#include <bit>
#include <cmath>
#include <cstring>
#include <mutex>

#include "bc7decomp.h"
#include "bc7enc.h"
#include "rgbcx.h"

namespace texcompress {

namespace {

void InitEncoders() {
  static std::once_flag once;
  std::call_once(once, [] {
    bc7enc_compress_block_init();
    rgbcx::init(rgbcx::bc1_approx_mode::cBC1Ideal);
  });
}

uint32_t BlockBytes(Format format) { return format == Format::kBC1 ? 8 : 16; }

size_t LevelBytes(Format format, uint32_t width, uint32_t height) {
  return size_t((width + 3) / 4) * ((height + 3) / 4) * BlockBytes(format);
}

// The 16 texels of the block at (bx, by); outside the image, the nearest edge texel.
void GatherBlock(const Image& image, uint32_t bx, uint32_t by, uint8_t (*out)[4]) {
  for (uint32_t y = 0; y < 4; ++y) {
    const uint32_t sy = std::min(by * 4 + y, image.height - 1);
    for (uint32_t x = 0; x < 4; ++x) {
      const uint32_t sx = std::min(bx * 4 + x, image.width - 1);
      std::memcpy(out[y * 4 + x], &image.rgba[(size_t(sy) * image.width + sx) * 4], 4);
    }
  }
}

double Psnr(double squared, uint64_t count) {
  if (!count || squared <= 0) return 999.0;
  const double mse = squared / double(count);
  return 10.0 * std::log10(255.0 * 255.0 / mse);
}

constexpr uint32_t FourCC(char a, char b, char c, char d) {
  return uint32_t(uint8_t(a)) | uint32_t(uint8_t(b)) << 8 | uint32_t(uint8_t(c)) << 16 |
         uint32_t(uint8_t(d)) << 24;
}

constexpr int kSmoothRange = 12;
constexpr uint32_t kBandError = 4;

constexpr uint32_t kDxgiBC7 = 98, kDxgiBC7Srgb = 99, kDxgiBC1 = 71, kDxgiBC1Srgb = 72;

}  // namespace

const char* FormatName(Format format) { return format == Format::kBC1 ? "BC1" : "BC7"; }

std::vector<Image> MakeMips(const Image& base) {
  std::vector<Image> levels;
  levels.push_back(base);
  while (levels.back().width > 1 || levels.back().height > 1) {
    const Image& source = levels.back();
    const uint32_t width = source.width, height = source.height;
    Image level;
    level.width = std::max(width / 2, 1u);
    level.height = std::max(height / 2, 1u);
    level.rgba.resize(size_t(level.width) * level.height * 4);
    for (uint32_t y = 0; y < level.height; ++y) {
      const uint32_t y0 = std::min(2 * y, height - 1), y1 = std::min(2 * y + 1, height - 1);
      for (uint32_t x = 0; x < level.width; ++x) {
        const uint32_t x0 = std::min(2 * x, width - 1), x1 = std::min(2 * x + 1, width - 1);
        for (uint32_t c = 0; c < 4; ++c) {
          const uint32_t sum = source.rgba[(size_t(y0) * width + x0) * 4 + c] +
                               source.rgba[(size_t(y0) * width + x1) * 4 + c] +
                               source.rgba[(size_t(y1) * width + x0) * 4 + c] +
                               source.rgba[(size_t(y1) * width + x1) * 4 + c];
          level.rgba[(size_t(y) * level.width + x) * 4 + c] = uint8_t((sum + 2) / 4);
        }
      }
    }
    levels.push_back(std::move(level));
  }
  return levels;
}

bool IsOpaque(const Image& image) {
  for (size_t i = 3; i < image.rgba.size(); i += 4) {
    if (image.rgba[i] != 255) return false;
  }
  return true;
}

std::vector<uint8_t> Encode(Format format, const Image& image, const Settings& settings) {
  InitEncoders();
  const uint32_t width_blocks = (image.width + 3) / 4, height_blocks = (image.height + 3) / 4;
  const uint32_t block_bytes = BlockBytes(format);
  std::vector<uint8_t> out(size_t(width_blocks) * height_blocks * block_bytes);
  bc7enc_compress_block_params params;
  bc7enc_compress_block_params_init(&params);
  bc7enc_compress_block_params_init_perceptual_weights(&params);
  params.m_uber_level = std::min<uint32_t>(settings.bc7_uber_level, BC7ENC_MAX_UBER_LEVEL);
  params.m_weights[3] = settings.bc7_alpha_weight;
  const uint32_t bc1_level = std::min<uint32_t>(settings.bc1_level, rgbcx::MAX_LEVEL);
  uint8_t texels[16][4];
  for (uint32_t by = 0; by < height_blocks; ++by) {
    for (uint32_t bx = 0; bx < width_blocks; ++bx) {
      GatherBlock(image, bx, by, texels);
      uint8_t* block = &out[(size_t(by) * width_blocks + bx) * block_bytes];
      if (format == Format::kBC7) {
        bc7enc_compress_block(block, texels, &params);
      } else {
        // 3-colour blocks, but never their transparent black: the texture is opaque.
        rgbcx::encode_bc1(bc1_level, block, &texels[0][0], true, false);
      }
    }
  }
  return out;
}

Image Decode(Format format, const uint8_t* blocks, size_t size, uint32_t width, uint32_t height) {
  InitEncoders();
  Image image;
  image.width = width;
  image.height = height;
  image.rgba.assign(size_t(width) * height * 4, 0);
  const uint32_t width_blocks = (width + 3) / 4, height_blocks = (height + 3) / 4;
  const uint32_t block_bytes = BlockBytes(format);
  if (size < size_t(width_blocks) * height_blocks * block_bytes) return {};
  uint8_t texels[16][4];
  for (uint32_t by = 0; by < height_blocks; ++by) {
    for (uint32_t bx = 0; bx < width_blocks; ++bx) {
      const uint8_t* block = blocks + (size_t(by) * width_blocks + bx) * block_bytes;
      if (format == Format::kBC7) {
        bc7decomp::unpack_bc7(block, reinterpret_cast<bc7decomp::color_rgba*>(texels));
      } else {
        rgbcx::unpack_bc1(block, texels, true, rgbcx::bc1_approx_mode::cBC1Ideal);
      }
      for (uint32_t y = 0; y < 4 && by * 4 + y < height; ++y) {
        for (uint32_t x = 0; x < 4 && bx * 4 + x < width; ++x) {
          std::memcpy(&image.rgba[(size_t(by * 4 + y) * width + bx * 4 + x) * 4], texels[y * 4 + x],
                      4);
        }
      }
    }
  }
  return image;
}

Error Compare(const Image& original, const Image& decoded) {
  Error error;
  if (original.width != decoded.width || original.height != decoded.height ||
      original.rgba.size() != decoded.rgba.size()) {
    error.max_difference = 255;
    return error;
  }
  double rgb = 0, alpha = 0;
  const uint32_t width = original.width, height = original.height;
  for (uint32_t by = 0; by < (height + 3) / 4; ++by) {
    for (uint32_t bx = 0; bx < (width + 3) / 4; ++bx) {
      double block = 0;
      uint32_t texels = 0;
      uint8_t low[3] = {255, 255, 255}, high[3] = {0, 0, 0};
      uint32_t block_max = 0;
      for (uint32_t y = by * 4; y < std::min(by * 4 + 4, height); ++y) {
        for (uint32_t x = bx * 4; x < std::min(bx * 4 + 4, width); ++x) {
          const size_t i = (size_t(y) * width + x) * 4;
          for (uint32_t c = 0; c < 4; ++c) {
            const int d = int(original.rgba[i + c]) - int(decoded.rgba[i + c]);
            error.max_difference = std::max<uint32_t>(error.max_difference, uint32_t(std::abs(d)));
            (c < 3 ? block : alpha) += double(d * d);
            if (c < 3) {
              low[c] = std::min(low[c], original.rgba[i + c]);
              high[c] = std::max(high[c], original.rgba[i + c]);
              block_max = std::max<uint32_t>(block_max, uint32_t(std::abs(d)));
            }
          }
          if ((original.rgba[i + 3] >= 128) != (decoded.rgba[i + 3] >= 128)) {
            ++error.alpha_flips;
            if (std::abs(int(original.rgba[i + 3]) - 128) >= 32) ++error.hard_alpha_flips;
          }
          ++texels;
        }
      }
      if (high[0] - low[0] <= kSmoothRange && high[1] - low[1] <= kSmoothRange &&
          high[2] - low[2] <= kSmoothRange) {
        ++error.smooth_blocks;
        if (block_max >= kBandError) ++error.banded_blocks;
      }
      rgb += block;
      error.worst_block_rmse = std::max(error.worst_block_rmse, std::sqrt(block / (3.0 * texels)));
    }
  }
  const uint64_t count = uint64_t(width) * height;
  error.rgb_psnr = Psnr(rgb, count * 3);
  error.alpha_psnr = Psnr(alpha, count);
  return error;
}

std::vector<uint8_t> WriteDds(Format format, uint32_t width, uint32_t height,
                              const std::vector<std::vector<uint8_t>>& levels) {
  uint32_t header[31] = {};
  header[0] = 124;
  header[1] = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000;  // caps, height, width, pixel format, linear size
  header[2] = height;
  header[3] = width;
  header[4] = uint32_t(LevelBytes(format, width, height));
  header[6] = uint32_t(levels.size());
  if (levels.size() > 1) header[1] |= 0x20000;  // mip map count
  header[18] = 32;
  header[19] = 0x4;  // four CC
  header[20] = format == Format::kBC1 ? FourCC('D', 'X', 'T', '1') : FourCC('D', 'X', '1', '0');
  header[26] = 0x1000;                                   // texture
  if (levels.size() > 1) header[26] |= 0x8 | 0x400000;  // complex, mip map
  std::vector<uint8_t> out(4 + sizeof(header));
  std::memcpy(out.data(), "DDS ", 4);
  std::memcpy(out.data() + 4, header, sizeof(header));
  if (format == Format::kBC7) {
    const uint32_t extension[5] = {kDxgiBC7, 3 /* 2D */, 0, 1, 0};
    out.insert(out.end(), reinterpret_cast<const uint8_t*>(extension),
               reinterpret_cast<const uint8_t*>(extension) + sizeof(extension));
  }
  for (const std::vector<uint8_t>& level : levels) out.insert(out.end(), level.begin(), level.end());
  return out;
}

bool ReadDds(const uint8_t* data, size_t size, DdsFile& out, std::string& error) {
  if (size < 128 || std::memcmp(data, "DDS ", 4)) {
    error = "not a DDS file";
    return false;
  }
  uint32_t header[31];
  std::memcpy(header, data + 4, sizeof(header));
  if (header[0] != 124 || !(header[19] & 0x4)) {
    error = "not a block-compressed DDS file";
    return false;
  }
  size_t offset = 128;
  if (header[20] == FourCC('D', 'X', 'T', '1')) {
    out.format = Format::kBC1;
  } else if (header[20] == FourCC('D', 'X', '1', '0') && size >= 148) {
    uint32_t extension[5];
    std::memcpy(extension, data + 128, sizeof(extension));
    offset = 148;
    if (extension[0] == kDxgiBC7 || extension[0] == kDxgiBC7Srgb) {
      out.format = Format::kBC7;
    } else if (extension[0] == kDxgiBC1 || extension[0] == kDxgiBC1Srgb) {
      out.format = Format::kBC1;
    } else {
      error = "DXGI format " + std::to_string(extension[0]) + " (only BC1 and BC7 are read)";
      return false;
    }
  } else {
    error = "a format other than BC1 or BC7";
    return false;
  }
  out.width = header[3];
  out.height = header[2];
  if (!out.width || !out.height || out.width > 16384 || out.height > 16384) {
    error = "bad size";
    return false;
  }
  uint32_t count = (header[1] & 0x20000) && header[6] ? header[6] : 1;
  count = std::min(count, uint32_t(std::bit_width(std::max(out.width, out.height))));
  out.levels.clear();
  for (uint32_t level = 0; level < count; ++level) {
    const size_t bytes = LevelBytes(out.format, std::max(out.width >> level, 1u),
                                    std::max(out.height >> level, 1u));
    if (offset + bytes > size) {
      error = "shorter than its header says";
      return false;
    }
    out.levels.emplace_back(offset, bytes);
    offset += bytes;
  }
  return true;
}

Result Compress(const Image& base, Choice choice, const Settings& settings) {
  Result result;
  const std::vector<Image> levels = MakeMips(base);
  result.levels = uint32_t(levels.size());
  std::vector<std::vector<uint8_t>> encoded(levels.size());
  Format format = choice == Choice::kBC1 ? Format::kBC1 : Format::kBC7;
  if (choice == Choice::kAuto && IsOpaque(base)) {
    // BC1 for an opaque texture it keeps nearly as well as BC7. The worst block and the banded
    // smooth blocks catch what a PSNR over the whole texture hides: a colour edge smeared in one
    // place, a gradient or sky in steps (BC1's 5:6:5 end points and four levels a block).
    encoded[0] = Encode(Format::kBC1, base, settings);
    result.error = Compare(base, Decode(Format::kBC1, encoded[0].data(), encoded[0].size(),
                                        base.width, base.height));
    if (result.error.rgb_psnr >= settings.min_bc1_psnr &&
        result.error.worst_block_rmse <= settings.max_bc1_block_rmse &&
        double(result.error.banded_blocks) <=
            settings.max_bc1_banded * double(result.error.smooth_blocks)) {
      format = Format::kBC1;
    } else {
      encoded[0].clear();
    }
  }
  if (encoded[0].empty()) {
    encoded[0] = Encode(format, base, settings);
    result.error = Compare(base, Decode(format, encoded[0].data(), encoded[0].size(), base.width,
                                        base.height));
  }
  for (size_t level = 1; level < levels.size(); ++level) {
    encoded[level] = Encode(format, levels[level], settings);
  }
  result.format = format;
  result.dds = WriteDds(format, base.width, base.height, encoded);
  return result;
}

}  // namespace texcompress
