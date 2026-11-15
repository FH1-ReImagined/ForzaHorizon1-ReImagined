// forza - the game's "fixedGPU" heap (category 0x21) running out (#857, src/hooks/fixed_gpu_heap.cpp).
// tu_address_map: TU4 addresses
#pragma once

#include <cstdint>

struct PPCContext;

namespace forza::fixed_gpu_heap {

// TU4 addresses. XMemAlloc (sub_82D83590) gives the memory manager's allocation loop
// (sub_82423000: r4 the heap, r5 the size, r6 the alignment) the calling thread's arena as
// the heap: 0x21 for free roam, where the world, the menus' scenes and textures and the
// resources the loader reads (sub_82CF60E8) all take their blocks.
constexpr uint32_t kCategory = 0x21;
// A heap object's "stop" byte: when an allocation fails with it set, sub_82423000 stores to
// guest address 4 (no capacity) or 5 (no block big enough) on purpose and the game stops.
constexpr uint32_t kStopFlag = 0x174;
constexpr uint32_t kPage = 4096;

// A heap object's link to the category tried after it (+0x160), and the one taken instead of
// that when it is 8 (+0x164); an arena's byte that says it is on (+0x310) and a heap's size.
constexpr uint32_t kNext = 0x160, kNextOverride = 0x164, kArenaOn = 0x310, kHeapSize = 0x34;
constexpr uint32_t kArenaSize = 0x314, kLowHeap = 0x10, kHighHeap = 0x190, kArenas = 0x8339664C;

// The heap a category stands for, as sub_82422E90 finds it: arena 9 is the calling thread's
// arena (TLS +12380; when that is 9 too, the manager's default at +12), 8 means none, and a heap
// that is off or empty passes the request on to its link. read32(address) and read8(address)
// read guest memory. 0 for none.
template <typename Read32, typename Read8>
uint32_t ResolveHeap(uint32_t category, uint32_t thread_arena, uint32_t default_arena,
                     Read32 read32, Read8 read8) {
  for (int step = 0; step < 32; ++step) {
    if ((category & 0xF) == 9) {
      uint32_t arena = thread_arena;
      if ((arena & 0xF) == 9) arena = default_arena;
      category = arena == 8 ? 8 : (arena & 0xF) | (category & 0x30);
    }
    if (category == 8) return 0;
    const uint32_t index = category & 0xF, kind = category & 0x30;
    if (index >= 8 || (kind != 0x10 && kind != 0x20)) return 0;
    const uint32_t arena = kArenas + index * kArenaSize;
    const uint32_t heap = arena + (kind == 0x10 ? kLowHeap : kHighHeap);
    if (read8(arena + kArenaOn) && read32(heap + kHeapSize)) return heap;
    const uint32_t over = read32(heap + kNextOverride);
    category = over == 8 ? read32(heap + kNext) : over;
  }
  return 0;
}

// The last heap sub_82423000 tries for a category: each one that fails passes the request to
// its link (+0x160) until there is none. The game's stop on a failure looks at that heap.
template <typename Read32, typename Read8>
uint32_t LastHeap(uint32_t category, uint32_t thread_arena, uint32_t default_arena,
                  Read32 read32, Read8 read8) {
  uint32_t last = 0;
  for (int step = 0; step < 16; ++step) {
    const uint32_t heap = ResolveHeap(category, thread_arena, default_arena, read32, read8);
    if (!heap) break;
    last = heap;
    category = read32(heap + kNext);
  }
  return last;
}

// Whether the overflow can serve this allocation: the request ends in heap 0x21 (`last`), with a
// size one of its chunks holds and an alignment its pages give.
constexpr bool Eligible(uint32_t last, uint32_t size, uint32_t alignment, uint32_t chunk) {
  return last == kArenas + (kCategory & 0xF) * kArenaSize + kHighHeap && size && size <= chunk &&
         alignment <= kPage;
}

// The sub_82423000 hook (src/hooks/fm4_track.cpp) calls this first. True when it made the
// call itself (ctx.r3 holds the block): a block that heap 0x21 has no room for comes from
// memory the hook keeps for it instead of stopping the game.
using Original = void (*)(PPCContext&, uint8_t*);
bool Allocate(PPCContext& ctx, uint8_t* base, Original original);
// XMemFree, XMemSize and the manager's free of a block (src/hooks/movie_memory.cpp,
// fm4_track.cpp): true / the size when the block is one of these.
bool Free(uint32_t address);
uint32_t Size(uint32_t address);

}  // namespace forza::fixed_gpu_heap
