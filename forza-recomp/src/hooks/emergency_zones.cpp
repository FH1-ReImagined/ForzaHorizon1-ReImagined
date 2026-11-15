// forza - the game's emergency zone loads keep their zones in a list of 7 and wrote past it (#902).
//
// When the zones the track presentation wants live are not streamed in time, its emergency path
// asks for them at once: CTrackPresentation::ManageEmergencyPVSZLoading (TU4 sub_82E27480, named
// from the FH2 prototype's symbols) walks m_ZonesToLoad (14 entries: the two zone windows' 7
// each), and for every zone there without a streamed-zone slot (m_PVSStreamedZones, 30 slots) it
// asks the extra-file streaming for the zone's file (a count on that file's load change) and
// appends the zone to m_EmergencyPVSZList, counting it in m_NumEmergencyPVSZ. Each entry is
// released (UnloadStreamingZone) when its zone is no longer wanted, the list's last entry moved
// into its place; the list empties once every zone in it is loaded. The list has 7 entries
// (+0xD994), the count is the word after it (+0xD9B0), and the first zone window follows
// (+0xD9C0). Nothing checks the count against the 7: with more than seven zones missing at once,
// the eighth append wrote a zone number over the count, the next appends went past the object,
// and the removals copied far-off memory over the zone windows.
//
// On a converted FM4 circuit at speed (Nordschleife after sector 13, #902) the zones far ahead
// are all new at once and its hexes are 100 m: the render thread then faulted in CTrackPresentation::
// ProcessDirty on a zone window full of 0xFF (the player's log), or in this function reading the
// list with a count of 0x3F800000 (local runs at the same place). FH2's build has the same code.
//
// The list's entries live here instead, 64 for each track presentation, and the four places that
// read or write them use this list (hints_tu4/emergency_zones.toml): everything else, the count
// in guest memory, the requests, the releases and the order, is the game's own. A zone that would
// be the 65th is asked for later: the hook marks the emergency unfinished, as the game does for a
// zone that is still loading. (Leaving zones out of the 7 instead does not work: the function's
// end takes the camera's zone as loaded once nothing is unfinished, and read a null zone.)
// FORZA_EMERGENCY_ZONES_TRACE=1 logs each emergency that needs more than 7 entries.

#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <unordered_map>

namespace {

constexpr uint32_t kGameSize = 7;   // CTrackPresentation::m_EmergencyPVSZList[7]
constexpr uint32_t kSize = 64;      // the entries kept here, for each track presentation

std::mutex mutex;
std::unordered_map<uint32_t, std::array<uint32_t, kSize>> lists;  // track presentation -> zones
std::atomic<uint64_t> past_seven{0}, full{0};

uint32_t Load32(const uint8_t* p) {
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

PPCContext& Context(PPCRegister& r3) { return *reinterpret_cast<PPCContext*>(&r3); }

uint8_t* Base() { return rex::system::kernel_state()->memory()->virtual_membase(); }

// m_NumEmergencyPVSZ (r23 holds its address)
uint32_t Count(const PPCContext& ctx) { return Load32(forza::GuestPtr(Base(), ctx.r23.u32)); }

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_EMERGENCY_ZONES_TRACE");
    return v && *v && *v != '0';
  }();
  return on;
}

}  // namespace

static_assert(offsetof(PPCContext, r3) == 0, "hooks recover ctx from &ctx.r3");

// 0x82E274BC `lwz r4, 0(r30)`: the release loop reads entry r29 (the hint goes on at 0x82E274C0).
void ForzaEmergencyZoneRead(PPCRegister& r3) {
  PPCContext& ctx = Context(r3);
  const uint32_t i = ctx.r29.u32;
  std::lock_guard<std::mutex> lock(mutex);
  ctx.r4.u64 = i < kSize ? lists[ctx.r31.u32][i] : 0xFFFFFFFFu;
}

// 0x82E27520 `lwzx r11, r11, r31`: the count was just lowered; the last entry takes the place of
// the released entry r29 (the hint goes on at 0x82E27528, past the store to the game's list).
void ForzaEmergencyZoneRemove(PPCRegister& r3) {
  PPCContext& ctx = Context(r3);
  const uint32_t last = Count(ctx), i = ctx.r29.u32;
  std::lock_guard<std::mutex> lock(mutex);
  auto& list = lists[ctx.r31.u32];
  if (last < kSize && i < kSize) list[i] = list[last];
  ctx.r11.u64 = i < kSize ? list[i] : 0xFFFFFFFFu;
}

// 0x82E27590, before a zone without a slot is asked for: true when the list is full (the hint
// goes to 0x82E27610: the emergency stays unfinished, the zone is asked for on a later pass).
bool ForzaEmergencyZoneListFull(PPCRegister& r3) {
  PPCContext& ctx = Context(r3);
  const uint32_t count = Count(ctx);
  if (count < kSize) return false;
  const uint64_t n = ++full;
  if (n <= 5 || (n & (n - 1)) == 0) {
    REXLOG_WARN("ZONES: emergency zone list full ({} entries): zone {} asked for later ({} so far)", count,
                Load32(forza::GuestPtr(Base(), ctx.r30.u32)), n);
  }
  return true;
}

// 0x82E275F0 `lwz r10, 0(r30)`: the zone just asked for (its m_ZonesToLoad entry at r30) is
// appended at the count (the hint goes on at 0x82E27604, which counts it).
void ForzaEmergencyZoneAppend(PPCRegister& r3) {
  PPCContext& ctx = Context(r3);
  const uint32_t zone = Load32(forza::GuestPtr(Base(), ctx.r30.u32)), count = Count(ctx);
  {
    std::lock_guard<std::mutex> lock(mutex);
    if (count < kSize) lists[ctx.r31.u32][count] = zone;
  }
  ctx.r10.u64 = zone;
  if (count == kGameSize) {  // the eighth: the game's own list would have been written past
    const uint64_t n = ++past_seven;
    if (Trace() || n <= 3 || (n & (n - 1)) == 0) {
      REXLOG_INFO("ZONES: an emergency needs more than {} zones (zone {} is the {}th; {} times so far)",
                  kGameSize, zone, count + 1, n);
    }
  }
}
