// The command processor's memo of the descriptor sets written in a frame (SDK patch 0444,
// #192): include/rex/graphics/vulkan/fh1_descriptor_memo.h of the ReIGlue source.
// Build with that folder on the include path:
//   c++ -std=c++17 -I ~/rexglue-sdk-src-forza/sdk/include/rex/graphics/vulkan
//       tools/test_descriptor_memo.cpp -o test_descriptor_memo
#include "fh1_descriptor_memo.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace memo = rex::graphics::vulkan::fh1_descriptor_memo;

#define CHECK(x)                                                   \
  do {                                                             \
    if (!(x)) {                                                    \
      std::fprintf(stderr, "descriptor memo: FAIL: %s\n", #x);     \
      std::exit(1);                                                \
    }                                                              \
  } while (false)

namespace {
struct Set {
  int id;
};

template <typename Memo>
Set* Find(const Memo& m, const std::vector<uint64_t>& words) {
  return m.Find(words.data(), uint32_t(words.size()),
                memo::HashWords(words.data(), words.size()));
}

template <typename Memo>
void Insert(Memo& m, const std::vector<uint64_t>& words, Set* set) {
  m.Insert(words.data(), uint32_t(words.size()), memo::HashWords(words.data(), words.size()), set);
}
}  // namespace

int main() {
  Set a{1}, b{2}, c{3};
  memo::DescriptorMemo<Set*, 16, 8> m;

  // A layout word, then two views and a sampler.
  const std::vector<uint64_t> key_a = {0x10002, 0x1000, 0x2000, 0x9000};
  const std::vector<uint64_t> key_b = {0x10002, 0x2000, 0x1000, 0x9000};  // views swapped
  const std::vector<uint64_t> key_c = {0x10002, 0x1000, 0x2000};          // a word fewer
  const std::vector<uint64_t> key_d = {0x10002 | (1ull << 32), 0x1000, 0x2000, 0x9000};

  CHECK(!Find(m, key_a));
  Insert(m, key_a, &a);
  CHECK(Find(m, key_a) == &a);
  // Only the exact words in the same order and layout find a set.
  CHECK(!Find(m, key_b));
  CHECK(!Find(m, key_c));
  CHECK(!Find(m, key_d));
  Insert(m, key_b, &b);
  Insert(m, key_c, &c);
  CHECK(Find(m, key_a) == &a);
  CHECK(Find(m, key_b) == &b);
  CHECK(Find(m, key_c) == &c);
  CHECK(m.size() == 3);

  // A new frame (or a destroyed handle) forgets every set.
  m.Clear();
  CHECK(m.size() == 0);
  CHECK(!Find(m, key_a));
  CHECK(!Find(m, key_b));
  CHECK(!Find(m, key_c));
  Insert(m, key_b, &a);
  CHECK(Find(m, key_b) == &a);
  CHECK(!Find(m, key_a));

  // Full: further sets are not remembered, earlier ones still found, probes end.
  m.Clear();
  std::vector<std::vector<uint64_t>> keys;
  std::vector<Set> sets(20);
  for (uint64_t i = 0; i < 20; ++i) {
    keys.push_back({0x10001, 0x1000 + i * 0x40});
    Insert(m, keys.back(), &sets[i]);
  }
  CHECK(m.size() == 8);
  for (size_t i = 0; i < 20; ++i) CHECK(Find(m, keys[i]) == (i < 8 ? &sets[i] : nullptr));

  // Colliding hashes (the same slot) still compare the words.
  memo::DescriptorMemo<Set*, 16, 8> collide;
  const std::vector<uint64_t> x = {1, 2}, y = {3, 4};
  collide.Insert(x.data(), 2, 5, &a);
  collide.Insert(y.data(), 2, 5, &b);
  CHECK(collide.Find(x.data(), 2, 5) == &a);
  CHECK(collide.Find(y.data(), 2, 5) == &b);
  CHECK(!collide.Find(x.data(), 1, 5));

  // Many clears (the epoch counter) keep the table empty after each.
  for (int i = 0; i < 1000; ++i) {
    Insert(collide, x, &c);
    CHECK(Find(collide, x) == &c);
    collide.Clear();
    CHECK(!Find(collide, x));
  }

  std::printf("descriptor memo: OK\n");
  return 0;
}
