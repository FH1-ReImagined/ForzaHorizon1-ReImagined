// Native festival shortcuts: select the real map entity, then let FH1 own
// confirmation, cost, map unloading, streaming, teleport and restoration.
// tu_address_map: TU4 addresses
#include "../platform/fh1_festival_travel.h"

#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
#include "../platform/fh1_pause_actions.h"
#include "../platform/guest_memory.h"
#include <rex/logging.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <initializer_list>
#include <array>
#include <optional>

extern "C" REX_FUNC(__imp__sub_827B3D28);
extern "C" REX_FUNC(__imp__sub_828B6998);
extern "C" REX_FUNC(__imp__sub_8278D988);

namespace forza::fh1_festival_travel {
namespace {
uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 |
         uint32_t(p[2]) << 8 | p[3];
}
const char* Name(Destination destination) {
  return destination == Destination::Garage ? "workshop_02" : "autoshow_01";
}
class Native {
public:
  PPCContext ctx;
  uint8_t* base;
  uint32_t scratch;
  Native(const PPCContext& context, uint8_t* memory)
      : ctx(context), base(memory),
        scratch(REX_KERNEL_MEMORY()->SystemHeapAlloc(128)) {}
  ~Native() { if (scratch) REX_KERNEL_MEMORY()->SystemHeapFree(scratch); }
  uint32_t Call(uint32_t function, std::initializer_list<uint32_t> args) {
    unsigned index = 0;
    for (auto value : args) switch (index++) {
      case 0: ctx.r3.u64 = value; break;
      case 1: ctx.r4.u64 = value; break;
      case 2: ctx.r5.u64 = value; break;
      case 3: ctx.r6.u64 = value; break;
    }
    rex::runtime::ResolveIndirectFunction(function)(ctx, base);
    return ctx.r3.u32;
  }
  uint32_t Component(uint32_t slot) {
    // 8269B4F8 dereferences the current game; validate its prerequisite first.
    const auto game = Read(base, 0x833CAAE4);
    if (!game || !Call(0x82F12410, {game})) return 0;
    const auto owner = Call(0x8269B4F8, {});
    const auto index = Read(base, slot);
    if (!owner || index >= 1024) return 0;
    const auto table = Read(base, owner);
    return table ? Read(base, table + index * 4) : 0;
  }
  uint32_t Activity(Destination destination) {
    const auto manager = Component(0x833EDA1C);
    if (!manager || !scratch) return 0;
    // Exactly the read-only lookup in 8264BAFC..8264BB24. Do not call that
    // wrapper: its following branch would activate a locked activity.
    Call(0x82480600, {scratch, destination == Destination::Garage
                                      ? 0x8202E79C : 0x8208C09C});
    const auto activity = Call(0x82961058, {manager, scratch});
    Call(0x82441428, {scratch, 1, 0});
    return activity;
  }
};
struct Request {
  bool pending = false;
  Destination destination = Destination::Garage;
  uint32_t map = 0;
  uint32_t activity = 0;
  uint32_t entity = 0;
  unsigned frames = 0;
  bool focused = false;
};
Request request;

uint32_t FindEntity(Native& native, uint32_t activity) {
  const auto service = Read(native.base, 0x833F0410);
  if (!service) return 0;
  const auto map = native.Call(0x826E2D20, {service, 1});
  if (!map) return 0;
  const auto begin = Read(native.base, map + 0x10);
  const auto end = Read(native.base, map + 0x14);
  if (!begin || end < begin || end - begin > 4096) return 0;
  // 82DA6AB8 traverses map layers; 82DA4EF8 walks their 0x40-byte records.
  for (auto at = begin; at < end; at += 4) {
    const auto layer = Read(native.base, at);
    if (!layer) continue;
    const auto first = Read(native.base, layer + 0x0C);
    const auto last = Read(native.base, layer + 0x10);
    if (!first || last < first || last - first > 0x400000) continue;
    for (auto record = first; record < last; record += 0x40) {
      const auto entity = Read(native.base, record + 0x14);
      if (entity && Read(native.base, entity) == 0x82024460 &&
          Read(native.base, entity + 0x28) == activity) return entity;
    }
  }
  return 0;
}

void BeforeOverlay(const PPCContext& context, uint8_t* base, uint32_t overlay) {
  if (!request.pending || !request.map ||
      Read(base, request.map + 0x188) != overlay) return;
  if (++request.frames > 600) {
    REXLOG_WARN("Festival travel: {} target selection timed out", Name(request.destination));
    request = {};
    return;
  }
  Native native(context, base);
  if (!native.scratch) return;
  if (!request.activity) request.activity = native.Activity(request.destination);
  if (!request.activity) return;
  if (!request.entity) request.entity = FindEntity(native, request.activity);
  if (!request.entity) return;
  // Native map visibility includes activity mode/unlock checks (825CB068).
  if (!native.Call(0x825CB068, {request.entity})) return;
  if (!request.focused) {
    const auto camera = Read(base, overlay + 0x1A4);
    if (!camera) return;
    // v18 copies the first actual icon point, including multi-point entities.
    native.Call(0x825CB0E0, {request.entity, native.scratch, 1});
    for (unsigned i = 0; i != 4; ++i)
      native.ctx.v1.u32[3 - i] = Read(base, native.scratch + i * 4);
    native.Call(0x82584348, {camera});
    request.focused = true;
    REXLOG_INFO("Festival travel: focused {} activity {:08X}, entity {:08X}",
                Name(request.destination), request.activity, request.entity);
    return;
  }
  const auto selected = Read(base, overlay + 0x244);
  if (selected != request.entity ||
      Read(base, selected + 0x28) != request.activity) return;
  if (!(native.Call(0x828AF468, {overlay}) & 0xFF)) return;
  // This is the native fast-travel request flag written by map input. The
  // original update now computes the destination matrix and current fee.
  GuestPtr(base, overlay)[0x1AA] = 1;
}

void AfterOverlay(const PPCContext& context, uint8_t* base, uint32_t overlay) {
  if (!request.pending || !request.map ||
      Read(base, request.map + 0x188) != overlay ||
      !GuestPtr(base, overlay)[0x1AD]) return;
  const auto selected = Read(base, overlay + 0x244);
  if (!selected || selected != request.entity ||
      Read(base, selected + 0x28) != request.activity) return;
  Native native(context, base);
  const auto component = native.Component(0x833EDC20);
  if (!component || !(native.Call(0x826BAAC8, {component}) & 0xFF)) return;
  const auto map = request.map;
  const auto destination = request.destination;
  const auto cost = Read(base, component + 0x60);
  const bool affordable = native.Call(0x826BAB38, {component}) & 0xFF;
  request = {}; // Clear before opening a modal, so it can never reopen itself.
  REXLOG_INFO("Festival travel: {} native confirmation, cost {}, affordable {}",
              Name(destination), cost, affordable);
  native.Call(affordable ? 0x827C81C0 : 0x827C8470, {map, cost});
}
} // namespace

bool Available(const PPCContext& context, uint8_t* base, Destination destination) {
  if (!base) return false;
  Native native(context, base);
  const auto component = native.Component(0x833EDC20);
  const bool enabled = component && (native.Call(0x826BAAC8, {component}) & 0xFF);
  const auto activity = enabled ? native.Activity(destination) : 0;
  const bool available = activity && GuestPtr(base, activity)[8];
  static std::array<std::optional<bool>, 2> previous;
  auto& last = previous[static_cast<unsigned>(destination)];
  if (last != available) {
    REXLOG_INFO("Festival travel: {} {} (component {:08X}, enabled {}, activity {:08X})",
                Name(destination), available ? "available" : "unavailable", component, enabled, activity);
    last = available;
  }
  return available;
}

bool Open(const PPCContext& context, uint8_t* base, uint32_t pause_screen,
          Destination destination) {
  if (!base || !pause_screen || Read(base, pause_screen) != 0x82061F94 ||
      !Available(context, base, destination)) return false;
  request = {};
  request.pending = true;
  request.destination = destination;
  if (!fh1_pause_actions::Dispatch(context, base, pause_screen, fh1_pause_actions::kMap)) {
    request = {};
    return false;
  }
  REXLOG_INFO("Festival travel: requested {} through native map", Name(destination));
  return true;
}
} // namespace forza::fh1_festival_travel

extern "C" REX_FUNC(sub_827B3D28) {
  using namespace forza::fh1_festival_travel;
  const auto screen = ctx.r3.u32;
  __imp__sub_827B3D28(ctx, base);
  if (request.pending && !request.map && Read(base, screen) == 0x8205C00C)
    request.map = screen;
}

extern "C" REX_FUNC(sub_828B6998) {
  using namespace forza::fh1_festival_travel;
  const auto overlay = ctx.r3.u32;
  BeforeOverlay(ctx, base, overlay);
  __imp__sub_828B6998(ctx, base);
  AfterOverlay(ctx, base, overlay);
}

extern "C" REX_FUNC(sub_8278D988) {
  using namespace forza::fh1_festival_travel;
  if (request.pending && (!request.map || request.map == ctx.r3.u32)) request = {};
  __imp__sub_8278D988(ctx, base);
}
#endif
