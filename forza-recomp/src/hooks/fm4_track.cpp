// forza - FM4 circuits inside FH1 (#648, "The Festival"): dev switches for the conversion
// work (tools/fm4_track_mod.py, tools/fm4_pvs.py). All off unless their variable is set:
//   FORZA_TRACK_SPAWN=x,y,z   the player car's teleports go there; also turns on the
//                             FM4TRACK diagnostics below (file errors, the dirty disc
//                             message, zone, texture and resource table checks)
//   FORZA_PVS_TRACE=<file>    every read of the PVS reader, to <file>
//   FORZA_INSECURE_FILES=a|b  files the secure file system lets open as plain files (a
//                             changed zipmanifest.xml that mounts a converted track's archive)
// tu_address_map: TU4 addresses
//
// tools/fm4_track_mod.py --free-roam makes FH1's free roam load an FM4 circuit in place of
// Colorado. The car then starts at its saved Colorado position, in the void.
// FORZA_TRACK_SPAWN=x,y,z (world metres, y up; Suzuka's flying start is 487.7,606,22.5)
// moves the player car's teleports there (the spawn into free roam, a car reset), and logs
// every vehicle teleport. Called from the teleport hook in teleport_velocity.cpp.

#include "../platform/guest_addr.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace forza::fm4_track {

void OnTeleport(PPCContext& ctx) {
#if FORZA_TU4
  static const struct Spawn {
    bool on = false;
    float x = 0, y = 0, z = 0;
  } spawn = [] {
    Spawn s;
    const char* p = std::getenv("FORZA_TRACK_SPAWN");
    s.on = p && std::sscanf(p, "%f,%f,%f", &s.x, &s.y, &s.z) == 3;
    return s;
  }();
  if (!spawn.on) return;
  // lvx byte-reverses the vector: x is the top lane (world_challenge.cpp).
  REXLOG_INFO("FM4TRACK: teleport of vehicle {:08X} to {:.1f},{:.1f},{:.1f}", ctx.r3.u32,
              ctx.v1.f32[3], ctx.v1.f32[2], ctx.v1.f32[1]);
  // Only the player's car: the first car teleported (the spawn into free roam) is it. The
  // free-roam AI drivers are teleported too, to their own places.
  static std::atomic<uint32_t> player{0};
  uint32_t expected = 0;
  if (!player.compare_exchange_strong(expected, ctx.r3.u32) && expected != ctx.r3.u32) return;
  ctx.v1.f32[3] = spawn.x;
  ctx.v1.f32[2] = spawn.y;
  ctx.v1.f32[1] = spawn.z;
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
#include <rex/system/kernel_state.h>
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
  __imp__sub_82E1CA20(ctx, base);
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
  __imp__sub_82D21528(ctx, base);
}
#endif
