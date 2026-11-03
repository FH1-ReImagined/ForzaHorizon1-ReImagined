// World ambient occlusion (SDK patch 0350, src/graphics/vulkan/fh1_world_ao.h, #380): the
// setting's levels, where the occlusion is computed, the projection taken from the shadow
// passes' constants, and which pixels keep their own shading.
//   clang++ -std=c++17 -I<sdk>/src/graphics/vulkan tools/test_world_ao.cpp
#include "fh1_world_ao.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace ao = rex::graphics::vulkan::fh1_world_ao;

#define CHECK(x)                                              \
  do {                                                        \
    if (!(x)) {                                               \
      std::fprintf(stderr, "world AO: FAIL: %s\n", #x);       \
      std::exit(1);                                           \
    }                                                         \
  } while (false)

int main() {
  // This is the production control flow around image replacement in BeforeDraw: the queue
  // wait closes the submission, even if the following allocation fails. Texture references
  // belong to the old submission until they are refreshed after a successful resume.
  for (bool closes : {false, true}) {
    for (bool allocated : {false, true}) {
      for (bool resume_ok : {false, true}) {
        bool open = true, refreshed = false;
        std::vector<int> calls;
        const auto status = ao::PrepareResourcesForDraw(
            [&] { calls.push_back(1); open = !closes; return allocated; },
            [&] { return open; },
            [&] { calls.push_back(2); open = resume_ok; return resume_ok; },
            [&] { CHECK(open); calls.push_back(3); refreshed = true; });
        if (closes && !resume_ok) {
          CHECK(status == ao::ResourceStatus::kSubmissionFailed);
          CHECK(calls == std::vector<int>({1, 2}) && !refreshed);
        } else {
          CHECK(open);
          CHECK(status == (allocated ? ao::ResourceStatus::kReady : ao::ResourceStatus::kUnavailable));
          CHECK(calls == (closes ? std::vector<int>({1, 2, 3}) : std::vector<int>({1})));
          CHECK(refreshed == closes);
        }
      }
    }
  }
  // The launcher's and the in-game screen's spellings; anything else is off.
  CHECK(ao::Level("off") == 0);
  CHECK(ao::Level("low") == 1);
  CHECK(ao::Level("high") == 2);
  for (const char* other : {"", "on", "High", "ultra", "2"}) CHECK(ao::Level(other) == 0);
  CHECK(ao::Intensity(2) > ao::Intensity(1));

  // Half resolution at render scale 2 and above, the scene's own at 1.
  auto grid = ao::GridFor(2560, 1440);
  CHECK(grid.downsample == 2 && grid.width == 1280 && grid.height == 720);
  grid = ao::GridFor(3840, 2160);
  CHECK(grid.downsample == 2 && grid.width == 1920 && grid.height == 1080);
  grid = ao::GridFor(1280, 720);
  CHECK(grid.downsample == 1 && grid.width == 1280 && grid.height == 720);
  grid = ao::GridFor(1921, 1081);
  CHECK(grid.width == 961 && grid.height == 541);

  // The free roam spawn's constants (c0.x, c1.y, c25.xy of the shadow passes): reversed depth,
  // near plane 0.3 m, a 16:9 view about 58 degrees high.
  float p[4];
  CHECK(ao::Projection(-1.01477f, -1.80404f, 1.00002f, -0.300005f, p));
  CHECK(std::fabs(p[2] - 0.98544f) < 1e-4f && std::fabs(p[3] - 0.55431f) < 1e-4f);
  CHECK(std::fabs(p[2] / p[3] - 16.0f / 9.0f) < 1e-3f);
  CHECK(std::fabs(ao::ViewZ(p, 1.0f) - 0.3f) < 1e-3f);    // the near plane
  CHECK(std::fabs(ao::ViewZ(p, 0.03f) - 10.0f) < 0.01f);  // 10 m
  CHECK(ao::ViewZ(p, 0.0f) == 0.0f);                      // the sky
  CHECK(ao::ViewZ(p, 0.0015f) > ao::kFadeEnd);            // about 200 m: beyond the fade
  // Missing or broken constants: no occlusion that frame.
  CHECK(!ao::Projection(0.0f, -1.8f, 1.0f, -0.3f, p));
  CHECK(!ao::Projection(-1.0f, -1.8f, 1.0f, 0.0f, p));
  CHECK(!ao::Projection(NAN, -1.8f, 1.0f, -0.3f, p));

  // The pre-pass stencil: the world (0) and the ground around the player's car (128) get the
  // occlusion; the cars (3 x id: the player's 15, traffic 24, 30, 51) and the trees (129) keep
  // their own shading.
  for (uint32_t world : {0u, 128u, 130u, 255u}) CHECK(!ao::KeepsItsShading(world));
  for (uint32_t kept : {1u, 15u, 24u, 30u, 51u, 127u, 129u}) CHECK(ao::KeepsItsShading(kept));

  // The shadow passes the occlusion starts from; the mask's blur is not one.
  CHECK(ao::IsShadowPass(0x93626E75D17576C5ull));
  CHECK(ao::IsShadowPass(0x22DA22B5639EBAE4ull));
  CHECK(!ao::IsShadowPass(0x58CE51CE56247C16ull));  // BlurShadowMask, the 2x2 box
  CHECK(!ao::IsShadowPass(0));

  std::printf("world AO: ok\n");
  return 0;
}
