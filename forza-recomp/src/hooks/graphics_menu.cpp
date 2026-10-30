// Original pause-menu entry and difficulty-style graphics settings.
// tu_address_map: TU4 addresses
// The scene/model layouts below are validated for Title Update 4 only.
#include "../platform/graphics_menu.h"
#include "../platform/native_pause_scene.h"
#include "../platform/online_photo.h"
#include "../platform/native_quit.h"
#include "../platform/native_car_tuning_menu.h"
#include "../platform/custom_race.h"
#include "../platform/benchmark.h"
#include "../platform/hud_overlay.h"
#include "../platform/graphics_presets.h"
#include "../platform/graphics_resolution.h"
#include "../platform/ultrawide.h"
#ifdef __APPLE__
#include "../platform/macos_graphics_config.h"
#include <iterator>
#endif
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include <algorithm>
#include <array>
#include <bit>
#include <cstdlib>
#include <fstream>
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <string>
#include <vector>
#include <unordered_map>
#include "frame_time.h"
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace forza::graphics_menu {
namespace {
std::filesystem::path pending_path;
#ifdef __APPLE__
std::filesystem::path native_config_path;
#endif
}
void Initialize(const std::filesystem::path &root) {
#ifdef __APPLE__
  native_config_path = root / "forza.toml";
#endif
  const char *path = std::getenv("FORZA_GRAPHICS_SETTINGS");
  pending_path = path && *path ? std::filesystem::path(path)
                               : root / "graphics-settings.json";
}
} // namespace forza::graphics_menu

#if FORZA_TU4
#define MenuBind sub_827D1618
#define OriginalBind __imp__sub_827D1618
#define MenuSelect sub_827EBEE0
#define OriginalSelect __imp__sub_827EBEE0
extern "C" REX_FUNC(OriginalBind);
extern "C" REX_FUNC(OriginalSelect);

namespace forza::graphics_menu {
namespace {
constexpr uint32_t kGraphics = 0x7000;
constexpr uint32_t kBenchmark = 0x7001;
uint32_t Read(uint8_t *base, uint32_t address) {
  const auto *p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 |
         p[3];
}
void Write(uint8_t *base, uint32_t address, uint32_t value) {
  auto *p = GuestPtr(base, address);
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
}

// The original guest constructors retain their own references to the text.
// Scratch storage is private to this synchronous call and never retained by a
// UI object.
class Native {
public:
  Native(PPCContext &ctx, uint8_t *base) : ctx_(ctx), base_(base) {
    scratch_ = REX_KERNEL_MEMORY()->SystemHeapAlloc(1024);
  }
  ~Native() {
    if (scratch_)
      REX_KERNEL_MEMORY()->SystemHeapFree(scratch_);
  }
  explicit operator bool() const { return scratch_ != 0; }
  uint32_t Call(uint32_t address, std::initializer_list<uint32_t> args) {
    unsigned i = 3;
    // GPRs are contiguous in PPCContext, but use named registers for ABI
    // stability.
    for (uint32_t value : args) {
      switch (i++) {
      case 3:
        ctx_.r3.u64 = value;
        break;
      case 4:
        ctx_.r4.u64 = value;
        break;
      case 5:
        ctx_.r5.u64 = value;
        break;
      case 6:
        ctx_.r6.u64 = value;
        break;
      case 7:
        ctx_.r7.u64 = value;
        break;
      case 8:
        ctx_.r8.u64 = value;
        break;
      case 9:
        ctx_.r9.u64 = value;
        break;
      }
    }
    rex::runtime::ResolveIndirectFunction(address)(ctx_, base_);
    return ctx_.r3.u32;
  }
  uint32_t Text(const std::string &text) {
    if (text.size() > 240)
      return 0;
    auto *out = GuestPtr(base_, scratch_ + 32);
    for (size_t i = 0; i < text.size(); ++i) {
      out[i * 2] = 0;
      out[i * 2 + 1] = uint8_t(text[i]);
    }
    out[text.size() * 2] = out[text.size() * 2 + 1] = 0;
    uint32_t object = Call(0x824265C8, {16});
    if (!object)
      return 0;
    Write(base_, object, 0x82001674);
    Write(base_, object + 4, 0);
    Write(base_, object + 8, 0);
    Write(base_, object + 12, 0);
    Call(0x82D6DE20, {object, scratch_ + 32, uint32_t(text.size())});
    Call(0x82555B90, {scratch_, object});
    return scratch_;
  }
  void AddSource(uint32_t source, uint32_t id, const std::string &label) {
    if (uint32_t text = Text(label))
      Call(0x828F5818, {source, text, id, 1, 0});
  }
  void Add(uint32_t menu, uint32_t id, const std::string &label) {
    AddSource(menu + 0x174, id, label);
  }
  uint32_t scratch() const { return scratch_; }
  uint32_t Hash(const std::string &value) {
    std::copy(value.begin(), value.end(), GuestPtr(base_, scratch_ + 512));
    GuestPtr(base_, scratch_ + 512)[value.size()] = 0;
    return Call(0x82F354E0, {scratch_ + 512});
  }
  void Field(uint32_t screen, const std::string &name, uint32_t binding,
             const std::string &text) {
    auto id = Hash(name);
    auto bound = Call(0x82F21830, {screen, id, binding});
    REXLOG_INFO("Graphics menu: field {} bound={}", name, bound);
    if (bound)
      Binding(binding, text);
  }
  uint32_t Node(uint32_t screen, const std::string &path) {
    const auto engine = Read(base_, screen + 8);
    const auto access = Call(Read(base_, Read(base_, engine) + 0x1C), {engine});
    std::copy(path.begin(), path.end(), GuestPtr(base_, scratch_ + 512));
    GuestPtr(base_, scratch_ + 512)[path.size()] = 0;
    return Call(0x82F348B8,
                {access, Read(base_, screen + 0x58), scratch_ + 512, 0});
  }
  void Binding(uint32_t target, const std::string &label) {
    if (uint32_t text = Text(label)) {
      Call(0x82F3A728, {target, text});
      Call(0x824CEAB0, {text});
    }
  }
  uint32_t Event(uint32_t screen, uint32_t event) {
    Write(base_, scratch_, 0x5D);
    Write(base_, scratch_ + 4, 0x60);
    uint32_t found = Call(Read(base_, Read(base_, screen) + 0xA8),
                          {screen, event, scratch_, 2});
    return found ? Read(base_, found) : 0;
  }
  PPCContext &context() { return ctx_; }

private:
  PPCContext ctx_;
  uint8_t *base_;
  uint32_t scratch_ = 0;
};

struct Choice {
  std::string value, label;
};
struct Setting {
  std::string key, cvar, label, value;
  std::vector<Choice> choices;
};
uint32_t active_menu = 0;
uint32_t main_menu = 0;
bool title_layout_pending = false;
struct TitleOffset { uint32_t row, node; float spacing; };
std::vector<TitleOffset> title_offsets;
void ShiftTitleRow(uint8_t *base, uint32_t node, float dy) {
  // Anark component instances cache both their local and world transforms.
  // Updating only the serialized position leaves the rendered instance in place.
  const auto object = Read(base, node + 4);
  if (!object || Read(base, object + 4) != node) return;
  const auto owner = Read(base, object);
  auto shift = [&](uint32_t address) {
    Write(base, address, std::bit_cast<uint32_t>(
        std::bit_cast<float>(Read(base, address)) + dy));
  };
  shift(object + 0x10); // instance local translation Y
  shift(object + 0x70); // cached local matrix translation Y
  std::vector<uint32_t> pending{node};
  while (!pending.empty()) {
    const auto current = pending.back();
    pending.pop_back();
    const auto rendered = Read(base, current + 4);
    // Materials and samplers use smaller descriptors. Only transform-bearing
    // objects have this instance owner and a back reference to the scene node.
    if (rendered && Read(base, rendered) == owner &&
        Read(base, rendered + 4) == current)
      shift(rendered + 0xB0); // cached world matrix / hit-geometry translation Y
    for (auto child = Read(base, current + 0x14); child;
         child = Read(base, child + 0x10))
      pending.push_back(child);
  }
}
void RestoreTitle(uint8_t *base) {
  for (const auto &offset : title_offsets) {
    ShiftTitleRow(base, offset.node, -offset.spacing);
  }
  title_offsets.clear();
}
bool from_main_menu = false;
std::vector<Setting> settings;
std::vector<Setting> saved_settings;
std::vector<Setting> applying_settings;
std::string applying_request;
uint64_t request_id = 0;
bool save_requested = false;
size_t selected_setting = 8;
std::vector<std::pair<std::string, std::string>> preview_baseline;

bool IsLive(const Setting &setting) {
  return setting.key == "vsync" || setting.key == "anisotropic_override" ||
         setting.key == "forza_hud_edges" || setting.key == "forza_performance_overlay" ||
         setting.key == "world_shadow_filter" || setting.key == "world_ambient_occlusion" ||
         setting.key == "forza_cockpit_fov" || setting.key == "forza_chase_near_fov" ||
         setting.key == "forza_chase_far_fov" || setting.key == "forza_hood_fov" ||
         setting.key == "forza_bumper_fov" || setting.key == "forza_minimap_camera";
}

void RememberPreview() {
  preview_baseline.clear();
  for (const auto &setting : settings)
    if (IsLive(setting))
      preview_baseline.emplace_back(setting.cvar,
                                    rex::cvar::GetFlagByName(setting.cvar));
}

void RestorePreview() {
  for (const auto &[name, value] : preview_baseline) {
    if (rex::cvar::GetFlagByName(name) == value)
      continue;
    if (rex::cvar::SetFlagByName(name, value))
      REXLOG_INFO("Graphics menu: reverted live {}={}", name, value);
  }
  preview_baseline.clear();
}

bool ApplyPreview() {
  bool success = true;
  for (const auto &setting : settings) {
    if (!IsLive(setting) ||
        rex::cvar::GetFlagByName(setting.cvar) == setting.value)
      continue;
    const bool applied = rex::cvar::SetFlagByName(setting.cvar, setting.value);
    REXLOG_INFO("Graphics menu: live {}={} applied={}", setting.cvar,
                setting.value, applied);
    success &= applied;
  }
  return success;
}

constexpr size_t kAntiAliasing = 9;
constexpr size_t kShadowSoftness = 10, kAmbientOcclusion = 11;
constexpr size_t kCockpitFov = 12;
constexpr size_t kChaseNearFov = 13, kChaseFarFov = 14, kHoodFov = 15, kBumperFov = 16;
constexpr std::array<size_t, 5> kCameraFovs{kChaseNearFov, kChaseFarFov, kCockpitFov, kHoodFov, kBumperFov};
// Preset first, then image quality, frame pacing, cameras, HUD and monitoring.
constexpr size_t kMinimapOrientation = 17;
constexpr std::array<size_t, 18> kDisplayOrder{8, 0, 1, kAntiAliasing, 2, 5,
    kShadowSoftness, kAmbientOcclusion, 3, 4,
    kChaseNearFov, kChaseFarFov, kCockpitFov, kHoodFov, kBumperFov, 6, kMinimapOrientation, 7};
void AntiAliasingChoices() {
  auto &aa = settings[kAntiAliasing];
  if (settings[1].value != "off") {
    aa.choices = {{"integrated", settings[1].value == "metalfx_spatial" ?
        "Game MSAA (spatial upscale)" : "Included (upscaler)"}};
    aa.value = "integrated";
    return;
  }
  if (aa.value == "integrated") aa.value = "game";
  aa.choices = {{"game", "Game MSAA"}};
  if (rex::cvar::GetFlagByName("gpu_backend") == "vulkan") {
    aa.choices.push_back({"smaa", "SMAA 1x"});
#ifdef _WIN32
    if (std::getenv("FORZA_DLSS_RUNTIME")) aa.choices.push_back({"dlaa", "NVIDIA DLAA"});
#endif
    aa.choices.push_back({"fsr3_native", "FSR Native AA (TAA)"});
  }
}
std::string BackendMode() {
  return settings[1].value != "off" ? settings[1].value :
      settings[kAntiAliasing].value == "game" ? "off" : settings[kAntiAliasing].value;
}
void Snapshot() {
  settings = {
      {"output",
       "resolution",
       "Output resolution",
       "",
       {{"720p", "1280 x 720"},
        {"1080p", "1920 x 1080"},
        {"1440p", "2560 x 1440"},
        {"4k", "3840 x 2160"}}},
      {"upscaler", "fh1_upscaler", "Upscaling", "", {{"off", "Off"}}},
      {"resolution_scale",
       "resolution_scale",
       "MSAA scene resolution",
       "",
       {{"1", "1280 x 720 (1x)"},
        {"2", "2560 x 1440 (2x)"},
        {"3", "3840 x 2160 (3x, experimental)"}}},
      {"frame_rate",
       "frame_rate",
       "Frame rate limit",
       "",
       {{"20", "20 FPS"},
        {"30", "30 FPS"},
        {"40", "40 FPS"},
        {"60", "60 FPS"},
        {"90", "90 FPS"},
        {"120", "120 FPS"},
        {"144", "144 FPS"},
        {"165", "165 FPS"},
        {"180", "180 FPS"},
        {"240", "240 FPS"}}},
      {"vsync",
       "vsync",
       "Vertical sync",
       "",
       {{"true", "On"}, {"false", "Off"}}},
      {"anisotropic_override",
       "anisotropic_override",
       "Texture filtering",
       "",
       {{"-1", "Game default"},
        {"0", "Off"},
        {"1", "1x"},
        {"2", "2x"},
        {"3", "4x"},
        {"4", "8x"},
        {"5", "16x"}}},
      {"forza_hud_edges",
       "forza_hud_edges",
       "Ultrawide HUD",
       "",
       {{"false", "Centered"}, {"true", "Screen edges"}}},
      {"forza_performance_overlay", "forza_performance_overlay", "Performance overlay", "",
       {{"off", "Off"}, {"fps", "FPS"}, {"graph", "FPS + frame-time graph"}}},
      {"preset", "", "Quality preset", "custom",
       {{"custom", "Custom"}, {"auto", "Auto (recommended)"}, {"low", "Low"},
        {"medium", "Medium"}, {"high", "High"}}},
      {"anti_aliasing", "", "Anti-aliasing", "game", {}},
      {"world_shadow_filter", "world_shadow_filter", "Shadow softness", "",
       {{"console", "Console"}, {"soft", "Soft"}, {"softer", "Softer"}}},
      {"world_ambient_occlusion", "world_ambient_occlusion", "Ambient occlusion", "",
       {{"off", "Off"}, {"low", "Low"}, {"high", "High"}}},
      {"forza_cockpit_fov", "forza_cockpit_fov", "Cockpit base FOV", "", {{"0", "Stock (62 degrees)"}}},
      {"forza_chase_near_fov", "forza_chase_near_fov", "Chase near base FOV", "", {{"0", "Stock (48.5 degrees)"}}},
      {"forza_chase_far_fov", "forza_chase_far_fov", "Chase far base FOV", "", {{"0", "Stock (58 degrees)"}}},
      {"forza_hood_fov", "forza_hood_fov", "Hood base FOV", "", {{"0", "Stock (66 degrees)"}}},
      {"forza_bumper_fov", "forza_bumper_fov", "Bumper base FOV", "", {{"0", "Stock (53 degrees)"}}},
      {"forza_minimap_camera", "forza_minimap_camera", "Minimap orientation", "",
       {{"false", "Vehicle (stock)"}, {"true", "Camera"}}},
  };
  for (const auto index : kCameraFovs)
    for (int degrees = 40; degrees <= 90; ++degrees)
      settings[index].choices.push_back({std::to_string(degrees), std::to_string(degrees) + " degrees"});
  auto &modes = settings[1].choices;
#ifdef _WIN32
  if (rex::cvar::GetFlagByName("gpu_backend") == "vulkan" &&
      (rex::cvar::GetFlagByName("gpu_vendor") == "nvidia" ||
       std::getenv("FORZA_DLSS_RUNTIME"))) {
    modes.insert(modes.end(), {{"quality", "DLSS Quality"},
                               {"balanced", "DLSS Balanced"},
                               {"dlss", "DLSS Performance"}});
  }
#endif
  if (rex::cvar::GetFlagByName("gpu_backend") == "vulkan") {
#ifdef __APPLE__
    modes.insert(modes.end(), {{"metalfx_temporal", "MetalFX Temporal"},
                              {"metalfx_spatial", "MetalFX Spatial"}});
#endif
    modes.insert(modes.end(), {{"fsr3_quality", "FSR 3 Quality"},
                               {"fsr3_balanced", "FSR 3 Balanced"},
                               {"fsr3_performance", "FSR 3 Performance"}});
  }
  // The ReGPU plugin's own upscaler (Glue2GPU).
  if (rex::cvar::GetFlagByName("gpu_plugin") == "regpu")
    modes.insert(modes.end(), {{"fsr1_quality", "FSR 1 Quality"},
                               {"fsr1_balanced", "FSR 1 Balanced"},
                               {"fsr1_performance", "FSR 1 Performance"}});
  for (auto &setting : settings) {
    if (setting.key == "preset" || setting.key == "anti_aliasing") continue;
    setting.value = rex::cvar::GetFlagByName(setting.cvar);
#ifdef __APPLE__
    if (setting.key == "output" && setting.value.empty()) {
      const auto mode = rex::cvar::GetFlagByName("fh1_upscaler");
      const bool split = mode != "off" ||
          rex::cvar::GetFlagByName("fh1_scene_720p_native_hud") == "true";
      const auto extent = split ? rex::cvar::GetFlagByName("fh1_output_height") :
          rex::cvar::GetFlagByName("resolution_scale") == "3" ? "2160" :
          rex::cvar::GetFlagByName("resolution_scale") == "2" ? "1440" : "720";
      setting.value = extent == "2160" ? "4k" : extent == "1440" ? "1440p" :
          extent == "1080" ? "1080p" : "720p";
    }
#endif
    if (setting.key == "output") setting.value = CurrentOutput(setting.value);
    if (setting.value.empty())
      setting.value = setting.choices.front().value;
  }
  // A value the screen does not offer would be saved back as it is; show and keep the
  // game's look (Console, Off).
  for (const size_t world : {kShadowSoftness, kAmbientOcclusion}) {
    auto &setting = settings[world];
    if (std::none_of(setting.choices.begin(), setting.choices.end(),
                     [&](const Choice &choice) { return choice.value == setting.value; }))
      setting.value = setting.choices.front().value;
  }
  // Never let an unknown/current launcher mode silently select spinner index
  // zero (1080p). Native ultrawide owns drawing and output until restart.
  auto &output = settings[0];
  for (const auto index : kCameraFovs) {
    auto &fov = settings[index];
    if (std::none_of(fov.choices.begin(), fov.choices.end(),
                     [&](const Choice &choice) { return choice.value == fov.value; }))
      fov.value = "0";
  }
  if (const auto extent = forza::ultrawide::OutputSize(); extent.width) {
    output.choices = {{output.value, std::to_string(extent.width) + " x " +
        std::to_string(extent.height) + " (window)"}};
    const auto drawing = forza::ultrawide::DrawingSize();
    settings[2].choices = {{settings[2].value, std::to_string(drawing.width) +
        " x " + std::to_string(drawing.height) + " (automatic)"}};
  } else if (std::none_of(output.choices.begin(), output.choices.end(),
                         [&](const Choice &choice) { return choice.value == output.value; })) {
    output.choices.insert(output.choices.begin(),
                          {output.value, OutputLabel(output.value) + " (current)"});
  }
  if (rex::cvar::GetFlagByName("fh1_scene_720p_native_hud") == "true")
    settings[2].value = rex::cvar::GetFlagByName("fh1_native_scene_scale");
  if (rex::cvar::GetFlagByName("present_effect") == "fsr3")
    settings[1].value = "fsr3_" + rex::cvar::GetFlagByName("fsr3_mode");
  const auto combined_mode = settings[1].value;
  if (combined_mode == "smaa" || combined_mode == "dlaa" || combined_mode == "fsr3_native") {
    settings[1].value = "off";
    settings[kAntiAliasing].value = combined_mode;
  }
  if (!saved_settings.empty()) {
    for (size_t i = 0; i < settings.size(); ++i)
      if (!IsLive(settings[i]) && settings[i].key != "preset")
        settings[i].value = saved_settings[i].value;
  }
  AntiAliasingChoices();
  RememberPreview();
  saved_settings = settings;
  save_requested = false;
}

bool Save() {
  std::error_code ec;
  std::filesystem::create_directories(pending_path.parent_path(), ec);
  if (ec)
    return false;
  auto temp = pending_path;
  temp += ".tmp";
  std::ofstream out(temp, std::ios::binary | std::ios::trunc);
  out << "{\n  \"version\": 1,\n  \"settings\": {\n";
  bool first = true;
  for (const auto &s : settings) {
    if (s.key == "preset") continue;
    if (s.key == "output" && s.value != "720p" && !LiveOutputSupported(s.value)) continue;
    if (forza::ultrawide::OutputSize().width &&
        (s.key == "output" || s.key == "upscaler" ||
         s.key == "resolution_scale" || s.key == "anti_aliasing")) continue;
    if (!first) out << ",\n";
    first = false;
    out << "    \"" << s.key << "\": \"" << s.value << "\"";
  }
  out << "\n  }\n}\n";
  out.close();
  if (!out)
    return false;
  // Windows rename does not replace an existing destination. Keep the previous
  // request intact until the complete replacement has been written.
#ifdef _WIN32
  return MoveFileExW(temp.c_str(), pending_path.c_str(),
                     MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
#else
  std::filesystem::rename(temp, pending_path, ec);
  return !ec;
#endif
}

#ifdef __APPLE__
bool SaveNativeConfig() {
  std::ifstream input(native_config_path, std::ios::binary);
  std::error_code ec;
  if (!input && std::filesystem::exists(native_config_path, ec)) return false;
  const std::string original((std::istreambuf_iterator<char>(input)), {});
  std::vector<std::pair<std::string, std::string>> updates;
  for (const char* name : {"resolution", "frame_rate", "video_mode_refresh_rate",
      "fh1_upscaler", "fh1_output_width", "fh1_output_height", "resolution_scale",
      "draw_resolution_scale_x", "draw_resolution_scale_y", "fh1_scene_720p_native_hud",
      "fh1_native_scene_scale", "swap_post_effect", "anisotropic_override", "vsync",
      "forza_hud_edges", "forza_performance_overlay", "world_shadow_filter", "world_ambient_occlusion",
      "forza_cockpit_fov", "forza_chase_near_fov", "forza_chase_far_fov", "forza_hood_fov", "forza_bumper_fov",
      "forza_minimap_camera"}) {
    auto value = rex::cvar::GetFlagByName(name);
    if (value.empty()) continue;
    const std::string key(name);
    if (key == "resolution" || key == "fh1_upscaler" || key == "swap_post_effect" ||
        key == "forza_performance_overlay" || key == "world_shadow_filter" ||
        key == "world_ambient_occlusion")
      value = "\"" + value + "\"";
    updates.emplace_back(key, value);
  }
  auto temporary = native_config_path;
  temporary += ".graphics.tmp";
  std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
  output << RewriteGraphicsConfig(original, updates);
  output.close();
  if (!output) return false;
  std::filesystem::rename(temporary, native_config_path, ec);
  return !ec;
}
#endif

bool CommitSettings() {
  if (!ApplyPreview()) return false;
  const bool rate_changed = rex::cvar::GetFlagByName("frame_rate") != settings[3].value;
  rex::cvar::SetFlagByName("frame_rate", settings[3].value);
  rex::cvar::SetFlagByName("video_mode_refresh_rate", std::to_string(frame_time::VblankHz()));
  if (rate_changed) frame_time::ResetRenderTiming();
  if (!forza::ultrawide::OutputSize().width)
    rex::cvar::SetFlagByName("resolution", settings[0].value);
  bool saved = Save();
#ifdef __APPLE__
  saved = saved && SaveNativeConfig();
#endif
  saved_settings = settings;
  RememberPreview();
  return saved;
}
} // namespace
} // namespace forza::graphics_menu

namespace forza::graphics_menu {
namespace {
// Online photo (#576, src/hooks/online_photo.cpp): the builder's Photo Mode row (action 11)
// in an online session, in the place the builder gives it offline. The stock list and the
// native pause both read the row; selecting it runs the game's own action 11.
void AddOnlinePhoto(PPCContext &ctx, uint8_t *base, uint32_t menu) {
  if (!forza::online_photo::WantsPauseRow(ctx, base, menu)) return;
  const uint32_t rows = menu + 0x174 + 0x28;
  auto ids = [&] {
    std::vector<uint32_t> found;
    const uint32_t begin = Read(base, rows), end = Read(base, rows + 4);
    if (!begin || end < begin || end - begin > 1024 || (end - begin) % 4) return found;
    for (uint32_t address = begin; address < end; address += 4) {
      const uint32_t row = Read(base, address);
      found.push_back(row ? Read(base, row + 8) : 0);
    }
    return found;
  };
  const auto before = ids();
  if (std::find(before.begin(), before.end(), 11u) != before.end()) return;
  Native ui(ctx, base);
  if (!ui) return;
  ui.Add(menu, 11, "ONLINE PHOTO");
  const auto after = ids();
  if (after.size() != before.size() + 1 || after.back() != 11) return;
  const uint32_t begin = Read(base, rows);
  const size_t at = forza::online_photo::RowPosition(before);
  const uint32_t added = Read(base, begin + uint32_t(before.size()) * 4);
  for (size_t i = before.size(); i > at; --i)
    Write(base, begin + uint32_t(i) * 4, Read(base, begin + uint32_t(i - 1) * 4));
  Write(base, begin + uint32_t(at) * 4, added);
  REXLOG_INFO("Online photo: pause menu entry added (row {} of {})", at + 1, after.size());
}
} // namespace
} // namespace forza::graphics_menu

extern "C" REX_FUNC(MenuBind) {
  using namespace forza::graphics_menu;
  active_menu = ctx.r3.u32;
  from_main_menu = false;
  AddOnlinePhoto(ctx, base, active_menu);
  if(forza::native_pause_scene::Owns(active_menu))return;
  Native ui(ctx, base);
  if (ui) {
    const bool tuning = forza::car_tuning_menu::Available(ctx, base, active_menu);
    if (tuning)
      ui.Add(active_menu, forza::car_tuning_menu::kMenuAction, "CAR TUNING");
    ui.Add(active_menu, kGraphics, "GRAPHICS");
    // The TU4 data source keeps display order in a vector of row pointers at
    // +0x28, independently of its ID lookup. Move our appended rows above Quit
    // before the original bind publishes the list to the native component.
    const uint32_t rows = active_menu + 0x174 + 0x28;
    const uint32_t begin = Read(base, rows);
    const uint32_t end = Read(base, rows + 4);
    const uint32_t added = tuning ? 2 : 1;
    if (begin && end >= begin && end - begin >= (added + 1) * 4 &&
        (end - begin) % 4 == 0) {
      const uint32_t graphics = Read(base, end - 4);
      const uint32_t previous_address = end - (added + 1) * 4;
      const uint32_t previous = Read(base, previous_address);
      if (graphics && Read(base, graphics + 8) == kGraphics) {
        for (uint32_t address = previous_address; address < end - 4; address += 4)
          Write(base, address, Read(base, address + 4));
        Write(base, end - 4, previous);
      }
    }
    REXLOG_INFO("Graphics menu: native pause entry added");
  }
  OriginalBind(ctx, base);
}
namespace forza::graphics_menu {
namespace {
bool opening_graphics = false;
uint32_t graphics_screen = 0;
std::vector<uint32_t> native_rows;
std::vector<uint32_t> retired_choices;
uint32_t empty_pause_source = 0;
void PauseRows(Native &ui, uint8_t *base, bool show) {
  if(forza::native_pause_scene::Page(ui.context(),base,active_menu,show))return;
  auto component = Read(base, active_menu + 0x170);
  const auto source = from_main_menu ? Read(base, active_menu + 0x174) : active_menu + 0x174;
  if (!show && !empty_pause_source) {
    empty_pause_source = ui.Call(0x824265C8, {0x38});
    if (!empty_pause_source)
      return;
    ui.Call(0x828EED20, {empty_pause_source});
  }
  // Graphics is created asynchronously. Keep the title's shifted transforms
  // while its rows are still visible, including their unfocus animations.
  // Restore only when the new page is ready to hide them, before rebinding
  // the component can retire or reuse those row nodes.
  if (!show && from_main_menu) RestoreTitle(base);
  ui.Call(0x828B82C8,
          {component, show ? source : empty_pause_source});
  ui.Call(Read(base, Read(base, component + 0x6C) + 0x28), {component + 0x6C});
  if (show && empty_pause_source) {
    ui.Call(Read(base, Read(base, empty_pause_source)),
            {empty_pause_source, 1});
    empty_pause_source = 0;
    if (!from_main_menu) ui.Call(0x827D6348, {active_menu});
    else title_layout_pending = true;
  }
}
std::string Help(size_t i) {
  if (i == kMinimapOrientation)
    return "Vehicle keeps the stock minimap. Camera rotates it with where you look. TU4 desktop gameplay only; the full map stays unchanged. Save keeps the choice; Back discards.";
  if (std::find(kCameraFovs.begin(), kCameraFovs.end(), i) != kCameraFovs.end())
    return "Base vertical FOV: Stock or 40-90 degrees. Speed can widen it; transitions blend changes. Off with VR head tracking. Save applies; Back discards.";
  if ((i <= 2 || i == 8 || i == kAntiAliasing) && forza::ultrawide::OutputSize().width) {
    const auto drawing = forza::ultrawide::DrawingSize();
    return "Ultrawide follows the window. Drawing: " + std::to_string(drawing.width) +
        " x " + std::to_string(drawing.height) +
        ". Scaled to the window; higher drawing sizes use more GPU work. Change display settings in the launcher and restart.";
  }
  if ((i == 1 || i == 2 || i == 8 || i == kAntiAliasing) &&
      !LiveOutputSupported(settings[0].value))
    return "This launcher resolution is active. Live rendering changes require 1080p, 1440p or 4K. Use the launcher for other modes.";
  if (i == 1 || i == 2 || i == kAntiAliasing) {
    const auto mode = BackendMode();
    const auto output = settings[0].value == "4k" ? "3840 x 2160" :
        settings[0].value == "720p" ? "1280 x 720" :
        settings[0].value == "1080p" ? "1920 x 1080" : "2560 x 1440";
    std::string scene;
    if (mode == "off") {
      const auto scale = std::stoi(settings[2].value);
      scene = "Selected MSAA scene: " + std::to_string(1280 * scale) + " x " +
          std::to_string(720 * scale) + ".";
    } else if (mode == "smaa" || mode == "dlaa" || mode == "fsr3_native") {
      scene = std::string("Selected native AA scene: ") + output + ". MSAA scene resolution is unused.";
    } else {
      scene = "Upscaling uses a lower-resolution scene. MSAA scene resolution is unused.";
    }
    return scene + " Output / HUD: " + output + ". Save applies; Back discards.";
  }
  constexpr const char *help[] = {
      "Output and HUD resolution. Save applies changes; Back discards unsaved edits.",
      "Reconstruct a lower-resolution scene with DLSS or FSR. Anti-aliasing is integrated while upscaling is on.",
      "Used only with Game MSAA. SMAA, DLAA and FSR Native AA render at output resolution. Upscalers select a lower input resolution.",
      "Maximum frame rate. Save applies the limit without restarting.",
      "Synchronize presentation with the display. Save applies this change.",
      "Improve texture detail on roads and distant surfaces. Save applies this change.",
      "Move ultrawide HUD anchors to the center or edges. Save applies this change when HUD layout is enabled.",
      "Show measured game FPS, optionally with recent frame times in milliseconds. Save applies this change.",
      "Adjust reconstruction and filtering together. Auto uses available features and output size, not a benchmark. HUD and FPS limit stay unchanged.",
      "Game default preserves guest MSAA. SMAA is spatial. DLAA and FSR Native AA are temporal at output resolution. Turn off upscaling to select a separate AA method.",
      "Softer, smoother edges on the world's shadows: Soft filters them over a 1.5x wider area, Softer 2x with a wider blur (a little GPU time). Console is the game's filter. Save applies this change.",
      "Darker sunlight where the world's surfaces meet: under cars, along rails and curbs, at the foot of walls and in creases of the ground. Low or High strength (about 0.5 ms per frame at 2560x1440). Off is the game's lighting. Save applies this change."};
  return i < std::size(help) ? help[i] : "Save applies graphics changes without restarting.";
}
uint32_t MakeChoices(Native &ui, uint8_t *base, size_t i) {
  auto &setting = settings[i];
  const auto choices = ui.Call(0x824265C8, {0x28});
  if (!choices) return 0;
  ui.Call(0x828DD2A8, {choices, uint32_t(setting.choices.size())});
  for (size_t j = 0; j < setting.choices.size(); ++j) {
    const auto text = ui.Text(setting.choices[j].label);
    ui.context().f1.f64 = 0;
    ui.Call(0x828DDCA8, {choices, uint32_t(j), text, 0, 0, 0, 1});
    ui.Call(0x824CEAB0, {text});
  }
  return choices;
}
bool Relevant(size_t i) {
  if ((i == 0 || i == 1 || i == 2 || i == 8 || i == kAntiAliasing) &&
      forza::ultrawide::OutputSize().width) return false;
#ifndef __APPLE__
  if ((i == 1 || i == 2 || i == 8 || i == kAntiAliasing) &&
      !LiveOutputSupported(settings[0].value)) return false;
#endif
  if (i == 2) return BackendMode() == "off";
  if (i == kAntiAliasing) return settings[1].value == "off";
  // The occlusion is a pass of the Vulkan renderer (SDK patch 0350).
  if (i == kAmbientOcclusion) return rex::cvar::GetFlagByName("gpu_backend") != "d3d12";
  if (i == 6) return rex::cvar::GetFlagByName("forza_ultrawide") == "true" ||
                     rex::cvar::GetFlagByName("forza_hud_layout") == "true";
  return true;
}
bool UpdateRelevance(uint8_t *base) {
  bool changed = false;
  for (size_t i = 0; i < native_rows.size(); ++i) {
    // TU4 spinner item +0x18 is the disabled byte, read by its native
    // IsEnabled virtual (8271D4E0). Use the game's own disabled styling.
    auto *disabled = GuestPtr(base, native_rows[i] + 0x18);
    const uint8_t value = Relevant(i) ? 0 : 1;
    changed |= *disabled != value;
    *disabled = value;
  }
  return changed;
}
struct TextTint { uint32_t control, original; };
std::vector<TextTint> text_tints;
std::unordered_map<uint32_t, uint32_t> text_controls;
void RefreshRelevance(PPCContext &, uint8_t *base) {
  if (!graphics_screen) return;
  const auto component = Read(base, graphics_screen + 0x16C);
  const auto begin = component ? Read(base, component + 0x130) : 0;
  const auto end = component ? Read(base, component + 0x134) : 0;
  if (!begin || end < begin || end - begin != settings.size() * 4) return;
  for (size_t index = 0; index < kDisplayOrder.size(); ++index) {
    const auto row = Read(base, begin + uint32_t(index) * 4);
    const auto node = row ? Read(base, row + 0x2C) : 0;
    if (!node) continue;
    const bool enabled = Relevant(kDisplayOrder[index]);
    std::vector<uint32_t> pending{node};
    while (!pending.empty()) {
      const auto current = pending.back(); pending.pop_back();
      const auto binding = text_controls.find(current);
      if (binding != text_controls.end()) {
        const auto control = binding->second;
        if (control >= 0x10000 && control < 0x80000000) {
          auto entry = std::find_if(text_tints.begin(), text_tints.end(),
              [control](const TextTint &tint) { return tint.control == control; });
          const auto color = Read(base, control + 0xBC);
          if (entry == text_tints.end() && !enabled) {
            text_tints.push_back({control, color}); entry = std::prev(text_tints.end());
          }
          if (entry != text_tints.end()) {
            if (enabled) { Write(base, control + 0xBC, entry->original); text_tints.erase(entry); }
            else {
              uint32_t grey = entry->original & 0xFF000000u;
              for (unsigned shift : {0u, 8u, 16u})
                grey |= uint32_t(float((entry->original >> shift) & 255u) * 0.45f) << shift;
              Write(base, control + 0xBC, grey);
            }
          }
        }
      }
      for (auto child = Read(base, current + 0x14); child; child = Read(base, child + 0x10))
        pending.push_back(child);
    }
  }
}
void SyncSettings(Native &ui, uint8_t *base) {
  const auto previous_output = settings[0].value;
  const auto previous_mode = settings[1].value;
  const auto previous_scale = settings[2].value;
  const auto previous_filtering = settings[5].value;
  const auto previous_preset = settings[8].value;
  const auto previous_aa = settings[kAntiAliasing].value;
  for (size_t i = 0; i < native_rows.size(); ++i) {
    const auto choice = Read(base, native_rows[i] + 8);
    if (Relevant(i) && choice < settings[i].choices.size())
      settings[i].value = settings[i].choices[choice].value;
  }
  const auto old_aa_choices = settings[kAntiAliasing].choices;
  bool refresh = false;
  if (settings[8].value != previous_preset && settings[8].value != "custom") {
    const auto &modes = settings[1].choices;
    auto available = [&](const char *mode) {
      return std::any_of(modes.begin(), modes.end(), [&](const Choice &choice) {
        return choice.value == mode;
      });
    };
    const auto preset = ChoosePreset(settings[8].value, settings[0].value,
                                     available("quality"), available("fsr3_quality"));
    settings[1].value = preset.mode;
    settings[kAntiAliasing].value = "game";
    settings[2].value = std::to_string(preset.scene_scale);
    settings[5].value = std::to_string(preset.filtering);
    refresh = true;
  } else if (settings[0].value != previous_output || settings[1].value != previous_mode || settings[2].value != previous_scale ||
             settings[5].value != previous_filtering || settings[kAntiAliasing].value != previous_aa) {
    settings[8].value = "custom";
    refresh = true;
  }
  AntiAliasingChoices();
  const bool aa_changed = old_aa_choices.size() != settings[kAntiAliasing].choices.size() ||
      old_aa_choices.front().value != settings[kAntiAliasing].choices.front().value ||
      old_aa_choices.front().label != settings[kAntiAliasing].choices.front().label;
  if (aa_changed) {
    if (const auto choices = MakeChoices(ui, base, kAntiAliasing)) {
      retired_choices.push_back(Read(base, native_rows[kAntiAliasing] + 4));
      Write(base, native_rows[kAntiAliasing] + 4, choices);
      refresh = true;
    }
  }
  refresh |= UpdateRelevance(base);
  if (refresh) {
    for (size_t i : {size_t(1), size_t(2), size_t(5), size_t(8), kAntiAliasing}) {
      for (size_t j = 0; j < settings[i].choices.size(); ++j)
        if (settings[i].choices[j].value == settings[i].value)
          Write(base, native_rows[i] + 8, uint32_t(j));
    }
    const auto component = Read(base, graphics_screen + 0x16C);
    ui.Call(Read(base, Read(base, component + 0x6C) + 0x28), {component + 0x6C});
  }
}
void Footer(Native &ui, uint8_t *base) {
  const auto footer = ui.Call(0x82746638, {graphics_screen});
  if (!footer) return;
  ui.Call(0x8289A908, {footer});
  for (auto [index, label] : {std::pair{0u, "SAVE"}, std::pair{1u, "BACK"}}) {
    const auto text = ui.Text(label);
    ui.Call(0x8289E2B0, {footer, index, text});
    ui.Call(0x824CEAB0, {text});
  }
  ui.Call(Read(base, Read(base, footer + 0x6C) + 0x28), {footer + 0x6C});
}
void RefreshRows(Native &ui, uint8_t *base) {
  UpdateRelevance(base);
  for (size_t i = 0; i < settings.size(); ++i) {
    if (i == kAntiAliasing) {
      const auto choices = MakeChoices(ui, base, i);
      if (choices) {
        retired_choices.push_back(Read(base, native_rows[i] + 4));
        Write(base, native_rows[i] + 4, choices);
      }
    }
    for (size_t j = 0; j < settings[i].choices.size(); ++j)
      if (settings[i].choices[j].value == settings[i].value)
        Write(base, native_rows[i] + 8, uint32_t(j));
  }
  const auto component = Read(base, graphics_screen + 0x16C);
  ui.Call(Read(base, Read(base, component + 0x6C) + 0x28), {component + 0x6C});
}

} // namespace
} // namespace forza::graphics_menu
extern "C" REX_FUNC(__imp__sub_82F2A8D0);
extern "C" REX_FUNC(sub_82F2A8D0) {
  using namespace forza::graphics_menu;
  const auto node = ctx.r5.u32;
  const auto control = graphics_screen ? Read(base, ctx.r4.u32) : 0;
  __imp__sub_82F2A8D0(ctx, base);
  // This callback fills Horizon's separate text cache, not the Anark actor
  // color. Record the live binding while this graphics view owns it.
  if (graphics_screen && control) text_controls[node] = control;
}
extern "C" REX_FUNC(__imp__sub_827303E8);
extern "C" REX_FUNC(__imp__sub_82739190);
extern "C" REX_FUNC(__imp__sub_827142C0);
extern "C" REX_FUNC(__imp__sub_82736B08);

extern "C" REX_FUNC(sub_827303E8) {
  if (forza::custom_race::TryBind(ctx, base)) return;
  if (forza::car_tuning_menu::TryBind(ctx, base)) return;
  using namespace forza::graphics_menu;
  if (!opening_graphics) {
    __imp__sub_827303E8(ctx, base);
    return;
  }
  opening_graphics = false;
  graphics_screen = ctx.r3.u32;
  Native ui(ctx, base);
  if (!ui)
    return;
  uint32_t source = ui.Call(0x824265C8, {0x28});
  ui.Call(0x8267A4B0, {source});
  Write(base, graphics_screen + 0x1A0, source);
  Write(base, Read(base, graphics_screen + 0xA4) + 0x1C, source);
  native_rows.assign(settings.size(), 0);
  for (size_t i : kDisplayOrder) {
    auto &setting = settings[i];
    uint32_t row = ui.Call(0x824265C8, {0x1C});
    for (unsigned offset = 0; offset < 0x1C; offset += 4)
      Write(base, row + offset, 0);
    Write(base, row, 0x82049434);
    auto label = ui.Text(setting.label);
    ui.Call(0x82471820, {row + 0x10, label});
    ui.Call(0x824CEAB0, {label});
    Write(base, row + 0xC, uint32_t(i));
    const uint32_t choices = MakeChoices(ui, base, i);
    Write(base, row + 4, choices);
    for (size_t j = 0; j < setting.choices.size(); ++j)
      if (setting.choices[j].value == setting.value) Write(base, row + 8, uint32_t(j));
    Write(base, ui.scratch(), row);
    ui.Call(0x826D9C80, {source + 0x18, ui.scratch()});
    native_rows[i] = row;
  }
  UpdateRelevance(base);
  ui.Call(0x827FBDD0, {graphics_screen});
  ui.Call(0x82F1EC20, {graphics_screen, ui.Hash("DIFFICULTY")});
  ui.Field(graphics_screen, "TEXT_SCREEN_NAME", graphics_screen + 0x1F4,
           "GRAPHICS SETTINGS");
  ui.Field(graphics_screen, "TEXT_EARNINGS", graphics_screen + 0x1E8,
           "NO RESTART NEEDED");
  ui.Field(graphics_screen, "TEXT_TITLE", graphics_screen + 0x170,
           "GRAPHICS SETTINGS");
  ui.Field(graphics_screen, "TEXT_DESCRIPTION", graphics_screen + 0x17C,
           Help(kDisplayOrder.front()));
  // Difficulty's earnings strip has no meaning for graphics. Move the entire
  // instance, including its tape and text, outside this screen's viewport.
  for (const char *path : {"parent.Layer.Group_BLADE.Group_EARNINGS",
                           "parent.Layer.Group_BLADE.Group_MENU.BONUS_BAR"})
    if (const auto node = ui.Node(graphics_screen, path))
      ShiftTitleRow(base, node, 10000.0f);
  PauseRows(ui, base, false);
  Footer(ui, base);
  REXLOG_INFO("Graphics menu: native fixed settings page built ({} rows)",
              native_rows.size());
}
extern "C" REX_FUNC(sub_82739190) {
  if (forza::custom_race::TryNotify(ctx, base)) return;
  if (forza::car_tuning_menu::TryNotify(ctx, base)) return;
  using namespace forza::graphics_menu;
  if (ctx.r3.u32 != graphics_screen) {
    __imp__sub_82739190(ctx, base);
    return;
  }
  if (ctx.r4.u32 != 1)
    return;
  Native ui(ctx, base);
  if (!ui)
    return;
  uint32_t component = Read(base, graphics_screen + 0x16C);
  if (ctx.r5.u32 == component + 8) {
    selected_setting = ctx.r6.u32 < kDisplayOrder.size() ? kDisplayOrder[ctx.r6.u32] : 0;
    ui.Binding(graphics_screen + 0x17C, Help(selected_setting));
  } else {
    SyncSettings(ui, base);
    ui.Binding(graphics_screen + 0x17C, Help(selected_setting));
  }
}
extern "C" REX_FUNC(sub_827142C0) {
  if (forza::custom_race::TryDestroy(ctx, base)) return;
  if (forza::car_tuning_menu::TryDestroy(ctx, base)) return;
  using namespace forza::graphics_menu;
  if (ctx.r3.u32 != graphics_screen) {
    __imp__sub_827142C0(ctx, base);
    forza::native_pause_scene::Page(ctx,base,active_menu,true);
    return;
  }
  Native ui(ctx, base);
  if (!ui)
    return;
  if (empty_pause_source || forza::native_pause_scene::Owns(active_menu))
    PauseRows(ui, base, true);
  RestorePreview();
  for (uint32_t row : native_rows) {
    auto choices = Read(base, row + 4);
    ui.Call(Read(base, Read(base, choices)), {choices, 1});
    ui.Call(Read(base, Read(base, row)), {row, 1});
  }
  for (const auto choices : retired_choices)
    ui.Call(Read(base, Read(base, choices)), {choices, 1});
  retired_choices.clear();
  auto source = Read(base, graphics_screen + 0x1A0);
  ui.Call(Read(base, Read(base, source)), {source, 1});
  Write(base, graphics_screen + 0x1A0, 0);
  native_rows.clear();
  text_tints.clear();
  text_controls.clear();
  graphics_screen = 0;
  save_requested = false;
}
extern "C" REX_FUNC(sub_82736B08) {
  if (forza::custom_race::TryInput(ctx, base)) return;
  if (forza::car_tuning_menu::TryInput(ctx, base)) return;
  using namespace forza::graphics_menu;
  if (ctx.r3.u32 != graphics_screen) {
    __imp__sub_82736B08(ctx, base);
    return;
  }
  Native ui(ctx, base);
  if (!ui)
    return;
  auto event = ui.Event(graphics_screen, ctx.r4.u32);
  if (!applying_request.empty() || save_requested) return;
  if (event == 0x60) {
    settings = saved_settings;
    PauseRows(ui, base, true);
    ui.Call(0x827F20F0, {graphics_screen});
    return;
  }
  if (event == 0x5D) {
    SyncSettings(ui, base);
    save_requested = true;
    ui.Binding(graphics_screen + 0x17C, "Applying and saving graphics settings...");
    return;
  }
  const auto component = Read(base, graphics_screen + 0x16C);
  ui.Call(Read(base, Read(base, component) + 0x34), {component, ctx.r4.u32});
  SyncSettings(ui, base);
  ui.Binding(graphics_screen + 0x17C, Help(selected_setting));
}
namespace forza::graphics_menu {
namespace {
void OpenGraphics(PPCContext &ctx, uint8_t *base) {
  Snapshot();
  selected_setting = kDisplayOrder.front();
  Native ui(ctx, base);
  if (!ui) return;
  opening_graphics = true;
  const auto params = ui.Call(0x824265C8, {0x88});
  if (!params) { opening_graphics = false; return; }
  ui.Call(0x825A5640, {params, 0});
  const auto title = ui.Text("GRAPHICS SETTINGS");
  ui.Call(0x82471820, {params + 0xC, title});
  ui.Call(0x824CEAB0, {title});
  Write(base, ui.scratch(), params);
  ui.Call(0x82675B00, {active_menu, 5, ui.scratch(), 0});
  REXLOG_INFO("Graphics menu: opening native graphics settings from {}",
              from_main_menu ? "main menu" : "pause menu");
}
}
}
extern "C" REX_FUNC(MenuSelect) {
  using namespace forza::graphics_menu;
  if (ctx.r3.u32 == active_menu && ctx.r4.u32 == 2 &&
      ctx.r6.u32 == forza::car_tuning_menu::kMenuAction) {
    forza::car_tuning_menu::Open(ctx, base, active_menu);
    return;
  }
  if (ctx.r3.u32 == active_menu && ctx.r4.u32 == 2 && ctx.r6.u32 == kGraphics) {
    from_main_menu = false;
    OpenGraphics(ctx, base);
    return;
  }
  OriginalSelect(ctx, base);
}

extern "C" REX_FUNC(__imp__sub_8283CB68);
extern "C" REX_FUNC(__imp__sub_82836928);
// CSplash's root choice list (Single Player / Multiplayer / Horizon Rally).
// The older CMainMenu and Kinect menu classes are not used in this flow.
extern "C" REX_FUNC(sub_8283CB68) {
  using namespace forza::graphics_menu;
  const uint32_t menu = ctx.r3.u32;
  // A mode transition can destroy the old title scene before rebuilding this
  // list (for example, Exit Multiplayer). Its cached nodes are no longer ours
  // to restore; only the new scene may receive the layout adjustment below.
  title_layout_pending = false;
  title_offsets.clear();
  __imp__sub_8283CB68(ctx, base);
  main_menu = menu;
  forza::native_quit::Title(ctx,base,menu);
  const auto source = Read(base, menu + 0x174);
  Native ui(ctx, base);
  if (source && ui) {
    const auto component = Read(base, menu + 0x170);
    title_layout_pending = true;
    ui.AddSource(source, kGraphics, "GRAPHICS");
    ui.AddSource(source, kBenchmark, "BENCHMARK");
    forza::benchmark::TitleReady(menu, 10);
    ui.Call(Read(base, Read(base, component + 0x6C) + 0x28), {component + 0x6C});
    REXLOG_INFO("Graphics menu: native main-menu entry added");
  }
}
extern "C" REX_FUNC(sub_82836928) {
  using namespace forza::graphics_menu;
  const bool title_choice = ctx.r3.u32 == main_menu && ctx.r4.u32 == 2 &&
                            Read(base, main_menu + 0x260) == 12;
  if(title_choice)forza::native_quit::Cancel();
  if (ctx.r3.u32 == main_menu && ctx.r4.u32 == 2 && ctx.r6.u32 == kBenchmark &&
      Read(base, main_menu + 0x260) == 12) {
    forza::benchmark::Show();
    return;
  }
  if (ctx.r3.u32 == main_menu && ctx.r4.u32 == 2 && ctx.r6.u32 == kGraphics &&
      Read(base, main_menu + 0x260) == 12) {
    title_layout_pending = false;
    active_menu = main_menu;
    from_main_menu = true;
    OpenGraphics(ctx, base);
    return;
  }
  __imp__sub_82836928(ctx, base);
  if (title_choice && Read(base, main_menu + 0x260) != 12) {
    // A stock choice can show a modal dialog and keep the title list alive.
    // In that case its animated selected label must keep the same shifted
    // transform as the static rows. Once the native menu really leaves, its
    // scene owns those transforms; discard our references without touching it.
    title_layout_pending = false;
    title_offsets.clear();
  }
}

namespace forza::graphics_menu {
void Tick(PPCContext& ctx, uint8_t* base) {
  forza::car_tuning_menu::Tick(ctx, base);
  forza::custom_race::Tick(ctx, base);
  UpdatePerformanceOverlay();
  RefreshRelevance(ctx, base);
  if (title_layout_pending && main_menu && Read(base, main_menu + 0x260) == 12) {
    const auto component = Read(base, main_menu + 0x170);
    const auto list = Read(base, component + 0x140);
    const auto rows = Read(base, list + 0x130);
    const auto end = Read(base, list + 0x134);
    if (rows && end >= rows + 8 && end <= rows + 32 * 4) {
      const auto first = Read(base, rows), second = Read(base, rows + 4);
      if (first && second) {
        // The scrolling list positions component instances at runtime. Their
        // instance transform moves text, tape and hit geometry together.
        // Use rendered row spacing, not a pixel constant, so the offset also
        // scales with native HUD output.
        // A row listed before its scene node exists has none (+0x2C is 0): reading
        // through it would read guest address 4. Tick runs every frame; wait for it.
        const auto first_node = Read(base, first + 0x2C);
        const auto second_node = Read(base, second + 0x2C);
        const auto first_object = first_node ? Read(base, first_node + 4) : 0;
        const auto second_object = second_node ? Read(base, second_node + 4) : 0;
        const float spacing = first_object && second_object
            ? std::bit_cast<float>(Read(base, first_object + 0xB0)) -
              std::bit_cast<float>(Read(base, second_object + 0xB0)) : 0;
        if (spacing > 0 && spacing < 100) {
          RestoreTitle(base);
          Native ui(ctx, base);
          const auto source = Read(base, main_menu + 0x174);
          if (!ui || !source) return;
          const auto count = ui.Call(Read(base, Read(base, source) + 4), {source, 0});
          if (!count || count > 32) return;
          const auto visible_end = std::min(end, rows + count * 4);
          for (auto item = rows; item < visible_end; item += 4) {
            const auto row = Read(base, item);
            const auto node = row ? Read(base, row + 0x2C) : 0;
            if (!node) continue;
            title_offsets.push_back({row, node, spacing * 2});
            ShiftTitleRow(base, node, spacing * 2);
          }
          // Refresh each native visual state so static, unfocused tape/text
          // geometry is rebuilt along with the animated selected item.
          for (size_t i = 0; i < title_offsets.size(); ++i) {
            const auto row = title_offsets[i].row;
            const auto table = Read(base, row);
            ui.Call(Read(base, table + 0x80), {row, 1});
            ui.Call(Read(base, table + 0x88), {row, 1});
            if (i == Read(base, list + 0x15C))
              ui.Call(Read(base, table + 0x80), {row, 1});
          }
          title_layout_pending = false;
          REXLOG_INFO("Graphics menu: title choices raised by {} units", spacing * 2);
        }
      }
    }
  }
  if (!graphics_screen || (!save_requested && applying_request.empty())) return;
  Native ui(ctx, base);
  if (!ui) return;
  std::string message;
  bool completed = false;
  bool success = false;
  if (applying_request.empty() && save_requested) {
    save_requested = false;
    const bool rebuild = settings[0].value != saved_settings[0].value ||
        settings[1].value != saved_settings[1].value ||
        settings[2].value != saved_settings[2].value ||
        settings[kAntiAliasing].value != saved_settings[kAntiAliasing].value;
    if (!rebuild) {
      success = CommitSettings();
      completed = true;
      message = success ? "Settings saved." : "Applied, but could not save settings.";
    } else if (!rex::cvar::GetFlagInfo("fh1_graphics_request")) {  // a renderer that answers it
      completed = true;
      message = "Live graphics requires the updated Vulkan renderer.";
    } else {
      const auto output = settings[0].value;
      const auto width = output == "4k" ? "3840" : output == "720p" ? "1280" :
          output == "1080p" ? "1920" : "2560";
      const auto height = output == "4k" ? "2160" : output == "720p" ? "720" :
          output == "1080p" ? "1080" : "1440";
      applying_settings = settings;
      applying_request = std::to_string(++request_id) + " " + width + " " + height +
          " " + settings[2].value + " " + BackendMode();
      if (!rex::cvar::SetFlagByName("fh1_graphics_request", applying_request)) {
        applying_request.clear();
        completed = true;
        message = "Could not apply graphics settings.";
      } else {
        ui.Binding(graphics_screen + 0x17C, "Applying graphics settings...");
      }
    }
  }
  if (!applying_request.empty()) {
    const auto result = rex::cvar::GetFlagByName("fh1_graphics_result");
    const auto prefix = applying_request + "|";
    if (result.rfind(prefix, 0) != 0) return;
    const auto status = result.substr(prefix.size());
    applying_request.clear();
    completed = true;
    if (status == "ok") {
      settings = applying_settings;
      success = CommitSettings();
      message = success ? "Settings saved." : "Applied, but could not save settings.";
    } else {
      message = status;
    }
    applying_settings.clear();
  }
  if (!completed) return;
  if (!success) {
    RestorePreview();
    settings = saved_settings;
    AntiAliasingChoices();
    RefreshRows(ui, base);
    RememberPreview();
  }
  REXLOG_INFO("Graphics menu: {}", message);
  ui.Binding(graphics_screen + 0x17C, message);
}
} // namespace forza::graphics_menu
#else
namespace forza::graphics_menu {
void Tick(PPCContext&, uint8_t*) { UpdatePerformanceOverlay(); }
}
#endif // FORZA_TU4
