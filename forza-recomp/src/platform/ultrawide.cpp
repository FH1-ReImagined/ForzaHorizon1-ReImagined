// Copyright (c) 2026 Forza Horizon 1: ReImagined contributors.
// SPDX-License-Identifier: MIT
#include "ultrawide.h"
#include "presentation_policy.h"
#include "ultrawide_math.h"
#include <rex/cvar.h>
#include <rex/logging.h>
#include <mutex>
#include <string>
#include <utility>
#ifdef _WIN32
std::string ForzaGpuBackend();
#endif
REXCVAR_DEFINE_BOOL(forza_ultrawide, false, "Display", "Widen gameplay to the window; keep menus at 16:9");
REXCVAR_DEFINE_BOOL(forza_hud_edges, false, "Display", "Anchor side HUD elements to the ultrawide edges");
REXCVAR_DEFINE_BOOL(forza_hud_layout, false, "Display", "Customize HUD safe area and position during gameplay");
REXCVAR_DEFINE_DOUBLE(forza_hud_safe_width, 100.0, "Display", "HUD safe rectangle width percent")
    .lifecycle(rex::cvar::Lifecycle::kHotReload);
REXCVAR_DEFINE_DOUBLE(forza_hud_safe_height, 100.0, "Display", "HUD safe rectangle height percent")
    .lifecycle(rex::cvar::Lifecycle::kHotReload);
REXCVAR_DEFINE_DOUBLE(forza_hud_offset_x, 0.0, "Display", "HUD horizontal offset, percent of screen")
    .lifecycle(rex::cvar::Lifecycle::kHotReload);
REXCVAR_DEFINE_DOUBLE(forza_hud_offset_y, 0.0, "Display", "HUD vertical offset, percent of screen")
    .lifecycle(rex::cvar::Lifecycle::kHotReload);
namespace forza::ultrawide {
namespace {
// Publish dimensions, policy and projection as one state. Invoke the host
// outside the lock: callbacks may synchronously query or resize the output.
struct Session {
  Extent output{}, drawing{};
  Presentation notify;
  PresentationPolicy policy;
  double projection_ratio = 1;
  bool enabled = false;
};
Session session;
std::mutex session_mutex;
bool HasSetting(const char* name) {
  for (const auto& entry : rex::cvar::GetRegistry())
    if (entry.name == name) return true;
  return false;
}
void SetDimension(const char* name, uint32_t value) {
  rex::cvar::SetFlagByName(name, std::to_string(value));
}
}  // namespace
void Resize(uint32_t width, uint32_t height) {
  std::lock_guard guard(session_mutex);
  session.output = {width, height};
}
Extent OutputSize() {
  std::lock_guard guard(session_mutex);
  return session.drawing.width ? session.output : Extent{};
}
Extent DrawingSize() {
  std::lock_guard guard(session_mutex);
  return session.drawing;
}
void Prepare(uint32_t width, uint32_t height) {
  {
    std::lock_guard guard(session_mutex);
    session.output = {width, height};
    session.drawing = {};
  }
  if (!REXCVAR_GET(forza_ultrawide)) return;
  bool usable = NativeSizeSupported(width, height) && HasSetting("forza_native_width");
#ifdef _WIN32
  usable = usable && ForzaGpuBackend() == "vulkan";
#endif
  if (!usable) {
    rex::cvar::SetFlagByName("forza_ultrawide", "false");
    REXLOG_WARN("Ultrawide cannot prepare {}x{}: native Vulkan drawing supports "
                "positive dimensions up to 8960x5040", width, height);
    return;
  }
  const Extent backing{width, DrawingHeight(height)};
  // EDRAM rows use whole guest-height multiples; filter the complete backing
  // for fractional physical output heights.
  SetDimension("draw_resolution_scale_x", RenderScale(width, 1280, 1));
  SetDimension("draw_resolution_scale_y", RenderScale(height, 720, 1));
  SetDimension("forza_native_width", backing.width);
  SetDimension("forza_native_height", backing.height);
  {
    std::lock_guard guard(session_mutex);
    session.drawing = backing;
  }
  REXLOG_INFO("Ultrawide physical output {}x{}, render backing {}x{}",
              width, height, backing.width, backing.height);
}
void Initialize(uint32_t width, uint32_t height, Presentation presentation) {
  const bool requested = REXCVAR_GET(forza_ultrawide) || REXCVAR_GET(forza_hud_layout);
  const bool enabled = requested && HasSetting("forza_ultrawide_hud_x");
  if (REXCVAR_GET(forza_hud_edges) && !HasSetting("forza_ultrawide_hud_edge_shift")) {
    rex::cvar::SetFlagByName("forza_hud_edges", "false");
    REXLOG_WARN("HUD edge anchors are unavailable in this GPU library");
  }
  if (enabled) {
    rex::cvar::SetFlagByName("forza_present_16_9", "true");
    rex::cvar::SetFlagByName("present_letterbox", "true");
  } else if (requested) {
    REXLOG_WARN("HUD layout requires a GPU library with FH1 presentation support");
  }
  std::lock_guard guard(session_mutex);
  session.output = {width, height};
  session.notify = std::move(presentation);
  session.policy = {};
  session.projection_ratio = 1;
  session.enabled = enabled;
}
void Frame() {
  const bool custom = REXCVAR_GET(forza_hud_layout);
  const bool panoramic = REXCVAR_GET(forza_ultrawide);
  Presentation notify;
  Layout layout{};
  bool active, changed;
  {
    std::lock_guard guard(session_mutex);
    if (!session.enabled) return;
    layout = ComputeLayout(panoramic ? session.output.width : 1280,
        panoramic ? session.output.height : 720,
        custom ? REXCVAR_GET(forza_hud_safe_width) : 100,
        custom ? REXCVAR_GET(forza_hud_safe_height) : 100,
        custom ? REXCVAR_GET(forza_hud_offset_x) : 0,
        custom ? REXCVAR_GET(forza_hud_offset_y) : 0, REXCVAR_GET(forza_hud_edges));
    const bool previous = session.policy.active();
    active = session.policy.Advance(layout.aspect_scale >= 1);
    changed = previous != active;
    session.projection_ratio = active ? layout.aspect_scale : 1;
    notify = session.notify;
  }
  if (notify) {
    const bool letterbox = !active || !panoramic || layout.aspect_scale <= 1;
    notify(letterbox, active ? layout.hud_scale_x : 1, active ? layout.hud_scale_y : 1,
           active ? layout.hud_offset_x : 0, active ? layout.hud_offset_y : 0,
           active ? layout.hud_edge_shift : 0);
  }
  if (changed) REXLOG_INFO("HUD presentation: {}", active ? "gameplay" : "protected screen");
}
double Projection(bool gameplay, bool map, uint32_t width, uint32_t height, double aspect) {
  if (width != 1280 || height != 720) return aspect;
  std::lock_guard guard(session_mutex);
  if (!session.enabled) return aspect;
  session.policy.Observe(gameplay, map);
  return map ? aspect : aspect * session.projection_ratio;
}
}  // namespace forza::ultrawide
