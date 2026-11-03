// ai_rubber_banding (src/hooks/ai_catchup.cpp, #433): where it applies and what it does to a
// CPU driver's AI config (src/platform/ai_catchup.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/ai_catchup_test.cpp
#include "platform/ai_catchup.h"

#include <cstdio>
#include <cstdlib>

namespace ac = forza::ai_catchup;

#define CHECK(x)                                                \
  do {                                                          \
    if (!(x)) {                                                 \
      std::fprintf(stderr, "ai catch-up: FAIL: %s\n", #x);      \
      std::exit(1);                                             \
    }                                                           \
  } while (false)

namespace {

void StoreInt(uint8_t* config, uint32_t offset, int32_t value) {
  const uint32_t v = __builtin_bswap32(uint32_t(value));
  std::memcpy(config + offset, &v, 4);
}

// A config as the game fills it for AISkills row 71 (FR02 on Medium) and AIRubberbands row 9.
void FillStock(uint8_t* c) {
  for (uint32_t i = 0; i < ac::kConfigSize; ++i) c[i] = uint8_t(i * 7 + 3);
  StoreInt(c, ac::kSkillId, 71);
  ac::StoreFloat(c, 320, 0.95f);  // braking minimum
  ac::StoreFloat(c, 324, 1.0f);   // braking maximum
  ac::StoreFloat(c, 328, 0.56f);  // cornering minimum
  ac::StoreFloat(c, 332, 0.57f);  // cornering maximum
  ac::StoreFloat(c, ac::kDistBehindForMaxPerformance, 25.0f);
  ac::StoreFloat(c, ac::kDistBehindForTorqueBoost, 0.0f);
  ac::StoreFloat(c, 352, 0.942f);  // nominal torque scale
  StoreInt(c, ac::kRubberbandId, 9);
  ac::StoreFloat(c, ac::kStartRubberbanding, 1.0f);
  ac::StoreFloat(c, ac::kMaxRubberbandingEffect, 100.0f);
  ac::StoreFloat(c, ac::kStartTorqueCut, 20.0f);
  ac::StoreFloat(c, ac::kMaxTorqueCut, 140.0f);
  ac::StoreFloat(c, ac::kTorqueCutFactor, 0.8f);
  ac::StoreFloat(c, ac::kStartCatchUp, 150.0f);
  ac::StoreFloat(c, ac::kStopCatchUp, 120.0f);
  ac::StoreFloat(c, ac::kCatchUpFactor, 0.7f);
  ac::StoreFloat(c, ac::kCatchUpGapPerPosition, 20.0f);
}

bool Changed(uint32_t offset) {
  switch (offset) {
    case ac::kDistBehindForMaxPerformance:
    case ac::kDistBehindForTorqueBoost:
    case ac::kStartRubberbanding:
    case ac::kMaxRubberbandingEffect:
    case ac::kStartTorqueCut:
    case ac::kMaxTorqueCut:
    case ac::kTorqueCutFactor:
    case ac::kStartCatchUp:
    case ac::kStopCatchUp:
    case ac::kCatchUpFactor:
    case ac::kCatchUpGapPerPosition:
      return true;
    default:
      return false;
  }
}

}  // namespace

int main() {
  // Only the launcher's "off" turns it off; the game as shipped otherwise.
  CHECK(ac::ParseMode("game") == ac::Mode::kGame);
  CHECK(ac::ParseMode("") == ac::Mode::kGame);
  CHECK(ac::ParseMode("Off") == ac::Mode::kGame);
  CHECK(ac::ParseMode("off") == ac::Mode::kOff);

  // Single-player races and free roam, never online ones.
  for (uint32_t type : {0u, 3u, 4u, 8u, 12u, 17u, 19u, 20u}) {
    CHECK(ac::Applies(ac::Mode::kOff, type));
    CHECK(!ac::Applies(ac::Mode::kGame, type));
  }
  CHECK(!ac::Applies(ac::Mode::kOff, ac::kRaceTypeOnlineRace));
  CHECK(!ac::Applies(ac::Mode::kOff, ac::kRaceTypeOnlineFreeRoam));

  uint8_t stock[ac::kConfigSize], config[ac::kConfigSize];
  FillStock(stock);
  std::memcpy(config, stock, sizeof(config));
  ac::RemoveCatchUp(config);

  // Ahead: no mode starts and the torque cut never begins (and would cut nothing).
  CHECK(ac::LoadFloat(config, ac::kStartRubberbanding) >= ac::kOutOfReach);
  CHECK(ac::LoadFloat(config, ac::kStartTorqueCut) >= ac::kOutOfReach);
  CHECK(ac::LoadFloat(config, ac::kMaxTorqueCut) > ac::LoadFloat(config, ac::kStartTorqueCut));
  CHECK(ac::LoadFloat(config, ac::kTorqueCutFactor) == 0.0f);
  // Behind: catch-up never turns on (and would pull nothing); no best skill, no torque boost.
  CHECK(ac::LoadFloat(config, ac::kStartCatchUp) >= ac::kOutOfReach);
  CHECK(ac::LoadFloat(config, ac::kStopCatchUp) >= ac::kOutOfReach);
  CHECK(ac::LoadFloat(config, ac::kCatchUpFactor) == 0.0f);
  CHECK(ac::LoadFloat(config, ac::kCatchUpGapPerPosition) == 0.0f);
  CHECK(ac::LoadFloat(config, ac::kDistBehindForMaxPerformance) == 0.0f);
  CHECK(ac::LoadFloat(config, ac::kDistBehindForTorqueBoost) == 0.0f);

  // Everything else (the skill, its ranges and torque, the temperament, the row ids) stays.
  for (uint32_t offset = 0; offset < ac::kConfigSize; offset += 4)
    if (!Changed(offset)) CHECK(std::memcmp(config + offset, stock + offset, 4) == 0);
  CHECK(ac::LoadInt(config, ac::kSkillId) == 71);
  CHECK(ac::LoadInt(config, ac::kRubberbandId) == 9);
  CHECK(ac::LoadFloat(config, 352) == 0.942f);

  // Rewriting twice is the same as once.
  uint8_t again[ac::kConfigSize];
  std::memcpy(again, config, sizeof(again));
  ac::RemoveCatchUp(again);
  CHECK(std::memcmp(again, config, sizeof(again)) == 0);

  std::printf("ai catch-up: ok\n");
  return 0;
}
