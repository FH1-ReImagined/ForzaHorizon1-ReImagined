// forza - Fast Travel Anywhere owned (#424), Title Update 4.
//
// Title Update 4 sold Fast Travel Anywhere in the map for Tokens (a
// CVIPFastTravelConsumeAssetTransaction, confirmed by Turn 10's token service on Xbox LIVE);
// with LIVE gone it can't be bought. Its purchase completion (sub_825D5C00) stores the bool
// "Main/FastTravelAnywhere" = true in the profile's value tree, and two places read it back
// with the path getter sub_82553198:
//   - sub_826E94B8, the unlock check: the map's travel cost (sub_828AC8C0: owned ? 0 : the
//     map's quoted cost, map+444), the purchase offer when travelling (sub_827C9300, event
//     0x9C: opens the offer while not owned) and sub_826F7750;
//   - sub_827A9AC8, the map's button legend, which lists the purchase while not owned.
// With fast_travel_anywhere on (the default), the getter answers that path with true, so every
// reader sees the unlock as bought: travel is free and the offer is gone. Nothing is written to
// the profile or the save, and no Tokens are given or spent: the Token balance and the other
// Token offers (the Treasure Map) stay as they are. Off, the getter is the game's again, and a
// profile that really bought it keeps it. Offline and online alike: no server is asked.
//
// Pinyon Shift (LICENSE-Pinyon-Shift) found the unlock's path, the reader sub_826E94B8 and the
// purchase completion's store (src/dlc_token_unlocks.cpp); it writes the value into the profile.
// This build answers the read instead, so turning the setting off takes nothing from a save.
// tu_address_map: TU4 addresses

#include "../platform/fast_travel_anywhere.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdint>

REXCVAR_DEFINE_BOOL(fast_travel_anywhere, true, "Gameplay",
                    "Own Fast Travel Anywhere (Title Update 4): travel from the map to any road "
                    "for free, as buying it with Tokens did. Gives no Tokens; nothing is saved, "
                    "so turning it off brings the game's own state back");

#if FORZA_TU4
// sub_82553198(value r3, tree r4, path r5) -> r3 the value: a copy of the tree's value at the
// path, empty when the tree has none.
extern "C" REX_FUNC(__imp__sub_82553198);

namespace {
namespace fta = forza::fast_travel_anywhere;

// 0 not checked yet, 1 the executable has the path where it was found, 2 it does not.
std::atomic<int> g_path_state{0};
// What was last logged: 0 nothing, 1 granted, 2 the profile owns it itself, 3 off.
std::atomic<int> g_logged{0};

bool PathFound(uint8_t* base) {
  int state = g_path_state.load(std::memory_order_acquire);
  if (state == 0) {
    state = fta::PathMatches(forza::GuestPtr(base, fta::kPathAddress), sizeof(fta::kPath)) ? 1 : 2;
    g_path_state.store(state, std::memory_order_release);
    if (state == 2)
      REXLOG_WARN("Fast Travel Anywhere: {:08X} is not \"{}\" in this executable; left as the game has it",
                  fta::kPathAddress, fta::kPath);
  }
  return state == 1;
}

void LogOnce(int what) {
  if (g_logged.exchange(what, std::memory_order_acq_rel) == what) return;
  if (what == 1)
    REXLOG_INFO("Fast Travel Anywhere: owned (fast_travel_anywhere; the profile has not bought it)");
  else if (what == 2)
    REXLOG_INFO("Fast Travel Anywhere: owned by the profile (bought)");
  else
    REXLOG_INFO("Fast Travel Anywhere: as the game has it, not owned (fast_travel_anywhere=false)");
}
}  // namespace

extern "C" REX_FUNC(sub_82553198) {
  const uint32_t path = ctx.r5.u32;
  const uint32_t value = ctx.r3.u32;
  __imp__sub_82553198(ctx, base);
  if (path != fta::kPathAddress || !value || !PathFound(base)) return;
  uint8_t* host = forza::GuestPtr(base, value);
  if (fta::Owned(host)) {
    LogOnce(2);
  } else if (!REXCVAR_GET(fast_travel_anywhere)) {
    LogOnce(3);
  } else if (fta::Grant(host)) {
    LogOnce(1);
  }
}
#endif
