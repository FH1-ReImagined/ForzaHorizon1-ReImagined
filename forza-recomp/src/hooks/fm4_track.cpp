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
#include <mutex>
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
  const uint32_t live_pres = ctx.r3.u32, live_zone = GuestReadable(ctx.r4.u32 + 12) ? Load32(base, ctx.r4.u32 + 12) : 0;
  __imp__sub_82E1CA20(ctx, base);
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
extern "C" REX_FUNC(sub_82E24248) {
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
    REXLOG_INFO("FM4TRACK: {} static texture pack: {} texture numbers", c.track, c.ids.size());
    return c;
  }();
  return c;
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
