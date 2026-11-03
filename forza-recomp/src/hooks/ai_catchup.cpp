// forza - races without the CPU drivers' rubber banding (#433, docs/playing.md).
//
// When a CPU driver's car is created (sub_824B3AE8, TU4 sub_82520EA0) the game fills its AI
// config from the database: the AISkills row (braking and cornering ranges, nominal torque),
// the AITemperaments row (passing) and the AIRubberbands row (an event's AIRubberband<level>
// column, or when that is 0, as in 81 events, its skill's Rubberbands_id). The fill is
// sub_829ADAC0 (TU4 sub_82A61F08). In single-player free roam (race type 17: the festival's
// drivers on the roads) the same rows come from a per-difficulty cache instead (sub_829AA1F0,
// TU4 sub_82A5E6B8). The driver's controller keeps a pointer to the config and
// reads these fields every update, against its track distance to the leading human:
//
// - Rubber band (sub_82B93A18, TU4 sub_82C52060; modes sub_82B89F10, TU4 sub_82C485B8): a
//   driver level with or ahead of the player and StartRubberbandingDistance or more ahead
//   (1-50 m) starts a mode: past StartTorqueCutDistance (10-600 m) a torque cut that ramps to
//   TorqueCutFactor (0.1-0.8) at MaxTorqueCutDistance, else one picked by weight from
//   AIRubberbandModeChoices (rows 5-10: 10-20% a driving mode with an amount, else none). With
//   the rows of the weakest skills (8-10) a driver 100-180 m ahead keeps 20% of its torque.
// - Catch-up (sub_82B84610, TU4 sub_82C42C08): a driver StartCatchUpDistance (+20 m a place)
//   behind brakes and corners toward the best values (1.0, 1.25) by CatchUpFactor (0.6-1.0)
//   until it is within StopCatchUpDistance.
// - Skill re-roll (sub_82B90EE8, TU4 sub_82C4F530): a driver more than
//   DistBehindForMaxPerformance behind (10-25 m) gets its skill's best braking and cornering
//   instead of a draw between its minimum and maximum; DistBehindForTorqueBoost > 0 (0 in the
//   rows 1-80 that events and online races use) adds torque further behind.
//
// ai_rubber_banding=off (launcher: Gameplay, CPU drivers) rewrites the filled config of every
// CPU driver in a single-player race (src/platform/ai_catchup.h): those distances out of reach
// and the factors neutral, so each driver races at its own skill wherever the player is.
// Online races (race type 6, and the online free roam, 18) keep the game's AI, so a Horizon
// World event or a Co-op Festival round drives the same on every console. Kept as the game has
// them: the skill and temperament rows (the difficulty), the boosts written into the code
// (sub_82B6AC10, TU4 sub_82C293E8: by grid place at the start and for a Nemesis rival, as
// Pinyon Shift reads it; not measured here) and the post-race skill adjustment
// (DynamicDifficulty in GameTunableSettings.ini). The idea of rewriting the config at the
// spawn, and the controller fields, come from Pinyon Shift's AI OPPONENTS option
// (LICENSE-Pinyon-Shift); the offsets were checked against both executables here.
//
// FORZA_AI_TRACE=1 logs each config fill (its rows, distances and factors) and, once a second
// per driver, its gap to the player, rubber band mode, torque scale and catch-up.
// tu_address_map: TU4 addresses

#include "../platform/ai_catchup.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>

REXCVAR_DEFINE_STRING(ai_rubber_banding, "game", "Gameplay",
                      "CPU drivers' rubber banding in single-player races: game (as shipped: "
                      "drivers ahead of you ease off, drivers behind you catch up) or off (each "
                      "races at its own skill wherever you are; online races are unchanged)");

#if FORZA_TU4
#define FillAiConfig sub_82A61F08
#define OriginalFillAiConfig __imp__sub_82A61F08
#define FillFreeRoamAiConfig sub_82A5E6B8
#define OriginalFillFreeRoamAiConfig __imp__sub_82A5E6B8
#define UpdateRubberBand sub_82C52060
#define OriginalUpdateRubberBand __imp__sub_82C52060
#else
#define FillAiConfig sub_829ADAC0
#define OriginalFillAiConfig __imp__sub_829ADAC0
#define FillFreeRoamAiConfig sub_829AA1F0
#define OriginalFillFreeRoamAiConfig __imp__sub_829AA1F0
#define UpdateRubberBand sub_82B93A18
#define OriginalUpdateRubberBand __imp__sub_82B93A18
#endif

// FillAiConfig(config, database, skill, temperament, track, difficulty, rubber band id,
// rubber band modifier): the AISkills, AITemperaments and AIRubberbands rows.
extern "C" REX_FUNC(OriginalFillAiConfig);
// FillFreeRoamAiConfig(cache, config, driver): the same rows from the free roam's cache;
// returns whether it had them.
extern "C" REX_FUNC(OriginalFillFreeRoamAiConfig);
// UpdateRubberBand(controller): a CPU driver's gap to the player and rubber band mode, once
// an update.
extern "C" REX_FUNC(OriginalUpdateRubberBand);

namespace {
namespace ac = forza::ai_catchup;

// Pointer to the race settings; +4 is the race type (the spawn reads it to pick the fill).
#if FORZA_TU4
constexpr uint32_t kRaceSettings = 0x833ED710;
#else
constexpr uint32_t kRaceSettings = 0x832FEBA0;
#endif
constexpr uint32_t kRaceType = 4;

// The driver's controller (the rubber band update's r3).
constexpr uint32_t kCar = 28;           // its car; +1328 is its place (0 first)
constexpr uint32_t kSkillState = 64;    // +128 braking, +132 cornering, +140 catch-up factor
constexpr uint32_t kConfig = 76;        // the AI config it was created with
constexpr uint32_t kModeOn = 152;       // byte
constexpr uint32_t kMode = 156;         // 8: torque cut, 0: none, others: mistakes
constexpr uint32_t kTorqueCut = 160;    // float, 0..factor
constexpr uint32_t kGap = 164;          // float, metres ahead of the leading human (behind < 0)
constexpr uint32_t kCatchingUp = 168;   // byte
constexpr uint32_t kTorqueScale = 216;  // float
constexpr uint32_t kCarPlace = 1328;

uint32_t Load32(uint8_t* base, uint32_t address) {
  uint32_t v;
  std::memcpy(&v, forza::GuestPtr(base, address), 4);
  return __builtin_bswap32(v);
}

float LoadFloat(uint8_t* base, uint32_t address) {
  const uint32_t v = Load32(base, address);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}

ac::Mode Mode() {
  static const ac::Mode mode = [] {
    const auto m = ac::ParseMode(REXCVAR_GET(ai_rubber_banding));
    if (m == ac::Mode::kOff)
      REXLOG_INFO("AI: no rubber banding in single-player races (ai_rubber_banding=off)");
    return m;
  }();
  return mode;
}

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_AI_TRACE");
    return v && *v && *v != '0';
  }();
  return on;
}

double Seconds() {
  static const auto start = std::chrono::steady_clock::now();
  return std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
}

uint32_t RaceType(uint8_t* base) {
  const uint32_t settings = Load32(base, kRaceSettings);
  return settings ? Load32(base, settings + kRaceType) : 0xFFFFFFFFu;
}

void Filled(uint8_t* base, uint32_t config, const char* path) {
  if (!config) return;
  uint8_t* c = forza::GuestPtr(base, config);
  const uint32_t race_type = RaceType(base);
  const bool apply = race_type != 0xFFFFFFFFu && ac::Applies(Mode(), race_type);
  if (Trace())
    REXLOG_INFO(
        "AITRACE {:.2f} fill {} race type {} skill {} rubber band {}: rubber band from {:.0f} m, "
        "torque cut {:.0f}-{:.0f} m x{:.2f}, catch-up {:.0f}/{:.0f} m (+{:.0f} a place) x{:.2f}, "
        "best skill {:.0f} m behind, torque boost {:.0f} m behind{}",
        Seconds(), path, race_type, ac::LoadInt(c, ac::kSkillId), ac::LoadInt(c, ac::kRubberbandId),
        ac::LoadFloat(c, ac::kStartRubberbanding), ac::LoadFloat(c, ac::kStartTorqueCut),
        ac::LoadFloat(c, ac::kMaxTorqueCut), ac::LoadFloat(c, ac::kTorqueCutFactor),
        ac::LoadFloat(c, ac::kStartCatchUp), ac::LoadFloat(c, ac::kStopCatchUp),
        ac::LoadFloat(c, ac::kCatchUpGapPerPosition), ac::LoadFloat(c, ac::kCatchUpFactor),
        ac::LoadFloat(c, ac::kDistBehindForMaxPerformance),
        ac::LoadFloat(c, ac::kDistBehindForTorqueBoost), apply ? "; removed" : "");
  if (apply) ac::RemoveCatchUp(c);
}

// The trace's once-a-second sample per controller.
std::mutex trace_mutex;
struct Sampled {
  uint32_t controller;
  double at;
};
Sampled sampled[32];

bool Due(uint32_t controller, double now) {
  std::lock_guard lock(trace_mutex);
  Sampled* free_slot = nullptr;
  Sampled* oldest = &sampled[0];
  for (auto& s : sampled) {
    if (s.controller == controller) {
      if (now - s.at < 1.0) return false;
      s.at = now;
      return true;
    }
    if (!s.controller && !free_slot) free_slot = &s;
    if (s.at < oldest->at) oldest = &s;
  }
  Sampled* slot = free_slot ? free_slot : oldest;
  *slot = {controller, now};
  return true;
}

}  // namespace

extern "C" REX_FUNC(FillAiConfig) {
  const uint32_t config = ctx.r3.u32;
  OriginalFillAiConfig(ctx, base);
  Filled(base, config, "event");
}

extern "C" REX_FUNC(FillFreeRoamAiConfig) {
  const uint32_t config = ctx.r4.u32;
  OriginalFillFreeRoamAiConfig(ctx, base);
  if (ctx.r3.u32 & 0xFF) Filled(base, config, "free roam");
}

extern "C" REX_FUNC(UpdateRubberBand) {
  const uint32_t controller = ctx.r3.u32;
  OriginalUpdateRubberBand(ctx, base);
  if (!Trace()) return;
  const double now = Seconds();
  if (!Due(controller, now)) return;
  const uint32_t car = Load32(base, controller + kCar);
  const uint32_t skill = Load32(base, controller + kSkillState);
  const uint32_t config = Load32(base, controller + kConfig);
  if (!car || !skill || !config) return;
  REXLOG_INFO(
      "AITRACE {:.2f} driver {:08X} skill {} rubber band {} place {} gap {:.1f} m mode {}{} cut "
      "{:.2f} torque {:.3f} catch-up {} braking {:.3f} cornering {:.3f}",
      now, controller, Load32(base, config + ac::kSkillId),
      int32_t(Load32(base, config + ac::kRubberbandId)), Load32(base, car + kCarPlace),
      LoadFloat(base, controller + kGap), Load32(base, controller + kMode),
      *forza::GuestPtr(base, controller + kModeOn) ? " on" : "",
      LoadFloat(base, controller + kTorqueCut), LoadFloat(base, controller + kTorqueScale),
      int(*forza::GuestPtr(base, controller + kCatchingUp)), LoadFloat(base, skill + 128),
      LoadFloat(base, skill + 132));
}
