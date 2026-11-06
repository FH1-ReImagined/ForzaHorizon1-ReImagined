// forza - the D3D library's waits for the GPU without a busy loop (#45, docs/perf.md).
//
// When the CPU waits for the GPU (for ring buffer space, for a fence: the render thread
// waits for the previous frames' swaps, so this is where the 30 fps cap is spent),
// sub_823E91F0 and six other D3D functions start a wait record with sub_829F03B0
// (device, reason, start timebase) and call sub_829F04A8 until the GPU has passed the
// point they need. Each call is a few nanoseconds of polling (the console's db16cyc
// delays do nothing here) and a check for a GPU hang; it returns 0 when the wait must
// stop (the device is lost), else 1. In a race the render thread spent 93% of a core in
// that loop (its own work: 7%), taking a core from the command processor and the rest of
// the game wherever they share one (a Steam Deck: 4 cores, 8 threads, one power budget
// with the GPU).
//
// Now each wait keeps the game's loop for its first 20 us (short waits stay as quick),
// then gives the core away between polls: yield up to 200 us, then block.
// - sub_823E91F0's loop (the render thread's waits for the swaps: nearly all of the time
//   waited) compares a word the GPU writes as it executes the ring (device +0x2B10 points
//   to it; in Title Update 4 it sits 0x3C before the read pointer write-back) with the
//   point it needs. It now blocks until that word changes (Linux futex, Windows
//   WaitOnAddress), at most 1 ms per poll: SDK patch 0115 wakes the threads blocked on a
//   word after each write of the command processor to guest memory. The wait ends at the
//   write the poll would have seen, without waking every 50 us (#45: 15,000 wakeups a
//   second in free roam and races, now about 30 a second plus the 1 ms limits while the
//   GPU waits for a vblank). The value the loop compared comes from its registers, so a
//   write between its check and the block is not missed.
// - The other six loops (their conditions can depend on other words) sleep 50 us per
//   poll: Linux and macOS nanosleep (Linux: timer slack 5 us), Windows a high-resolution
//   waitable timer (Windows 10 1803 and later; Sleep counts whole milliseconds, and on
//   older versions each poll yields instead). Windows yielded on every poll before, which
//   kept the waiting thread on a core for the whole wait (#268).
// The setting gpu_wait_blocking turns this on, on every platform by default (Advanced > GPU
// in the launcher). On Windows it was off until it was measured there (#640: a 4-core
// i5-10300H laptop with a GTX 1650 Ti, the built-in benchmark, 3 runs each: the render thread
// 86% of a core -> 45%, 42.7 -> 46.8 fps, p99 33.2 -> 30.9 ms; docs/perf.md). Off, every
// loop waits as before it: 50 us sleeps per poll on Linux and macOS, a yield per poll on
// Windows (SwitchToThread returns at once when no other thread waits for that CPU, so the
// thread stays on it). FORZA_GPU_WAIT=block|sleep|spin overrides it: sleep is the old
// waits, spin the game's loop. FORZA_GPU_WAIT_LOG=1 logs the waits every 10 s (count, total
// and longest time, polls, blocks on the word and those that ran into the 1 ms limit).

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>
#include <rex/thread.h>

#include <cerrno>
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
#pragma comment(lib, "synchronization.lib")
#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002  // Windows 10 1803
#endif
#else
#include <time.h>
#endif
#ifdef __linux__
#include <linux/futex.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

REXCVAR_DEFINE_BOOL(gpu_wait_blocking, true, "GPU",
                    "The render thread blocks until the GPU writes, and sleeps on a timer, while it "
                    "waits for the GPU, instead of polling")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

namespace {

using Clock = std::chrono::steady_clock;

constexpr auto kSpin = std::chrono::microseconds(20);
constexpr auto kYield = std::chrono::microseconds(200);
constexpr long kSleepNs = 50'000;
constexpr long kBlockNs = 1'000'000;  // one block on the GPU's word, at most

// sub_823E91F0's call of sub_829F04A8 (its loop: r10 = device +0x2B1C, r11 = r10 - the
// value of the GPU's word it compared).
constexpr uint32_t kSwapWaitReturn = FORZA_ADDR(0x823E9288);
constexpr uint32_t kGpuWord = 0x2B10;  // device: guest address of the word the GPU writes

// kBlock: this file's waits; kSleep: the waits before it (50 us sleeps, Windows yields);
// kSpin: the game's loop.
enum class Mode { kBlock, kSleep, kSpin };

Mode GetMode() {
  static const Mode mode = [] {
    const char* v = std::getenv("FORZA_GPU_WAIT");
    if (v && std::strcmp(v, "spin") == 0) return Mode::kSpin;
    if (v && std::strcmp(v, "sleep") == 0) return Mode::kSleep;
    if (v && std::strcmp(v, "block") == 0) return Mode::kBlock;
    return REXCVAR_GET(gpu_wait_blocking) ? Mode::kBlock : Mode::kSleep;
  }();
  return mode;
}

bool Log() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_GPU_WAIT_LOG");
    return v && *v && *v != '0';
  }();
  return on;
}

struct WaitState {
  Clock::time_point start, last_poll;
  uint64_t polls = 0;
  uint32_t device = 0;
  bool slack_set = false;
  // The GPU's word as the command processor writes it (the physical view of guest memory),
  // for the last device seen.
  uint32_t word_device = 0;
  uint32_t* word = nullptr;
#ifdef _WIN32
  bool timer_ready = false;
  HANDLE timer = nullptr;
  ~WaitState() {
    if (timer) CloseHandle(timer);
  }
#endif
  // FORZA_GPU_WAIT_LOG
  Clock::time_point window_start;
  uint64_t waits = 0, total_polls = 0, long_waits = 0, blocks = 0, block_limits = 0;
  Clock::duration total{}, longest{};
};
thread_local WaitState t_wait;

uint32_t Load32(uint8_t* base, uint32_t address) {
  const auto* p = reinterpret_cast<const volatile uint8_t*>(forza::GuestPtr(base, address));
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

uint32_t* GpuWord(uint8_t* base, uint32_t device) {
  WaitState& w = t_wait;
  if (device == w.word_device && w.word) return w.word;
  w.word_device = device;
  w.word = nullptr;
  auto* kernel = rex::system::kernel_state();
  if (!kernel || !kernel->memory() || device < 0x10000) return nullptr;
  const uint32_t address = Load32(base, device + kGpuWord);
  if (address < 0x10000 || (address & 3)) return nullptr;
  const uint32_t physical = kernel->memory()->GetPhysicalAddress(address);
  if (physical == UINT32_MAX) return nullptr;
  w.word = kernel->memory()->TranslatePhysical<uint32_t*>(physical);
  return w.word;
}

void EndOfWait() {
  WaitState& w = t_wait;
  if (!Log() || w.polls == 0) return;
  const auto now = w.last_poll;
  const auto took = now - w.start;
  w.waits++;
  w.total_polls += w.polls;
  w.total += took;
  if (took > w.longest) w.longest = took;
  if (took > kYield) w.long_waits++;
  if (w.window_start == Clock::time_point{}) w.window_start = now;
  if (now - w.window_start >= std::chrono::seconds(10)) {
    using us = std::chrono::microseconds;
    REXLOG_INFO("GPUWAIT: waits={} long={} total_ms={:.1f} longest_ms={:.2f} polls={} blocks={} "
                "block_limits={} mode={}",
                w.waits, w.long_waits, std::chrono::duration_cast<us>(w.total).count() / 1000.0,
                std::chrono::duration_cast<us>(w.longest).count() / 1000.0, w.total_polls,
                w.blocks, w.block_limits,
                GetMode() == Mode::kSpin ? "spin" : GetMode() == Mode::kSleep ? "sleep" : "block");
    w.window_start = now;
    w.waits = w.total_polls = w.long_waits = w.blocks = w.block_limits = 0;
    w.total = w.longest = {};
  }
}

// Sleeps about kSleepNs.
void SleepStep(WaitState& w) {
#ifdef _WIN32
  if (!w.timer_ready) {
    w.timer_ready = true;
    w.timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                                     SYNCHRONIZE | TIMER_MODIFY_STATE);
    if (!w.timer)
      REXLOG_INFO("GPUWAIT: no high-resolution timer (error {}); yielding instead", GetLastError());
  }
  LARGE_INTEGER due;
  due.QuadPart = -(kSleepNs / 100);  // relative, in 100 ns units
  if (w.timer && SetWaitableTimer(w.timer, &due, 0, nullptr, nullptr, FALSE) &&
      WaitForSingleObject(w.timer, INFINITE) == WAIT_OBJECT_0)
    return;
  rex::thread::MaybeYield();
#else
#ifdef __linux__
  if (!w.slack_set) {
    prctl(PR_SET_TIMERSLACK, 5000UL);
    w.slack_set = true;
  }
#endif
  timespec ts{0, kSleepNs};
  nanosleep(&ts, nullptr);
#endif
}

// Blocks until the word no longer holds `seen` (its value as stored: big-endian), or kBlockNs. Returns false where the platform cannot block on a word.
bool BlockOnWord(WaitState& w, uint32_t* word, uint32_t seen) {
#if defined(__linux__)
  if (!w.slack_set) {
    prctl(PR_SET_TIMERSLACK, 5000UL);
    w.slack_set = true;
  }
  timespec ts{0, kBlockNs};
  const long r = syscall(SYS_futex, word, FUTEX_WAIT_PRIVATE, seen, &ts, nullptr, 0);
  if (Log()) {
    w.blocks++;
    if (r != 0 && errno == ETIMEDOUT) w.block_limits++;
  }
  return true;
#elif defined(_WIN32)
  const BOOL woke = WaitOnAddress(word, &seen, sizeof(seen), kBlockNs / 1'000'000);
  if (Log()) {
    w.blocks++;
    if (!woke && GetLastError() == ERROR_TIMEOUT) w.block_limits++;
  }
  return true;
#else
  (void)w, (void)word, (void)seen;
  return false;
#endif
}

uint32_t ToStored(uint32_t value) {  // the guest's big-endian word as it sits in memory
  return (value >> 24) | ((value >> 8) & 0xFF00) | ((value << 8) & 0xFF0000) | (value << 24);
}

// `compared`: the value of the GPU's word the swap loop just compared (it had not passed),
// else 0 with `known` false.
void BetweenPolls(uint8_t* base, Clock::time_point now, bool known, uint32_t compared) {
  WaitState& w = t_wait;
  const auto waited = now - w.start;
  if (waited < kSpin) return;
  if (waited < kYield) {
    rex::thread::MaybeYield();
    return;
  }
  if (GetMode() != Mode::kBlock) {  // the waits before gpu_wait_blocking
#ifdef _WIN32
    rex::thread::MaybeYield();
#else
#ifdef __linux__
    if (!w.slack_set) {
      prctl(PR_SET_TIMERSLACK, 5000UL);
      w.slack_set = true;
    }
#endif
    timespec ts{0, kSleepNs};
    nanosleep(&ts, nullptr);
#endif
    return;
  }
  if (known) {
    if (uint32_t* word = GpuWord(base, w.device)) {
      if (BlockOnWord(w, word, ToStored(compared))) return;
    }
  }
  SleepStep(w);
}

}  // namespace

// Start of a wait: (r3 = the wait record, r4 = device, r5 = reason).
extern "C" REX_FUNC(__imp__sub_829F03B0);
extern "C" REX_FUNC(sub_829F03B0) {
  EndOfWait();  // a wait that ended without its end call (the loop's early exits)
  t_wait.start = Clock::now();
  t_wait.polls = 0;
  t_wait.device = ctx.r4.u32;
  __imp__sub_829F03B0(ctx, base);
}

// One poll of the wait: r3 = 1 while it may go on (the caller then checks the read
// pointer again), 0 to give up.
extern "C" REX_FUNC(__imp__sub_829F04A8);
extern "C" REX_FUNC(sub_829F04A8) {
  const bool known = ctx.lr == kSwapWaitReturn;
  const uint32_t compared = known ? ctx.r10.u32 - ctx.r11.u32 : 0;
  __imp__sub_829F04A8(ctx, base);
  WaitState& w = t_wait;
  w.polls++;
  const Mode mode = GetMode();
  if (mode == Mode::kSpin && !Log()) return;
  w.last_poll = Clock::now();
  if (ctx.r3.u32 != 0 && mode != Mode::kSpin) BetweenPolls(base, w.last_poll, known, compared);
}
