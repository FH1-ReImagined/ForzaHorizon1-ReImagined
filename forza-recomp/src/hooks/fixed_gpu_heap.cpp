// forza - a resource that the game's "fixedGPU" heap has no room for (#857).
//
// The game keeps a resource's data (car pictures, menu scenes and their textures) in one of
// its fixed-size heaps, chosen by the arena of the code that asked for it. In free roam that
// is heap 0x21, "fixedGPU" (62,464 KB, src/platform/game_heap.h), and the world keeps 51 to
// 54 MB of it. The garage's menus run in arena 4, the "compressed" heap the game turns on for
// its menus (heap 0x24, 28 MB). My Cars opened from the Native pause menu runs in free roam's
// arena, so its car pictures (864 KB each, about eight at a time) went to heap 0x21 next to
// the world and to the menu's own scenes: 59 to 61.5 MB in use, the largest free block 0.5 to
// 0.9 MB. Then a picture or a menu texture (1,146,880 bytes, the menu's scenes loaded again
// when My Cars closes) found no block, and the memory manager's allocation loop (sub_82423000)
// stopped the game on purpose: a store to guest address 4 or 5, on the resource loader's
// thread, call chain 82D83798 82CF6430 82CF8604 82CE2A60 (#857, and the same chain in #779).
//
// Now such a block (a resource's data in heap 0x21, fixed_gpu_heap.h Eligible) that the heap
// has no room for is taken from memory kept for it instead: the heap's stop byte is cleared
// for the one call, so the loop returns nothing instead of stopping, and the block comes from
// an arena of 8 MB chunks of the guest's 4 KB-page physical memory, taken as they are needed
// and kept for the session (64 MB at most), as for a converted FM4 circuit's streamed textures
// (fm4_stream_textures.h has why the memory is kept and why a block given back is not handed
// out again for two seconds). XMemFree, XMemSize and the manager's free know these blocks.
// When the arena has no room either, the game's own allocation runs again and stops as before.
// A heap with room is never touched: the game runs exactly as it did until the heap is full.
// FORZA_FIXED_GPU_OVERFLOW=0 turns this off; =always takes every such block from the arena
// (a test of this path).
// tu_address_map: TU4 addresses
#include "../platform/fixed_gpu_heap.h"
#include "../platform/guest_addr.h"

#if FORZA_TU4
#include "fm4_stream_textures.h"
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
namespace st = forza::fm4_stream_textures;
namespace gh = forza::game_heap;

constexpr uint32_t kArenas = 0x8339664C;  // the memory manager's arena table (TU4)

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

std::mutex mutex;  // the heap's stop byte during a call, the arena and the sizes
st::Arena arena;
std::unordered_map<uint32_t, uint32_t> sizes;  // block -> the size asked for
std::atomic<bool> any{false};                  // a block was given: frees may be ours
uint64_t taken = 0;                            // blocks given so far

// A chunk for the arena: guest physical memory that stays allocated, the GPU side's copy of it
// dropped once (fm4_track.cpp's streamed textures do the same, see there).
bool AddChunk() {
  auto* memory = REX_KERNEL_MEMORY();
  auto* heap = memory->LookupHeapByType(true, st::kPage);
  uint32_t chunk = 0;
  if (!heap || !heap->Alloc(st::kChunkBytes, st::kPage,
                            rex::memory::kMemoryAllocationReserve | rex::memory::kMemoryAllocationCommit,
                            rex::memory::kMemoryProtectRead | rex::memory::kMemoryProtectWrite, true, &chunk) ||
      !chunk)
    return false;
  arena.AddChunk(chunk, st::kChunkBytes);
  memory->EnablePhysicalMemoryAccessCallbacks(memory->GetPhysicalAddress(chunk), st::kChunkBytes, true, false);
  memory->TriggerPhysicalMemoryCallbacks(rex::thread::global_critical_region::AcquireDirect(), chunk,
                                         st::kChunkBytes, true, true, true);
  return true;
}
}  // namespace

bool Allocate(PPCContext& ctx, uint8_t* base, Original original) {
  const Mode mode = GetMode();
  const uint32_t category = ctx.r4.u32, return_address = uint32_t(ctx.lr);
  if (mode == Mode::kOff || category != kCategory || return_address != kFromXMemAlloc) return false;
  // XMemAlloc's own return address: its frame's back chain, then the link register saved below it.
  const uint32_t frame = Load32(base, ctx.r1.u32);
  const uint32_t caller = frame > 8 ? Load32(base, frame - 8) : 0;
  const uint32_t size = ctx.r5.u32, alignment = ctx.r6.u32;
  if (!Eligible(category, return_address, caller) || !size || size > st::kChunkBytes ||
      alignment > st::kPage)
    return false;
  const uint32_t heap = gh::HeapOfCategory(kArenas, kCategory);
  const PPCContext call = ctx;
  std::lock_guard<std::mutex> lock(mutex);
  if (mode != Mode::kAlways) {
    uint8_t* stop = forza::GuestPtr(base, heap + kStopFlag);
    const uint8_t was = *stop;
    *stop = 0;
    original(ctx, base);
    *stop = was;
    if (ctx.r3.u32) return true;
  }
  const uint64_t now = NowMs();
  uint32_t block = arena.Take(size, now);
  bool added = false;
  if (!block && arena.MayAddChunk(now)) {
    added = AddChunk();
    if (added) block = arena.Take(size, now);
    else if (arena.ChunkRefused(now))
      REXLOG_WARN("FORZA: no {} MB left in one piece in the guest's memory for the fixedGPU heap's "
                  "overflow ({} MB taken, {} MB in use) (#857)",
                  st::kChunkBytes >> 20, arena.capacity() >> 20, arena.held() >> 20);
  }
  if (!block) {
    // No room here either: the game's own allocation again, which stops the game as before.
    ctx = call;
    original(ctx, base);
    return true;
  }
  sizes[block] = size;
  any.store(true, std::memory_order_release);
  ++taken;
  if (added || (taken & (taken - 1)) == 0)
    REXLOG_INFO("FORZA: the fixedGPU heap (0x21) has no room for a resource's {} KB ({} of {} KB in use): "
                "taken from memory kept for it, {} blocks so far ({} MB taken, {} KB in use) (#857)",
                (size + 1023) / 1024, Load32(base, heap + gh::kUsed) / 1024, Load32(base, heap + gh::kSize) / 1024,
                taken, arena.capacity() >> 20, arena.held() / 1024);
  ctx.r3.u64 = block;
  return true;
}

bool Free(uint32_t address) {
  if (!address || !any.load(std::memory_order_acquire)) return false;
  std::lock_guard<std::mutex> lock(mutex);
  if (!arena.Give(address, NowMs())) return false;
  sizes.erase(address);
  return true;
}

uint32_t Size(uint32_t address) {
  if (!address || !any.load(std::memory_order_acquire)) return 0;
  std::lock_guard<std::mutex> lock(mutex);
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
