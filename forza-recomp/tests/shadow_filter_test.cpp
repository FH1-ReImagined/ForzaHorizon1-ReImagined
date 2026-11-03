// world_shadow_filter (src/hooks/shadow_filter.cpp): what each level does to the shadow
// passes (src/platform/shadow_filter.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/shadow_filter_test.cpp
#include "platform/shadow_filter.h"

#include <cstdio>
#include <cstdlib>

namespace s = forza::shadow_filter;

#define CHECK(x)                                                \
  do {                                                          \
    if (!(x)) {                                                 \
      std::fprintf(stderr, "shadow filter: FAIL: %s\n", #x);    \
      std::exit(1);                                             \
    }                                                           \
  } while (false)

int main() {
  CHECK(s::ParseLevel("console") == s::Level::kConsole);
  CHECK(s::ParseLevel("") == s::Level::kConsole);
  CHECK(s::ParseLevel("Soft") == s::Level::kConsole);  // only the launcher's spellings
  CHECK(s::ParseLevel("high") == s::Level::kConsole);
  CHECK(s::ParseLevel("soft") == s::Level::kSoft);
  CHECK(s::ParseLevel("softer") == s::Level::kSofter);

  // Console: the game's taps and every blur kind as the game asks. Soft: the game's blur too.
  CHECK(s::TapScale(s::Level::kConsole) == 1.0f);
  for (auto level : {s::Level::kConsole, s::Level::kSoft}) {
    for (uint32_t kind = 0; kind <= 5; ++kind) {
      const auto plan = s::PlanBlur(kind, level);
      CHECK(plan.first == kind);
      CHECK(!plan.then_vertical);
    }
  }

  // The softer levels widen the disc, more for softer.
  CHECK(s::TapScale(s::Level::kSoft) == 1.5f);
  CHECK(s::TapScale(s::Level::kSofter) == 2.0f);
  CHECK(s::TapScale(s::Level::kSofter) > s::TapScale(s::Level::kSoft));

  {
    const auto level = s::Level::kSofter;
    // The 2x2 box in play becomes the 6-tap horizontal pass, then (after a resolve) vertical.
    auto plan = s::PlanBlur(s::kBlurBox, level);
    CHECK(plan.first == s::kBlurHorizontal);
    CHECK(plan.then_vertical);
    // Photo mode's 4-tap pair becomes the 6-tap pair, pass for pass (the game resolves between).
    plan = s::PlanBlur(s::kBlurHorizontalLQ, level);
    CHECK(plan.first == s::kBlurHorizontal && !plan.then_vertical);
    plan = s::PlanBlur(s::kBlurVerticalLQ, level);
    CHECK(plan.first == s::kBlurVertical && !plan.then_vertical);
    // The 6-tap pair itself, and anything unknown, stays.
    for (uint32_t kind : {s::kBlurHorizontal, s::kBlurVertical, 5u}) {
      plan = s::PlanBlur(kind, level);
      CHECK(plan.first == kind && !plan.then_vertical);
    }
  }
  std::printf("shadow filter: ok\n");
  return 0;
}
