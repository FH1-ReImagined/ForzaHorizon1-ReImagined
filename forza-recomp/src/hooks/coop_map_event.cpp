// forza - the Co-op Festival's events on the map (#309, docs/coop_campaign.md, "Milestone 4:
// events from the map").
//
// Offline, every festival event is an activity of career_event_activations.xml
// (GameModes::CActivityCareerEventActivation, vtable 0x8200C104, its event id at +404): a
// marker on the map and the minimap, whose card (X DETAILS) shows the event, and a zone at its
// start whose SIGN ME UP opens the same card with the career's entry (A loads the race).
//
// 1. The markers. The free roam loads its activity files by race type (sub_826A0AC0; file ids
//    of the resource table at 0x833930C8): 17, the single-player free roam, loads 146-168;
//    18, the online one, leaves out 151 (paintshop), 156 (race activations), 160 (race
//    encounters), 162 (multiplayer), 164 (career event activations), 166 (DLC center) and
//    168 (street race hubs). So an online world had no event markers. In a Co-op Festival
//    world (Find Game's playlist 20) file 164 is loaded too, after the others
//    (sub_8296EDD0(loader, 164)): the career makes the activities of the events the save has
//    unlocked, as it does offline.
// 2. Their info. Each marker reads its event's entry in the career's event map info (a map
//    component, *(sub_8269B4F8())[*(0x833EDC58)]: a std::map at +0xC034 that sub_827445A0's
//    SQL fills, and the markers themselves, 96 bytes each from +16, their count at +0xC018).
//    Nothing fills it online: the career activity's map filter (sub_82505098) read a missing
//    entry and crashed. sub_825CB940, which CTriggerEventUnlocks runs after a career race,
//    fills it and copies each marker's entry into the marker (+0x40: the card under the map's
//    cursor reads it, sub_828B6998, and crashed on a null). It runs before the file is loaded
//    and again once the markers exist (they are made a frame or more later): sub_825CB068, the
//    map's test whether a marker shows, runs it when a career marker has no entry yet, once
//    per count of the component's markers.
// 3. Starting one from its card (ForzaUI::CCareerEventDescription; its hooks are
//    custom_race.cpp's, which ask here first) in a Co-op Festival free roam: the map's Details
//    card (no career entry: its A sets a route) offers SELECT "START CO-OP EVENT" (put back
//    after the map clears the card's footer for its own prompts, sub_8289A908), which sends
//    the server the World chat command "/event <id>" (LiveChatCommand), as if it was typed:
//    the server starts the event for everyone in the world when the sender is the world's
//    host, and tells a guest who is (server/forza_live/server.py chat_command). The world's
//    free roam then ends into the event's lobby (world_events.cpp), from the map as well.
//    The zone at the event's start shows SIGN ME UP online too, but its X does nothing there
//    (the activity is never entered online); a sign-up card that does open (the career's
//    entry: A would load the single-player race out of the online world, X the Rivals time
//    trial) gets the co-op start on A and nothing on X.
//
// Title Update 4 only (the card's hooks are). FORZA_COOP_MAP_TRACE=1 logs the markers' info
// refreshes and every card.

#include "coop_map_event.h"

#include "../platform/guest_addr.h"

#if FORZA_TU4
#include "../kernel/live.h"
#include "../platform/custom_race.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <string>

REXCVAR_DEFINE_BOOL(coop_map_events, true, "Network/Forza Live",
                    "Co-op Festival: the festival's events on the map; an event's Details card "
                    "starts it for the world (SELECT, the host's /event)");

extern "C" REX_FUNC(__imp__sub_826A0AC0);
extern "C" REX_FUNC(__imp__sub_825CB068);
extern "C" REX_FUNC(__imp__sub_8289A908);

namespace rex::runtime {
PPCFunc* ResolveIndirectFunction(uint32_t guest_address);
}

namespace forza::coop_map_event {
namespace {
constexpr uint32_t kCoopPlaylist = 20;        // server/forza_live/title_services/lsp.py COOP_HOPPER
constexpr uint32_t kRaceFreeRoamOnline = 18;  // CGameTypeDecision's multiplayer free roam
constexpr uint32_t kCareerEventFile = 164;    // GameModes\%s\career_event_activations.xml
constexpr uint32_t kCareerActivityVtable = 0x8200C104;
constexpr uint32_t kActivityMarkerVtable = 0x82024460;  // CMapEntityActivity
constexpr uint32_t kOnlineGame = 0x833F4A80;  // the online game object (base 0x83305CC4)
constexpr uint32_t kModeFindGame = 7, kPresetFreeRoam = 7;
constexpr uint32_t kMapInfoSlot = 0x833EDC58;  // the event map info component's slot
constexpr uint32_t kMapInfoMarkers = 0xC018;   // its count of markers
constexpr uint32_t kEventAccept = 0x5D, kEventRivals = 0x99;

uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_COOP_MAP_TRACE");
    return v && *v && *v != '0';
  }();
  return on;
}

int64_t NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

// Calls a guest function on a copy of the hooked function's context; returns r3.
uint32_t Call(PPCContext& ctx, uint8_t* base, uint32_t address, uint32_t r3 = 0,
              uint32_t r4 = 0) {
  PPCFunc* fn = rex::runtime::ResolveIndirectFunction(address);
  if (!fn) return 0;
  PPCContext saved = ctx;
  ctx.r3.u64 = r3;
  ctx.r4.u64 = r4;
  fn(ctx, base);
  const uint32_t result = ctx.r3.u32;
  ctx = saved;
  return result;
}

// Find Game runs the Co-op Festival and the world's round is its free roam.
bool CoopFreeRoam(uint8_t* base) {
  if (LiveMatchPlaylist() != kCoopPlaylist) return false;
  const uint32_t game = Read(base, kOnlineGame);
  // the mode at +0x28; TU4's game options (+0x1B4) start with 4 bytes, then the preset
  return game && Read(base, game + 0x28) == kModeFindGame &&
         GuestPtr(base, game + 0x1B4 + 4)[0] == kPresetFreeRoam;
}

// The career's event map info component, or 0.
uint32_t MapInfo(PPCContext& ctx, uint8_t* base) {
  const uint32_t owner = Call(ctx, base, 0x8269B4F8);
  const uint32_t table = owner ? Read(base, owner) : 0;
  const uint32_t slot = Read(base, kMapInfoSlot);
  return table && slot < 1024 ? Read(base, table + slot * 4) : 0;
}

// The career event markers were loaded into this free roam (they go with it).
std::atomic<bool> loaded{false};
std::atomic<uint32_t> refreshed_count{~0u};  // the markers' count at the last refresh

void Refresh(PPCContext& ctx, uint8_t* base, const char* why) {
  const uint32_t info = MapInfo(ctx, base);
  if (!info) return;
  Call(ctx, base, 0x825CB940, info);
  refreshed_count = Read(base, info + kMapInfoMarkers);
  if (Trace()) {
    REXLOG_INFO("COOP MAP: event map info refreshed ({}): {} markers", why,
                refreshed_count.load());
  }
}

// The card this file offers the co-op start on (UI thread).
uint32_t card = 0;
uint32_t card_event = 0;
bool card_entry = false;
int64_t sent_at = 0;

void Start() {
  const int64_t now = NowMs();
  if (now - sent_at < 3000) return;  // one request per press, not per repeated input
  sent_at = now;
  const bool sent = LiveChatCommand("/event " + std::to_string(card_event));
  REXLOG_INFO("COOP MAP: event {} from its {}: {}", card_event,
              card_entry ? "sign-up" : "map card", sent ? "/event sent" : "not online");
}
}  // namespace

void CardBound(PPCContext& ctx, uint8_t* base, uint32_t screen, uint32_t event,
               bool career_entry) {
  const bool ours = REXCVAR_GET(coop_map_events) && event && CoopFreeRoam(base);
  if (Trace()) {
    REXLOG_INFO("COOP MAP: card {:08X} event {} ({}): {}", screen, event,
                career_entry ? "sign-up" : "map details", ours ? "co-op start" : "not ours");
  }
  if (!ours) {
    if (card == screen) card = 0;
    return;
  }
  card = screen;
  card_event = event;
  card_entry = career_entry;
  if (!career_entry) CardSelectPrompt(ctx, base, screen, "START CO-OP EVENT");
}

bool CardInput(PPCContext& ctx, uint8_t* base, uint32_t screen, uint32_t input) {
  if (!card || screen != card) return false;
  if (!CoopFreeRoam(base)) {  // the world's free roam ended under the card
    card = 0;
    return false;
  }
  if (!card_entry) {
    if (!CardSelectPressed()) return false;
    Start();
    return true;
  }
  if (CardInputIs(ctx, base, screen, input, kEventAccept)) {
    Start();
    return true;
  }
  return CardInputIs(ctx, base, screen, input, kEventRivals);
}

void CardDestroyed(uint32_t screen) {
  if (screen && screen == card) card = 0;
}
}  // namespace forza::coop_map_event

using namespace forza::coop_map_event;

// sub_826A0AC0(loader): loads the free roam's activity files for the race type at +40 (the
// files' owner at +12). In a Co-op Festival world the career event activations come too, with
// the event map info filled first.
extern "C" REX_FUNC(sub_826A0AC0) {
  const uint32_t loader = ctx.r3.u32;
  __imp__sub_826A0AC0(ctx, base);
  loaded = false;
  refreshed_count = ~0u;
  if (Read(base, loader + 40) != kRaceFreeRoamOnline || !REXCVAR_GET(coop_map_events) ||
      !CoopFreeRoam(base)) {
    return;
  }
  Refresh(ctx, base, "before the markers");
  PPCContext saved = ctx;
  ctx.r3.u64 = Read(base, loader + 12);
  ctx.r4.u64 = kCareerEventFile;
  rex::runtime::ResolveIndirectFunction(0x8296EDD0)(ctx, base);
  ctx = saved;
  loaded = true;
  REXLOG_INFO("COOP MAP: the festival's events are on the map (Co-op Festival world)");
}

// sub_825CB068(marker): whether a map marker shows (its activity's own tests). A career event
// marker of this world without its info (+0x40) gets the info refreshed.
extern "C" REX_FUNC(sub_825CB068) {
  const uint32_t marker = ctx.r3.u32;
  // an activity's marker (CMapEntityActivity: its activity at +0x28)
  if (loaded && marker && Read(base, marker) == kActivityMarkerVtable &&
      !Read(base, marker + 0x40)) {
    const uint32_t activity = Read(base, marker + 0x28);
    if (activity && Read(base, activity) == kCareerActivityVtable) {
      const uint32_t info = MapInfo(ctx, base);
      if (info && Read(base, info + kMapInfoMarkers) != refreshed_count) {
        Refresh(ctx, base, "new markers");
      }
    }
  }
  __imp__sub_825CB068(ctx, base);
}

// sub_8289A908(footer): a screen's footer loses its prompts. The map's Details card gets its
// own (SET ROUTE, BACK, ...) after its bind, which clears the footer first: the co-op start
// goes back on.
extern "C" REX_FUNC(sub_8289A908) {
  const uint32_t footer = ctx.r3.u32;
  __imp__sub_8289A908(ctx, base);
  if (card && !card_entry && footer && footer == Read(base, card + 352)) {
    forza::CardSelectPrompt(ctx, base, card, "START CO-OP EVENT");
  }
}

#endif  // FORZA_TU4 (the disc build has no event card hooks: custom_race.cpp is TU4 only)
