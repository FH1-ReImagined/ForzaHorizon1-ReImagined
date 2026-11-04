// forza - FM4 circuits inside FH1 (#648, "The Festival"): a dev spawn for the spike.
// tu_address_map: TU4 addresses
//
// tools/fm4_track_mod.py --free-roam makes FH1's free roam load an FM4 circuit in place of
// Colorado. The car then starts at its saved Colorado position, in the void.
// FORZA_TRACK_SPAWN=x,y,z (world metres, y up; Suzuka's flying start is 487.7,606,22.5)
// moves the player car's teleports there (the spawn into free roam, a car reset), and logs
// every vehicle teleport. Called from the teleport hook in teleport_velocity.cpp.

#include "../platform/guest_addr.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>

#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace forza::fm4_track {

void OnTeleport(PPCContext& ctx) {
#if FORZA_TU4
  static const struct Spawn {
    bool on = false;
    float x = 0, y = 0, z = 0;
  } spawn = [] {
    Spawn s;
    const char* p = std::getenv("FORZA_TRACK_SPAWN");
    s.on = p && std::sscanf(p, "%f,%f,%f", &s.x, &s.y, &s.z) == 3;
    return s;
  }();
  if (!spawn.on) return;
  // lvx byte-reverses the vector: x is the top lane (world_challenge.cpp).
  REXLOG_INFO("FM4TRACK: teleport of vehicle {:08X} to {:.1f},{:.1f},{:.1f}", ctx.r3.u32,
              ctx.v1.f32[3], ctx.v1.f32[2], ctx.v1.f32[1]);
  // Only the player's car: the first car teleported (the spawn into free roam) is it. The
  // free-roam AI drivers are teleported too, to their own places.
  static std::atomic<uint32_t> player{0};
  uint32_t expected = 0;
  if (!player.compare_exchange_strong(expected, ctx.r3.u32) && expected != ctx.r3.u32) return;
  ctx.v1.f32[3] = spawn.x;
  ctx.v1.f32[2] = spawn.y;
  ctx.v1.f32[1] = spawn.z;
#else
  (void)ctx;
#endif
}

}  // namespace forza::fm4_track
