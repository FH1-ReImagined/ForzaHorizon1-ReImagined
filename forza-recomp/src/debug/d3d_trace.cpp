// D3D trace (FORZA_D3D_TRACE=ON, TU4, Linux; docs/native-renderer.md "Tools"): a counting
// wrapper around every function of d3d_trace_hooks.inc, for the native renderer's inventory of
// the Xbox D3D layer.
//
// Each wrapped call counts, and reads the D3D device's command-buffer cursor (device +48: the
// last dword written) when it starts, when it ends and around wrapped calls nested in it. The
// dwords appended in between are tagged with the call's id in the shared segment
// (d3d_trace_shm.h): `tags` with the innermost wrapped frame, `otags` with the outermost (the
// entry point the game called). The GPU plugin's PM4 trace (ReIGlue REXGLUE_FH1_PM4_TRACE)
// looks every executed packet's tag up and writes the report; without that plugin the counters
// are kept but nothing reports them.
//
// The device is learnt from the functions whose r3 is always one (kDevice) and from any r3 that
// looks like one: a cursor in a physical view, below its hard limit (+52), with the soft limit
// (+56) close. Recording contexts of the same layout (the HUD's command buffers, a secondary
// context) are learnt the same way.

#include "d3d_trace.h"

#include "d3d_trace_shm.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

namespace forza::d3d_trace {
namespace {

using fh1pm4::Shm;

constexpr int kHookCount = 0
#define D3D_TRACE_HOOK(id, function, kind, role) +1
#include "d3d_trace_hooks.inc"
#undef D3D_TRACE_HOOK
    ;

Shm* OpenShm() {
  // Only the GPU plugin's PM4 trace reads the counters, and only with FH1_PM4_STATS set; it
  // also removes the segment's name once both sides have mapped it.
  const char* stats = std::getenv("FH1_PM4_STATS");
  if (!stats || !*stats) {
    REXLOG_INFO("D3D trace: FH1_PM4_STATS is not set, nothing is counted");
    return nullptr;
  }
  char name[64];
  std::snprintf(name, sizeof(name), "/fh1pm4-%d", int(getpid()));
  int fd = shm_open(name, O_RDWR | O_CREAT, 0600);
  if (fd < 0) return nullptr;
  if (ftruncate(fd, sizeof(Shm)) != 0) {
    close(fd);
    return nullptr;
  }
  void* p = mmap(nullptr, sizeof(Shm), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_NORESERVE, fd, 0);
  close(fd);
  if (p == MAP_FAILED) return nullptr;
  auto* shm = static_cast<Shm*>(p);
  shm->version = fh1pm4::kVersion;
  shm->size = sizeof(Shm);
  __atomic_store_n(&shm->magic, fh1pm4::kMagic, __ATOMIC_RELEASE);
  REXLOG_INFO("D3D trace: counting {} D3D functions into /dev/shm{} ({} MB)", kHookCount, name,
              sizeof(Shm) >> 20);
  return shm;
}

Shm* Get() {
  static Shm* shm = OpenShm();
  return shm;
}

struct Frame {
  uint32_t id, dev, last;
};
constexpr int kDepth = 64;
thread_local Frame t_stack[kDepth];
thread_local int t_depth = 0;
thread_local uint32_t t_dev = 0;

inline uint32_t Be32(const uint8_t* base, uint32_t a) {
  uint32_t v;
  std::memcpy(&v, base + a, 4);
  return __builtin_bswap32(v);
}
inline uint64_t Be64(const uint8_t* base, uint32_t a) {
  uint64_t v;
  std::memcpy(&v, base + a, 8);
  return __builtin_bswap64(v);
}
inline void Add(uint64_t& c, uint64_t v) { __atomic_fetch_add(&c, v, __ATOMIC_RELAXED); }

bool Known(Shm* s, uint32_t d) {
  uint32_t n = __atomic_load_n(&s->ndev, __ATOMIC_ACQUIRE);
  for (uint32_t i = 0; i < n && i < uint32_t(fh1pm4::kDevices); ++i) {
    if (s->devs[i] == d) return true;
  }
  return false;
}

void Learn(Shm* s, uint32_t d) {
  if (!d || Known(s, d)) return;
  static std::atomic_flag lock = ATOMIC_FLAG_INIT;
  while (lock.test_and_set(std::memory_order_acquire)) {
  }
  if (!Known(s, d) && s->ndev < uint32_t(fh1pm4::kDevices)) {
    s->devs[s->ndev] = d;
    __atomic_store_n(&s->ndev, s->ndev + 1, __ATOMIC_RELEASE);
  }
  lock.clear(std::memory_order_release);
}

bool LooksLikeDevice(const uint8_t* base, uint32_t r3) {
  if (r3 < 0x40000000u || r3 >= 0xFFFF0000u || (r3 & 3)) return false;
  uint32_t c = Be32(base, r3 + 48), l1 = Be32(base, r3 + 52), l2 = Be32(base, r3 + 56);
  if (c < 0xA0000000u || (c & 3) || l1 < c || l1 - c > (32u << 20)) return false;
  if (l2 > l1 || l1 - l2 > (32u << 20)) return false;
  return true;
}

inline uint32_t Cursor(const uint8_t* base, uint32_t dev) { return dev ? Be32(base, dev + 48) : 0; }

void LogCursor(Shm* s, uint32_t v) {
  uint64_t n = __atomic_load_n(&s->cursor_logged, __ATOMIC_RELAXED);
  if (n >= 64) return;
  for (uint64_t i = 0; i < n; ++i) {
    if ((s->cursor_log[i][0] >> 20) == (v >> 20)) return;
  }
  n = __atomic_fetch_add(&s->cursor_logged, 1, __ATOMIC_RELAXED);
  if (n < 64) {
    s->cursor_log[n][0] = v;
    s->cursor_log[n][1] = fh1pm4::Phys(v);
  }
}

// What the frame's device cursor moved by since the frame last looked: counted and tagged.
void Flush(Shm* s, const uint8_t* base, Frame& f) {
  if (!f.dev) return;
  uint32_t c = Cursor(base, f.dev);
  int64_t d = int64_t(c) - int64_t(f.last);
  if (d > 0 && d < (4 << 20) && !(d & 3)) {
    Add(s->bytes[f.id], uint64_t(d));
    uint32_t n = __atomic_load_n(&s->ndev, __ATOMIC_ACQUIRE);
    for (uint32_t i = 0; i < n && i < uint32_t(fh1pm4::kDevices); ++i) {
      if (s->devs[i] == f.dev) {
        Add(s->dev_bytes[i], uint64_t(d));
        break;
      }
    }
    const uint32_t outer = t_stack[0].id;
    Add(s->outer_bytes[outer], uint64_t(d));
    uint32_t p = fh1pm4::Phys(f.last + 4);
    if (uint64_t(p) + uint64_t(d) <= fh1pm4::kPhys) {
      std::memset(&s->tags[p >> 2], int(f.id), size_t(d >> 2));
      std::memset(&s->otags[p >> 2], int(outer), size_t(d >> 2));
    }
    LogCursor(s, f.last + 4);
  } else if (d != 0) {
    Add(s->disc[f.id], 1);
  }
  f.last = c;
}

void CountReturnAddress(Shm* s, uint32_t lr, uint32_t id) {
  uint64_t key = (uint64_t(lr) << 8) | id;
  uint64_t h = (key * 0x9E3779B97F4A7C15ull) >> 49;  // 15 bits
  for (int i = 0; i < 64; ++i) {
    fh1pm4::LrEntry& e = s->lr[(h + i) & (fh1pm4::kLr - 1)];
    uint64_t k = __atomic_load_n(&e.key, __ATOMIC_RELAXED);
    if (k == key) {
      Add(e.count, 1);
      return;
    }
    if (k == 0) {
      uint64_t z = 0;
      if (__atomic_compare_exchange_n(&e.key, &z, key, false, __ATOMIC_RELAXED,
                                      __ATOMIC_RELAXED) ||
          z == key) {
        Add(e.count, 1);
        return;
      }
    }
  }
}

}  // namespace

void Enter(uint32_t id, bool device, PPCContext& ctx, uint8_t* base) {
  Shm* s = Get();
  if (!s) return;
  Add(s->calls[id], 1);
  uint32_t r3 = ctx.r3.u32;
  if (device) {
    Learn(s, r3);
    t_dev = r3;
  } else if (!Known(s, r3) && LooksLikeDevice(base, r3)) {
    Learn(s, r3);
  }
  // A call whose r3 is not a device (a setter taking an object) writes through the thread's
  // last device.
  uint32_t dev = Known(s, r3) ? r3 : t_dev;
  if (dev == r3) t_dev = r3;
  if (t_depth > 0 && t_depth <= kDepth) Flush(s, base, t_stack[t_depth - 1]);
  if (t_depth == 0) {
    Add(s->outer_calls[id], 1);
    CountReturnAddress(s, uint32_t(ctx.lr), id);
    if (dev) {
      for (int k = 0; k < 6; ++k) {
        Add(s->mask_pop[id][k], uint64_t(__builtin_popcountll(Be64(base, dev + 8 * k))));
      }
      Add(s->mask_calls[id], 1);
    }
  }
  if (t_depth < kDepth) t_stack[t_depth] = Frame{id, dev, Cursor(base, dev)};
  ++t_depth;
}

void Leave(uint8_t* base) {
  Shm* s = Get();
  if (!s || t_depth <= 0) return;
  --t_depth;
  if (t_depth < kDepth) Flush(s, base, t_stack[t_depth]);
  if (t_depth > 0 && t_depth <= kDepth) {
    Frame& parent = t_stack[t_depth - 1];
    parent.last = Cursor(base, parent.dev);
  }
}

}  // namespace forza::d3d_trace

// The wrappers. kChained functions are hooked elsewhere and open a Scope there.
namespace {
constexpr bool kPlain = false, kDevice = true;
[[maybe_unused]] constexpr bool kChained = false;
}  // namespace

#define D3D_TRACE_WRAP_kPlain(id, function)                         \
  extern "C" REX_FUNC(__imp__##function);                           \
  extern "C" REX_FUNC(function) {                                   \
    ::forza::d3d_trace::Enter(id, kPlain, ctx, base);               \
    __imp__##function(ctx, base);                                   \
    ::forza::d3d_trace::Leave(base);                                \
  }
#define D3D_TRACE_WRAP_kDevice(id, function)                        \
  extern "C" REX_FUNC(__imp__##function);                           \
  extern "C" REX_FUNC(function) {                                   \
    ::forza::d3d_trace::Enter(id, kDevice, ctx, base);              \
    __imp__##function(ctx, base);                                   \
    ::forza::d3d_trace::Leave(base);                                \
  }
#define D3D_TRACE_WRAP_kChained(id, function)
#define D3D_TRACE_HOOK(id, function, kind, role) D3D_TRACE_WRAP_##kind(id, function)
#include "d3d_trace_hooks.inc"
#undef D3D_TRACE_HOOK
