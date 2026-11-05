// forza - car detail option (src/hooks/car_detail.cpp, docs/graphics-detail.md, #361).
// The decisions, without guest memory, so tests/car_detail_test.cpp can check them.
#pragma once

#include <cstdint>
#include <string_view>

namespace forza::car_detail {

enum class Level { kConsole, kHigh, kUltra };

inline Level ParseLevel(std::string_view value) {
  if (value == "high") return Level::kHigh;
  if (value == "ultra") return Level::kUltra;
  return Level::kConsole;
}

// The game picks each car's model LOD from its distance to the camera (minus its size, divided
// by the camera's zoom) against the render setting CarLODTransDist (LODdist_Near 15, _Far 25,
// _VeryFar 50: LOD0 nearer than 15 m, LOD1 to 25, LOD2 to 50, LOD3 beyond), for traffic
// CarLODTransDistTraffic (LODdist_T_LOD2-5 15/25/50/100, _T_LOD4_Closest 20, _T_LOD5_Closest 80).
// The levels multiply every distance.
inline float DistanceScale(Level level) {
  switch (level) {
    case Level::kHigh: return 2.0f;
    case Level::kUltra: return 4.0f;
    default: return 1.0f;
  }
}

// CarLODThresholds (LOD1_Threshold 2, LOD2_Threshold 4, and 6): the nearest 2 cars other than
// the player's may use LOD0, the next ones up to the 4th LOD1, the rest LOD2 at best; from 6 cars
// on, both counts drop by one. The levels raise the counts with the distances, so that the
// cars the longer distances keep detailed are not capped back by their rank.
inline int32_t CountScale(Level level) {
  switch (level) {
    case Level::kHigh: return 2;
    case Level::kUltra: return 4;
    default: return 1;
  }
}

inline float ScaleDistance(float value, Level level) {
  return value > 0.0f ? value * DistanceScale(level) : value;
}
inline int32_t ScaleCount(int32_t value, Level level) {
  if (value <= 0 || value > 1000) return value;
  return value * CountScale(level);
}

}  // namespace forza::car_detail
