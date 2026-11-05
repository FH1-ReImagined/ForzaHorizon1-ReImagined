// scene_resolution (src/hooks/scene_resolution.cpp): the scene's size and MSAA, the scenario it
// is drawn with, the screen textures that follow it and the tonemap's rectangle
// (src/platform/scene_resolution.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/scene_resolution_test.cpp
#include "platform/scene_resolution.h"

#include <cstdio>
#include <cstdlib>

namespace r = forza::scene_resolution;

#define CHECK(x)                                                   \
  do {                                                             \
    if (!(x)) {                                                    \
      std::fprintf(stderr, "scene resolution: FAIL: %s\n", #x);    \
      std::exit(1);                                                \
    }                                                              \
  } while (false)

int main() {
  // The setting.
  CHECK(r::ParseHeight("540") == 540);
  CHECK(r::ParseHeight("360") == 720);  // not offered (docs/perf.md)
  CHECK(r::ParseHeight("720") == 720);
  CHECK(r::ParseHeight("") == 720);
  CHECK(r::ParseHeight("480") == 720);
  CHECK(!r::Scaled(720));
  CHECK(r::Scaled(540));
  CHECK(r::SceneSize(540).width == 960 && r::SceneSize(540).height == 540);
  CHECK(r::SceneSize(360).width == 640 && r::SceneSize(360).height == 360);

  // EDRAM: the game's 720p scene needs three tiles at 4x and two at 2x, one pass without MSAA.
  CHECK(r::FitsEdram({1280, 720}, 1));
  CHECK(!r::FitsEdram({1280, 720}, 2));
  CHECK(!r::FitsEdram({1280, 720}, 4));
  // Its unused "Largest 2xMSAA" scenario is the biggest 2x surface that fits.
  CHECK(r::FitsEdram({1120, 584}, 2));
  CHECK(r::FitsEdram({960, 540}, 2) && !r::FitsEdram({960, 540}, 4));
  CHECK(r::FitsEdram({640, 360}, 4));

  // MSAA in one pass: the most that fits for "game", capped by 2x / off.
  using W = r::MsaaWish;
  CHECK(r::SceneMsaa({960, 540}, W::kGame) == r::k2x);
  CHECK(r::SceneMsaa({960, 540}, W::k2x) == r::k2x);
  CHECK(r::SceneMsaa({960, 540}, W::kOff) == r::k1x);
  CHECK(r::SceneMsaa({640, 360}, W::kGame) == r::k4x);
  CHECK(r::SceneMsaa({640, 360}, W::k2x) == r::k2x);
  CHECK(r::SceneMsaa({640, 360}, W::kOff) == r::k1x);

  // Scenarios: 4x tiled, 2x tiled and one pass become the smaller scene; split screen stays.
  CHECK(r::SceneScenario(0) == r::kSceneScenario);
  CHECK(r::SceneScenario(1) == r::kSceneScenario);
  CHECK(r::SceneScenario(2) == r::kSceneScenario);
  CHECK(r::SceneScenario(3) == 3);
  CHECK(r::SceneScenario(4) == 4);

  // Screen textures: the front buffers and the map stay at 720p.
  CHECK(!r::ScreenTextureFollowsScene("FrontBuffer_0"));
  CHECK(!r::ScreenTextureFollowsScene("FrontBuffer_1"));
  CHECK(!r::ScreenTextureFollowsScene("MapRender"));
  CHECK(r::ScreenTextureFollowsScene("WorldRender"));
  CHECK(r::ScreenTextureFollowsScene("WorldDepth"));
  CHECK(r::ScreenTextureFollowsScene("Shared"));
  // Sizes as the game computes them for 1280x720 (1/4: 320x192), at the scene's size.
  CHECK(r::TextureSide(1, 960) == 960 && r::TextureSide(1, 540) == 540);
  CHECK(r::TextureSide(4, 1280) == 320 && r::TextureSide(4, 720) == 192);
  CHECK(r::TextureSide(4, 960) == 256 && r::TextureSide(4, 540) == 160);
  CHECK(r::TextureSide(64, 360) == 32);

  // The tonemap's halves of the 720p screen, from a 960x540 scene texture: the whole texture.
  r::Rect top{0, 0, 1280, 360};
  CHECK(r::ScaleQuadRect(top, 960, 540, {960, 540}));
  CHECK(top.left == 0 && top.top == 0 && top.right == 960 && top.bottom == 270);
  r::Rect bottom{0, 360, 1280, 720};
  CHECK(r::ScaleQuadRect(bottom, 960, 540, {960, 540}));
  CHECK(bottom.top == 270 && bottom.bottom == 540);
  r::Rect full{0, 0, 1280, 720};
  CHECK(r::ScaleQuadRect(full, 640, 360, {640, 360}));
  CHECK(full.right == 640 && full.bottom == 360);
  // Passes inside the scene (a rectangle within the texture), other textures: unchanged.
  r::Rect inner{0, 0, 960, 540};
  CHECK(!r::ScaleQuadRect(inner, 960, 540, {960, 540}));
  CHECK(inner.right == 960 && inner.bottom == 540);
  r::Rect other{0, 0, 1280, 720};
  CHECK(!r::ScaleQuadRect(other, 1280, 720, {960, 540}));
  CHECK(!r::ScaleQuadRect(other, 256, 160, {960, 540}));
  CHECK(other.right == 1280);

  // The sun's shadow mask (#682): its 1280x720 viewport and screen size are the scene's.
  r::Viewport mask{0, 0, 1280, 720};
  CHECK(r::ScaleMaskViewport(mask, {960, 540}));
  CHECK(mask.x == 0 && mask.y == 0 && mask.width == 960 && mask.height == 540);
  r::Viewport cascade{0, 0, 1024, 1024};  // a shadow map, a mirror, a corner: unchanged
  CHECK(!r::ScaleMaskViewport(cascade, {960, 540}));
  CHECK(cascade.width == 1024 && cascade.height == 1024);
  r::Viewport offset{0, 360, 1280, 720};
  CHECK(!r::ScaleMaskViewport(offset, {960, 540}));
  r::Viewport native{0, 0, 1280, 720};
  CHECK(!r::ScaleMaskViewport(native, {1280, 720}));
  CHECK(native.width == 1280);
  r::Size taps{1280, 720};
  CHECK(r::ScaleMaskScreenSize(taps, {960, 540}));
  CHECK(taps.width == 960 && taps.height == 540);
  r::Size half{640, 360};
  CHECK(!r::ScaleMaskScreenSize(half, {960, 540}));
  CHECK(half.width == 640);

  std::puts("scene resolution: all checks passed");
  return 0;
}
