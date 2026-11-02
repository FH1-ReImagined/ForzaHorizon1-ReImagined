// Sample after a real physics substep. The outer step also runs while paused;
// sub_82D4DE30 (TU4 82E100B0) is called only by its substep loop. The world update around it
// (sub_823E40D8, TU4 82414068: once a frame while a world is loaded, `this` the main loop's
// game + 4) tells the racing wheel whether the player is driving (platform/driving_gate.h
// PlayerGate, platform/wheel_bridge.cpp) and samples the Discord status details
// (discord_details.cpp).
#include "discord_details.h"
#include "../platform/driving_gate.h"
#include "../platform/camera_fov.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/telemetry.h"
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <atomic>
#include <bit>
#include <cstring>
namespace {
std::atomic<uint32_t> g_substeps{0};
}
extern "C" REX_FUNC(__imp__sub_82D4DE30);
extern "C" REX_FUNC(sub_82D4DE30) {
  __imp__sub_82D4DE30(ctx, base);
  g_substeps.fetch_add(1, std::memory_order_relaxed);
  forza::telemetry::Physics(base);
}
extern "C" REX_FUNC(__imp__sub_823E40D8);
extern "C" REX_FUNC(sub_823E40D8) {
  const uint32_t world = ctx.r3.u32;
  forza::camera::camera_world.store(world, std::memory_order_relaxed);
  const uint32_t substeps = g_substeps.load(std::memory_order_relaxed);
  __imp__sub_823E40D8(ctx, base);
  const uint8_t* w = forza::GuestPtr(base, world);
  uint32_t scale;
  std::memcpy(&scale, w + 0x28, sizeof(scale));
  forza::telemetry::WorldFrame f;
  f.paused = w[0x0C] != 0;
  f.loaded = w[0x0D] != 0;
  f.rewinding = std::bit_cast<float>(std::byteswap(scale)) < 0.0f;
  f.stepped = g_substeps.load(std::memory_order_relaxed) != substeps;
  forza::telemetry::Player().Frame(forza::telemetry::DrivingNowMs(), f);
  forza::discord::WorldFrame(ctx, base);
}
