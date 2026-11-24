// forza - a resource that the game's "fixedGPU" heap has no room for (#857).
//
// The game keeps a resource's data (car pictures, menu scenes and their textures) in one of
// its fixed-size heaps, chosen by the arena of the code that asked for it. In free roam that
// is heap 0x21, "fixedGPU" (62,464 KB, src/platform/game_heap.h), and the world keeps 51 to
// 54 MB of it. The garage's menus run in arena 4, the "compressed" heap the game turns on for
// its menus (heap 0x24, 28 MB). My Cars opened from the Native pause menu runs in free roam's
// arena, so its car pictures (864 KB each, about eight at a time) went to heap 0x21 next to
// the world and to the menu's own scenes: 59 to 61.8 MB in use, the largest free block 0.5 to
// 0.9 MB. Then whichever block came next found no room, and the memory manager's allocation
// loop (sub_82423000) stopped the game on purpose: a store to guest address 4 or 5. Seen: a
// picture or a menu texture (1,146,880 bytes, the menu's scenes loaded again when My Cars
// closes) on the resource loader's thread, chain 82D83798 82CF6430 82CF8604 82CE2A60 (the
// players' logs), and a menu scene's 119,756 bytes on the UI's loader, chain 82D83798 82F222C8.
//
// Now a block that heap 0x21 has no room for (fixed_gpu_heap.h Eligible) is taken from memory
// kept for it instead: every allocation that ends in the heap runs with its stop byte cleared,
// one at a time, so the loop returns nothing instead of stopping, and the block comes from
// an arena of 8 MB chunks of the guest's 4 KB-page physical memory, taken as they are needed
// and kept for the session (64 MB at most), as for a converted FM4 circuit's streamed textures
// (fm4_stream_textures.h has why the memory is kept and why a block given back is not handed
// out again for two seconds). A block over 8 MB gets a chunk of its own size (rounded up to
// whole 8 MB), kept apart from the 8 MB ones, in the same 64 MB, and handed out again as soon
// as it comes back (fixed_gpu_heap.h Overflow has why): with Paint job detail 2x a paint job's
// buffers are 16 and 22 MB, and when the Race Central cutscene (the Green Wristband) ends, the
// player's car's paint job is drawn again with heap 0x21 at 51 of its 61 MB, no 16 MB in one
// piece (#787). XMemFree, XMemSize and the manager's free know these blocks.
// When the arena has no room either, the game's own allocation runs again and stops as before.
// A heap with room is never touched: the game runs exactly as it did until the heap is full.
// "Ends in the heap" is wider than the heap's own category: vehicleGPU (0x26) and nuiGPU (0x22)
// pass what they have no room for on to 0x21 (their +0x160 links, read in game), so their
// blocks take the same lock and can get the same overflow. One lock for all of them: none of
// the heap classes' allocations calls the allocation loop again (a same-thread call would be
// passed straight to the game's own, see Allocate).
// FORZA_FIXED_GPU_OVERFLOW=0 turns this off; =always takes every block it can serve from the
// arena first, the game's heap only once the arena is full (a test of this path).
// tu_address_map: TU4 addresses
#include "../platform/fixed_gpu_heap.h"
#include "../platform/guest_addr.h"

#if FORZA_TU4
#include "../platform/game_heap.h"
#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>
#include <rex/thread/mutex.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <unordered_map>

namespace forza::fixed_gpu_heap {
namespace {
namespace gh = forza::game_heap;

enum class Mode { kOff, kOnFailure, kAlways };
Mode GetMode() {
  static const Mode mode = [] {
    const char* v = std::getenv("FORZA_FIXED_GPU_OVERFLOW");
    if (v && std::strcmp(v, "0") == 0) return Mode::kOff;
    if (v && std::strcmp(v, "always") == 0) return Mode::kAlways;
    return Mode::kOnFailure;
  }();
  return mode;
}

uint32_t Load32(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
uint64_t NowMs() {
  return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now().time_since_epoch()).count());
}

constexpr uint32_t kFixedHeap = kArenas + (kCategory & 0xF) * kArenaSize + kHighHeap;
std::mutex mutex;  // the heap's stop byte during a call, the arena and the sizes
thread_local bool in_call = false;  // this thread is inside the heap's allocation (holds the lock)
thread_local uint8_t stop_was = 0;  // the stop byte as the game had it before that call
// The lock, unless this thread holds it already (a free from inside the heap's allocation).
std::unique_lock<std::mutex> Lock() {
  return in_call ? std::unique_lock<std::mutex>(mutex, std::defer_lock) : std::unique_lock<std::mutex>(mutex);
}
Overflow arena;
std::unordered_map<uint32_t, uint32_t> sizes;  // block -> the size asked for
std::atomic<bool> any{false};                  // a block was given: frees may be ours
uint64_t taken = 0;                            // blocks given so far

// A chunk of `bytes`: guest physical memory that stays allocated, the GPU
// side's copy of it dropped once (fm4_track.cpp's streamed textures do the same, see there).
bool AddChunk(uint32_t bytes) {
  auto* memory = REX_KERNEL_MEMORY();
  auto* heap = memory->LookupHeapByType(true, kPage);
  uint32_t chunk = 0;
  if (!heap || !heap->Alloc(bytes, kPage,
                            rex::memory::kMemoryAllocationReserve | rex::memory::kMemoryAllocationCommit,
                            rex::memory::kMemoryProtectRead | rex::memory::kMemoryProtectWrite, true, &chunk) ||
      !chunk)
    return false;
  arena.AddChunk(chunk, bytes);
  memory->EnablePhysicalMemoryAccessCallbacks(memory->GetPhysicalAddress(chunk), bytes, true, false);
  memory->TriggerPhysicalMemoryCallbacks(rex::thread::global_critical_region::AcquireDirect(), chunk,
                                         bytes, true, true, true);
  return true;
}
}  // namespace

bool Allocate(PPCContext& ctx, uint8_t* base, Original original) {
  const Mode mode = GetMode();
  if (mode == Mode::kOff) return false;
  // r3 the memory manager, r4 the category, r5 the size, r6 the alignment. Category 0x29 (the
  // calling thread's arena) is 0x21 in free roam, and a heap that is off passes its requests on.
  const uint32_t size = ctx.r5.u32, alignment = ctx.r6.u32;
  const uint32_t last = LastHeap(
      ctx.r4.u32, Load32(base, Load32(base, ctx.r13.u32) + 12380), Load32(base, ctx.r3.u32 + 12),
      [base](uint32_t a) { return Load32(base, a); },
      [base](uint32_t a) { return uint32_t(*forza::GuestPtr(base, a)); });
  if (last != kFixedHeap) return false;
  const uint32_t heap = last;
  uint8_t* stop = forza::GuestPtr(base, heap + kStopFlag);
  if (in_call) {
    // An allocation from inside the heap's own one on this thread (none is known: the heap
    // classes' allocations and what they call never come back here). The lock is held already,
    // so no wait for it; the stop byte as the game set it, so a failure stops as before.
    static std::atomic<bool> warned{false};
    if (!warned.exchange(true))
      REXLOG_WARN("FORZA: the fixedGPU heap's allocation called itself (from {:08X}) (#857)", uint32_t(ctx.lr));
    const uint8_t now_was = *stop;
    *stop = stop_was;
    original(ctx, base);
    *stop = now_was;
    return true;
  }
  const PPCContext call = ctx;
  // Every request that ends in this heap takes the lock, also one the overflow cannot serve
  // (over 64 MB): with the stop byte cleared for another thread's request, its failure would
  // return nothing instead of stopping the game.
  std::lock_guard<std::mutex> lock(mutex);
  struct InCall {
    InCall() { in_call = true; }
    ~InCall() { in_call = false; }
  } in;
  stop_was = *stop;
  const bool eligible = Eligible(last, size, alignment, kMaxBytes);
  if (!eligible || mode != Mode::kAlways) {
    if (eligible) *stop = 0;
    original(ctx, base);
    *stop = stop_was;
    if (ctx.r3.u32 || !eligible) return true;
  }
  const uint64_t now = NowMs();
  uint32_t block = arena.Take(size, alignment, now);
  bool added = false;
  if (!block) {
    if (const uint32_t bytes = arena.Grow(size, alignment, now)) {
      added = AddChunk(bytes);
      if (added) block = arena.Take(size, alignment, now);
      else if (arena.Refused(size, alignment, now))
        REXLOG_WARN("FORZA: no {} MB left in one piece in the guest's memory for the fixedGPU heap's "
                    "overflow ({} MB taken, {} MB in use) (#857)",
                    bytes >> 20, arena.capacity() >> 20, arena.held() >> 20);
    }
  }
  if (!block) {
    // No room here either: the game's own allocation again, which stops the game as before.
    static std::atomic<bool> full{false};
    if (!full.exchange(true))
      REXLOG_WARN("FORZA: the fixedGPU heap's overflow has no room for {} bytes either ({} MB taken, {} KB "
                  "in use) (#857, #787)", size, arena.capacity() >> 20, arena.held() / 1024);
    ctx = call;
    original(ctx, base);
    return true;
  }
  sizes[block] = size;
  any.store(true, std::memory_order_release);
  ++taken;
  if (added || (taken & (taken - 1)) == 0) {
    // Who asked: the return address, and when that is XMemAlloc, XMemAlloc's own (its frame's
    // back chain, then the link register saved below it).
    const uint32_t frame = Load32(base, call.r1.u32);
    const uint32_t caller = frame > 8 ? Load32(base, frame - 8) : 0;
    REXLOG_INFO("FORZA: the fixedGPU heap (0x21) has no room for {} bytes ({} of {} KB in use; from {:08X} "
                "{:08X}): taken from memory kept for it, {} blocks so far ({} MB taken, {} KB in use) (#857)",
                size, Load32(base, heap + gh::kUsed) / 1024, Load32(base, heap + gh::kSize) / 1024,
                uint32_t(call.lr), caller, taken, arena.capacity() >> 20, arena.held() / 1024);
  }
  ctx.r3.u64 = block;
  return true;
}

bool Free(uint32_t address) {
  if (!address || !any.load(std::memory_order_acquire)) return false;
  const auto lock = Lock();
  if (!arena.Give(address, NowMs())) return false;
  sizes.erase(address);
  return true;
}

uint32_t Size(uint32_t address) {
  if (!address || !any.load(std::memory_order_acquire)) return 0;
  const auto lock = Lock();
  auto it = sizes.find(address);
  return it == sizes.end() ? 0 : it->second;
}

}  // namespace forza::fixed_gpu_heap
#else
namespace forza::fixed_gpu_heap {
bool Allocate(PPCContext&, uint8_t*, Original) { return false; }
bool Free(uint32_t) { return false; }
uint32_t Size(uint32_t) { return 0; }
}  // namespace forza::fixed_gpu_heap
#endif
