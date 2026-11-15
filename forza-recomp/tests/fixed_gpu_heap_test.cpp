// Which heap a request ends in (src/platform/fixed_gpu_heap.h, #857): the thread's arena for
// category 0x29, heaps that are off passing requests on, and the chain of heaps tried.
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
  CHECK(!f::Eligible(fixed, 4096, 8192, 8u << 20));
  CHECK(f::Eligible(fixed, 8u << 20, 4096, 8u << 20));
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
