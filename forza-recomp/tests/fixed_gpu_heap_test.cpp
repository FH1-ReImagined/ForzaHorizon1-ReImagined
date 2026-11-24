// Which heap a request ends in (src/platform/fixed_gpu_heap.h, #857): the thread's arena for
// category 0x29, heaps that are off passing requests on, and the chain of heaps tried. And the
// overflow's blocks (Overflow, #787): blocks over one chunk, mixed sizes, reuse and the cap.
// Standalone: c++ -std=c++17 fixed_gpu_heap_test.cpp -o fixed_gpu_heap_test
#include "../src/platform/fixed_gpu_heap.h"
#include <cstdint>
#include <cstdio>
#include <map>

// Checks that count with NDEBUG too (assert would not).
static int failures = 0;
#define CHECK(x)                                                   \
  do {                                                             \
    if (!(x)) {                                                    \
      std::fprintf(stderr, "%s:%d: CHECK(%s)\n", __FILE__, __LINE__, #x); \
      ++failures;                                                  \
    }                                                              \
  } while (0)

namespace f = forza::fixed_gpu_heap;

struct Guest {
  std::map<uint32_t, uint32_t> words;
  std::map<uint32_t, uint8_t> bytes;
  uint32_t Read32(uint32_t a) const {
    auto it = words.find(a);
    return it == words.end() ? 0 : it->second;
  }
  uint32_t Read8(uint32_t a) const {
    auto it = bytes.find(a);
    return it == bytes.end() ? 0 : it->second;
  }
  static uint32_t Arena(uint32_t index) { return f::kArenas + index * f::kArenaSize; }
  static uint32_t Heap(uint32_t category) {
    return Arena(category & 0xF) + ((category & 0x30) == 0x10 ? f::kLowHeap : f::kHighHeap);
  }
  // A heap in use (its arena on, a size), passing failed requests to `next`.
  void On(uint32_t category, uint32_t next = 8) {
    bytes[Arena(category & 0xF) + f::kArenaOn] = 1;
    words[Heap(category) + f::kHeapSize] = 0x100000;
    words[Heap(category) + f::kNext] = next;
    words[Heap(category) + f::kNextOverride] = 8;
  }
  uint32_t Last(uint32_t category, uint32_t thread, uint32_t fallback = 1) const {
    return f::LastHeap(category, thread, fallback, [this](uint32_t a) { return Read32(a); },
                       [this](uint32_t a) { return Read8(a); });
  }
};

// The hook's steps for one block: Take, else Grow + a chunk at `next` (advanced past it) + Take.
static uint32_t Serve(f::Overflow& o, uint32_t size, uint64_t now, uint32_t& next,
                      uint32_t alignment = f::kPage) {
  if (const uint32_t block = o.Take(size, alignment, now)) return block;
  const uint32_t bytes = o.Grow(size, alignment, now);
  if (!bytes) return 0;
  o.AddChunk(next, bytes);
  // Chunks never touch, as two guest allocations need not.
  next += bytes + f::kMaxAlignment;
  return o.Take(size, alignment, now);
}

static void TestOverflow() {
  namespace st = forza::fm4_stream_textures;
  constexpr uint32_t k16 = 16u << 20, k1 = 1u << 20;
  constexpr uint64_t later = st::kReuseDelayMs;
  {
    // A 16 MB block gets a 16 MB chunk of its own, and the block is the whole of it.
    f::Overflow o;
    uint32_t next = 0x40000000;
    CHECK(o.Take(k16, f::kPage, 0) == 0);
    CHECK(o.Grow(k16, f::kPage, 0) == k16);
    const uint32_t a = Serve(o, k16, 0, next);
    CHECK(a == 0x40000000);
    CHECK(o.capacity() == k16 && o.held() == k16 && o.blocks() == 1);
    // Given back, it is handed out again at once, with no new chunk; while it is in use, a
    // second block needs a chunk of its own.
    CHECK(o.Give(a, 10));
    CHECK(!o.Give(a, 10));  // not twice
    CHECK(o.held() == 0);
    CHECK(o.Take(k16, f::kPage, 10) == a);
    CHECK(o.capacity() == k16);
    const uint32_t b = Serve(o, k16, 10, next);
    CHECK(b && b != a && o.capacity() == 2 * uint64_t(k16));
    // A block between one and two chunks takes a 16 MB chunk.
    CHECK(o.Grow(f::kChunkBytes + 1, f::kPage, 0) == k16);
  }
  {
    // Race Central at Paint job detail 2x: 16 MB and 22,413,312-byte buffers taken and given
    // back in turn, twice (one paint job, then the next). Two chunks, 16 + 24 MB, serve all of
    // them, each size from its own chunk (best fit: a 16 MB block never goes to the 24 MB run).
    constexpr uint32_t k22 = 22413312;
    static_assert(f::ChunkFor(k22) == 24u << 20);
    f::Overflow o;
    uint32_t next = 0x40000000;
    const uint32_t r22 = Serve(o, k22, 0, next);  // the larger chunk first, at the lower address
    const uint32_t r16 = Serve(o, k16, 0, next);
    CHECK(r22 && r16 && r16 > r22);
    CHECK(o.Give(r22, 100) && o.Give(r16, 200));
    for (int round = 0; round < 4; ++round) {
      const uint64_t t = 300 + round * 100;
      const uint32_t x = Serve(o, k16, t, next);
      CHECK(x == r16);
      const uint32_t y = Serve(o, k22, t, next);
      CHECK(y == r22);
      CHECK(o.Give(x, t + 50) && o.Give(y, t + 60));
    }
    CHECK(o.capacity() == (40u << 20) && o.held() == 0);
    // Both in use at once: still the same two chunks.
    CHECK(Serve(o, k16, 1000, next) == r16 && Serve(o, k22, 1000, next) == r22);
    CHECK(o.capacity() == 40u << 20);
    // A third 16 MB block while both are in use: a chunk of its own (56 MB); a fourth large
    // block then finds the cap.
    CHECK(Serve(o, k16, 1000, next));
    CHECK(o.capacity() == 56u << 20);
    CHECK(!Serve(o, k22, 1000, next) && o.Grow(k16, f::kPage, 1000) == 0);
    CHECK(o.Grow(f::kChunkBytes, f::kPage, 1000) == f::kChunkBytes);  // a small block still has room
  }
  {
    // Mixed sizes: small blocks come from 8 MB chunks, never from a large chunk given back, so
    // the next large block still finds its run whole (no fragmentation trap).
    f::Overflow o;
    uint32_t next = 0x40000000;
    const uint32_t big = Serve(o, k16, 0, next);
    CHECK(big);
    CHECK(o.Give(big, 0));
    const uint32_t small = Serve(o, k1, later, next);  // after the delay: the large run is free
    CHECK(small && (small < big || small >= big + k16));
    CHECK(o.capacity() == uint64_t(k16) + f::kChunkBytes);
    CHECK(o.Take(k16, f::kPage, later) == big);  // whole, no new chunk
    CHECK(o.capacity() == uint64_t(k16) + f::kChunkBytes);
    // An 8 MB block exactly fills a small chunk; the next small ones take a new one.
    f::Overflow p;
    uint32_t n2 = 0x40000000;
    const uint32_t c8 = Serve(p, f::kChunkBytes, 0, n2);
    CHECK(c8 && p.capacity() == f::kChunkBytes);
    const uint32_t s1 = Serve(p, 4096, 0, n2);
    CHECK(s1 && p.capacity() == 2 * uint64_t(f::kChunkBytes));
    // Small blocks given back join again into a run long enough for an 8 MB one.
    f::Overflow q;
    uint32_t n3 = 0x40000000;
    uint32_t parts[8];
    for (auto& part : parts) part = Serve(q, k1, 0, n3);
    CHECK(q.capacity() == f::kChunkBytes && q.held() == f::kChunkBytes);
    for (auto part : parts) CHECK(q.Give(part, 0));
    CHECK(q.held() == 0);
    CHECK(q.Take(f::kChunkBytes, f::kPage, later) == parts[0]);
    CHECK(q.capacity() == f::kChunkBytes);
  }
  {
    // Alignment: a 64 KB-aligned block skips to the next multiple in its run, and the pages
    // before it stay free for others. The chunks start one page before a multiple of 64 KB, as the
    // guest's physical heap at 0xE0000000 gives them (seen: E7F2F000).
    static_assert(f::Span(k16, 0x10000) == k16 + 0xF000 && f::Span(k16, 0) == k16);
    static_assert(f::ChunkFor(f::Span(k16, 0x10000)) == 24u << 20);
    f::Overflow o;
    uint32_t next = 0xE7F2F000;
    const uint32_t aligned = Serve(o, 4096, 0, next, 0x10000);
    CHECK(aligned == 0xE7F30000);
    CHECK(Serve(o, 4096, 0, next) == 0xE7F2F000);  // the page skipped
    CHECK(Serve(o, 4096, 0, next) == 0xE7F31000);
    CHECK(Serve(o, 4096, 0, next, 0x10000) == 0xE7F40000);
    CHECK(Serve(o, 4096, 0, next) == 0xE7F32000);  // from the pages skipped
    CHECK(o.capacity() == f::kChunkBytes);
    // A small block whose alignment could push it past 8 MB goes to the large pool.
    CHECK(o.Grow(f::kChunkBytes, 0x10000, 0) == 16u << 20);
    // The saved layout: 16 MB with 64 KB alignment needs a 24 MB chunk to fit wherever that
    // starts; given back, it is taken again at once at the same place.
    CHECK(o.Grow(k16, 0x10000, 0) == 24u << 20);
    const uint32_t layout = Serve(o, k16, 0, next, 0x10000);
    CHECK(layout && (layout & 0xFFFF) == 0);
    CHECK(o.capacity() == uint64_t(f::kChunkBytes) + (24u << 20));
    CHECK(o.Give(layout, 1) && Serve(o, k16, 1, next, 0x10000) == layout);
    CHECK(o.capacity() == uint64_t(f::kChunkBytes) + (24u << 20));
    // An alignment that is not a power of two is never served.
    CHECK(o.Take(4096, 0x3000, 2) == 0);
    // An exact 16 MB chunk one page off 64 KB holds a page-aligned 16 MB block but not a
    // 64 KB-aligned one, which is why Grow asks for more.
    f::Overflow tight;
    tight.AddChunk(0xE7F2F000, k16);
    CHECK(tight.Take(k16, 0x10000, 0) == 0);
    CHECK(tight.Take(k16, f::kPage, 0) == 0xE7F2F000);
    // An aligned block given back joins the pages skipped before it and the rest of its run.
    f::Overflow join;
    join.AddChunk(0xE7F2F000, 24u << 20);
    const uint32_t mid = join.Take(k16, 0x10000, 0);
    CHECK(mid == 0xE7F30000);
    CHECK(join.Give(mid, 0));
    CHECK(join.Take(24u << 20, f::kPage, 0) == 0xE7F2F000);
  }
  {
    // The cap: 64 MB in all, both pools together.
    f::Overflow o;
    uint32_t next = 0x40000000;
    CHECK(Serve(o, k1, 0, next));                   // 8 MB small chunk
    for (int i = 0; i < 3; ++i) CHECK(Serve(o, k16, 0, next));  // 56 MB
    CHECK(o.capacity() == 56u << 20);
    CHECK(o.Grow(k16, f::kPage, 0) == 0);                     // 72 MB would be over
    CHECK(!Serve(o, k16, 0, next));
    CHECK(o.Grow(f::kChunkBytes, f::kPage, 0) == f::kChunkBytes);  // one more 8 MB chunk fits
    CHECK(Serve(o, f::kChunkBytes, 0, next));
    CHECK(o.capacity() == f::kMaxBytes);
    CHECK(o.Grow(4096, f::kPage, 0) == 0);
    CHECK(o.Grow(f::kMaxBytes + 1, f::kPage, 0) == 0 && o.Grow(0, f::kPage, 0) == 0);
    // One block of the whole cap, in an empty overflow.
    f::Overflow whole;
    uint32_t n2 = 0x40000000;
    CHECK(whole.Grow(f::kMaxBytes, f::kPage, 0) == f::kMaxBytes);
    CHECK(Serve(whole, f::kMaxBytes, 0, n2));
    CHECK(whole.Grow(4096, f::kPage, 0) == 0);
  }
  {
    // A refused chunk: that pool waits kChunkRetryMs before it asks again; the other does not.
    f::Overflow o;
    CHECK(o.Grow(k16, f::kPage, 0) == k16);
    CHECK(o.Refused(k16, f::kPage, 0));
    CHECK(!o.Refused(k16, f::kPage, 0));  // logged once
    CHECK(o.Grow(k16, f::kPage, 1) == 0);
    CHECK(o.Grow(k1, f::kPage, 1) == f::kChunkBytes);
    CHECK(o.Grow(k16, f::kPage, st::kChunkRetryMs) == k16);
  }
}

int main() {
  const uint32_t fixed = Guest::Heap(0x21);
  static_assert(f::kArenas + 1 * f::kArenaSize + f::kHighHeap == 0x83396AF0);
  {
    Guest g;
    g.On(0x21);
    CHECK(g.Last(0x21, 1) == fixed);
    // 0x29: the calling thread's arena (1 in free roam, 4 in the garage's menus).
    CHECK(g.Last(0x29, 1) == fixed);
    CHECK(g.Last(0x29, 4) == 0);   // heap 0x24 off and nothing after it
    CHECK(g.Last(0x29, 9, 1) == fixed);  // thread arena 9: the manager's default
    CHECK(g.Last(0x29, 8) == 0);   // 8: none
    g.On(0x24);
    CHECK(g.Last(0x29, 4) == Guest::Heap(0x24));
    CHECK(f::Eligible(g.Last(0x29, 1), 0x1D3CC, 4, 8u << 20));
    CHECK(!f::Eligible(g.Last(0x29, 4), 0x1D3CC, 4, 8u << 20));
  }
  {
    // A heap that is off passes the request to its link: 0x24 off -> 0x21.
    Guest g;
    g.On(0x21);
    g.words[Guest::Heap(0x24) + f::kNext] = 0x21;
    g.words[Guest::Heap(0x24) + f::kNextOverride] = 8;
    CHECK(g.Last(0x24, 1) == fixed);
    // ... or to its override when that is not 8.
    g.words[Guest::Heap(0x24) + f::kNextOverride] = 0x20;
    g.On(0x20);
    CHECK(g.Last(0x24, 1) == Guest::Heap(0x20));
  }
  {
    // A heap that fails passes the request on: 0x20 -> 0x21, so 0x21 is the last one tried.
    Guest g;
    g.On(0x20, 0x21);
    g.On(0x21);
    CHECK(g.Last(0x20, 1) == fixed);
    CHECK(f::Eligible(g.Last(0x20, 1), 4096, 4096, 8u << 20));
    // And 0x21 -> 0x26: the game's stop then looks at 0x26, which the overflow leaves alone.
    g.On(0x21, 0x26);
    g.On(0x26);
    CHECK(g.Last(0x21, 1) == Guest::Heap(0x26));
    CHECK(!f::Eligible(g.Last(0x21, 1), 4096, 4096, 8u << 20));
  }
  // Sizes and alignments the arena's chunks and pages hold.
  CHECK(!f::Eligible(fixed, 0, 4, 8u << 20));
  CHECK(!f::Eligible(fixed, (8u << 20) + 1, 4, 8u << 20));
  CHECK(f::Eligible(fixed, 4096, 8192, 8u << 20));
  CHECK(f::Eligible(fixed, 4096, 0, 8u << 20));
  CHECK(!f::Eligible(fixed, 4096, f::kMaxAlignment * 2, 8u << 20));
  CHECK(!f::Eligible(fixed, 4096, 0x3000, 8u << 20));  // not a power of two
  CHECK(f::Eligible(fixed, 8u << 20, 4096, 8u << 20));
  // The hook's limit is the whole overflow: Paint job detail 2x's 16 MB render texture (#787).
  CHECK(f::Eligible(fixed, 16u << 20, 4096, f::kMaxBytes));
  CHECK(f::Eligible(fixed, f::kMaxBytes, 4096, f::kMaxBytes));
  CHECK(!f::Eligible(fixed, f::kMaxBytes + 1, 4096, f::kMaxBytes));
  // The 2x paint job's saved layout: 16 MB with 64 KB alignment (sub_82D4E7D0).
  CHECK(f::Eligible(fixed, 16u << 20, 0x10000, f::kMaxBytes));
  static_assert(f::kChunkBytes == 8u << 20 && f::kMaxBytes == 64u << 20);
  static_assert(f::ChunkFor(1) == f::kChunkBytes && f::ChunkFor(f::kChunkBytes) == f::kChunkBytes);
  static_assert(f::ChunkFor(f::kChunkBytes + 1) == 16u << 20 && f::ChunkFor(16u << 20) == 16u << 20);
  static_assert(f::ChunkFor(f::kMaxBytes) == f::kMaxBytes);
  TestOverflow();
  // A loop of links ends.
  {
    Guest g;
    g.words[Guest::Heap(0x22) + f::kNext] = 0x23;
    g.words[Guest::Heap(0x22) + f::kNextOverride] = 8;
    g.words[Guest::Heap(0x23) + f::kNext] = 0x22;
    g.words[Guest::Heap(0x23) + f::kNextOverride] = 8;
    CHECK(g.Last(0x22, 1) == 0);
  }
  if (failures) std::fprintf(stderr, "%d check(s) failed\n", failures);
  else std::printf("fixed_gpu_heap_test: all checks passed\n");
  return failures ? 1 : 0;
}
