// world_shadow_detail / world_texture_detail (src/hooks/world_detail.cpp): the track settings
// values each level gives (src/platform/world_detail.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/world_detail_test.cpp
#include "platform/world_detail.h"

#include <cstdio>
#include <cstdlib>

namespace w = forza::world_detail;

#define CHECK(x)                                                \
  do {                                                          \
    if (!(x)) {                                                 \
      std::fprintf(stderr, "world detail: FAIL: %s\n", #x);     \
      std::exit(1);                                             \
    }                                                           \
  } while (false)

int main() {
  CHECK(w::ParseLevel("console") == w::Level::kConsole);
  CHECK(w::ParseLevel("") == w::Level::kConsole);
  CHECK(w::ParseLevel("Ultra") == w::Level::kConsole);  // only the launcher's spellings
  CHECK(w::ParseLevel("high") == w::Level::kHigh);
  CHECK(w::ParseLevel("ultra") == w::Level::kUltra);

  // Console keeps the track's own values exactly.
  CHECK(w::ShadowCasterDistance(60.0f, w::Level::kConsole) == 60.0f);
  CHECK(w::MipLodBias(0.0f, w::Level::kConsole) == 0.0f);
  CHECK(w::MipLodBias(0.25f, w::Level::kConsole) == 0.25f);

  // Colorado: 60 m -> 180 m / 600 m; the texture bias 0 -> -0.5 / -1.
  CHECK(w::ShadowCasterDistance(60.0f, w::Level::kHigh) == 180.0f);
  CHECK(w::ShadowCasterDistance(60.0f, w::Level::kUltra) == 600.0f);
  CHECK(w::MipLodBias(0.0f, w::Level::kHigh) == -0.5f);
  CHECK(w::MipLodBias(0.0f, w::Level::kUltra) == -1.0f);
  // A track with its own bias keeps it as the starting point.
  CHECK(w::MipLodBias(0.5f, w::Level::kUltra) == -0.5f);

  // A distance of 0 or less turns the game's size test off (everything casts): left alone.
  CHECK(w::ShadowCasterDistance(0.0f, w::Level::kUltra) == 0.0f);
  CHECK(w::ShadowCasterDistance(-1.0f, w::Level::kHigh) == -1.0f);

  // Higher levels never shorten the range or blur textures.
  for (float d : {1.0f, 60.0f, 350.0f}) {
    CHECK(w::ShadowCasterDistance(d, w::Level::kHigh) >= d);
    CHECK(w::ShadowCasterDistance(d, w::Level::kUltra) >= w::ShadowCasterDistance(d, w::Level::kHigh));
  }
  CHECK(w::TextureLodBias(w::Level::kUltra) <= w::TextureLodBias(w::Level::kHigh));
  CHECK(w::TextureLodBias(w::Level::kHigh) <= w::TextureLodBias(w::Level::kConsole));
  // The VR cockpit's slower eye adaptation: the track's rate times the factor; 0 or less keeps it.
  CHECK(w::ExposureAdaptation(0.075f, 0.02f) > 0.0014f && w::ExposureAdaptation(0.075f, 0.02f) < 0.0016f);
  CHECK(w::ExposureAdaptation(0.075f, 1.0f) == 0.075f);
  CHECK(w::ExposureAdaptation(0.075f, 0.0f) == 0.075f);
  CHECK(w::kToneMapDelay == 416);
  std::printf("world detail: ok\n");
  return 0;
}
