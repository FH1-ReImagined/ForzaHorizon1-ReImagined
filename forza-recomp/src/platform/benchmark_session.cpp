#include "benchmark.h"
#include "benchmark_label.h"
#include "benchmark_profile.h"
#include "benchmark_start.h"
#include "guest_memory.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/rex_app.h>
#include <rex/system/kernel_state.h>

namespace forza {
void RestartForBenchmark();
}
namespace forza::benchmark {
namespace {
using Clock = std::chrono::steady_clock;
std::mutex view_mutex;
View view;
std::atomic<Screen> screen{Screen::hidden};
std::atomic<int> action{0};
std::atomic<bool> reset_pad{false};
std::atomic<bool> automatic{false}, boot_input{false}, confirm_input{false},
    pause_input{false};
std::filesystem::path source_root, settings_snapshot;
uint32_t title_menu = 0, single_player_id = 10;
bool title_started = false, event_started = false;
[[maybe_unused]] bool title_listed = false;
Clock::time_point started = Clock::now(), world_ready{};

void Environment(const char *name, const std::string &value) {
#ifdef _WIN32
  _putenv_s(name, value.c_str());
#else
  if (value.empty())
    unsetenv(name);
  else
    setenv(name, value.c_str(), 1);
#endif
}
std::string Settings() {
  const auto get = [](const char *name) { return rex::cvar::GetFlagByName(name); };
  const std::string upscaler = get("fh1_upscaler"), width = get("fh1_output_width"),
                    height = get("fh1_output_height"), scale = get("resolution_scale"),
                    scene = get("scene_resolution"), effect = get("present_effect"),
                    rate = get("frame_rate");
  return SettingsLabel({upscaler, width, height, scale, scene, effect, rate});
}
void Status(const std::string &status, Screen next) {
  std::lock_guard lock(view_mutex);
  view.status = status;
  view.screen = next;
  if (next == Screen::setup || next == Screen::results)
    reset_pad.store(true);
  screen.store(next);
}
uint32_t Read(uint8_t *base, uint32_t address) {
  const auto *p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 |
         p[3];
}
bool Valid(uint32_t p) { return p >= 0x100000 && p < 0x80000000 && !(p & 3); }
uint32_t Activities(uint8_t *base) {
  const auto root = Read(base, 0x833CAAE4),
             holder = Valid(root) ? Read(base, root + 4) : 0;
  const auto manager = Valid(holder) ? Read(base, holder + 0x4C) : 0;
  const auto table = Valid(manager) ? Read(base, manager) : 0,
             index = Read(base, 0x833EDA1C);
  return Valid(table) && index < 512 ? Read(base, table + index * 4) : 0;
}
uint32_t Call(PPCContext &ctx, uint8_t *base, uint32_t address,
              std::initializer_list<uint32_t> args) {
  PPCContext call = ctx;
  unsigned i = 3;
  for (auto value : args) {
    switch (i++) {
    case 3:
      call.r3.u64 = value;
      break;
    case 4:
      call.r4.u64 = value;
      break;
    case 5:
      call.r5.u64 = value;
      break;
    case 6:
      call.r6.u64 = value;
      break;
    }
  }
  rex::runtime::ResolveIndirectFunction(address)(call, base);
  return call.r3.u32;
}
bool GraphicsFlag(const std::string &key) {
  if (key == "fh1_graphics_request" || key == "fh1_graphics_result" ||
      key.find("status") != std::string::npos)
    return false;
  return key.starts_with("fh1_") || key.starts_with("fsr3_") ||
         key == "forza_hud_edges" || key == "forza_performance_overlay" ||
         key == "forza_cockpit_fov" || key == "forza_chase_near_fov" ||
         key == "forza_chase_far_fov" || key == "forza_hood_fov" ||
         key == "forza_bumper_fov" ||
         key == "world_shadow_filter" || key == "world_ambient_occlusion" ||
         key == "world_car_detail" || key == "frame_rate" || key == "video_mode_refresh_rate" ||
         key == "resolution" || key == "resolution_scale" || key == "vsync" ||
         key == "anisotropic_override" || key == "swap_post_effect";
}
void SaveSettings() {
  std::ofstream out(source_root / "benchmark-settings.txt", std::ios::trunc);
  for (const auto &key : rex::cvar::ListFlags())
    if (GraphicsFlag(key))
      out << std::quoted(key) << ' '
          << std::quoted(rex::cvar::GetFlagByName(key)) << '\n';
  out.close();
  if (!out)
    throw std::runtime_error("Could not preserve graphics settings");
}
void Restart(bool run) {
  try {
    const auto request = source_root / "benchmark-request.txt";
    if (run) {
      SaveSettings();
      const auto temp = source_root / "benchmark-request.tmp";
      std::ofstream out(temp, std::ios::trunc);
      out << "forza-benchmark-v1\n";
      out.close();
      if (!out)
        throw std::runtime_error("Could not prepare the benchmark");
      std::error_code ec;
      std::filesystem::remove(request, ec);
      std::filesystem::rename(temp, request);
    }
    if (!run) {
      std::ofstream out(source_root / "benchmark-return.txt");
      out << "forza-benchmark-v1\n";
      out.close();
      if (!out)
        throw std::runtime_error("Could not return to the game");
    }
    forza::RestartForBenchmark();
    throw std::runtime_error("Could not restart the game for the benchmark");
  } catch (const std::exception &error) {
    Status(error.what(), Screen::setup);
  }
}
} // namespace

void ConfigurePaths(rex::PathConfig &paths) {
  source_root = paths.user_data_root;
  if (const char *was_session = std::getenv("FORZA_BENCHMARK_SESSION");
      was_session && *was_session) {
    for (auto key : {"FORZA_NET", "FORZA_SERVER", "FORZA_GRAPHICS_SETTINGS",
                     "FORZA_DLSS_STATUS_FILE"}) {
      const std::string saved = "FORZA_BENCHMARK_SAVED_" + std::string(key);
      const char *original = std::getenv(saved.c_str());
      if (original) {
        const std::string value(original);
        Environment(key, value == "<unset>" ? "" : value);
        Environment(saved.c_str(), "");
      }
    }
    for (auto key : {"FORZA_BENCHMARK_AI", "FORZA_BENCHMARK_OUTPUT",
                     "FORZA_BENCHMARK_AUTOSTART", "FORZA_BENCHMARK_SESSION"})
      Environment(key, "");
  }
  const auto returning = source_root / "benchmark-return.txt";
  if (std::filesystem::is_regular_file(returning)) {
    settings_snapshot = source_root / "benchmark-settings.txt";
    std::filesystem::remove(returning);
  }
  std::ifstream request(source_root / "benchmark-request.txt");
  std::string marker;
  std::getline(request, marker);
  request.close();
  if (marker != "forza-benchmark-v1")
    return;
  std::error_code ec;
  std::filesystem::remove(source_root / "benchmark-request.txt", ec);
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
  try {
    const auto stamp =
        std::chrono::system_clock::now().time_since_epoch().count();
    const auto folder =
        source_root / "benchmarks" / ("recaro-" + std::to_string(stamp));
    CopyProfiles(source_root, folder);
    if (std::filesystem::is_regular_file(source_root /
                                         "benchmark-settings.txt")) {
      settings_snapshot = folder / "runtime-settings.txt";
      std::filesystem::copy_file(source_root / "benchmark-settings.txt",
                                 settings_snapshot);
    }
    // DLC packages are immutable game inputs. Hard links avoid duplicating
    // large installations; writable profiles above are always independent.
    LinkPackages(source_root, folder);
    for (auto key : {"FORZA_NET", "FORZA_SERVER", "FORZA_GRAPHICS_SETTINGS",
                     "FORZA_DLSS_STATUS_FILE"}) {
      const char *original = std::getenv(key);
      Environment(("FORZA_BENCHMARK_SAVED_" + std::string(key)).c_str(),
                  original ? original : "<unset>");
    }
    paths.user_data_root = folder;
    rex::cvar::SetFlagByName("user_data_root", folder.string());
    for (auto key : {"FORZA_SERVER", "FORZA_RELAUNCHED"})
      Environment(key, "");
    Environment("FORZA_NET", "0");
    rex::cvar::SetFlagByName("live_server", "");
    Environment("FORZA_BENCHMARK_AI", "1");
    Environment("FORZA_BENCHMARK_OUTPUT", folder.string());
    Environment("FORZA_BENCHMARK_SESSION", "1");
    Environment("FORZA_GRAPHICS_SETTINGS",
                (folder / "graphics-settings.json").string());
    Environment("FORZA_DLSS_STATUS_FILE", (folder / "dlss.status").string());
    automatic.store(true);
    boot_input.store(true);
    Status("Loading Recaro Rush", Screen::running);
    {
      std::lock_guard lock(view_mutex);
      view.folder = folder.string();
    }
  } catch (const std::exception &error) {
    Status(error.what(), Screen::setup);
  }
#else
  Status("Benchmark mode requires Title Update 4", Screen::setup);
#endif
}
void Initialize(const std::filesystem::path &root) {
  if (source_root.empty())
    source_root = root;
  {
    std::lock_guard lock(view_mutex);
    view.settings = Settings();
  }
  if (automatic.load()) {
    std::ofstream(root / "session.txt")
        << "Recaro Rush (FR02)\nNative race AI\n15 seconds settling, 60 "
           "seconds measured\n"
        << "Output: " << rex::cvar::GetFlagByName("fh1_output_width") << "x"
        << rex::cvar::GetFlagByName("fh1_output_height")
        << "\nFrame cap: " << rex::cvar::GetFlagByName("frame_rate")
        << "\nAA/upscaler: " << rex::cvar::GetFlagByName("fh1_upscaler")
        << "\n";
  }
}
void PrepareRuntime() {
  if (!settings_snapshot.empty()) {
    std::ifstream in(settings_snapshot);
    std::string key, value;
    while (in >> std::quoted(key) >> std::quoted(value))
      if (GraphicsFlag(key))
        if (!rex::cvar::SetFlagByName(key, value))
          REXLOG_WARN("BENCHMARK: could not restore graphics flag {}", key);
  }
  if (automatic.load())
    rex::cvar::SetFlagByName("live_server", "");
}
void Show() {
  {
    std::lock_guard lock(view_mutex);
    view.settings = Settings();
  }
  Status("Recaro Rush", Screen::setup);
}
void Run() { action.store(1); }
void Close() { action.store(2); }
bool WantsPad() { return screen.load() != Screen::hidden || Enabled(); }
View Snapshot() {
  std::lock_guard lock(view_mutex);
  return view;
}
void TitleReady(uint32_t menu, uint32_t id) {
  title_menu = menu;
  single_player_id = id;
}
void PreRaceReady(uint32_t) {
  if (automatic.load())
    confirm_input.store(true);
}
void Progress(const char *text, double elapsed, double duration) {
  if (!automatic.load())
    return;
  boot_input.store(false);
  confirm_input.store(false);
  std::lock_guard lock(view_mutex);
  view.status = text;
  view.elapsed = elapsed;
  view.duration = duration;
  view.screen = Screen::running;
  screen.store(Screen::running);
}
void Completed(bool valid, const char *reason,
               const std::vector<double> &frames) {
  if (!automatic.load())
    return;
  std::lock_guard lock(view_mutex);
  view.valid = valid;
  view.stats = Summarize(frames);
  view.frame_ms.clear();
  // Retain the worst interval in each bucket, so the graph keeps real hitches.
  const size_t stride = std::max<size_t>(1, (frames.size() + 599) / 600);
  for (size_t i = 0; i < frames.size(); i += stride)
    view.frame_ms.push_back(float(*std::max_element(
        frames.begin() + i,
        frames.begin() + std::min(frames.size(), i + stride))));
  view.status = valid ? "Benchmark complete"
                      : std::string("Benchmark incomplete: ") + reason;
  view.screen = Screen::results;
  reset_pad.store(true);
  screen.store(Screen::results);
  confirm_input.store(false);
  pause_input.store(valid);
}
uint16_t Pad(uint16_t buttons, bool &block_all) {
  static uint16_t previous = 0;
  static Screen previous_screen = Screen::hidden;
  const auto current = screen.load();
  // Pad is not polled while the overlay is hidden. After closing and reopening
  // setup, previous_screen can still be setup: discard the opening A press on
  // every dialog entry, even when no hidden-screen poll occurred between them.
  const bool entered = reset_pad.exchange(false);
  const uint16_t pressed = !entered && current == previous_screen
                               ? buttons & ~previous : 0;
  previous = buttons;
  previous_screen = current;
  if (pause_input.exchange(false)) {
    block_all = true;
    return 0x10;
  }
  if (current == Screen::setup || current == Screen::results) {
    if (pressed & 0x1000)
      Run();
    if (pressed & 0x2000)
      Close();
    block_all = true;
    return 0;
  }
  if (automatic.load()) {
    block_all = true;
    if (pressed & 0x2000)
      action.store(3);
    const double seconds =
        std::chrono::duration<double>(Clock::now() - started).count();
    if ((boot_input.load() || confirm_input.load()) &&
        std::fmod(seconds, 2.0) < 0.15)
      return boot_input.load() ? 0x1010 : 0x1000;
    return 0;
  }
  return buttons;
}
void FlowTick(PPCContext &ctx, uint8_t *base) {
  const int requested = action.exchange(0);
  if (requested == 1) {
    Restart(true);
    return;
  }
  if (requested == 2) {
    if (automatic.load())
      Restart(false);
    else
      Status("", Screen::hidden);
    return;
  }
  if (requested == 3) {
    Cancel();
    return;
  }
  if (!automatic.load() || screen.load() == Screen::results)
    return;
  // The race has to start while the game keeps drawing (src/platform/benchmark_start.h, #725).
  if (const char *reason = StartTimeout(
          std::chrono::duration<double>(Clock::now() - started).count(), SecondsSinceSwap(),
          event_started)) {
    Fail(reason);
    return;
  }
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
  // The title list ignores presses until its scene has loaded, which takes seconds on a
  // new profile. Calling its choice handler (sub_82836928) on the first tick that listed
  // the rows started the game's Single Player transition too early: it read guest
  // address 4 and the game closed (#540). Boot's A presses (boot_input) choose the
  // highlighted Single Player as a player's do, once the game takes them; the menu's
  // state is then Single Player's id.
  if (!title_started && Valid(title_menu)) {
    const auto state = Read(base, title_menu + 0x260);
    if (state == 12)
      title_listed = true;
    else if (title_listed && state == single_player_id) {
      boot_input.store(false);
      title_started = true;
      REXLOG_INFO("BENCHMARK: loading single player for fixed event FR02");
      return;
    }
  }
  if (title_started && !event_started) {
    const auto activities = Activities(base);
    const auto first = Valid(activities) ? Read(base, activities + 4) : 0,
               end = Valid(activities) ? Read(base, activities + 8) : 0;
    if (!Valid(first) || end < first || end - first > 16384)
      return;
    uint32_t found = 0;
    for (uint32_t p = first; p < end; p += 4) {
      const auto item = Read(base, p);
      if (!Valid(item))
        continue;
      if (Read(base, item) != 0x8200C104)
        continue;
      const auto name =
          Read(base, item + 0x24) < 16 ? item + 0x10 : Read(base, item + 0x10);
      if (Valid(name) && Read(base, item + 0x20) == 4 &&
          std::string(reinterpret_cast<char *>(GuestPtr(base, name)), 4) ==
              "FR02")
        found = item;
    }
    if (!found)
      return;
    if (world_ready == Clock::time_point{})
      world_ready = Clock::now();
    if (Clock::now() - world_ready < std::chrono::seconds(8))
      return;
    const auto scratch = REX_KERNEL_MEMORY()->SystemHeapAlloc(128);
    if (!scratch)
      return;
    std::fill_n(GuestPtr(base, scratch), 128, 0);
    const std::string state_name = "load_into_event", entry = "enter";
    std::copy(state_name.begin(), state_name.end(), GuestPtr(base, scratch));
    std::copy(entry.begin(), entry.end(), GuestPtr(base, scratch + 64));
    Call(ctx, base, 0x8296B130, {found});
    const bool accepted = Call(ctx, base, 0x82997058,
                               {found + 0x164, scratch, scratch + 64}) != 0;
    REX_KERNEL_MEMORY()->SystemHeapFree(scratch);
    event_started = accepted;
    REXLOG_INFO("BENCHMARK: fixed event FR02 {}",
                accepted ? "requested" : "rejected");
  }

#endif
}
} // namespace forza::benchmark

#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
extern "C" REX_FUNC(__imp__sub_8281CF68);
extern "C" REX_FUNC(sub_8281CF68) {
  const auto screen = ctx.r3.u32;
  __imp__sub_8281CF68(ctx, base);
  forza::benchmark::PreRaceReady(screen);
}
#endif
