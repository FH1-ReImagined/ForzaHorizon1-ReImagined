// Copyright (c) 2026 Forza Horizon 1: ReImagined contributors.
// SPDX-License-Identifier: MIT
// Comparison-target isolation for the optional single-player AI policy.
#pragma once

#include <array>
#include <cstdint>
#include <span>
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

inline float LoadFloat(const uint8_t* config, uint32_t offset) {
  uint32_t v;
  std::memcpy(&v, config + offset, 4);
  v = __builtin_bswap32(v);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}

inline int32_t LoadInt(const uint8_t* config, uint32_t offset) {
  uint32_t v;
  std::memcpy(&v, config + offset, 4);
  return int32_t(__builtin_bswap32(v));
}

// These return addresses were read from branch-and-link instructions in both
// supported XEX images. Other uses of the same queries (including steering and
// race ordering) must receive the original result.
enum class Query { LeadingHuman, TrailingHuman };
enum class Comparison { None, RubberBand, CatchUp, RelativeSkill };
struct ComparisonSite { uint32_t base, tu4; Query query; Comparison use; };
inline constexpr std::array<ComparisonSite, 5> kComparisonSites{{
    {0x82B93A84, 0x82C520CC, Query::LeadingHuman, Comparison::RubberBand},
    {0x82B84384, 0x82C4297C, Query::LeadingHuman, Comparison::RubberBand},
    {0x82B84654, 0x82C42C4C, Query::LeadingHuman, Comparison::CatchUp},
    {0x82B90FA4, 0x82C4F5EC, Query::TrailingHuman, Comparison::RelativeSkill},
    {0x82B910CC, 0x82C4F714, Query::TrailingHuman, Comparison::RelativeSkill}}};

inline Comparison ClassifyComparison(Query query, uint32_t caller, bool tu4) {
  for (const auto& site : kComparisonSites)
    if (site.query == query && caller == (tu4 ? site.tu4 : site.base)) return site.use;
  return Comparison::None;
}

inline Comparison IsolatedComparison(Mode mode, uint32_t race_type, Query query,
                                      uint32_t caller, bool tu4) {
  if (race_type == UINT32_MAX || !Applies(mode, race_type)) return Comparison::None;
  return ClassifyComparison(query, caller, tu4);
}

// The game's no-opponent catch-up branch returns without clearing these live
// fields. Retire any previously active blend before taking that branch. No AI
// config, skill-range, temperament or torque field is rewritten.
inline void RetireCatchUpBlend(std::span<uint8_t> controller, std::span<uint8_t> skill) {
  if (controller.size() > 168) controller[168] = 0;
  if (skill.size() >= 144) std::memset(skill.data() + 140, 0, 4);
}

}  // namespace forza::ai_catchup
