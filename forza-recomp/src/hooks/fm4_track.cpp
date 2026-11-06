// forza - FM4 circuits inside FH1 (#648, "The Festival"): dev switches for the conversion
// work (tools/fm4_track_mod.py, tools/fm4_pvs.py). All off unless their variable is set:
//   FORZA_TRACK_SPAWN=x,y,z   the player car's teleports go there; also turns on the
//                             FM4TRACK diagnostics below (file errors, the dirty disc
//                             message, zone, texture and resource table checks)
//   FORZA_PVS_TRACE=<file>    every read of the PVS reader, to <file>
//   FORZA_INSECURE_FILES=a|b  files the secure file system lets open as plain files (a
//                             changed zipmanifest.xml that mounts a converted track's archive)
//   FORZA_FM4_COL=1           the track's collision is read whole from its .col files (a
//                             converted FM4 track's, tools/fm4_col.py), not streamed as .fiz
//   FORZA_FIZ_TRACE=1         log the track archive's depot reads (with their callers), the
//                             teleports' orientation and the collision queries (callers,
//                             limits, masks, empty results, the car's cache boxes)
//   FORZA_TRACK_NO_TRAFFIC=1  the other cars go far below the circuit
//   FORZA_STREAM_LOG=1        log every track archive file streamed (FM4STREAM: number, name)
//   FORZA_ZONE_TRACE=1        log zones made live and dropped, with the streaming blocks,
//                             live instance objects and placements in use
//   FORZA_FM4_ZONE_POOL=n     n times the live instance objects (a converted track's zones)
// tu_address_map: TU4 addresses
//
// tools/fm4_track_mod.py --free-roam makes FH1's free roam load an FM4 circuit in place of
// Colorado. The car then starts at its saved Colorado position, in the void.
// FORZA_TRACK_SPAWN=x,y,z[,fx,fz] (world metres, y up; Suzuka's grid is
// 605.6,598.5,-134.1,0.61,-0.79) moves the player car's teleports there (the spawn into free roam, a car
// reset), facing (fx, 0, fz) when given, and logs every vehicle teleport. Called from the teleport hook in teleport_velocity.cpp.

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>

#include <algorithm>
#include <cmath>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace forza::fm4_track {

void OnTeleport(PPCContext& ctx, uint8_t* base) {
#if FORZA_TU4
  static const struct Spawn {
    bool on = false, heading = false;
    float x = 0, y = 0, z = 0, fx = 0, fz = 0;
  } spawn = [] {
    Spawn s;
    const char* p = std::getenv("FORZA_TRACK_SPAWN");
    const int n = p ? std::sscanf(p, "%f,%f,%f,%f,%f", &s.x, &s.y, &s.z, &s.fx, &s.fz) : 0;
    s.on = n >= 3;
    s.heading = n == 5;
    if (s.heading) {
      const float m = std::sqrt(s.fx * s.fx + s.fz * s.fz);
      s.heading = m > 0;
      if (s.heading) { s.fx /= m; s.fz /= m; }
    }
    return s;
  }();
  if (!spawn.on) return;
  // lvx byte-reverses the vector: x is the top lane (world_challenge.cpp).
  REXLOG_INFO("FM4TRACK: teleport of vehicle {:08X} to {:.1f},{:.1f},{:.1f}", ctx.r3.u32,
              ctx.v1.f32[3], ctx.v1.f32[2], ctx.v1.f32[1]);
  if (std::getenv("FORZA_FIZ_TRACE") && ctx.r4.u32 >= 0x40000000 && ctx.r4.u32 < 0x80000000) {
    std::string o;
    for (int i = 0; i < 16; ++i) {
      const uint8_t* p = forza::GuestPtr(base, ctx.r4.u32 + 4 * i);
      const uint32_t v = uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
      float f;
      std::memcpy(&f, &v, 4);
      o += fmt::format(" {:.3f}", f);
    }
    REXLOG_INFO("FM4TRACK: teleport orientation at {:08X}:{}", ctx.r4.u32, o);
  }
  // Only the player's car: the first car teleported (the spawn into free roam) is it. The
  // free-roam AI drivers are teleported too, to their own places.
  static std::atomic<uint32_t> player{0};
  uint32_t expected = 0;
  if (!player.compare_exchange_strong(expected, ctx.r3.u32) && expected != ctx.r3.u32) {
    // FORZA_TRACK_NO_TRAFFIC=1: the other cars (free roam's traffic and drivers) go far below
    // the circuit, where nothing holds them.
    if (std::getenv("FORZA_TRACK_NO_TRAFFIC")) ctx.v1.f32[2] = -2000.0f;
    return;
  }
  ctx.v1.f32[3] = spawn.x;
  ctx.v1.f32[2] = spawn.y;
  ctx.v1.f32[1] = spawn.z;
  // x,y,z,fx,fz: the car faces (fx, 0, fz). r4 is the orientation, rows of 4 floats (right,
  // up, forward: right = (fz, 0, -fx), as in the game's own teleports).
  if (spawn.heading && ctx.r4.u32 >= 0x40000000 && ctx.r4.u32 < 0x80000000) {
    const float rows[12] = {spawn.fz, 0, -spawn.fx, 0, 0, 1, 0, 0, spawn.fx, 0, spawn.fz, 0};
    for (int i = 0; i < 12; ++i) {
      uint32_t v;
      std::memcpy(&v, &rows[i], 4);
      uint8_t* q = forza::GuestPtr(base, ctx.r4.u32 + 4 * i);
      q[0] = uint8_t(v >> 24); q[1] = uint8_t(v >> 16); q[2] = uint8_t(v >> 8); q[3] = uint8_t(v);
    }
  }
#else
  (void)ctx;
#endif
}

}  // namespace forza::fm4_track

// FORZA_PVS_TRACE=<file>: every read of the PVS reader (sub_82CF2968, CTrackPresentation's
// PVS file) goes to <file> as records of offset (u32), size (u32) and the reading code's
// return addresses (8 x u32), little-endian, so the version-50 layout can be written down
// from a real file (tools/fm4_pvs.py).
// tu_address_map: TU4 addresses
#if FORZA_TU4
#include "../platform/guest_memory.h"

#include <rex/ppc/func.h>
#include <rex/filesystem.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/file.h>
#include <rex/filesystem/vfs.h>
#include <rex/system/kernel_state.h>
#include <span>
#include <rex/system/xmemory.h>

#include <cctype>
#include <cstring>
#include <chrono>
#include <map>
#include <mutex>
#include <unordered_map>
#include <set>
#include <string_view>
#include <thread>
#include <vector>

extern "C" REX_FUNC(__imp__sub_82CF2968);
extern "C" REX_FUNC(__imp__sub_82D378E8);

namespace {
std::FILE* PvsTrace() {
  static std::FILE* f = [] {
    const char* p = std::getenv("FORZA_PVS_TRACE");
    return p && *p ? std::fopen(p, "wb") : nullptr;
  }();
  return f;
}
thread_local int pvs_depth = 0;
thread_local uint32_t pvs_offset = 0;
std::mutex pvs_mutex;

uint32_t Load32(uint8_t* base, uint32_t a) {
  const uint8_t* p = forza::GuestPtr(base, a);
  return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
}
bool Stack(uint32_t a) { return a >= 0x40000000 && a < 0x80000000 && !(a & 3); }
std::string CString(uint8_t* base, uint32_t a);
bool GuestReadable(uint32_t a) {
  auto* heap = rex::system::kernel_state()->memory()->LookupHeap(a);
  uint32_t protect = 0;
  return heap && heap->QueryProtect(a, &protect) && (protect & rex::memory::kMemoryProtectRead);
}
std::string CString(uint8_t* base, uint32_t a) {
  std::string s;
  for (uint32_t i = 0; i < 300 && GuestReadable(a + i); ++i) {
    const char c = char(*forza::GuestPtr(base, a + i));
    if (!c) break;
    s += c;
  }
  return s;
}
}  // namespace

extern "C" REX_FUNC(sub_82CF2968) {
  std::FILE* f = PvsTrace();
  if (!f) return __imp__sub_82CF2968(ctx, base);
  REXLOG_INFO("PVST: begin");
  ++pvs_depth;
  pvs_offset = 0;
  __imp__sub_82CF2968(ctx, base);
  --pvs_depth;
  std::fflush(f);
  REXLOG_INFO("PVST: end at {}", pvs_offset);
}

extern "C" REX_FUNC(sub_82D378E8) {
  if (!pvs_depth) return __imp__sub_82D378E8(ctx, base);
  uint32_t rec[10] = {pvs_offset, ctx.r5.u32, uint32_t(ctx.lr)};
  uint32_t frame = ctx.r1.u32;
  for (int i = 3; i < 10 && Stack(frame) && GuestReadable(frame); ++i) {
    const uint32_t parent = Load32(base, frame);
    if (parent <= frame || parent - frame > 0x100000 || !GuestReadable(parent - 8)) break;
    rec[i] = Load32(base, parent - 8);
    frame = parent;
  }
  __imp__sub_82D378E8(ctx, base);
  pvs_offset += rec[1];
  std::lock_guard<std::mutex> lock(pvs_mutex);
  std::fwrite(rec, sizeof(rec), 1, PvsTrace());
}
#endif

// The game's fatal file error (the dirty disc message, sub_82CBF3E0): who raised it, when
// FORZA_TRACK_SPAWN or FORZA_PVS_TRACE is set (a track being converted).
// tu_address_map: TU4 addresses
#if FORZA_TU4
extern "C" REX_FUNC(__imp__sub_82CBF3E0);
extern "C" REX_FUNC(sub_82CBF3E0) {
  // Called after every file operation (r3 the kind, 1 a read, 2 an open; r4 the result, 0 a
  // failure; r5 the error code).
  if (!ctx.r4.u32 && (std::getenv("FORZA_TRACK_SPAWN") || PvsTrace())) {
    std::string chain;
    uint32_t frame = ctx.r1.u32;
    for (int i = 0; i < 12 && Stack(frame) && GuestReadable(frame); ++i) {
      const uint32_t parent = Load32(base, frame);
      if (parent <= frame || parent - frame > 0x100000 || !GuestReadable(parent - 8)) break;
      chain += fmt::format(" {:08X}", Load32(base, parent - 8));
      frame = parent;
    }
    REXLOG_ERROR("FM4TRACK: fatal file error r3 {:08X} r4 {:08X} r5 {} from {:08X}{}",
                 ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, uint32_t(ctx.lr), chain);
  }
  __imp__sub_82CBF3E0(ctx, base);
}

// The dirty disc message (sub_82CC94E8): who showed it.
extern "C" REX_FUNC(__imp__sub_82CC94E8);
extern "C" REX_FUNC(sub_82CC94E8) {
  if (std::getenv("FORZA_TRACK_SPAWN") || PvsTrace()) {
    std::string chain;
    uint32_t frame = ctx.r1.u32;
    for (int i = 0; i < 16 && Stack(frame) && GuestReadable(frame); ++i) {
      const uint32_t parent = Load32(base, frame);
      if (parent <= frame || parent - frame > 0x100000 || !GuestReadable(parent - 8)) break;
      chain += fmt::format(" {:08X}", Load32(base, parent - 8));
      frame = parent;
    }
    REXLOG_ERROR("FM4TRACK: dirty disc message r3 {:08X} r4 {:08X} from {:08X}{}", ctx.r3.u32,
                 ctx.r4.u32, uint32_t(ctx.lr), chain);
  }
  __imp__sub_82CC94E8(ctx, base);
}

// The secure file system's lookup (sub_82CC3180: r3 the result {found, size, ...}, r4 the
// path). The disc's files are listed in the executable's digest section with their sizes;
// the game opens a listed file through it and stops with its dirty disc message when the
// size differs. FORZA_INSECURE_FILES=a|b (parts of paths, e.g. zipmanifest.xml) makes the
// lookup say "not listed" for those, so the game opens them as plain files: a changed
// zipmanifest.xml that mounts a converted track's archive.
extern "C" REX_FUNC(__imp__sub_82CC3180);
extern "C" REX_FUNC(sub_82CC3180) {
  const uint32_t out = ctx.r3.u32, path = ctx.r4.u32;
  __imp__sub_82CC3180(ctx, base);
  static const std::vector<std::string> insecure = [] {
    std::vector<std::string> v;
    const char* p = std::getenv("FORZA_INSECURE_FILES");
    std::string cur;
    for (; p && *p; ++p) {
      if (*p == '|') { if (!cur.empty()) v.push_back(cur); cur.clear(); }
      else cur += char(std::tolower(static_cast<unsigned char>(*p)));
    }
    if (!cur.empty()) v.push_back(cur);
    return v;
  }();
  if (insecure.empty()) return;
  std::string p = CString(base, path);
  for (auto& c : p) c = char(std::tolower(static_cast<unsigned char>(c)));
  for (const auto& part : insecure) {
    if (p.find(part) == std::string::npos) continue;
    uint8_t* r = forza::GuestPtr(base, out);
    if (r[0]) REXLOG_INFO("FM4TRACK: {} opened as a plain file, not a secure one", p);
    std::memset(r, 0, 16);
    return;
  }
}

namespace { void DumpTexObjectsLive(uint8_t* base, uint32_t t); }
// The track presentation, from the last zone made live (sub_82E1CA20's r3).
static std::atomic<uint32_t> fm4_presentation{0};
// The streaming resource manager (sub_82E2EAD8's r3; +16 its 12-byte entries by id).
static std::atomic<uint32_t> fm4_resource_manager{0};
// A zone being made live (sub_82E1CA20: r3 the track presentation, r4 the zone record):
// the presentation's model and instance tables, for FORZA_TRACK_SPAWN runs.
namespace { void LogZones(uint8_t* base, uint32_t pres, const char* what, uint32_t zone); }
extern "C" REX_FUNC(__imp__sub_82E1CA20);
extern "C" REX_FUNC(sub_82E1CA20) {
  if (std::getenv("FORZA_TRACK_SPAWN")) {
    const uint32_t t = ctx.r3.u32, z = ctx.r4.u32;
    {
      const uint32_t texs = Load32(base, t + 2812);
      REXLOG_INFO("FM4TRACK: zone live: presentation {:08X} textures {:08X} x{} [0x4C2] {:08X}",
                  t, texs, Load32(base, t + 2816), Load32(base, texs + 4 * 0x4C2));
    }
    REXLOG_INFO("FM4TRACK: zone {:08X} live: models {:08X} x{} instances {:08X} x{} zone id {}",
                z, Load32(base, t + 2548), Load32(base, t + 2552) >> 16, Load32(base, t + 2556),
                Load32(base, t + 2560), Load32(base, z + 12));
    const uint32_t table = Load32(base, t + 2556), n = Load32(base, t + 2560);
    uint32_t nulls = 0, first = 0xFFFFFFFF, last = 0;
    for (uint32_t i = 0; i < n && GuestReadable(table + 4 * i); ++i) {
      if (!Load32(base, table + 4 * i)) {
        if (first == 0xFFFFFFFF) first = i;
        last = i;
        ++nulls;
      }
    }
    REXLOG_INFO("FM4TRACK: instance objects: {} null of {}, first {} last {}", nulls, n, first,
                last);
    const uint32_t pool = Load32(base, t + 0x50);
    if (pool && GuestReadable(pool)) {
      REXLOG_INFO("FM4TRACK: zone pool {:08X}: {:08X} {:08X} {:08X} {:08X} {:08X} {:08X}", pool,
                  Load32(base, pool), Load32(base, pool + 4), Load32(base, pool + 8),
                  Load32(base, pool + 12), Load32(base, pool + 16), Load32(base, pool + 20));
    }
    const uint32_t zones = Load32(base, t + 55332), id = Load32(base, z + 12);
    const uint32_t cell = zones ? Load32(base, zones + 20 * id) : 0;
    if (cell && GuestReadable(cell)) {
      const uint32_t list = Load32(base, cell + 88), count = Load32(base, cell + 92) >> 16;
      uint32_t mx = 0;
      std::string first5;
      for (uint32_t k = 0; k < count && GuestReadable(list + 4 * k); ++k) {
        const uint32_t v = Load32(base, list + 4 * k) & 0x7FFFFFFF;
        mx = std::max(mx, v);
        if (k < 5) first5 += fmt::format(" {:08X}", Load32(base, list + 4 * k));
      }
      REXLOG_INFO("FM4TRACK: zone cell {:08X}: {} instances, max index {}, first{}", cell, count,
                  mx, first5);
    }
  }
  fm4_presentation = ctx.r3.u32;
  const uint32_t live_pres = ctx.r3.u32, live_zone = GuestReadable(ctx.r4.u32 + 12) ? Load32(base, ctx.r4.u32 + 12) : 0;
  __imp__sub_82E1CA20(ctx, base);
  {
    static std::atomic<int> dumps{0};
    { const int d = dumps.fetch_add(1); if (std::getenv("FORZA_FM4_TEXDUMP") && (d == 0 || d == 4 || d == 15)) DumpTexObjectsLive(base, live_pres); }
  }
  if (std::getenv("FORZA_ZONE_TRACE")) LogZones(base, live_pres, "live", live_zone);
}

// A zone's instance index (sub_82CEE9F8: r3 out, r4 the cell, r5 &k): log the zone's list
// once, for FORZA_TRACK_SPAWN runs.
extern "C" REX_FUNC(__imp__sub_82CEE9F8);
extern "C" REX_FUNC(sub_82CEE9F8) {
  const uint32_t cell = ctx.r4.u32;
  __imp__sub_82CEE9F8(ctx, base);
  static std::atomic<int> logged{0};
  if (std::getenv("FORZA_TRACK_SPAWN") && logged.fetch_add(1) < 3) {
    const uint32_t list = Load32(base, cell + 88), count = Load32(base, cell + 92) >> 16;
    uint32_t mx = 0, cap = Load32(base, cell + 96);
    std::string first5;
    for (uint32_t k = 0; k < count && GuestReadable(list + 4 * k); ++k) {
      const uint32_t v = Load32(base, list + 4 * k);
      mx = std::max(mx, v & 0x7FFFFFFF);
      if (k < 6) first5 += fmt::format(" {:08X}", v);
    }
    REXLOG_INFO("FM4TRACK: cell {:08X} list {:08X} count {} (+96 {:08X}) max {} first{}", cell,
                list, count, cap, mx, first5);
  }
}

// A model's textures being bound (sub_82E18FC8: r3 the presentation, r4 &model): log a model
// whose texture has no user list, for FORZA_TRACK_SPAWN runs.
extern "C" REX_FUNC(__imp__sub_82E18FC8);
extern "C" REX_FUNC(sub_82E18FC8) {
  static std::atomic<int> logged{0};
  if (std::getenv("FORZA_TRACK_SPAWN") && logged.load() < 5) {
    const uint32_t t = ctx.r3.u32;
    const uint32_t model = uint32_t(*forza::GuestPtr(base, ctx.r4.u32)) << 8 |
                           *forza::GuestPtr(base, ctx.r4.u32 + 1);
    const uint32_t models = Load32(base, t + 55368), m = models + 84 * model;
    const uint32_t list = Load32(base, m + 60), count = Load32(base, m + 64);
    const uint32_t texs = Load32(base, t + 2812), ntex = Load32(base, t + 2816);
    for (uint32_t k = 0; k < count && k < 64; ++k) {
      const uint32_t idx = Load32(base, list + 4 * k) & 0xFFFFFF;
      const uint32_t obj = idx < ntex ? Load32(base, texs + 4 * idx) : 0;
      const uint32_t users = obj ? Load32(base, obj + 20) : 0;
      if (!obj || !users) {
        if (logged.fetch_add(1) < 5) {
          REXLOG_INFO("FM4TRACK: model {} slot {} texture {:08X} (of {}): object {:08X} "
                      "users {:08X} count {}; presentation {:08X} textures {:08X}", model, k,
                      Load32(base, list + 4 * k), ntex, obj, users,
                      obj ? Load32(base, obj + 16) >> 16 : 0, t, texs);
        }
        break;
      }
    }
  }
  __imp__sub_82E18FC8(ctx, base);
}

// The texture user lists (sub_82E24248, once per track load): its early-out conditions.
namespace forza { void LogGuestThreads(const char* why); }
extern "C" REX_FUNC(__imp__sub_82E24248);
namespace { void AliasAtlasTiles(uint8_t* base, uint32_t t); }
extern "C" REX_FUNC(sub_82E24248) {
  AliasAtlasTiles(base, ctx.r3.u32);
  if (std::getenv("FORZA_TRACK_SPAWN")) {
    const uint32_t t = ctx.r3.u32;
    REXLOG_INFO("FM4TRACK: texture users: lists {} {} textures {} models {} instances {}",
                Load32(base, t + 59436 + 4) - Load32(base, t + 59436),
                Load32(base, t + 59452 + 4) - Load32(base, t + 59452), Load32(base, t + 2816),
                Load32(base, t + 55372) >> 16, Load32(base, t + 55360));
  }
  const uint32_t t = ctx.r3.u32;
  __imp__sub_82E24248(ctx, base);
  if (std::getenv("FORZA_TRACK_SPAWN")) {
    const uint32_t texs = Load32(base, t + 2812), n = Load32(base, t + 2816);
    uint32_t nulls = 0, first = 0xFFFFFFFF;
    for (uint32_t i = 0; i < n; ++i) {
      const uint32_t o = Load32(base, texs + 4 * i);
      if (!o || !Load32(base, o + 20)) { ++nulls; if (first == 0xFFFFFFFF) first = i; }
    }
    const uint32_t o = n > 0x4C2 ? Load32(base, texs + 4 * 0x4C2) : 0;
    if (o) {
      // Watch the entry: a host thread polls it and logs every guest thread's call chain
      // when it changes (the closest to a data breakpoint the runtime has).
      const uint32_t addr = texs + 4 * 0x4C2;
      std::thread([addr, o, base] {
        for (int i = 0; i < 2000000; ++i) {
          if (Load32(base, addr) != o) {
            REXLOG_INFO("FM4TRACK: watch: [{:08X}] {:08X} -> {:08X}", addr, o, Load32(base, addr));
            forza::LogGuestThreads("at the watched write");
            return;
          }
          std::this_thread::sleep_for(std::chrono::microseconds(20));
        }
      }).detach();
    }
    REXLOG_INFO("FM4TRACK: after texture users ({:08X} table {:08X}): {} of {} without a list (first {}); 0x4C2 "
                "object {:08X} +16 {:08X} +20 {:08X}", t, texs, nulls, n, first, o,
                o ? Load32(base, o + 16) : 0, o ? Load32(base, o + 20) : 0);
  }
}

extern "C" REX_FUNC(__imp__sub_82E23B50);
extern "C" REX_FUNC(sub_82E23B50) {
  if (std::getenv("FORZA_TRACK_SPAWN")) {
    REXLOG_INFO("FM4TRACK: texture user slots: {} (from {:08X})", ctx.r4.u32, uint32_t(ctx.lr));
  }
  __imp__sub_82E23B50(ctx, base);
}

// The streaming resource table (sub_82E2E890: r3 the manager, r4 the resource id; 12-byte
// entries at +16, their count at +52): log ids past the table, for FORZA_TRACK_SPAWN runs.
extern "C" REX_FUNC(__imp__sub_82E2E890);
extern "C" REX_FUNC(sub_82E2E890) {
  static std::atomic<int> logged{0};
  if (std::getenv("FORZA_TRACK_SPAWN")) {
    const uint32_t m = ctx.r3.u32, id = ctx.r4.u32, n = Load32(base, m + 52);
    if ((id >= n || logged.load() < 3) && logged.fetch_add(1) < 12) {
      REXLOG_INFO("FM4TRACK: resource {} of {} (table {:08X}) r5 {:08X} r6 {:08X} r7 {:08X} "
                  "from {:08X}", id, n, Load32(base, m + 16), ctx.r5.u32, ctx.r6.u32, ctx.r7.u32,
                  uint32_t(ctx.lr));
    }
  }
  __imp__sub_82E2E890(ctx, base);
}



// A track archive file being streamed (sub_82EB6960: r5 its number in the archive): which.
extern "C" REX_FUNC(__imp__sub_82EB6960);
extern "C" REX_FUNC(sub_82EB6960) {
  static std::atomic<int> logged{0};
  if (std::getenv("FORZA_TRACK_SPAWN") && GuestReadable(ctx.r7.u32) &&
      Load32(base, ctx.r7.u32) == 0xBEBEBEBE) {
    REXLOG_ERROR("FM4TRACK: stream file {} '{}' with an unset descriptor {:08X}", ctx.r5.u32,
                 CString(base, ctx.r6.u32), ctx.r7.u32);
  }
  static std::atomic<int> totals[3];
  if (std::getenv("FORZA_TRACK_SPAWN")) {
    const std::string name = CString(base, ctx.r6.u32);
    const int kind = name.find(".rmb.bin") != std::string::npos ? 0
                     : name.find(".bix") != std::string::npos ? 1 : 2;
    const int n = totals[kind].fetch_add(1) + 1;
    if ((n & (n - 1)) == 0) {  // 1, 2, 4, 8...
      REXLOG_INFO("FM4TRACK: streamed {} {} so far (last '{}')", n,
                  kind == 0 ? "models" : kind == 1 ? "textures" : "other files", name);
    }
  }
  static const bool every = std::getenv("FORZA_STREAM_LOG") != nullptr;  // every file
  if (every) REXLOG_INFO("FM4STREAM: {} {}", ctx.r5.u32, CString(base, ctx.r6.u32));
  if (std::getenv("FORZA_TRACK_SPAWN") && logged.fetch_add(1) < 5) {
    REXLOG_INFO("FM4TRACK: stream file {} r4 {:08X} r6 '{}' r7 {:08X} r8 {:08X} from {:08X}",
                ctx.r5.u32, ctx.r4.u32, CString(base, ctx.r6.u32), ctx.r7.u32, ctx.r8.u32,
                uint32_t(ctx.lr));
  }
  __imp__sub_82EB6960(ctx, base);
}

// FM4's static texture pack (<track>.stx.bin, a CAFF archive): FH1 takes its last texture-list
// symbol as the texture numbers and the one before as the textures; FM4's pack has only the
// textures, one per texture file of the track (the file numbers of the PVS's texture records).
// FORZA_FM4_STX=<track>|<file> gives, while that pack is read (sub_82E323A0), the textures
// (sub_82D09648) from FM4's last symbol and the numbers (sub_82D099E0) from <file>: a u32
// texture record number per pack entry, -1 for none (tools/fm4_pvs.py writes it).
extern "C" REX_FUNC(__imp__sub_82E323A0);
extern "C" REX_FUNC(__imp__sub_82D099E0);
extern "C" REX_FUNC(__imp__sub_82D09648);
extern "C" REX_FUNC(__imp__sub_82D095C0);
namespace {
struct Fm4Stx {
  std::string track;
  std::vector<uint32_t> ids;
  // Per texture record: the pack entry an FM4 atlas tile is drawn from, -1 for none
  // (<track>_stx_files.bin next to the numbers file; tools/fm4_pvs.py).
  std::vector<uint32_t> atlas_of;
  // <track>_stx_tiles.bin: per model (the game's model numbers), its slots: pack entry (-1:
  // not a tile; top bit: a streamed tile) and UV rectangle. Empty for a model with no tile.
  struct TileModel { std::vector<uint32_t> entry; std::vector<uint32_t> rect; };
  std::vector<TileModel> tile_models;
  uint32_t tiled_models = 0;
};
const Fm4Stx& Fm4StxConfig() {
  static const Fm4Stx c = [] {
    Fm4Stx c;
    const char* p = std::getenv("FORZA_FM4_STX");
    if (!p) return c;
    const std::string v = p;
    const auto bar = v.find('|');
    if (bar == std::string::npos) return c;
    for (char ch : v.substr(0, bar)) c.track += char(std::tolower(static_cast<unsigned char>(ch)));
    if (std::FILE* f = std::fopen(v.substr(bar + 1).c_str(), "rb")) {
      uint8_t b[4];
      while (std::fread(b, 1, 4, f) == 4) {
        c.ids.push_back(uint32_t(b[0]) << 24 | b[1] << 16 | b[2] << 8 | b[3]);
      }
      std::fclose(f);
    }
    std::string files = v.substr(bar + 1);
    const auto at = files.rfind("_stx_numbers.bin");
    if (at != std::string::npos) {
      files.replace(at, std::strlen("_stx_numbers.bin"), "_stx_files.bin");
      if (std::FILE* f = std::fopen(files.c_str(), "rb")) {
        uint8_t b[4];
        while (std::fread(b, 1, 4, f) == 4) {
          c.atlas_of.push_back(uint32_t(b[0]) << 24 | b[1] << 16 | b[2] << 8 | b[3]);
        }
        std::fclose(f);
      }
    }
    std::string tiles = v.substr(bar + 1);
    const auto tat = tiles.rfind("_stx_numbers.bin");
    if (tat != std::string::npos) {
      tiles.replace(tat, std::strlen("_stx_numbers.bin"), "_stx_tiles.bin");
      if (std::FILE* f = std::fopen(tiles.c_str(), "rb")) {
        std::vector<uint32_t> w;
        uint8_t b[4];
        while (std::fread(b, 1, 4, f) == 4) w.push_back(uint32_t(b[0]) << 24 | b[1] << 16 | b[2] << 8 | b[3]);
        std::fclose(f);
        size_t p = 0;
        const uint32_t nm = p < w.size() ? w[p++] : 0;
        for (uint32_t m = 0; m < nm && p < w.size(); ++m) {
          Fm4Stx::TileModel tm;
          const uint32_t cnt = w[p++];
          for (uint32_t j = 0; j < cnt && p + 4 < w.size(); ++j) {
            tm.entry.push_back(w[p++]);
            for (int q = 0; q < 4; ++q) tm.rect.push_back(w[p++]);
          }
          if (!tm.entry.empty()) ++c.tiled_models;
          c.tile_models.push_back(std::move(tm));
        }
      }
    }
    REXLOG_INFO("FM4TRACK: {} static texture pack: {} texture numbers, {} records, {} models with atlas tiles",
                c.track, c.ids.size(), c.atlas_of.size(), c.tiled_models);
    return c;
  }();
  return c;
}
// FM4's atlas tiles (see Fm4Stx::atlas_of): each such record's texture object becomes its
// atlas's (the pack entry's record's), before the objects' user lists are made (sub_82E24248),
// so the tiles' draws get the atlas's texture when it is there.
// A test only (FORZA_FM4_ATLAS_ALIAS=1): with the objects shared, the per-model texture
// binding (sub_82E18FC8's callee, lr 82E18E80) read a null pointer at +0xC soon after the
// track loaded. The tiles need the atlas's texture in their own objects instead.
void DumpTexObjects(uint8_t* base, uint32_t t, const char* when) {
  const Fm4Stx& c = Fm4StxConfig();
  const uint32_t texs = Load32(base, t + 2812), n = Load32(base, t + 2816);
  if (c.atlas_of.size() != n || !texs) return;
  int shown = 0;
  for (uint32_t i = 0; i < n && shown < 3; ++i) {
    const uint32_t k = c.atlas_of[i];
    if (k >= c.ids.size() || c.ids[k] >= n) continue;
    ++shown;
    for (uint32_t r : {c.ids[k], i}) {
      const uint32_t obj = Load32(base, texs + 4 * r);
      std::string b;
      for (uint32_t o = 0; o < 100; o += 4) b += fmt::format(" {:08X}", GuestReadable(obj + o) ? Load32(base, obj + o) : 0);
      REXLOG_INFO("FM4TEX {} record {} (pack {}) object {:08X}:{}", when, r, k, obj, b);
      for (uint32_t o = 0; o < 96; o += 4) {
        const uint32_t p = GuestReadable(obj + o) ? Load32(base, obj + o) : 0;
        if (p < 0x10000 || (p >> 24) == 0x82 || !GuestReadable(p) || !GuestReadable(p + 60)) continue;
        std::string q;
        for (uint32_t u = 0; u < 64; u += 4) q += fmt::format(" {:08X}", Load32(base, p + u));
        REXLOG_INFO("FM4TEX   +{:02X} -> {:08X}:{}", o, p, q);
      }
    }
  }
}

void DumpTexObjectsLive(uint8_t* base, uint32_t t) {
  DumpTexObjects(base, t, "zone live");
  // The presentation round its texture object table (+2812): pointers and counts, and for
  // each pointer to an array, its first entries and the entries of a pack record and a tile.
  const Fm4Stx& c = Fm4StxConfig();
  uint32_t pack = 0xFFFFFFFF, tile = 0xFFFFFFFF;
  for (uint32_t i = 0; i < c.atlas_of.size(); ++i) {
    const uint32_t k = c.atlas_of[i];
    if (k < c.ids.size() && c.ids[k] != i) { pack = c.ids[k]; tile = i; break; }
  }
  for (uint32_t o = 2700; o < 2960; o += 4) {
    const uint32_t v = Load32(base, t + o);
    std::string q;
    if (v > 0x10000 && GuestReadable(v) && GuestReadable(v + 4 * std::max(pack, tile) + 4) && pack != 0xFFFFFFFF) {
      for (uint32_t u = 0; u < 16; u += 4) q += fmt::format(" {:08X}", Load32(base, v + u));
      q += fmt::format(" | [pack {}] {:08X} [tile {}] {:08X}", pack, Load32(base, v + 4 * pack), tile,
                       Load32(base, v + 4 * tile));
    }
    REXLOG_INFO("FM4PRES +{} = {:08X}{}", o, v, q);
  }
}

// FM4's atlas tiles get their atlas's texture: a texture object's +0x1C is the id of the
// streaming resource that holds its texture (a streamed record's file; -1 for a record that
// no pack entry names). The pack loop registers each entry's texture under the id the numbers
// list gives it (sub_82E2EAD8 -> sub_82E2E890), so a tile's object takes its atlas entry's id:
// the tile keeps its own object and UV rectangle and draws from the atlas. (Registering the
// texture again under each tile's number overwrote the archive's own resources: models
// vanished.)
void AtlasTileResources(uint8_t* base, uint32_t t) {
  const Fm4Stx& c = Fm4StxConfig();
  const uint32_t texs = Load32(base, t + 2812), n = Load32(base, t + 2816);
  if (c.atlas_of.empty() || c.atlas_of.size() != n || !texs || !GuestReadable(texs)) return;
  uint32_t done = 0;
  // FM4_PVS_PACK_IDS=1 conversions: atlas_of holds the resource id itself (>= the record count).
  const bool ids_given = std::getenv("FORZA_FM4_PACK_IDS") != nullptr;
  for (uint32_t i = 0; i < n; ++i) {
    const uint32_t k = c.atlas_of[i];
    if (!ids_given && (k >= c.ids.size() || c.ids[k] >= n || c.ids[k] == i)) continue;
    if (ids_given && k == 0xFFFFFFFF) continue;
    const uint32_t obj = Load32(base, texs + 4 * i);
    if (!obj || !GuestReadable(obj + 0x1C) || Load32(base, obj + 0x1C) != 0xFFFFFFFF) continue;
    uint8_t* p = forza::GuestPtr(base, obj + 0x1C);
    const uint32_t id = ids_given ? k : c.ids[k];
    p[0] = uint8_t(id >> 24); p[1] = uint8_t(id >> 16); p[2] = uint8_t(id >> 8); p[3] = uint8_t(id);
    ++done;
  }
  REXLOG_INFO("FM4TRACK: {} atlas tiles draw from their atlas's texture", done);
}

void AliasAtlasTiles(uint8_t* base, uint32_t t) {
  if (std::getenv("FORZA_FM4_TEXDUMP")) DumpTexObjects(base, t, "before users");
  if (!std::getenv("FORZA_FM4_ATLAS_ALIAS")) {
    if (std::getenv("FORZA_FM4_ATLAS_IDS")) AtlasTileResources(base, t);  // a test
    return;
  }
  const Fm4Stx& c = Fm4StxConfig();
  const uint32_t texs = Load32(base, t + 2812), n = Load32(base, t + 2816);
  if (c.atlas_of.empty() || c.atlas_of.size() != n || !texs || !GuestReadable(texs)) return;
  uint32_t done = 0;
  for (uint32_t i = 0; i < n; ++i) {
    const uint32_t k = c.atlas_of[i];
    if (k >= c.ids.size() || c.ids[k] >= n || c.ids[k] == i) continue;
    const uint32_t obj = Load32(base, texs + 4 * c.ids[k]);
    if (!obj) continue;
    uint8_t* p = forza::GuestPtr(base, texs + 4 * i);
    p[0] = uint8_t(obj >> 24); p[1] = uint8_t(obj >> 16); p[2] = uint8_t(obj >> 8);
    p[3] = uint8_t(obj);
    ++done;
  }
  REXLOG_INFO("FM4TRACK: {} atlas tiles take their atlas's texture", done);
}
thread_local bool fm4_stx_active = false;
thread_local uint32_t fm4_stx_last = 0, fm4_stx_ids = 0;
}  // namespace

// The pack's loop (in sub_82E323A0): n = sub_82D095C0, numbers = sub_82D099E0 (the last
// data symbol), then n - 1 textures one symbol at a time from the end, sub_82D09648.
extern "C" REX_FUNC(sub_82E323A0) {
  const Fm4Stx& c = Fm4StxConfig();
  std::string path = CString(base, ctx.r5.u32);
  if (std::getenv("FORZA_TRACK_SPAWN")) {
    REXLOG_INFO("FM4TRACK: texture pack '{}' r7 {:08X} r8 {:08X}", path, ctx.r7.u32, ctx.r8.u32);
  }
  for (auto& ch : path) ch = char(std::tolower(static_cast<unsigned char>(ch)));
  fm4_stx_active =
      !c.ids.empty() && path.find("\\" + c.track + ".stx.bin") != std::string::npos;
  fm4_stx_last = fm4_stx_ids = 0;
  __imp__sub_82E323A0(ctx, base);
  fm4_stx_active = false;
  if (fm4_stx_ids) REX_KERNEL_MEMORY()->SystemHeapFree(fm4_stx_ids);
  fm4_stx_ids = 0;
}

void DumpCaff(uint8_t* base, uint32_t owner, const char* what) {
  const uint32_t it = Load32(base, owner + 4);
  if (!it || !GuestReadable(it)) return;
  const uint32_t hdr = Load32(base, it), syms = Load32(base, it + 36);
  REXLOG_INFO("FM4TRACK: CAFF {}: iterator {:08X} header {:08X} (+24 {} +28 {}) symbols {:08X} "
              "+88 {:08X} +92 {} +100 {:08X} +104 {}", what, it, hdr, Load32(base, hdr + 24),
              Load32(base, hdr + 28), syms, Load32(base, it + 88), Load32(base, it + 92),
              Load32(base, it + 100), *forza::GuestPtr(base, it + 104));
  const uint32_t n = Load32(base, hdr + 28);
  for (uint32_t i = n > 6 ? n - 6 : 0; i < n; ++i) {
    std::string b;
    for (int k = 0; k < 14; ++k) b += fmt::format("{:02X}", *forza::GuestPtr(base, syms + 14 * i + k));
    REXLOG_INFO("FM4TRACK: CAFF symbol {}: {}", i, b);
  }
}

extern "C" REX_FUNC(sub_82D099E0) {
  static std::atomic<int> dumps{0};
  if (std::getenv("FORZA_TRACK_SPAWN") && dumps.fetch_add(1) < 2) {
    DumpCaff(base, ctx.r3.u32, fm4_stx_active ? "FM4 pack" : "pack");
  }
  if (!fm4_stx_active) return __imp__sub_82D099E0(ctx, base);
  __imp__sub_82D099E0(ctx, base);
  // FM4's last data symbol is a 4-byte one (number 564 in Suzuka's pack) where FH1 has its
  // numbers list; the textures are the 116-byte symbols before it, numbered from 1 (texture
  // file k is symbol k + 1). It is dropped; the textures come from sub_82D09648 as for FH1.
  const auto& ids = Fm4StxConfig().ids;
  fm4_stx_ids = REX_KERNEL_MEMORY()->SystemHeapAlloc(uint32_t(4 * ids.size() + 4));
  for (size_t i = 0; i < ids.size(); ++i) {
    uint8_t* p = forza::GuestPtr(base, fm4_stx_ids + uint32_t(4 * i));
    p[0] = uint8_t(ids[i] >> 24);
    p[1] = uint8_t(ids[i] >> 16);
    p[2] = uint8_t(ids[i] >> 8);
    p[3] = uint8_t(ids[i]);
  }
  ctx.r3.u64 = fm4_stx_ids;
}

extern "C" REX_FUNC(sub_82D09648) { __imp__sub_82D09648(ctx, base); }

// FM4's atlas tiles (FORZA_FM4_ATLAS_FILL=1, a test): a model's draw takes each texture slot's
// texture from the model's texture table (the object at draw setup +1192: +8 the instance,
// entries at +32, count +36, 32 bytes each: +0 the texture header, +16..+28 the UV rectangle).
// FH1 fills +0 when a slot's record has a texture (sub_82E18E78); an atlas tile's never does,
// so its draws take a stand-in (Laguna Seca's black road). The pack loop registers each
// entry's texture in the streaming resource table (sub_82E2EAD8: r3 the manager, r4 the id;
// manager +16: 12-byte entries, +0 the 52-byte header); those headers are kept per pack entry
// here, and before a model's draw is set up its empty tile slots get their atlas's header
// (their rectangle is the tile's already).
std::vector<uint32_t> fm4_pack_headers;
extern "C" REX_FUNC(__imp__sub_82E2EAD8);
extern "C" REX_FUNC(sub_82E2EAD8) {
  const uint32_t mgr = ctx.r3.u32, id = ctx.r4.u32;
  fm4_resource_manager = mgr;
  __imp__sub_82E2EAD8(ctx, base);
  if (!fm4_stx_active) return;
  const Fm4Stx& c = Fm4StxConfig();
  if (fm4_pack_headers.size() != c.ids.size()) fm4_pack_headers.assign(c.ids.size(), 0);
  const uint32_t table = Load32(base, mgr + 16);
  if (!table) return;
  const uint32_t header = Load32(base, table + 12 * id);
  for (uint32_t k = 0; k < c.ids.size(); ++k)
    if (c.ids[k] == id) { fm4_pack_headers[k] = header; break; }
}

static inline uint8_t base_byte(uint8_t* base, uint32_t a) { return *forza::GuestPtr(base, a); }
static uint32_t tm_entry_size(const Fm4Stx::TileModel* tm) { return tm ? uint32_t(tm->entry.size()) : 0; }
static void FillAtlasTiles(uint8_t* base, uint32_t tbl) {
  const Fm4Stx& c = Fm4StxConfig();
  if (!tbl || c.tile_models.empty() || fm4_pack_headers.empty()) return;
  const uint32_t entries = Load32(base, tbl + 32), count = Load32(base, tbl + 36);
  if (!entries || !count || count > 32) return;
  uint32_t rect[4 * 32];
  for (uint32_t j = 0; j < count; ++j)
    for (uint32_t q = 0; q < 4; ++q) rect[4 * j + q] = Load32(base, entries + 32 * j + 16 + 4 * q);
  // The table's model: its +4 points at the model's record (presentation +55368, 84 bytes
  // each, the count at +55372's high half); the rectangles must agree with the converter's.
  const Fm4Stx::TileModel* tm = nullptr;
  bool skip[32] = {};
  const uint32_t pres = fm4_presentation.load();
  const uint32_t models = pres ? Load32(base, pres + 55368) : 0, nm = pres ? Load32(base, pres + 55372) >> 16 : 0;
  const uint32_t rec = Load32(base, tbl + 4);
  if (models && rec >= models && (rec - models) % 84 == 0 && (rec - models) / 84 < nm &&
      (rec - models) / 84 < c.tile_models.size()) {
    const Fm4Stx::TileModel& cand = c.tile_models[(rec - models) / 84];
    const uint32_t n = std::min<uint32_t>(count, uint32_t(cand.entry.size()));
    // A streamed tile's slot (top bit) gets the identity rectangle from the game once its own
    // file (the tile alone) is in: that agrees too.
    static const uint32_t identity[4] = {0x3F800000, 0x3F800000, 0, 0};
    auto agrees = [&](uint32_t j) {
      return std::memcmp(&cand.rect[4 * j], &rect[4 * j], 16) == 0 ||
             (cand.entry[j] != 0xFFFFFFFF && (cand.entry[j] & 0x80000000) &&
              std::memcmp(identity, &rect[4 * j], 16) == 0);
    };
    bool all = n > 0;
    for (uint32_t j = 0; j < n; ++j) {
      skip[j] = !agrees(j);
      if (skip[j]) all = false;
    }
    if (n) tm = &cand;  // the model is certain from +4: slots that disagree are left alone
    if (!all && n) {
      static std::atomic<int> told{0};
      if (told.fetch_add(1) < 5) {
        std::string d;
        for (uint32_t j = 0; j < n; ++j)
          if (!agrees(j))
            d += fmt::format(" slot {} entry {:08X}: {:08X} {:08X} {:08X} {:08X} here, {:08X} {:08X} {:08X} {:08X} converted;",
                             j, cand.entry[j], rect[4 * j], rect[4 * j + 1], rect[4 * j + 2], rect[4 * j + 3],
                             cand.rect[4 * j], cand.rect[4 * j + 1], cand.rect[4 * j + 2], cand.rect[4 * j + 3]);
        REXLOG_INFO("FM4TRACK: model {}'s texture table {:08X} ({} slots, {} converted): slots with other rectangles left alone:{}",
                    (rec - models) / 84, tbl, count, cand.entry.size(), d);
      }
    }
  }
  static const bool diag = std::getenv("FORZA_FM4_FILLDIAG") != nullptr;
  if (diag) {
    // Tables with a slot of the road's tile size (1/128 by 1/256): matched or not, every slot.
    static std::mutex dm;
    static std::set<uint32_t> shown;
    bool road = false;
    for (uint32_t j = 0; j < count; ++j)
      for (uint32_t q = 0; q + 1 < 4; ++q)
        if (rect[4 * j + q] == 0x3C000000 && rect[4 * j + q + 1] == 0x3B800000) road = true;
    std::lock_guard<std::mutex> lock(dm);
    if (road && shown.size() < 3 && shown.insert(tbl).second) {
      std::string s;
      for (uint32_t j = 0; j < count; ++j)
        s += fmt::format(" <{}> {:08X} {:08X} | {:08X} {:08X} {:08X} {:08X}", j, Load32(base, entries + 32 * j),
                         Load32(base, entries + 32 * j + 4), rect[4 * j], rect[4 * j + 1], rect[4 * j + 2], rect[4 * j + 3]);
      for (uint32_t j = 0; j < count; ++j) {
        const uint32_t hp = Load32(base, entries + 32 * j);
        const uint32_t k = j < tm_entry_size(tm) ? tm->entry[j] & 0x7FFFFFFF : 0xFFFFFFFF;
        const uint32_t ph = k < fm4_pack_headers.size() ? fm4_pack_headers[k] : hp;
        if (!ph) continue;
        std::string w;
        for (uint32_t o = 0; o < 52; o += 4) w += fmt::format(" {:08X}", Load32(base, ph + o));
        REXLOG_INFO("FM4FILLDIAG   slot {} header {:08X}:{}", j, ph, w);
      }
      REXLOG_INFO("FM4FILLDIAG table {:08X} head {:08X} {:08X} {:08X} matched {} count {}:{}", tbl, Load32(base, tbl),
                  Load32(base, tbl + 4), Load32(base, tbl + 8), tm ? 1 : 0, count, s);
    }
  }
  if (tm && diag) {
    static std::mutex mm;
    static std::set<uint32_t> told;
    std::lock_guard<std::mutex> lock(mm);
    if (told.size() < 60 && told.insert(tbl).second) {
      // What the table's +4 points at: a model record (presentation +55368, 84 bytes each),
      // an instance object (+2556), or something else; and the words there.
      const uint32_t pres = fm4_presentation.load(), p4 = Load32(base, tbl + 4);
      const uint32_t models = pres ? Load32(base, pres + 55368) : 0, nm = pres ? Load32(base, pres + 55372) >> 16 : 0;
      const uint32_t insts = pres ? Load32(base, pres + 2556) : 0, ni = pres ? Load32(base, pres + 2560) : 0;
      int64_t inst = -1;
      for (uint32_t i = 0; i < ni && i < 100000; ++i) if (Load32(base, insts + 4 * i) == p4) { inst = i; break; }
      std::string w;
      for (uint32_t o = 0; o < 32; o += 4) w += fmt::format(" {:08X}", Load32(base, p4 + o));
      REXLOG_INFO("FM4MATCH table {:08X} +8 {} row {} count {} | +4 {:08X} model rec {} rem {} (of {}) inst obj {}:{}",
                  tbl, Load32(base, tbl + 8), uint32_t(tm - c.tile_models.data()), count, p4,
                  models && p4 >= models ? int64_t((p4 - models) / 84) : -1,
                  models && p4 >= models ? int64_t((p4 - models) % 84) : -1, nm, inst, w);
    }
  }
  if (diag && models && rec >= models && (rec - models) % 84 == 0) {
    // The tables seen; 30 s after the first, a host thread looks at every streamed record's
    // slot: applied (the slot points into its texture object) or stuck, with samples.
    static std::mutex tm_mu;
    static std::vector<std::pair<uint32_t, uint32_t>> seen_tables;
    static std::set<uint32_t> seen_set;
    static bool started = false;
    {
      std::lock_guard<std::mutex> lock(tm_mu);
      if (seen_set.insert(tbl).second) seen_tables.emplace_back(tbl, (rec - models) / 84);
      if (!started) {
        started = true;
        if (const char* w = std::getenv("FORZA_FM4_OBJWATCH")) {
          // Records to watch (comma list): their texture objects' +8 state word and +0x2C..+0x30,
          // every change with a timestamp; guest call chains at the first change of each.
          std::vector<uint32_t> recs;
          for (const char* p = w; *p;) { recs.push_back(uint32_t(std::strtoul(p, const_cast<char**>(&p), 10))); if (*p == ',') ++p; else break; }
          std::thread([base, pres, recs] {
            const auto t0 = std::chrono::steady_clock::now();
            const uint32_t texs = Load32(base, pres + 2812);
            std::vector<uint32_t> objs, last(recs.size(), 0xDEADBEEF), last2(recs.size(), 0xDEADBEEF);
            std::vector<bool> chained(recs.size(), false);
            for (uint32_t r : recs) objs.push_back(Load32(base, texs + 4 * r));
            for (size_t k = 0; k < objs.size(); ++k) {
              // The users list: +0x10 high half the count, +0x14 the list (6 bytes each?).
              const uint32_t o = objs[k], n = Load32(base, o + 0x10) >> 16, lst = Load32(base, o + 0x14);
              std::string s;
              for (uint32_t i = 0; i < n && i < 24; ++i) {
                s += fmt::format(" [{:04X} {:08X}]", uint32_t(*forza::GuestPtr(base, lst + 6 * i)) << 8 | *forza::GuestPtr(base, lst + 6 * i + 1),
                                 Load32(base, lst + 6 * i + 2));
              }
              std::string raw;
              for (uint32_t i = 0; i < 48; i += 4) raw += fmt::format(" {:08X}", Load32(base, lst + i));
              REXLOG_INFO("FM4WATCH record {} users {} at {:08X}:{} | raw{}", recs[k], n, lst, s, raw);
              if (k == 0) {
                const uint32_t vt = Load32(base, o);
                std::string v;
                for (uint32_t i = 0; i < 12; ++i) v += fmt::format(" {:08X}", Load32(base, vt + 4 * i));
                REXLOG_INFO("FM4WATCH texture object vtable {:08X}:{}", vt, v);
              }
            }
            for (int i = 0; i < 2000000; ++i) {
              for (size_t k = 0; k < objs.size(); ++k) {
                const uint32_t v = Load32(base, objs[k] + 8), v2 = Load32(base, objs[k] + 0x2C);
                if (v != last[k] || v2 != last2[k]) {
                  const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
                  REXLOG_INFO("FM4WATCH {} ms record {} object {:08X}: +8 {:08X} -> {:08X}, +2C {:08X} -> {:08X}, +10 {:08X} +1C {:08X}",
                              ms, recs[k], objs[k], last[k], v, last2[k], v2, Load32(base, objs[k] + 0x10), Load32(base, objs[k] + 0x1C));
                  static int snaps = 0;
                  if (k == 0 && last[k] != 0xDEADBEEF && snaps < 20) { ++snaps; forza::LogGuestThreads("at the watched object's change"); }
                  last[k] = v; last2[k] = v2;
                }
              }
              std::this_thread::sleep_for(std::chrono::microseconds(50));
            }
          }).detach();
        }
        std::thread([base, pres, models] {
          for (int pass = 0; pass < 3; ++pass) {
          std::this_thread::sleep_for(std::chrono::seconds(30));
          std::vector<std::pair<uint32_t, uint32_t>> tabs;
          { std::lock_guard<std::mutex> lock(tm_mu); tabs = seen_tables; }
          const uint32_t texs = Load32(base, pres + 2812), nt = Load32(base, pres + 2816);
          uint32_t applied = 0, stuck = 0, other = 0;
          uint64_t texels = 0, bytes = 0;
          std::set<uint32_t> applied_set, stuck_set;
          std::set<uint32_t> applied_models, stuck_models, applied_rids, stuck_rids, other_rids;
          int shown_a = 0, shown_s = 0;
          for (auto [tb, m] : tabs) {
            const uint32_t entries = Load32(base, tb + 32), count = Load32(base, tb + 36);
            const uint32_t mr = models + 84 * m, list = Load32(base, mr + 60), nl = Load32(base, mr + 64);
            for (uint32_t j = 0; j < count && j < nl && j < 32; ++j) {
              const uint32_t ri = Load32(base, list + 4 * j) & 0xFFFFFF;
              const uint32_t obj = ri < nt ? Load32(base, texs + 4 * ri) : 0;
              if (!obj || (Load32(base, obj + 0xC) & 0x4) == 0) continue;
              const uint32_t hp = Load32(base, entries + 32 * j);
              const bool ok = hp >= obj && hp < obj + 0x80;
              const uint32_t d2 = hp ? Load32(base, hp + 0x24) : 0;
              const bool small = hp && (d2 & 0x1FFF) + 1 <= 32 && ((d2 >> 13) & 0x1FFF) + 1 <= 32;
              if (ok) ++applied; else if (small) ++stuck; else ++other;
              if (ok) applied_models.insert(m); else if (small) { stuck_models.insert(m); stuck_set.insert(obj); }
              (ok ? applied_rids : small ? stuck_rids : other_rids).insert(Load32(base, obj + 0x1C));
              {
                // Resource state of the record's own file, by class; applied texels.
                const uint32_t rid = Load32(base, obj + 0x1C);
                const uint32_t mgr = fm4_resource_manager.load(), rt = mgr ? Load32(base, mgr + 16) : 0;
                const uint32_t r0 = rt && rid < 0x100000 ? Load32(base, rt + 12 * rid) : 0;
                const uint32_t r2 = rt && rid < 0x100000 ? Load32(base, rt + 12 * rid + 8) : 0;
                static thread_local std::map<std::string, uint32_t>* states = nullptr;
                if (!states || (j == 0 && tb == tabs.front().first)) { delete states; states = new std::map<std::string, uint32_t>; }
                const char* cls = ok ? "applied" : small ? "stuck" : "other";
                const char* st = !r0 ? "empty" : r2 == 0x105FFFFF ? "loaded" : (r0 >> 24) >= 0x2E ? "header" : "pending";
                (*states)[std::string(cls) + "/" + st]++;
                if (ok && applied_set.insert(obj).second) {
                  const uint64_t n = uint64_t((d2 & 0x1FFF) + 1) * (((d2 >> 13) & 0x1FFF) + 1);
                  texels += n;
                  const uint32_t fmt = Load32(base, hp + 0x20) & 0x3F;  // DXT1 (0x12) half a byte a texel
                  bytes += fmt == 0x12 ? n / 2 : fmt == 0x13 || fmt == 0x14 ? n : n * 4;
                }
                if (tb == tabs.back().first && j + 1 == std::min<uint32_t>(std::min(count, nl), 32)) {
                  std::string s;
                  for (auto& [k, v] : *states) s += fmt::format(" {}={}", k, v);
                  REXLOG_INFO("FM4OBJ states:{} applied texels {}", s, texels);
                }
              }
              if ((ok && shown_a < 4) || (!ok && small && shown_s < 6)) {
                (ok ? shown_a : shown_s)++;
                std::string w;
                for (uint32_t o = 0; o < 0x60; o += 4) w += fmt::format(" {:08X}", Load32(base, obj + o));
                REXLOG_INFO("FM4OBJ {} model {} slot {} record {} header {:08X} {}x{} object {:08X}:{}", ok ? "applied" : "stuck",
                            m, j, ri, hp, (d2 & 0x1FFF) + 1, ((d2 >> 13) & 0x1FFF) + 1, obj, w);
              }
            }
          }
          REXLOG_INFO("FM4OBJ after {} s: {} tables, streamed slots applied {} ({} textures, {} texels, {} KB), stuck small {} ({} textures), other {}", 30 * (pass + 1), tabs.size(),
                      applied, applied_set.size(), texels, bytes / 1024, stuck, stuck_set.size(), other);
          for (int k = 0; k < 2; ++k) {
            std::string s;
            for (uint32_t m : (k ? stuck_models : applied_models)) s += fmt::format(" {}", m);
            REXLOG_INFO("FM4OBJ {} models:{}", k ? "stuck" : "applied", s);
          }
          if (pass == 0) {
            const uint32_t mgr = fm4_resource_manager.load();
            std::string s;
            for (uint32_t o = 0; o < 64 && mgr; o += 4) s += fmt::format(" {:08X}", Load32(base, mgr + o));
            REXLOG_INFO("FM4RES manager {:08X}:{}", mgr, s);
            const uint32_t rt = mgr ? Load32(base, mgr + 16) : 0;
            for (uint32_t id : {100u, 1000u, 2000u, 3000u, 3150u, 3158u, 3159u, 3160u, 3161u, 3162u, 3165u, 3200u, 4000u, 5000u, 5229u, 5300u, 7000u, 7109u}) {
              REXLOG_INFO("FM4RES entry {}: {:08X} {:08X} {:08X}", id, Load32(base, rt + 12 * id), Load32(base, rt + 12 * id + 4),
                          Load32(base, rt + 12 * id + 8));
            }
          }
          for (int k = 0; k < 3; ++k) {
            std::string s;
            for (uint32_t r : (k == 0 ? applied_rids : k == 1 ? stuck_rids : other_rids)) s += fmt::format(" {}", int32_t(r));
            REXLOG_INFO("FM4OBJ {} resource ids:{}", k == 0 ? "applied" : k == 1 ? "stuck" : "other", s);
          }
          }
        }).detach();
      }
    }
    // Slots whose texture is 32 x 32 or smaller (the pack's resident mips): model, slot, size;
    // from 25 s after the first draw (once streaming has had its time).
    static std::mutex sm;
    static std::set<uint64_t> told;
    static const auto t0 = std::chrono::steady_clock::now();
    {
      // Calls and small slots per 5 s window.
      static std::atomic<uint32_t> calls{0}, smalls{0}, window{0};
      const uint32_t w = uint32_t(std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now() - t0).count() / 5);
      uint32_t s = 0;
      for (uint32_t j = 0; j < count; ++j) {
        const uint32_t hp = Load32(base, entries + 32 * j);
        if (hp && ((Load32(base, hp + 0x24) & 0x1FFF) + 1) <= 32) ++s;
      }
      if (window.exchange(w) != w)
        REXLOG_INFO("FM4BINDS window {}: {} binder calls, {} small slots", w, calls.exchange(0), smalls.exchange(0));
      calls.fetch_add(1); smalls.fetch_add(s);
    }
    if (std::chrono::steady_clock::now() - t0 < std::chrono::seconds(25)) goto small_done;
    for (uint32_t j = 0; j < count; ++j) {
      const uint32_t hp = Load32(base, entries + 32 * j);
      if (!hp) continue;
      const uint32_t d2 = Load32(base, hp + 0x24), w = (d2 & 0x1FFF) + 1, hh = ((d2 >> 13) & 0x1FFF) + 1;
      if (w > 32 || hh > 32) continue;
      std::lock_guard<std::mutex> lock(sm);
      if (told.size() < 400 && told.insert(uint64_t((rec - models) / 84) << 8 | j).second)
      {
        // The slot's record (the model's texture list, presentation +55368 record +60) and
        // its texture object (+2812): flags, resource id, and that resource's entry.
        const uint32_t mr = models + 84 * ((rec - models) / 84);
        const uint32_t list = Load32(base, mr + 60), nl = Load32(base, mr + 64);
        const uint32_t ri = j < nl ? Load32(base, list + 4 * j) & 0xFFFFFF : 0xFFFFFFFF;
        const uint32_t texs = Load32(base, pres + 2812), nt = Load32(base, pres + 2816);
        const uint32_t obj = ri < nt ? Load32(base, texs + 4 * ri) : 0;
        const uint32_t rid = obj ? Load32(base, obj + 0x1C) : 0xFFFFFFFF;
        const uint32_t mgr = fm4_resource_manager.load(), rt = mgr ? Load32(base, mgr + 16) : 0;
        const bool ok = rt && rid < 0x100000;
        REXLOG_INFO("FM4SMALL model {} slot {} {}x{} header {:08X} record {} object {:08X} +C {:08X} +1C {} res {:08X} {:08X} {:08X}",
                    (rec - models) / 84, j, w, hh, hp, ri, obj, obj ? Load32(base, obj + 0xC) : 0, int32_t(rid),
                    ok ? Load32(base, rt + 12 * rid) : 0, ok ? Load32(base, rt + 12 * rid + 4) : 0,
                    ok ? Load32(base, rt + 12 * rid + 8) : 0);
      }
    }
  small_done:;
  }
  if (!tm) return;
  static std::atomic<uint32_t> filled{0};
  for (uint32_t j = 0; j < tm->entry.size() && j < count; ++j) {
    if (tm->entry[j] == 0xFFFFFFFF || skip[j]) continue;
    const uint32_t k = tm->entry[j] & 0x7FFFFFFF;
    if (k >= fm4_pack_headers.size() || !fm4_pack_headers[k]) continue;
    const uint32_t h = fm4_pack_headers[k], now = Load32(base, entries + 32 * j);
    if (now == h) continue;  // a tile slot may hold a stand-in, not 0
    if ((tm->entry[j] & 0x80000000) && now) continue;  // a streamed tile's own texture
    uint8_t* p = forza::GuestPtr(base, entries + 32 * j);
    p[0] = uint8_t(h >> 24); p[1] = uint8_t(h >> 16); p[2] = uint8_t(h >> 8); p[3] = uint8_t(h);
    const uint32_t n = filled.fetch_add(1) + 1;
    if ((n & (n - 1)) == 0)
      REXLOG_INFO("FM4TRACK: {} atlas tile slots given their atlas's texture (slot {} of {}, pack entry {}, header {:08X})",
                  n, j, count, k, h);
  }
}

// FORZA_FM4_FILLDIAG: the binder's slot 1 (sub_82EAF1B0: r4 the model texture table, r5/r7 the
// material, r9 the 800-byte block) for tables holding a road-sized atlas tile: the material's
// sampler map (texture table slot per sampler) and the block's textures and flags.
extern "C" REX_FUNC(__imp__sub_82EAF1B0);
extern "C" REX_FUNC(sub_82EAF1B0) {
  const uint32_t tbl = ctx.r4.u32, r5 = ctx.r5.u32, r7 = ctx.r7.u32, blk = ctx.r9.u32;
  // The streaming bind loop (sub_82E2B8F8, through sub_82E18E78) rewrites the table's textures
  // every frame, a tile slot back to null, so tiles get their atlas just before the copy here.
  static const bool fill = [] {
    const char* v = std::getenv("FORZA_FM4_ATLAS_FILL");
    return !v || std::string_view(v) != "0";
  }();
  if (fill) FillAtlasTiles(base, tbl);
  __imp__sub_82EAF1B0(ctx, base);
  static const bool diag = std::getenv("FORZA_FM4_FILLDIAG") != nullptr;
  if (!diag || !tbl || !blk) return;
  const uint32_t entries = Load32(base, tbl + 32), count = Load32(base, tbl + 36);
  if (!entries || !count || count > 32) return;
  {
    // Null textures a sampler still maps to after the fill: which, and their rectangles.
    static std::mutex nm;
    static std::set<uint64_t> seen;
    const uint32_t mat0 = Load32(base, Load32(base, r5 + 4)) + 56 * Load32(base, r7 + 40);
    const uint32_t a0 = Load32(base, mat0 + 40), a1 = Load32(base, mat0 + 44);
    for (uint32_t a = a0, i = 0; a < a1 && i < 20; a += 4, ++i) {
      const int32_t m = int32_t(Load32(base, a));
      if (m < 0 || uint32_t(m) >= count || Load32(base, entries + 32 * m)) continue;
      std::lock_guard<std::mutex> lock(nm);
      if (seen.size() < 40 && seen.insert((uint64_t(tbl) << 8) | uint32_t(m)).second) {
        std::string r;
        for (uint32_t j = 0; j < count; ++j)
          r += fmt::format(" <{}>{:08X} {:08X},{:08X},{:08X},{:08X}", j, Load32(base, entries + 32 * j),
                           Load32(base, entries + 32 * j + 16), Load32(base, entries + 32 * j + 20),
                           Load32(base, entries + 32 * j + 24), Load32(base, entries + 32 * j + 28));
        REXLOG_INFO("FM4NULL table {:08X} sampler {} slot {} of {}:{}", tbl, i, m, count, r);
      }
    }
  }
  bool road = false;
  for (uint32_t j = 0; j < count; ++j)
    if (Load32(base, entries + 32 * j + 16) == 0x3C000000 && Load32(base, entries + 32 * j + 20) == 0x3B800000) road = true;
  if (!road) return;
  static std::atomic<int> n{0};
  if (n.fetch_add(1) >= 8) return;
  const uint32_t mat = Load32(base, Load32(base, r5 + 4)) + 56 * Load32(base, r7 + 40);
  const uint32_t m0 = Load32(base, mat + 40), m1 = Load32(base, mat + 44);
  std::string s = fmt::format("FM4BIND table {:08X} count {} mat {:08X} map", tbl, count, mat);
  for (uint32_t a = m0; a < m1 && a < m0 + 80; a += 4) s += fmt::format(" {}", int32_t(Load32(base, a)));
  const uint32_t nb = Load32(base, blk + 28);
  s += fmt::format(" | block n {}:", nb);
  for (uint32_t i = 0; i < nb + 2 && i < 20; ++i)
    s += fmt::format(" {:08X}/{}{}", Load32(base, blk + 32 + 4 * i), int(base_byte(base, blk + 752 + i)), int(base_byte(base, blk + 772 + i)));
  REXLOG_INFO("{}", s);
}

// FORZA_FM4_TEXDUMP: the track model draw setup (sub_82D1B080) fills an 800-byte binding
// block through a virtual call on the object at +1188 (vtable slot 1), then applies it as
// command-buffer fixups (sub_8248C200: 9, 10; sub_8248B2D8: 3 textures, 8 UV offsets;
// sub_8248B5D0: buffers). Logs that object, its vtable and slot 1 once.
extern "C" REX_FUNC(__imp__sub_82D1B080);
extern "C" REX_FUNC(sub_82D1B080) {
  static std::atomic<int> logged{0};
  if (std::getenv("FORZA_FM4_TEXDUMP") && logged.fetch_add(1) < 3) {
    const uint32_t self = ctx.r3.u32, binder = Load32(base, self + 1188);
    const uint32_t vt = binder && GuestReadable(binder) ? Load32(base, binder) : 0;
    std::string slots;
    for (uint32_t k = 0; k < 8 && vt; ++k) slots += fmt::format(" {:08X}", Load32(base, vt + 4 * k));
    REXLOG_INFO("FM4TEX vtable {:08X}:{}", vt, slots);
    REXLOG_INFO("FM4TEX draw setup {:08X}: binder {:08X} vtable {:08X} slot1 {:08X} +1192 {:08X} r4 {:08X} r5 {:08X} r6 {:08X} r7 {:08X}",
                self, binder, vt, vt ? Load32(base, vt + 4) : 0,
                Load32(base, self + 1192), ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32);
  }
  if (std::getenv("FORZA_FM4_TEXWATCH")) {
    // A write watch: entries of model texture tables whose texture (+0) is still null; a host
    // thread polls them and logs every guest thread's call chain when one gets a texture.
    static std::mutex wm;
    static std::vector<uint32_t> watch;
    static std::set<uint32_t> tables;
    static bool started = false;
    const uint32_t tbl = Load32(base, ctx.r3.u32 + 1192);
    static std::atomic<int> seen{0};
    const int seen_n = seen.fetch_add(1);
    if (seen_n == 0) REXLOG_INFO("FM4TEX watch on: first table {:08X}", tbl);
    if (seen_n % 200 == 199) {
      std::lock_guard<std::mutex> lock(wm);
      REXLOG_INFO("FM4TEX watch: {} calls, {} tables, {} empty entries", seen_n + 1, tables.size(), watch.size());
    }
    if (tbl) {
      std::lock_guard<std::mutex> lock(wm);
      tables.insert(tbl);
      const uint32_t entries = Load32(base, tbl + 32), count = Load32(base, tbl + 36);
      static std::set<uint32_t> have;
      for (uint32_t j = 0; j < count && j < 20 && watch.size() < 400; ++j)
        if (entries && !Load32(base, entries + 32 * j) && have.insert(entries + 32 * j).second)
          watch.push_back(entries + 32 * j);
      if (!started && watch.size() >= 40) {
        started = true;
        std::vector<uint32_t> w = watch;
        REXLOG_INFO("FM4TEX watching {} empty texture entries", w.size());
        std::thread([w, base] {
          for (int i = 0; i < 30000000; ++i) {
            for (uint32_t a : w) {
              const uint32_t v = Load32(base, a);
              if (v) {
                REXLOG_INFO("FM4TEX watch: entry {:08X} got texture {:08X} (rect {:08X} {:08X} {:08X} {:08X})", a, v,
                            Load32(base, a + 16), Load32(base, a + 20), Load32(base, a + 24), Load32(base, a + 28));
                forza::LogGuestThreads("at the texture entry write");
                return;
              }
            }
            std::this_thread::sleep_for(std::chrono::microseconds(5));
          }
        }).detach();
      }
    }
  }
  if (std::getenv("FORZA_FM4_TEXDUMP")) {
    static std::atomic<int> tl{0};
    const uint32_t tbl = Load32(base, ctx.r3.u32 + 1192);
    if (tbl && GuestReadable(tbl + 40) && tl.fetch_add(1) % 500 == 0) {
      const Fm4Stx& c = Fm4StxConfig();
      const uint32_t entries = Load32(base, tbl + 32), count = Load32(base, tbl + 36);
      std::string q;
      int shown = 0;
      for (uint32_t i = 0; i < c.atlas_of.size() && shown < 3; ++i) {
        const uint32_t k = c.atlas_of[i];
        if (k >= c.ids.size() || c.ids[k] == i || c.ids[k] >= count || i >= count) continue;
        ++shown;
        for (uint32_t r : {c.ids[k], i}) {
          const uint32_t e = entries + 32 * r;
          q += fmt::format(" [{}] {:08X} {:08X} {:08X} {:08X} | {:08X} {:08X} {:08X} {:08X};", r, Load32(base, e),
                           Load32(base, e + 4), Load32(base, e + 8), Load32(base, e + 12), Load32(base, e + 16),
                           Load32(base, e + 20), Load32(base, e + 24), Load32(base, e + 28));
        }
      }
      for (uint32_t j = 0; j < count && j < 8; ++j) {
        const uint32_t e = entries + 32 * j;
        q += fmt::format(" <{}> {:08X} {:08X} {:08X} {:08X} {:08X} {:08X} {:08X} {:08X}", j, Load32(base, e),
                         Load32(base, e + 4), Load32(base, e + 8), Load32(base, e + 12), Load32(base, e + 16),
                         Load32(base, e + 20), Load32(base, e + 24), Load32(base, e + 28));
      }
      std::string head;
      for (uint32_t o = 0; o < 48; o += 4) head += fmt::format(" {:08X}", Load32(base, tbl + o));
      for (uint32_t o : {16u, 24u}) {
        const uint32_t p = Load32(base, tbl + o);
        if (!p || !GuestReadable(p + 32)) continue;
        head += fmt::format(" [+{} ->", o);
        for (uint32_t u = 0; u < 32; u += 4) head += fmt::format(" {:08X}", Load32(base, p + u));
        head += "]";
      }
      REXLOG_INFO("FM4TEX binder table {:08X}:{} | entries {:08X} count {}:{}", tbl, head, entries, count, q);
    }
  }
  __imp__sub_82D1B080(ctx, base);
}

// The pack loop also maps each entry's record to the entry (sub_82E66690(map, &record) gives
// the map's slot, the loop stores the entry's index k there). A test (FORZA_FM4_ATLAS_MAP=1):
// while FM4's pack is read, each atlas tile of the entry goes into the map with that k too.
extern "C" REX_FUNC(__imp__sub_82E66690);
extern "C" REX_FUNC(sub_82E66690) {
  static const bool on = std::getenv("FORZA_FM4_ATLAS_MAP") != nullptr;
  if (!on || !fm4_stx_active) return __imp__sub_82E66690(ctx, base);
  const uint32_t map = ctx.r3.u32, keyp = ctx.r4.u32, key = Load32(base, keyp);
  __imp__sub_82E66690(ctx, base);
  const uint64_t ret = ctx.r3.u64;
  const Fm4Stx& c = Fm4StxConfig();
  uint32_t k = 0xFFFFFFFF;
  for (uint32_t e = 0; e < c.ids.size(); ++e) if (c.ids[e] == key) { k = e; break; }
  if (k != 0xFFFFFFFF) {
    static uint32_t tmp = REX_KERNEL_MEMORY()->SystemHeapAlloc(4);
    uint32_t added = 0;
    for (uint32_t i = 0; i < c.atlas_of.size(); ++i) {
      if (c.atlas_of[i] != k || i == key) continue;
      uint8_t* p = forza::GuestPtr(base, tmp);
      p[0] = uint8_t(i >> 24); p[1] = uint8_t(i >> 16); p[2] = uint8_t(i >> 8); p[3] = uint8_t(i);
      ctx.r3.u64 = map; ctx.r4.u64 = tmp;
      __imp__sub_82E66690(ctx, base);
      uint8_t* slot = forza::GuestPtr(base, ctx.r3.u32);
      slot[0] = uint8_t(k >> 24); slot[1] = uint8_t(k >> 16); slot[2] = uint8_t(k >> 8); slot[3] = uint8_t(k);
      ++added;
    }
    if (added) REXLOG_INFO("FM4TRACK: pack entry {}: {} atlas tiles mapped to it", k, added);
  }
  ctx.r3.u64 = ret;
}


extern "C" REX_FUNC(sub_82D095C0) {
  __imp__sub_82D095C0(ctx, base);
  if (fm4_stx_active) {
    REXLOG_INFO("FM4TRACK: static texture pack holds {} textures; {} numbers given", ctx.r3.u32,
                Fm4StxConfig().ids.size());
  }
}

// A track archive file read into the file depot's cache (sub_82D21528: r4 its number).
extern "C" REX_FUNC(__imp__sub_82D21528);
extern "C" REX_FUNC(sub_82D21528) {
  static std::atomic<int> logged{0};
  if (std::getenv("FORZA_TRACK_SPAWN") && logged.fetch_add(1) < 400) {
    REXLOG_INFO("FM4TRACK: depot file {}", ctx.r4.u32);
  }
  static std::atomic<int> traced{0};
  if (std::getenv("FORZA_FIZ_TRACE") && traced.fetch_add(1) < 600) {
    std::string chain;
    uint32_t frame = ctx.r1.u32;
    for (int i = 0; i < 10 && Stack(frame) && GuestReadable(frame); ++i) {
      const uint32_t parent = Load32(base, frame);
      if (parent <= frame || parent - frame > 0x100000 || !GuestReadable(parent - 8)) break;
      chain += fmt::format(" {:08X}", Load32(base, parent - 8));
      frame = parent;
    }
    REXLOG_INFO("FM4TRACK: depot file {} r3 {:08X} r5 {:08X} r6 {:08X} from {:08X}{}", ctx.r4.u32,
                ctx.r3.u32, ctx.r5.u32, ctx.r6.u32, uint32_t(ctx.lr), chain);
  }
  __imp__sub_82D21528(ctx, base);
}
#endif


#if FORZA_TU4
// A ribbon's collision index given to the collision manager (sub_82E0B2A8: r3 the track, r4
// the .col file, r5 0 to read every cell from the .col file now, sub_82DD8548; Colorado's
// come with 1, its cells streamed as .fiz files with the zones). FORZA_FM4_COL=1 passes 0:
// a converted FM4 track keeps its cells in the .col file (tools/fm4_col.py).
extern "C" REX_FUNC(__imp__sub_82E0B2A8);
extern "C" REX_FUNC(sub_82E0B2A8) {
  if (std::getenv("FORZA_FIZ_TRACE") || std::getenv("FORZA_FM4_COL")) {
    REXLOG_INFO("FM4TRACK: collision index: r5 {} r6 {}{}", ctx.r5.u32, ctx.r6.u32,
                std::getenv("FORZA_FM4_COL") ? ", cells read whole (FORZA_FM4_COL)" : "");
  }
  if (std::getenv("FORZA_FM4_COL")) ctx.r5.u64 = 0;
  __imp__sub_82E0B2A8(ctx, base);
}
#endif

#if FORZA_TU4
// The pool of live instance objects (sub_82E11B60: r6 the count, 224 bytes each), made by the
// track presentation (sub_82E296C0) with 3 x (the largest zone's instance count + 128) + the
// dynamic instances. The game makes up to 14 zones live around the car (sub_82E27480);
// Colorado's zones each list everything visible from them, so the live zones share most
// instances, where a converted FM4 track's hex zones hold only their own and 9 live ones can
// need more objects than that (a null object written at +0x80, sub_82E2E750).
// FORZA_FM4_ZONE_POOL=n makes the pool n times larger.
extern "C" REX_FUNC(__imp__sub_82E11B60);
extern "C" REX_FUNC(sub_82E11B60) {
  if (const char* p = std::getenv("FORZA_FM4_ZONE_POOL")) {
    const uint32_t lr = uint32_t(ctx.lr);
    const int n = std::atoi(p);
    if (lr > 0x82E296C0 && lr < 0x82E29A00 && n > 1) {
      REXLOG_INFO("FM4TRACK: instance object pool {} -> {}", ctx.r6.u32, ctx.r6.u32 * n);
      ctx.r6.u64 = uint64_t(ctx.r6.u32) * n;
    }
  }
  __imp__sub_82E11B60(ctx, base);
}
#endif

#if FORZA_TU4
namespace {
bool FizTrace() {
  static const bool on = std::getenv("FORZA_FIZ_TRACE") != nullptr;
  return on;
}
}  // namespace

// The collision world's triangle query (sub_82DD7310, the cell query's one caller): its
// callers, once each, for FORZA_FIZ_TRACE runs (which code turns triangles into forces).
extern "C" REX_FUNC(__imp__sub_82DD7310);
extern "C" REX_FUNC(sub_82DD7310) {
  if (FizTrace()) {
    static std::mutex m;
    static std::vector<uint32_t> seen;
    const uint32_t lr = uint32_t(ctx.lr);
    std::lock_guard<std::mutex> lock(m);
    if (seen.size() < 24 && std::find(seen.begin(), seen.end(), lr) == seen.end()) {
      seen.push_back(lr);
      std::string chain;
      uint32_t frame = ctx.r1.u32;
      for (int i = 0; i < 8 && Stack(frame) && GuestReadable(frame); ++i) {
        const uint32_t parent = Load32(base, frame);
        if (parent <= frame || parent - frame > 0x100000 || !GuestReadable(parent - 8)) break;
        chain += fmt::format(" {:08X}", Load32(base, parent - 8));
        frame = parent;
      }
      REXLOG_INFO("FM4TRACK: triangle query from {:08X} (r5 {:08X} r6 {:08X} r7 {:08X}){}", lr,
                  ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, chain);
    }
  }
  __imp__sub_82DD7310(ctx, base);
}
#endif

#if FORZA_TU4
// The collision world's other query (sub_82DD59F0, its vtable's second entry): callers.
extern "C" REX_FUNC(__imp__sub_82DD59F0);
extern "C" REX_FUNC(sub_82DD59F0) {
  if (FizTrace()) {
    static std::mutex m;
    static std::vector<uint32_t> seen;
    const uint32_t lr = uint32_t(ctx.lr);
    std::lock_guard<std::mutex> lock(m);
    if (seen.size() < 24 && std::find(seen.begin(), seen.end(), lr) == seen.end()) {
      seen.push_back(lr);
      std::string chain;
      uint32_t frame = ctx.r1.u32;
      for (int i = 0; i < 8 && Stack(frame) && GuestReadable(frame); ++i) {
        const uint32_t parent = Load32(base, frame);
        if (parent <= frame || parent - frame > 0x100000 || !GuestReadable(parent - 8)) break;
        chain += fmt::format(" {:08X}", Load32(base, parent - 8));
        frame = parent;
      }
      REXLOG_INFO("FM4TRACK: world query 1 from {:08X} (r5 {:08X} r6 {:08X} r7 {:08X}){}", lr,
                  ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, chain);
    }
  }
  const uint32_t self = ctx.r3.u32, lr1 = uint32_t(ctx.lr), box = ctx.r4.u32, lim = ctx.r5.u32;
  __imp__sub_82DD59F0(ctx, base);
  if (FizTrace() && lim == 10000) {
    static std::atomic<int> n{0};
    const uint32_t rec = GuestReadable(self + 100) ? Load32(base, self + 100) : 0;
    const uint32_t st = rec && GuestReadable(rec + 20) ? Load32(base, rec + 20) : 0;
    const uint32_t state = st && GuestReadable(st + 8) ? Load32(base, st + 8) : 0xFFFFFFFF;
    float x = 0, z = 0;
    if (GuestReadable(box)) {
      uint32_t v = Load32(base, box);
      std::memcpy(&x, &v, 4);
      v = Load32(base, box + 8);
      std::memcpy(&z, &v, 4);
    }
    if (n.fetch_add(1) < 300)
      REXLOG_INFO("FM4TRACK: world query 1 limit 10000 from {:08X} at {:.1f},{:.1f}: cell record "
                  "{:08X} cell {:08X} state {} -> {}", lr1, x, z, rec,
                  rec && GuestReadable(rec) ? Load32(base, rec) : 0, state, ctx.r3.u32);
  }
}
#endif

#if FORZA_TU4
// The cell queries (sub_82DD5318, sub_82DD6C08: r3 the cell, r4 the box, r5 the most
// triangles wanted, r6 the list, r7 the flag mask): each caller's limit and mask, and how
// often a query comes back empty, for FORZA_FIZ_TRACE runs.
namespace {
struct QueryStat {
  uint32_t lr, limit, mask;
  uint64_t calls, empty;
};
std::mutex query_mutex;
std::vector<QueryStat> query_stats;
void CountQuery(uint32_t lr, uint32_t limit, uint32_t mask, uint32_t result) {
  std::lock_guard<std::mutex> lock(query_mutex);
  for (auto& q : query_stats) {
    if (q.lr == lr && q.limit == limit && q.mask == mask) {
      ++q.calls;
      if (!result) ++q.empty;
      if ((q.calls & (q.calls - 1)) == 0 && q.calls >= 64)
        REXLOG_INFO("FM4TRACK: cell query from {:08X} limit {} mask {:02X}: {} calls, {} empty",
                    lr, limit, mask, q.calls, q.empty);
      return;
    }
  }
  query_stats.push_back({lr, limit, mask, 1, result ? 0u : 1u});
  REXLOG_INFO("FM4TRACK: cell query from {:08X} limit {} mask {:02X} (new)", lr, limit, mask);
}
}  // namespace
extern "C" REX_FUNC(__imp__sub_82DD5318);
extern "C" REX_FUNC(sub_82DD5318) {
  const uint32_t lr = uint32_t(ctx.lr), limit = ctx.r5.u32, mask = ctx.r7.u32 & 0xFF;
  const uint32_t box = ctx.r4.u32;
  float b[8] = {};
  if (FizTrace() && GuestReadable(box))
    for (int i = 0; i < 8; ++i) {
      const uint32_t v = Load32(base, box + 4 * i);
      std::memcpy(&b[i], &v, 4);
    }
  __imp__sub_82DD5318(ctx, base);
  if (FizTrace()) {
    CountQuery(lr, limit, mask, ctx.r3.u32);
    static std::atomic<int> boxes{0};
    if (limit == 10000 && boxes.fetch_add(1) < 400)
      REXLOG_INFO("FM4TRACK: cache query box {:.1f},{:.1f},{:.1f} .. {:.1f},{:.1f},{:.1f}: {} triangles",
                  b[0], b[1], b[2], b[4], b[5], b[6], ctx.r3.u32);
  }
}
extern "C" REX_FUNC(__imp__sub_82DD6C08);
extern "C" REX_FUNC(sub_82DD6C08) {
  const uint32_t lr = uint32_t(ctx.lr), limit = ctx.r5.u32, mask = ctx.r7.u32 & 0xFF;
  __imp__sub_82DD6C08(ctx, base);
  if (FizTrace()) CountQuery(lr, limit, mask, ctx.r3.u32);
}
#endif

#if FORZA_TU4
// The streaming file blocks: sub_82E3A208 makes the pool the first time a file is streamed,
// 22440 blocks of 1028 bytes (23 MB) linked in a free list at 0x835A0B40, and sub_82E3A2E8
// takes one block from it. Colorado's zones fit; a converted FM4 circuit's hold more (the
// pool ran dry after a few minutes of Suzuka, and the next file read wrote through a null
// block, sub_82E3CAE8). tools/fm4_pvs.py writes what a converted track needs into
// media/tracks/<track>/fm4_stream_blocks.txt (the largest union of a zone's and its six
// neighbours' model files, in KB, with a margin). When the pool is made, the largest such
// number of the tracks in game:\media\tracks\ is linked in at once, up to kMaxBlocks in all;
// FH1's own data has no such file and keeps its pool as it is. The game makes the pool again
// when a world loads (all its blocks free again): the blocks added are kept and linked in
// again each time, never allocated twice. Should a converted track still empty the pool,
// 4096 more blocks are added each time, up to the same cap, with a warning.
namespace {
using rex::X_STATUS;
constexpr uint32_t kBlock = 1028, kGameBlocks = 22440, kMaxBlocks = 65536;  // 64 MB in all
constexpr uint32_t kFreeList = 0x835A0B40, kPoolMade = 0x835A0B44;
std::atomic<uint32_t> stream_blocks{kGameBlocks};
bool converted_track = false;
std::vector<std::pair<uint32_t, uint32_t>> stream_chunks;  // guest address, blocks

// The blocks the converted tracks ask for (0: none installed).
uint32_t TrackStreamBlocks() {
  auto* vfs = rex::system::kernel_state()->file_system();
  auto* tracks = vfs ? vfs->ResolvePath("game:\\media\\tracks") : nullptr;
  if (!tracks) return 0;
  uint32_t want = 0;
  for (const auto& dir : tracks->children()) {
    auto* entry = dir->GetChild("fm4_stream_blocks.txt");
    if (!entry || entry->size() == 0 || entry->size() > 64) continue;
    rex::filesystem::File* file = nullptr;
    if (entry->Open(rex::filesystem::FileAccess::kFileReadData, &file) != X_STATUS_SUCCESS || !file)
      continue;
    char text[65] = {};
    size_t read = 0;
    file->ReadSync(std::span(reinterpret_cast<uint8_t*>(text), entry->size()), 0, &read);
    file->Destroy();
    const unsigned long n = std::strtoul(text, nullptr, 10);
    REXLOG_INFO("FM4TRACK: {} asks for {} streaming blocks", dir->name(), n);
    want = std::max<uint32_t>(want, uint32_t(std::min<unsigned long>(n, kMaxBlocks)));
  }
  return want;
}

// Links a chunk of blocks in front of the free list.
void LinkChunk(uint8_t* base, uint32_t mem, uint32_t count) {
  const uint32_t head = Load32(base, kFreeList);
  for (uint32_t i = 0; i < count; ++i) {
    const uint32_t next = i + 1 < count ? mem + kBlock * (i + 1) : head;
    uint8_t* p = forza::GuestPtr(base, mem + kBlock * i);
    p[0] = uint8_t(next >> 24); p[1] = uint8_t(next >> 16); p[2] = uint8_t(next >> 8); p[3] = uint8_t(next);
  }
  uint8_t* h = forza::GuestPtr(base, kFreeList);
  h[0] = uint8_t(mem >> 24); h[1] = uint8_t(mem >> 16); h[2] = uint8_t(mem >> 8); h[3] = uint8_t(mem);
}

// A new chunk of count blocks (guest heap, kept for the process), linked in; false if none.
bool AddStreamBlocks(uint8_t* base, uint32_t count) {
  count = std::min(count, kMaxBlocks - std::min(kMaxBlocks, stream_blocks.load()));
  if (!count) return false;
  const uint32_t mem = REX_KERNEL_MEMORY()->SystemHeapAlloc(kBlock * count);
  if (!mem) return false;
  stream_chunks.emplace_back(mem, count);
  LinkChunk(base, mem, count);
  stream_blocks += count;
  return true;
}
}  // namespace

extern "C" REX_FUNC(__imp__sub_82E3A208);
extern "C" REX_FUNC(sub_82E3A208) {
  const bool made = Load32(base, kPoolMade) != 0;
  __imp__sub_82E3A208(ctx, base);
  if (made || !Load32(base, kPoolMade)) return;
  // The pool is made under its own lock (sub_82E3A2E8 waits on it); blocks are taken after.
  if (!stream_chunks.empty()) {  // made again: the blocks added before are free again too
    for (const auto& [mem, count] : stream_chunks) LinkChunk(base, mem, count);
    REXLOG_INFO("FM4TRACK: streaming pool made again: {} blocks", stream_blocks.load());
    return;
  }
  const uint32_t want = TrackStreamBlocks();
  converted_track = want != 0;
  if (want > kGameBlocks && AddStreamBlocks(base, want - kGameBlocks))
    REXLOG_INFO("FM4TRACK: streaming blocks {} (the game's {} and {} for converted tracks)",
                stream_blocks.load(), kGameBlocks, want - kGameBlocks);
}

extern "C" REX_FUNC(__imp__sub_82E3A2E8);
extern "C" REX_FUNC(sub_82E3A2E8) {
  if (converted_track && Load32(base, kPoolMade) && !Load32(base, kFreeList)) {
    static std::mutex m;
    std::lock_guard<std::mutex> lock(m);
    if (!Load32(base, kFreeList) && AddStreamBlocks(base, 4096))
      REXLOG_WARN("FM4TRACK: streaming blocks ran out: 4096 more, {} in all (raise the "
                  "track's fm4_stream_blocks.txt)", stream_blocks.load());
  }
  __imp__sub_82E3A2E8(ctx, base);
}
#endif

#if FORZA_TU4
// Zone bookkeeping for FORZA_ZONE_TRACE runs: zone requests dropped (sub_82E1AFD8: r3 the
// presentation, r4 the zone), the loaded-zone list (presentation+0xA08..0xA0C, 16 bytes each,
// +0xC the zone) and the streaming blocks in use (0x835A0B48), on every make-live and drop.
namespace {
bool ZoneTrace() {
  static const bool on = std::getenv("FORZA_ZONE_TRACE") != nullptr;
  return on;
}
void LogZones(uint8_t* base, uint32_t pres, const char* what, uint32_t zone) {
  const uint32_t a = Load32(base, pres + 0xA08), b = Load32(base, pres + 0xA0C);
  const uint32_t loaded = b >= a ? (b - a) / 16 : 0;
  std::string ids;
  for (uint32_t p = a; p < b && p < a + 16 * 40; p += 16) ids += fmt::format(" {}", Load32(base, p + 12));
  const uint32_t pending = Load32(base, pres + 0x10000 - 9808);
  const uint32_t ipool = Load32(base, pres + 0x50);
  const uint32_t ppool = Load32(base, pres + 0x10000 - 10232 + 232);
  REXLOG_INFO("FM4ZONES: {} {}: {} loaded ({}), {} pending, streaming blocks in use {}, "
              "instance objects in use {}, placements in use {}", what, zone, loaded, ids,
              pending, Load32(base, 0x835A0B48), ipool && GuestReadable(ipool + 12) ? Load32(base, ipool + 12) : 0,
              ppool && GuestReadable(ppool + 12) ? Load32(base, ppool + 12) : 0);
}
}  // namespace
extern "C" REX_FUNC(__imp__sub_82E1AFD8);
extern "C" REX_FUNC(sub_82E1AFD8) {
  const uint32_t pres = ctx.r3.u32, zone = ctx.r4.u32;
  __imp__sub_82E1AFD8(ctx, base);
  if (ZoneTrace()) LogZones(base, pres, "dropped", zone);
}
#endif

#if FORZA_TU4
// The pool of instance placements (sub_82CEDEE0: r4 the count, set up right after the live
// instance objects by sub_82E296C0, with the same count): FORZA_FM4_ZONE_POOL=n makes it n
// times larger too (driving into Suzuka's Degner zones ran it dry: a null placement written
// in the zone reader, sub_82CF2168).
extern "C" REX_FUNC(__imp__sub_82CEDEE0);
extern "C" REX_FUNC(sub_82CEDEE0) {
  if (const char* p = std::getenv("FORZA_FM4_ZONE_POOL")) {
    const uint32_t lr = uint32_t(ctx.lr);
    const int n = std::atoi(p);
    if (lr > 0x82E296C0 && lr < 0x82E29A00 && n > 1) {
      REXLOG_INFO("FM4TRACK: instance placement pool {} -> {}", ctx.r4.u32, ctx.r4.u32 * n);
      ctx.r4.u64 = uint64_t(ctx.r4.u32) * n;
    }
  }
  __imp__sub_82CEDEE0(ctx, base);
}
#endif

// Streamed track models on a converted FM4 track. Their vertex and index buffers come from one
// of the game's own fixed-size heaps (sub_825CB038, the game's XMemAlloc, called from the
// buffer helpers at 82AA5030, vertex data, and 82AA50FC, index data; attributes B2800000).
// FH1 sizes that heap for Colorado: FM4's zones ask for more (Sunset Infield's spot 2: 35 MB
// of vertex data, then every further request failed, 140000 failed calls a minute, retried for
// ever), and a model that never gets its buffers never binds its textures either: they stay on
// the static pack's resident mips (32 x 32: flat grey roads, dark verges). On a converted track
// (fm4_stream_blocks.txt found) a failed allocation from those two callers is taken from the
// guest's 4 KB-page physical heap instead, and the game's free and size calls for it
// (sub_825CB040, sub_825CB048) go there too. FORZA_FM4_TEX_FALLBACK=0 turns it off;
// FORZA_FM4_ALLOCDIAG=1 logs failures and totals per caller.
namespace {
std::mutex tex_fallback_mu;
std::unordered_map<uint32_t, uint32_t> tex_fallback;  // guest address -> size
uint64_t tex_fallback_bytes = 0;
bool TexFallbackOn() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_FM4_TEX_FALLBACK");
    return !v || std::string_view(v) != "0";
  }();
  return on && converted_track;
}
}  // namespace

extern "C" REX_FUNC(__imp__sub_825CB038);
extern "C" REX_FUNC(sub_825CB038) {
  static const bool diag = std::getenv("FORZA_FM4_ALLOCDIAG") != nullptr;
  const uint32_t size = ctx.r3.u32, flags = ctx.r4.u32, lr = uint32_t(ctx.lr);
  __imp__sub_825CB038(ctx, base);
  if (!ctx.r3.u32 && (lr == 0x82AA5034 || lr == 0x82AA5100) && size && TexFallbackOn()) {
    auto* heap = REX_KERNEL_MEMORY()->LookupHeapByType(true, 4096);
    uint32_t addr = 0;
    const uint32_t rounded = (size + 4095) & ~4095u;
    if (heap && heap->Alloc(rounded, 4096, rex::memory::kMemoryAllocationReserve | rex::memory::kMemoryAllocationCommit,
                            rex::memory::kMemoryProtectRead | rex::memory::kMemoryProtectWrite, true, &addr) && addr) {
      std::memset(forza::GuestPtr(base, addr), 0, rounded);
      std::lock_guard<std::mutex> lock(tex_fallback_mu);
      tex_fallback[addr] = size;
      tex_fallback_bytes += rounded;
      const size_t n = tex_fallback.size();
      if ((n & (n - 1)) == 0)
        REXLOG_INFO("FM4TRACK: {} streamed track textures' memory from the physical heap ({} KB in use)", n,
                    tex_fallback_bytes / 1024);
      ctx.r3.u64 = addr;
    }
  }
  if (!diag) return;
  static std::mutex mu;
  static std::map<uint32_t, std::pair<uint64_t, uint32_t>> by_caller;  // bytes, failures
  static uint32_t calls = 0, fails = 0;
  std::lock_guard<std::mutex> lock(mu);
  auto& e = by_caller[lr];
  if (ctx.r3.u32) e.first += size; else { ++e.second; ++fails; }
  if (!ctx.r3.u32 && fails <= 20)
    REXLOG_INFO("FM4ALLOC failed: {} bytes flags {:08X} from {:08X}", size, flags, lr);
  if (++calls % 20000 == 0) {
    std::string s;
    for (auto& [c, v] : by_caller) s += fmt::format(" {:08X}:{}KB/{}f", c, v.first / 1024, v.second);
    REXLOG_INFO("FM4ALLOC {} calls, {} failed:{}", calls, fails, s);
  }
}

// The game's free (r3 address, r4 attributes) and size for those addresses.
extern "C" REX_FUNC(__imp__sub_825CB040);
extern "C" REX_FUNC(sub_825CB040) {
  {
    std::lock_guard<std::mutex> lock(tex_fallback_mu);
    auto it = tex_fallback.find(ctx.r3.u32);
    if (it != tex_fallback.end()) {
      const uint32_t addr = it->first, rounded = (it->second + 4095) & ~4095u;
      tex_fallback.erase(it);
      tex_fallback_bytes -= rounded;
      if (auto* heap = REX_KERNEL_MEMORY()->LookupHeap(addr)) heap->Release(addr);
      return;
    }
  }
  __imp__sub_825CB040(ctx, base);
}
extern "C" REX_FUNC(__imp__sub_825CB048);
extern "C" REX_FUNC(sub_825CB048) {
  {
    std::lock_guard<std::mutex> lock(tex_fallback_mu);
    auto it = tex_fallback.find(ctx.r3.u32);
    if (it != tex_fallback.end()) {
      ctx.r3.u64 = it->second;
      return;
    }
  }
  __imp__sub_825CB048(ctx, base);
}

// FORZA_FM4_ALLOCDIAG: the game's other allocator (sub_82D81300: r3 size, r4 alignment; the
// track's pack loop and texture objects call it): failures with size and caller.
extern "C" REX_FUNC(__imp__sub_82D81300);
extern "C" REX_FUNC(sub_82D81300) {
  static const bool diag = std::getenv("FORZA_FM4_ALLOCDIAG") != nullptr;
  const uint32_t size = ctx.r3.u32, align = ctx.r4.u32, lr = uint32_t(ctx.lr);
  __imp__sub_82D81300(ctx, base);
  if (!diag) return;
  static std::mutex mu;
  static std::map<uint32_t, std::pair<uint64_t, uint32_t>> by_caller;
  static uint32_t calls = 0, fails = 0;
  std::lock_guard<std::mutex> lock(mu);
  auto& e = by_caller[lr];
  if (ctx.r3.u32) e.first += size; else { ++e.second; ++fails; }
  if (!ctx.r3.u32 && fails <= 20)
    REXLOG_INFO("FM4ALLOC2 failed: {} bytes align {} from {:08X}", size, align, lr);
  if (++calls % 20000 == 0) {
    std::string s;
    for (auto& [c, v] : by_caller) s += fmt::format(" {:08X}:{}KB/{}f", c, v.first / 1024, v.second);
    REXLOG_INFO("FM4ALLOC2 {} calls, {} failed:{}", calls, fails, s);
  }
}
