// forza - the game's "fixedGPU" heap (category 0x21) running out (#857, src/hooks/fixed_gpu_heap.cpp).
// tu_address_map: TU4 addresses
#pragma once

#include "../hooks/fm4_stream_textures.h"

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

// The overflow's memory: chunks of the guest's physical memory, kept for the session, 64 MB at
// most in all.
constexpr uint32_t kChunkBytes = fm4_stream_textures::kChunkBytes;  // 8 MB
constexpr uint32_t kMaxBytes = kChunkBytes * fm4_stream_textures::kMaxChunks;
// The largest alignment it gives: the paint job layout a 2x paint job saves (16 MB,
// sub_82D4E7D0) asks for 64 KB.
constexpr uint32_t kMaxAlignment = 64u << 10;

// Whether the overflow can serve this allocation: the request ends in heap 0x21 (`last`), with a
// size its chunks can hold (`largest`, kMaxBytes for the hook) and an alignment it gives (none,
// or a power of two up to kMaxAlignment).
constexpr bool Eligible(uint32_t last, uint32_t size, uint32_t alignment, uint32_t largest) {
  return last == kArenas + (kCategory & 0xF) * kArenaSize + kHighHeap && size && size <= largest &&
         alignment <= kMaxAlignment && !(alignment & (alignment - 1));
}

// The bytes a block needs in a chunk wherever the chunk starts: its size, and with an alignment
// over a page the pages it may have to skip. The chunks come from the guest's 4 KB-page physical
// heap at 0xE0000000, whose addresses are one page off its physical ones, so a chunk the heap
// aligns to 64 KB starts 4 KB past a multiple of 64 KB (seen: E7F2F000).
constexpr uint64_t Span(uint32_t size, uint32_t alignment) {
  return uint64_t(size) + (alignment > kPage ? alignment - kPage : 0);
}
// A block whose span is over one chunk (#787: Paint job detail 2x draws a paint job into
// 2048x2048 render textures, 16 MB each, sub_82D53420 -> sub_82CF9058) gets a chunk of its own,
// its span rounded up to whole chunks.
constexpr bool IsLarge(uint64_t span) { return span > kChunkBytes; }
constexpr uint64_t ChunkFor(uint64_t span) {
  return IsLarge(span) ? ((span - 1) / kChunkBytes + 1) * kChunkBytes : kChunkBytes;
}

// The overflow's blocks: those up to one chunk from 8 MB chunks, the larger ones from chunks of
// their own size, in two separate pools so small blocks never cut up a large chunk (the next
// large block would find no run long enough and take another chunk each time, up to the cap).
// Both pools count towards the one kMaxBytes. A small block given back waits
// fm4_stream_textures::kReuseDelayMs before it is handed out again; a large one is handed out
// again at once, to the block it fits best. A paint job at 2x makes and frees its 16 MB and
// 22 MB buffers within a second, again for each paint job drawn (seen at Race Central: 16, 22,
// 16, 22 MB, each given back 100-300 ms after it was taken). With the delay every one of them
// took a chunk of its own and the fourth found the cap. The chunks stay allocated and watched,
// so what is written to a block used again is seen, as in the game's own heap, which hands a
// freed block out again at once too. The hook asks Take; when it gives 0, Grow says whether it
// may allocate a chunk for the block and how large, AddChunk takes that chunk (a new chunk always
// holds the block, wherever it starts), and Refused records that the guest had none. Not thread
// safe (the hook locks).
class Overflow {
 public:
  uint32_t Take(uint32_t size, uint32_t alignment, uint64_t now_ms) {
    return Pool(Span(size, alignment)).Take(size, now_ms, alignment);
  }
  // The bytes of a chunk to allocate for a block that Take had no room for, or 0: the cap
  // reached, or a chunk of its pool refused less than kChunkRetryMs ago.
  uint32_t Grow(uint32_t size, uint32_t alignment, uint64_t now_ms) const {
    const uint64_t span = Span(size, alignment);
    if (!size || span > kMaxBytes) return 0;
    const uint64_t bytes = ChunkFor(span);
    if (capacity() + bytes > kMaxBytes) return 0;
    return Pool(span).MayAddChunk(now_ms) ? uint32_t(bytes) : 0;
  }
  // A chunk of `bytes` that Grow asked for: a large one when it is over one chunk.
  void AddChunk(uint32_t address, uint32_t bytes) { Pool(bytes).AddChunk(address, bytes); }
  // True the first time since that pool last got a chunk: the hook logs it once.
  bool Refused(uint32_t size, uint32_t alignment, uint64_t now_ms) {
    return Pool(Span(size, alignment)).ChunkRefused(now_ms);
  }
  // A block given back; false when it is not one of the overflow's in use.
  bool Give(uint32_t address, uint64_t now_ms) {
    return small_.Give(address, now_ms) || large_.Give(address, now_ms);
  }
  uint64_t capacity() const { return small_.capacity() + large_.capacity(); }
  uint64_t held() const { return small_.held() + large_.held(); }
  uint32_t blocks() const { return small_.blocks() + large_.blocks(); }

 private:
  fm4_stream_textures::Arena& Pool(uint64_t span) { return IsLarge(span) ? large_ : small_; }
  const fm4_stream_textures::Arena& Pool(uint64_t span) const { return IsLarge(span) ? large_ : small_; }
  fm4_stream_textures::Arena small_;
  fm4_stream_textures::Arena large_{0, true};  // no reuse delay, best fit
};

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
