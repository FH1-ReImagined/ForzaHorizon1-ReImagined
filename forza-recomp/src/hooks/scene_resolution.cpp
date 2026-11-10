// forza - the 3D scene below 720p for GPU-limited PCs (scene_resolution, #347, docs/perf.md
// "Scene resolution below 720p").
//
// The game draws its scene at the size of its render scenario (TilingScenario, an entry of a
// table in the executable: src/platform/scene_resolution.h) and its post-processing at the size
// of a table of screen textures (1280x720 or fractions of it). The HUD and menus are drawn on
// the 720p front buffers afterwards. scene_resolution = 540 draws the scene, its
// post-processing (bloom, depth, shadow mask, FXAA) and the tonemap's input at 960x540; the
// tonemap scales it onto the 720p front buffer and the HUD stays at 720p. The
// SDK's upscaling filter (FSR 1) then takes the 720p picture to the window as before.
//
// - At start (PatchSceneResolution, after the executable is loaded): render scenario 3
//   ("Largest 2xMSAA No Split", which no scenario of the game uses) becomes one pass at the
//   scene size with the MSAA that fits EDRAM.
// - The scene's screen textures get the scene size (sub_8257C220, TU4 sub_825F2100) and keep
//   their 720p memory (sub_82A40F28, TU4 sub_82AFE678): the block they share with each other
//   and with other users lies as at 720p. A 1280x720 post-processing pass into one of them
//   (sub_824426B8, TU4 sub_824728D8) is drawn at the scene size.
// - The full-screen render scenarios become scenario 3 (src/hooks/scene_tiling.cpp).
// - The world view (sub_82444E60, TU4 sub_824744D0) draws with the view's viewport, 1280x720;
//   it is the scene size while that view is drawn.
// - The post-processing quad (sub_823E5628, TU4 sub_82415620) maps screen pixels to texels;
//   the tonemap names 720p rectangles for its scene-sized source, which are scaled.
// - The scenario's full-size 2:10:10:10 surface (sub_82C3F2E8, TU4 sub_82CFFC58), where the
//   render target manager's full-screen filters draw before resolving into the 720p front
//   buffers, stays 1280x720.
// - The D3D device is made with the first scenario's size as its back buffer
//   (sub_82C3F058, TU4 sub_82CFF9C8); it stays 1280x720.
// - The sun's shadow mask (sub_824532F0, TU4 sub_824829D8) is drawn with a 1280x720 viewport
//   and blur and quad offsets for 1280x720 written in its code; while it runs they are the scene
//   size, or the mask came out of a 1280x720 drawing over the scene's depth and was resolved
//   cropped (#682: the car's shadow a long streak up and to the left).
// Not with the SDK's scene reconstruction (fh1_upscaler: FSR 3, DLSS, SMAA), its separate scene
// AA or native ultrawide, which find the scene by its 720p targets: the scene stays 720p.
// tu_address_map: TU4 addresses

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/scene_resolution.h"
#include "../platform/scene_tiling.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>

REXCVAR_DEFINE_STRING(scene_resolution, "720", "Display",
                      "Height of the 3D scene: 720 (the game's own) or 540. Below 720 the "
                      "scene and its effects are drawn smaller and scaled to 720p; the HUD and "
                      "menus stay at 720p. Experimental (#347)");
REXCVAR_DECLARE(std::string, scene_msaa);       // src/hooks/scene_tiling.cpp
REXCVAR_DECLARE(bool, forza_ultrawide);         // src/platform/ultrawide.cpp

#if FORZA_TU4
#define WorldViewRender sub_824744D0
#define OriginalWorldViewRender __imp__sub_824744D0
#define PostQuad sub_82415620
#define OriginalPostQuad __imp__sub_82415620
#define CreateDevice sub_82CFF9C8
#define OriginalCreateDevice __imp__sub_82CFF9C8
#define ScenarioSurface sub_82CFFC58
#define OriginalScenarioSurface __imp__sub_82CFFC58
#define ScreenTextures sub_825F2100
#define OriginalScreenTextures __imp__sub_825F2100
#define PostPass sub_824728D8
#define OriginalPostPass __imp__sub_824728D8
#define TextureHeader sub_82AFE678
#define OriginalTextureHeader __imp__sub_82AFE678
#define ShadowMask sub_824829D8
#define OriginalShadowMask __imp__sub_824829D8
#define SetViewport sub_82440BE0
#define OriginalSetViewport __imp__sub_82440BE0
#define MaskVolumeQuad sub_82EA04E8
#define OriginalMaskVolumeQuad __imp__sub_82EA04E8
#define BlurTaps sub_82412BD8
#define OriginalBlurTaps __imp__sub_82412BD8
constexpr uint32_t kScenarioTable = 0x833901F8;
constexpr uint32_t kTextureTable = 0x8330C830;
#else
#define WorldViewRender sub_82444E60
#define OriginalWorldViewRender __imp__sub_82444E60
#define PostQuad sub_823E5628
#define OriginalPostQuad __imp__sub_823E5628
#define CreateDevice sub_82C3F058
#define OriginalCreateDevice __imp__sub_82C3F058
#define ScenarioSurface sub_82C3F2E8
#define OriginalScenarioSurface __imp__sub_82C3F2E8
#define ScreenTextures sub_8257C220
#define OriginalScreenTextures __imp__sub_8257C220
#define PostPass sub_824426B8
#define OriginalPostPass __imp__sub_824426B8
#define TextureHeader sub_82A40F28
#define OriginalTextureHeader __imp__sub_82A40F28
#define ShadowMask sub_824532F0
#define OriginalShadowMask __imp__sub_824532F0
#define SetViewport sub_82410C30
#define OriginalSetViewport __imp__sub_82410C30
#define MaskVolumeQuad sub_82DDDEE0
#define OriginalMaskVolumeQuad __imp__sub_82DDDEE0
#define BlurTaps sub_823E2C48
#define OriginalBlurTaps __imp__sub_823E2C48
constexpr uint32_t kScenarioTable = 0x832A4860;
constexpr uint32_t kTextureTable = 0x832295B0;
#endif
extern "C" REX_FUNC(OriginalWorldViewRender);
extern "C" REX_FUNC(OriginalPostQuad);
extern "C" REX_FUNC(OriginalCreateDevice);
extern "C" REX_FUNC(OriginalScenarioSurface);
extern "C" REX_FUNC(OriginalScreenTextures);
extern "C" REX_FUNC(OriginalPostPass);
extern "C" REX_FUNC(OriginalTextureHeader);
extern "C" REX_FUNC(OriginalShadowMask);
extern "C" REX_FUNC(OriginalSetViewport);
extern "C" REX_FUNC(OriginalMaskVolumeQuad);
extern "C" REX_FUNC(OriginalBlurTaps);

namespace forza {
namespace {
namespace sr = scene_resolution;

// The scene size once PatchSceneResolution has changed the tables; 0 while the scene is 720p.
std::atomic<uint32_t> g_width{0};
std::atomic<uint32_t> g_height{0};
// Which screen textures follow the scene, their 720p size, and where the renderer keeps the D3D
// textures; set on the thread that runs sub_8257C220 while it makes them.
bool g_scaled[scene_resolution::kTextureCount] = {};
scene_resolution::Size g_native[scene_resolution::kTextureCount] = {};
std::atomic<uint32_t> g_textures{0};
thread_local bool g_making_textures = false;
// Set on the render thread while the sun's shadow mask is drawn (ShadowMask).
thread_local bool g_drawing_shadow_mask = false;

uint32_t Load32(const uint8_t* p) {
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
void Store32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v >> 24);
  p[1] = uint8_t(v >> 16);
  p[2] = uint8_t(v >> 8);
  p[3] = uint8_t(v);
}

sr::MsaaWish Wish() {
  switch (scene_tiling::ParseMode(REXCVAR_GET(scene_msaa))) {
    case scene_tiling::Mode::k2x: return sr::MsaaWish::k2x;
    case scene_tiling::Mode::kOff: return sr::MsaaWish::kOff;
    default: return sr::MsaaWish::kGame;
  }
}

}  // namespace

namespace scene_resolution {
bool Active() { return g_height.load(std::memory_order_relaxed) != 0; }
}  // namespace scene_resolution

void PatchSceneResolution() {
  const uint32_t height = sr::ParseHeight(REXCVAR_GET(scene_resolution));
  if (!sr::Scaled(height)) return;
  const sr::Size scene = sr::SceneSize(height);
  // The SDK's scene reconstruction (FSR 3, DLSS, SMAA) and native ultrawide find the game's scene
  // by its 720p (or wider) targets.
  const std::string upscaler = rex::cvar::GetFlagByName("fh1_upscaler");
  const char* other = nullptr;
  if (!upscaler.empty() && upscaler != "off") other = "fh1_upscaler (FSR 3, DLSS, SMAA)";
  if (rex::cvar::GetFlagByName("fh1_scene_720p_native_hud") == "true") other = "separate scene AA";
  if (REXCVAR_GET(forza_ultrawide)) other = "ultrawide gameplay";
  if (other) {
    REXLOG_WARN("scene_resolution: {} is on, the scene stays at 720p", other);
    return;
  }
  uint8_t* base = rex::system::kernel_state()->memory()->virtual_membase();

  // The executable must hold the tables as expected: the five 1280x720 scenarios, entry 3 one
  // tile, and the screen textures' names.
  for (uint32_t i = 0; i < 5; ++i) {
    const uint8_t* e = GuestPtr(base, kScenarioTable + i * sr::kScenarioSize);
    if (Load32(e + sr::kScenarioWidth) != sr::kNativeWidth ||
        Load32(e + sr::kScenarioHeight) != sr::kNativeHeight) {
      REXLOG_WARN("scene_resolution: render scenario {} is not 1280x720; the scene stays 720p", i);
      return;
    }
  }
  uint8_t* entry = GuestPtr(base, kScenarioTable + sr::kSceneScenario * sr::kScenarioSize);
  if (Load32(entry + sr::kScenarioTileCount) != 1) {
    REXLOG_WARN("scene_resolution: render scenario 3 is not one pass; the scene stays at 720p");
    return;
  }
  auto texture_name = [&](uint32_t i) {
    return Load32(GuestPtr(base, kTextureTable + i * sr::kTextureSize + sr::kTextureName));
  };
  bool names_ok = true;
  for (uint32_t i = 0; i < sr::kTextureCount && names_ok; ++i) {
    names_ok = texture_name(i) >= 0x82000000 && texture_name(i) < 0x84000000;
  }
  if (!names_ok ||
      std::strcmp(reinterpret_cast<const char*>(GuestPtr(base, texture_name(1))),
                  "FrontBuffer_0") != 0) {
    REXLOG_WARN("scene_resolution: screen texture table not found; the scene stays at 720p");
    return;
  }

  const uint32_t msaa = sr::SceneMsaa(scene, Wish());
  Store32(entry + sr::kScenarioWidth, scene.width);
  Store32(entry + sr::kScenarioHeight, scene.height);
  Store32(entry + sr::kScenarioMsaa, msaa);
  uint8_t* tile = entry + sr::kScenarioTiles;
  Store32(tile + 0, 0);
  Store32(tile + 4, 0);
  Store32(tile + 8, scene.width);
  Store32(tile + 12, scene.height);

  uint32_t scaled = 0;
  for (uint32_t i = 0; i < sr::kTextureCount; ++i) {
    uint8_t* t = GuestPtr(base, kTextureTable + i * sr::kTextureSize);
    if (!t[0]) continue;  // a fixed size (shadow maps, cube map, ...)
    const char* name = reinterpret_cast<const char*>(GuestPtr(base, texture_name(i)));
    if (!sr::ScreenTextureFollowsScene(std::string_view(name, strnlen(name, 32)))) continue;
    t[0] = 0;
    const uint32_t dw = Load32(t + sr::kTextureWidth), dh = Load32(t + sr::kTextureHeight);
    g_native[i] = {sr::TextureSide(dw, sr::kNativeWidth), sr::TextureSide(dh, sr::kNativeHeight)};
    Store32(t + sr::kTextureWidth, sr::TextureSide(dw, scene.width));
    Store32(t + sr::kTextureHeight, sr::TextureSide(dh, scene.height));
    g_scaled[i] = true;
    ++scaled;
  }
  g_width.store(scene.width);
  g_height.store(scene.height);
  REXLOG_INFO("scene_resolution: 3D scene at {}x{} in one pass with {}x MSAA, {} screen textures "
              "with it; HUD and menus at 1280x720", scene.width, scene.height, 1u << msaa, scaled);
}

}  // namespace forza

namespace sr = forza::scene_resolution;

// Copies the screen texture table into the renderer (r3), makes the textures' headers and adds
// up their memory (sub_8257C220): the D3D textures follow the renderer's 41 slots, in the
// table's order. Remembered so a pass into one of the scene's can be told apart.
extern "C" REX_FUNC(ScreenTextures) {
  forza::g_textures.store(ctx.r3.u32 + sr::kTextureObjects);
  forza::g_making_textures = true;
  OriginalScreenTextures(ctx, base);
  forza::g_making_textures = false;
}

// Makes a 2D texture's header (sub_82A40F28: r3 width, r4 height, ..., the texture as the third
// argument on the stack, r1 + 0x64) and returns the size of its memory. For the scene's screen
// textures the header gets the scene size and the memory stays the 720p size
// (src/platform/scene_resolution.h: the block is shared and lent out with 720p sizes): the 720p
// header is made first for its size, then the scene-size one over it.
extern "C" REX_FUNC(TextureHeader) {
  const uint32_t textures = forza::g_textures.load(std::memory_order_relaxed);
  if (!forza::g_making_textures || !textures) {
    OriginalTextureHeader(ctx, base);
    return;
  }
  const uint32_t texture = forza::Load32(forza::GuestPtr(base, ctx.r1.u32 + 0x64));
  const uint32_t offset = texture - textures;
  const uint32_t i = offset / sr::kRendererTextureSize;
  if (texture < textures || offset % sr::kRendererTextureSize || i >= sr::kTextureCount ||
      !forza::g_scaled[i]) {
    OriginalTextureHeader(ctx, base);
    return;
  }
  const PPCRegister args[8] = {ctx.r3, ctx.r4, ctx.r5, ctx.r6, ctx.r7, ctx.r8, ctx.r9, ctx.r10};
  ctx.r3.u64 = forza::g_native[i].width;
  ctx.r4.u64 = forza::g_native[i].height;
  OriginalTextureHeader(ctx, base);
  const uint32_t native_size = ctx.r3.u32;
  ctx.r3 = args[0];
  ctx.r4 = args[1];
  ctx.r5 = args[2];
  ctx.r6 = args[3];
  ctx.r7 = args[4];
  ctx.r8 = args[5];
  ctx.r9 = args[6];
  ctx.r10 = args[7];
  OriginalTextureHeader(ctx, base);
  ctx.r3.u64 = native_size;
}

// A post-processing pass into texture r4 with viewport r8 (x, y, width, height). A 1280x720 pass
// into one of the scene's textures covers it: on menu screens and loads the render target
// manager's full-screen filter (sub_82C3E310, TU4 sub_82CFEC80) copies the last 720p frame into
// the Shared texture in WorldDepth this way. At 1280x720 its resolve wrote 1280x720 pixels into
// a 960x540 texture, and past the end of the texture block while its memory was the 540p size.
extern "C" REX_FUNC(PostPass) {
  const uint32_t w = forza::g_width.load(std::memory_order_relaxed);
  const uint32_t textures = forza::g_textures.load(std::memory_order_relaxed);
  const uint32_t dest = ctx.r4.u32;
  uint8_t* vp = ctx.r8.u32 ? forza::GuestPtr(base, ctx.r8.u32) : nullptr;
  const bool scene_texture = w && textures && dest >= textures &&
                             dest < textures + sr::kTextureCount * sr::kRendererTextureSize &&
                             (dest - textures) % sr::kRendererTextureSize == 0 &&
                             forza::g_scaled[(dest - textures) / sr::kRendererTextureSize];
  if (!scene_texture || !vp || forza::Load32(vp) != 0 || forza::Load32(vp + 4) != 0 ||
      forza::Load32(vp + 8) != sr::kNativeWidth || forza::Load32(vp + 12) != sr::kNativeHeight) {
    OriginalPostPass(ctx, base);
    return;
  }
  forza::Store32(vp + 8, w);
  forza::Store32(vp + 12, forza::g_height.load(std::memory_order_relaxed));
  OriginalPostPass(ctx, base);
  forza::Store32(vp + 8, sr::kNativeWidth);
  forza::Store32(vp + 12, sr::kNativeHeight);
}

// Draws a view of the world (r3); its viewport is at +0x1A8 (x, y, width, height).
extern "C" REX_FUNC(WorldViewRender) {
  const uint32_t w = forza::g_width.load(std::memory_order_relaxed);
  uint8_t* vp = forza::GuestPtr(base, ctx.r3.u32 + 0x1A8);
  if (!w || forza::Load32(vp) != 0 || forza::Load32(vp + 4) != 0 ||
      forza::Load32(vp + 8) != sr::kNativeWidth || forza::Load32(vp + 12) != sr::kNativeHeight) {
    OriginalWorldViewRender(ctx, base);
    return;
  }
  // The screen-sized view: drawn at the scene size, its viewport restored afterwards (the
  // HUD's projected labels read it at 720p).
  forza::Store32(vp + 8, w);
  forza::Store32(vp + 12, forza::g_height.load(std::memory_order_relaxed));
  OriginalWorldViewRender(ctx, base);
  forza::Store32(vp + 8, sr::kNativeWidth);
  forza::Store32(vp + 12, sr::kNativeHeight);
}

// The post-processing quad for a rectangle (r4: left, top, right, bottom in pixels) of a source
// texture of r5 x r6 texels.
extern "C" REX_FUNC(PostQuad) {
  const uint32_t w = forza::g_width.load(std::memory_order_relaxed);
  if (!w) {
    OriginalPostQuad(ctx, base);
    return;
  }
  uint8_t* p = forza::GuestPtr(base, ctx.r4.u32);
  sr::Rect rect{int32_t(forza::Load32(p)), int32_t(forza::Load32(p + 4)),
                int32_t(forza::Load32(p + 8)), int32_t(forza::Load32(p + 12))};
  const sr::Rect original = rect;
  if (!sr::ScaleQuadRect(rect, ctx.r5.u32, ctx.r6.u32,
                         {w, forza::g_height.load(std::memory_order_relaxed)})) {
    OriginalPostQuad(ctx, base);
    return;
  }
  forza::Store32(p, uint32_t(rect.left));
  forza::Store32(p + 4, uint32_t(rect.top));
  forza::Store32(p + 8, uint32_t(rect.right));
  forza::Store32(p + 12, uint32_t(rect.bottom));
  OriginalPostQuad(ctx, base);
  forza::Store32(p, uint32_t(original.left));
  forza::Store32(p + 4, uint32_t(original.top));
  forza::Store32(p + 8, uint32_t(original.right));
  forza::Store32(p + 12, uint32_t(original.bottom));
}

// Makes surface `idx` (r5) of render scenario `scen` (r4): r6 x r7, MSAA r8. Surface 0 (the full
// size 2:10:10:10 one) is where the render target manager's full-screen filters (the fades and
// blurs of loads and camera cuts) draw before they resolve into the 720p front buffers: it stays
// 1280x720 for the smaller scene, or those resolves are refused ("Resolve region 0 <= x < 1280 is
// outside the surface pitch 960").
extern "C" REX_FUNC(ScenarioSurface) {
  if (forza::g_width.load(std::memory_order_relaxed) && ctx.r4.u32 == sr::kSceneScenario &&
      ctx.r5.u32 == 0) {
    ctx.r6.u64 = sr::kNativeWidth;
    ctx.r7.u64 = sr::kNativeHeight;
  }
  OriginalScenarioSurface(ctx, base);
}

// Creates the D3D device with the presentation parameters at r6 (+0 back buffer width, +4
// height), which the scenario switch filled with the scene's size.
extern "C" REX_FUNC(CreateDevice) {
  const uint32_t w = forza::g_width.load(std::memory_order_relaxed);
  uint8_t* params = forza::GuestPtr(base, ctx.r6.u32);
  const uint32_t pw = forza::Load32(params), ph = forza::Load32(params + 4);
  if (!w || pw != w) {
    OriginalCreateDevice(ctx, base);
    return;
  }
  forza::Store32(params, sr::kNativeWidth);
  forza::Store32(params + 4, sr::kNativeHeight);
  OriginalCreateDevice(ctx, base);
  forza::Store32(params, pw);
  forza::Store32(params + 4, ph);
}

// Draws the sun's shadow mask over the depth pre-pass (sub_824532F0: the cascades' passes, the
// blur and the resolve into the ShadowMask texture, docs/graphics-detail.md "Shadow softness").
// Its viewport and its blur's and volume quad's texel offsets are for 1280x720, written in the
// code: they follow the scene while it runs (the hooks below).
extern "C" REX_FUNC(ShadowMask) {
  if (!forza::g_width.load(std::memory_order_relaxed) || forza::g_drawing_shadow_mask) {
    OriginalShadowMask(ctx, base);
    return;
  }
  forza::g_drawing_shadow_mask = true;
  OriginalShadowMask(ctx, base);
  forza::g_drawing_shadow_mask = false;
}

// D3DDevice_SetViewport (r4: x, y, width, height, min z, max z). The shadow mask's 1280x720
// viewport is the scene's while the mask is drawn.
extern "C" REX_FUNC(SetViewport) {
  uint8_t* vp = ctx.r4.u32 ? forza::GuestPtr(base, ctx.r4.u32) : nullptr;
  if (!forza::g_drawing_shadow_mask || !vp) {
    OriginalSetViewport(ctx, base);
    return;
  }
  sr::Viewport v{forza::Load32(vp), forza::Load32(vp + 4), forza::Load32(vp + 8),
                 forza::Load32(vp + 12)};
  const sr::Viewport original = v;
  if (!sr::ScaleMaskViewport(v, {forza::g_width.load(std::memory_order_relaxed),
                                 forza::g_height.load(std::memory_order_relaxed)})) {
    OriginalSetViewport(ctx, base);
    return;
  }
  forza::Store32(vp + 8, v.width);
  forza::Store32(vp + 12, v.height);
  OriginalSetViewport(ctx, base);
  forza::Store32(vp + 8, original.width);
  forza::Store32(vp + 12, original.height);
}

// The shadow mask's helpers given the screen size in r4 x r5: the cascades' volume quad with its
// half-texel offsets (sub_82DDDEE0, TU4 sub_82EA04E8) and the blur's tap offsets (sub_823E2C48,
// TU4 sub_82412BD8). 1280x720 is the scene size while the mask is drawn.
namespace {
void ScaleMaskScreenSize(PPCContext& ctx) {
  if (!forza::g_drawing_shadow_mask) return;
  sr::Size s{ctx.r4.u32, ctx.r5.u32};
  if (sr::ScaleMaskScreenSize(s, {forza::g_width.load(std::memory_order_relaxed),
                                  forza::g_height.load(std::memory_order_relaxed)})) {
    ctx.r4.u64 = s.width;
    ctx.r5.u64 = s.height;
  }
}
}  // namespace

extern "C" REX_FUNC(MaskVolumeQuad) {
  ScaleMaskScreenSize(ctx);
  OriginalMaskVolumeQuad(ctx, base);
}

extern "C" REX_FUNC(BlurTaps) {
  ScaleMaskScreenSize(ctx);
  OriginalBlurTaps(ctx, base);
}
