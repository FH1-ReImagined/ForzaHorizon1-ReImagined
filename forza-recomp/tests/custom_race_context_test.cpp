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
std::vector<std::string> statements;
void Setup(bool career_entry) {
  memory.fill(0);
  Write(memory.data(), screen, kDescriptionVtable);
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
  armed = launch_pending = launched = false;
  StopInjecting();
  statements.clear();
  native_inputs = 0;
  native_binds = 0;
}
REX_FUNC(Database) { ctx.r3.u64 = 0x8200; }
REX_FUNC(Sql) {
  statements.emplace_back(reinterpret_cast<char*>(memory.data() + ctx.r5.u32));
  Write(base, ctx.r3.u32, 0);
}
REX_FUNC(Release) {}
}
namespace rex::runtime {
PPCFunc* ResolveIndirectFunction(uint32_t address) {
  switch (address) {
    case 0x82F1E918: return Database;
    case 0x11110000: return Sql;
    case 0x824CEAB0: return Release;
    default: Check(false); return nullptr;
  }
}
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

int main() {
  PPCContext ctx{};
  Setup(false);
  ctx.r3.u64 = screen;
  sub_82733A68(ctx, memory.data());
  Check(native_binds == 1 && !card_eligible);
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
  std::puts("custom race context: OK");
}
