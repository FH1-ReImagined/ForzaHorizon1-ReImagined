// forza - Custom Race (src/platform/custom_race.h): replay a won festival or street event
// with your own car class, laps, number of opponents and time of day.
//
// Flow (Title Update 4; every address below is TU4's):
//
// 1. The event's title card is ForzaUI::CCareerEventDescription (vtable 0x8204B7FC),
//    shown by the CShowUIScreen_CareerEventDescription state of
//    gamemodes/career_event_activations.xml. Its bind (vtable slot 5, sub_82733A68) reads
//    the current career event id from *(0x833CAACC)+0x388. After it, this file checks the
//    event (festival or street race, no provided car) and whether it has been won
//    (sub_82522108(profile, event, &place), the query behind the card's BEST FINISH, place
//    1) and that the player is offline (the native car tuning's gate: Colorado free roam,
//    no Forza Live network, no session), and adds SELECT "CUSTOM RACE" to the card's footer (slot 13 of the footer's
//    17 button slots is the Back/Select icon, table 0x83333B98).
// 2. The card's input handler (slot 9, sub_82738948 -> sub_82736150) handles A 0x5D,
//    B 0x60, X 0x99 (Rivals), Y 0x9C (restrictions) and two more. SELECT is not among
//    them; CustomRacePad() (player 1's pad, kernel/input_autopress.cpp) notes its press
//    and the hook opens the Custom Race page when the card gets that input.
// 3. The page is the game's difficulty-style options screen opened with event 5 and a
//    params object, the way src/hooks/graphics_menu.cpp opens Graphics from the pause
//    menu; graphics_menu.cpp owns that screen's hooks and hands them here first while
//    this page is the one open. Rows: CAR CLASS, LAPS (circuits only), OPPONENTS (1-7),
//    TIME OF DAY. B closes it (sub_827F20F0) and cancels; A arms the custom race and
//    closes it.
// 4. Arming runs SQL through the game's own database object (*(0x833CAAE4) ->
//    sub_82F1E918 -> vfunc 0x20, the path every "SELECT ... FROM Events WHERE Id=%d" of
//    the card takes): TEMP copies of Events, Races, EventParticipants, EventRestrictions
//    and EventRecommendedCars with this event's rows changed (the statements are in
//    src/platform/custom_race.h). TEMP tables are searched before the main database, so the
//    car registration's class filter, the dealer, the race setup (CLoadIntoCareerRace) and
//    the CPU grid all read the custom values. The opponents
//    are the event's own named drivers in cars of the chosen class (Data_Car.ClassID,
//    installed, selectable), in random order.
// 5. Back on the card, an A press is added to the pad (CustomRacePad), only while the card is
//    the active screen (from the page's destroy until the card takes it, gives the input to
//    the page again or is destroyed) and never while another overlay blocks the pad: the
//    card's own A runs
//    in the UI dispatcher as for a normal entry and opens the event's car registration
//    (with BUY NEW CAR), which offers only cars of the chosen class (or below); its A
//    starts the race. Rewards are the event's own (paid as usual).
// 6. The shadow tables are dropped when the card binds again without a pending race (B
//    back from the car registration, the next visit), when the page is cancelled, and when the
//    player is back in free roam after the race.
//
// FORZA_CUSTOM_RACE_TRACE=1 logs each step. Off by default: cvar custom_race (the launcher's
// Gameplay > Custom Race). docs/custom-race.md describes it for players.

#include "../platform/custom_race.h"
#include "../platform/guest_addr.h"

#include <rex/cvar.h>
#include <rex/logging.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <string>

REXCVAR_DEFINE_BOOL(custom_race, false, "Game",
                    "Custom Race: SELECT on a won festival/street event's title card opens a "
                    "page to replay it with your own class, laps, opponents and time of day "
                    "(offline only)");


#if FORZA_TU4
#include "../kernel/live.h"
#include "../platform/pause_car_access.h"
#include "../platform/guest_memory.h"

#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>

#include <algorithm>
#include <bit>
#include <cstring>
#include <initializer_list>
#include <mutex>

extern "C" REX_FUNC(__imp__sub_82733A68);  // CCareerEventDescription bind (slot 5)
extern "C" REX_FUNC(__imp__sub_82738948);  // its input handler (slot 9)
extern "C" REX_FUNC(__imp__sub_82727530);  // its destructor (slot 1)
extern "C" REX_FUNC(__imp__sub_82735FD0);  // its message handler (car selection, car check)

namespace forza::custom_race {
namespace {

using Clock = std::chrono::steady_clock;

constexpr uint32_t kDescriptionVtable = 0x8204B7FC;  // ForzaUI::CCareerEventDescription
constexpr uint32_t kCareerGlobals = 0x833CAACC;      // +0x388: current career event id
constexpr uint32_t kDatabaseHolder = 0x833CAAE4;     // game database (sub_82F1E918)
constexpr uint32_t kFooterSlotSelect = 13;           // Back_Button.tga
constexpr uint32_t kEventAccept = 0x5D, kEventBack = 0x60;
constexpr uint16_t kPadBack = 0x0020;                // XINPUT_GAMEPAD_BACK
constexpr uint16_t kPadA = 0x1000;                   // XINPUT_GAMEPAD_A

uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
void Write(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = GuestPtr(base, address);
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
}

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_CUSTOM_RACE_TRACE");
    return v && *v && *v != '0';
  }();
  return on;
}

// Guest calls on a copy of the hooked function's context, with private guest scratch.
// Layout: +0 out slots, +32 UTF-16 text, +1024 names, +2048 SQL (up to 6 KB).
class Ui {
 public:
  Ui(PPCContext& ctx, uint8_t* base)
      : ctx_(ctx), base_(base), scratch_(REX_KERNEL_MEMORY()->SystemHeapAlloc(kSize)) {}
  ~Ui() {
    if (scratch_) REX_KERNEL_MEMORY()->SystemHeapFree(scratch_);
  }
  Ui(const Ui&) = delete;
  Ui& operator=(const Ui&) = delete;
  explicit operator bool() const { return scratch_ != 0; }
  uint8_t* base() const { return base_; }
  uint32_t scratch() const { return scratch_; }
  PPCContext& context() { return ctx_; }

  uint32_t Call(uint32_t address, std::initializer_list<uint32_t> args = {}) {
    PPCFunc* fn = address ? rex::runtime::ResolveIndirectFunction(address) : nullptr;
    if (!fn) return 0;
    unsigned i = 3;
    for (uint32_t value : args) {
      switch (i++) {
        case 3: ctx_.r3.u64 = value; break;
        case 4: ctx_.r4.u64 = value; break;
        case 5: ctx_.r5.u64 = value; break;
        case 6: ctx_.r6.u64 = value; break;
        case 7: ctx_.r7.u64 = value; break;
        case 8: ctx_.r8.u64 = value; break;
        case 9: ctx_.r9.u64 = value; break;
      }
    }
    fn(ctx_, base_);
    return ctx_.r3.u32;
  }
  uint32_t Virtual(uint32_t object, uint32_t offset, std::initializer_list<uint32_t> args) {
    if (!object || !Read(base_, object)) return 0;
    return Call(Read(base_, Read(base_, object) + offset), args);
  }
  void Destroy(uint32_t object) {
    if (object) Virtual(object, 0, {object, 1});
  }
  // A text object (the game's wide string), released with sub_824CEAB0 after use.
  uint32_t Text(const std::string& text) {
    if (!scratch_ || text.size() > 240) return 0;
    auto* out = GuestPtr(base_, scratch_ + 32);
    for (size_t i = 0; i < text.size(); ++i) {
      out[i * 2] = 0;
      out[i * 2 + 1] = uint8_t(text[i]);
    }
    out[text.size() * 2] = out[text.size() * 2 + 1] = 0;
    const uint32_t object = Call(0x824265C8, {16});
    if (!object) return 0;
    Write(base_, object, 0x82001674);
    Write(base_, object + 4, 0);
    Write(base_, object + 8, 0);
    Write(base_, object + 12, 0);
    Call(0x82D6DE20, {object, scratch_ + 32, uint32_t(text.size())});
    Call(0x82555B90, {scratch_, object});
    return scratch_;
  }
  void Release(uint32_t text) {
    if (text) Call(0x824CEAB0, {text});
  }
  uint32_t Name(const std::string& value) {
    if (value.size() >= 1000) return 0;
    std::memcpy(GuestPtr(base_, scratch_ + 1024), value.data(), value.size());
    GuestPtr(base_, scratch_ + 1024)[value.size()] = 0;
    return scratch_ + 1024;
  }
  uint32_t Hash(const std::string& value) { return Call(0x82F354E0, {Name(value)}); }
  void Binding(uint32_t target, const std::string& label) {
    if (uint32_t text = Text(label)) {
      Call(0x82F3A728, {target, text});
      Release(text);
    }
  }
  void Field(uint32_t screen, const char* name, uint32_t binding, const std::string& text) {
    const uint32_t id = Hash(name);
    if (Call(0x82F21830, {screen, id, binding})) Binding(binding, text);
  }
  uint32_t Node(uint32_t screen, const std::string& path) {
    const uint32_t engine = Read(base_, screen + 8);
    if (!engine) return 0;
    const uint32_t access = Call(Read(base_, Read(base_, engine) + 0x1C), {engine});
    const uint32_t name = Name(path);
    return access ? Call(0x82F348B8, {access, Read(base_, screen + 0x58), name, 0}) : 0;
  }
  // The screen's input event among `codes` (vfunc 0xA8 only looks, it doesn't consume).
  uint32_t Event(uint32_t screen, uint32_t input, std::initializer_list<uint32_t> codes) {
    uint32_t i = 0;
    for (uint32_t code : codes) Write(base_, scratch_ + 4 * i++, code);
    const uint32_t found = Virtual(screen, 0xA8, {screen, input, scratch_, i});
    return found ? Read(base_, found) : 0;
  }

  // The game database: run one SQL statement; `value` gets column 0 of the first row.
  bool Sql(const std::string& sql, int32_t* value = nullptr) {
    if (!scratch_ || sql.size() >= kSize - 2048 - 1) return false;
    const uint32_t holder = Read(base_, kDatabaseHolder);
    if (!holder) return false;
    const uint32_t wrapper = Call(0x82F1E918, {holder});
    const uint32_t db = wrapper ? Read(base_, wrapper) : 0;
    if (!db) return false;
    std::memcpy(GuestPtr(base_, scratch_ + 2048), sql.c_str(), sql.size() + 1);
    Write(base_, scratch_, 0);
    Virtual(db, 0x20, {scratch_, db, scratch_ + 2048});
    const uint32_t records = Read(base_, scratch_);
    bool row = false;
    if (records && value) {
      if (int32_t(Virtual(records, 0xB8, {records})) > 0) {  // row count
        *value = int32_t(Virtual(records, 0x90, {records, 0}));  // integer, column 0
        row = true;
      }
    }
    Call(0x824CEAB0, {scratch_});
    if (Trace()) REXLOG_INFO("CUSTOM RACE: SQL {}{}", sql, value ? (row ? " -> row" : " -> none") : "");
    return value ? row : true;
  }

 private:
  static constexpr uint32_t kSize = 8192;
  PPCContext ctx_;
  uint8_t* base_;
  uint32_t scratch_ = 0;
};

// ---- State ------------------------------------------------------------------------

std::mutex mutex;  // the pad thread vs. the UI thread

// The title card on screen, if any, and what we know about its event.
uint32_t card = 0;
bool card_eligible = false;
EventInfo event_info;
Settings settings;  // the page's values (kept per event while the game runs)

// The Custom Race page.
bool opening = false;
Clock::time_point opening_since{};
uint32_t page = 0;  // the difficulty-style screen while it is ours
uint32_t source = 0;
struct Row {
  uint32_t object = 0;
  int kind = 0;  // kClass, kLaps, kOpponents, kTime
  std::vector<uint32_t> values;
};
enum { kClass, kLaps, kOpponents, kTime };
std::vector<Row> rows;
size_t selected_row = 0;

// The shadow tables and the hand-over to the card.
bool armed = false;
bool launch_pending = false;   // A confirmed: waiting for the card to take the added A press
Clock::time_point armed_at{};
bool page_closed = false;      // the page's destroy ran since A
Clock::time_point inject_from{}, inject_until{};
bool launched = false;         // the custom race was handed to native career entry
bool left_free_roam = false;
Clock::time_point free_roam_since{};

// Pad (CustomRacePad, any thread).
std::atomic<int64_t> select_pressed_ms{0};
std::atomic<int64_t> inject_from_ms{0}, inject_until_ms{0};
// The career title card is the active screen: set when the Custom Race page closes over it,
// cleared when the card takes the press, opens the page again or is destroyed. The added A
// is only ever pressed while this is set.
std::atomic<bool> card_active{false};

void StopInjecting() {
  card_active = false;
  inject_from_ms = 0;
  inject_until_ms = 0;
}

int64_t NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch())
      .count();
}

// A SELECT press within the last 400 ms that nobody took yet.
bool TakeSelectPress() {
  int64_t at = select_pressed_ms.load();
  if (!at || NowMs() - at > 400) return false;
  return select_pressed_ms.compare_exchange_strong(at, 0);
}

bool IsCard(uint8_t* base, uint32_t screen) {
  return screen && Read(base, screen) == kDescriptionVtable;
}

bool HasCareerEntry(uint8_t* base, uint32_t screen) {
  // TU4's native A dispatcher (sub_82736150) uses +376, the bound okCareer
  // transition, before taking its career entry path. With no transition it
  // delegates acceptance to the owner: map details uses that to set a route.
  // Neither the car-check result (+374) nor event eligibility (+408) identifies
  // this context. Leave all native fields and entry checks untouched.
  return IsCard(base, screen) && Read(base, screen + 376) != 0;
}

void Disarm(Ui& ui, const char* why) {
  if (!armed) return;
  for (const auto& sql : DisarmStatements()) ui.Sql(sql);
  armed = false;
  launched = false;
  launch_pending = false;
  left_free_roam = false;
  StopInjecting();
  REXLOG_INFO("CUSTOM RACE: cancelled ({})", why);
}

bool Arm(Ui& ui) {
  if (!HasCareerEntry(ui.base(), card)) {
    Disarm(ui, "the card no longer has a career entry");
    return false;
  }
  bool ok = true;
  for (const auto& sql : ArmStatements(settings, IsCircuit(event_info.career_type))) {
    ok = ui.Sql(sql) && ok;
  }
  armed = true;  // drop whatever was created, even after a failure
  armed_at = Clock::now();
  int32_t drivers = 0;
  ui.Sql("SELECT COUNT(*) FROM EventParticipants WHERE EventID=" + std::to_string(settings.event),
         &drivers);
  REXLOG_INFO("CUSTOM RACE: event {} armed: class {} laps {} opponents {} ({} placed) time {}{}",
              settings.event, settings.class_id, settings.laps, settings.opponents, drivers,
              settings.time_of_day, ok ? "" : " (some statements failed)");
  if (!ok || drivers <= 0) {
    Disarm(ui, "the event could not be set up");
    return false;
  }
  return true;
}

// ---- The title card -------------------------------------------------------------------

void ReadEvent(Ui& ui, uint32_t id) {
  event_info = EventInfo{};
  event_info.id = id;
  const std::string e = std::to_string(id);
  int32_t v = 0;
  if (ui.Sql("SELECT CareerTypeId FROM Events WHERE Id=" + e, &v)) event_info.career_type = v;
  if (ui.Sql("SELECT TargetClass FROM Events WHERE Id=" + e, &v)) event_info.target_class = v;
  if (ui.Sql("SELECT NumberOfDrivers FROM Events WHERE Id=" + e, &v)) event_info.drivers = v;
  if (ui.Sql("SELECT TimeOfDayStart FROM Events WHERE Id=" + e, &v)) event_info.time_of_day = v;
  if (ui.Sql("SELECT NumLaps FROM Races WHERE EventId=" + e + " ORDER BY RaceNumber", &v))
    event_info.laps = v;
}

bool Eligible(Ui& ui, uint32_t id) {
  if (!id) return false;
  const std::string e = std::to_string(id);
  int32_t style = 0, provided = 0, races = 0;
  if (!ui.Sql("SELECT CareerEventStyle FROM Events WHERE Id=" + e, &style)) return false;
  ui.Sql("SELECT PlayerCarId FROM Events WHERE Id=" + e, &provided);
  ui.Sql("SELECT COUNT(*) FROM Races WHERE EventId=" + e, &races);
  if ((style != 1 && style != 2) || provided != 0 || races != 1) return false;
  if (!SupportedType(event_info.career_type)) return false;
  // Offline only, as the native car tuning (src/platform/pause_car_access.h): a replay pays
  // XP and credits, which must not reach a profile shared with an online session.
  uint32_t presence = 0, region = 0;
  const bool known = LiveLocalActivity(presence, region);
  const bool network = NetworkOn(), session = LiveCurrentSession().has_value();
  if (!pause_car_access::Allowed(known, presence, region, network, session)) {
    if (Trace()) {
      REXLOG_INFO("CUSTOM RACE: event {}: not offline (presence known {} {} region {} network {} "
                  "session {})", id, known, presence, region, network, session);
    }
    return false;
  }
  // Won: the card's own BEST FINISH query (sub_827209A0).
  const uint32_t profile = ui.Call(0x8274B680);
  if (!profile) return false;
  Write(ui.base(), ui.scratch(), 0);
  const bool found = (ui.Call(0x82522108, {profile, id, ui.scratch()}) & 0xFF) != 0;
  const uint32_t place = Read(ui.base(), ui.scratch());
  if (Trace()) REXLOG_INFO("CUSTOM RACE: event {} style {} best finish {}", id, style, found ? place : 0);
  return found && place == 1;
}

void CardFooter(Ui& ui) {
  if (!card || !card_eligible) return;
  const uint32_t footer = ui.Call(0x82746638, {card});
  if (!footer) return;
  // Already shown (sub_8289A960: the slot is visible). Setting it again adds another prompt.
  if (ui.Call(0x8289A960, {footer, kFooterSlotSelect}) & 0xFF) return;
  if (const uint32_t text = ui.Text("CUSTOM RACE")) {
    ui.Call(0x8289E2B0, {footer, kFooterSlotSelect, text});
    ui.Release(text);
  }
  ui.Virtual(footer + 0x6C, 0x28, {footer + 0x6C});
}

// ---- The Custom Race page ---------------------------------------------------------------

std::string Label(int kind, uint32_t value) {
  switch (kind) {
    case kClass:
      for (const auto& c : kClasses)
        if (c.id == value) return c.label;
      return std::to_string(value);
    case kLaps: return std::to_string(value) + (value == 1 ? " LAP" : " LAPS");
    case kOpponents: return std::to_string(value);
    case kTime:
      for (const auto& t : kTimes)
        if (t.seconds == value) return t.label;
      return std::to_string(value);
  }
  return {};
}

const char* Help(int kind) {
  switch (kind) {
    case kClass: return "The highest car class allowed. You choose your car next; your opponents drive cars of this class.";
    case kLaps: return "Number of laps of the circuit.";
    case kOpponents: return "Number of CPU drivers on the grid, from 1 to 7. You start at the back.";
    case kTime: return "Time of day at the start. Event default keeps the event's own time.";
  }
  return "";
}

uint32_t& Value(int kind) {
  switch (kind) {
    case kClass: return settings.class_id;
    case kLaps: return settings.laps;
    case kOpponents: return settings.opponents;
    default: return settings.time_of_day;
  }
}

void BuildRows() {
  rows.clear();
  Row cls{0, kClass, {}};
  for (const auto& c : kClasses) cls.values.push_back(c.id);
  rows.push_back(cls);
  if (IsCircuit(event_info.career_type)) {
    Row laps{0, kLaps, {}};
    for (uint32_t n = kMinLaps; n <= kMaxLaps; ++n) laps.values.push_back(n);
    rows.push_back(laps);
  }
  Row opp{0, kOpponents, {}};
  for (uint32_t n = kMinOpponents; n <= kMaxOpponents; ++n) opp.values.push_back(n);
  rows.push_back(opp);
  Row tod{0, kTime, {}};
  for (const auto& t : kTimes) tod.values.push_back(t.seconds);
  rows.push_back(tod);
}

uint32_t MakeChoices(Ui& ui, const Row& row) {
  const uint32_t choices = ui.Call(0x824265C8, {0x28});
  if (!choices) return 0;
  ui.Call(0x828DD2A8, {choices, uint32_t(row.values.size())});
  for (size_t j = 0; j < row.values.size(); ++j) {
    const uint32_t text = ui.Text(Label(row.kind, row.values[j]));
    ui.context().f1.f64 = 0;
    ui.Call(0x828DDCA8, {choices, uint32_t(j), text, 0, 0, 0, 1});
    ui.Release(text);
  }
  return choices;
}

void SyncRows(uint8_t* base) {
  for (const auto& row : rows) {
    if (!row.object) continue;
    const uint32_t choice = Read(base, row.object + 8);
    if (choice < row.values.size()) Value(row.kind) = row.values[choice];
  }
}

// Difficulty's earnings strip means nothing here; move it out of the viewport, as the
// Graphics page does (the instance's local and cached matrices, and its children's).
void ShiftOut(uint8_t* base, uint32_t node, float dy) {
  const uint32_t object = Read(base, node + 4);
  if (!object || Read(base, object + 4) != node) return;
  const uint32_t owner = Read(base, object);
  auto shift = [&](uint32_t address) {
    Write(base, address, std::bit_cast<uint32_t>(std::bit_cast<float>(Read(base, address)) + dy));
  };
  shift(object + 0x10);
  shift(object + 0x70);
  std::vector<uint32_t> pending{node};
  for (int guard = 0; !pending.empty() && guard < 4096; ++guard) {
    const uint32_t current = pending.back();
    pending.pop_back();
    const uint32_t rendered = Read(base, current + 4);
    if (rendered && Read(base, rendered) == owner && Read(base, rendered + 4) == current)
      shift(rendered + 0xB0);
    for (uint32_t child = Read(base, current + 0x14); child; child = Read(base, child + 0x10))
      pending.push_back(child);
  }
}

void PageFooter(Ui& ui) {
  const uint32_t footer = ui.Call(0x82746638, {page});
  if (!footer) return;
  ui.Call(0x8289A908, {footer});
  for (auto [index, label] : {std::pair{0u, "RACE"}, std::pair{1u, "BACK"}}) {
    const uint32_t text = ui.Text(label);
    ui.Call(0x8289E2B0, {footer, index, text});
    ui.Release(text);
  }
  ui.Virtual(footer + 0x6C, 0x28, {footer + 0x6C});
}

void OpenPage(Ui& ui) {
  const uint32_t params = ui.Call(0x824265C8, {0x88});
  if (!params) return;
  ui.Call(0x825A5640, {params, 0});
  const uint32_t title = ui.Text("CUSTOM RACE");
  ui.Call(0x82471820, {params + 0xC, title});
  ui.Release(title);
  Write(ui.base(), ui.scratch(), params);
  StopInjecting();  // the page takes the input from the card
  opening = true;
  opening_since = Clock::now();
  select_pressed_ms = 0;
  ui.Call(0x82675B00, {card, 5, ui.scratch(), 0});
  REXLOG_INFO("CUSTOM RACE: opening the page for event {}", event_info.id);
}

}  // namespace

// ---- The difficulty-style screen (called by graphics_menu.cpp's hooks first) ------------

bool TryBind(PPCContext& ctx, uint8_t* base) {
  if (!opening) return false;
  opening = false;
  page = ctx.r3.u32;
  Ui ui(ctx, base);
  if (!ui) return true;
  source = ui.Call(0x824265C8, {0x28});
  ui.Call(0x8267A4B0, {source});
  Write(base, page + 0x1A0, source);
  Write(base, Read(base, page + 0xA4) + 0x1C, source);
  BuildRows();
  for (size_t i = 0; i < rows.size(); ++i) {
    Row& row = rows[i];
    row.object = ui.Call(0x824265C8, {0x1C});
    if (!row.object) continue;
    for (uint32_t offset = 0; offset < 0x1C; offset += 4) Write(base, row.object + offset, 0);
    Write(base, row.object, 0x82049434);  // spinner item
    const char* name = row.kind == kClass ? "CAR CLASS" : row.kind == kLaps ? "LAPS"
                       : row.kind == kOpponents ? "OPPONENTS" : "TIME OF DAY";
    const uint32_t label = ui.Text(name);
    ui.Call(0x82471820, {row.object + 0x10, label});
    ui.Release(label);
    Write(base, row.object + 0xC, uint32_t(i));
    Write(base, row.object + 4, MakeChoices(ui, row));
    const auto at = std::find(row.values.begin(), row.values.end(), Value(row.kind));
    Write(base, row.object + 8, at == row.values.end() ? 0 : uint32_t(at - row.values.begin()));
    Write(base, ui.scratch(), row.object);
    ui.Call(0x826D9C80, {source + 0x18, ui.scratch()});
  }
  SyncRows(base);
  selected_row = 0;
  ui.Call(0x827FBDD0, {page});
  ui.Call(0x82F1EC20, {page, ui.Hash("DIFFICULTY")});
  ui.Field(page, "TEXT_SCREEN_NAME", page + 0x1F4, "CUSTOM RACE");
  ui.Field(page, "TEXT_EARNINGS", page + 0x1E8, "");
  ui.Field(page, "TEXT_TITLE", page + 0x170, "CUSTOM RACE");
  ui.Field(page, "TEXT_DESCRIPTION", page + 0x17C, Help(rows.front().kind));
  for (const char* path : {"parent.Layer.Group_BLADE.Group_EARNINGS",
                           "parent.Layer.Group_BLADE.Group_MENU.BONUS_BAR"}) {
    if (const uint32_t node = ui.Node(page, path)) ShiftOut(base, node, 10000.0f);
  }
  PageFooter(ui);
  REXLOG_INFO("CUSTOM RACE: page built ({} rows)", rows.size());
  return true;
}

bool TryNotify(PPCContext& ctx, uint8_t* base) {
  if (!page || ctx.r3.u32 != page) return false;
  if (ctx.r4.u32 != 1) return true;
  Ui ui(ctx, base);
  if (!ui) return true;
  const uint32_t component = Read(base, page + 0x16C);
  if (ctx.r5.u32 == component + 8) {
    selected_row = ctx.r6.u32 < rows.size() ? ctx.r6.u32 : 0;
  } else {
    SyncRows(base);
  }
  if (!rows.empty()) ui.Binding(page + 0x17C, Help(rows[selected_row].kind));
  return true;
}

bool TryInput(PPCContext& ctx, uint8_t* base) {
  if (!page || ctx.r3.u32 != page) return false;
  Ui ui(ctx, base);
  if (!ui) return true;
  const uint32_t event = ui.Event(page, ctx.r4.u32, {kEventAccept, kEventBack});
  if (event == kEventBack) {
    std::lock_guard<std::mutex> lock(mutex);
    Disarm(ui, "back to the title card");
    ui.Call(0x827F20F0, {page});
    return true;
  }
  if (event == kEventAccept) {
    SyncRows(base);
    std::lock_guard<std::mutex> lock(mutex);
    if (!Arm(ui)) {
      ui.Binding(page + 0x17C, "This race could not be set up. Try another car class.");
      return true;
    }
    launch_pending = true;
    page_closed = false;
    ui.Call(0x827F20F0, {page});
    return true;
  }
  const uint32_t component = Read(base, page + 0x16C);
  if (component) ui.Virtual(component, 0x34, {component, ctx.r4.u32});
  SyncRows(base);
  return true;
}

bool TryDestroy(PPCContext& ctx, uint8_t* base) {
  if (!page || ctx.r3.u32 != page) return false;
  Ui ui(ctx, base);
  if (ui) {
    for (const auto& row : rows) {
      if (!row.object) continue;
      ui.Destroy(Read(base, row.object + 4));
      ui.Destroy(row.object);
    }
    if (const uint32_t s = Read(base, page + 0x1A0)) ui.Destroy(s);
    Write(base, page + 0x1A0, 0);
  }
  rows.clear();
  page = 0;
  source = 0;
  std::lock_guard<std::mutex> lock(mutex);
  if (launch_pending && card) {
    // Hand the race to the card: an A press once the card has its input back.
    page_closed = true;
    inject_from = Clock::now() + std::chrono::milliseconds(300);
    inject_until = inject_from + std::chrono::milliseconds(150);
    const int64_t now = NowMs();
    inject_from_ms = now + 300;
    inject_until_ms = now + 450;
    card_active = true;
  }
  return true;
}

void Tick(PPCContext& ctx, uint8_t* base) {
  const auto now = Clock::now();
  if (opening && now - opening_since > std::chrono::seconds(3)) {
    opening = false;
    REXLOG_WARN("CUSTOM RACE: the page did not open (the title card did not take event 5)");
  }
  std::lock_guard<std::mutex> lock(mutex);
  if (launch_pending && ((page_closed && now > inject_until + std::chrono::seconds(2)) ||
                         now - armed_at > std::chrono::seconds(15))) {
    // The card never got the press: don't leave a custom race armed behind a normal card.
    Ui ui(ctx, base);
    if (ui) Disarm(ui, "the title card did not take the race");
    REXLOG_WARN("CUSTOM RACE: the title card did not receive the A hand-over");
  }
  if (!armed || !launched) return;
  // After the race: the title-published presence goes back to single-player free roam (1).
  uint32_t presence = 0, region = 0;
  if (!forza::LiveLocalActivity(presence, region)) return;
  if (presence != 1) {
    left_free_roam = true;
    free_roam_since = {};
  } else if (left_free_roam) {
    if (free_roam_since == Clock::time_point{}) free_roam_since = now;
    if (now - free_roam_since > std::chrono::seconds(3)) {
      Ui ui(ctx, base);
      if (ui) Disarm(ui, "back in free roam");
    }
  }
}

}  // namespace forza::custom_race

using namespace forza::custom_race;
using forza::GuestPtr;

// CCareerEventDescription bind: drop a stale custom race first (unless one is being handed
// to this card), then check the event and offer SELECT.
extern "C" REX_FUNC(sub_82733A68) {
  const uint32_t screen = ctx.r3.u32;
  const bool ours = REXCVAR_GET(custom_race) && IsCard(base, screen);
  if (ours) {
    std::lock_guard<std::mutex> lock(mutex);
    if (armed && !launch_pending) {
      Ui ui(ctx, base);
      if (ui) Disarm(ui, "title card shown again");
    }
  }
  __imp__sub_82733A68(ctx, base);
  if (!ours) return;
  Ui ui(ctx, base);
  if (!ui) return;
  card = screen;
  const uint32_t globals = Read(base, kCareerGlobals);
  const uint32_t id = globals ? Read(base, globals + 0x388) : 0;
  ReadEvent(ui, id);
  card_eligible = HasCareerEntry(base, screen) && Eligible(ui, id);
  if (!card_eligible && launch_pending) {
    std::lock_guard<std::mutex> lock(mutex);
    Disarm(ui, "the rebound card has no eligible career entry");
  }
  if (card_eligible && settings.event != id) settings = Defaults(event_info);
  if (Trace() || card_eligible) {
    REXLOG_INFO("CUSTOM RACE: title card for event {} (type {}, class {}, {} laps, {} drivers): {}",
                id, event_info.career_type, event_info.target_class, event_info.laps,
                event_info.drivers, card_eligible ? "SELECT offers Custom Race" : "not offered");
  }
  CardFooter(ui);
}

// CCareerEventDescription input (also the Nemesis/Showcase cards' slot; only the career
// card is ours).
extern "C" REX_FUNC(sub_82738948) {
  const uint32_t screen = ctx.r3.u32;
  if (screen == card && IsCard(base, screen) && !HasCareerEntry(base, screen)) {
    card_eligible = false;
    StopInjecting();
    if (armed) {
      Ui ui(ctx, base);
      std::lock_guard<std::mutex> lock(mutex);
      if (ui) Disarm(ui, "the card no longer has a career entry");
    }
  }
  if (!REXCVAR_GET(custom_race) || !IsCard(base, screen) || screen != card || !card_eligible) {
    __imp__sub_82738948(ctx, base);
    return;
  }
  Ui ui(ctx, base);
  // The hand-over: the A press CustomRacePad added after the page closed. The card's own A
  // (event 0x5D) then runs as for a normal entry: its car check result (+374) sends it to the
  // event's car registration (the list with BUY NEW CAR, filtered by the shadowed class),
  // whose A starts the race, and whose B returns here (the card binds again: cancelled).
  if (ui && launch_pending && armed &&
      ui.Event(screen, ctx.r4.u32, {kEventAccept}) == kEventAccept) {
    {
      std::lock_guard<std::mutex> lock(mutex);
      launch_pending = false;
      StopInjecting();
      launched = true;
      left_free_roam = false;
      if (Trace()) {
        REXLOG_INFO("CUSTOM RACE: card +374 {} +376 {:08X} +392 {:08X} +408 {}",
                    GuestPtr(base, screen + 374)[0], Read(base, screen + 376),
                    Read(base, screen + 392), Read(base, screen + 408));
      }
      REXLOG_INFO("CUSTOM RACE: event {}: handing class {} to native career entry", settings.event,
                  settings.class_id);
    }
    __imp__sub_82738948(ctx, base);
    return;
  }
  if (ui && TakeSelectPress()) {
    std::lock_guard<std::mutex> lock(mutex);
    if (!launch_pending && !opening && !page) OpenPage(ui);
    ctx.r3.u64 = 1;
    return;
  }
  if (ui && ui.Event(screen, ctx.r4.u32, {kEventBack}) == kEventBack) {
    std::lock_guard<std::mutex> lock(mutex);
    if (!launch_pending) Disarm(ui, "left the title card");
  }
  const uint32_t handled = (__imp__sub_82738948(ctx, base), ctx.r3.u32);
  if (ui && card == screen) CardFooter(ui);  // only if a stock action removed the prompt
  ctx.r3.u64 = handled;
}

// The card's message handler (sub_82735FD0, shared with other screens; only the career card
// is ours): FORZA_CUSTOM_RACE_TRACE logs what the card is told (r4 message, r6 data: +4 kind,
// +12 result).
extern "C" REX_FUNC(sub_82735FD0) {
  const uint32_t screen = ctx.r3.u32, data = ctx.r6.u32;
  if (Trace() && screen && screen == card && IsCard(base, screen)) {
    REXLOG_INFO("CUSTOM RACE: card message {} kind {} result {} (+374 {} +400 {} +408 {})",
                ctx.r4.u32, data ? Read(base, data + 4) : 0, data ? Read(base, data + 12) : 0,
                GuestPtr(base, screen + 374)[0], GuestPtr(base, screen + 400)[0],
                Read(base, screen + 408));
  }
  __imp__sub_82735FD0(ctx, base);
}

// CCareerEventDescription destructor.
extern "C" REX_FUNC(sub_82727530) {
  if (ctx.r3.u32 && ctx.r3.u32 == card) {
    card = 0;
    card_eligible = false;
    StopInjecting();
  }
  __imp__sub_82727530(ctx, base);
}

uint16_t forza::CustomRacePad(uint16_t buttons) {
  const int64_t now = NowMs();
  const int64_t from = inject_from_ms.load(), until = inject_until_ms.load();
  if (card_active && from && now >= from && now < until) buttons |= kPadA;  // the hand-over
  static std::atomic<uint16_t> previous{0};
  const uint16_t before = previous.exchange(buttons);
  if ((buttons & kPadBack) && !(before & kPadBack)) select_pressed_ms = now;
  return buttons;
}

#else  // the disc build has no Custom Race (its screens' addresses differ)

namespace forza::custom_race {
bool TryBind(PPCContext&, uint8_t*) { return false; }
bool TryNotify(PPCContext&, uint8_t*) { return false; }
bool TryDestroy(PPCContext&, uint8_t*) { return false; }
bool TryInput(PPCContext&, uint8_t*) { return false; }
void Tick(PPCContext&, uint8_t*) {}
}  // namespace forza::custom_race
uint16_t forza::CustomRacePad(uint16_t buttons) { return buttons; }

#endif  // FORZA_TU4
