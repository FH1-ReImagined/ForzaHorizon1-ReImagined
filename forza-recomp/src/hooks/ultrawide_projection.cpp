// The viewport aspect query feeds both projection and culling. Hooking the result
// keeps off-screen objects visible when ultrawide widens the gameplay camera, and gives the
// VR cockpit its own aspect (the eyes' fields of view, docs/vr.md).
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/ultrawide.h"
#include "../platform/xr.h"
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

namespace {
uint32_t Read32(const uint8_t* p) {
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
}

extern "C" REX_FUNC(__imp__sub_823E22C8);
extern "C" REX_FUNC(sub_823E22C8) {
  const uint32_t viewport = ctx.r3.u32;
  const uint32_t caller = uint32_t(ctx.lr);
  __imp__sub_823E22C8(ctx, base);
  // The original function has already read these fields. Guard address overflow
  // and use GuestPtr so physical aliases retain the runtime's Windows mapping.
  if (viewport > 0xFFFFFFEFu) return;
  const auto* data = forza::GuestPtr(base, viewport);
  const uint32_t width = Read32(data + 8), height = Read32(data + 12);
  ctx.f1.f64 = forza::ultrawide::Projection(caller == FORZA_ADDR(0x829A48F4),
      caller == FORZA_ADDR(0x8263AE18), width, height, ctx.f1.f64);
  // Inside the VR cockpit's posed view (the presentation camera and the game-side frustum of the
  // in-car view): the projection covers the eyes' fields of view, whatever the picture's shape.
  if (const float vr = forza::xr::PosedViewAspect(); vr > 0) ctx.f1.f64 = vr;
}
