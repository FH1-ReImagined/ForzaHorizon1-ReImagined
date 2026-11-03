// forza - world detail options: the track settings the console tuned for its hardware
// (docs/graphics-detail.md, #361).
//
// Each track's TrackSettings.xml (media/tracks/<track>/) is read by one loader
// (sub_82D505C0, TU4 sub_82E128B8; `this` r3 the settings struct, the same layout in both
// builds). Two of its values are raised after the read; the game reads both again every frame:
// - <screenAreaDistances depth> (struct +1272, 60 in Colorado): the view setup (sub_82E91710 in
//   TU4) copies it into the shadow pass's cull record, and the pass draws an object only within
//   that distance * sqrt(size / k) of the camera (sub_8246FE78 in TU4). Small and far objects
//   (trees up a hill, posts, rocks) cast no shadow on the console. world_shadow_detail scales it.
// - <MipMapLodBiasModifier> (+1404, 0): the track pass adds it to the texture LOD bias of the
//   track's textures. world_texture_detail makes it negative: sharper mip levels further away.
// - <ToneMapeDelay> (+416, 0.075): the exposure's adaptation rate. The VR cockpit slows it
//   (ScaleExposureAdaptation, vr_exposure_speed; docs/vr.md "Version 4").
// What is not here, and why (docs/graphics-detail.md): the world's model LODs (terrain patches,
// mid-distance terrain, LOD00-02 meshes) are chosen per PVS zone in the track data, not by a
// distance at run time; the main view's size test (<screenAreaDistances mainScene>) culls almost
// nothing in free roam; the shadow map size follows the render scale.
// tu_address_map: TU4 addresses

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/world_detail.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

REXCVAR_DEFINE_STRING(world_shadow_detail, "console", "Display",
                      "Shadows of small and distant objects: console (the game's), high (3x as far), "
                      "ultra (10x as far)");
REXCVAR_DEFINE_STRING(world_texture_detail, "console", "Display",
                      "Texture sharpness in the distance (terrain, road, scenery): console (the "
                      "game's), high (LOD bias -0.5), ultra (-1)");

#if FORZA_TU4
#define TrackSettingsLoad sub_82E128B8
#define OriginalTrackSettingsLoad __imp__sub_82E128B8
#else
#define TrackSettingsLoad sub_82D505C0
#define OriginalTrackSettingsLoad __imp__sub_82D505C0
#endif
extern "C" REX_FUNC(OriginalTrackSettingsLoad);

namespace {
namespace wd = forza::world_detail;

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
float LoadF(uint8_t* base, uint32_t address) {
  const uint32_t v = Load32(base, address);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}
void StoreF(uint8_t* base, uint32_t address, float f) {
  uint32_t v;
  std::memcpy(&v, &f, 4);
  Store32(base, address, v);
}

// The loaded track's settings struct and its own exposure adaptation rate (after the overrides).
std::atomic<uint32_t> track_settings{0};
std::atomic<float> track_tone_delay{0.0f};
std::atomic<float> track_main_scene{-1.0f};
std::atomic<float> track_shadow_depth{-1.0f};

// FORZA_TRACK_SETTINGS="1272=600,1404=-1" sets track settings floats by offset after the
// options (a test aid for comparing values; "i" before a value stores an integer).
void ApplyTestOverrides(uint8_t* base, uint32_t settings) {
  const char* env = std::getenv("FORZA_TRACK_SETTINGS");
  if (!env) return;
  const std::string s = env;
  size_t pos = 0;
  while (pos < s.size()) {
    size_t end = s.find(',', pos);
    if (end == std::string::npos) end = s.size();
    const std::string item = s.substr(pos, end - pos);
    const size_t eq = item.find('=');
    if (eq != std::string::npos) {
      const uint32_t off = uint32_t(std::strtoul(item.substr(0, eq).c_str(), nullptr, 0));
      const std::string val = item.substr(eq + 1);
      if (off < 0x1000 && !val.empty()) {
        if (val[0] == 'i') {
          Store32(base, settings + off, uint32_t(std::strtol(val.c_str() + 1, nullptr, 0)));
        } else {
          StoreF(base, settings + off, std::strtof(val.c_str(), nullptr));
        }
        REXLOG_INFO("world detail: test override +{} = {}", off, val);
      }
    }
    pos = end + 1;
  }
}

}  // namespace

extern "C" REX_FUNC(TrackSettingsLoad) {
  const uint32_t settings = ctx.r3.u32;
  OriginalTrackSettingsLoad(ctx, base);
  const wd::Level shadows = wd::ParseLevel(REXCVAR_GET(world_shadow_detail));
  const wd::Level textures = wd::ParseLevel(REXCVAR_GET(world_texture_detail));
  const float caster = LoadF(base, settings + wd::kShadowDepthArea);
  const float bias = LoadF(base, settings + wd::kMipLodBias);
  if (shadows != wd::Level::kConsole) {
    StoreF(base, settings + wd::kShadowDepthArea, wd::ShadowCasterDistance(caster, shadows));
  }
  if (textures != wd::Level::kConsole) {
    StoreF(base, settings + wd::kMipLodBias, wd::MipLodBias(bias, textures));
  }
  REXLOG_INFO("world detail: track settings {:08X}: shadow caster distance {} -> {}, texture LOD "
              "bias {} -> {}",
              settings, caster, LoadF(base, settings + wd::kShadowDepthArea), bias,
              LoadF(base, settings + wd::kMipLodBias));
  ApplyTestOverrides(base, settings);
  track_tone_delay = LoadF(base, settings + wd::kToneMapDelay);
  track_main_scene = LoadF(base, settings + wd::kMainSceneArea);
  track_shadow_depth = LoadF(base, settings + wd::kShadowDepthArea);
  track_settings = settings;
}

namespace forza::world_detail {
void ScaleExposureAdaptation(uint8_t* base, float scale) {
  const uint32_t settings = track_settings.load(std::memory_order_relaxed);
  if (!settings) return;
  const float want = ExposureAdaptation(track_tone_delay.load(std::memory_order_relaxed), scale);
  if (LoadF(base, settings + kToneMapDelay) != want) StoreF(base, settings + kToneMapDelay, want);
}
bool IsCameraCullDistance(float d) {
  return d > 0.0f && (d == track_main_scene.load(std::memory_order_relaxed) ||
                      d == track_shadow_depth.load(std::memory_order_relaxed));
}
}  // namespace forza::world_detail
