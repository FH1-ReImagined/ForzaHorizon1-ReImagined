// forza - engine and turbo/supercharger volume (#730).
//
// The audio manager (TU4 *0x8352EA5C, disc *0x83439490) keeps one live level per car sound
// bus: AudioEngineConfig.xml gives the defaults (gamePlayerEngine 0.45, gameOpponentEngine
// 0.25, gameSuperCharger 0.5, gameTurbo 0.3, ...), and the mixer snapshots
// (AudioMixerSnapshots.xml: FreeRoam, Race, Paused, ...) fade them by writing the same fields
// through setters. Every car sound reads its level through a getter on each update and
// multiplies it into the volume it sets on its FMOD events:
// - gamePlayerEngine (TU4 +0x5DDC, vtable +0x1A8; disc +0x5DD8, +0x19C): the engine voices
//   (TU4 sub_82C7E068, sub_82C7EA40, sub_82C7ED28, sub_82C81678); sub_82C84B08 takes it for
//   the player's car and gameOpponentEngine (+4) for the others.
// - gameSuperCharger (TU4 +0x5DF0, +0x1BC): the supercharger whine (Cars/SuperChargers).
// - gameTurbo (TU4 +0x5DF4, +0x1C0): the turbo spool and the blow-off valve, Hi and Lo
//   layers (Cars/Turbos: TurboPSI, TurboBlowoffValveOpen).
// car_engine_volume and car_turbo_volume (percent, 0-200, 100 = the game's level) scale what
// those getters return. The fields themselves keep the game's values: the mixer reads them
// through its own by-bus getter (TU4 sub_82C6C978, disc sub_82BAE278) to take its defaults
// and to fade back to them, then writes them back, so that read stays unscaled (a scaled
// default would be written back and scaled again).

#include "../platform/guest_addr.h"

#include <rex/cvar.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <algorithm>
#include <cstdint>

// tu_address_map: TU4 addresses (the disc and TU4 names are both given below; the map
// does not pair the disc's gameTurbo getter).
#if FORZA_TU4
#define PlayerEngineLevel sub_82C68E70
#define OpponentEngineLevel sub_82C68E78
#define SuperChargerLevel sub_82C68E98
#define TurboLevel sub_82C68EA0
#define BusLevel sub_82C6C978
#define OriginalPlayerEngineLevel __imp__sub_82C68E70
#define OriginalOpponentEngineLevel __imp__sub_82C68E78
#define OriginalSuperChargerLevel __imp__sub_82C68E98
#define OriginalTurboLevel __imp__sub_82C68EA0
#define OriginalBusLevel __imp__sub_82C6C978
#else
#define PlayerEngineLevel sub_82BAA620
#define OpponentEngineLevel sub_82BAA628
#define SuperChargerLevel sub_82BAA648
#define TurboLevel sub_82BAA650
#define BusLevel sub_82BAE278
#define OriginalPlayerEngineLevel __imp__sub_82BAA620
#define OriginalOpponentEngineLevel __imp__sub_82BAA628
#define OriginalSuperChargerLevel __imp__sub_82BAA648
#define OriginalTurboLevel __imp__sub_82BAA650
#define OriginalBusLevel __imp__sub_82BAE278
#endif

REXCVAR_DEFINE_INT32(car_engine_volume, 100, "Audio",
                     "Engine sound of every car, percent of the game's level (0-200; 100 keeps it).");
REXCVAR_DEFINE_INT32(car_turbo_volume, 100, "Audio",
                     "Turbo spool, blow-off and supercharger whine of every car, percent of the "
                     "game's level (0-200; 100 keeps it).");

extern "C" REX_FUNC(OriginalPlayerEngineLevel);
extern "C" REX_FUNC(OriginalOpponentEngineLevel);
extern "C" REX_FUNC(OriginalSuperChargerLevel);
extern "C" REX_FUNC(OriginalTurboLevel);
extern "C" REX_FUNC(OriginalBusLevel);

namespace {

// Set while the mixer reads a bus level for itself (BusLevel), on the audio thread that runs it.
thread_local bool mixer_read = false;

double Scaled(double level, int32_t percent) {
  if (percent == 100 || mixer_read) return level;
  return double(float(level * (std::clamp(percent, 0, 200) / 100.0)));
}

}  // namespace

extern "C" REX_FUNC(PlayerEngineLevel) {
  OriginalPlayerEngineLevel(ctx, base);
  ctx.f1.f64 = Scaled(ctx.f1.f64, REXCVAR_GET(car_engine_volume));
}

extern "C" REX_FUNC(OpponentEngineLevel) {
  OriginalOpponentEngineLevel(ctx, base);
  ctx.f1.f64 = Scaled(ctx.f1.f64, REXCVAR_GET(car_engine_volume));
}

extern "C" REX_FUNC(SuperChargerLevel) {
  OriginalSuperChargerLevel(ctx, base);
  ctx.f1.f64 = Scaled(ctx.f1.f64, REXCVAR_GET(car_turbo_volume));
}

extern "C" REX_FUNC(TurboLevel) {
  OriginalTurboLevel(ctx, base);
  ctx.f1.f64 = Scaled(ctx.f1.f64, REXCVAR_GET(car_turbo_volume));
}

extern "C" REX_FUNC(BusLevel) {
  const bool outer = mixer_read;
  mixer_read = true;
  OriginalBusLevel(ctx, base);
  mixer_read = outer;
}
