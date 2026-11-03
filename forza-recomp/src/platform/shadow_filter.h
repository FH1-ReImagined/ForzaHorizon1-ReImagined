// forza - shadow softness option (src/hooks/shadow_filter.cpp, docs/graphics-detail.md, #380).
// The decisions, without guest memory, so tests/shadow_filter_test.cpp can check them.
#pragma once

#include <cstdint>
#include <string_view>

namespace forza::shadow_filter {

enum class Level { kConsole, kSoft, kSofter };

inline Level ParseLevel(std::string_view value) {
  if (value == "soft") return Level::kSoft;
  if (value == "softer") return Level::kSofter;
  return Level::kConsole;
}

// The shadow apply passes compare the shadow map at four (eight in photo mode) points around
// each pixel, the "FilterTaps" offsets, turned by a per-pixel angle. The game scales them per
// cascade (Colorado's nearest: 2 and 1.4 texels); the levels widen that disc, which widens the
// shadows' soft edge.
inline float TapScale(Level level) {
  switch (level) {
    case Level::kSoft: return 1.5f;
    case Level::kSofter: return 2.0f;
    default: return 1.0f;
  }
}

// Blur kinds of the shadow mask blur (sub_823EFC08, r6): 0 the 2x2 box ("BlurShadowMask", the
// game's choice in play), 1/2 the 4-tap horizontal/vertical pair ("BlurShadowMaskHLQ"/"VLQ",
// photo mode), 3/4 the 6-tap pair ("BlurShadowMaskH"/"V", never chosen). Softer uses the 6-tap
// pair, which smooths the grain of its twice as wide disc: the box becomes the 6-tap horizontal
// pass, a resolve of the mask and the 6-tap vertical pass (about 0.4 ms at 2560x1440). Soft
// keeps the game's blur, so it costs nothing.
constexpr uint32_t kBlurBox = 0, kBlurHorizontalLQ = 1, kBlurVerticalLQ = 2, kBlurHorizontal = 3,
                   kBlurVertical = 4;

struct BlurPlan {
  uint32_t first;   // the kind to draw
  bool then_vertical;  // also resolve the mask and draw kBlurVertical after it
};

inline BlurPlan PlanBlur(uint32_t kind, Level level) {
  if (level != Level::kSofter) return {kind, false};
  switch (kind) {
    case kBlurBox: return {kBlurHorizontal, true};
    case kBlurHorizontalLQ: return {kBlurHorizontal, false};
    case kBlurVerticalLQ: return {kBlurVertical, false};
    default: return {kind, false};
  }
}

}  // namespace forza::shadow_filter
