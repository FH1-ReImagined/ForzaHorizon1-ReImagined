// forza - the game's database queries: Title Update 4's car view with indexed joins, and a log
// line for every slow query (#167).
//
// The game keeps its data (cars, upgrades, events, the garage) in an in-memory SQLite 3.6.19
// database and runs its queries synchronously on whichever thread asks, the main loop
// included. Every query string goes through one method that locks the database (a critical
// section at +12), runs each statement of the string and copies all rows into a recordset
// (sub_82C19130, TU4 sub_82CD8FD8; the copy is sub_82C188D0 / sub_82C18D00).
//
// Title Update 4 (the Rally Expansion) creates the view Restrictions_Data_Car at start: every
// car (Data_Car) with IsRally / CouldRally, LEFT JOINed to three derived tables (the level 4
// springs, the level 5 tyres, the level 4 transmissions with their drivetrain). SQLite 3.6.19
// has no automatic indexes: it materializes each derived table and scans it for every car, so
// a query of the view costs cars x (springs + tyres + transmissions) row visits: 0.13 s with
// all DLC (251 cars) on a Ryzen 7 7800X3D, 0.3 s on the PC of #167. The main loop runs
// "SELECT * FROM Restrictions_Data_Car WHERE IsCarInstalled(ModelId) ORDER BY ModelId" twice
// after each load into free roam (fast travel, the end of an event, joining a session), so the
// first seconds of driving had two or three frames that long; an event's sign-up screens run
// three more on another thread, holding the database lock the main loop waits for. The disc
// version has no such view.
//
// The view is created instead with the same columns (names, order, declared types) and rows
// from joins on the tables themselves: the springs and tyres with their level in the ON
// clause; the transmissions through the car's drivetrains that have a level 4 transmission
// (EXISTS), then those transmissions. Each row of the game's view is one (drivetrain,
// transmission) pair, or NULLs when the car has none; the drivetrain join keeps exactly the
// drivetrains with a pair, so the rows are the same, NULLs included (tools/check_car_view.py
// compares both on your game files, docs/perf.md). Indexes on the joined columns, created after
// the view, make each join a lookup: 17 ms instead of 131 for the main loop's query. When the
// game rebuilds its database it copies the tables without their indexes and runs each view's
// stored SQL again: our view's statement is recognized too and gets the indexes again
// (src/platform/car_view.h). FORZA_GAME_CAR_VIEW=1 keeps the game's view.
//
// The club garage's insert of a shared car gets a second statement that names the car from
// Data_Car, DLC included: the server knows no names and not every car (#697,
// src/platform/club_garage.h).
//
// A query that takes 50 ms or more logs (at most 100 lines a session)
//   FORZA_SQL: 131 ms (lock 0 ms) on thread F800007C: SELECT * FROM Restrictions_Data_Car ...
// (lock: the time outside the statements, waiting for the database's lock; the copy routines
// call each other, only the outermost is timed) and FORZA_SQL_LOG=1 logs every query.

#include "../platform/car_view.h"
#include "../platform/club_garage.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>
#include <rex/system/xthread.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

extern "C" REX_FUNC(__imp__sub_82C19130);
extern "C" REX_FUNC(__imp__sub_82C188D0);
extern "C" REX_FUNC(__imp__sub_82C18D00);

namespace {

constexpr int64_t kSlowUs = 50000;
constexpr int kMaxLines = 100;
std::atomic<int> lines{0};
thread_local forza::car_view::NestedTimer run_time;  // the statements' time in queries

bool EnvOn(const char* name) {
  const char* v = std::getenv(name);
  return v && *v && *v != '0';
}

bool LogAll() {
  static const bool on = EnvOn("FORZA_SQL_LOG");
  return on;
}

int64_t NowUs() {
  return std::chrono::duration_cast<std::chrono::microseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

std::string Sql(uint8_t* base, uint32_t address) {
  if (!address) return "(none)";
  const char* s = reinterpret_cast<const char*>(forza::GuestPtr(base, address));
  const size_t n = strnlen(s, 400);
  std::string out(s, n);
  for (char& c : out)
    if (c == '\n' || c == '\r' || c == '\t') c = ' ';
  if (n == 400) out += "...";
  return out;
}

// The guest copy of kIndexedView (0 when the game's view is kept or no memory was had).
uint32_t IndexedViewSql() {
  static const uint32_t address = [] {
    if (EnvOn("FORZA_GAME_CAR_VIEW")) {
      REXLOG_INFO("Restrictions_Data_Car: the game's view (FORZA_GAME_CAR_VIEW)");
      return 0u;
    }
    constexpr auto& sql = forza::car_view::kIndexedView;
    const uint32_t a = REX_KERNEL_MEMORY()->SystemHeapAlloc(sizeof(sql));
    if (!a) return 0u;
    std::memcpy(REX_KERNEL_MEMORY()->TranslateVirtual(a), sql, sizeof(sql));
    REXLOG_INFO("Restrictions_Data_Car: the car view joins by index (#167; FORZA_GAME_CAR_VIEW=1 "
                "keeps the game's)");
    return a;
  }();
  return address;
}

// A guest copy of the club garage's insert with the statement that names its car (0 when
// there was no memory); the caller frees it after the query.
uint32_t SharedCarInsertSql(const char* insert) {
  const std::string sql = forza::club_garage::WithGameCarNames(insert);
  const uint32_t a = REX_KERNEL_MEMORY()->SystemHeapAlloc(uint32_t(sql.size() + 1));
  if (!a) return 0;
  std::memcpy(REX_KERNEL_MEMORY()->TranslateVirtual(a), sql.c_str(), sql.size() + 1);
  static std::atomic<bool> logged{false};
  if (!logged.exchange(true)) {
    REXLOG_INFO("Club garage: shared cars named from the game's car list (#697)");
  }
  return a;
}

template <void (*Run)(PPCContext&, uint8_t*)>
void Timed(PPCContext& ctx, uint8_t* base) {
  run_time.Enter(NowUs());
  Run(ctx, base);
  run_time.Exit(NowUs());
}

}  // namespace

// Database::Query(result*, database*, const char* sql)
extern "C" REX_FUNC(sub_82C19130) {
  // The game's view, or ours as the game runs it again from sqlite_master: the view and indexes.
  if (ctx.r5.u32 && forza::car_view::Classify(reinterpret_cast<const char*>(forza::GuestPtr(
                        base, ctx.r5.u32))) != forza::car_view::Statement::kOther) {
    if (const uint32_t view = IndexedViewSql()) ctx.r5.u64 = view;
  }
  const uint32_t sql = ctx.r5.u32;  // logged: the game's string, not the copy below
  uint32_t shared_car = 0;
  if (sql && forza::club_garage::IsSharedCarInsert(
                 reinterpret_cast<const char*>(forza::GuestPtr(base, sql)))) {
    shared_car = SharedCarInsertSql(reinterpret_cast<const char*>(forza::GuestPtr(base, sql)));
    if (shared_car) ctx.r5.u64 = shared_car;
  }
  const int64_t run0 = run_time.total();
  const int64_t t0 = NowUs();
  __imp__sub_82C19130(ctx, base);
  const int64_t us = NowUs() - t0;
  if (shared_car) REX_KERNEL_MEMORY()->SystemHeapFree(shared_car);
  const int64_t run_us = run_time.total() - run0;
  const bool all = LogAll();
  if (us < kSlowUs && !all) return;
  if (!all && lines.fetch_add(1, std::memory_order_relaxed) >= kMaxLines) return;
  const uint32_t thread = rex::system::XThread::IsInThread()
                              ? rex::system::XThread::GetCurrentThreadHandle()
                              : 0;
  const std::string text = Sql(base, sql);
  if (us >= kSlowUs) {
    REXLOG_WARN("FORZA_SQL: {} ms (lock {} ms) on thread {:08X}: {}", us / 1000,
                (us - run_us) / 1000, thread, text);
  } else {
    REXLOG_INFO("FORZA_SQL: {:.2f} ms on thread {:08X}: {}", us / 1000.0, thread, text);
  }
}

extern "C" REX_FUNC(sub_82C188D0) { Timed<__imp__sub_82C188D0>(ctx, base); }
extern "C" REX_FUNC(sub_82C18D00) { Timed<__imp__sub_82C18D00>(ctx, base); }
