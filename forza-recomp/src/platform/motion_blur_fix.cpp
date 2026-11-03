// forza - the speed blur's zero-velocity rectangle around the player's car
// (docs/rendering.md, "Speed blur: sharp rectangle around the car").
//
// The velocity pass (technique PPMotionBlurDepthHorizonVel) reads the resolved depth and
// stencil and picks a reprojection matrix by the stencil value: stencil / 3 is an object
// (c[32 + 4 * id], the player's car is 15 -> 5, other cars 3..24), and a stencil at or
// above a threshold means "no object, reproject as world". Before the scene, the game
// marks a ground rectangle around the player's car with stencil 128, which is meant to be
// world. The threshold is a pixel-shader literal, c253.w = 0x3F008100 (128.0019 / 255),
// just above what the host's UNORM8 fetch returns for 128 (exactly 128 / 255): on the
// host 128 counts as object 43, whose constants happen to be an identity matrix, so the
// rectangle gets zero velocity and stays sharp while everything around it blurs. The
// literal suggests the console's fetch rounds 8-bit values up by a few ulps; whatever the
// cause, 128 must land on the world side. The literal is set to 0.5 (127.5 / 255), between
// 127 and 128, in the executable's image before the game runs.
//
// FORZA_BLUR_THRESHOLD=0 leaves the image alone (for comparisons).
// Setting motion_blur=false zeroes the velocity scale (c252.y = 0.0), causing
// the velocity pass to output neutral center velocity everywhere and the
// combine pass (BloomTonemap_MotionBlur_Vignette) to sample with zero displacement.

#include "motion_blur.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string_view>

REXCVAR_DEFINE_BOOL(motion_blur, true, "Display",
                    "Enable motion blur (speed blur while driving); off zeroes "
                    "velocity while preserving tone mapping, bloom and vignette");

namespace forza {

namespace {
uint32_t blur_scale_addr = 0;

void ApplyMotionBlurScale(bool enabled) {
  if (!blur_scale_addr) return;
  auto* memory = rex::system::kernel_state()->memory();
  if (!memory) return;
  uint8_t* base = memory->virtual_membase();
  constexpr uint32_t kPage = 0x1000;
  auto* h = memory->LookupHeap(blur_scale_addr);
  if (!h) return;
  uint32_t old_protect = 0;
  h->QueryProtect(blur_scale_addr, &old_protect);
  h->Protect(blur_scale_addr & ~(kPage - 1), kPage,
             rex::memory::kMemoryProtectRead | rex::memory::kMemoryProtectWrite);
  const uint32_t val = enabled ? 0x40A00000 : 0x00000000;
  const uint8_t bytes[4] = {
      uint8_t(val >> 24), uint8_t(val >> 16), uint8_t(val >> 8), uint8_t(val)};
  std::memcpy(base + blur_scale_addr, bytes, 4);
  h->Protect(blur_scale_addr & ~(kPage - 1), kPage, old_protect);
  REXLOG_INFO("motion blur: {} (velocity scale set to {})",
              enabled ? "enabled" : "disabled", enabled ? 5.0f : 0.0f);
}
}  // namespace

void SetMotionBlurEnabled(bool enabled) {
  rex::cvar::SetFlagByName("motion_blur", enabled ? "true" : "false");
}

bool MotionBlurEnabled() {
  return REXCVAR_GET(motion_blur);
}

void PatchMotionBlurStencilThreshold() {
  auto* memory = rex::system::kernel_state()->memory();
  uint8_t* base = memory->virtual_membase();
  // The literal constants c252-c255 of the velocity pass's pixel shader, big-endian, as
  // they sit in the image right before its microcode (the same in the disc's executable
  // and Title Update 4's).
  static const uint32_t kLiterals[16] = {
      0xBF800000, 0x40A00000, 0x3EFEFEFF, 0x00000000,  // c252, .y = blur scale (5.0)
      0x35800000, 0x42AA0000, 0x40800000, 0x3F008100,  // c253, .w = the threshold
      0x3F800000, 0x3F000000, 0xC1700000, 0x417F0000,  // c254
      0x437F0000, 0x477F0000, 0x49800000, 0x3DCCCCCD,  // c255
  };
  uint8_t pattern[sizeof(kLiterals)];
  for (size_t i = 0; i < 16; ++i) {
    uint32_t v = kLiterals[i];
    pattern[i * 4 + 0] = uint8_t(v >> 24);
    pattern[i * 4 + 1] = uint8_t(v >> 16);
    pattern[i * 4 + 2] = uint8_t(v >> 8);
    pattern[i * 4 + 3] = uint8_t(v);
  }
  constexpr uint32_t kImageBase = 0x82000000;
  constexpr uint32_t kImageEnd = 0x84000000;
  constexpr uint32_t kPage = 0x1000;
  int patched = 0;
  // Scan runs of readable pages (literals are 4-byte aligned, so runs need not overlap
  // much; a 64-byte block straddling unreadable pages cannot occur).
  uint32_t run_start = 0;
  for (uint32_t page = kImageBase; page <= kImageEnd; page += kPage) {
    uint32_t protect = 0;
    auto* heap = page < kImageEnd ? memory->LookupHeap(page) : nullptr;
    bool readable = heap && heap->QueryProtect(page, &protect) &&
                    (protect & rex::memory::kMemoryProtectRead);
    if (readable) {
      if (!run_start) run_start = page;
      continue;
    }
    if (!run_start) continue;
    const uint8_t* begin = base + run_start;
    const uint8_t* end = base + page;
    for (const uint8_t* p = begin; p + sizeof(pattern) <= end; p += 4) {
      if (std::memcmp(p, pattern, sizeof(pattern)) != 0) continue;
      uint32_t addr = run_start + uint32_t(p - begin) + 7 * 4;
      blur_scale_addr = run_start + uint32_t(p - begin) + 1 * 4;

      const char* env = std::getenv("FORZA_BLUR_THRESHOLD");
      if (!env || std::strcmp(env, "0") != 0) {
        auto* h = memory->LookupHeap(addr);
        uint32_t old_protect = 0;
        h->QueryProtect(addr, &old_protect);
        h->Protect(addr & ~(kPage - 1), kPage,
                   rex::memory::kMemoryProtectRead | rex::memory::kMemoryProtectWrite);
        const uint8_t half[4] = {0x3F, 0x00, 0x00, 0x00};  // 0.5
        std::memcpy(base + addr, half, 4);
        h->Protect(addr & ~(kPage - 1), kPage, old_protect);
        REXLOG_INFO("motion blur: stencil threshold at {:08X} set to 0.5", addr);
      } else {
        REXLOG_INFO("motion blur: stencil threshold left as is (FORZA_BLUR_THRESHOLD=0)");
      }

      if (!REXCVAR_GET(motion_blur)) {
        ApplyMotionBlurScale(false);
      }
      rex::cvar::RegisterChangeCallback("motion_blur", [](std::string_view, std::string_view value) {
        const bool enabled = value != "false" && value != "0";
        ApplyMotionBlurScale(enabled);
      });
      ++patched;
    }
    run_start = 0;
  }
  if (!patched) {
    REXLOG_WARN("motion blur: velocity shader literals not found; the rectangle around the "
                "car stays sharp");
  }
}

}  // namespace forza
