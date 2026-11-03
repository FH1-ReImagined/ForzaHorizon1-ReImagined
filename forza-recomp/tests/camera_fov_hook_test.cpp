// Compile the actual hook with small SDK interfaces, separately for TU0/TU4.
// This checks dispatch/return behavior, not the SDK's PPCContext layout or VM.
// Match the Windows host target's handling of the portable C runtime API.
#define _CRT_SECURE_NO_WARNINGS
#include "../src/hooks/camera_fov.cpp"
#include <cassert>

namespace {
unsigned original_calls = 0;
constexpr uint32_t kDirector = 0x01004000, kCamera = 0x01000100, kWorld = 0x01006000;
void Word(uint8_t* memory, uint32_t address, uint32_t value) {
  auto* p = rex::memory::GuestPtr(memory, address);
  p[0] = uint8_t(value >> 24); p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8); p[3] = uint8_t(value);
}
void Float(uint8_t* memory, uint32_t address, float value) {
  uint32_t bits;
  std::memcpy(&bits, &value, 4);
  Word(memory, address, bits);
}
bool vr = false, xr_cockpit = false;
}
bool forza::xr::CockpitActive() { return xr_cockpit; }
bool& FLAGS_vr_head_pose_test_storage_() { return vr; }
extern "C" REX_FUNC(__imp__sub_8284EF90) {
  ++original_calls;
  ctx.f1.f64 = forza::camera::ReadFloat(rex::memory::GuestPtr(base, ctx.r3.u32 + 304));
}

int main() {
  using namespace forza::camera;
#if FORZA_TU4
  static_assert(kTables.cockpit == 0x820860FC && kTables.blend == 0x8208576C);
  static_assert(kTables.chase_near == 0x820858D4 && kTables.chase_far == 0x82085954);
  static_assert(kTables.hood == 0x8208607C && kTables.bumper == 0x8208617C);
#else
  static_assert(kTables.cockpit == 0x82075DD4 && kTables.blend == 0x82075444);
  static_assert(kTables.chase_near == 0x820755AC && kTables.chase_far == 0x8207562C);
  static_assert(kTables.hood == 0x82075D54 && kTables.bumper == 0x82075E54);
#endif
  std::array<uint8_t, 0x8000> memory{};
  Word(memory.data(), kDirector + 20, kCamera);
  Float(memory.data(), kDirector + 304, Radians(70)); // 62 base + 8 physics.
  Word(memory.data(), kCamera, kTables.cockpit);
  Float(memory.data(), kCamera + 272, Radians(62));
  rex::memory::GuestPtr(memory.data(), kWorld)[0x0D] = 1;
  Float(memory.data(), kWorld + 0x28, 1);
  camera_world = kWorld;
  FLAGS_forza_cockpit_fov_storage_() = 80;
  const auto pristine = memory;
  // Seven actual call-site return PCs. The hook must not discriminate on LR;
  // even the four consumers missed by the old scoped implementation get 88.
  for (uint32_t caller : {0x82621F9C, 0x82622320, 0x82A58E28, 0x824135B0,
                          0x8262260C, 0x825156B0, 0x824C5084}) {
    PPCContext context;
    context.r3.u32 = kDirector;
    context.lr = caller;
    context.untouched.fill(0xCAFE010203040506);
    const auto before = context;
    const auto calls = original_calls;
    sub_8284EF90(context, memory.data());
    assert(original_calls == calls + 1);
    assert(std::fabs(context.f1.f64 - Radians(88)) < 2e-6);
    assert(context.r3.u32 == before.r3.u32 && context.lr == before.lr);
    assert(context.untouched == before.untouched && memory == pristine);
  }
  auto result = [&] {
    PPCContext context;
    context.r3.u32 = kDirector;
    sub_8284EF90(context, memory.data());
    return context.f1.f64;
  };
  FLAGS_forza_chase_near_fov_storage_() = 70;
  FLAGS_forza_chase_far_fov_storage_() = 75;
  FLAGS_forza_hood_fov_storage_() = 85;
  FLAGS_forza_bumper_fov_storage_() = 90;
  const std::array<uint32_t, 5> tables{kTables.chase_near, kTables.chase_far,
      kTables.cockpit, kTables.hood, kTables.bumper};
  const std::array<float, 5> bases{48.5f, 58, 62, 66, 53};
  const std::array<float, 5> speed{22, 22, 8, 10, 23};
  const std::array<float, 5> preferences{70, 75, 80, 85, 90};
  for (size_t i = 0; i < tables.size(); ++i) {
    Word(memory.data(), kCamera, tables[i]);
    Float(memory.data(), kCamera + 272, Radians(bases[i]));
    Word(memory.data(), kCamera + 192, 0x01007000);
    Float(memory.data(), 0x01007000 + 28, bases[i]);
    Float(memory.data(), kDirector + 304, Radians(bases[i] + speed[i]));
    const auto snapshot = memory;
    assert(std::fabs(result() - Radians(preferences[i] + speed[i])) < 2e-6);
    assert(memory == snapshot);
  }
  FLAGS_forza_chase_near_fov_storage_() = FLAGS_forza_chase_far_fov_storage_() = 0;
  FLAGS_forza_hood_fov_storage_() = FLAGS_forza_bumper_fov_storage_() = 0;
  Word(memory.data(), kCamera, kTables.cockpit);
  Float(memory.data(), kCamera + 272, Radians(62));
  Float(memory.data(), kDirector + 304, Radians(70));
  // Exercise the production hook's blend classification and correction too.
  constexpr uint32_t blend = 0x01000500, photo = 0x01000700;
  Word(memory.data(), photo, 0xDEADBEEF);
  Word(memory.data(), blend, kTables.blend);
  Word(memory.data(), blend + 192, photo);
  Word(memory.data(), blend + 196, kCamera);
  Float(memory.data(), blend + 200, 0.5f);
  Word(memory.data(), kDirector + 20, blend);
  Float(memory.data(), kDirector + 304, Radians(63));
  assert(std::fabs(result() - Radians(72)) < 2e-6);
  Word(memory.data(), kDirector + 20, kCamera);
  Float(memory.data(), kDirector + 304, Radians(70));
  vr = true;
  assert(result() == Radians(70));
  vr = false;
  xr_cockpit = true;
  assert(result() == Radians(70));
  xr_cockpit = false;
  assert(std::fabs(result() - Radians(88)) < 2e-6);
  rex::memory::GuestPtr(memory.data(), kWorld)[0x0C] = 1;
  assert(result() == Radians(70));
  rex::memory::GuestPtr(memory.data(), kWorld)[0x0C] = 0;
  FLAGS_forza_cockpit_fov_storage_() = 0;
  assert(result() == Radians(70));
  // Unknown class with the same finished FOV remains untouched.
  FLAGS_forza_cockpit_fov_storage_() = 80;
  Word(memory.data(), kCamera, FORZA_ADDR(0x82075CD4)); // Separate, unused CCamCockpit.
  assert(result() == Radians(70));
}
