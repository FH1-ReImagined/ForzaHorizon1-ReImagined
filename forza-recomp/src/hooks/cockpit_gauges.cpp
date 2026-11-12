// forza - "Cockpit gauges always lit" (cockpit_gauges_lit, issue #183): the instrument
// backlight of the player's own car stays on in daylight, as the game shows it at night.
//
// How the game lights it: the car's headlight amount (0 by day, on at night) goes from
// CCarPresentationDeferred::SetHeadLightAmount (game thread) to the render thread's
// Presentation_Unified car (sub_82E952D0 stores it at render object + 0x342C), and
// sub_82448F90 copies it into the pixel shader constant dynamicLights2.x (c108.x) once per
// car and view. Every car material that glows with the lights multiplies by it: the
// headlight and tail light lenses, the number plate light, and the cockpit's gauge faces and
// needles (CockpitGaugesEmissive * GaugeEmissiveScale * dynamicLights2.x in the
// cockpit_gauge shaders of media/shaders/Cars/shaders_v16.fxobj). So the amount can't change
// for the car as a whole without lighting the lamps too.
//
// The car is drawn as a set of models, one call of sub_8246CEC8(presentation, ?, pass,
// group, lod) each: group 16 is the cockpit interior, 1-15 the analog gauge needles, the
// lamps are in other groups. Each model is drawn at once by sub_824469B0(constants, model)
// from the constant shadow the GPU reads at that draw (src/platform/cockpit_gauges.h). For
// the cockpit groups of the local player's car the hook raises c108.x to at least 1 around
// that draw and puts the game's value back after it, so only the dashboard glows; the
// headlights, their beams and every other car stay as the game draws them.
//
// The local player's car: in the vehicle list (as world_challenge.cpp reads it), the vehicle
// of kind 0 holds its deferred presentation at +0x2D94; that object's +4 is the render
// thread's presentation, the `this` of sub_8246CEC8. The game thread compares each car's
// SetHeadLightAmount call with the list and publishes the local one; the render thread only
// compares pointers. Other players' cars online (kind 2) and CPU drivers never match.
// tu_address_map: TU4 addresses

#include "../debug/d3d_trace.h"
#include "../platform/cockpit_gauges.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdint>
#include <cstring>

REXCVAR_DEFINE_BOOL(cockpit_gauges_lit, false, "Display",
                    "Keep the cockpit gauges of your own car lit, also by day");

#if FORZA_TU4
#define DrawCarModel sub_8246CEC8
#define OriginalDrawCarModel __imp__sub_8246CEC8
#define DrawModelNow sub_824469B0
#define OriginalDrawModelNow __imp__sub_824469B0
#define SetHeadLightAmount sub_82E81A48
#define OriginalSetHeadLightAmount __imp__sub_82E81A48
#else
#define DrawCarModel sub_8243CDC0
#define OriginalDrawCarModel __imp__sub_8243CDC0
#define DrawModelNow sub_82416A00
#define OriginalDrawModelNow __imp__sub_82416A00
#define SetHeadLightAmount sub_82DBFB78
#define OriginalSetHeadLightAmount __imp__sub_82DBFB78
#endif
extern "C" REX_FUNC(OriginalDrawCarModel);
extern "C" REX_FUNC(OriginalDrawModelNow);
extern "C" REX_FUNC(OriginalSetHeadLightAmount);

namespace {
namespace gauges = forza::cockpit_gauges;

#if FORZA_TU4
constexpr uint32_t kCarRoot = 0x833CAAE4;
#else
constexpr uint32_t kCarRoot = 0x832DF024;
#endif

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
bool Plausible(uint32_t address) { return address >= 0x00100000 && address < 0xF0000000; }

// The local player's car's render thread presentation, 0 while unknown.
std::atomic<uint32_t> local_presentation{0};
thread_local bool drawing_cockpit = false;

// Whether `deferred` (a car's deferred presentation) belongs to this console's player.
bool LocalPlayersCar(uint8_t* base, uint32_t deferred) {
  const uint32_t root = Load32(base, kCarRoot);
  if (!Plausible(root)) return false;
  const uint32_t holder = Load32(base, root + 4);
  if (!Plausible(holder)) return false;
  const uint32_t list = Load32(base, holder + 4);
  if (!Plausible(list)) return false;
  const uint32_t first = Load32(base, list + 0x80);
  const uint32_t end = Load32(base, list + 0x84);
  if (!Plausible(first) || end < first || end - first > 4 * 64) return false;
  for (uint32_t slot = first; slot < end; slot += 4) {
    const uint32_t vehicle = Load32(base, slot);
    if (Plausible(vehicle) && Load32(base, vehicle + 0x2DD0) == 0 &&
        Load32(base, vehicle + 0x2D94) == deferred) {
      return true;
    }
  }
  return false;
}

void MarkHeadlightAmountDirty(uint8_t* base, uint32_t constants) {
  constexpr uint64_t bit = gauges::PixelConstantDirtyBit(gauges::kDynamicLights2);
  static_assert(bit >> 32, "the bit is in the big-endian high word at +8");
  Store32(base, constants + 8, Load32(base, constants + 8) | uint32_t(bit >> 32));
}
}  // namespace

// Game thread, every car and frame: this = the car's deferred presentation, f1 = the amount.
extern "C" REX_FUNC(SetHeadLightAmount) {
  if (REXCVAR_GET(cockpit_gauges_lit)) {
    const uint32_t deferred = ctx.r3.u32;
    const uint32_t presentation = Plausible(deferred) ? Load32(base, deferred + 4) : 0;
    if (presentation && LocalPlayersCar(base, deferred)) {
      local_presentation.store(presentation, std::memory_order_relaxed);
    } else if (presentation) {
      // Another car now has the address a replaced local car had.
      uint32_t expected = presentation;
      local_presentation.compare_exchange_strong(expected, 0, std::memory_order_relaxed);
    }
  }
  OriginalSetHeadLightAmount(ctx, base);
}

// Render thread: r3 = the car's presentation, r6 = the model group.
extern "C" REX_FUNC(DrawCarModel) {
  const uint32_t presentation = ctx.r3.u32;
  if (!gauges::IsCockpitGroup(ctx.r6.u32) || !REXCVAR_GET(cockpit_gauges_lit) || !presentation ||
      presentation != local_presentation.load(std::memory_order_relaxed)) {
    OriginalDrawCarModel(ctx, base);
    return;
  }
  drawing_cockpit = true;
  OriginalDrawCarModel(ctx, base);
  drawing_cockpit = false;
}

// r3 = the shader constant shadow the model is drawn with, r4 = the model.
extern "C" REX_FUNC(DrawModelNow) {
  FORZA_D3D_TRACE_SCOPE(DrawModelNow);  // RunCommandBuffer in the D3D trace
  if (!drawing_cockpit) {
    OriginalDrawModelNow(ctx, base);
    return;
  }
  const uint32_t constants = ctx.r3.u32;
  const uint32_t amount = constants + gauges::kHeadlightAmountOffset;
  const uint32_t game = Load32(base, amount);
  float value;
  std::memcpy(&value, &game, sizeof(value));
  value = gauges::LitHeadlightAmount(value);
  uint32_t lit;
  std::memcpy(&lit, &value, sizeof(lit));
  Store32(base, amount, lit);
  MarkHeadlightAmountDirty(base, constants);
  OriginalDrawModelNow(ctx, base);
  Store32(base, amount, game);
  MarkHeadlightAmountDirty(base, constants);
}
