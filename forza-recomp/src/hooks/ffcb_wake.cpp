// forza - wake the HUD worker when its command-buffer slot is not ready (#512).
// tu_address_map: TU4 addresses
//
// Retry this exact native slot wait in 2 ms slices, waking the worker only after
// STATUS_TIMEOUT. After 2 s use the original infinite wait. Success consumes the
// event once; errors retain the native boolean result. No host submission totals
// survive ring wrap, device reset, or address reuse. FORZA_FFCB_WAKE=0: original wait.
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>

#if FORZA_TU4
#define FfcbConsume sub_824A1E40
#define OriginalFfcbConsume __imp__sub_824A1E40
#define SlotWait sub_82419270
#define OriginalSlotWait __imp__sub_82419270
#define RawWait sub_824957B0
#define SetEventHandle sub_824AA568
constexpr uint32_t kSlotWaitReturn = 0x8249E108;
#else
#define FfcbConsume sub_824726C8
#define OriginalFfcbConsume __imp__sub_824726C8
#define SlotWait sub_823E91C0
#define OriginalSlotWait __imp__sub_823E91C0
#define RawWait sub_82465EB8
#define SetEventHandle sub_8247AF78
constexpr uint32_t kSlotWaitReturn = 0x8246E920;
#endif

namespace {
uint32_t Load32(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
}
bool Enabled() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_FFCB_WAKE");
    return !(v && std::strcmp(v, "0") == 0);
  }();
  return on;
}
struct ConsumeScope;
thread_local const ConsumeScope* consuming = nullptr;
// Lifetime equals the native consume call. This does not extend guest object
// lifetime or make concurrent native teardown safe.
struct ConsumeScope {
  uint32_t object, event;
  const ConsumeScope* previous = consuming;
  ConsumeScope(uint32_t r, uint32_t e) : object(r), event(e) { consuming = this; }
  ~ConsumeScope() { consuming = previous; }
};
void LogWake(bool ready, int64_t us) {
  static std::atomic<uint64_t> wakes{0}, summarized{10};
  static std::atomic<int64_t> next_summary{0};
  const uint64_t n = wakes.fetch_add(1, std::memory_order_relaxed) + 1;
  const int64_t now_s = std::chrono::duration_cast<std::chrono::seconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count();
  if (n <= 10) {
    REXLOG_INFO("FFCB: woke the HUD command buffer worker: {}, after {} us (#512, {} so far)",
                ready ? "slot ready" : "native wait resumed", us, n);
    if (n == 10) next_summary.store(now_s + 10, std::memory_order_relaxed);
  } else if (now_s >= next_summary.load(std::memory_order_relaxed)) {
    next_summary.store(now_s + 10, std::memory_order_relaxed);
    const uint64_t before = summarized.exchange(n, std::memory_order_relaxed);
    REXLOG_INFO("FFCB: woke the HUD command buffer worker {} more times (#512, {} so far)",
                n - before, n);
  }
}
}  // namespace

extern "C" REX_FUNC(OriginalFfcbConsume);
extern "C" REX_FUNC(FfcbConsume) {
  if (!Enabled()) { OriginalFfcbConsume(ctx, base); return; }
  const uint32_t r = ctx.r3.u32;
  const uint32_t slot = Load32(base, r + 0x75C);
  if (slot >= 12) { OriginalFfcbConsume(ctx, base); return; }
  ConsumeScope scope(r, r + 0x470 + 60 * slot + 28);
  OriginalFfcbConsume(ctx, base);
}
extern "C" REX_FUNC(OriginalSlotWait);
extern "C" REX_FUNC(RawWait);
extern "C" REX_FUNC(SetEventHandle);
extern "C" REX_FUNC(SlotWait) {
  if (!consuming || ctx.lr != kSlotWaitReturn || ctx.r3.u32 != consuming->event ||
      ctx.r4.u32 != 0xFFFFFFFF) {
    OriginalSlotWait(ctx, base);
    return;
  }
  const uint32_t wake = Load32(base, consuming->object + 0x764);
  if (!wake) { OriginalSlotWait(ctx, base); return; }
  const uint32_t handle = Load32(base, ctx.r3.u32);
  const auto start = std::chrono::steady_clock::now();
  bool woke = false;
  while (std::chrono::steady_clock::now() - start < std::chrono::seconds(2)) {
    // RawWait takes the handle, uses a non-alertable relative timeout, and
    // translates errors as the original boolean wrapper does. Keep its guest
    // stack writes below the caller's frame, like a separate ABI call.
    PPCContext call = ctx;
    call.r1.u64 = ctx.r1.u32 - 0x400;
    call.r3.u64 = handle;
    call.r4.u64 = 2;
    RawWait(call, base);
    if (call.r3.u32 != 0x102) {
      // Native boolean wrapper: zero -> true, every other result -> false.
      // Only timeout permits a wake; errors must not be retried.
      ctx.r3.u64 = call.r3.u32 == 0;
      if (woke) LogWake(call.r3.u32 == 0, std::chrono::duration_cast<std::chrono::microseconds>(
          std::chrono::steady_clock::now() - start).count());
      return;
    }
    call = ctx;
    call.r1.u64 = ctx.r1.u32 - 0x400;
    call.r3.u64 = Load32(base, wake);
    SetEventHandle(call, base);
    woke = true;
  }
  if (woke) LogWake(false, std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::steady_clock::now() - start).count());
  OriginalSlotWait(ctx, base);
}
