// forza - world detail options (src/hooks/world_detail.cpp, docs/graphics-detail.md).
// The decisions, without guest memory, so tests/world_detail_test.cpp can check them.
#pragma once

#include <cstdint>
#include <string_view>

namespace forza::world_detail {

// Fields of the track's settings (media/tracks/<track>/TrackSettings.xml) after the game read
// them, as offsets in the struct its loader fills (the same in the disc build and Title Update 4).
constexpr uint32_t kMainSceneArea = 1260;   // <screenAreaDistances mainScene> (350 in Colorado)
constexpr uint32_t kShadowDepthArea = 1272; // <screenAreaDistances depth> (60)
constexpr uint32_t kMipLodBias = 1404;      // <MipMapLodBiasModifier value> (0)
constexpr uint32_t kToneMapDelay = 416;     // <ToneMapeDelay val> (0.075): how fast the exposure adapts

enum class Level { kConsole, kHigh, kUltra };

inline Level ParseLevel(std::string_view value) {
  if (value == "high") return Level::kHigh;
  if (value == "ultra") return Level::kUltra;
  return Level::kConsole;
}

// The shadow pass draws an object only within distance * sqrt(size / k) of the camera
// (the size test sub_8246FE78, with the track's "depth" screen-area distance): small and far
// objects (trees up the hill, posts, rocks) cast no shadow. Console 60 m; the levels scale it.
inline float ShadowCasterScale(Level level) {
  switch (level) {
    case Level::kHigh: return 3.0f;
    case Level::kUltra: return 10.0f;
    default: return 1.0f;
  }
}

// Added to the track's texture LOD bias: negative picks sharper mip levels for the terrain,
// road and scenery textures further away.
inline float TextureLodBias(Level level) {
  switch (level) {
    case Level::kHigh: return -0.5f;
    case Level::kUltra: return -1.0f;
    default: return 0.0f;
  }
}

// The track's values with the options applied; a value the track does not set (0 or less,
// which turns the size test off) stays as it is.
inline float ShadowCasterDistance(float track_value, Level level) {
  return track_value > 0.0f ? track_value * ShadowCasterScale(level) : track_value;
}
inline float MipLodBias(float track_value, Level level) {
  return track_value + TextureLodBias(level);
}

// The exposure's adaptation rate with a factor (the VR cockpit slows it: the eye adaptation
// measures the whole picture, so with the headset's wide view a head turn toward a bright window
// or the dark footwell re-exposed the whole cabin within a fraction of a second).
inline float ExposureAdaptation(float track_value, float scale) {
  return track_value * (scale > 0.0f ? scale : 1.0f);
}

// hooks/world_detail.cpp, any thread: the current track's exposure adaptation rate times
// `scale` (1: the track's own). No-op before a track is loaded.
void ScaleExposureAdaptation(uint8_t* base, float scale);
// Whether `d` is the loaded track's <screenAreaDistances mainScene> or its shadow casters' depth
// distance as set (the cull records of the main view and of the shadow view carry them, both with
// the camera's field of view; hooks/vr_culling.cpp).
bool IsCameraCullDistance(float d);

}  // namespace forza::world_detail
