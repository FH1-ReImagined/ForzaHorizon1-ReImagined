// forza - the settings line of the benchmark's card (src/platform/benchmark_session.cpp, #640).
// Without guest memory or cvars, so tests/benchmark_label_test.cpp can check it.
#pragma once

#include <charconv>
#include <string>
#include <string_view>

namespace forza::benchmark {

struct LabelInputs {
  std::string_view upscaler;          // fh1_upscaler: "off", fsr3_*, dlss modes, smaa, ...
  std::string_view output_width;      // fh1_output_width/height: only used with an upscaler
  std::string_view output_height;
  std::string_view resolution_scale;  // the 720p game's integer render scale
  std::string_view scene_resolution;  // "540" draws the scene at 960x540 (no upscaler only)
  std::string_view present_effect;    // bilinear, cas, fsr (FSR 1)
  std::string_view frame_rate;
};

inline int Int(std::string_view s, int fallback) {
  int v = fallback;
  const auto r = std::from_chars(s.data(), s.data() + s.size(), v);
  return r.ec == std::errc() && v > 0 ? v : fallback;
}

// With an upscaler: its output size and name. Without one the output size cvars are unused
// (their defaults read 2560 x 1440): the scene the game draws and the filter that scales it.
inline std::string SettingsLabel(const LabelInputs& in) {
  const std::string cap = std::string(in.frame_rate) + " FPS cap";
  if (!in.upscaler.empty() && in.upscaler != "off")
    return std::string(in.output_width) + " x " + std::string(in.output_height) + "  /  " +
           std::string(in.upscaler) + "  /  " + cap;
  const int scale = Int(in.resolution_scale, 1);
  const bool small = in.scene_resolution == "540";
  const int w = (small ? 960 : 1280) * scale, h = (small ? 540 : 720) * scale;
  const std::string filter = in.present_effect == "fsr" ? "AMD FSR 1"
                             : in.present_effect == "cas" ? "AMD CAS"
                                                           : "bilinear";
  return std::to_string(w) + " x " + std::to_string(h) + " scene  /  " + filter + "  /  " + cap;
}

}  // namespace forza::benchmark
