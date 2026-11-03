#include "../platform/camera_fov.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/xr.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <cstdlib>

REXCVAR_DEFINE_INT32(forza_cockpit_fov, 0, "Display",
                    "Cockpit base vertical FOV (40-90 degrees); 0 keeps stock. Speed widening is preserved.");
REXCVAR_DEFINE_INT32(forza_chase_near_fov, 0, "Display", "Chase near base vertical FOV (40-90 degrees); 0 keeps stock.");
REXCVAR_DEFINE_INT32(forza_chase_far_fov, 0, "Display", "Chase far base vertical FOV (40-90 degrees); 0 keeps stock.");
REXCVAR_DEFINE_INT32(forza_hood_fov, 0, "Display", "Hood base vertical FOV (40-90 degrees); 0 keeps stock.");
REXCVAR_DEFINE_INT32(forza_bumper_fov, 0, "Display", "Bumper base vertical FOV (40-90 degrees); 0 keeps stock.");
REXCVAR_DECLARE(bool, vr_head_pose_test);

namespace {
struct Memory {
  uint8_t* base;
  const uint8_t* operator()(uint32_t address, size_t count) const {
    if (address < 0x00100000 || address >= 0xF0000000 || count > 0xF0000000 - address) return nullptr;
    return forza::GuestPtr(base, address);
  }
};
constexpr forza::camera::Vtables kTables{
    FORZA_ADDR(0x820755AC), FORZA_ADDR(0x8207562C), FORZA_ADDR(0x82075DD4),
    FORZA_ADDR(0x82075D54), FORZA_ADDR(0x82075E54), FORZA_ADDR(0x82075444)};
}

// All seven call sites get one consistent FOV, including world and post effects.
// The original accessor reads director+304 after base FOV and CameraPhysics.
extern "C" REX_FUNC(__imp__sub_8284EF90);
extern "C" REX_FUNC(sub_8284EF90) {
  const uint32_t director = ctx.r3.u32;
  __imp__sub_8284EF90(ctx, base);
  const forza::camera::Settings settings{REXCVAR_GET(forza_chase_near_fov),
      REXCVAR_GET(forza_chase_far_fov), REXCVAR_GET(forza_cockpit_fov),
      REXCVAR_GET(forza_hood_fov), REXCVAR_GET(forza_bumper_fov)};
  static const bool trace = std::getenv("FORZA_FOV_TRACE") != nullptr;
  if ((settings.Stock() && !trace) || REXCVAR_GET(vr_head_pose_test) || forza::xr::CockpitActive()) return;
  const Memory memory{base};
  const auto world = forza::camera::camera_world.load(std::memory_order_relaxed);
  if (!forza::camera::LiveCameraWorld(memory(world, 0x2C)) || director > UINT32_MAX - 20) return;
  const auto* active = memory(director + 20, 4);
  if (!active) return;
  const float original = float(ctx.f1.f64);
  const auto reader = [&](uint32_t address) { return forza::camera::ReadNode(address, memory, kTables); };
  const float adjusted = forza::camera::AdjustedFov(original, forza::camera::Read32(active), settings, reader);
  if (std::isfinite(adjusted) && adjusted != original) ctx.f1.f64 = double(adjusted);
  // Opt-in, bounded evidence for the caller coverage, real blends and speed
  // widening. Normal play does not perform these extra reads or log samples.
  if (trace) {
    static std::atomic<uint32_t> calls{0}, blends{0};
    const auto node = reader(forza::camera::Read32(active));
    const auto call = calls.fetch_add(1, std::memory_order_relaxed);
    const bool blend = node && node->kind == forza::camera::Kind::kBlend;
    if (call < 64 || (call % 420 == 0 && call < 420000) ||
        (blend && blends.fetch_add(1, std::memory_order_relaxed) < 4096))
      REXLOG_INFO("Camera FOV trace: caller {:08X} director {:08X} camera {:08X} kind {} "
                  "t {:.4f} base {:.4f} original {:.4f} result {:.4f} degrees",
          uint32_t(ctx.lr), director, forza::camera::Read32(active), node ? int(node->kind) : -1,
          node ? node->t : 0.0f, node ? node->base * (180.0f / forza::camera::kPi) : 0.0f,
          original * (180.0f / forza::camera::kPi), adjusted * (180.0f / forza::camera::kPi));
  }
}
