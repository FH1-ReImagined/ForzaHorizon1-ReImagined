// forza - the ReXGlue app: runtime setup for Forza Horizon.
//
// Customize by overriding virtual hooks from rex::ReXApp.

#pragma once

#include "debug/frame_stats.h"
#include "debug/stall_watch.h"
#include "kernel/live.h"
#include "platform/achievement_toast.h"
#include "platform/discord_presence.h"
#include "platform/cursor_idle.h"
#include "platform/auto_pause.h"
#include "platform/hud_overlay.h"
#include "platform/resume.h"
#include "platform/native_quit.h"
#include "platform/graphics_menu.h"
#include "platform/benchmark.h"
#include "platform/vulkan_hooks.h"
#include "platform/xr.h"
#include "platform/ultrawide.h"
#include <rex/ui/window.h>
#include <rex/cvar.h>
#include <array>
#include <atomic>

#include <string>

#include <rex/filesystem/devices/host_path_device.h>
#include <rex/filesystem/vfs.h>
#include <rex/logging.h>
#include <rex/rex_app.h>
#include <rex/runtime.h>
#include <rex/system/gpu_plugin.h>
#include <rex/ui/overlay/achievement_notification.h>

#include <filesystem>
#include <string>
#ifdef __APPLE__
#include <rex/filesystem.h>
#endif

namespace forza {
void InstallFaultGuard();  // src/debug/fault_guard.cpp
void LogGuestThreads(const char* why);  // src/debug/fault_guard.cpp
void PatchMotionBlurStencilThreshold();  // src/platform/motion_blur_fix.cpp
void PatchSceneResolution();  // src/hooks/scene_resolution.cpp
void LogDlcPackages(const std::filesystem::path& user_data_root);  // src/debug/dlc_log.cpp
// The player's mods over update:\media.zip (src/kernel/mod_overlay.cpp, #149).
void InstallMods(rex::filesystem::VirtualFileSystem* vfs,
                 const std::filesystem::path& user_data_root,
                 const std::filesystem::path& cache_root);
}  // namespace forza

// SDK settings with this game's defaults (src/main.cpp); the GPU plugin's own settings exist
// only once the plugin is loaded.
void ApplyForzaCvarDefaults();
void ApplyForzaGpuCvarDefaults();
void LogForzaPerformanceSettings();

// GPU backend on Windows (the gpu_backend setting, src/main.cpp).
std::string ForzaGpuBackend();

class ForzaApp : public rex::ReXApp {
 public:
  using rex::ReXApp::ReXApp;
  ~ForzaApp() override { forza::discord::Shutdown(); }

  static std::unique_ptr<rex::ui::WindowedApp> Create(
      rex::ui::WindowedAppContext& ctx) {
    return std::unique_ptr<ForzaApp>(new ForzaApp(ctx, "forza",
        PPCImageConfig));
  }

  // Runs after forza.toml and the command line are applied, before the window opens.
  void OnPostInitLogging() override { ApplyForzaCvarDefaults(); }

  void OnConfigureFonts(ImFontAtlas* atlas) override {
    forza::ConfigurePerformanceFont(atlas);
  }

  void OnPreSetup(rex::RuntimeConfig& config) override {
    // VR (vr_mode): the OpenXR runtime must create the Vulkan instance and device, so it is set
    // up before the GPU plugin makes its provider (src/platform/xr.h).
    forza::xr::Prepare();
    if (!config.graphics && config.gpu_plugin.empty()) {
      config.gpu_plugin = "xenos";
    }
    // Loaded here rather than by the SDK so OnCreateDialogs can reach the GPU provider.
    if (!config.graphics && !config.gpu_plugin.empty()) {
#ifdef _WIN32
      config.graphics = rex::system::LoadGpuPlugin(config.gpu_plugin, ForzaGpuBackend());
#else
      config.graphics = rex::system::LoadGpuPlugin(config.gpu_plugin);
#endif
    }
    graphics_ = config.graphics.get();
    ApplyForzaGpuCvarDefaults();
    forza::benchmark::PrepareRuntime();
  }

  // The GPU provider exists now and the window has no swapchain yet: hook the Vulkan
  // calls (swapchain present-mode log, RECOMP_PERF timing; src/platform/vulkan_hooks.h).
  // Friends/chat/invite overlays remain separate from native HUD notifications.
  void OnCreateDialogs(rex::ui::ImGuiDrawer* drawer) override {
    forza::InstallVulkanHooks(graphics_);
    forza::xr::Start(graphics_);  // VR: the session, once the device exists
    forza::InstallHud(drawer, app_context(), window());
    quit_lifetime_=std::make_shared<std::atomic_bool>(true);
    std::weak_ptr<std::atomic_bool> lifetime=quit_lifetime_;
    forza::native_quit::InstallHost([context=&app_context(),game_window=window(),lifetime](uint64_t generation){
      const auto alive=lifetime.lock();if(!alive||!alive->load())return;
      context->CallInUIThread([game_window,lifetime,generation]{
        const auto current=lifetime.lock();
        if(current&&current->load()&&forza::native_quit::Consume(generation))game_window->RequestClose();
      });
    });
    forza::InstallFriendsKey();
    cursor_idle_ = std::make_unique<forza::CursorIdle>(window(), drawer);
    auto_pause_ = std::make_unique<forza::auto_pause::WindowObserver>(window());
  }

  void OnPostSetup() override { forza::InstallVulkanHooks(runtime()->graphics_system()); }
  std::optional<rex::PathConfig> OnFinalizePaths(const rex::PathConfig& defaults,
      std::function<void(rex::PathConfig)> /*resume*/) override {
    if (window()) forza::ultrawide::Prepare(window()->GetActualPhysicalWidth(), window()->GetActualPhysicalHeight());
    return defaults;
  }
  void OnWindowPixelSizeChanged(uint32_t width, uint32_t height) override {
    forza::ultrawide::Resize(width, height);
  }

  // Notifications go directly from the SDK event API to the native HUD.
  std::unique_ptr<rex::ui::AchievementNotificationDialog> CreateAchievementNotificationDialog()
      override { return {}; }

  void OnShutdown() override {
    auto_pause_.reset();
    forza::xr::Shutdown();
    if(quit_lifetime_)quit_lifetime_->store(false);
    forza::native_quit::InstallHost({});
    quit_lifetime_.reset();
    cursor_idle_.reset();
    stall_watchdog_.Stop();
    resume_watcher_.Stop();
    if (achievement_listener_) {
      forza::RemoveAchievementHud(achievements(), achievement_listener_);
      achievement_listener_ = 0;
    }
  }

  // The SDK hard-exits right after a close request is accepted (SIGINT included), so the
  // frame statistics summary is written here, and where each guest thread is (a game that
  // loads forever is closed this way, #76).
  bool OnWindowCloseRequested() override {
    auto_pause_.reset();
    forza::native_quit::Cancel();
    // The SDK terminates guest threads immediately after accepting this request.
    // A retained XThread handle does not keep its guest stack alive.
    stall_watchdog_.Stop();
    resume_watcher_.Stop();
    forza::xr::Shutdown();  // ends the headset session before the SDK exits
    forza::discord::Shutdown();
    forza::perf::Shutdown();
    forza::LogGuestThreads("at close");
    return true;
  }

  // The game keeps ghost and replay streams on the console's utility partition
  // (cache:\ghost_stream_N, cache:\replay_stream, as in Xenia's cache folder). Its
  // XAPI XMountUtilityDrive gets no symbolic link from the runtime (the SDK backs the
  // raw Cache0/Cache1 partitions with a null device), so cache:\ fails with "device
  // not found". Mount a host folder there as Xenia does with mount_cache.
  void OnPreLaunchModule() override {
    forza::graphics_menu::Initialize(user_data_root());
    forza::benchmark::Initialize(user_data_root());
    forza::discord::Initialize();

    if (!achievement_listener_) achievement_listener_ = forza::InstallAchievementHud(achievements());
    if (window()) forza::ultrawide::Initialize(window()->GetActualPhysicalWidth(),
        window()->GetActualPhysicalHeight(),
        [this, last = std::array<double, 6>{-1,-1,-1,-1,-1,-1}]
        (bool letterbox, double sx, double sy, double ox, double oy, double edges) mutable {
          const std::array<double, 6> next{double(letterbox), sx, sy, ox, oy, edges};
          if (last == next) return;
          last = next;
          app_context().CallInUIThreadDeferred([this, letterbox, sx, sy, ox, oy, edges] {
            rex::cvar::SetFlagByName("forza_present_16_9", "true");
            rex::cvar::SetFlagByName("present_letterbox", letterbox ? "true" : "false");
            rex::cvar::SetFlagByName("forza_ultrawide_hud_x", std::to_string(sx));
            rex::cvar::SetFlagByName("forza_ultrawide_hud_y", std::to_string(sy));
            rex::cvar::SetFlagByName("forza_ultrawide_hud_offset_x", std::to_string(ox));
            rex::cvar::SetFlagByName("forza_ultrawide_hud_offset_y", std::to_string(oy));
            rex::cvar::SetFlagByName("forza_ultrawide_hud_edge_shift", std::to_string(edges));
            if (window()) window()->RequestPaint();
          });
        });
    // After the runtime's own handlers (MMIO, GPU watches), so it only sees faults
    // nothing else takes.
    forza::InstallFaultGuard();
    // Long frames and where the game was during them (#187).
    stall_watchdog_.Start();
    // A stopped game or a machine that slept: logged, Forza Live signs in again (#140).
    resume_watcher_.Start();
    // The executable's image is loaded: fix the speed blur's stencil threshold.
    forza::PatchMotionBlurStencilThreshold();
    // The 3D scene below 720p (scene_resolution, #347): scenario and screen texture tables.
    forza::PatchSceneResolution();
    LogForzaPerformanceSettings();
    // Which DLC the game will find (#79: Horizon Rally without the Rally Expansion Pack).
    forza::LogDlcPackages(user_data_root());
    // Texture packs (#384, docs/texture_packs.md): dumps and replacements live in
    // <user data>/textures unless texture_pack_root names another folder (the launcher passes
    // its data folder's, which a Xenia save's user data root is not).
    if (rex::cvar::GetFlagByName("texture_pack_root").empty()) {
      rex::cvar::SetFlagByName("texture_pack_root", (user_data_root() / "textures").string());
    }
    auto* vfs = runtime()->file_system();
    // Before the game opens its first archive.
    forza::InstallMods(vfs, user_data_root(), cache_root());
    const std::filesystem::path host = cache_root() / "cache";
    std::error_code ec;
    std::filesystem::create_directories(host, ec);
    auto device = std::make_unique<rex::filesystem::HostPathDevice>("\\CACHE", host, false);
    if (!device->Initialize() || !vfs->RegisterDevice(std::move(device)) ||
        !vfs->RegisterSymbolicLink("cache:", "\\CACHE")) {
      REXLOG_ERROR("Failed to mount cache: at {}", host.string());
    }
  }

  void OnConfigurePaths(rex::PathConfig& paths) override {
    forza::benchmark::ConfigurePaths(paths);
#ifdef __APPLE__
    const auto exe = rex::filesystem::GetExecutableFolder();
    if (exe.filename() == "MacOS" && exe.parent_path().filename() == "Contents") {
      // Settings must stay writable without changing the signed app bundle.
      paths.config_path = paths.user_data_root / "forza.toml";
    }
#endif
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
    // Title Update 4 build: the patched executable, its facade DLLs and the update's media.zip
    // are in one folder (tools/apply_title_update.py), mounted as update: (the SDK's
    // update_data_root setting; default: tu4 next to the game folder).
    if (paths.update_data_root.empty()) {
      auto game = std::filesystem::absolute(paths.game_data_root).lexically_normal();
      if (!game.has_filename()) game = game.parent_path();
      paths.update_data_root = game.parent_path() / "tu4";
    }
    if (!std::filesystem::exists(paths.update_data_root / "default.xex")) {
      REXLOG_ERROR("This build is for Title Update 4 and needs the patched default.xex in {} "
                   "(tools/apply_title_update.py; set update_data_root)",
                   paths.update_data_root.string());
    }
#endif
  }
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
  // game:\default.xex and the facades resolve to the update's: symbolic links from their
  // game: device paths, which ResolvePath follows after game: itself.
  void OnLoadXexImage(std::string& /*xex_image*/) override {
    auto* vfs = runtime()->file_system();
    for (const char* name : {"default.xex", "XMediaFacade_default.xex", "SpeechFacade_default.xex"}) {
      vfs->RegisterSymbolicLink(std::string("\\Device\\Harddisk0\\Partition1\\") + name,
                                std::string("\\Device\\Harddisk0\\PartitionUpdate\\") + name);
    }
    REXLOG_INFO("Title Update 4 build: executable and facades from update:");
  }
#endif

 private:
  std::shared_ptr<std::atomic_bool> quit_lifetime_;
  std::unique_ptr<forza::CursorIdle> cursor_idle_;
  std::unique_ptr<forza::auto_pause::WindowObserver> auto_pause_;
  forza::stall::Watchdog stall_watchdog_;
  forza::resume::Watcher resume_watcher_;
  uint64_t achievement_listener_ = 0;
  rex::system::IGraphicsSystem* graphics_ = nullptr;
};
