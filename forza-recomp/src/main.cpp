// forza - ReXGlue Recompiled Project

#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
#include "generated_tu4/default/forza_init.h"  // codegen output of forza_manifest_tu4.toml
#else
#include "generated/default/forza_init.h"
#endif

#include "forza_app.h"
#include "hooks/frame_time.h"
#include "debug/performance_settings.h"

#include <rex/cvar.h>
#include <rex/logging.h>

#include <string>
#include <string_view>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#endif

REX_DEFINE_APP(forza, ForzaApp::Create)

REXCVAR_DEFINE_STRING(gpu_backend, "vulkan", "GPU",
                      "GPU backend on Windows: vulkan (with this project's fixes) or d3d12")
    .lifecycle(rex::cvar::Lifecycle::kInitOnly);

std::string ForzaGpuBackend() { return REXCVAR_GET(gpu_backend); }

// The game's vblank callback wakes its main loop every second guest vblank (every vblank
// with its mainthread30fps option off, every third at 20 and 40 fps), and the main loop
// times its simulation in real time, so the frame rate follows the guest vblank rate. Away
// from 30 the frame time comes from the guest tick clock instead of the kernel's millisecond
// tick (src/hooks/frame_time.cpp, docs/framerate.md).
REXCVAR_DEFINE_INT32(frame_rate, 30, "Display",
                     "Frame rate: 30 (the console's), 20 or 40 (even frames on 60 and 120 Hz "
                     "screens for PCs that cannot hold 30 or 60), or 60 to 240 (60, 120, 144, "
                     "...). The game times its simulation in real time (docs/framerate.md)")
    .validator([](std::string_view v) {
      int fps = 0;
      for (char c : v) {
        if (c < '0' || c > '9' || fps > 1000) return false;
        fps = fps * 10 + (c - '0');
      }
      return fps == 20 || fps == 30 || fps == 40 || (fps >= 60 && fps <= 240);
    })
    .lifecycle(rex::cvar::Lifecycle::kHotReload);

#ifdef _WIN32
namespace {
// forza.exe's manifest (resources/forza.manifest) already makes the process per-monitor-v2 DPI
// aware. This is the same request for a build whose manifest did not make it into the exe: it is
// refused (harmlessly) once the awareness is set. Before main() so it runs ahead of any window.
// Without it, display scaling (200% on a 4K TV) shrinks exclusive fullscreen to a quarter (#418).
const bool g_dpi_aware = [] {
  using Fn = BOOL(WINAPI*)(HANDLE);
  if (HMODULE user32 = GetModuleHandleW(L"user32.dll")) {
    if (auto fn = reinterpret_cast<Fn>(
            reinterpret_cast<void*>(GetProcAddress(user32, "SetProcessDpiAwarenessContext")))) {
      fn(reinterpret_cast<HANDLE>(-4));  // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    }
  }
  return true;
}();
}  // namespace
#endif

namespace {

// Gives an SDK cvar this game's default. A value from forza.toml, the environment
// (REX_<NAME>) or the command line stays; the settings UI and SaveConfig treat the new
// value as the default. (From RecompiledHaloReach.)
void SetSdkCvarDefault(std::string_view name, std::string_view value) {
  for (rex::cvar::FlagEntry& entry : rex::cvar::GetRegistry()) {
    if (entry.name != name) continue;
    if (entry.source == rex::cvar::Source::kDefault) entry.setter(value);
    entry.default_value = value;
    return;
  }
  REXLOG_WARN("SDK setting {} not found", name);
}

}  // namespace

void ApplyForzaCvarDefaults() {
  // Present with FIFO (vsync) only. The SDK prefers IMMEDIATE (tearing), and its UI thread
  // repaints nonstop on Linux: without a vsync wait that is thousands of presents a second,
  // GPU time taken from the emulated GPU (measured in RecompiledHaloReach, and here in
  // docs/perf.md). forza.toml or the command line can allow the other modes again.
  SetSdkCvarDefault("vulkan_allow_present_mode_immediate", "false");
  SetSdkCvarDefault("vulkan_allow_present_mode_mailbox", "false");
  SetSdkCvarDefault("vulkan_allow_present_mode_fifo_relaxed", "false");
#if defined(_WIN32)
  // Direct3D 12 likewise: the SDK presents without waiting for vsync and with tearing
  // allowed, so a fixed-refresh display tore even with vsync on (#218). Without tearing the
  // flip model shows the newest finished frame at each refresh instead.
  SetSdkCvarDefault("d3d12_allow_variable_refresh_rate_and_tearing", "false");
#endif
  // frame_rate other than 30: the guest vblank at three times 20 or 40, twice a higher frame
  // rate, or the frame rate when twice is past the SDK's limit (src/hooks/frame_time.cpp). A
  // video_mode_refresh_rate set in forza.toml or on the command line wins.
  if (REXCVAR_GET(frame_rate) != 30) {
    SetSdkCvarDefault("video_mode_refresh_rate", std::to_string(forza::frame_time::VblankHz()));
  }
}

void ApplyForzaGpuCvarDefaults() {
  // The game renders each car's thumbnail (Car Select, garage, the car panel), reads the
  // resolved image back on the CPU, compresses it and saves it in the profile
  // (ForzaProfile/Thumbnails). Without resolve readback the CPU reads stale guest memory and
  // the saved thumbnail is noise (docs/framerate.md). "some" copies a resolve back only the
  // first time it is seen (and again after 60 unused frames), which is what the thumbnails
  // need and costs nothing in a race; "fast" copies every resolve every frame and cost
  // 16-21% of the frame rate at 144 (docs/perf.md).
  SetSdkCvarDefault("readback_resolve", "some");
}

void LogForzaPerformanceSettings() {
  forza::performance_settings::Snapshot([](std::string_view name)
      -> std::optional<std::string> {
    if (!rex::cvar::GetFlagInfo(name)) return std::nullopt;
    return rex::cvar::GetFlagByName(name);
  }, [](const std::string& line) {
    REXLOG_INFO("Performance settings at guest launch: {}", line);
  });
}
