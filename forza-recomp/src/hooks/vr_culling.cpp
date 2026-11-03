// forza - the game's screen-size cull in the VR cockpit (docs/vr.md "Version 4", #333).
//
// Each view the renderer sets up (main scene, shadow/depth, cube map faces, mirror; TU4
// sub_82E91710) gets a cull record: the camera position (+0), a projection factor k (+16), the
// track's screen-area distance D (+20, TrackSettings <screenAreaDistances>) and D squared (+24).
// The size test (sub_8243FD70, TU4 sub_8246FE78; r3 the record, r4 the item's position, f1 its
// size) keeps an item while k * distance^2 <= D^2 * size, i.e. while it covers enough of the
// picture; D below a small constant turns the test off. k is 3 tan^2 of the view's vertical
// half-angle (1.08 for the in-car camera's 62 degrees, 3 for a cube map face, 5.9 for the VR
// cockpit's 109), and the main view's and the shadow view's records both take the camera's: in
// the VR cockpit the size test dropped objects at 1/2.3 of their distance on a monitor (measured
// standing at the festival: 41 items a frame in the main view, 9 with the in-car camera's k), so
// cars, posts and roadside objects appeared close ahead.
//
// In the VR cockpit (vr_cull_scale, default on) those two records are tested with the in-car
// camera's own k (the factor comes from the camera hook: tan^2 of its vertical half-angle over
// the cockpit's), so objects stay as far out as on a monitor, at the same angular size. The cube
// map and mirror records are unchanged. FORZA_VR_CULL_STATS=1 logs, every 5 s, each record's D
// and k and how many items it tested and dropped (a test aid).
// tu_address_map: TU4 addresses

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/world_detail.h"
#include "../platform/xr.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>

REXCVAR_DEFINE_BOOL(vr_cull_scale, true, "VR",
                    "Cockpit: test the main view's object size cull as for the in-car camera's own "
                    "field of view (off: the wide headset view drops small and far objects, such as "
                    "cars and posts, at a fraction of the distance)");

#if FORZA_TU4
#define SizeTest sub_8246FE78
#define OriginalSizeTest __imp__sub_8246FE78
#else
#define SizeTest sub_8243FD70
#define OriginalSizeTest __imp__sub_8243FD70
#endif
extern "C" REX_FUNC(OriginalSizeTest);

namespace {

float LoadF(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  const uint32_t v = uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
  float f;
  std::memcpy(&f, &v, 4);
  return f;
}

// FORZA_VR_CULL_STATS: per record (by D) the last k, items tested and dropped.
struct Stat {
  std::atomic<uint32_t> d_bits{0};
  std::atomic<float> k{0};
  std::atomic<uint32_t> tested{0}, dropped{0}, rescued{0};
};
Stat stats[8];
std::atomic<int64_t> next_log{0};

int64_t NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

void Count(float d, float k, bool kept, bool rescued) {
  uint32_t bits;
  std::memcpy(&bits, &d, 4);
  for (Stat& s : stats) {
    uint32_t cur = s.d_bits.load(std::memory_order_relaxed);
    if (cur == 0 && s.d_bits.compare_exchange_strong(cur, bits)) cur = bits;
    if (cur != bits) continue;
    s.k.store(k, std::memory_order_relaxed);
    s.tested.fetch_add(1, std::memory_order_relaxed);
    if (!kept) s.dropped.fetch_add(1, std::memory_order_relaxed);
    if (rescued) s.rescued.fetch_add(1, std::memory_order_relaxed);
    break;
  }
  const int64_t now = NowMs();
  int64_t due = next_log.load(std::memory_order_relaxed);
  if (now < due || !next_log.compare_exchange_strong(due, now + 5000)) return;
  if (!due) return;  // the first call only arms the timer
  for (Stat& s : stats) {
    const uint32_t b = s.d_bits.load(std::memory_order_relaxed);
    if (!b) continue;
    float dd;
    std::memcpy(&dd, &b, 4);
    REXLOG_INFO("VR cull stats: D {:.1f} k {:.4g}: {} tested, {} dropped, {} kept by the VR scale (5 s)", dd,
                s.k.load(), s.tested.exchange(0), s.dropped.exchange(0), s.rescued.exchange(0));
  }
}

}  // namespace

extern "C" REX_FUNC(SizeTest) {
  static const bool log_stats = std::getenv("FORZA_VR_CULL_STATS") != nullptr;
  const float scale = forza::xr::CullScale();
  if (!log_stats && scale >= 1.0f) {
    OriginalSizeTest(ctx, base);
    return;
  }
  const uint32_t record = ctx.r3.u32;
  const float d = LoadF(base, record + 20);
  const float k = LoadF(base, record + 16);
  // The main view's and the shadow view's records (the track's mainScene and shadow caster
  // distances; both carry the camera's field of view). The factor is below 1 only while posed
  // cockpit views are drawn.
  const bool main_view = scale < 1.0f && forza::world_detail::IsCameraCullDistance(d);
  const uint32_t pos = ctx.r4.u32;
  const double size = ctx.f1.f64;
  OriginalSizeTest(ctx, base);
  bool kept = ctx.r3.u32 != 0;
  bool rescued = false;
  if (!kept && main_view) {
    // The same test with k scaled: k * scale * distance^2 <= D^2 * size.
    const float dx = LoadF(base, pos) - LoadF(base, record), dy = LoadF(base, pos + 4) - LoadF(base, record + 4),
                dz = LoadF(base, pos + 8) - LoadF(base, record + 8);
    if (k * scale * (dx * dx + dy * dy + dz * dz) <= LoadF(base, record + 24) * float(size)) {
      ctx.r3.u64 = 1;
      kept = rescued = true;
    }
  }
  if (log_stats) Count(d, k, kept, rescued);
}
