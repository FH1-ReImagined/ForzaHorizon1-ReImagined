// forza - the Frame rate limit choices of the in-game Graphics menu (#783).
// The launcher offers the same rates (launcher/forza_launcher/schema.py FRAME_RATE_PRESETS);
// launcher/test_frame_rate_presets.py fails when the two lists differ.
#pragma once
#include <algorithm>
#include <array>
#include <string>
#include <string_view>
#include <vector>

namespace forza::graphics_menu {
inline constexpr std::array<int, 13> kFrameRatePresets{20,  30,  40,  60,  75,  90, 100,
                                                       120, 144, 165, 180, 200, 240};

// A rate the game runs (main.cpp's frame_rate validator): 20, 30, 40, or 60 to 240.
inline int ParseFrameRate(std::string_view value) {
  if (value.empty() || value.size() > 3) return 0;
  int fps = 0;
  for (char c : value) {
    if (c < '0' || c > '9') return 0;
    fps = fps * 10 + (c - '0');
  }
  return fps == 20 || fps == 30 || fps == 40 || (fps >= 60 && fps <= 240) ? fps : 0;
}

// The presets, plus the current rate in its place when it is another rate the game runs (one
// typed in the launcher, such as 72), so opening and saving the menu keeps it.
inline std::vector<int> FrameRateChoices(std::string_view current) {
  std::vector<int> rates(kFrameRatePresets.begin(), kFrameRatePresets.end());
  if (const int fps = ParseFrameRate(current);
      fps && std::find(rates.begin(), rates.end(), fps) == rates.end())
    rates.insert(std::upper_bound(rates.begin(), rates.end(), fps), fps);
  return rates;
}

inline std::string FrameRateLabel(int fps) { return std::to_string(fps) + " FPS"; }
}  // namespace forza::graphics_menu
