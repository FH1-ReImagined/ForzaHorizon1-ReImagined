// A converted FM4 circuit's streamed textures (src/hooks/fm4_stream_textures.h, #824): which
// failed allocation the hook takes over, and the arena it cuts the textures' memory from.
// Build from the repository root:
//   g++ -std=c++20 -Wall -Wextra -Werror -Iforza-recomp/src forza-recomp/tests/fm4_stream_textures_test.cpp
//       -o fm4-stream-textures-test && ./fm4-stream-textures-test
#include "hooks/fm4_stream_textures.h"

#include <cstdio>
#include <cstdlib>

namespace st = forza::fm4_stream_textures;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}
}  // namespace

int main() {
  // Only the texture load's own allocation: the instruction after its call.
  Check(st::IsTextureLoad(0x82CEBF94), "the texture load's return address");
  Check(!st::IsTextureLoad(0x82CEBF90), "the call itself is not a return address");
  Check(!st::IsTextureLoad(0x82AA5034) && !st::IsTextureLoad(0), "another caller of the heap");

  // Whole 4 KB pages.
  Check(st::Rounded(1) == 4096 && st::Rounded(4096) == 4096 && st::Rounded(4097) == 8192,
        "sizes round up to pages");
  Check(st::Rounded(40988) == 45056, "a 40 KB texture file takes 11 pages");

  const uint32_t kBase = 0xE1000000, kMB = 1u << 20;
  st::Arena arena;
  Check(arena.Take(4096, 0) == 0, "no chunk yet: nothing to give");
  Check(!arena.Give(kBase, 0), "an address that is not a block of the arena is not ours to free");

  arena.AddChunk(kBase, kMB);
  Check(arena.chunks() == 1 && arena.capacity() == kMB && arena.held() == 0, "one chunk, all free");

  // First fit, whole pages, one after the other.
  const uint32_t a = arena.Take(40988, 0), b = arena.Take(1, 0), c = arena.Take(8192, 0);
  Check(a == kBase && b == kBase + 45056 && c == kBase + 49152, "blocks in a row from the chunk's start");
  Check(arena.blocks() == 3 && arena.held() == 45056 + 4096 + 8192, "three blocks held");
  Check(arena.Take(0, 0) == kBase + 57344, "a request of nothing still gets a page of its own");

  // A block given back is ours once, and is not handed out again before the delay is over:
  // the GPU's thread may still read it.
  Check(arena.Give(a, 1000), "the first block given back");
  Check(!arena.Give(a, 1000), "not twice");
  Check(!arena.Give(a + 4096, 1000), "nor an address inside a block");
  Check(arena.held() == 4096 + 8192 + 4096 && arena.peak() == 45056 + 4096 + 8192 + 4096, "the books follow");
  const uint32_t d = arena.Take(45056, 1000 + st::kReuseDelayMs - 1);
  Check(d == kBase + 61440, "just before the delay is over: another place");
  const uint32_t e = arena.Take(45056, 1000 + st::kReuseDelayMs);
  Check(e == a, "once it is over: the first fit is the block that came back");

  // Neighbours given back join: a block of both their sizes fits where they were.
  Check(arena.Give(b, 5000) && arena.Give(c, 5000), "two neighbours given back");
  Check(arena.Take(12288, 5000 + st::kReuseDelayMs) == b, "one block where the two were");

  // A full arena gives nothing, and the allocation fails as it did; another chunk helps.
  st::Arena small;
  small.AddChunk(kBase, 16384);
  Check(small.Take(8192, 0) == kBase && small.Take(8192, 0) == kBase + 8192, "the chunk used up");
  Check(small.Take(4096, 0) == 0, "no room");
  Check(small.Take(32768, 0) == 0, "and never for more than a chunk holds");
  small.AddChunk(kBase + 0x100000, 16384);
  Check(small.Take(4096, 0) == kBase + 0x100000 && small.chunks() == 2 && small.capacity() == 32768,
        "the second chunk's first page");
  // Chunks next to each other are one run.
  st::Arena joined;
  joined.AddChunk(kBase, 8192);
  joined.AddChunk(kBase + 8192, 8192);
  Check(joined.Take(16384, 0) == kBase, "a block across two chunks that touch");

  // Many textures coming and going: nothing lost, nothing handed out twice.
  st::Arena busy;
  busy.AddChunk(kBase, 4 * kMB);
  uint32_t held[64] = {};
  uint64_t now = 0;
  for (int round = 0; round < 2000; ++round) {
    const int k = round * 37 % 64;
    now += 50;
    if (held[k]) {
      Check(busy.Give(held[k], now), "a held block is given back");
      held[k] = 0;
    } else {
      const uint32_t bytes = 4096u * (1 + round % 23);
      held[k] = busy.Take(bytes, now);
      Check(held[k] != 0, "room in a 4 MB chunk for 64 blocks of up to 92 KB");
      for (int j = 0; j < 64; ++j)
        Check(j == k || !held[j] || held[j] != held[k], "no address handed out twice");
    }
  }
  uint64_t in_use = 0;
  for (uint32_t address : held)
    if (address) ++in_use;
  Check(busy.blocks() == in_use, "the arena's count is ours");
  for (uint32_t& address : held)
    if (address) Check(busy.Give(address, now), "all given back");
  Check(busy.blocks() == 0 && busy.held() == 0, "nothing held");
  Check(busy.Take(4 * kMB, now + st::kReuseDelayMs) == kBase, "and the whole chunk is one free run again");

  std::puts("fm4_stream_textures_test: ok");
  return 0;
}
