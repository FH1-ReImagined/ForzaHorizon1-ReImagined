// forza - frame-time statistics; see frame_stats.h and docs/perf.md.

#include "frame_stats.h"
#include "../platform/hud_overlay.h"
#include "../platform/benchmark.h"

#include "../hooks/frame_time.h"

#include <rex/logging.h>
#include <rex/ui/flags.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <mutex>
#include <numeric>
#include <string>
#include <vector>

namespace forza::perf {
namespace {

using Clock = std::chrono::steady_clock;

constexpr double kWindowSeconds = 10.0;
// The game's main loop runs every second guest vblank (33.3 ms at the console's 60 Hz), or
// every vblank when twice the frame rate is past the SDK's limit (src/hooks/frame_time.cpp,
// docs/framerate.md).
double TargetMs() { return forza::frame_time::TargetMs(); }
constexpr double kHitchMs = 100.0;

struct Config {
  bool enabled = false;
  std::string scene = "default";
  std::string reset_path;
  std::FILE* csv = nullptr;
};

bool EnvSet(const char* name) {
  const char* v = std::getenv(name);
  return v && *v && *v != '0';
}

Config& GetConfig() {
  static Config config = [] {
    Config c;
    c.enabled = EnvSet("RECOMP_PERF");
    if (!c.enabled) return c;
    if (const char* v = std::getenv("RECOMP_PERF_SCENE"); v && *v) c.scene = v;
    if (const char* v = std::getenv("RECOMP_PERF_RESET"); v && *v) c.reset_path = v;
    if (const char* v = std::getenv("RECOMP_PERF_CSV"); v && *v) {
      c.csv = std::fopen(v, "w");
      if (c.csv) {
        std::fputs("scene,src,t,ms\n", c.csv);
      } else {
        REXLOG_WARN("PERF: cannot write {}", v);
      }
    }
    REXLOG_INFO("PERF: frame statistics on (scene={}{}{})", c.scene, c.csv ? ", CSV" : "",
                c.reset_path.empty() ? "" : ", reset trigger");
    return c;
  }();
  return config;
}

std::mutex& CsvMutex() {
  static std::mutex mutex;
  return mutex;
}

void WriteCsv(const char* src, double t, float ms) {
  Config& config = GetConfig();
  std::lock_guard lock(CsvMutex());
  if (config.csv) std::fprintf(config.csv, "%s,%s,%.4f,%.3f\n", config.scene.c_str(), src, t, ms);
}

void FlushCsv() {
  Config& config = GetConfig();
  std::lock_guard lock(CsvMutex());
  if (config.csv) std::fflush(config.csv);
}

std::atomic<uint64_t> g_pipelines{0};

// Seconds since the first timed event.
double Seconds(Clock::time_point now) {
  static const Clock::time_point start = [now] {
    if (const char* path = std::getenv("RECOMP_PERF_EPOCH"); path && *path) {
      const auto wall = std::chrono::system_clock::now();
      const auto steady = Clock::now();
      const double epoch = std::chrono::duration<double>(wall.time_since_epoch()).count() -
          std::chrono::duration<double>(steady - now).count();
      if (std::FILE* file = std::fopen(path, "w")) {
        std::fprintf(file, "%.9f\n", epoch);
        std::fclose(file);
      } else {
        REXLOG_WARN("PERF: cannot write clock epoch {}", path);
      }
    }
    return now;
  }();
  return std::chrono::duration<double>(now - start).count();
}

struct Stats {
  double fps, p50, p95, p99, max, low1, ontarget;
  uint32_t stutters, hitches;
};

Stats Compute(std::vector<float> ms) {
  Stats s{};
  if (ms.empty()) return s;
  std::sort(ms.begin(), ms.end());
  const size_t n = ms.size();
  auto pct = [&](double p) {
    size_t rank = size_t(std::ceil(p / 100.0 * double(n)));
    return double(ms[std::clamp<size_t>(rank, 1, n) - 1]);
  };
  const double sum = std::accumulate(ms.begin(), ms.end(), 0.0);
  s.fps = sum > 0 ? 1000.0 * double(n) / sum : 0;
  s.p50 = pct(50);
  s.p95 = pct(95);
  s.p99 = pct(99);
  s.max = ms.back();
  const size_t slow = std::max<size_t>(1, n / 100);
  const double slow_mean = std::accumulate(ms.end() - slow, ms.end(), 0.0) / double(slow);
  s.low1 = slow_mean > 0 ? 1000.0 / slow_mean : 0;
  uint32_t on_target = 0;
  const double target_ms = TargetMs();
  for (float f : ms) {
    if (f > 2.0 * s.p50) ++s.stutters;
    if (f > kHitchMs) ++s.hitches;
    if (std::abs(f - target_ms) <= 0.2 * target_ms) ++on_target;
  }
  s.ontarget = 100.0 * on_target / double(n);
  return s;
}

class Stream {
 public:
  explicit Stream(const char* name) : name_(name) {}

  void OnFrame(Clock::time_point now) {
    const double t = Seconds(now);
    std::lock_guard lock(mutex_);
    if (!has_last_) {
      has_last_ = true;
      last_ = now;
      window_start_ = total_start_ = t;
      window_pipelines_ = total_pipelines_ = g_pipelines.load();
      return;
    }
    const float ms = std::chrono::duration<float, std::milli>(now - last_).count();
    last_ = now;
    window_.push_back(ms);
    total_.push_back(ms);
    WriteCsv(name_, t, ms);
    if (t - window_start_ >= kWindowSeconds) {
      const uint64_t pipelines = g_pipelines.load();
      Log(window_, t, pipelines - window_pipelines_, "");
      window_.clear();
      window_start_ = t;
      window_pipelines_ = pipelines;
      FlushCsv();
    }
  }

  void Reset(double t) {
    std::lock_guard lock(mutex_);
    total_.clear();
    total_start_ = t;
    total_pipelines_ = g_pipelines.load();
  }

  void Summarize(double t) {
    std::lock_guard lock(mutex_);
    if (total_.empty()) return;
    char tail[64];
    std::snprintf(tail, sizeof(tail), " final=1 from=%.1f", total_start_);
    Log(total_, t, g_pipelines.load() - total_pipelines_, tail);
  }

 private:
  void Log(const std::vector<float>& ms, double t, uint64_t pipelines, const char* tail) {
    const Stats s = Compute(ms);
    REXLOG_INFO(
        "PERF: scene={} src={} t={:.1f} fps={:.1f} p50={:.1f} p95={:.1f} p99={:.1f} max={:.1f} "
        "low1={:.1f} stutters={} hitches={} ontarget={:.1f} pipelines={}{}",
        GetConfig().scene, name_, t, s.fps, s.p50, s.p95, s.p99, s.max, s.low1, s.stutters,
        s.hitches, s.ontarget, pipelines, tail);
  }

  const char* name_;
  std::mutex mutex_;
  bool has_last_ = false;
  Clock::time_point last_;
  double window_start_ = 0, total_start_ = 0;
  uint64_t window_pipelines_ = 0, total_pipelines_ = 0;
  std::vector<float> window_;  // frame times (ms) of the current 10 s window
  std::vector<float> total_;   // since the start or the last reset
};

Stream& Guest() {
  static Stream stream("guest");
  return stream;
}

Stream& Present() {
  static Stream stream("present");
  return stream;
}

}  // namespace

bool Enabled() { return GetConfig().enabled; }

void OnGuestFrame() {
  PerformanceOverlayFrame();
  benchmark::OnSwap();
  if (!Enabled()) return;
  const Clock::time_point now = Clock::now();
  Guest().OnFrame(now);
  // The reset trigger is polled every 10 guest frames.
  static int polls = 0;
  const Config& config = GetConfig();
  if (!config.reset_path.empty() && ++polls % 10 == 0 &&
      std::remove(config.reset_path.c_str()) == 0) {
    const double t = Seconds(now);
    Guest().Reset(t);
    Present().Reset(t);
    REXLOG_INFO("PERF: summary restarted at t={:.1f}", t);
  }
}

void OnHostPresent() {
  if (Enabled()) Present().OnFrame(Clock::now());
}

void OnPipelinesCreated(uint32_t count) { g_pipelines.fetch_add(count); }

void SetScene(const std::string& name) {
  if (!Enabled()) return;
  const double t = Seconds(Clock::now());
  Guest().Summarize(t);
  Present().Summarize(t);
  Guest().Reset(t);
  Present().Reset(t);
  {
    std::lock_guard lock(CsvMutex());
    GetConfig().scene = name;
  }
  REXLOG_INFO("PERF: scene={} from t={:.1f}", name, t);
}

void Shutdown() {
  static std::atomic<bool> done{false};
  if (!Enabled() || done.exchange(true)) return;
  const double t = Seconds(Clock::now());
  Guest().Summarize(t);
  Present().Summarize(t);
  Config& config = GetConfig();
  std::lock_guard lock(CsvMutex());
  if (config.csv) {
    std::fclose(config.csv);
    config.csv = nullptr;
  }
}

}  // namespace forza::perf
