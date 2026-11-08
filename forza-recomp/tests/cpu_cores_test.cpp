#include "platform/cpu_cores.h"

#include <cassert>

int main() {
  using forza::cpu_cores::CountCores;
  // Steam Deck: 4 cores, 8 threads (siblings n and n+4 on Linux).
  auto deck = [](unsigned cpu) { return std::to_string(cpu % 4) + "," + std::to_string(cpu % 4 + 4); };
  assert(CountCores({0, 1, 2, 3, 4, 5, 6, 7}, deck) == 4);
  // Ryzen 5 5600X: 6 cores, 12 threads (siblings n and n+6).
  auto r5 = [](unsigned cpu) { return std::to_string(cpu % 6) + "," + std::to_string(cpu % 6 + 6); };
  assert(CountCores({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11}, r5) == 6);
  // 8 cores without SMT, the process pinned to six of them (taskset -c 0-5).
  auto plain = [](unsigned cpu) { return std::to_string(cpu); };
  assert(CountCores({0, 1, 2, 3, 4, 5}, plain) == 6);
  // Only one thread of each of two cores allowed, then both threads of a third.
  assert(CountCores({0, 1, 2, 8}, r5) == 3);
  // Unknown topology: each CPU counts.
  assert(CountCores({0, 1, 2}, [](unsigned) { return std::string(); }) == 3);
  return 0;
}
