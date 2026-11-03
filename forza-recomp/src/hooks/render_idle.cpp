// forza - the render thread waits for the next main frame instead of spinning (#234, docs/perf.md).
//
// The render thread (sub_825A6208, TU4 sub_8261E178) runs one pass of its loop
// (sub_8259F3E8, TU4 sub_826164D0) after another, and a pass that has nothing to draw
// returns at once. There are two such passes:
// - idle: the main frames' queue count (+0xA50) is not past the frame it took last
//   (+0x94C); in mode 2 (+0xCB0, free roam and races) also when the queue has none ready
//   (sub_823F4AE8). It gives the render-thread jobs their time budget and calls
//   SleepEx(0, TRUE) (sub_82A6D7D8, TU4 sub_82B2A848): on the console that yields to the
//   other threads of the core; here it returns at once.
// - empty: the count says there is a frame but the queue has none to take yet
//   (sub_824748F8, TU4 sub_824A4070: its list +0x20 and current +0x38 are empty). In the
//   menus (mode 0) the count runs ahead of the queue, so every pass between two frames is
//   one of these, with no sleep at all.
// So between two main frames the thread runs pass after pass: a whole core behind the main
// menu, 19,000 idle passes a second in free roam. There a pass took ~30 us, nearly all of
// it the screen gamma transition it begins with (sub_82586CD8, TU4 sub_825FCD50), which
// never ended and rebuilt the gamma ramp in every pass (gamma_transition.cpp now ends it).
// That was 20 ms of the render thread's CPU in each 33 ms frame.
//
// Now such a pass, at the idle pass's SleepEx and at the empty pass's end (its call of
// sub_82DD9D58, TU4 sub_82E9C3B8, with no frame taken: r30 < 0), keeps the game's loop for
// the first 20 us since the thread last took a frame, yields up to 200 us, then sleeps in
// 50 us steps until the pass would find work: a frame to take (the same tests as the pass),
// a message (+0x8, the size of the list the pass handles) or a job in the job manager's
// list (sub_82E4B2C0's list 1), or 1 ms has passed. A pass that finds jobs queued goes on
// at once, as before. Each call site is told by its return address. The sleeps: Linux and
// macOS nanosleep (Linux: timer slack 5 us); Windows a high-resolution waitable timer
// (Windows 10 1803 and later; Sleep and SleepEx count whole milliseconds), and on older
// versions, which have none, a yield (SwitchToThread) in its place.
// FORZA_RENDER_IDLE=spin keeps the game's loop; FORZA_RENDER_IDLE_LOG=1 logs every 10 s.

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/thread.h>

#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002  // Windows 10 1803
#endif
#else
#include <time.h>
#endif
#ifdef __linux__
#include <sys/prctl.h>
#endif

namespace {

using Clock = std::chrono::steady_clock;

constexpr uint32_t kIdleSleepReturn = FORZA_ADDR(0x8259FBEC);  // the idle pass's SleepEx call
constexpr uint32_t kPassEndReturn = FORZA_ADDR(0x8259FB5C);    // the pass's sub_82DD9D58 call
constexpr uint32_t kJobManager = FORZA_ADDR(0x834B36A8);       // pointer; sub_82E4B2C0's r3
constexpr uint32_t kJobList = 0x280;  // list 1 (the pass's): begin +4, end +8

// The render object (r31 in the pass).
constexpr uint32_t kMessages = 0x8;       // size of the message list the pass handles
constexpr uint32_t kTaken = 0x94C;        // the last main frame it took
constexpr uint32_t kQueue = 0x9D8;        // the main frames' queue:
constexpr uint32_t kQueueList = 0x20;     //   frames to take (size)
constexpr uint32_t kQueueCurrent = 0x38;  //   the frame taken
constexpr uint32_t kQueueHeld = 0x3C;     //   sub_823F4AE8: 1 if set, else +0x80
constexpr uint32_t kQueueCount = 0x78;    //   main frames counted so far (sub_82C09BE0)
constexpr uint32_t kQueueReady = 0x80;
constexpr uint32_t kMode = 0xCB0;         // 2: a frame also needs sub_823F4AE8 != 0
constexpr uint32_t kGammaStart = 0x16E0;  // double; > 0 while the gamma transition runs

constexpr auto kSpin = std::chrono::microseconds(20);
constexpr auto kYield = std::chrono::microseconds(200);
constexpr auto kMaxWait = std::chrono::milliseconds(1);
constexpr long kSleepNs = 50'000;

enum class Pass { kIdle, kEmpty };

bool Spin() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_RENDER_IDLE");
    return v && std::strcmp(v, "spin") == 0;
  }();
  return on;
}

bool Log() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_RENDER_IDLE_LOG");
    return v && *v && *v != '0';
  }();
  return on;
}

uint32_t Load32(uint8_t* base, uint32_t address) {
  const auto* p = reinterpret_cast<const volatile uint8_t*>(forza::GuestPtr(base, address));
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

// The pass would take a frame: as it tests before its idle path (idle) or in sub_824748F8
// (empty).
bool FrameReady(uint8_t* base, uint32_t render, uint32_t taken, Pass pass) {
  const uint32_t q = render + kQueue;
  if (pass == Pass::kEmpty)
    return Load32(base, q + kQueueList) != 0 || Load32(base, q + kQueueCurrent) != 0;
  if (Load32(base, q + kQueueCount) <= taken) return false;
  return Load32(base, render + kMode) != 2 || Load32(base, q + kQueueHeld) != 0 ||
         Load32(base, q + kQueueReady) != 0;
}

bool WorkQueued(uint8_t* base, uint32_t render) {
  if (Load32(base, render + kMessages) != 0) return true;
  const uint32_t jobs = Load32(base, kJobManager);
  if (jobs < 0x10000 || jobs >= 0xF0000000) return false;
  return Load32(base, jobs + kJobList + 4) != Load32(base, jobs + kJobList + 8);
}

struct Stats {
  Clock::time_point window_start;
  uint64_t idle = 0, empty = 0, busy = 0, waits = 0, timeouts = 0, gamma = 0;
  Clock::duration waited{};
};

struct IdleState {
  uint32_t taken = 0xFFFFFFFF;
  Clock::time_point start;
  bool sleep_ready = false;
#ifdef _WIN32
  HANDLE timer = nullptr;
  ~IdleState() {
    if (timer) CloseHandle(timer);
  }
#endif
  Stats stats;
};
thread_local IdleState t_idle;

// Sleeps about kSleepNs.
void SleepStep(IdleState& s) {
#ifdef _WIN32
  if (!s.sleep_ready) {
    s.sleep_ready = true;
    s.timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                                     SYNCHRONIZE | TIMER_MODIFY_STATE);
    if (!s.timer)
      REXLOG_INFO("RENDERIDLE: no high-resolution timer (error {}); yielding instead",
                  GetLastError());
  }
  LARGE_INTEGER due;
  due.QuadPart = -(kSleepNs / 100);  // relative, in 100 ns units
  if (s.timer && SetWaitableTimer(s.timer, &due, 0, nullptr, nullptr, FALSE) &&
      WaitForSingleObject(s.timer, INFINITE) == WAIT_OBJECT_0)
    return;
  rex::thread::MaybeYield();
#else
#ifdef __linux__
  if (!s.sleep_ready) {
    prctl(PR_SET_TIMERSLACK, 5000UL);
    s.sleep_ready = true;
  }
#endif
  timespec ts{0, kSleepNs};
  nanosleep(&ts, nullptr);
#endif
}

void LogStats(IdleState& s, uint8_t* base, uint32_t render, Pass pass, Clock::time_point now) {
  Stats& st = s.stats;
  (pass == Pass::kIdle ? st.idle : st.empty)++;
  const uint32_t hi = Load32(base, render + kGammaStart);
  if ((hi & 0x7FFFFFFF) != 0 && !(hi & 0x80000000)) st.gamma++;  // a positive double
  if (st.window_start == Clock::time_point{}) st.window_start = now;
  if (now - st.window_start < std::chrono::seconds(10)) return;
  using us = std::chrono::microseconds;
  REXLOG_INFO("RENDERIDLE: idle_passes={} empty_passes={} work_queued={} waits={} timeouts={} "
              "wait_ms={:.1f} gamma_passes={} mode={}",
              st.idle, st.empty, st.busy, st.waits, st.timeouts,
              std::chrono::duration_cast<us>(st.waited).count() / 1000.0, st.gamma,
              Spin() ? "spin" : "wait");
  st = Stats{};
  st.window_start = now;
}

// A pass with nothing to draw is about to return: wait for something to do first.
void WaitForWork(PPCContext& ctx, uint8_t* base, Pass pass) {
  IdleState& s = t_idle;
  const uint32_t render = ctx.r31.u32;
  const uint32_t taken = Load32(base, render + kTaken);
  auto now = Clock::now();
  if (Log()) LogStats(s, base, render, pass, now);
  if (taken != s.taken) {  // the first such pass since it took a frame
    s.taken = taken;
    s.start = now;
  }
  if (Spin()) return;
  const auto idle = now - s.start;
  if (idle < kSpin) return;
  if (idle < kYield) {
    rex::thread::MaybeYield();
    return;
  }
  if (WorkQueued(base, render)) {
    s.stats.busy++;
    return;
  }
  const auto start = now, deadline = now + kMaxWait;
  bool woke = false;
  while (now < deadline) {
    SleepStep(s);
    now = Clock::now();
    if (FrameReady(base, render, taken, pass) || WorkQueued(base, render)) {
      woke = true;
      break;
    }
  }
  s.stats.waits++;
  if (!woke) s.stats.timeouts++;
  s.stats.waited += now - start;
}

}  // namespace

// SleepEx (r3 = milliseconds, r4 = alertable).
extern "C" REX_FUNC(__imp__sub_82A6D7D8);
extern "C" REX_FUNC(sub_82A6D7D8) {
  if (ctx.lr == kIdleSleepReturn && ctx.r3.u32 == 0) WaitForWork(ctx, base, Pass::kIdle);
  __imp__sub_82A6D7D8(ctx, base);
}

// Called once per pass that took the frame path, after it left the queue's critical
// section; r30 is the frame it took, < 0 when the queue had none.
extern "C" REX_FUNC(__imp__sub_82DD9D58);
extern "C" REX_FUNC(sub_82DD9D58) {
  if (ctx.lr == kPassEndReturn && ctx.r30.s32 < 0) WaitForWork(ctx, base, Pass::kEmpty);
  __imp__sub_82DD9D58(ctx, base);
}
