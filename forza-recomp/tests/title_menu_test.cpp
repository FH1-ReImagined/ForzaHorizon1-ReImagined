// Intercept only the synchronous native allocator/call boundary. The actual
// selection, graphics-open, row-hiding and transform code is compiled below.
#include <rex/system/kernel_state.h>
struct FixtureMemory {
  bool fail = false;
  uint32_t SystemHeapAlloc(size_t) { return fail ? 0 : 0x6000; }
  void SystemHeapFree(uint32_t) {}
};
FixtureMemory fixture_memory;
#undef REX_KERNEL_MEMORY
#define REX_KERNEL_MEMORY() (&fixture_memory)
// Exercise the actual title selection hook and transform code with synthetic
// guest memory. No game files, generated functions, runtime or GPU are started.
// This checks the modal/departure lifecycle, not native input repeat or drawing.
#include "../src/hooks/graphics_menu.cpp"

#include <cstdio>

namespace {
using namespace forza::graphics_menu;
constexpr uint32_t kMenu = 0x100;
constexpr float kSpacing = 40;
std::array<uint8_t, 0x8000> memory{};
uint32_t native_calls = 0;
uint32_t benchmark_calls = 0;
bool leave_title = false;
bool graphics_snapshot = false;
bool fail_params = false;
bool fail_empty = false;
unsigned opened = 0;
unsigned hiding = 0;
unsigned expected_rows = 0;
unsigned expected_shift = 0;
bool showing_rows = false;

void Check(bool ok, const char* message) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", message);
    std::exit(1);
  }
}
void Unexpected() { Check(false, "unexpected guest/runtime path"); }

float Position(uint32_t address) {
  return std::bit_cast<float>(Read(memory.data(), address));
}
void Position(uint32_t address, float value) {
  Write(memory.data(), address, std::bit_cast<uint32_t>(value));
}
void Setup(unsigned extra_rows) {
  memory.fill(0);
  Write(memory.data(), kMenu + 0x170, 0x4000);
  Write(memory.data(), kMenu + 0x174, 0x4500);
  Write(memory.data(), 0x406C, 0x4400);
  Write(memory.data(), 0x4428, 0x11111112);
  Write(memory.data(), 0x4600, 0x11111113);
  Write(memory.data(), 0x4140, 0x4800); // component's native list
  Write(memory.data(), 0x4930, 0x4A00); // list row vector
  Write(memory.data(), 0x4934, 0x4A00 + (3 + extra_rows) * 4);
  Write(memory.data(), 0x4500, 0x4B00); // title data source
  Write(memory.data(), 0x4B04, 0x11111114); // source count
  Write(memory.data(), 0x4C80, 0x11111115); // focus/unfocus
  Write(memory.data(), 0x4C88, 0x11111115);
  empty_pause_source = 0;
  opening_graphics = false;
  graphics_screen = 0;
  title_offsets.clear();
  main_menu = kMenu;
  title_layout_pending = false;
  Write(memory.data(), kMenu + 0x260, 12);
  for (unsigned i = 0; i < 3 + extra_rows; ++i) {
    const uint32_t row = 0x1000 + i * 0x400;
    Write(memory.data(), 0x4A00 + i * 4, row);
    Write(memory.data(), row, 0x4C00);
    const uint32_t node = row + 0x40, object = row + 0x80;
    const uint32_t child = row + 0x180, child_object = row + 0x1C0;
    Write(memory.data(), row + 0x2C, node);
    Write(memory.data(), node + 4, object);
    Write(memory.data(), object, 0x7000); // transform owner
    Write(memory.data(), object + 4, node);
    Write(memory.data(), node + 0x14, child);
    Write(memory.data(), child + 4, child_object);
    Write(memory.data(), child_object, 0x7000);
    Write(memory.data(), child_object + 4, child);
    for (auto offset : {0x10u, 0x70u, 0xB0u})
      Position(object + offset, -float(i) * kSpacing);
    Position(child_object + 0xB0, -float(i) * kSpacing + 5);
    const float shift = kSpacing * extra_rows;
    title_offsets.push_back({row, node, shift});
    ShiftTitleRow(memory.data(), node, shift);
  }
}
void CheckPositions(unsigned rows, unsigned shift_rows) {
  for (unsigned i = 0; i < rows; ++i) {
    const auto row = 0x1000 + i * 0x400;
    const float expected = (float(shift_rows) - float(i)) * kSpacing;
    for (auto offset : {0x10u, 0x70u, 0xB0u})
      Check(Position(row + 0x80 + offset) == expected,
            "row local, cached and world positions stay aligned");
    Check(Position(row + 0x1C0 + 0xB0) == expected + 5,
          "child text/hit geometry follows its row");
  }
}
void Select(uint32_t id, uint32_t menu = kMenu, uint32_t event = 2) {
  PPCContext ctx{};
  ctx.r3.u32 = menu;
  ctx.r4.u32 = event;
  ctx.r6.u32 = id;
  sub_82836928(ctx, memory.data());
}
} // namespace

// A modal leaves CSplash in state 12; a departure switches state and owns
// the old scene. Graphics uses the synchronous native-call fixture below.
extern "C" REX_FUNC(__imp__sub_82836928) {
  ++native_calls;
  if (leave_title) Write(base, kMenu + 0x260, 13);
}
extern "C" REX_FUNC(OriginalBind) { Unexpected(); }
extern "C" REX_FUNC(OriginalSelect) { Unexpected(); }
extern "C" REX_FUNC(__imp__sub_82F2A8D0) { Unexpected(); }
extern "C" REX_FUNC(__imp__sub_827303E8) { Unexpected(); }
extern "C" REX_FUNC(__imp__sub_82739190) { Unexpected(); }
extern "C" REX_FUNC(__imp__sub_827142C0) { Unexpected(); }
extern "C" REX_FUNC(__imp__sub_82736B08) { Unexpected(); }
extern "C" REX_FUNC(__imp__sub_8283CB68) { Unexpected(); }
namespace forza::benchmark {
void Show() { ++benchmark_calls; }
void TitleReady(uint32_t, uint32_t) { Unexpected(); }
}
// Tuning is a pause-only dependency of graphics_menu.cpp. Title selections must
// never enter its runtime; fail immediately if this fixture reaches that path.
namespace forza::online_photo {
bool WantsPauseRow(const PPCContext&, uint8_t*, uint32_t) { return false; }  // offline here
bool Active() { return false; }
bool Enabled() { return false; }
}  // namespace forza::online_photo

namespace forza::car_tuning_menu {
bool Available(const PPCContext&, uint8_t*, uint32_t) { Unexpected(); return false; }
bool Open(const PPCContext&, uint8_t*, uint32_t) { Unexpected(); return false; }
bool TryBind(PPCContext&, uint8_t*) { Unexpected(); return false; }
bool TryNotify(PPCContext&, uint8_t*) { Unexpected(); return false; }
bool TryDestroy(PPCContext&, uint8_t*) { Unexpected(); return false; }
bool TryInput(PPCContext&, uint8_t*) { Unexpected(); return false; }
void Tick(const PPCContext&, uint8_t*) { Check(graphics_snapshot, "title layout tick only"); }
}
// Custom Race shares the difficulty-style screen hooks; it never claims a title menu.
namespace forza::custom_race {
bool TryBind(PPCContext&, uint8_t*) { return false; }
bool TryNotify(PPCContext&, uint8_t*) { return false; }
bool TryDestroy(PPCContext&, uint8_t*) { return false; }
bool TryInput(PPCContext&, uint8_t*) { return false; }
void Tick(PPCContext&, uint8_t*) {}
}
namespace forza::ultrawide {
Extent OutputSize() { Check(graphics_snapshot, "snapshot only for Graphics"); return {}; }
Extent DrawingSize() { Unexpected(); return {}; }
}
namespace forza::frame_time {
int VblankHz() { Unexpected(); return 60; }
void ResetRenderTiming() { Unexpected(); }
}
// The shared row bridge probes whether the active menu belongs to native pause.
// This fixture owns only the title menu, so these checked probes return false.
// Actual pause/tuning operations remain forbidden by the guards above.
namespace forza::native_quit {
void Cancel() {} // The title-only fixture has no pending pause exit.
void Title(PPCContext&, uint8_t*, uint32_t) { Unexpected(); }
}
namespace forza::native_pause_scene {
bool Owns(uint32_t screen) {
  Check(from_main_menu && screen == kMenu, "ownership probe stays on the title owner");
  return false;
}
bool Page(PPCContext&, uint8_t* base, uint32_t screen, bool) {
  Check(from_main_menu && screen == kMenu && base == memory.data(),
        "shared row bridge probes only the title fixture");
  return false;
}
}
namespace forza {
void UpdatePerformanceOverlay() { Check(graphics_snapshot, "title layout tick only"); }
}

namespace {
REX_FUNC(FixtureAllocate) {
  Check(ctx.r3.u32 == 0x88 || ctx.r3.u32 == 0x38 || ctx.r3.u32 == 16,
        "only expected native allocations");
  ctx.r3.u32 = ctx.r3.u32 == 0x88 ? (fail_params ? 0 : 0x7000) :
               ctx.r3.u32 == 0x38 ? (fail_empty ? 0 : 0x7200) : 0x7100;
}
REX_FUNC(FixtureNoop) {}
REX_FUNC(FixtureCount) { ctx.r3.u32 = expected_rows; }
REX_FUNC(FixtureEmptySource) { Write(base, ctx.r3.u32, 0x4600); }
REX_FUNC(FixtureOpen) {
  ++opened;
  CheckPositions(expected_rows, expected_shift);
  Check(ctx.r3.u32 == kMenu && ctx.r4.u32 == 5,
        "Graphics queues the native difficulty page on the title owner");
}
REX_FUNC(FixtureBindSource) {
  ++hiding;
  Check(ctx.r3.u32 == 0x4000 && ctx.r4.u32 == (showing_rows ? 0x4500 : 0x7200),
        "native component binds the expected visible/empty source");
  CheckPositions(expected_rows, 0);
}
}
namespace rex::runtime {
PPCFunc* ResolveIndirectFunction(uint32_t address) {
  switch (address) {
    case 0x824265C8: return FixtureAllocate;
    case 0x82675B00: return FixtureOpen;
    case 0x828EED20: return FixtureEmptySource;
    case 0x828B82C8: return FixtureBindSource;
    case 0x11111114: return FixtureCount;
    case 0x825A5640: case 0x82D6DE20: case 0x82555B90:
    case 0x82471820: case 0x824CEAB0: case 0x11111112:
    case 0x11111113: case 0x11111115:
      return FixtureNoop;
    default: Unexpected(); return nullptr;
  }
}
}

int main() {
  for (unsigned extra_rows : {1u, 2u}) {
    Setup(extra_rows);
    const auto shifted = memory;
    // #223: repeatedly select Single Player, Multiplayer and Horizon Rally.
    // Native modal opening/dismissal retains the same title scene and state.
    for (unsigned repeat = 0; repeat < 20; ++repeat) {
      for (uint32_t id : {10u, 11u, 9u}) {
        const auto before = native_calls;
        Select(id);
        Check(native_calls == before + 1, "stock choice forwarded exactly once");
        Check(memory == shifted, "stock modal does not restore or re-shift rows");
        Check(title_offsets.size() == 3 + extra_rows, "modal retains row references");
        CheckPositions(3 + extra_rows, extra_rows);
      }
    }
    const auto before = native_calls;
    Select(kBenchmark);
    Check(benchmark_calls == extra_rows, "benchmark setup dispatched once");
    Check(native_calls == before && memory == shifted,
          "benchmark overlay retains the title and does not call native selection");

    // A real departure must forget the old nodes without traversing/moving them.
    leave_title = true;
    Select(10);
    Check(title_offsets.empty() && !title_layout_pending, "departure forgets title rows");
    CheckPositions(3 + extra_rows, extra_rows);
    RestoreTitle(memory.data());
    CheckPositions(3 + extra_rows, extra_rows);
    leave_title = false;

    // Repeated restore calls and fresh title lifetimes must not accumulate
    // a translation.
    Setup(extra_rows);
    RestoreTitle(memory.data());
    RestoreTitle(memory.data());
    CheckPositions(3 + extra_rows, 0);
    Check(title_offsets.empty(), "restoring consumes every saved offset");
  }

  Setup(2);
  const auto shifted = memory;
  leave_title = true;
  Select(10, kMenu + 0x400); // another menu's callback cannot discard these rows
  Check(title_offsets.size() == 5, "unrelated menu retains title offsets");
  Write(memory.data(), kMenu + 0x260, 12);
  Select(10, kMenu, 1); // nor can a non-selection event
  Check(title_offsets.size() == 5, "non-selection event retains title offsets");
  Write(memory.data(), kMenu + 0x260, 12);
  Check(memory == shifted, "unrelated callbacks do not move title rows");
  // Graphics activation returns before the asynchronous native page bind.
  // The old hook restored the still-visible title at this point (#223).
  leave_title = false;
  for (unsigned extras : {1u, 2u}) {
    expected_rows = 3 + extras;
    expected_shift = extras;
    graphics_snapshot = true;
    for (unsigned failure : {1u, 2u, 0u}) {
      Setup(extras);
      fixture_memory.fail = failure == 1;
      fail_params = failure == 2;
      const auto before = native_calls;
      const auto before_open = opened;
      Select(kGraphics);
      Check(native_calls == before, "Graphics never dispatches a stock choice");
      Check(opened == before_open + (failure == 0), "native open only after allocation succeeds");
      CheckPositions(expected_rows, expected_shift);
      Check(title_offsets.size() == expected_rows,
            "visible title retains its offsets until native rows are hidden");
      Check(!title_layout_pending, "queued Graphics must not re-layout a visible title");
      if (failure != 0) {
        Select(9);
        Check(native_calls == before + 1, "failed Graphics leaves native title choices usable");
        CheckPositions(expected_rows, expected_shift);
      }
      if (failure == 0) {
        PPCContext ctx{};
        Native ui(ctx, memory.data());
        const auto before_hide = hiding;
        fail_empty = true;
        PauseRows(ui, memory.data(), false);
        Check(hiding == before_hide && title_offsets.size() == expected_rows,
              "failed empty-source allocation keeps visible rows and references");
        CheckPositions(expected_rows, expected_shift);
        fail_empty = false;
        PauseRows(ui, memory.data(), false);
        Check(hiding == before_hide + 1 && title_offsets.empty(),
              "hide restores live rows before the native source can retire them");
        CheckPositions(expected_rows, 0);
        // Current main has both Graphics and Benchmark. Back rebinds the title
        // and Tick reacquires and shifts the new live rows once. Repeat the
        // actual open/hide/back/layout lifecycle rather than manually restoring.
        if (extras == 2) {
          // Rows listed before their scene nodes exist (+0x2C is 0) are waited for, not read
          // through: that read is guest address 4. Plant a row-like object there, which
          // a read through the missing node would shift the title by.
          {
            RestoreTitle(memory.data());
            const auto saved = memory;
            const uint32_t first_row = Read(memory.data(), 0x4A00),
                           second_row = Read(memory.data(), 0x4A04);
            Write(memory.data(), 4, 0x7100);
            Position(0x7100 + 0xB0, 50);
            title_layout_pending = true;
            for (auto row : {first_row, second_row}) {
              const auto node = Read(memory.data(), row + 0x2C);
              Write(memory.data(), row + 0x2C, 0);
              Tick(ctx, memory.data());
              Check(title_layout_pending && title_offsets.empty(),
                    "Tick waits for a row without a scene node");
              Write(memory.data(), row + 0x2C, node);
            }
            Write(memory.data(), 4, 0);
            Write(memory.data(), 0x7100 + 0xB0, 0);
            Check(memory == saved, "waiting for a row's node moves nothing");
            title_layout_pending = false;
          }
          for (unsigned repeat = 0; repeat < 10; ++repeat) {
            showing_rows = true;
            PauseRows(ui, memory.data(), true);
            showing_rows = false;
            Check(title_layout_pending && title_offsets.empty(),
                  "Back schedules a fresh layout without retaining hidden nodes");
            Tick(ctx, memory.data());
            CheckPositions(expected_rows, expected_shift);
            Check(!title_layout_pending && title_offsets.size() == expected_rows,
                  "Back layout applies exactly one shift to live rows");
            Select(kGraphics);
            CheckPositions(expected_rows, expected_shift);
            PauseRows(ui, memory.data(), false);
            Check(title_offsets.empty(), "repeated hide consumes all live offsets");
            CheckPositions(expected_rows, 0);
          }
        }
      }
    }
    graphics_snapshot = false;
  }
  std::fputs("title menu: modals, graphics open/failure/hide/back, benchmark and departure passed\n", stderr);
}
