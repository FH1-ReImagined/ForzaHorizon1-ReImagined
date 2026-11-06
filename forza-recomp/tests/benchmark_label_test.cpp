// The benchmark card's settings line (src/platform/benchmark_label.h, #640).
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/benchmark_label_test.cpp
#include "platform/benchmark_label.h"

#include <cstdio>
#include <cstdlib>

using forza::benchmark::LabelInputs;
using forza::benchmark::SettingsLabel;

#define CHECK_EQ(a, b)                                                                   \
  do {                                                                                   \
    if ((a) != (b)) {                                                                    \
      std::fprintf(stderr, "benchmark label: FAIL: %s\n  got: %s\n", #a, (a).c_str());  \
      std::exit(1);                                                                      \
    }                                                                                    \
  } while (false)

int main() {
  // No upscaler: the output-size cvars keep their 2560x1440 defaults and must not show.
  CHECK_EQ(SettingsLabel({"off", "2560", "1440", "1", "540", "fsr", "30"}),
           std::string("960 x 540 scene  /  AMD FSR 1  /  30 FPS cap"));
  CHECK_EQ(SettingsLabel({"off", "2560", "1440", "2", "720", "bilinear", "60"}),
           std::string("2560 x 1440 scene  /  bilinear  /  60 FPS cap"));
  CHECK_EQ(SettingsLabel({"", "2560", "1440", "", "720", "cas", "60"}),
           std::string("1280 x 720 scene  /  AMD CAS  /  60 FPS cap"));
  // With one: its output and name, as before.
  CHECK_EQ(SettingsLabel({"fsr3_quality", "1920", "1080", "2", "540", "fsr", "60"}),
           std::string("1920 x 1080  /  fsr3_quality  /  60 FPS cap"));
  std::puts("benchmark label: ok");
}
