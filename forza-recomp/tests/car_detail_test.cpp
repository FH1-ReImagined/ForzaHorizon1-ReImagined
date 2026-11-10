// world_car_detail (src/hooks/car_detail.cpp): the car LOD distances and counts each level gives
// (src/platform/car_detail.h).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/car_detail_test.cpp
#include "platform/car_detail.h"

#include <cstdio>
#include <cstdlib>

namespace c = forza::car_detail;

#define CHECK(x)                                              \
  do {                                                        \
    if (!(x)) {                                               \
      std::fprintf(stderr, "car detail: FAIL: %s\n", #x);     \
      std::exit(1);                                           \
    }                                                         \
  } while (false)

int main() {
  CHECK(c::ParseLevel("console") == c::Level::kConsole);
  CHECK(c::ParseLevel("") == c::Level::kConsole);
  CHECK(c::ParseLevel("Ultra") == c::Level::kConsole);  // only the launcher's spellings
  CHECK(c::ParseLevel("high") == c::Level::kHigh);
  CHECK(c::ParseLevel("ultra") == c::Level::kUltra);

  // Console returns the game's values exactly.
  for (const float d : {15.0f, 25.0f, 50.0f, 100.0f, 20.0f, 80.0f}) {
    CHECK(c::ScaleDistance(d, c::Level::kConsole) == d);
  }
  for (const int n : {2, 4, 6}) CHECK(c::ScaleCount(n, c::Level::kConsole) == n);

  // CarLODTransDist 15/25/50 m: High 30/50/100, Ultra 60/100/200.
  CHECK(c::ScaleDistance(15.0f, c::Level::kHigh) == 30.0f);
  CHECK(c::ScaleDistance(50.0f, c::Level::kHigh) == 100.0f);
  CHECK(c::ScaleDistance(15.0f, c::Level::kUltra) == 60.0f);
  CHECK(c::ScaleDistance(100.0f, c::Level::kUltra) == 400.0f);
  // CarLODThresholds 2/4/6: High 4/8/12, Ultra 8/16/24.
  CHECK(c::ScaleCount(2, c::Level::kHigh) == 4);
  CHECK(c::ScaleCount(6, c::Level::kHigh) == 12);
  CHECK(c::ScaleCount(4, c::Level::kUltra) == 16);

  // Values that are not distances or counts (0 or less, or a sentinel) stay as they are.
  CHECK(c::ScaleDistance(0.0f, c::Level::kUltra) == 0.0f);
  CHECK(c::ScaleDistance(-1.0f, c::Level::kUltra) == -1.0f);
  CHECK(c::ScaleCount(0, c::Level::kUltra) == 0);
  CHECK(c::ScaleCount(-1, c::Level::kUltra) == -1);
  CHECK(c::ScaleCount(0x7FFFFFFF, c::Level::kUltra) == 0x7FFFFFFF);

  // Each level keeps more detail than the one below it.
  CHECK(c::DistanceScale(c::Level::kConsole) < c::DistanceScale(c::Level::kHigh));
  CHECK(c::DistanceScale(c::Level::kHigh) < c::DistanceScale(c::Level::kUltra));
  CHECK(c::CountScale(c::Level::kConsole) < c::CountScale(c::Level::kHigh));
  CHECK(c::CountScale(c::Level::kHigh) < c::CountScale(c::Level::kUltra));

  std::puts("car detail: ok");
  return 0;
}
