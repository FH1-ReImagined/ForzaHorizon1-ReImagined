// Native TU4 garage entry for the pause-menu adapter.
// tu_address_map: TU4 addresses
#pragma once

#include "guest_addr.h"
#include "guest_memory.h"
#include "pause_car_access.h"
#include "native_pause_pages.h"
#include "../kernel/live.h"
#include <rex/ppc/func.h>
#include <rex/ppc/context.h>
#include <cstdint>

namespace forza::offline_cars {

// 0xA1 is the BASE game's My Cars ID. TU4 moved CMyCars to 0xAF:
// 8274131C stores factory 827179E0 at manager+57C (8*AF+4), and
// CShowGarageScreen's constructor 82A0F3C0 passes AF to 82A020D0.
// The TU4 A1 factory is 82717758, CMarketplaceCarSelect (vtable 8205F0EC).
inline constexpr uint32_t kMyCarsScreen = 0xAF;

// Only a request made through Open may adapt a My Cars instance. The hooks
// leave the festival's existing garage and every other car selector intact.
inline bool pending = false;
inline uint32_t pending_context = 0;
inline uint32_t active_screen = 0;
inline pause_car_access::ReloadIntent reload_intent;

inline uint32_t ReadWord(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 |
         uint32_t(p[2]) << 8 | p[3];
}

inline bool Available(uint8_t* base, uint32_t parent_screen) {
#if FORZA_TU4
  if (!base || !parent_screen || ReadWord(base, parent_screen) != 0x82061F94) return false;
  uint32_t presence = 0, region = 0;
  const bool known = LiveLocalActivity(presence, region);
  return pause_car_access::Allowed(known, presence, region, NetworkOn(),
                                   LiveCurrentSession().has_value());
#else
  return false;
#endif
}

inline void ClearReload(uint8_t* base) {
  reload_intent.Clear();
#if FORZA_TU4
  if (base) GuestPtr(base, 0x833F6CA4)[0] = 0;
#endif
}
inline void BeforeTransition(uint8_t* base, menu::PauseAction action) {
  if (action != menu::PauseAction::ChangeCar) ClearReload(base);
}
inline void PrepareResume(uint8_t* base, uint32_t screen) {
#if FORZA_TU4
  const auto owner = ReadWord(base, screen + 0x10C);
  GuestPtr(base, 0x833F6CA4)[0] = reload_intent.Resume(owner, Available(base, screen)) ? 1 : 0;
#endif
}

// Uses the game's screen manager and caller context, exactly as the native
// CCarSelect entry at 8274EA04 does. No fabricated profile, garage list, or
// online entitlement is required: CMyCars loads the local garage through
// 8278B8A8 -> 82753318 and owns its normal selection/confirmation dialogs.
// The caller retains responsibility for restoring its originating pause tab.
// True means the screen-manager request was dispatched, not that loading has
// completed or that the player selected a car.
inline bool Open(const PPCContext& context, uint8_t* base,
                 uint32_t parent_screen) {
#if FORZA_TU4
  if (!Available(base, parent_screen)) return false;
  const uint32_t parent_context = ReadWord(base, parent_screen + 0x10C);
  if (!parent_context) return false;
  auto ctx = context;
  rex::runtime::ResolveIndirectFunction(0x824A3770)(ctx, base);
  const uint32_t manager = ctx.r3.u32;
  if (!manager) return false;
  const uint32_t vtable = ReadWord(base, manager);
  if (!vtable) return false;
  const uint32_t open = ReadWord(base, vtable + 0x4C);
  if (!open) return false;
  ctx.r3.u64 = manager;
  ctx.r4.u64 = parent_context;
  ctx.r5.u64 = kMyCarsScreen;
  ctx.r6.u64 = 0;
  ctx.r7.s64 = -1;
  ctx.r8.u64 = 0;
  pending_context = ctx.r4.u32;
  pending = true;
  rex::runtime::ResolveIndirectFunction(open)(ctx, base);
  return true;
#else
  return false;
#endif
}

} // namespace forza::offline_cars
