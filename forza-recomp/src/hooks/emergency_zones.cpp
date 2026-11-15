// forza - streamed zones: the emergency list written past its 7 entries, and the slots of zones
// that were never let go (#902).
//
// The track presentation streams its zones (the PVS's .pvsz files) into 30 slots
// (m_PVSStreamedZones, made in CTrackPresentation::InitializePVSZoneList). Each pass of
// CTrackPresentation::ManageEmergencyPVSZLoading (TU4 sub_82E27480, named from the FH2 prototype's
// symbols) walks m_ZonesToLoad (14 entries: the two zone windows' 7 each), registers the zones that
// have loaded, and asks the extra-file streaming for the file of every zone there without a slot,
// appending that zone to m_EmergencyPVSZList and counting it in m_NumEmergencyPVSZ. Until every
// zone in the list is loaded the function reports the zones as not ready, and the game skips its
// ordinary streaming (CZoneWindow::AddCurrent, DecOld, AddNew: what lets go of the zones the
// windows have moved past). An entry is released (UnloadStreamingZone) when its zone is no longer
// wanted, the list's last entry moved into its place. The PVS thread gives a requested zone a slot
// only when one is free and drops the request otherwise (CTrackPresentation::UpdatePVS), so the
// function asks again on every pass for a zone still without a slot, and appended it again each
// time. The list has 7 entries (+0xD994), the count is the word after it (+0xD9B0), and the first
// zone window follows (+0xD9C0). Nothing checks the count against the 7: once the list had more
// than seven appends, the eighth wrote a zone number over the count, the next appends went past
// the object, and the releases copied far-off memory and zone numbers over the zone windows.
//
// That happened on the Nordschleife at speed (#902) because the slots ran out: DecOld lets go of
// a zone only when the zone was registered and counted as a window's current zone; a zone whose
// file finished loading after the windows had moved on, or that was registered after it became
// current, keeps its slot for good. On a converted circuit's 100 m zones at racing speed that left
// 16 of the 30 slots to zones a kilometre or more behind by sector 13 (the same 16 in every run).
// The next zones then never got a slot, the emergency never ended, and its list overflowed: the
// render thread faulted in CTrackPresentation::ProcessDirty on a zone window full of 0xFF or zone
// numbers (the player's logs), or this function read its list with a count of 0x3F800000.
//
// Two changes (hints_tu4/emergency_zones.toml):
// - The list's entries live here, for each track presentation, and the function's three places
//   that read or write an entry use this list. A zone is listed once: asked for again while it
//   waits for a slot, as the game does, but not appended again (a second release of the same zone
//   finds no slot and does nothing). At most the 14 zones wanted at once are listed. The count
//   stays in guest memory; the requests, the releases and their order are the game's own.
// - With an FM4 circuit mounted, before each pass asks for zones: when fewer than 10 slots are
//   free, the slots of zones that no zone window wants, holds or draws (m_ZonesToLoad,
//   m_ZonesLoaded, each window's own lists and its previous, current and drawn zone, the last
//   drawn and the predicted zones), that the emergency has not listed, and that have gone
//   unwanted for a second are let go of through the game's own UnloadStreamingZone, as DecOld
//   would have. FORZA_ZONE_SWEEP=0 turns it off, =1 turns it on for any track.
// `ZONES:` lines log what both do.

#include "../platform/guest_memory.h"
#include "fm4_track_mount.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <mutex>
#include <unordered_map>

extern "C" REX_FUNC(sub_82E1AFD8);  // CTrackPresentation::UnloadStreamingZone(int zoneIndex)

namespace {

using Clock = std::chrono::steady_clock;

constexpr uint32_t kGameSize = 7;   // CTrackPresentation::m_EmergencyPVSZList[7]
constexpr uint32_t kSize = 32;      // more than the 14 zones m_ZonesToLoad holds
constexpr uint32_t kSlots = 0xA08;  // CTrackPresentation::m_PVSStreamedZones (begin, end), 0x10 each
constexpr uint32_t kZonesToLoad = 0xDC08, kZonesLoaded = 0xDC40;  // 14 each
constexpr uint32_t kZoneWindows = 0xD9C0, kZoneWindowSize = 0x120;  // CZoneWindow m_ZoneWindow[2]
constexpr uint32_t kLastRendered = 0xDC00, kPredicted = 0xDC78;     // 2 each
constexpr int kSweepBelow = 10;     // free slots
constexpr auto kUnwantedFor = std::chrono::seconds(1);

struct Presentation {
  std::array<uint32_t, kSize> listed{};
  bool open = false;  // an emergency has listed zones and not finished
  Clock::time_point start;
  uint32_t most = 0;      // zones listed at once
  uint64_t repeats = 0;   // zones asked for again while listed (the game appended them again)
  std::unordered_map<uint32_t, Clock::time_point> unwanted;  // slot zones no window wants, since
};

std::mutex mutex;
std::unordered_map<uint32_t, Presentation> presentations;  // track presentation -> its state
std::atomic<int> open_count{0};
std::atomic<uint64_t> past_seven{0}, logged{0}, swept{0};

uint32_t Load32(const uint8_t* p) {
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

PPCContext& Context(PPCRegister& r3) { return *reinterpret_cast<PPCContext*>(&r3); }

uint8_t* Base() { return rex::system::kernel_state()->memory()->virtual_membase(); }

uint32_t Guest32(uint32_t address) { return Load32(forza::GuestPtr(Base(), address)); }

// m_NumEmergencyPVSZ (r23 holds its address)
uint32_t Count(const PPCContext& ctx) { return Guest32(ctx.r23.u32); }

bool InList(uint32_t list, uint32_t zone, uint32_t n = 14) {
  for (uint32_t i = 0; i < n; ++i)
    if (Guest32(list + 4 * i) == zone) return true;
  return false;
}

// A zone the track presentation still has a use for: wanted or held by a zone window (its lists,
// its previous, current and drawn zone), drawn last, or predicted.
bool InUse(uint32_t tp, uint32_t zone) {
  if (InList(tp + kZonesToLoad, zone) || InList(tp + kZonesLoaded, zone) || InList(tp + kLastRendered, zone, 2) ||
      InList(tp + kPredicted, zone, 2))
    return true;
  for (uint32_t w = 0; w < 2; ++w) {
    const uint32_t window = tp + kZoneWindows + w * kZoneWindowSize;
    // m_PreviousZone +0x18, m_PVSZone +0x1C, m_RenderZone +0x30, m_ZonesLoaded +0x34, m_ZonesToLoad +0x50
    if (Guest32(window + 0x18) == zone || Guest32(window + 0x1C) == zone || Guest32(window + 0x30) == zone ||
        InList(window + 0x34, zone, 7) || InList(window + 0x50, zone, 7))
      return true;
  }
  return false;
}

bool SweepOn() {
  static const bool on = [] {
    if (const char* v = std::getenv("FORZA_ZONE_SWEEP"); v && *v) return *v != '0';
    return !forza::fm4_mount::TrackPath().empty();
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
  ctx.r4.u64 = i < kSize ? presentations[ctx.r31.u32].listed[i] : 0xFFFFFFFFu;
}

// 0x82E27520 `lwzx r11, r11, r31`: the count was just lowered; the last entry takes the place of
// the released entry r29 (the hint goes on at 0x82E27528, past the store to the game's list).
void ForzaEmergencyZoneRemove(PPCRegister& r3) {
  PPCContext& ctx = Context(r3);
  const uint32_t last = Count(ctx), i = ctx.r29.u32;
  std::lock_guard<std::mutex> lock(mutex);
  auto& listed = presentations[ctx.r31.u32].listed;
  if (last < kSize && i < kSize) listed[i] = listed[last];
  ctx.r11.u64 = i < kSize ? listed[i] : 0xFFFFFFFFu;
}

// 0x82E27534, after the release loop and before the zones are asked for: lets go of the slots of
// zones nothing wants any more when slots run short (see the top).
void ForzaZoneSweep(PPCRegister& r3) {
  if (!SweepOn()) return;
  PPCContext& ctx = Context(r3);
  const uint32_t tp = ctx.r31.u32;
  const uint32_t first = Guest32(tp + kSlots), end = Guest32(tp + kSlots + 4);
  if (end < first || end - first > 0x10 * 256) return;
  const auto now = Clock::now();
  uint32_t candidates[256];
  int free_slots = 0, n = 0;
  {
    std::lock_guard<std::mutex> lock(mutex);
    Presentation& p = presentations[tp];
    const uint32_t count = std::min(Count(ctx), kSize);
    std::unordered_map<uint32_t, Clock::time_point> still;
    for (uint32_t s = first; s < end; s += 0x10) {
      const uint32_t zone = Guest32(s + 0xC);
      if (zone == 0xFFFFFFFFu) {
        ++free_slots;
        continue;
      }
      bool wanted = InUse(tp, zone);
      for (uint32_t i = 0; i < count && !wanted; ++i) wanted = p.listed[i] == zone;
      if (wanted) continue;
      const auto it = p.unwanted.find(zone);
      const auto since = it == p.unwanted.end() ? now : it->second;
      still[zone] = since;
      if (now - since >= kUnwantedFor) candidates[n++] = zone;
    }
    p.unwanted.swap(still);
    if (free_slots >= kSweepBelow) return;
    for (int i = 0; i < n; ++i) p.unwanted.erase(candidates[i]);
  }
  if (n == 0) return;
  // The game's own release, as its emergency path and its load changes call it (they run here,
  // on this thread, inside UpdatePVSReferences); every register is put back after.
  uint8_t* base = Base();
  const PPCContext saved = ctx;
  for (int i = 0; i < n; ++i) {
    ctx.r3.u64 = tp;
    ctx.r4.u64 = candidates[i];
    sub_82E1AFD8(ctx, base);
    ctx = saved;
    const uint64_t total = ++swept;
    if (total <= 20 || (total & (total - 1)) == 0) {
      REXLOG_INFO("ZONES: let go of zone {}: no zone window wants or holds it ({} slots were free; {} so far)",
                  candidates[i], free_slots, total);
    }
  }
}

// 0x82E275F0 `lwz r10, 0(r30)`, after the zone (its m_ZonesToLoad entry at r30) was asked for:
// false appends it at the count (the hint goes on at 0x82E27604, which counts it); true when it
// is listed already, or the list is full, which leaves the count (0x82E27610: the emergency stays
// unfinished, as for every zone asked for).
bool ForzaEmergencyZoneAppend(PPCRegister& r3) {
  PPCContext& ctx = Context(r3);
  const uint32_t zone = Guest32(ctx.r30.u32), count = Count(ctx);
  ctx.r10.u64 = zone;
  std::lock_guard<std::mutex> lock(mutex);
  Presentation& p = presentations[ctx.r31.u32];
  if (count == 0 && !p.open) {
    p.open = true;
    p.start = Clock::now();
    p.most = 0;
    p.repeats = 0;
    ++open_count;
  }
  for (uint32_t i = 0; i < count && i < kSize; ++i) {
    if (p.listed[i] == zone) {
      ++p.repeats;
      return true;
    }
  }
  if (count >= kSize) return true;  // not expected: the listed zones are all in m_ZonesToLoad
  p.listed[count] = zone;
  p.most = std::max(p.most, count + 1);
  if (count == kGameSize) {  // the eighth: the game's own list would have been written past
    const uint64_t n = ++past_seven;
    if (n <= 3 || (n & (n - 1)) == 0) {
      REXLOG_INFO("ZONES: an emergency wants more than {} zones at once (zone {} is the 8th; {} times so far)",
                  kGameSize, zone, n);
    }
  }
  return false;
}

// 0x82E276E0 `stw r22, 0(r23)`: every zone wanted is in, and the count goes back to 0 (the
// function's end, on every pass without an emergency too).
void ForzaEmergencyZoneDone(PPCRegister& r3) {
  if (open_count.load(std::memory_order_relaxed) == 0) return;
  PPCContext& ctx = Context(r3);
  std::lock_guard<std::mutex> lock(mutex);
  auto it = presentations.find(ctx.r31.u32);
  if (it == presentations.end() || !it->second.open) return;
  Presentation& p = it->second;
  p.open = false;
  --open_count;
  const double secs = std::chrono::duration<double>(Clock::now() - p.start).count();
  if (p.most > kGameSize || secs > 1.0) {
    const uint64_t n = ++logged;
    if (n <= 20 || (n & (n - 1)) == 0) {
      REXLOG_INFO("ZONES: an emergency of {} zones done in {:.2f} s, {} asked for again ({} logged)", p.most,
                  secs, p.repeats, n);
    }
  }
}
