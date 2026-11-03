// forza - the CPU drivers' rubber banding option (src/hooks/ai_catchup.cpp, #433).
// The decisions and the AI config rewrite, without guest memory, so tests/ai_catchup_test.cpp
// can check them.
#pragma once

#include <cstdint>
#include <cstring>
#include <string_view>

namespace forza::ai_catchup {

enum class Mode { kGame, kOff };

inline Mode ParseMode(std::string_view value) {
  return value == "off" ? Mode::kOff : Mode::kGame;
}

// The race type of the race being set up (the race settings' +4, the value
// CGameTypeDecision maps to a flow): 3 career race, 6 online race, 8 replay, 12 time trial,
// 17 single-player free roam, 18 online free roam, 19/20 (Title Update 4) rally free roam
// and race. Online races keep the game's AI on every console, so the CPU drivers of a Horizon
// World event or a Co-op Festival round drive the same for everyone.
constexpr uint32_t kRaceTypeOnlineRace = 6, kRaceTypeOnlineFreeRoam = 18;

inline bool Applies(Mode mode, uint32_t race_type) {
  return mode == Mode::kOff && race_type != kRaceTypeOnlineRace &&
         race_type != kRaceTypeOnlineFreeRoam;
}

// The AI config a CPU driver is created with (496 bytes, big-endian; filled at its spawn from
// the AISkills, AITemperaments and AIRubberbands rows, docs/playing.md). The fields that move
// a driver's pace by where it is against the player:
constexpr uint32_t kSkillId = 280;                   // AISkills row (int)
constexpr uint32_t kDistBehindForMaxPerformance = 336;  // > 0: that far behind, best braking and cornering
constexpr uint32_t kDistBehindForTorqueBoost = 344;  // > 0: that far behind, more torque
constexpr uint32_t kRubberbandId = 368;              // AIRubberbands row (int, -1: none)
constexpr uint32_t kStartRubberbanding = 416;        // ahead this far: a slow-down mode starts
constexpr uint32_t kMaxRubberbandingEffect = 420;
constexpr uint32_t kStartTorqueCut = 424;            // ahead this far: the mode cuts torque
constexpr uint32_t kMaxTorqueCut = 428;              // the full cut from here
constexpr uint32_t kTorqueCutFactor = 432;           // torque x (1 - factor) at the full cut
constexpr uint32_t kStartCatchUp = 436;              // behind this far: catch-up on
constexpr uint32_t kStopCatchUp = 440;               // behind less than this: off again
constexpr uint32_t kCatchUpFactor = 444;             // braking/cornering pulled to the best
constexpr uint32_t kCatchUpGapPerPosition = 448;     // added to both per place behind
constexpr uint32_t kConfigSize = 496;

// Farther than any route: the rubber band, torque cut and catch-up never start. The factors
// are also neutral (0), so neither would change anything if they did.
constexpr float kOutOfReach = 1.0e6f;

inline float LoadFloat(const uint8_t* config, uint32_t offset) {
  uint32_t v;
  std::memcpy(&v, config + offset, 4);
  v = __builtin_bswap32(v);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}

inline void StoreFloat(uint8_t* config, uint32_t offset, float f) {
  uint32_t v;
  std::memcpy(&v, &f, 4);
  v = __builtin_bswap32(v);
  std::memcpy(config + offset, &v, 4);
}

inline int32_t LoadInt(const uint8_t* config, uint32_t offset) {
  uint32_t v;
  std::memcpy(&v, config + offset, 4);
  return int32_t(__builtin_bswap32(v));
}

// Rewrites a filled config (host pointer to its first byte) so the driver races at its own
// skill wherever the player is. Its skill (braking and cornering ranges, nominal torque),
// temperament and rubber band row id stay.
inline void RemoveCatchUp(uint8_t* config) {
  StoreFloat(config, kDistBehindForMaxPerformance, 0.0f);
  StoreFloat(config, kDistBehindForTorqueBoost, 0.0f);
  StoreFloat(config, kStartRubberbanding, kOutOfReach);
  StoreFloat(config, kMaxRubberbandingEffect, 2 * kOutOfReach);
  StoreFloat(config, kStartTorqueCut, kOutOfReach);
  StoreFloat(config, kMaxTorqueCut, 2 * kOutOfReach);
  StoreFloat(config, kTorqueCutFactor, 0.0f);
  StoreFloat(config, kStartCatchUp, kOutOfReach);
  StoreFloat(config, kStopCatchUp, kOutOfReach);
  StoreFloat(config, kCatchUpFactor, 0.0f);
  StoreFloat(config, kCatchUpGapPerPosition, 0.0f);
}

}  // namespace forza::ai_catchup
