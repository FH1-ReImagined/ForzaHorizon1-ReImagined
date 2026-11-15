// forza - the game's emergency zone loads write past their list (#902).
//
// When the zones the track presentation wants live are not streamed in time, its emergency path
// asks for them at once: CTrackPresentation::ManageEmergencyPVSZLoading (TU4 sub_82E27480, named
// from the FH2 prototype's symbols) walks m_ZonesToLoad (14 entries: the two zone windows' 7
// each) and, for every zone there that is not streamed yet, asks the extra-file streaming for
// its file and appends the zone to m_EmergencyPVSZList, then counts it in m_NumEmergencyPVSZ.
// The list has 7 entries (+0xD994), the count is the word after it (+0xD9B0), and the first zone
// window follows (m_ZoneWindow[0] at +0xD9C0). Nothing checks the count against the 7: the list
// is emptied only when every zone it holds is loaded, so with more than seven zones missing
// at once, the eighth append wrote a zone number over the count, and the next appends and the
// list's removals (each one copies the list's last entry over the one it drops) wrote far-off
// memory over the zone windows.
//
// On a converted FM4 circuit at speed (Nordschleife after sector 13, #902) the second window's
// zones far ahead are all new at once: the render thread then faulted in CTrackPresentation::
// ProcessDirty on a zone window full of 0xFF (the player's log), or in this function reading the
// list with a count of 0x3F800000 (a local run at the same place). Colorado's larger zones
// rarely want that many at once.
//
// The hook (hints_tu4/emergency_zones.toml) runs where the function is about to ask for a zone
// and append it: with the list full (or its count out of range), that zone is left out of the
// emergency path. Not marked as unfinished, it does not hold the emergency open: once the
// listed zones are in, the list empties as it does, and the game's normal streaming (AddNew,
// which waits while the emergency is open) asks for the rest. The zones are taken in
// m_ZonesToLoad's order, the camera's window first. FORZA_EMERGENCY_ZONES_UNCAPPED=1 keeps the
// game's own behaviour and only logs the appends past the seventh (a check that the cap is
// what changes a run).

#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace {

constexpr int32_t kListSize = 7;  // CTrackPresentation::m_EmergencyPVSZList[7]

uint32_t Load32(const uint8_t* p) {
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

std::atomic<uint64_t> left_out{0};

}  // namespace

static_assert(offsetof(PPCContext, r3) == 0, "hooks recover ctx from &ctx.r3");

// Before 0x82E27590 (the request of a zone that is not streamed): r23 holds the address of
// m_NumEmergencyPVSZ, r30 the zone's entry in m_ZonesToLoad, r31 the track presentation.
// True: the zone is left out (the hint jumps to the loop's next zone, 0x82E27614).
bool ForzaEmergencyZoneListFull(PPCRegister& r3) {
  const PPCContext& ctx = *reinterpret_cast<const PPCContext*>(&r3);
  uint8_t* base = rex::system::kernel_state()->memory()->virtual_membase();
  const int32_t count = int32_t(Load32(forza::GuestPtr(base, ctx.r23.u32)));
  if (count >= 0 && count < kListSize) return false;
  static const bool uncapped = [] {
    const char* v = std::getenv("FORZA_EMERGENCY_ZONES_UNCAPPED");
    return v && *v && *v != '0';
  }();
  const uint32_t zone = Load32(forza::GuestPtr(base, ctx.r30.u32));
  const uint64_t n = ++left_out;
  if (n <= 5 || (n & (n - 1)) == 0) {
    REXLOG_WARN("ZONES: emergency zone list full ({} of {}): zone {} {} ({} so far)", count, kListSize,
                zone, uncapped ? "appended past it (FORZA_EMERGENCY_ZONES_UNCAPPED)"
                               : "left to the normal streaming",
                n);
  }
  return !uncapped;
}
