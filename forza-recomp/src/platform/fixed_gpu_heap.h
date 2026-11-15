// forza - the game's "fixedGPU" heap (category 0x21) running out (#857, src/hooks/fixed_gpu_heap.cpp).
// tu_address_map: TU4 addresses
#pragma once

#include <cstdint>

struct PPCContext;

namespace forza::fixed_gpu_heap {

// TU4 addresses. A cacheable resource's data (sub_82CF60E8, the resource loader's thread) is
// read into a block it asks XMemAlloc for (sub_825CB038 -> sub_82D83590, attributes BC800000);
// XMemAlloc gives the memory manager's allocation loop (sub_82423000) the calling thread's
// arena as the heap: 0x21 for free roam.
constexpr uint32_t kCategory = 0x21;
constexpr uint32_t kFromXMemAlloc = 0x82D83798;   // sub_82423000's return address in XMemAlloc
constexpr uint32_t kResourceData = 0x82CF6430;    // XMemAlloc's return address in sub_82CF60E8
// A heap object's "stop" byte: when an allocation fails with it set, sub_82423000 stores to
// guest address 4 (no capacity) or 5 (no block big enough) on purpose and the game stops.
constexpr uint32_t kStopFlag = 0x174;

// Whether this allocation is a resource's data in heap 0x21: the heap (r4), the return address
// into XMemAlloc (lr) and XMemAlloc's own return address (from its stack frame).
constexpr bool Eligible(uint32_t category, uint32_t return_address, uint32_t caller) {
  return category == kCategory && return_address == kFromXMemAlloc && caller == kResourceData;
}

// The sub_82423000 hook (src/hooks/fm4_track.cpp) calls this first. True when it made the
// call itself (ctx.r3 holds the block): such a block that heap 0x21 has no room for comes from
// memory the hook keeps for it instead of stopping the game.
using Original = void (*)(PPCContext&, uint8_t*);
bool Allocate(PPCContext& ctx, uint8_t* base, Original original);
// XMemFree, XMemSize and the manager's free of a block (src/hooks/movie_memory.cpp,
// fm4_track.cpp): true / the size when the block is one of these.
bool Free(uint32_t address);
uint32_t Size(uint32_t address);

}  // namespace forza::fixed_gpu_heap
