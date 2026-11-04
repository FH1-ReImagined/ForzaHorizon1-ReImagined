// forza - car detail option: how far other cars keep their detailed models (#361,
// docs/graphics-detail.md "Car detail").
//
// The game's render settings ("DynamicRenderSettings": media/renderscenarios.zip and the
// executable's defaults) are read through one getter per setting, which picks the value of the
// override set, the scenario on top, or the defaults. The car LOD selection (sub_824351D8, each
// car each frame; TU4 addresses) compares the car's distance to the camera, less its size and
// divided by the camera's zoom, with CarLODTransDist (LODdist_Near 15, Far 25, VeryFar 50;
// getter sub_8248CBD0) or, for traffic, CarLODTransDistTraffic (T_LOD2-5 15/25/50/100,
// T_LOD4/5_Closest 20/80; getter sub_82E99A08), and clamps the result to the car's minimum LOD.
// That minimum comes from the car's rank by distance (sub_82477368) against CarLODThresholds
// (2/4/6, getter sub_8247C5C8): only the two nearest cars other than the player's may use LOD0,
// up to the fourth LOD1, the rest LOD2 at best. world_car_detail scales the distances and the
// counts in the getters' results (the stored settings stay the game's), so it applies live.
// Only the LODs a car has loaded are used (the selection clamps to the car's LOD count).
// Disc build: the same functions (sub_82405228, sub_8245D378, sub_82DD74C8, sub_8244CF08).
// tu_address_map: TU4 addresses

#include "../platform/car_detail.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string_view>
#include <unordered_map>

REXCVAR_DEFINE_STRING(world_car_detail, "console", "Display",
                      "How far other cars keep their detailed models: console (the game's), "
                      "high (2x as far, twice as many cars), ultra (4x)");

#if FORZA_TU4
#define CarLodDistances sub_8248CBD0
#define OriginalCarLodDistances __imp__sub_8248CBD0
#define TrafficLodDistances sub_82E99A08
#define OriginalTrafficLodDistances __imp__sub_82E99A08
#define CarLodThresholds sub_8247C5C8
#define OriginalCarLodThresholds __imp__sub_8247C5C8
#define CarLodSelect sub_824351D8
#define OriginalCarLodSelect __imp__sub_824351D8
#else
#define CarLodDistances sub_8245D378
#define OriginalCarLodDistances __imp__sub_8245D378
#define TrafficLodDistances sub_82DD74C8
#define OriginalTrafficLodDistances __imp__sub_82DD74C8
#define CarLodThresholds sub_8244CF08
#define OriginalCarLodThresholds __imp__sub_8244CF08
#define CarLodSelect sub_82405228
#define OriginalCarLodSelect __imp__sub_82405228
#endif
extern "C" REX_FUNC(OriginalCarLodDistances);
extern "C" REX_FUNC(OriginalTrafficLodDistances);
extern "C" REX_FUNC(OriginalCarLodThresholds);
extern "C" REX_FUNC(OriginalCarLodSelect);

namespace {
namespace cd = forza::car_detail;

std::atomic<int> level{0};

cd::Level CurrentLevel() {
  static std::once_flag once;
  std::call_once(once, [] {
    level = int(cd::ParseLevel(REXCVAR_GET(world_car_detail)));
    // The in-game Graphics screen changes it live.
    rex::cvar::RegisterChangeCallback("world_car_detail",
                                      [](std::string_view, std::string_view value) {
                                        level = int(cd::ParseLevel(value));
                                      });
  });
  // FORZA_CAR_DETAIL_FILE=PATH: a test aid for comparing the levels in one session (the cost
  // measurement in docs/graphics-detail.md): the level is read from the file twice a second.
  static const char* test_file = std::getenv("FORZA_CAR_DETAIL_FILE");
  if (test_file && *test_file) {
    static std::atomic<int64_t> next{0};
    const int64_t now = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::steady_clock::now().time_since_epoch())
                            .count();
    if (now >= next.load(std::memory_order_relaxed)) {
      next = now + 500;
      if (FILE* f = std::fopen(test_file, "r")) {
        char text[16] = {};
        const size_t n = std::fread(text, 1, sizeof(text) - 1, f);
        std::fclose(f);
        std::string_view value(text, n);
        while (!value.empty() && (value.back() == '\n' || value.back() == ' ')) {
          value.remove_suffix(1);
        }
        level = int(cd::ParseLevel(value));
      }
    }
  }
  const auto current = cd::Level(level.load(std::memory_order_relaxed));
  static std::atomic<int> logged{-1};
  if (logged.exchange(int(current)) != int(current)) {
    REXLOG_INFO("world_car_detail: {} (LOD distances x{}, LOD counts x{})",
                current == cd::Level::kUltra  ? "ultra"
                : current == cd::Level::kHigh ? "high"
                                              : "console",
                cd::DistanceScale(current), cd::CountScale(current));
  }
  return current;
}

uint32_t Load32(uint8_t* base, uint32_t address) {
  const auto* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
void Store32(uint8_t* base, uint32_t address, uint32_t value) {
  auto* p = forza::GuestPtr(base, address);
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
}
float LoadF(uint8_t* base, uint32_t address) {
  const uint32_t v = Load32(base, address);
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}
void StoreF(uint8_t* base, uint32_t address, float f) {
  uint32_t v;
  std::memcpy(&v, &f, 4);
  Store32(base, address, v);
}

void ScaleDistances(uint8_t* base, uint32_t out, int count) {
  const cd::Level l = CurrentLevel();
  if (l == cd::Level::kConsole || !out) return;
  for (int i = 0; i < count; ++i) {
    StoreF(base, out + 4 * i, cd::ScaleDistance(LoadF(base, out + 4 * i), l));
  }
}

// FORZA_CAR_LOD_STATS=1: count the LOD changes of each car and log them every 5 s
// (the pop-in measurement in docs/graphics-detail.md).
struct LodStats {
  std::mutex mutex;
  std::unordered_map<uint64_t, int32_t> last;
  uint64_t calls = 0, switches = 0, by_lod[8] = {};
  // Switches by the car's distance to the camera: < 25, 25-50, 50-100, 100-200, 200+ m.
  uint64_t by_distance[5] = {};
  std::chrono::steady_clock::time_point since = std::chrono::steady_clock::now();
};
LodStats& Stats() {
  static LodStats s;
  return s;
}
bool StatsOn() {
  static const bool on = [] {
    const char* e = std::getenv("FORZA_CAR_LOD_STATS");
    return e && *e && *e != '0';
  }();
  return on;
}
void Record(uint32_t model, uint32_t car, int32_t lod, float distance) {
  auto& s = Stats();
  std::lock_guard<std::mutex> lock(s.mutex);
  const uint64_t key = uint64_t(model) << 32 | car;
  auto [it, fresh] = s.last.try_emplace(key, lod);
  if (!fresh && it->second != lod) {
    ++s.switches;
    ++s.by_distance[distance < 25 ? 0 : distance < 50 ? 1 : distance < 100 ? 2 : distance < 200 ? 3 : 4];
    it->second = lod;
  }
  ++s.calls;
  ++s.by_lod[lod >= 0 && lod < 8 ? lod : 7];
  const auto now = std::chrono::steady_clock::now();
  if (now - s.since >= std::chrono::seconds(5)) {
    REXLOG_INFO("car LOD stats: {} picks, {} switches, {} cars; LOD0-4 {} {} {} {} {}; switches "
                "at <25/25-50/50-100/100-200/200+ m {} {} {} {} {}",
                s.calls, s.switches, s.last.size(), s.by_lod[0], s.by_lod[1], s.by_lod[2],
                s.by_lod[3], s.by_lod[4], s.by_distance[0], s.by_distance[1], s.by_distance[2],
                s.by_distance[3], s.by_distance[4]);
    s.calls = s.switches = 0;
    for (auto& n : s.by_lod) n = 0;
    for (auto& n : s.by_distance) n = 0;
    s.since = now;
  }
}

}  // namespace

// r3 the result (float[3]: Near, Far, VeryFar), r4 the settings, r5 the scenario.
extern "C" REX_FUNC(CarLodDistances) {
  const uint32_t out = ctx.r3.u32;
  OriginalCarLodDistances(ctx, base);
  ScaleDistances(base, out, 3);
}

// r3 the result (float[6]: T_LOD2, T_LOD3, T_LOD4, T_LOD5, T_LOD4_Closest, T_LOD5_Closest).
extern "C" REX_FUNC(TrafficLodDistances) {
  const uint32_t out = ctx.r3.u32;
  OriginalTrafficLodDistances(ctx, base);
  ScaleDistances(base, out, 6);
}

// r3 the result (int32[3]: LOD1_Threshold, LOD2_Threshold, the car count that lowers both).
extern "C" REX_FUNC(CarLodThresholds) {
  const uint32_t out = ctx.r3.u32;
  OriginalCarLodThresholds(ctx, base);
  const cd::Level l = CurrentLevel();
  if (l == cd::Level::kConsole || !out) return;
  for (int i = 0; i < 3; ++i) {
    Store32(base, out + 4 * i,
            uint32_t(cd::ScaleCount(int32_t(Load32(base, out + 4 * i)), l)));
  }
}

// r3 the car's model, r4 the car; returns the car's LOD in r3.
extern "C" REX_FUNC(CarLodSelect) {
  if (!StatsOn()) {
    OriginalCarLodSelect(ctx, base);
    return;
  }
  const uint32_t model = ctx.r3.u32, car = ctx.r4.u32;
  // The distance the selection starts from: the model's centre (+960) through the car's
  // camera-relative matrix (rows at +0, +16, +32, +48), before it takes the car's size off.
  float p[4];
  for (int j = 0; j < 4; ++j) {
    p[j] = LoadF(base, car + 48 + 4 * j);
    for (int i = 0; i < 3; ++i) {
      p[j] += LoadF(base, model + 960 + 4 * i) * LoadF(base, car + 16 * i + 4 * j);
    }
  }
  const float w = p[3] != 0.0f ? p[3] : 1.0f;
  const float distance =
      std::sqrt((p[0] * p[0] + p[1] * p[1] + p[2] * p[2]) / (w * w));
  OriginalCarLodSelect(ctx, base);
  Record(model, car, int32_t(ctx.r3.u32), distance);
}
