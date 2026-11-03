#include "../src/platform/garage_capacity.h"
#include <cassert>
#include <cstdint>
#include <initializer_list>

int main() {
  using forza::garage::Full;
  for (uint32_t stored : {549u, 550u, 551u, 999u, 1000u, 1001u}) {
    assert(Full(stored, 0, 0, 0) == (stored >= 1000));
    for (uint32_t pending = 0; pending <= 1001; ++pending)
      assert(Full(stored, 0x1000, 0x1000 + pending * 8, 0x4000) ==
             (uint64_t(stored) + pending >= 1000));
  }
  assert(!Full(998, 0x1000, 0x1008, 0x1010));
  assert(Full(999, 0x1000, 0x1008, 0x1010));
  assert(Full(UINT32_MAX, 0x1000, 0x1008, 0x1010));
  assert(Full(0, 0, 8, 8));
  assert(Full(0, 0x1000, 0xff8, 0x1010));
  assert(Full(0, 0x1000, 0x1018, 0x1010));
  assert(Full(0, 0x1001, 0x1009, 0x1011));
  assert(Full(0, 0x1000, 0x1004, 0x1010));
  assert(Full(0, 0x1000, 0x1000, 0x1004));
  assert(!Full(0, 0x1000, 0x1000, 0x1000));
}
