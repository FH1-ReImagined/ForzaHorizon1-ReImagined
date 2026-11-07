// Runs the production wrappers of car_audio_volume.cpp (#730) against stand-ins for the game's
// level getters: the engine, supercharger and turbo levels the car sounds read are scaled by
// car_engine_volume / car_turbo_volume, the mixer's own by-bus read is not, and 100 % returns
// the game's value unchanged.
#include "platform/guest_addr.h"
#include <rex/ppc/func.h>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>

#if FORZA_TU4
#define PlayerEngine sub_82C68E70
#define OpponentEngine sub_82C68E78
#define SuperCharger sub_82C68E98
#define Turbo sub_82C68EA0
#define Bus sub_82C6C978
#define OriginalPlayerEngine __imp__sub_82C68E70
#define OriginalOpponentEngine __imp__sub_82C68E78
#define OriginalSuperCharger __imp__sub_82C68E98
#define OriginalTurbo __imp__sub_82C68EA0
#define OriginalBus __imp__sub_82C6C978
#else
#define PlayerEngine sub_82BAA620
#define OpponentEngine sub_82BAA628
#define SuperCharger sub_82BAA648
#define Turbo sub_82BAA650
#define Bus sub_82BAE278
#define OriginalPlayerEngine __imp__sub_82BAA620
#define OriginalOpponentEngine __imp__sub_82BAA628
#define OriginalSuperCharger __imp__sub_82BAA648
#define OriginalTurbo __imp__sub_82BAA650
#define OriginalBus __imp__sub_82BAE278
#endif

extern int32_t car_engine_volume;
extern int32_t car_turbo_volume;
extern "C" REX_FUNC(PlayerEngine);
extern "C" REX_FUNC(OpponentEngine);
extern "C" REX_FUNC(SuperCharger);
extern "C" REX_FUNC(Turbo);
extern "C" REX_FUNC(Bus);

// The game's levels (AudioEngineConfig.xml defaults).
extern "C" REX_FUNC(OriginalPlayerEngine) { (void)base; ctx.f1.f64 = 0.45f; }
extern "C" REX_FUNC(OriginalOpponentEngine) { (void)base; ctx.f1.f64 = 0.25f; }
extern "C" REX_FUNC(OriginalSuperCharger) { (void)base; ctx.f1.f64 = 0.5f; }
extern "C" REX_FUNC(OriginalTurbo) { (void)base; ctx.f1.f64 = 0.3f; }
// The by-bus getter is a jump table that tail-calls the bus's getter through the vtable.
extern "C" REX_FUNC(OriginalBus) {
  switch (ctx.r4.u32) {
    case 0: PlayerEngine(ctx, base); break;
    case 1: OpponentEngine(ctx, base); break;
    case 3: SuperCharger(ctx, base); break;
    case 4: Turbo(ctx, base); break;
    default: ctx.f1.f64 = 1.0; break;
  }
}

namespace {
uint8_t memory[16];
double Call(void (*f)(PPCContext&, uint8_t*), uint32_t bus = 0) {
  PPCContext c; c.r4.u64 = bus; f(c, memory); return c.f1.f64;
}
void Near(double got, double want, const char* what) {
  if (std::fabs(got - want) > 1e-6) {
    std::cerr << what << ": " << got << " != " << want << "\n";
    assert(false);
  }
}
void Levels(double engine, double turbo, const char* what) {
  Near(Call(PlayerEngine), 0.45f * engine, what);
  Near(Call(OpponentEngine), 0.25f * engine, what);
  Near(Call(SuperCharger), 0.5f * turbo, what);
  Near(Call(Turbo), 0.3f * turbo, what);
  // The mixer's read keeps the game's value whatever the settings.
  Near(Call(Bus, 0), 0.45f, what);
  Near(Call(Bus, 1), 0.25f, what);
  Near(Call(Bus, 3), 0.5f, what);
  Near(Call(Bus, 4), 0.3f, what);
  Near(Call(Bus, 7), 1.0, what);
}
}  // namespace

int main() {
  Levels(1, 1, "default");
  // 100 % is the game's own value, bit for bit.
  assert(Call(PlayerEngine) == double(0.45f) && Call(Turbo) == double(0.3f));
  car_engine_volume = 0; Levels(0, 1, "engine off");
  car_engine_volume = 50; Levels(0.5, 1, "engine half");
  car_engine_volume = 100; car_turbo_volume = 0; Levels(1, 0, "turbo off");
  car_turbo_volume = 200; Levels(1, 2, "turbo double");
  car_engine_volume = 150; car_turbo_volume = 25; Levels(1.5, 0.25, "both");
  car_engine_volume = 1000; car_turbo_volume = -40; Levels(2, 0, "out of range");
  std::cout << "car audio volume checks passed\n";
  return 0;
}
