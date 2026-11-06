// forza - the 3D scene below 720p (scene_resolution, #347, src/hooks/scene_resolution.cpp).
// The decisions, without guest memory, so tests/scene_resolution_test.cpp can check them.
#pragma once

#include <cstdint>
#include <string_view>

namespace forza::scene_resolution {

constexpr uint32_t kNativeWidth = 1280;
constexpr uint32_t kNativeHeight = 720;

struct Size {
  uint32_t width;
  uint32_t height;
};

// The scene's height in lines: 720 (the game's own: nothing changes) or 540. Anything else is
// 720. 360 (640x360) is not offered: the scene came out magnified with blown-out rectangles and
// the game hung after about 10 s of driving (docs/perf.md).
inline uint32_t ParseHeight(std::string_view value) {
  if (value == "540") return 540;
  return kNativeHeight;
}

inline bool Scaled(uint32_t height) { return height < kNativeHeight; }

// 16:9 at that height: 960x540, 640x360.
inline Size SceneSize(uint32_t height) { return {height * kNativeWidth / kNativeHeight, height}; }

// The render scenarios (TilingScenario, src/platform/scene_tiling.h) are entries of a table in
// the executable, 0x4D0 bytes each: +0x04 width, +0x08 height, +0x0C MSAA (0 1x, 1 2x, 2 4x),
// +0x10 tile count, +0x20 the tiles as (left, top, right, bottom), 16 bytes each. The game sizes
// the scene's EDRAM surfaces, its resolves and the scene views from the entry.
constexpr uint32_t kScenarioSize = 0x4D0;
constexpr uint32_t kScenarioWidth = 0x04;
constexpr uint32_t kScenarioHeight = 0x08;
constexpr uint32_t kScenarioMsaa = 0x0C;
constexpr uint32_t kScenarioTileCount = 0x10;
constexpr uint32_t kScenarioTiles = 0x20;
// The smaller scene uses entry 3, "Largest 2xMSAA No Split" (one 1120x584 tile of the 720p
// scene), which no render scenario of the game selects: it becomes one pass at the scene size.
constexpr uint32_t kSceneScenario = 3;
enum Msaa : uint32_t { k1x = 0, k2x = 1, k4x = 2 };

// The scene is drawn in one pass, so its colour and depth surfaces (4 bytes a sample each) have
// to fit the 10 MB of EDRAM: 2048 tiles of 80x16 samples. Samples per pixel: 1, 2, 4.
inline bool FitsEdram(Size s, uint32_t samples) {
  const uint32_t sample_w = s.width * (samples == 4 ? 2 : 1);
  const uint32_t sample_h = s.height * (samples >= 2 ? 2 : 1);
  const uint32_t tiles = ((sample_w + 79) / 80) * ((sample_h + 15) / 16);
  return 2 * tiles <= 2048;
}

// scene_msaa (src/platform/scene_tiling.h): "game" takes the most the scene fits in one pass
// (2x at 540, 4x at 360, as the game's own 4x), "2x" at most 2x, "off" none.
enum class MsaaWish { kGame, k2x, kOff };
inline uint32_t SceneMsaa(Size s, MsaaWish wish) {
  if (wish == MsaaWish::kOff) return k1x;
  if (wish == MsaaWish::kGame && FitsEdram(s, 4)) return k4x;
  if (FitsEdram(s, 2)) return k2x;
  return k1x;
}

// Which render scenario draws the scene: the full-screen ones (4x in three tiles, 2x in two,
// one pass without MSAA) become the smaller one-pass scene; split screen keeps its own.
inline uint32_t SceneScenario(uint32_t scenario) {
  return scenario <= 2 ? kSceneScenario : scenario;
}

// The renderer's textures (sub_8257C220 copies a table of 41 entries, 0x2C bytes each, from the
// executable, computes the sizes and allocates them): +0x00 a byte, set when the size is the
// 1280x720 screen divided by +0x04 / +0x08 (rounded up to 32 when divided), +0x28 the name.
// The scene's (WorldRender, WorldDepth, ScratchBuffer, ShadowMask, Bloom, Shared) follow the
// scene; the front buffers the HUD is drawn on and the map stay at 720p.
constexpr uint32_t kTextureCount = 41;
constexpr uint32_t kTextureSize = 0x2C;
constexpr uint32_t kTextureWidth = 0x04;
constexpr uint32_t kTextureHeight = 0x08;
constexpr uint32_t kTextureName = 0x28;
// The renderer keeps a 0x34-byte slot per entry (sub_8257C220's r3 + 0x1A0: the table entry, its
// memory size at +0x2C, its address at +0x30), then the D3D textures, 0x34 bytes each in table
// order: the passes draw into and resolve to those.
constexpr uint32_t kRendererTextures = 0x1A0;
constexpr uint32_t kRendererTextureSize = 0x34;
constexpr uint32_t kTextureObjects = kRendererTextures + kTextureCount * kRendererTextureSize;
static_assert(kTextureObjects == 0x9F4);
// Several entries share memory: an entry whose +0x1C is not 0x29 lies in another one's memory
// (at its start or after it), and the renderer lends parts of the block to others with 720p
// sizes written in the code (sub_8257C428). The textures that follow the scene therefore keep
// their 720p memory: only their size changes, not where they and everything after them lie.
// With the 540p sizes the block ended 6.5 MB earlier, and a full-screen filter's 1280x720 resolve
// into the Shared texture in WorldDepth wrote into the stream buffers of the file heap after it
// (a race load then crashed in their allocator, #347).
inline bool ScreenTextureFollowsScene(std::string_view name) {
  return name != "FrontBuffer_0" && name != "FrontBuffer_1" && name != "MapRender";
}
inline uint32_t TextureSide(uint32_t divisor, uint32_t scene_side) {
  if (divisor <= 1) return scene_side;
  return (scene_side / divisor + 31) & ~31u;
}

// The post-processing quad (sub_823E5628) maps a rectangle in screen pixels to texture
// coordinates of a source texture, texel for texel: rect / texture size. The passes that draw
// a scene texture onto the 720p front buffer (the tonemap) name the rectangle in 720p pixels;
// for a scene-sized source the rectangle is scaled to the scene, so the scene fills the screen.
struct Rect {
  int32_t left, top, right, bottom;
};
inline bool ScaleQuadRect(Rect& r, uint32_t tex_width, uint32_t tex_height, Size scene) {
  if (tex_width != scene.width || tex_height != scene.height) return false;
  if (r.right != int32_t(kNativeWidth) && r.bottom != int32_t(kNativeHeight)) return false;
  auto x = [&](int32_t v) { return int32_t(int64_t(v) * scene.width / kNativeWidth); };
  auto y = [&](int32_t v) { return int32_t(int64_t(v) * scene.height / kNativeHeight); };
  r = {x(r.left), y(r.top), x(r.right), y(r.bottom)};
  return true;
}

// The sun's shadow mask (src/hooks/scene_resolution.cpp, #682) is drawn over the scene's depth
// with a viewport and texel offsets for a 1280x720 screen written in the game's code. While
// the scene is smaller, a 1280x720 viewport at 0,0 is the scene's; anything else is left alone.
struct Viewport {
  uint32_t x, y, width, height;
};
inline bool ScaleMaskViewport(Viewport& v, Size scene) {
  if (v.x != 0 || v.y != 0 || v.width != kNativeWidth || v.height != kNativeHeight ||
      !Scaled(scene.height)) {
    return false;
  }
  v.width = scene.width;
  v.height = scene.height;
  return true;
}
inline bool ScaleMaskScreenSize(Size& s, Size scene) {
  if (s.width != kNativeWidth || s.height != kNativeHeight || !Scaled(scene.height)) return false;
  s = scene;
  return true;
}

}  // namespace forza::scene_resolution
