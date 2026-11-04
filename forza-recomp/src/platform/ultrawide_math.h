// Built-in ultrawide layout math; no runtime or plugin dependencies.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
namespace forza::ultrawide {
constexpr double kNativeAspect = 16.0 / 9.0;
struct Layout { double aspect_scale, hud_scale_x, hud_scale_y, hud_offset_x, hud_offset_y, hud_edge_shift; };
// Express HUD placement as a rectangle in a 720-line presentation canvas.
// This derives the transform from rectangle bounds rather than from guest
// projection state. Invalid hot-reload input selects neutral values.
inline Layout ComputeLayout(uint32_t width, uint32_t height, double safe_width,
                            double safe_height, double offset_x, double offset_y,
                            bool edges = false) {
  const double canvas = width && height
      ? std::clamp(720.0 * width / height, 1280.0, 5120.0) : 1280.0;
  const auto bounded = [](double value, double fallback, double low, double high) {
    return std::isfinite(value) ? std::clamp(value, low, high) : fallback;
  };
  const double inset = std::min(bounded(safe_width, 100, 50, 100),
                                bounded(safe_height, 100, 50, 100)) * 0.01;
  const double hud_width = 1280 * inset;
  const double hud_height = 720 * inset;
  const double occupied_width = edges ? canvas * inset : hud_width;
  const double x_room = (canvas - occupied_width) * 0.5;
  const double y_room = (720 - hud_height) * 0.5;
  const double x = std::clamp(bounded(offset_x, 0, -100, 100) * canvas * 0.01,
                              -x_room, x_room);
  const double y = std::clamp(bounded(offset_y, 0, -100, 100) * 7.2,
                              -y_room, y_room);
  return {canvas / 1280, hud_width / canvas, hud_height / 720,
          2 * x / canvas, y / 360,
          edges ? (occupied_width - hud_width) / canvas : 0};
}
// Resolve addresses use whole guest-height multiples. For fractional window
// heights, draw into the next whole multiple and let presentation downsample.
// Camera framing and HUD layout must still use the physical window aspect.
inline uint32_t DrawingHeight(uint32_t height) {
  return ((height + 719) / 720) * 720;
}
inline bool NativeSizeSupported(uint32_t width, uint32_t height) {
  return width > 0 && width <= 1280 * 7 && height > 0 &&
         height <= 720 * 7;
}
inline uint32_t RenderScale(uint32_t extent, uint32_t guest_extent, uint32_t requested) {
  return std::clamp(std::max(requested, (extent + guest_extent - 1) / guest_extent), 1u, 7u);
}
}  // namespace forza::ultrawide
