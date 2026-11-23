// forza - the scene in one pass instead of three EDRAM tiles (scene_msaa, #45, docs/perf.md
// "One-pass scene").
//
// Free roam and the races draw the 3D scene at 1280x720 with 4x MSAA, too big for the 10 MB of
// EDRAM: the game's render scenario (TilingScenario in media/renderscenarios.zip, an index
// into a table in the executable, src/platform/scene_tiling.h) splits it into three
// predicated tiles, and the emulated GPU runs the scene's commands once per tile. On a
// 4-core CPU those three passes are most of the command processor's time.
//
// scene_msaa = game (default) keeps the game's choice; 2x lowers the 4x scenario to the
// game's own 2x MSAA one (two tiles, used by its 12- and 16-car races); off lowers 4x and 2x
// to its one-pass scenario without MSAA. With scene_resolution below 720p the full-screen
// scenarios become its smaller one-pass scene instead (src/hooks/scene_resolution.cpp). Two hooks:
// - Each integer render setting reads its "value" from the XML with one virtual function
//   (sub_82D7DD68, TU4 sub_82E40028); for the TilingScenario setting (its name function,
//   vtable +8, sub_82D80DD8, TU4 sub_82E42F98) the value is lowered after the read. It
//   applies from the next scenario read (startup, a race, free roam).
// - The track (static world) presentation and its procedural models (trees, barriers, ...)
//   draw each scene pass from D3D command lists recorded per (pass, layer), index pass * 3 +
//   layer. The scene pass is 4 with three tiles, 3 with two and 5 with one, and the game's
//   model data hold lists for pass 4 only, so with two tiles or one those items drew nothing.
//   Passes 3 and 5 now draw pass 4's lists: the track's item check (sub_82DEA700, TU4
//   sub_82EAC930) finds them, the track's pass (sub_82436468, TU4 sub_82466418) draws them
//   with its list path (sub_824365B0, TU4 sub_82466560) after its own work, and the procedural
//   models' pass (sub_82417BC0, TU4 sub_82447B70, the pass in its ninth argument) is given pass
//   4. A pass whose view changed fixes its lists up again (sub_82DEBA08, TU4 sub_82EADC80,
//   per pass); pass 4's lists are now reset with passes 3 and 5. Those lists were recorded for
//   the three tiles: their per-tile window writes and the 4x MSAA render target are made NOPs
//   while they run for pass 3 or 5 and put back before they run for another pass (D3D's
//   RunCommandBuffer caller, sub_824167F8, TU4 sub_824467A8; src/platform/scene_tiling.h
//   TileBoundPackets). Pass 4, the game's 4x scenario, is unchanged.
// tu_address_map: TU4 addresses

#include "../debug/list_trace.h"
#include "native_lists.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/scene_resolution.h"
#include "../platform/scene_tiling.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

REXCVAR_DEFINE_STRING(scene_msaa, "game", "Display",
                      "MSAA of the 3D scene: game (4x MSAA in three EDRAM tiles), 2x (two tiles), "
                      "off (one pass without MSAA: about a fifth less of the GPU emulation's "
                      "CPU time)");

#if FORZA_TU4
#define ReadIntSetting sub_82E40028
#define OriginalReadIntSetting __imp__sub_82E40028
#define TrackDrawPass sub_82466418
#define OriginalTrackDrawPass __imp__sub_82466418
#define TrackDrawLists sub_82466560
#define HasRecordedList sub_82EAC930
#define OriginalHasRecordedList __imp__sub_82EAC930
#define ProceduralDrawPass sub_82447B70
#define OriginalProceduralDrawPass __imp__sub_82447B70
#define ResetPassFixups sub_82EADC80
#define OriginalResetPassFixups __imp__sub_82EADC80
#define RunRecordedList sub_824467A8
#define OriginalRunRecordedList __imp__sub_824467A8
constexpr uint32_t kTilingScenarioName = 0x82E42F98;
#else
#define ReadIntSetting sub_82D7DD68
#define OriginalReadIntSetting __imp__sub_82D7DD68
#define TrackDrawPass sub_82436468
#define OriginalTrackDrawPass __imp__sub_82436468
#define TrackDrawLists sub_824365B0
#define HasRecordedList sub_82DEA700
#define OriginalHasRecordedList __imp__sub_82DEA700
#define ProceduralDrawPass sub_82417BC0
#define OriginalProceduralDrawPass __imp__sub_82417BC0
#define ResetPassFixups sub_82DEBA08
#define OriginalResetPassFixups __imp__sub_82DEBA08
#define RunRecordedList sub_824167F8
#define OriginalRunRecordedList __imp__sub_824167F8
constexpr uint32_t kTilingScenarioName = 0x82D80DD8;
#endif
extern "C" REX_FUNC(OriginalReadIntSetting);
extern "C" REX_FUNC(OriginalTrackDrawPass);
extern "C" REX_FUNC(TrackDrawLists);
extern "C" REX_FUNC(OriginalHasRecordedList);
extern "C" REX_FUNC(OriginalProceduralDrawPass);
extern "C" REX_FUNC(OriginalResetPassFixups);
extern "C" REX_FUNC(OriginalRunRecordedList);

namespace forza::scene_resolution {
bool Active();  // src/hooks/scene_resolution.cpp: the scene is drawn below 720p
}  // namespace forza::scene_resolution

namespace {
namespace tiling = forza::scene_tiling;

uint32_t Load32(uint8_t* base, uint32_t address) {
  const auto* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
uint32_t Load16(uint8_t* base, uint32_t address) {
  const auto* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 8 | p[1];
}
uint32_t Load8(uint8_t* base, uint32_t address) { return *forza::GuestPtr(base, address); }
void Store32(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = forza::GuestPtr(base, address);
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
}

}  // namespace

// Reads an integer render setting (r3) from a render scenario XML element (r4).
extern "C" REX_FUNC(ReadIntSetting) {
  const uint32_t setting = ctx.r3.u32;
  OriginalReadIntSetting(ctx, base);
  const tiling::Mode mode = tiling::ParseMode(REXCVAR_GET(scene_msaa));
  const bool smaller_scene = forza::scene_resolution::Active();
  if (mode == tiling::Mode::kGame && !smaller_scene) return;
  const uint32_t vtable = Load32(base, setting);
  if (vtable < 0x82000000 || vtable >= 0x84000000 ||
      Load32(base, vtable + 8) != kTilingScenarioName) {
    return;
  }
  const uint32_t scenario = Load32(base, setting + 4);
  // Below 720p the full-screen scenarios draw the smaller scene in one pass, whose MSAA follows
  // scene_msaa (src/hooks/scene_resolution.cpp).
  const uint32_t chosen = smaller_scene ? forza::scene_resolution::SceneScenario(scenario)
                                        : tiling::SceneScenario(mode, scenario);
  if (chosen == scenario) return;
  Store32(base, setting + 4, chosen);
  static std::atomic<bool> logged{false};
  if (!logged.exchange(true)) {
    REXLOG_INFO("scene_msaa: render scenario tiling {} instead of {}", chosen, scenario);
  }
}

namespace {

// The lists of a model (its render data + 128): count at +32, the lists at +40 (each points
// to the D3D command buffer, null without one), loaded at +244.
uint32_t ModelLists(uint8_t* base, uint32_t model) {
  const uint32_t data = model ? Load32(base, model + 48) : 0;
  return data ? data + 128 : 0;
}
bool HasList(uint8_t* base, uint32_t lists, uint32_t pass, uint32_t layer) {
  const uint32_t index = pass * 3 + layer;
  if (!lists || index >= Load16(base, lists + 32)) return false;
  const uint32_t list = Load32(base, Load32(base, lists + 40) + index * 4);
  return list && Load32(base, list);
}

// Set while this thread draws pass 4's lists for pass 3 or 5.
thread_local bool t_lists_for_fewer_tiles = false;
struct ListsForFewerTiles {
  bool previous = t_lists_for_fewer_tiles;
  ListsForFewerTiles() { t_lists_for_fewer_tiles = true; }
  ~ListsForFewerTiles() { t_lists_for_fewer_tiles = previous; }
};

// D3D command buffer: flags at +108 (0x800: recorded with tiling rectangles, as pass 4's
// lists are and those of passes 0, 1 and 6 are not), its indirect buffers at +116 (a chain:
// next, count, then per buffer the size in words, low 24 bits, and its physical address).
constexpr uint32_t kRecordedForTiles = 0x800;
uint32_t PhysicalToGuest(uint32_t address) { return 0xA0000000u | (address & 0x1FFFFFFFu); }
bool IsNop(uint32_t header) {
  return header >> 30 == 3 && ((header >> 8) & 0x7F) == tiling::kPm4Nop;
}

// The game's lists stay in guest memory, and the same list can run for pass 4 later in the
// session: the game reads every render scenario's TilingScenario at startup (0, 1 and 4) and
// uses the one of the current screen, and a value changed in free roam changed the tiling
// from the next frame. A list unloaded and loaded again comes back as recorded. So the
// tile-bound packets (TileBoundPackets) are made NOPs while a list runs for pass 3 or 5 and
// put back before it runs for any other pass. A list recorded for the
// tiles starts with the first tile's bin mask and window write (word 4); that word is a NOP
// exactly while the list's packets are NOPs, and the replaced headers are kept per list.
// Lists that start otherwise (not seen in the game) are left as they are.
std::mutex g_untiled_mutex;
std::unordered_map<uint32_t, std::vector<std::pair<uint32_t, uint32_t>>> g_untiled;

// Word 4 of list cb recorded for the tiles (0 for other lists).
uint32_t FifthWord(uint8_t* base, uint32_t cb) {
  if (!cb || !(Load32(base, cb + 108) & kRecordedForTiles)) return 0;
  const uint32_t chain = Load32(base, cb + 116);
  if (!chain || !Load32(base, chain + 4) || (Load32(base, chain + 8) & 0xFFFFFF) <= 4) return 0;
  return Load32(base, PhysicalToGuest(Load32(base, chain + 12)) + 16);
}

void UntileList(uint8_t* base, uint32_t cb) {
  const uint32_t fifth = FifthWord(base, cb);
  if (!fifth || IsNop(fifth)) return;
  if (fifth >> 30 != 3 || ((fifth >> 8) & 0x7F) != tiling::kPm4SetConstant) {
    static std::atomic<bool> told{false};
    if (!told.exchange(true)) {
      REXLOG_INFO("scene_msaa: list {:08X} recorded for the tiles starts otherwise; left as it is",
                  cb);
    }
    return;
  }
  thread_local std::vector<uint32_t> words;
  std::vector<std::pair<uint32_t, uint32_t>> replaced;
  const uint32_t chain = Load32(base, cb + 116);
  for (uint32_t link = chain, links = 0; link && links < 64; link = Load32(base, link), ++links) {
    const uint32_t buffers = Load32(base, link + 4);
    for (uint32_t b = 0; b < buffers && b < 256; ++b) {
      const uint32_t size = Load32(base, link + 8 + b * 8) & 0xFFFFFF;
      const uint32_t address = PhysicalToGuest(Load32(base, link + 12 + b * 8));
      words.resize(size);
      for (uint32_t i = 0; i < size; ++i) words[i] = Load32(base, address + i * 4);
      for (size_t i : tiling::TileBoundPackets(words.data(), words.size())) {
        replaced.emplace_back(address + uint32_t(i) * 4, words[i]);
      }
    }
  }
  if (replaced.empty()) return;
  std::lock_guard<std::mutex> lock(g_untiled_mutex);
  // Word 4 last: it marks the list as done only once all its packets are.
  for (auto it = replaced.rbegin(); it != replaced.rend(); ++it) {
    Store32(base, it->first, tiling::NopPacket(it->second));
  }
  const size_t count = replaced.size();
  g_untiled[cb] = std::move(replaced);
  static std::atomic<bool> logged{false};
  if (!logged.exchange(true)) {
    REXLOG_INFO("scene_msaa: the scene draws the three-tile pass's recorded lists without their "
                "tile layout ({} packets in the first)", count);
  }
}

// Puts back the packets of list cb if they are NOPs (it runs for pass 4 or another pass).
void RetileList(uint8_t* base, uint32_t cb) {
  if (!IsNop(FifthWord(base, cb))) return;
  std::lock_guard<std::mutex> lock(g_untiled_mutex);
  const auto found = g_untiled.find(cb);
  if (found == g_untiled.end()) return;
  // Word 4 first, the others after it, each only where the NOP is still in place.
  for (const auto& [address, header] : found->second) {
    if (Load32(base, address) == tiling::NopPacket(header)) Store32(base, address, header);
  }
  g_untiled.erase(found);
  static std::atomic<bool> logged{false};
  if (!logged.exchange(true)) {
    REXLOG_INFO("scene_msaa: a list drawn without its tile layout runs with its tiles again");
  }
}

}  // namespace

// The headers list cb's untiling replaced (guest address, header), for native_lists.cpp: a list
// first seen untiled is converted from a copy with them put back.
namespace forza::scene_tiling_hooks {
std::vector<std::pair<uint32_t, uint32_t>> SavedHeaders(uint32_t cb) {
  std::lock_guard<std::mutex> lock(g_untiled_mutex);
  const auto found = g_untiled.find(cb);
  return found == g_untiled.end() ? std::vector<std::pair<uint32_t, uint32_t>>{} : found->second;
}
}  // namespace forza::scene_tiling_hooks

// Whether lists r3 hold a recorded list for pass r4 and layer r5 (r3); the track presentation
// skips items without one. Passes 3 and 5 have pass 4's.
extern "C" REX_FUNC(HasRecordedList) {
  const uint32_t pass = ctx.r4.u32;
  const uint32_t list_pass = tiling::RecordedListPass(pass);
  if (list_pass == pass) {
    OriginalHasRecordedList(ctx, base);
    return;
  }
  const uint32_t lists = ctx.r3.u32;
  const uint32_t layer = ctx.r5.u32;
  ctx.r3.u64 = HasList(base, lists, pass, layer) || HasList(base, lists, list_pass, layer);
}

// Draws the track presentation's items for one scene pass (r4) and layer (r6) of item (r8);
// its last argument (r10) picks the recorded lists (TrackDrawLists: presentation, pass, r5,
// layer, item, r9) over the direct path. For passes 3 and 5 the function does the pass's own
// work and finds no list; pass 4's are drawn after it.
extern "C" REX_FUNC(TrackDrawPass) {
  FORZA_LIST_TRACE_CONTEXT(kTrack, ctx.r4.u32, ctx.r6.u32);
  const forza::native_lists::GroupScope native_lists_group(ctx.r4.u32, ctx.r6.u32);
  const uint32_t pass = ctx.r4.u32;
  const uint32_t list_pass = tiling::RecordedListPass(pass);
  if (list_pass == pass || !(ctx.r10.u32 & 0xFF)) {
    OriginalTrackDrawPass(ctx, base);
    return;
  }
  const uint32_t presentation = ctx.r3.u32;
  const uint32_t r5 = ctx.r5.u32;
  const uint32_t layer = ctx.r6.u32;
  const uint32_t item = ctx.r8.u32;
  const uint32_t r9 = ctx.r9.u32;
  OriginalTrackDrawPass(ctx, base);
  const uint32_t lists = ModelLists(base, Load32(base, item + 4));
  if (!lists || !Load8(base, lists + 244) || HasList(base, lists, pass, layer) ||
      !HasList(base, lists, list_pass, layer)) {
    return;
  }
  ctx.r3.u64 = presentation;
  ctx.r4.u64 = list_pass;
  ctx.r5.u64 = r5;
  ctx.r6.u64 = layer;
  ctx.r7.u64 = item;
  ctx.r8.u64 = r9;
  ListsForFewerTiles scope;
  TrackDrawLists(ctx, base);
}

// proceduralGeometry::CProceduralModels (vtable +164): draws the instances of one scene pass
// (its ninth argument, r1 + 84) and layer (r9) from each model's recorded lists, skipping
// models without one; it has no direct path. Passes 3 and 5 are drawn as pass 4.
extern "C" REX_FUNC(ProceduralDrawPass) {
  const uint32_t at = ctx.r1.u32 + 84;
  const uint32_t pass = Load32(base, at);
  FORZA_LIST_TRACE_CONTEXT(kProcedural, pass, ctx.r9.u32);
  const forza::native_lists::GroupScope native_lists_group(pass, ctx.r9.u32);
  const uint32_t list_pass = tiling::RecordedListPass(pass);
  if (list_pass == pass) {
    OriginalProceduralDrawPass(ctx, base);
    return;
  }
  Store32(base, at, list_pass);
  {
    ListsForFewerTiles scope;
    OriginalProceduralDrawPass(ctx, base);
  }
  Store32(base, at, pass);
}

// The view of pass r4 changed (CTrackPresentation vtable +352): every model's lists of that
// pass are fixed up again (bits pass * 3 + layer of +56 and +60 cleared). Pass 4's lists drawn
// for pass 3 or 5 follow that pass's view.
extern "C" REX_FUNC(ResetPassFixups) {
  const uint32_t presentation = ctx.r3.u32;
  const uint32_t pass = ctx.r4.u32;
  OriginalResetPassFixups(ctx, base);
  const uint32_t list_pass = tiling::RecordedListPass(pass);
  if (list_pass != pass) {
    ctx.r3.u64 = presentation;
    ctx.r4.u64 = list_pass;
    OriginalResetPassFixups(ctx, base);
  }
}

// Runs D3D command buffer r4 (a recorded list) on the device, or queues it for a deferred
// context.
extern "C" REX_FUNC(RunRecordedList) {
  FORZA_LIST_TRACE_RECORDED_LIST();
  if (t_lists_for_fewer_tiles) {
    UntileList(base, ctx.r4.u32);
  } else {
    RetileList(base, ctx.r4.u32);
  }
  OriginalRunRecordedList(ctx, base);
}
