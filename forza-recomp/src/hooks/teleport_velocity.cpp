// forza - a teleport no longer leaves the car's old speed in its reported velocity (#349).
//
// The vehicle teleport sub_824B87A8 (r3 the vehicle, r4 the orientation, v1 the position;
// fast travel, the car reset, an event's start) moves the car object (vehicle+0x2D90) with
// its own setters: the position (vfunc 80), the velocity (vfunc 84, sub_82D2A788, here
// always zero), the angular velocity (vfunc 90) and the orientation (vfunc 94). The velocity
// setter writes the physics state (car-0xA10) only. The car's velocity getter (vfunc 83,
// sub_82D1CE18) reads the copy that the car's physics step makes of it (car+0xB10), so from
// the teleport to the car's next physics step (a fast travel keeps the physics still behind
// its loading screen) the car still reports the speed it had before.
//
// A speed trap (sub_828BB140) takes its speed from that getter when the car enters it.
// Fast travelling onto a trap recorded the speed the car had before the fast travel, while
// it stood still at the trap, and sent it to the online leaderboard.
//
// After the teleport the copy is refreshed from the velocity the teleport set, as the next
// physics step would. Cars whose vfuncs are not these two are left alone.

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>

extern "C" REX_FUNC(__imp__sub_824B87A8);

namespace {

constexpr uint32_t kVehicleCar = 0x2D90;        // the car object of a vehicle
constexpr uint32_t kGetVelocitySlot = 83 * 4;
constexpr uint32_t kSetVelocitySlot = 84 * 4;
constexpr uint32_t kGetVelocity = FORZA_ADDR(0x82D1CE18);  // reads car+0xB10
constexpr uint32_t kSetVelocity = FORZA_ADDR(0x82D2A788);  // writes car-0xA10
constexpr int32_t kStateVelocity = -0xA10;
constexpr int32_t kReportedVelocity = 0xB10;

uint32_t Load32(const uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

float LoadFloat(const uint8_t* base, uint32_t address) {
  const uint32_t v = Load32(base, address);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}

float Speed(const uint8_t* base, uint32_t address) {
  const float x = LoadFloat(base, address), y = LoadFloat(base, address + 4),
              z = LoadFloat(base, address + 8);
  return std::sqrt(x * x + y * y + z * z);
}

bool Valid(uint32_t address) { return address >= 0x00100000 && address < 0xF0000000; }

void RefreshReportedVelocity(uint8_t* base, uint32_t vehicle) {
  if (!Valid(vehicle)) return;
  const uint32_t car = Load32(base, vehicle + kVehicleCar);
  if (!Valid(car)) return;
  const uint32_t vtable = Load32(base, car);
  if (Load32(base, vtable + kGetVelocitySlot) != kGetVelocity ||
      Load32(base, vtable + kSetVelocitySlot) != kSetVelocity) {
    return;
  }
  const uint32_t state = car + uint32_t(kStateVelocity);
  const uint32_t reported = car + uint32_t(kReportedVelocity);
  const float before = Speed(base, reported), after = Speed(base, state);
  std::memcpy(forza::GuestPtr(base, reported), forza::GuestPtr(base, state), 16);
  // The speed the car would have reported until its next physics step, for the log.
  static std::atomic<int> reports{0};
  if (std::fabs(before - after) > 1.0f && reports.fetch_add(1) < 50) {
    REXLOG_INFO("TELEPORT: car {:08X} moved; its reported speed {:.1f} km/h is now {:.1f} km/h",
                car, before * 3.6f, after * 3.6f);
  }
}

}  // namespace

namespace forza::fm4_track {
void OnTeleport(PPCContext& ctx);  // fm4_track.cpp: the dev spawn on an FM4 circuit
}

extern "C" REX_FUNC(sub_824B87A8) {
  const uint32_t vehicle = ctx.r3.u32;
  forza::fm4_track::OnTeleport(ctx);
  __imp__sub_824B87A8(ctx, base);
  RefreshReportedVelocity(base, vehicle);
}
