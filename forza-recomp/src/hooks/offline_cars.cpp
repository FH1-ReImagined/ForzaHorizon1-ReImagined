// Standalone native garage lifecycle for a car selector opened from pause.
// tu_address_map: TU4 addresses
#include "../platform/offline_cars.h"

#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
#include <rex/logging.h>
#include <initializer_list>

extern "C" REX_FUNC(__imp__sub_827683D0);
extern "C" REX_FUNC(__imp__sub_827C3620);
extern "C" REX_FUNC(__imp__sub_82789108);

namespace {
uint32_t CurrentCar(const PPCContext& context, uint8_t* base,
                    uint32_t screen) {
  auto ctx = context;
  ctx.r3.u64 = screen;
  rex::runtime::ResolveIndirectFunction(0x82753318)(ctx, base);
  if (!ctx.r3.u32) return UINT32_MAX;
  rex::runtime::ResolveIndirectFunction(0x825979B8)(ctx, base);
  return ctx.r3.u32;
}
}

extern "C" REX_FUNC(sub_827683D0) {
  using namespace forza::offline_cars;
  const uint32_t screen = ctx.r3.u32;
  const bool adapt = pending && ReadWord(base, screen) == 0x8205F254 &&
                     ReadWord(base, screen + 0x10C) == pending_context;
  __imp__sub_827683D0(ctx, base);
  if (!adapt) return;
  pending = false;
  active_screen = screen;
  // My Cars and its base both acquire the current state's "done" signal.
  // In pause that signal resumes driving. Release these two references to
  // select the native standalone branches instead: cancel at 8274E9B4 and
  // Get In Car at 827BCDF4 pop the selector through the screen manager.
  // 824CEAB0 resets the reference as well as releasing its ownership.
  auto call = ctx;
  for (const uint32_t offset : {0x298u, 0x450u}) {
    call.r3.u64 = screen + offset;
    rex::runtime::ResolveIndirectFunction(0x824CEAB0)(call, base);
  }
  REXLOG_INFO("Pause cars: bound native offline garage {:08X}", screen);
}

extern "C" REX_FUNC(sub_82789108) {
  using namespace forza::offline_cars;
  const uint32_t screen = ctx.r3.u32;
  const bool standalone = active_screen && screen == active_screen;
  const uint32_t before = standalone ? CurrentCar(ctx, base, screen) : UINT32_MAX;
  __imp__sub_82789108(ctx, base);
  if (!standalone) return;
  const uint32_t after = CurrentCar(ctx, base, screen);
  if (before != UINT32_MAX && after != UINT32_MAX && before != after) {
    // Hold this intent outside guest memory. Only native Resume may publish
    // the reload byte; map/photo/online/quit exits must never inherit it.
    reload_intent.Changed(ReadWord(base, screen + 0x10C));
    REXLOG_INFO("Pause cars: selected garage car {} -> {}", before, after);
  }
}

extern "C" REX_FUNC(sub_827C3620) {
  using namespace forza::offline_cars;
  if (ctx.r3.u32 == active_screen) active_screen = 0;
  __imp__sub_827C3620(ctx, base);
}
#endif
