// forza - shadow softness option (docs/graphics-detail.md, #380).
//
// The world's sun shadows are not filtered where the materials read them: the renderer first
// draws a screen-space shadow mask ("ShadowMask", read by the world shaders as ShadowMaskSamp).
// For each cascade, ApplyShadows (sub_82C3AF68, TU4 sub_82CFB8D8) picks a technique of the
// engine effect by its r10 (1-3 PCF 2x2-4x4, 4 the low-quality disc, 8 the high-quality one)
// and draws a full-screen pass that compares the shadow map at the "FilterTaps" offsets (its r9,
// float4[4]: up to eight 2D offsets in shadow map UV) turned by a per-pixel angle. In play every
// cascade uses the 4-tap disc ("ApplyShadowsLQ", "...WithFading_Depth" for the last); photo mode
// uses the 8-tap one for the first. Then the mask is blurred once (sub_823EFC08, TU4
// sub_8241FD00, kind r6: 0 the 2x2 box in play) and resolved for the world pass.
// world_shadow_filter widens the disc by scaling the taps for the call (and puts them back
// after); softer also blurs with the effect's 6-tap horizontal and vertical passes instead of
// the box.
// Console leaves both calls exactly as they are. docs/graphics-detail.md has what else was
// tried (the game's PCF and 8-tap techniques) and why they are not used.
// tu_address_map: TU4 addresses

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/shadow_filter.h"
#include "thumbnail_sync.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string_view>

REXCVAR_DEFINE_STRING(world_shadow_filter, "console", "Display",
                      "Soft edges of the world's shadows: console (the game's), soft (a 1.5x "
                      "wider filter), softer (2x, and a wider blur)");

#if FORZA_TU4
#define ApplyShadows sub_82CFB8D8
#define OriginalApplyShadows __imp__sub_82CFB8D8
#define BlurShadowMask sub_8241FD00
#define OriginalBlurShadowMask __imp__sub_8241FD00
#define ResolveShadowMask __imp__sub_824933E0
#else
#define ApplyShadows sub_82C3AF68
#define OriginalApplyShadows __imp__sub_82C3AF68
#define BlurShadowMask sub_823EFC08
#define OriginalBlurShadowMask __imp__sub_823EFC08
#define ResolveShadowMask __imp__sub_82463AD8
#endif
extern "C" REX_FUNC(OriginalApplyShadows);
extern "C" REX_FUNC(OriginalBlurShadowMask);
extern "C" REX_FUNC(ResolveShadowMask);

namespace {
namespace sf = forza::shadow_filter;

// The shadow renderer (the caller's r29, kept across both calls): +8472 is the mask's render
// target, which the blur draws into (its r5) and ResolveShadowMask resolves.
constexpr uint32_t kMaskTarget = 8472;
constexpr int kFilterTapWords = 16;  // FilterTaps: float4[4]

std::atomic<int> level{0};

sf::Level CurrentLevel() {
  static std::once_flag once;
  std::call_once(once, [] {
    level = int(sf::ParseLevel(REXCVAR_GET(world_shadow_filter)));
    // The in-game Graphics screen changes it live.
    rex::cvar::RegisterChangeCallback("world_shadow_filter",
                                      [](std::string_view, std::string_view value) {
                                        level = int(sf::ParseLevel(value));
                                      });
  });
  const auto current = sf::Level(level.load(std::memory_order_relaxed));
  static std::atomic<int> logged{-1};
  if (logged.exchange(int(current)) != int(current)) {
    REXLOG_INFO("world_shadow_filter: {} (filter taps x{})",
                current == sf::Level::kSofter ? "softer"
                : current == sf::Level::kSoft ? "soft"
                                              : "console",
                sf::TapScale(current));
  }
  return current;
}

uint32_t Load32(uint8_t* base, uint32_t address) {
  const auto* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
void Store32(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = forza::GuestPtr(base, address);
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
}
}  // namespace

extern "C" REX_FUNC(ApplyShadows) {
  // Technique 8, the high-quality disc, is photo mode's (#167, src/hooks/thumbnail_sync.cpp).
  if (ctx.r10.u32 == 8) forza::thumbnail_sync::HoldForPhotoMode();
  const float scale = sf::TapScale(CurrentLevel());
  const uint32_t taps = ctx.r9.u32;
  if (scale == 1.0f || !taps) {
    OriginalApplyShadows(ctx, base);
    return;
  }
  // The caller fills the taps on its stack for each cascade; the effect copies them into the
  // pass's constants during the call. Put the game's values back after it.
  uint32_t saved[kFilterTapWords];
  for (int i = 0; i < kFilterTapWords; ++i) {
    saved[i] = Load32(base, taps + 4 * i);
    float f;
    std::memcpy(&f, &saved[i], 4);
    f *= scale;
    uint32_t v;
    std::memcpy(&v, &f, 4);
    Store32(base, taps + 4 * i, v);
  }
  OriginalApplyShadows(ctx, base);
  for (int i = 0; i < kFilterTapWords; ++i) Store32(base, taps + 4 * i, saved[i]);
}

extern "C" REX_FUNC(BlurShadowMask) {
  const uint32_t kind = ctx.r6.u32;
  const sf::BlurPlan plan = sf::PlanBlur(kind, CurrentLevel());
  // Only the shadow renderer's own mask: its r29 is the renderer, r5 the mask target.
  const uint32_t renderer = ctx.r29.u32;
  if ((plan.first == kind && !plan.then_vertical) || !renderer ||
      Load32(base, renderer + kMaskTarget) != ctx.r5.u32) {
    OriginalBlurShadowMask(ctx, base);
    return;
  }
  const uint64_t r3 = ctx.r3.u64, r4 = ctx.r4.u64, r5 = ctx.r5.u64;
  ctx.r6.u64 = plan.first;
  OriginalBlurShadowMask(ctx, base);
  if (!plan.then_vertical) return;
  // As the game does between its own horizontal and vertical passes.
  ctx.r3.u64 = renderer;
  ResolveShadowMask(ctx, base);
  ctx.r3.u64 = r3;
  ctx.r4.u64 = r4;
  ctx.r5.u64 = r5;
  ctx.r6.u64 = sf::kBlurVertical;
  OriginalBlurShadowMask(ctx, base);
}
