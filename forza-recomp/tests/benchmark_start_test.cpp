// How long the benchmark waits for its race (src/platform/benchmark_start.h, #725).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/benchmark_start_test.cpp
#include "platform/benchmark_start.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

using forza::benchmark::StartTimeout;

static void Check(const char *got, const char *want, const char *what) {
  if ((got == nullptr) != (want == nullptr) || (got && std::strcmp(got, want) != 0)) {
    std::fprintf(stderr, "benchmark start: FAIL: %s: got %s\n", what, got ? got : "(wait)");
    std::exit(1);
  }
}

int main() {
  // A slow first load that keeps presenting: past the old 5 minutes, still waiting.
  Check(StartTimeout(6 * 60, 0.4, true), nullptr, "slow load, frames presented");
  Check(StartTimeout(14 * 60, 12, true), nullptr, "a 12 s shader-compile frame");
  Check(StartTimeout(5, -1, false), nullptr, "no frame yet at the boot");
  // No frame for a minute: the game stopped drawing.
  Check(StartTimeout(3 * 60, 60, true), "benchmark_stalled", "no frame for 60 s");
  // The far limits.
  Check(StartTimeout(15 * 60, 1, false), "event_load_timeout", "event never requested");
  Check(StartTimeout(30 * 60, 1, true), "benchmark_start_timeout", "race never started");
  std::puts("benchmark start: ok");
}
