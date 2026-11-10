// TU4 AI benchmark. Normal launches never enter this path.
#include "benchmark.h"
#include "benchmark_stats.h"
#include "guest_memory.h"
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <vector>

namespace {
using Clock = std::chrono::steady_clock;
bool Requested() {
  static const bool enabled = [] {
    const char *value = std::getenv("FORZA_BENCHMARK_AI");
    return value && std::strcmp(value, "1") == 0;
  }();
  return enabled;
}
uint32_t Read(uint8_t *base, uint32_t address) {
  const auto *p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 |
         p[3];
}
bool Valid(uint32_t address) {
  return address >= 0x100000 && address < 0x80000000 && !(address & 3);
}
float Float(uint8_t *base, uint32_t address) {
  uint32_t bits = Read(base, address);
  float value;
  std::memcpy(&value, &bits, sizeof(value));
  return value;
}
struct Position {
  double x, y, z;
};
double Distance(Position a, Position b) {
  return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y) +
                   (a.z - b.z) * (a.z - b.z));
}
std::atomic<bool> recording{false};
std::mutex samples_mutex;
std::vector<double> samples;
Clock::time_point last_swap;
enum class Phase { waiting, warmup, measuring, finished };
Phase phase = Phase::waiting;
uint32_t race_vehicle = 0;
Position origin{}, last_moved{};
Clock::time_point armed_at, phase_at, moved_at;

void Finish(bool valid, const char *reason) {
  recording.store(false);
  phase = Phase::finished;
  const char *path = std::getenv("FORZA_BENCHMARK_OUTPUT");
  if (!path || !*path)
    return;
  std::vector<double> frames;
  {
    std::lock_guard lock(samples_mutex);
    frames = samples;
  }
  const auto stats = forza::benchmark::Summarize(frames);
  if (stats.frames < 2 && valid) {
    valid = false;
    reason = "insufficient_frames";
  }
  try {
    const std::filesystem::path folder(path);
    std::filesystem::create_directories(folder);
    std::ofstream csv(folder / "frames.csv");
    csv << "elapsed_ms,frame_ms\n";
    double elapsed = 0;
    for (double ms : frames) {
      elapsed += ms;
      csv << elapsed << ',' << ms << '\n';
    }
    csv.close();
    const auto temp = folder / "result.json.tmp";
    std::ofstream out(temp);
    out << "{\n  \"valid\": " << (valid ? "true" : "false")
        << ",\n  \"reason\": \"" << reason
        << "\",\n  \"source\": \"guest_vdswap\""
        << ",\n  \"warmup_seconds\": 15,\n  \"frames\": " << stats.frames
        << ",\n  \"seconds\": " << stats.seconds
        << ",\n  \"average_fps\": " << stats.fps
        << ",\n  \"low_1_percent_fps\": " << stats.low1
        << ",\n  \"p99_ms\": " << stats.p99_ms
        << ",\n  \"worst_ms\": " << stats.worst_ms
        << ",\n  \"stalls_over_50ms\": " << stats.stalls50
        << ",\n  \"stalls_over_100ms\": " << stats.stalls100 << "\n}\n";
    out.close();
    if (!out || !csv)
      throw std::runtime_error("could not write benchmark report");
    std::filesystem::rename(temp, folder / "result.json");
    forza::benchmark::Completed(valid, reason, frames);
    REXLOG_INFO("BENCHMARK: {} ({}) {:.1f} FPS, 1% low {:.1f}; report {}",
                valid ? "complete" : "invalid", reason, stats.fps, stats.low1,
                path);
  } catch (const std::exception &error) {
    REXLOG_ERROR("BENCHMARK: could not save report: {}", error.what());
    forza::benchmark::Completed(false, "report_write_failed", frames);
  }
}
void Observe(uint8_t *base, uint32_t vehicle) {
  if (phase == Phase::finished || !std::getenv("FORZA_BENCHMARK_OUTPUT"))
    return;
  const auto now = Clock::now();
  auto physics = Read(base, vehicle + 0x2D90);
  if (!Valid(physics) || physics < 0xA20)
    return;
  Position pos{Float(base, physics - 0xA20), Float(base, physics - 0xA1C),
               Float(base, physics - 0xA18)};
  if (!std::isfinite(pos.x) || !std::isfinite(pos.y) || !std::isfinite(pos.z)) {
    Finish(false, "invalid_position");
    return;
  }
  if (!race_vehicle) {
    race_vehicle = vehicle;
    origin = last_moved = pos;
    armed_at = moved_at = phase_at = now;
    REXLOG_INFO("BENCHMARK: waiting for race movement; 15 seconds settling, "
                "then 60 seconds measured");
  } else if (race_vehicle != vehicle) {
    Finish(false, "race_changed");
    return;
  }
  if (Distance(pos, last_moved) > 1) {
    moved_at = now;
    last_moved = pos;
  }
  if (phase == Phase::waiting) {
    if (Distance(origin, pos) > 10) {
      phase = Phase::warmup;
      phase_at = now;
      forza::benchmark::Progress("Settling", 0, 15);
      REXLOG_INFO("BENCHMARK: settling for 15 seconds");
    } else if (now - armed_at > std::chrono::minutes(3))
      Finish(false, "race_not_started");
    return;
  }
  if (phase == Phase::warmup || phase == Phase::measuring)
    forza::benchmark::Progress(
        phase == Phase::warmup ? "Settling" : "Measuring",
        std::chrono::duration<double>(now - phase_at).count(),
        phase == Phase::warmup ? 15 : 60);
  if (now - moved_at > std::chrono::seconds(10)) {
    Finish(false, "paused_or_stuck");
    return;
  }
  if (phase == Phase::warmup && now - phase_at >= std::chrono::seconds(15)) {
    {
      std::lock_guard lock(samples_mutex);
      samples.clear();
      last_swap = {};
    }
    phase = Phase::measuring;
    phase_at = now;
    recording.store(true);
    REXLOG_INFO("BENCHMARK: measuring 60 seconds");
  } else if (phase == Phase::measuring &&
             now - phase_at >= std::chrono::seconds(60))
    Finish(true, "completed");
}
} // namespace

#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
extern "C" REX_FUNC(sub_8250B050);
extern "C" REX_FUNC(__imp__sub_82A5D3C0);
extern "C" REX_FUNC(sub_82A5D748);
extern "C" REX_FUNC(sub_82A5D3C0) {
  if (!Requested()) {
    __imp__sub_82A5D3C0(ctx, base);
    return;
  }
  // Allocate the race view and route simulator through the opponent factory.
  // Preserve player ownership: changing the game vehicle's category breaks its
  // script bindings and is not a valid AI takeover.
  sub_82A5D748(ctx, base);
  const auto container = ctx.r3.u32;
  if (!Valid(container))
    return;
  auto *category = forza::GuestPtr(base, container + 8);
  category[0] = category[1] = category[2] = category[3] = 0;
  REXLOG_INFO(
      "BENCHMARK AI: created native race driver for player container {:08X}",
      container);
}
#endif

namespace forza::benchmark {
bool Enabled() { return Requested(); }
void Cancel() { Finish(false, "cancelled"); }
void Fail(const char *reason) { Finish(false, reason); }
std::atomic<int64_t> last_swap_ns{0};
double SecondsSinceSwap() {
  const int64_t last = last_swap_ns.load(std::memory_order_relaxed);
  if (!last)
    return -1;
  const int64_t now =
      std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
  return double(now - last) / 1e9;
}
void OnSwap() {
  last_swap_ns.store(
      std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count(),
      std::memory_order_relaxed);
  if (!recording.load())
    return;
  const auto now = Clock::now();
  std::lock_guard lock(samples_mutex);
  if (!recording.load())
    return;
  if (last_swap != Clock::time_point{})
    samples.push_back(
        std::chrono::duration<double, std::milli>(now - last_swap).count());
  last_swap = now;
}
void Tick(PPCContext &ctx, uint8_t *base) {
  FlowTick(ctx, base);
  if (!Requested())
    return;
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
  auto unavailable = [] {
    if (phase == Phase::warmup || phase == Phase::measuring)
      Finish(false, "race_unavailable");
  };
  auto root = Read(base, 0x833CAAE4);
  if (!Valid(root)) {
    unavailable();
    return;
  }
  auto holder = Read(base, root + 4);
  if (!Valid(holder)) {
    unavailable();
    return;
  }
  auto manager = Read(base, holder + 0x4C);
  if (!Valid(manager)) {
    unavailable();
    return;
  }
  auto table = Read(base, manager), index = Read(base, 0x833ED9F4);
  if (!Valid(table) || index > 128) {
    unavailable();
    return;
  }
  auto component = Read(base, table + index * 4);
  if (!Valid(component)) {
    unavailable();
    return;
  }
  auto first = Read(base, component + 0x28),
       count = Read(base, component + 0x30);
  if (!Valid(first) || count > 32) {
    unavailable();
    return;
  }
  uint32_t player = 0;
  int opponents = 0;
  for (uint32_t i = 0; i < count; ++i) {
    auto container = Read(base, first + i * 4);
    if (!Valid(container))
      continue;
    auto ai = Read(base, container + 0x18);
    if (!Valid(ai))
      continue;
    auto behavior = Read(base, ai + 0x28);
    if (!Valid(behavior) || Read(base, behavior) != 0x82147CFC)
      continue;
    auto vehicle = Read(base, container + 0x10);
    if (!Valid(vehicle))
      continue;
    if (Read(base, container + 8) == 0 && Read(base, vehicle + 0x2DD0) == 0)
      player = vehicle;
    else if (Read(base, container + 8) == 1)
      ++opponents;
  }
  if (!player || !opponents) {
    unavailable();
    return;
  }
  auto controller = Read(base, player + 0x3B80);
  if (!Valid(controller) || Read(base, controller) != 0x820024A4) {
    unavailable();
    return;
  }
  // true releases the human override (mode 1 -> 0). The normal game update
  // then advances its own AI state machine and physics exactly once per tick.
  if (Read(base, player + 0x3B88) == 1) {
    PPCContext call = ctx;
    call.r3.u64 = player;
    call.r4.u64 = 1;
    sub_8250B050(call, base);
    REXLOG_INFO("BENCHMARK AI: handed player {:08X} to native AI control",
                player);
  }
  Observe(base, player);
#else
  if (phase != Phase::finished)
    Finish(false, "title_update_4_required");
#endif
}
} // namespace forza::benchmark
