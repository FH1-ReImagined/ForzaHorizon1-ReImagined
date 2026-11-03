// Windows probe of the production world hook, Poll and live guest-world guards.
// Notifications are injected unless checking the real SDK export. No OS/USB test.
#include <windows.h>
#include "../src/platform/auto_pause.cpp"
#include "../src/hooks/telemetry.cpp"
#include <cassert>
#include <cstdio>
#ifdef FORZA_AUTO_PAUSE_SDK_BRIDGE
#include "../src/platform/sdk_import.cpp"
#endif

namespace {
uint32_t sequence = 0;
bool xr_running = false;
uint32_t Sequence(uint32_t) { return sequence; }
unsigned world_calls = 0, substep_calls = 0, physics_calls = 0;
}
namespace forza::xr {
bool SessionRunning() { return xr_running; }
}
namespace forza::discord {
void WorldFrame(const PPCContext&, uint8_t*) {}
}
namespace forza::telemetry {
void Physics(uint8_t*) { ++physics_calls; }
}
extern "C" REX_FUNC(__imp__sub_82D4DE30) { ++substep_calls; }
extern "C" REX_FUNC(__imp__sub_823E40D8) {
  ++world_calls;
  // Both consumers must see the current world before the original update runs.
  assert(forza::auto_pause::world.load() == ctx.r3.u32);
  assert(forza::camera::camera_world.load() == ctx.r3.u32);
  sub_82D4DE30(ctx, base);
}
#ifndef FORZA_AUTO_PAUSE_SDK_BRIDGE
namespace forza {
void* SdkSymbol(const char* name) {
  assert(std::strcmp(name, "ForzaInputDisconnectSequence") == 0);
#ifdef FORZA_AUTO_PAUSE_MISSING_BRIDGE
  return nullptr;  // deterministic old-runtime compatibility case, in a fresh process
#else
  return reinterpret_cast<void*>(Sequence);
#endif
}
}
#endif
int main() {
  using namespace forza;
  auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x110000,
      MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
  assert(base);
  auto* world = base + 0x100000;
  world[0x0d] = 1; world[0x28] = 0x3f; world[0x29] = 0x80;  // loaded, time scale 1
  // Exercise the actual merge-conflict site and both TU address mappings. A new
  // world must replace both cached addresses; do not feed auto_pause::World directly.
  PPCContext ctx{};
  for (uint32_t address : {0x101000u, 0x100000u}) {
    ctx.r3.u32 = address;
    sub_823E40D8(ctx, base);
    assert(auto_pause::world.load() == address);
    assert(camera::camera_world.load() == address);
  }
  assert(world_calls == 2 && substep_calls == 2 && physics_calls == 2);
  telemetry::WorldFrame frame; frame.stepped = true;
  for (uint64_t ms : {1u, 300u, 600u}) telemetry::Player().Frame(ms, frame);
  assert(telemetry::Player().State() == telemetry::Drive::kDriving);
  REXCVAR_SET(forza_pause_on_focus_loss, true);
  REXCVAR_SET(forza_pause_on_controller_disconnect, true);
  assert(!auto_pause::Poll(base, 0, 15));
#if defined(FORZA_AUTO_PAUSE_SDK_BRIDGE) || defined(FORZA_AUTO_PAUSE_MISSING_BRIDGE)
#ifdef FORZA_AUTO_PAUSE_SDK_BRIDGE
  // Current main pins a runtime with this export. Enforce that contract through
  // production symbol discovery, without inventing physical device loss.
  const auto bridge = reinterpret_cast<uint32_t (*)(uint32_t)>(
      SdkSymbol("ForzaInputDisconnectSequence"));
  assert(bridge);
  const auto initial = bridge(15);
  assert(bridge(0) == 0 && bridge(15) == initial);
#else
  assert(!SdkSymbol("ForzaInputDisconnectSequence"));
#endif
  ++sequence;
  assert(!auto_pause::Poll(base, 0, 15));
  ++auto_pause::focus_losses;
  assert(auto_pause::Poll(base, 0, 15));
  auto_pause::Cancel();
  REXCVAR_SET(forza_pause_on_focus_loss, false);
  ++auto_pause::focus_losses;
  ++sequence;
  assert(!auto_pause::Poll(base, 0, 15));
  REXCVAR_SET(forza_pause_on_focus_loss, true);
  xr_running = true;
  ++auto_pause::focus_losses;
  assert(!auto_pause::Poll(base, 0, 15));
  xr_running = false;
  assert(!auto_pause::Poll(base, 0, 15));
  auto_pause::World(0);
  VirtualFree(base, 0, MEM_RELEASE);
#ifdef FORZA_AUTO_PAUSE_SDK_BRIDGE
  assert(bridge(15) == initial);
  std::fprintf(stderr, "SDK auto-pause: actual export, focus and XR guards pass\n");
#else
  std::fprintf(stderr, "missing bridge auto-pause: injected absence, focus fallback and XR guards pass\n");
#endif
  return 0;
#endif
  ++auto_pause::focus_losses;
  assert(auto_pause::Poll(base, 0, 15));
  xr_running = true;
  assert(!auto_pause::Poll(base, 0, 15));  // VR cancels a focus pulse already in flight
  ++auto_pause::focus_losses;
  assert(!auto_pause::Poll(base, 0, 15));  // VR consumes focus loss without pausing
  ++sequence;
  assert(auto_pause::Poll(base, 0, 15));  // physical disconnect remains active in VR
  auto_pause::Cancel();
  xr_running = false;
  assert(!auto_pause::Poll(base, 0, 15));  // leaving VR does not replay its focus edge
  ++auto_pause::focus_losses;
  assert(auto_pause::Poll(base, 0, 15));
  world[0x0c] = 1;  // game has paused, debounced gate still says driving
  assert(!auto_pause::Poll(base, 0, 15));
  ++sequence;
  assert(!auto_pause::Poll(base, 0, 15));  // a new event cannot resume the paused game
  world[0x0c] = 0;
  assert(!auto_pause::Poll(base, 0, 15));  // returning/reconnecting is not a request
  ++sequence; assert(auto_pause::Poll(base, 0, 15));
  assert(!auto_pause::Poll(base, 0x10, 15));  // manual START takes priority
  assert(!auto_pause::Poll(base, 0, 15));
  world[0x28] = 0xbf;  // rewind
  ++auto_pause::focus_losses; assert(!auto_pause::Poll(base, 0, 15));
  world[0x28] = 0x3f; world[0x0d] = 0;  // loading
  ++sequence; assert(!auto_pause::Poll(base, 0, 15));
  world[0x0d] = 1;
  DWORD previous;
  assert(VirtualProtect(world, 0x1000, PAGE_NOACCESS, &previous));
  ++sequence; assert(!auto_pause::Poll(base, 0, 15));  // stale/unmapped world is safe
  assert(VirtualProtect(world, 0x1000, previous, &previous));
  assert(!auto_pause::Poll(base, 0, 15));
  auto_pause::World(0);
  VirtualFree(base, 0, MEM_RELEASE);
  std::fprintf(stderr, "native auto-pause: live paused/loading/rewind/unmapped guards, world hook and manual resume pass\n");
}
