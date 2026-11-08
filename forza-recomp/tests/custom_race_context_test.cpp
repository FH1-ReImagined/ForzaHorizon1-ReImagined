// Exercise the actual hooks with sparse synthetic guest memory and checked
// native-call boundaries. No game data, process, input or rendering is used.
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/system/kernel_state.h>
#include "../src/platform/guest_memory.h"
#include <array>
#include <cstdio>
#include <cstdlib>

namespace {
std::array<uint8_t, 0x10000> memory{};
bool enabled = true;
void Check(bool ok) { if (!ok) std::abort(); }
struct FixtureMemory {
  uint32_t SystemHeapAlloc(size_t) { return 0x6000; }
  void SystemHeapFree(uint32_t) {}
} fixture_memory;
}
namespace forza {
uint8_t* FixtureGuestPtr(uint8_t* base, uint32_t address) {
  Check(base == memory.data());
  if (address == 0x833CAACC) address = 0x8000;
  if (address == 0x833CAAE4) address = 0x8010;
  // The street race hub's vtable (only its input lookup, vfunc 0xA8, is read).
  if (address >= 0x820527AC && address < 0x820527AC + 0x100) address = 0x9000 + (address - 0x820527AC);
  Check(address < memory.size());
  return base + address;
}
}
#undef REX_KERNEL_MEMORY
#define REX_KERNEL_MEMORY() (&fixture_memory)
#undef REXCVAR_DEFINE_BOOL
#define REXCVAR_DEFINE_BOOL(...)
#undef REXCVAR_GET
#define REXCVAR_GET(...) enabled
#undef REXLOG_INFO
#define REXLOG_INFO(...) ((void)0)
#undef REXLOG_WARN
#define REXLOG_WARN(...) ((void)0)
#define GuestPtr FixtureGuestPtr
#include "../src/hooks/custom_race.cpp"
#undef GuestPtr

namespace {
using namespace forza::custom_race;
constexpr uint32_t screen = 0x100, input = 0x200;
unsigned native_inputs = 0;
unsigned native_binds = 0;
unsigned native_street_inputs = 0, native_street_binds = 0, native_street_notifies = 0;
unsigned native_street_ok = 0, native_street_destroys = 0;
unsigned career_entries = 0;
bool street_input_reaches_ok = true;  // the base dispatcher's A reaches the hub's okCareer
std::vector<std::string> statements;
void Setup(bool career_entry, uint32_t vtable = kDescriptionVtable) {
  memory.fill(0);
  Write(memory.data(), screen, vtable);
  // The street hub's input lookup (vfunc 0xA8) reports A (0x5D) at 0x9800.
  Write(memory.data(), 0x9000 + 0xA8, 0x22220000);
  Write(memory.data(), 0x9800, kEventAccept);
  Write(memory.data(), screen + 376, career_entry ? 0x5000 : 0);
  // The observed remote map card is otherwise a won/eligible career event:
  // car-check result +374=1, event eligibility +408=1.
  memory[screen + 374] = 1;
  Write(memory.data(), screen + 408, 1);
  Write(memory.data(), kDatabaseHolder, 0x8100);
  Write(memory.data(), 0x8200, 0x8300);
  Write(memory.data(), 0x8300, 0x8400);
  Write(memory.data(), 0x8420, 0x11110000);
  card = screen;
  card_eligible = true;
  armed = launch_pending = launched = force_registration = false;
  event_info = EventInfo{};
  StopInjecting();
  statements.clear();
  native_inputs = 0;
  native_binds = 0;
  native_street_inputs = native_street_binds = native_street_notifies = 0;
  native_street_ok = native_street_destroys = 0;
  career_entries = 0;
  street_input_reaches_ok = true;
}
REX_FUNC(Database) { ctx.r3.u64 = 0x8200; }
REX_FUNC(Sql) {
  statements.emplace_back(reinterpret_cast<char*>(memory.data() + ctx.r5.u32));
  Write(base, ctx.r3.u32, 0);
}
REX_FUNC(Release) {}
REX_FUNC(InputLookup) { ctx.r3.u64 = 0x9800; }  // the screen's input event (vfunc 0xA8)
REX_FUNC(CareerEntryPath) {                     // sub_82734200, the base's okCareer entry
  Check(ctx.r3.u32 == screen && base == memory.data());
  ++career_entries;
}
}
namespace rex::runtime {
PPCFunc* ResolveIndirectFunction(uint32_t address) {
  switch (address) {
    case 0x82F1E918: return Database;
    case 0x11110000: return Sql;
    case 0x824CEAB0: return Release;
    case 0x22220000: return InputLookup;
    case 0x82734200: return CareerEntryPath;
    default: Check(false); return nullptr;
  }
}
}
namespace forza::coop_map_event {  // the Co-op Festival's start (coop_map_event.cpp)
unsigned binds = 0;
void CardBound(PPCContext&, uint8_t*, uint32_t, uint32_t, bool) { ++binds; }
bool CardInput(PPCContext&, uint8_t*, uint32_t, uint32_t) { return false; }
void CardDestroyed(uint32_t) {}
}
namespace forza {
bool NetworkOn() { Check(false); return false; }
bool LiveLocalActivity(uint32_t&, uint32_t&) { Check(false); return false; }
std::optional<LiveSessionView> LiveCurrentSession() { Check(false); return {}; }
}
extern "C" REX_FUNC(__imp__sub_82733A68) {
  Check(ctx.r3.u32 == screen && base == memory.data());
  ++native_binds;
}
extern "C" REX_FUNC(__imp__sub_82727530) { Check(false); }
extern "C" REX_FUNC(__imp__sub_82735FD0) { Check(false); }
extern "C" REX_FUNC(__imp__sub_82738948) {
  Check(ctx.r3.u32 == screen && ctx.r4.u32 == input && base == memory.data());
  ++native_inputs;
  ctx.r3.u64 = 7;
}

extern "C" REX_FUNC(__imp__sub_82755D60) {
  Check(ctx.r3.u32 == screen && base == memory.data());
  ++native_street_binds;
}
extern "C" REX_FUNC(__imp__sub_827472D0) {
  Check(ctx.r3.u32 == screen && ctx.r4.u32 == input && base == memory.data());
  ++native_street_inputs;
  // The base dispatcher's A: +376 set, the row available (vfunc 244), then okCareer (vfunc
  // 228, the hub's sub_8275FFA8).
  if (street_input_reaches_ok) sub_8275FFA8(ctx, base);
  ctx.r3.u64 = 7;
}
extern "C" REX_FUNC(__imp__sub_8275FEF8) {
  Check(ctx.r3.u32 == screen && base == memory.data());
  ++native_street_notifies;
}
extern "C" REX_FUNC(__imp__sub_8275FFA8) {
  Check(ctx.r3.u32 == screen && base == memory.data());
  ++native_street_ok;
}
extern "C" REX_FUNC(__imp__sub_8274DF78) {
  Check(ctx.r3.u32 == screen && base == memory.data());
  ++native_street_destroys;
}

int main() {
  PPCContext ctx{};
  Setup(false);
  ctx.r3.u64 = screen;
  sub_82733A68(ctx, memory.data());
  Check(native_binds == 1 && !card_eligible && forza::coop_map_event::binds == 1);
  Check(statements.size() == 5);
  for (const auto& sql : statements) Check(sql.starts_with("SELECT "));
  // Any attempt to add the Custom Race footer or query the profile/offline
  // gates would hit a forbidden native call in this remote-map fixture.

  Setup(false);
  {
    Ui ui(ctx, memory.data());
    Check(!Arm(ui));
    Check(statements.empty() && !armed);  // no custom tables on remote map details
  }
  ctx.r3.u64 = screen; ctx.r4.u64 = input;
  sub_82738948(ctx, memory.data());
  Check(!card_eligible && native_inputs == 1 && ctx.r3.u32 == 7);
  Check(statements.empty());

  Setup(false);
  armed = launch_pending = true;
  card_active = true;
  inject_from_ms = NowMs() - 1;
  inject_until_ms = NowMs() + 1000;
  ctx.r3.u64 = screen; ctx.r4.u64 = input;
  sub_82738948(ctx, memory.data());
  Check(statements == DisarmStatements());  // all seven TEMP tables removed
  Check(!armed && !launch_pending && !launched && !card_active);
  Check(inject_from_ms == 0 && inject_until_ms == 0);
  Check(native_inputs == 1 && ctx.r3.u32 == 7);
  Check((forza::CustomRacePad(0) & kPadA) == 0);

  Setup(true);
  Check(HasCareerEntry(memory.data(), screen));
  Write(memory.data(), screen, 0x12345678);
  Check(!HasCareerEntry(memory.data(), screen));  // another screen cannot qualify
  Check(!HasCareerEntry(memory.data(), 0));

  // ---- The street race hub (CCareerStreetRaceSelect) ----
  constexpr uint32_t kStreetHub = 0x820527AC;
  Setup(true, kStreetHub);
  Check(IsStreetHub(memory.data(), screen) && !IsCard(memory.data(), screen));
  Check(HasCareerEntry(memory.data(), screen));  // the base's okCareer, as on the card
  Write(memory.data(), screen + 396, 38);
  Write(memory.data(), 0x8000, 0x8800);
  Write(memory.data(), 0x8800 + 0x388, 51);
  Check(ScreenEvent(memory.data(), screen) == 38);  // the hub's selected row
  Write(memory.data(), screen, kDescriptionVtable);
  Check(ScreenEvent(memory.data(), screen) == 51);  // the card's race settings

  // A hub without a career entry: its event is read, nothing is offered or queried further
  // (a footer, profile or offline call would hit a forbidden native call).
  Setup(false, kStreetHub);
  Write(memory.data(), screen + 396, 38);
  card = 0;
  card_eligible = false;
  ctx.r3.u64 = screen;
  sub_82755D60(ctx, memory.data());
  Check(native_street_binds == 1 && card == screen && !card_eligible);
  Check(statements.size() == 5 && event_info.id == 38);
  for (const auto& sql : statements) Check(sql.starts_with("SELECT ") && sql.find("=38") != sql.npos);

  // Its list has no row yet: no query at all.
  Setup(true, kStreetHub);
  card = 0;
  ctx.r3.u64 = screen;
  sub_82755D60(ctx, memory.data());
  Check(native_street_binds == 1 && !card_eligible && statements.empty());

  // The cursor moves to another row: the native notify runs, then the new row is checked;
  // the same row again is not checked twice.
  Setup(false, kStreetHub);
  event_info.id = 38;
  card_eligible = false;
  Write(memory.data(), screen + 396, 50);
  ctx.r3.u64 = screen; ctx.r4.u64 = 1;
  sub_8275FEF8(ctx, memory.data());
  Check(native_street_notifies == 1 && event_info.id == 50 && statements.size() == 5);
  for (const auto& sql : statements) Check(sql.find("=50") != sql.npos);
  statements.clear();
  ctx.r3.u64 = screen;
  sub_8275FEF8(ctx, memory.data());
  Check(native_street_notifies == 2 && statements.empty());

  // Another screen's notify is only passed on.
  Setup(true, kStreetHub);
  card = 0x300;
  ctx.r3.u64 = screen;
  sub_8275FEF8(ctx, memory.data());
  Check(native_street_notifies == 1 && statements.empty());

  // The hub's okCareer outside a hand-over is the native one.
  Setup(true, kStreetHub);
  memory[screen + 374] = 0;
  ctx.r3.u64 = screen;
  sub_8275FFA8(ctx, memory.data());
  Check(native_street_ok == 1 && career_entries == 0 && memory[screen + 374] == 0);

  // The hand-over's A on the hub: the native input runs, and the hub's okCareer takes the
  // base's entry with the car check set (car registration for the chosen class), without
  // the native row-flag check. The custom race stays armed for the race.
  Setup(true, kStreetHub);
  memory[screen + 374] = 0;
  Write(memory.data(), screen + 396, 38);
  event_info.id = 38;
  armed = launch_pending = true;
  card_active = true;
  inject_from_ms = NowMs() - 1;
  inject_until_ms = NowMs() + 1000;
  ctx.r3.u64 = screen; ctx.r4.u64 = input;
  sub_827472D0(ctx, memory.data());
  Check(native_street_inputs == 1 && ctx.r3.u32 == 7);
  Check(career_entries == 1 && native_street_ok == 0 && memory[screen + 374] == 1);
  Check(armed && launched && !launch_pending && !force_registration && !card_active);
  Check(statements.empty());  // no drop: the race is on its way
  Check(inject_from_ms == 0 && inject_until_ms == 0);

  // A hand-over whose A never reached okCareer cancels the custom race.
  Setup(true, kStreetHub);
  Write(memory.data(), screen + 396, 38);
  event_info.id = 38;
  armed = launch_pending = true;
  street_input_reaches_ok = false;
  ctx.r3.u64 = screen; ctx.r4.u64 = input;
  sub_827472D0(ctx, memory.data());
  Check(native_street_inputs == 1 && ctx.r3.u32 == 7 && career_entries == 0);
  Check(statements == DisarmStatements() && !armed && !launched && !force_registration);

  // A hub that lost its career entry: cancelled, as the card.
  Setup(false, kStreetHub);
  armed = launch_pending = true;
  ctx.r3.u64 = screen; ctx.r4.u64 = input;
  sub_827472D0(ctx, memory.data());
  Check(statements == DisarmStatements() && !armed && !card_eligible);
  Check(native_street_inputs == 1 && career_entries == 0);

  // The hub's destructor forgets it.
  Setup(true, kStreetHub);
  card_active = true;
  ctx.r3.u64 = screen;
  sub_8274DF78(ctx, memory.data());
  Check(native_street_destroys == 1 && card == 0 && !card_eligible && !card_active);

  std::puts("custom race context: OK");
}
