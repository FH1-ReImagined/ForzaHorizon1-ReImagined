#include "../src/platform/frame_rate_choices.h"
#include <cassert>
#include <vector>
using namespace forza::graphics_menu;
int main() {
  const std::vector<int> presets{20, 30, 40, 60, 75, 90, 100, 120, 144, 165, 180, 200, 240};
  // #783: the menu offers the launcher's rates, 75 among them.
  for (const char *current : {"30", "60", "75", "240", "", "abc", "45", "999", "0", "-60"})
    assert(FrameRateChoices(current) == presets);
  // A rate typed in the launcher stays a choice, in order.
  auto with_72 = presets;
  with_72.insert(with_72.begin() + 4, 72);
  assert(FrameRateChoices("72") == with_72);
  auto with_61 = presets;
  with_61.insert(with_61.begin() + 4, 61);
  assert(FrameRateChoices("61") == with_61);
  auto with_239 = presets;
  with_239.insert(with_239.end() - 1, 239);
  assert(FrameRateChoices("239") == with_239);
  assert(ParseFrameRate("20") == 20 && ParseFrameRate("40") == 40 && ParseFrameRate("60") == 60);
  assert(!ParseFrameRate("59") && !ParseFrameRate("241") && !ParseFrameRate("1000") &&
         !ParseFrameRate("7 5"));
  assert(FrameRateLabel(75) == "75 FPS");
}
