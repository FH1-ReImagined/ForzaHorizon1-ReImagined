// Selected effective startup cvars, not a dump of config paths or account data.
#pragma once

#include <optional>
#include <string>
#include <string_view>

namespace forza::performance_settings {

// Missing GPU-provider flags must remain distinguishable from disabled flags.
inline std::string Value(const std::optional<std::string>& value) {
  if (!value) return "<unavailable>";
  if (value->empty()) return "<empty>";
  std::string out;
  for (char c : value->substr(0, 64)) {
    if (static_cast<unsigned char>(c) < 32 || c == 127) out += '?';
    else out += c;
  }
  if (value->size() > 64) out += "...";
  return out;
}

// Read at emission time, after plugin loading, config/defaults, benchmark restore
// and ultrawide setup. Keep a fixed allowlist: no paths, server or identity cvars.
template <typename Read, typename Emit>
void Snapshot(Read read, Emit emit) {
  constexpr std::string_view groups[] = {
      "gpu_backend frame_rate video_mode_refresh_rate vsync resolution "
      "resolution_scale draw_resolution_scale_x draw_resolution_scale_y",
      "scene_msaa scene_resolution native_2x_msaa fh1_scene_720p_native_hud "
      "fh1_native_scene_scale fh1_upscaler fh1_output_width fh1_output_height",
      "present_effect swap_post_effect vulkan_allow_present_mode_mailbox forza_ultrawide "
      "readback_resolve vulkan_readback_skip_render_targets vulkan_max_frames_in_flight "
      "vulkan_submission_draw_limit",
      "async_shader_compilation vulkan_allow_present_mode_fifo_latest_ready "
      "vulkan_allow_present_mode_immediate world_shadow_detail world_texture_detail "
      "world_shadow_filter log_level log_verbose"};
  for (auto group : groups) {
    std::string line;
    while (!group.empty()) {
      const auto end = group.find(' ');
      const auto key = group.substr(0, end);
      if (!line.empty()) line += "; ";
      line += key;
      line += '=';
      line += Value(read(key));
      if (end == std::string_view::npos) break;
      group.remove_prefix(end + 1);
    }
    emit(line);
  }
}
}  // namespace forza::performance_settings
