// tu_address_map: TU4 addresses
#include "../platform/minimap_camera.h"
#include "../platform/minimap_heading.h"
#include "../platform/camera_fov.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/xr.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <algorithm>
#include <cstring>
#include <cstdlib>

REXCVAR_DEFINE_BOOL(forza_minimap_camera, false, "Display/HUD",
    "Rotate the gameplay minimap with the camera instead of the car. TU4 only.");
REXCVAR_DECLARE(bool, vr_head_pose_test);

namespace forza::minimap {
#if FORZA_TU4
namespace {
uint8_t* Memory(uint8_t* base, uint32_t address, size_t count) {
  // The native map pass owns these objects in the fixed guest mapping. Match
  // camera_fov's range guard; OS protection queries can parse /proc/self/maps.
  if (!base || address < 0x00100000 || address >= 0xF0000000 ||
      count > 0xF0000000 - address) return nullptr;
  return GuestPtr(base, address);
}
uint32_t Word(uint8_t* base, uint32_t address) {
  const auto* p = Memory(base, address, 4);
  return p ? camera::Read32(p) : 0;
}
void Float(uint8_t* p, float value) {
  const auto bits = std::bit_cast<uint32_t>(value);
  p[0] = uint8_t(bits >> 24); p[1] = uint8_t(bits >> 16);
  p[2] = uint8_t(bits >> 8); p[3] = uint8_t(bits);
}
thread_local uint32_t previous_record = 0;
}
MapScope::MapScope(const PPCContext& context, uint8_t* base) {
  if (!REXCVAR_GET(forza_minimap_camera) && !previous_record) return;
  if (context.r4.u32 != 0 || context.r3.u32 != Word(base, 0x833F0410)) return;
  const auto service = context.r3.u32;
  if (!service) return;
  const auto begin = Word(base, service + 0x74), end = Word(base, service + 0x78);
  if (!begin || end < begin || end - begin > 256 || (end - begin) % 4) return;
  uint32_t record = 0;
  for (auto entry = begin; entry < end; entry += 4) {
    const auto candidate = Word(base, entry);
    if (!Memory(base, candidate, 0x28) || Word(base, candidate) != 0) continue;
    if (record) return; // ambiguous HUD ownership
    record = candidate;
  }
  if (!record || !Memory(base, record, 0x28) || !GuestPtr(base, record + 0x1C)[0]) return;
  const auto world = camera::camera_world.load(std::memory_order_relaxed);
  const auto* world_memory = Memory(base, world, 0x2C);
  const auto sample = REXCVAR_GET(forza_minimap_camera) && !REXCVAR_GET(vr_head_pose_test) &&
      !xr::CockpitActive() && camera::LiveCameraWorld(world_memory)
      ? heading.Read(world, NowMs()) : std::nullopt;
  if (!sample && previous_record != record) return;
  const auto map = Word(base, record + 0x20), map_camera = Word(base, record + 0x24);
  if (!Memory(base, map, 8) || !Word(base, map + 4) || !Memory(base, map_camera, 0x150)) return;
  base_ = base; camera_ = map_camera;
  std::copy_n(GuestPtr(base, camera_ + 0x20), direction_.size(), direction_.begin());
  std::copy_n(GuestPtr(base, camera_ + 0x50), matrices_.size(), matrices_.begin());
  if (sample) {
    static const bool trace = std::getenv("FORZA_MINIMAP_TRACE") != nullptr;
    static thread_local unsigned samples = 0;
    static thread_local Heading last{100, 100};
    if (trace && samples < 64 && std::hypot(sample->x - last.x, sample->z - last.z) > 0.01f) {
      REXLOG_INFO("Minimap camera: record {:08X} camera {:08X} heading {:.3f} {:.3f}; native {:.3f} {:.3f}",
          record, camera_, sample->x, sample->z, camera::ReadFloat(direction_.data()),
          camera::ReadFloat(direction_.data() + 8));
      last = *sample; ++samples;
    }
    Float(GuestPtr(base, camera_ + 0x20), sample->x);
    Float(GuestPtr(base, camera_ + 0x24), 0);
    Float(GuestPtr(base, camera_ + 0x28), sample->z);
  }
  // The same native preparation used by Register826E2E20 and the pause map.
  PPCContext call = context;
  call.r3.u64 = camera_;
  rex::runtime::ResolveIndirectFunction(0x82DA2388)(call, base);
  call = context; call.r3.u64 = map; call.r4.u64 = camera_;
  rex::runtime::ResolveIndirectFunction(0x82DA4A80)(call, base);
  std::copy_n(GuestPtr(base, camera_ + 0x20), applied_direction_.size(), applied_direction_.begin());
  std::copy_n(GuestPtr(base, camera_ + 0x50), applied_matrices_.size(), applied_matrices_.begin());
  GuestPtr(base, record + 0x1D)[0] = 1;
  previous_record = sample ? record : 0;
}
MapScope::~MapScope() {
  if (!camera_) return;
  if (!Memory(base_, camera_, 0x150) ||
      std::memcmp(GuestPtr(base_, camera_ + 0x20), applied_direction_.data(), applied_direction_.size()) ||
      std::memcmp(GuestPtr(base_, camera_ + 0x50), applied_matrices_.data(), applied_matrices_.size())) return;
  std::copy(direction_.begin(), direction_.end(), GuestPtr(base_, camera_ + 0x20));
  std::copy(matrices_.begin(), matrices_.end(), GuestPtr(base_, camera_ + 0x50));
}
#else
MapScope::MapScope(const PPCContext&, uint8_t*) {}
MapScope::~MapScope() = default;
#endif
} // namespace forza::minimap

#if FORZA_TU4 && !defined(FORZA_NATIVE_PAUSE)
extern "C" REX_FUNC(__imp__sub_826E22A8);
extern "C" REX_FUNC(sub_826E22A8) {
  forza::minimap::MapScope scope(ctx, base);
  __imp__sub_826E22A8(ctx, base);
}
#endif
