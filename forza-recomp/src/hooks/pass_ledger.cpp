// forza - the pass ledger's render thread side (pass_ledger.h; docs/rendering.md "Pass ledger";
// the labels and marks: src/platform/pass_ledger.h).
//
// On the render thread (the one that runs the shadow mask function), on the game's D3D device,
// at each flush call the game makes (not the D3D library's own nested ones):
// - the draw class of the call (platform/pass_ledger.h ClassOf); a call of another class than
//   the one in progress first writes a class mark, carrying the numbers of the calls of the one
//   that ends: their time in the flush points, their count, the draws they submitted (a list's
//   from its packets, counted once per command buffer and kept) and the lists run;
// - after a Resolve, and before the swap, a segment end mark with the segment's label and the
//   render thread's CPU time since the previous one.
// D3D's tiling and predication calls make the next draw write a class mark again: the scene's
// commands run once per EDRAM tile, and each run should start in its class.
// Flush calls on another device or another thread are counted only (in the next end mark).
//
// tu_address_map: TU4 addresses

#include "pass_ledger.h"

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/native_shadow_record.h"
#include "../platform/pass_ledger.h"

#include <rex/cvar.h>
#include <rex/logging.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <intrin.h>
#include <windows.h>
#else
#include <time.h>
#endif

namespace forza::pass_ledger {

namespace {
thread_local Scope t_scope;
thread_local bool t_render_thread = false;
thread_local bool t_mask = false;
}  // namespace

Scope& CurrentScope() { return t_scope; }

void SetMask(bool running) {
  if (t_render_thread) t_mask = running;
}

void ReadSwitch() {
  const std::string csv = rex::cvar::GetFlagByName("fh1_pass_cost_csv");
#if FORZA_TU4
  if (csv.empty()) return;
  t_render_thread = true;
  native_device::g_hooks.fetch_or(native_device::kHookLedger, std::memory_order_relaxed);
  REXLOG_INFO(
      "pass ledger: on (the render thread marks its passes for the GPU plugin's "
      "fh1_pass_cost_csv)");
#else
  if (!csv.empty())
    REXLOG_INFO("pass ledger: needs the Title Update 4 build (its D3D flush points); off");
#endif
}

}  // namespace forza::pass_ledger

#if FORZA_TU4

namespace forza::pass_ledger {
namespace {

namespace ns = forza::native_shadow;
namespace nd = forza::native_device;

constexpr uint32_t kDeviceMode = 11068;  // the device's mode byte (0x80: recording)
constexpr uint32_t kCbChain = 116;       // a command buffer's chain of indirect buffers

uint32_t Load32(uint8_t* base, uint32_t address) {
  uint32_t v;
  std::memcpy(&v, forza::GuestPtr(base, address), 4);
  return __builtin_bswap32(v);
}
uint64_t SteadyNs() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}
// The calling thread's CPU time (0 where unknown).
uint64_t ThreadCpuNs() {
#if defined(_WIN32)
#if defined(_M_X64) || defined(__x86_64__)
  // The thread's cycles at the time stamp counter's rate: only where it is invariant.
  static const double ns_per_cycle = [] {
    int regs[4] = {};
    __cpuid(regs, int(0x80000000u));
    if (uint32_t(regs[0]) < 0x80000007u) return 0.0;
    __cpuid(regs, int(0x80000007u));
    if (!((uint32_t(regs[3]) >> 8) & 1)) return 0.0;
    LARGE_INTEGER frequency, first, last;
    if (!QueryPerformanceFrequency(&frequency) || !QueryPerformanceCounter(&first)) return 0.0;
    const uint64_t tsc_first = __rdtsc();
    do {
      QueryPerformanceCounter(&last);
    } while (last.QuadPart - first.QuadPart < frequency.QuadPart / 500);
    const uint64_t tsc_last = __rdtsc();
    const double ns = double(last.QuadPart - first.QuadPart) * 1e9 / double(frequency.QuadPart);
    return tsc_last > tsc_first ? ns / double(tsc_last - tsc_first) : 0.0;
  }();
  ULONG64 cycles = 0;
  if (ns_per_cycle <= 0 || !QueryThreadCycleTime(GetCurrentThread(), &cycles)) return 0;
  return uint64_t(double(cycles) * ns_per_cycle);
#else
  return 0;
#endif
#else
  timespec ts;
  if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &ts) != 0) return 0;
  return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
#endif
}

bool LooksLikeDevice(uint8_t* base, uint32_t r3) {
  if (r3 < 0x40000000u || r3 >= 0x80000000u || (r3 & 3)) return false;
  const uint32_t c = Load32(base, r3 + ns::kCursor), hard = Load32(base, r3 + ns::kHardLimit);
  return c >= 0xA0000000u && !(c & 3) && hard >= c && hard - c <= (32u << 20);
}

Flush KindOf(nd::Point point) {
  switch (point) {
    case nd::kPointDrawVertices:
    case nd::kPointDrawIndexed:
      return Flush::kDraw;
    case nd::kPointBeginVertices:
    case nd::kPointDrawVerticesUp:
    case nd::kPointDrawIndexedUp:
      return Flush::kUp;
    case nd::kPointClear:
      return Flush::kClear;
    case nd::kPointRunCommandBuffer:
      return Flush::kList;
    case nd::kPointResolve:
      return Flush::kResolve;
    case nd::kPointSwap:
      return Flush::kSwap;
    case nd::kPointTiling:
    case nd::kPointPredication:
      return Flush::kTiling;
    default:
      return Flush::kOther;
  }
}

// The render thread's state (only it writes marks).
struct Render {
  uint32_t dev = 0;
  int depth = 0;
  uint32_t seq = 0;
  uint32_t current = ClassWord(kClassNone, 0);  // the class in progress
  bool force = true;                            // mark the next draw's class even if the same
  Interval interval;
  uint64_t segment_cpu = 0;
  Labeler labeler;
  // Counts for the log.
  uint64_t frames = 0, marks = 0, lost = 0, lists = 0, lists_unparsed = 0, recording = 0;
};
Render& R() {
  static Render r;
  return r;
}
// Calls on another device or thread since the last end mark.
std::atomic<uint32_t> other_calls{0};

// A command buffer's draws, from its indirect buffers' packets; kept per command buffer object
// and its first buffer (a list loaded again elsewhere is counted again).
struct ListDraws {
  uint32_t first = 0, size = 0;
  int draws = 0;
};
std::unordered_map<uint32_t, ListDraws> list_draws;

bool Plausible(uint32_t address) { return address >= 0x00100000 && address < 0xF0000000; }
// Why a list's draws were not counted (the log).
enum Unparsed { kBadBuffer, kBadChain, kBadLink, kTooLong, kCut, kUnparsedCount };
uint64_t unparsed[kUnparsedCount] = {};

int CountListDraws(uint8_t* base, uint32_t cb) {
  if (!Plausible(cb)) return ++unparsed[kBadBuffer], -1;
  const uint32_t chain = Load32(base, cb + kCbChain);
  if (!Plausible(chain)) return ++unparsed[kBadChain], -1;
  if (!Load32(base, chain + 4)) return 0;
  const uint32_t first = Load32(base, chain + 12), size = Load32(base, chain + 8) & 0xFFFFFF;
  auto found = list_draws.find(cb);
  if (found != list_draws.end() && found->second.first == first && found->second.size == size)
    return found->second.draws;
  thread_local std::vector<uint32_t> words;
  int draws = 0;
  uint32_t link = chain;
  for (uint32_t links = 0; link && links < 64; link = Load32(base, link), ++links) {
    if (!Plausible(link)) return ++unparsed[kBadLink], -1;
    const uint32_t buffers = Load32(base, link + 4);
    for (uint32_t b = 0; b < buffers && b < 256; ++b) {
      const uint32_t n = Load32(base, link + 8 + b * 8) & 0xFFFFFF;
      const uint32_t at = 0xA0000000u | (Load32(base, link + 12 + b * 8) & 0x1FFFFFFFu);
      if (n > (1u << 22)) return ++unparsed[kTooLong], -1;
      words.resize(n);
      for (uint32_t i = 0; i < n; ++i) words[i] = Load32(base, at + i * 4);
      const int d = CountDraws(words.data(), n);
      if (d < 0) return ++unparsed[kCut], -1;
      draws += d;
    }
  }
  if (list_draws.size() > (1u << 20)) list_draws.clear();
  list_draws[cb] = {first, size, draws};
  return draws;
}

void Emit(PPCContext& ctx, uint8_t* base, const uint32_t* words, uint32_t n) {
  Render& r = R();
  ++r.marks;
  if (!nd::EmitHostPacket(ctx, base, r.dev, words, n)) ++r.lost;
}

void ClassChange(PPCContext& ctx, uint8_t* base, uint32_t cls) {
  Render& r = R();
  uint32_t w[kClassWords];
  ClassMark(++r.seq, cls, r.current, r.interval, w);
  Emit(ctx, base, w, kClassWords);
  r.interval = Interval();
  r.current = cls;
  r.force = false;
}

void SegmentEnd(PPCContext& ctx, uint8_t* base) {
  Render& r = R();
  const uint64_t cpu = ThreadCpuNs();
  uint32_t w[kEndWords];
  EndMark(++r.seq, r.labeler.End(), cpu > r.segment_cpu ? cpu - r.segment_cpu : 0, r.current,
          r.interval, other_calls.exchange(0, std::memory_order_relaxed), w);
  Emit(ctx, base, w, kEndWords);
  r.interval = Interval();
  r.segment_cpu = cpu;
}

void LogFrames() {
  Render& r = R();
  if (++r.frames % 3600) return;
  REXLOG_INFO(
      "pass ledger: {} frames, {} marks ({} without room), {} command lists counted, {} not "
      "(buffer {}, chain {}, link {}, too long {}, cut {}), {} calls while the device recorded",
      r.frames, r.marks, r.lost, r.lists, r.lists_unparsed, unparsed[kBadBuffer],
      unparsed[kBadChain], unparsed[kBadLink], unparsed[kTooLong], unparsed[kCut], r.recording);
}

}  // namespace

void FlushBegin(nd::Point point, PPCContext& ctx, uint8_t* base, nd::LedgerFlush& f) {
  f.counted = false;
  if (!t_render_thread) {
    other_calls.fetch_add(1, std::memory_order_relaxed);
    return;
  }
  Render& r = R();
  if (r.depth++ != 0) return;  // D3D's own call inside one the game made
  const Flush kind = KindOf(point);
  if (kind != Flush::kSwap) {
    const uint32_t dev = ctx.r3.u32;
    if (!r.dev && LooksLikeDevice(base, dev)) {
      r.dev = dev;
      r.segment_cpu = ThreadCpuNs();
    }
    if (!r.dev || dev != r.dev) {
      other_calls.fetch_add(1, std::memory_order_relaxed);
      return;
    }
    if (forza::GuestPtr(base, dev)[kDeviceMode] & 0x80) {
      ++r.recording;  // into a command buffer: counted when it runs
      return;
    }
  } else if (!r.dev) {
    return;
  }
  f.kind = uint32_t(kind);
  f.counted = true;
  if (kind == Flush::kSwap) {
    // Everything after the last resolve, then the frame's labels start again (and a mask scope
    // the world pass did not end ends here).
    SegmentEnd(ctx, base);
    r.labeler.Frame();
    t_mask = false;
    r.force = true;
    LogFrames();
    f.start_ns = SteadyNs();
    return;
  }
  const Scope& scope = t_scope;
  r.labeler.Saw(scope.cls != 0xFF ? int(scope.pass) : -1, scope.cls == kClassCar, t_mask);
  const uint32_t cls = ClassOf(kind, scope.cls, scope.layer);
  if (cls != kNoClass && (cls != r.current || r.force)) ClassChange(ctx, base, cls);
  switch (kind) {
    case Flush::kDraw:
    case Flush::kUp:
    case Flush::kClear:
      ++r.interval.draws;
      break;
    case Flush::kList: {
      ++r.interval.runs;
      const int draws = CountListDraws(base, ctx.r4.u32);
      if (draws >= 0) {
        r.interval.draws += uint32_t(draws);
        ++r.lists;
      } else {
        ++r.lists_unparsed;
      }
    } break;
    case Flush::kTiling:
      r.force = true;  // the tiles' runs each start with a class mark
      break;
    default:
      break;
  }
  f.start_ns = SteadyNs();
}

void FlushEnd(PPCContext& ctx, uint8_t* base, nd::LedgerFlush& f) {
  if (!t_render_thread) return;
  Render& r = R();
  if (f.counted) {
    r.interval.flush_ns += SteadyNs() - f.start_ns;
    ++r.interval.calls;
    // Still at the call's depth: a flush point D3D's make-space call reaches is its own.
    if (Flush(f.kind) == Flush::kResolve) SegmentEnd(ctx, base);
  }
  --r.depth;
}

}  // namespace forza::pass_ledger

#endif  // FORZA_TU4
