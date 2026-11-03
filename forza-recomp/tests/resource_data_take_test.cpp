#include "../src/hooks/resource_data_take.h"

#include <array>
#include <barrier>
#include <cstdio>
#include <cstdlib>
#include <thread>

#define CHECK(condition) do { if (!(condition)) { \
  std::fprintf(stderr, "Failed line %d: %s\n", __LINE__, #condition); \
  std::abort(); } } while (false)

using forza::resource::TakeData;

static uint32_t GuestWord(uint32_t value) {
  if constexpr (std::endian::native == std::endian::little) return __builtin_bswap32(value);
  return value;
}

int main() {
  // The old unload could read a pointer, pause, then clear/release it after the
  // load had taken and released the same pointer. An atomic load here models
  // that schedule without introducing a C++ data race into the test itself.
  uint32_t field = GuestWord(0x2EA0AD90);
  const auto old_unload = std::atomic_ref<uint32_t>(field).load();
  const auto load = TakeData(field);
  std::atomic_ref<uint32_t>(field).store(0);
  CHECK(old_unload == GuestWord(load));  // both sides owned the same object

  // Either side winning now leaves the other with no object to release.
  for (uint32_t pointer : {0u, 0x2EA0AD90u, 0x81020304u, 0xFFCAB000u}) {
    field = GuestWord(pointer);
    CHECK(TakeData(field) == pointer);
    CHECK(TakeData(field) == 0);
    CHECK(field == 0);
  }

  // There must also be no delayed zero-store after the exchange: data published
  // between two takes must survive until the second owner takes it.
  field = GuestWord(0x2EA0AD90);
  CHECK(TakeData(field) == 0x2EA0AD90);
  std::atomic_ref<uint32_t>(field).store(GuestWord(0x2EA0BEA0));
  CHECK(TakeData(field) == 0x2EA0BEA0);

  // Force load/unload contenders to race for the same field repeatedly. Each
  // round publishes a distinct pointer and must have exactly one winner.
  constexpr int rounds = 10000;
  std::array<uint32_t, 2> taken{};
  std::barrier phase(3);
  auto contender = [&](int index) {
    for (int round = 0; round < rounds; ++round) {
      phase.arrive_and_wait();
      taken[index] = TakeData(field);
      phase.arrive_and_wait();
    }
  };
  std::thread loader(contender, 0), unloader(contender, 1);
  for (int round = 0; round < rounds; ++round) {
    const uint32_t pointer = 0x2EA00000u + uint32_t(round) * 4;
    field = GuestWord(pointer);
    phase.arrive_and_wait();
    phase.arrive_and_wait();
    CHECK((taken[0] == pointer && taken[1] == 0) ||
          (taken[0] == 0 && taken[1] == pointer));
    CHECK(field == 0);
  }
  loader.join();
  unloader.join();
  std::puts("resource data: endian, prior double-take schedule, republish and 10000 competing takes passed");
}
