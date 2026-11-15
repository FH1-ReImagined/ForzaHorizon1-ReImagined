// List trace (FORZA_LIST_TRACE=ON, TU4, Linux; docs/native-renderer.md "M4"): what the game's
// RunCommandBuffer calls run, for the native renderer's model list work. A measurement build: it
// costs every RunCommandBuffer call (a lookup, a check of the list's header and a hash of its
// static words), never for players.
//
// For every RunCommandBuffer call (sub_824469B0) it counts, into keyed counters in the shared
// segment (list_trace_shm.h; the GPU plugin's list trace reports them as deltas): the device and
// its mode byte, the command buffer's type and flags, its class (a model's list from the files,
// told by the files' trailer after its PM4; another command buffer of type 9, which D3D recorded;
// type 10; other), the pass group the game was drawing (pass * 3 + layer, from the draw functions'
// hooks: FORZA_LIST_TRACE_CONTEXT), whether it is untiled, and the caller. It registers the
// buffers the call names by physical address, so the command processor can time and count them
// by class. The callers of sub_824467A8 (RunRecordedList), and whether it queued the list for the
// deferred context, are counted in its hook.
//
// The first time a command buffer is seen its header masks and flags are classified against what
// the files hold, and a model list is converted (src/platform/native_list.h) from a copy of its
// parts: refusals by reason, template sizes. Every later call compares the masks and flags with
// that first look, and the live list's key (LiveKey: its static words) with the template's. Shader
// loads (IM_LOAD) have the low two bits of their address word counted at the first look and after
// each shader fixup. The fixup dispatcher (sub_82D19210) is hooked to count fixups by type and to
// mark the list for that check. D3D's command buffer clone (sub_82AA8220) and memcpy (sub_82B3A7E0,
// copies of 512 bytes or more into physical memory) are hooked to tell who wrote the command
// buffers of type 9 that are not the files' lists, looked up when one first runs.
//
// FH1_LIST_STATS=<file> (the plugin's report) turns it on; FH1_LIST_CHECK_EVERY=N checks the key of
// a converted list on every Nth call only (default 1: every call).

#include "list_trace.h"

#include "d3d_trace.h"
#include "list_trace_shm.h"

#include "../platform/guest_memory.h"
#include "../platform/native_list.h"
#include "../platform/scene_tiling.h"

#include <fmt/format.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

namespace forza::list_trace {
namespace {

namespace nl = forza::native_list;
using fh1list::Shm;

// Keyed counters: the table in the top byte (tools/list_trace_report.py decodes them).
enum : uint64_t {
  kTCalls = 1,           // device slot, mode byte, flags, class, group, context, untiled, type
  kTDevice = 2,          // device slot, device
  kTRecordedCaller = 3,  // sub_824467A8: return address, queued, thread flag, class, context
  kTRunCaller = 4,       // sub_824469B0: return address, class, type
  kTRecordedDevice = 5,  // sub_824467A8: thread flag, the device it picks, queued
  kTHeader = 6,          // first look: class, group, mask kind, flags
  kTHeaderChange = 7,    // later calls: class, group, masks changed, flags changed (xor)
  kTConvert = 8,         // first look: class, group, refusal, untiled
  kTConvertWords = 9,    // first look: group, what (0 lists, 1 PM4 dwords, 2 template words)
  kTAreaC = 10,          // first look: group, the header's +0x80 (area C's length in the files)
  kTLiveKey = 11,        // later calls of converted lists: group, result
  kTLiveKeyWhere = 12,   // a key that differs: group, region of the first word that differs
  kTImLoad = 13,         // group, position in the pair, the address word's low two bits, when
  kTFixup = 14,          // fixup type, class, group
  kTCaller10908 = 15,    // sub_82D10908's return address
  kTCaller9258 = 16,     // sub_82CE9258's return address
  kTSegments = 17,       // first look: class, segment count
  kTRebind = 20,         // a buffer registered again with another command buffer or length
  kTNewCommandBuffer = 21,  // first looks: class, group
  kTRecording = 24,      // calls on a recording device: class, group
  kTLoaderFlags = 25,    // first look of a model list: group, its flags in memory
  kTReused = 26,         // a command buffer that holds another list now: old class, new class
  kTKeyDword = 27,       // a key that differs: group, region, the first dword that differs
  kTRecordedWriter = 28,  // first look of a recorded buffer: the memcpy that wrote its start
  kTClone = 29,          // sub_82AA8220 (D3D's command buffer clone): caller, the source's class
  kTCloneOf = 30,        // first look of a recorded buffer: made by the clone function, source class

};
// The loader sets flags 0x101 and clears 0x6 of the files' 0x86 / 0x886 (seen in the first runs):
// the list is converted and checked with its flags in the files' form, so its key can be compared
// with the keys of the files' lists (FH1_LIST_KEYS).
constexpr uint32_t kLoaderFlags = 0x101, kFileOnlyFlags = 0x6;
uint32_t FileFormFlags(uint32_t flags) {
  return (flags & ~(kLoaderFlags | nl::kFlagFixedUp)) | kFileOnlyFlags;
}
inline uint64_t T(uint64_t table) { return table << 56; }

enum : uint8_t { kModel = fh1list::kClassModel, kRecorded = fh1list::kClassRecorded };
constexpr uint8_t kGroupNone = fh1list::kGroupNone;

uint32_t Load32(uint8_t* base, uint32_t address) {
  const auto* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
uint32_t Load8(uint8_t* base, uint32_t address) { return *forza::GuestPtr(base, address); }
bool Plausible(uint32_t address) { return address >= 0x00100000 && address < 0xF0000000; }
uint32_t PhysicalToGuest(uint32_t address) { return 0xA0000000u | (address & 0x1FFFFFFFu); }

Shm* OpenShm() {
  const char* stats = std::getenv("FH1_LIST_STATS");
  if (!stats || !*stats) {
    REXLOG_INFO("list trace: FH1_LIST_STATS is not set, nothing is counted");
    return nullptr;
  }
  char name[64];
  std::snprintf(name, sizeof(name), "/fh1list-%d", int(getpid()));
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
  shm->version = fh1list::kVersion;
  shm->size = sizeof(Shm);
  __atomic_store_n(&shm->magic, fh1list::kMagic, __ATOMIC_RELEASE);
  REXLOG_INFO("list trace: counting RunCommandBuffer calls into /dev/shm{} ({} MB)", name,
              sizeof(Shm) >> 20);
  return shm;
}

Shm* Get() {
  static Shm* shm = OpenShm();
  return shm;
}

uint32_t CheckEvery() {
  static const uint32_t every = [] {
    const char* e = std::getenv("FH1_LIST_CHECK_EVERY");
    const int n = e ? std::atoi(e) : 1;
    return uint32_t(n > 0 ? n : 1);
  }();
  return every;
}

void Count(Shm* s, uint64_t key, uint64_t add = 1) {
  if (!key) return;
  const uint32_t h = fh1list::Slot(key, fh1list::kCounterBits);
  for (uint32_t i = 0; i < fh1list::kProbe; ++i) {
    fh1list::Counter& e = s->counters[(h + i) & (fh1list::kCounters - 1)];
    uint64_t k = __atomic_load_n(&e.key, __ATOMIC_ACQUIRE);
    if (k == 0) {
      uint64_t zero = 0;
      if (__atomic_compare_exchange_n(&e.key, &zero, key, false, __ATOMIC_ACQ_REL,
                                      __ATOMIC_ACQUIRE)) {
        k = key;
      } else {
        k = zero;
      }
    }
    if (k == key) {
      __atomic_fetch_add(&e.count, add, __ATOMIC_RELAXED);
      return;
    }
  }
}

// Devices seen, in the order seen (slot 15: the rest).
constexpr uint32_t kDeviceSlots = 15;
std::atomic<uint32_t> g_devices[kDeviceSlots];
std::mutex g_devices_mutex;
uint32_t DeviceSlot(uint32_t dev) {
  for (uint32_t i = 0; i < kDeviceSlots; ++i) {
    const uint32_t d = g_devices[i].load(std::memory_order_acquire);
    if (d == dev) return i;
    if (!d) break;
  }
  std::lock_guard<std::mutex> lock(g_devices_mutex);
  for (uint32_t i = 0; i < kDeviceSlots; ++i) {
    const uint32_t d = g_devices[i].load(std::memory_order_relaxed);
    if (d == dev) return i;
    if (!d) {
      g_devices[i].store(dev, std::memory_order_release);
      return i;
    }
  }
  return kDeviceSlots;
}

thread_local ContextState t_context;

uint8_t GroupOf(const ContextState& c) {
  switch (c.kind) {
    case Context::kTrack:
    case Context::kProcedural: {
      const uint32_t group = scene_tiling::RecordedListPass(c.pass) * 3 + c.layer;
      return group < 21 ? uint8_t(group) : kGroupNone;
    }
    case Context::kCar:
      return uint8_t(0x80 | (c.pass & 0x7F));
    default:
      return kGroupNone;
  }
}

// A command buffer: its type, flags, first segment and class.
struct Buffer {
  uint32_t type = 0, flags = 0, segments = 0, phys = 0, words = 0;
  uint8_t cls = fh1list::kClassOther;
};
Buffer Classify(uint8_t* base, uint32_t cb) {
  Buffer b;
  if (!Plausible(cb)) return b;
  b.type = Load32(base, cb) & 15;
  b.flags = Load32(base, cb + nl::kHeaderFlags);
  if (b.type == 10) {
    b.cls = fh1list::kClassType10;
    return b;
  }
  if (b.type != 9) return b;
  b.cls = kRecorded;
  const uint32_t chain = Load32(base, cb + 116);
  for (uint32_t link = chain, n = 0; Plausible(link) && n < 64; link = Load32(base, link), ++n) {
    b.segments += Load32(base, link + 4);
  }
  if (!Plausible(chain) || !Load32(base, chain + 4)) return b;
  b.words = Load32(base, chain + 8) & 0xFFFFFF;
  b.phys = Load32(base, chain + 12) & 0x1FFFFFFF;
  // A list from the files keeps the files' marker 11223344 55667789 a few words after its patch
  // table (padding between them), as the loader copied it. (Its PM4's trailer is not a test:
  // the first trailer word is rewritten at run time.)
  const uint32_t patch = Load32(base, cb + 144);
  const uint32_t patch_words = Load32(base, cb + nl::kHeaderPatchWords);
  if (Plausible(patch) && patch_words < (1u << 16)) {
    const uint32_t after = patch + patch_words * 4;
    for (uint32_t i = 0; i < 8; ++i) {
      if (Load32(base, after + i * 4) == 0x11223344u &&
          Load32(base, after + i * 4 + 4) == 0x55667789u) {
        b.cls = kModel;
        break;
      }
    }
  }
  return b;
}

bool UntiledNow(uint8_t* base, const Buffer& b) {
  if (!(b.flags & nl::kFlagTiles) || b.words <= 4) return false;
  const uint32_t w = Load32(base, PhysicalToGuest(b.phys) + 16);
  return w >> 30 == 3 && ((w >> 8) & 0x7F) == scene_tiling::kPm4Nop;
}

// Registers every segment of command buffer cb, and counts the call on each.
void RegisterSegments(Shm* s, uint8_t* base, uint32_t cb, const Buffer& b, uint8_t group,
                      uint8_t context) {
  const uint32_t chain = Load32(base, cb + 116);
  for (uint32_t link = chain, n = 0; Plausible(link) && n < 64; link = Load32(base, link), ++n) {
    const uint32_t count = Load32(base, link + 4);
    for (uint32_t i = 0; i < count && i < 256; ++i) {
      const uint32_t words = Load32(base, link + 8 + i * 8) & 0xFFFFFF;
      const uint32_t phys = Load32(base, link + 12 + i * 8) & 0x1FFFFFFF;
      if (!phys) continue;
      const uint32_t h = fh1list::Slot(phys, fh1list::kListBits);
      for (uint32_t k = 0; k < fh1list::kProbe; ++k) {
        fh1list::List& e = s->lists[(h + k) & (fh1list::kLists - 1)];
        uint32_t p = __atomic_load_n(&e.phys, __ATOMIC_ACQUIRE);
        bool mine = false;
        if (!p) {
          uint32_t zero = 0;
          mine = __atomic_compare_exchange_n(&e.phys, &zero, phys, false, __ATOMIC_ACQ_REL,
                                             __ATOMIC_ACQUIRE);
          p = mine ? phys : zero;
        }
        if (p != phys) continue;
        if (mine) {
          e.words = words;
          e.cb = cb;
          e.flags = b.flags;
          e.cls = b.cls;
          e.group = group;
          e.context = context;
          __atomic_store_n(&e.ready, uint8_t(1), __ATOMIC_RELEASE);
        } else if (__atomic_load_n(&e.ready, __ATOMIC_ACQUIRE)) {
          if (e.cb != cb || e.words != words || e.cls != b.cls) {
            e.cb = cb;
            e.words = words;
            e.cls = b.cls;
            e.flags = b.flags;
            e.group = group;
            e.context = context;
            __atomic_fetch_add(&e.binds, 1u, __ATOMIC_RELAXED);
            Count(s, T(kTRebind) | uint64_t(b.cls) << 48);
          } else if (e.group == kGroupNone && group != kGroupNone) {
            e.group = group;
            e.context = context;
          }
        }
        __atomic_fetch_add(&e.calls, 1, __ATOMIC_RELAXED);
        break;
      }
    }
  }
}

// The last large copies into physical memory (memcpy sub_82B3A7E0, hooked below): who wrote a
// recorded command buffer's words, looked up at its first run.
struct GuestCopy {
  std::atomic<uint32_t> phys{0}, bytes{0}, lr{0}, src{0};
};
constexpr uint32_t kCopies = 8192;
GuestCopy g_copies[kCopies];
std::atomic<uint32_t> g_copy_next{0};

void CountWriter(Shm* s, const Buffer& b, uint8_t group) {
  uint32_t lr = 0, src = 0;
  const uint32_t next = g_copy_next.load(std::memory_order_relaxed);
  for (uint32_t k = 1; k <= kCopies && k <= next; ++k) {
    const GuestCopy& c = g_copies[(next - k) % kCopies];
    const uint32_t p = c.phys.load(std::memory_order_relaxed);
    if (b.phys >= p && b.phys < p + c.bytes.load(std::memory_order_relaxed)) {
      lr = c.lr.load(std::memory_order_relaxed);
      src = c.src.load(std::memory_order_relaxed);
      break;
    }
  }
  // The source: 1 a physical view, 2 another guest address, 0 none found.
  const uint32_t from = !lr ? 0 : src >= 0xA0000000u ? 1 : 2;
  Count(s, T(kTRecordedWriter) | uint64_t(group) << 48 | uint64_t(lr) << 8 | from);
}

// Command buffers made by D3D's clone function (sub_82AA8220, hooked below: it copies a command
// buffer's PM4 and tables into new memory and marks the copy with 0x00100000 in its first word):
// the copy -> (the source's class and group if it ran before, the caller).
struct CloneOf {
  uint8_t source_cls = 0, source_group = kGroupNone;
  uint32_t lr = 0;
};
std::mutex g_clone_mutex;
std::unordered_map<uint32_t, CloneOf> g_clones;
uint8_t ClassOfSource(uint8_t* base, uint32_t cb) { return Classify(base, cb).cls; }

// What a command buffer looked like the first time it ran, and its template.
struct CommandBuffer {
  uint32_t masks[nl::kMaskWords] = {};
  uint32_t flags = 0;
  uint8_t cls = 0, group = kGroupNone;
  uint32_t phys = 0, words = 0, patch_words = 0;  // its segment and patch table then
  bool converted = false, dirty = false;
  std::vector<uint32_t> tmpl;
  std::vector<uint8_t> header, patch, area_c, pm4;  // copies at the first look, for the key's check
  uint64_t calls = 0;
};
std::mutex g_mutex;
std::unordered_map<uint32_t, CommandBuffer> g_buffers;

std::vector<uint8_t> Copy(uint8_t* base, uint32_t address, uint32_t bytes) {
  std::vector<uint8_t> v(bytes);
  if (bytes) std::memcpy(v.data(), forza::GuestPtr(base, address), bytes);
  return v;
}

// Masks as the files have them: 0 all ones; 1 all ones but bit 3 of the third pair (register
// 0x2208), inherited and untouched alike (passes 0 and 6); 2 another value, both halves equal;
// 3 the halves differ.
uint32_t MaskKind(const uint32_t* m) {
  bool all = true, same = true;
  for (uint32_t i = 0; i < 10; ++i) {
    all &= m[i] == 0xFFFFFFFFu && m[i + 10] == 0xFFFFFFFFu;
    same &= m[i] == m[i + 10];
  }
  if (all) return 0;
  if (!same) return 3;
  for (uint32_t i = 0; i < 10; ++i) {
    if (m[i] != (i == 5 ? 0xFFFFFFF7u : 0xFFFFFFFFu)) return 2;
  }
  return 1;
}

// Counts the low two bits of every IM_LOAD's address word (when: 0 the first look, 1 after a
// shader fixup).
void CountShaderLoads(Shm* s, const uint8_t* pm4, uint32_t words, uint8_t group, uint32_t when) {
  uint32_t loads = 0;
  for (uint32_t i = 0; i < words;) {
    const uint32_t h = nl::Word(pm4, i);
    switch (h >> 30) {
      case 0:
        i += ((h >> 16) & 0x3FFF) + 2;
        break;
      case 1:
        i += 3;
        break;
      case 2:
        i += 1;
        break;
      default: {
        const uint32_t count = ((h >> 16) & 0x3FFF) + 1;
        if (((h >> 8) & 0x7F) == nl::kPm4ImLoad && count >= 2 && i + 1 < words) {
          const uint32_t bits = nl::Word(pm4, i + 1) & 3;
          Count(s, T(kTImLoad) | uint64_t(group) << 40 | uint64_t(loads & 1) << 16 |
                       uint64_t(bits) << 8 | when);
          ++loads;
        }
        i += count + 1;
        break;
      }
    }
  }
}

// The first command buffers of each context and flags, word by word, in the log (FH1_LIST_DUMP=N:
// N of each, default 0).
void Dump(uint8_t* base, uint32_t cb, const Buffer& b, uint8_t group, uint8_t context) {
  static const uint32_t per = [] {
    const char* e = std::getenv("FH1_LIST_DUMP");
    return e ? uint32_t(std::atoi(e)) : 0u;
  }();
  if (!per) return;
  static std::mutex mutex;
  static std::unordered_map<uint64_t, uint32_t> seen;
  {
    std::lock_guard<std::mutex> lock(mutex);
    if (seen[uint64_t(context) << 32 | b.flags]++ >= per) return;
  }
  std::string line = fmt::format("list trace: dump cb {:08X} ctx {} group {} flags {:X} words {} phys {:08X}:",
                                 cb, context, group, b.flags, b.words, b.phys);
  for (uint32_t i = 0; i < nl::kHeaderBytes; i += 4) {
    line += fmt::format(" {:02X}={:08X}", i, Load32(base, cb + i));
  }
  const uint32_t patch_words = Load32(base, cb + nl::kHeaderPatchWords);
  const uint32_t after = Load32(base, cb + 144) + std::min<uint32_t>(patch_words, 4096) * 4;
  line += " | after patch:";
  for (uint32_t i = 0; i < 12; ++i) line += fmt::format(" {:08X}", Load32(base, after + i * 4));
  const uint32_t end = PhysicalToGuest(b.phys) + b.words * 4;
  line += " | pm4 head:";
  for (uint32_t i = 0; i < 6; ++i) line += fmt::format(" {:08X}", Load32(base, PhysicalToGuest(b.phys) + i * 4));
  line += " | after pm4:";
  for (uint32_t i = 0; i < 4; ++i) line += fmt::format(" {:08X}", Load32(base, end + i * 4));
  REXLOG_INFO("{}", line);
}

// FH1_LIST_KEYS=<file>: every converted list's key at its first look (group, flags in memory,
// PM4 dwords, key), to compare with the keys of the files' lists (tools/native_list_check).
void WriteKey(uint8_t group, uint32_t flags, const nl::Input& in, const nl::Key& key) {
  static std::FILE* out = [] {
    const char* path = std::getenv("FH1_LIST_KEYS");
    return path && *path ? std::fopen(path, "w") : nullptr;
  }();
  if (!out) return;
  std::fprintf(out, "%u %X %u %u %08X%08X%08X%08X\n", unsigned(group), flags, in.pm4_words,
               in.area_c_words, key.w[0], key.w[1], key.w[2], key.w[3]);
  std::fflush(out);
}

void FirstLook(Shm* s, uint8_t* base, uint32_t cb, const Buffer& b, uint8_t group,
               CommandBuffer& c) {
  c.cls = b.cls;
  c.group = group;
  c.flags = b.flags;
  c.phys = b.phys;
  c.words = b.words;
  c.patch_words = Load32(base, cb + nl::kHeaderPatchWords);
  Dump(base, cb, b, group, uint8_t(t_context.kind));
  for (uint32_t i = 0; i < nl::kMaskWords; ++i) {
    c.masks[i] = Load32(base, cb + nl::kHeaderMasks + i * 4);
  }
  Count(s, T(kTNewCommandBuffer) | uint64_t(b.cls) << 48 | uint64_t(group) << 40);
  Count(s, T(kTSegments) | uint64_t(b.cls) << 48 | std::min<uint32_t>(b.segments, 0xFFFF));
  Count(s, T(kTHeader) | uint64_t(b.cls) << 48 | uint64_t(group) << 40 |
               uint64_t(MaskKind(c.masks)) << 32 | b.flags);
  if (b.cls == kRecorded) {
    CountWriter(s, b, group);
    CloneOf from;
    bool cloned = false;
    {
      std::lock_guard<std::mutex> lock(g_clone_mutex);
      auto it = g_clones.find(cb);
      if (it != g_clones.end()) from = it->second, cloned = true;
    }
    // 0xFF: not made by the clone function since the trace started.
    Count(s, T(kTCloneOf) | uint64_t(group) << 48 | uint64_t(cloned ? from.source_cls : 0xFF) << 40 |
                 uint64_t(from.source_group) << 32 | from.lr);
  }
  if (b.cls != kModel) return;
  const uint32_t patch_words =
      std::min<uint32_t>(Load32(base, cb + nl::kHeaderPatchWords), 1u << 16);
  const uint32_t patch = Load32(base, cb + 144);
  c.header = Copy(base, cb, nl::kHeaderBytes);
  Count(s, T(kTLoaderFlags) | uint64_t(group) << 40 | b.flags);
  {
    const uint32_t flags = FileFormFlags(b.flags);
    for (uint32_t i = 0; i < 4; ++i) c.header[nl::kHeaderFlags + i] = uint8_t(flags >> (24 - 8 * i));
  }
  c.patch = Plausible(patch) ? Copy(base, patch, patch_words * 4) : std::vector<uint8_t>{};
  // Area C (the buffer fixups): its length is at +0x80 in the files' headers (words), its address
  // at +0x94 (148) once loaded.
  const uint32_t area_c_words = Load32(base, cb + 0x80);
  const uint32_t area_c = Load32(base, cb + 148);
  Count(s, T(kTAreaC) | uint64_t(group) << 40 | uint64_t(Plausible(area_c) ? 1 : 0) << 32 |
               std::min<uint32_t>(area_c_words, 0xFFFF));
  if (Plausible(area_c) && area_c_words <= 4096) c.area_c = Copy(base, area_c, area_c_words * 4);
  c.pm4 = Copy(base, PhysicalToGuest(b.phys), std::min<uint32_t>(b.words, nl::kMaxListWords) * 4);
  nl::Input in;
  in.header = c.header.data();
  in.patch_table = c.patch.data();
  in.patch_words = uint32_t(c.patch.size() / 4);
  in.area_c = c.area_c.data();
  in.area_c_words = uint32_t(c.area_c.size() / 4);
  in.pm4 = c.pm4.data();
  in.pm4_words = uint32_t(c.pm4.size() / 4);
  const nl::Result r =
      b.segments == 1 ? nl::Convert(in, &c.tmpl) : nl::Result{nl::Refuse::kSegments};
  c.converted = bool(r);
  if (!c.converted) c.tmpl.clear();
  Count(s, T(kTConvert) | uint64_t(b.cls) << 48 | uint64_t(group) << 40 |
               uint64_t(r.refuse) << 32 | uint64_t(UntiledNow(base, b)));
  Count(s, T(kTConvertWords) | uint64_t(group) << 40 | 0);
  Count(s, T(kTConvertWords) | uint64_t(group) << 40 | 1, in.pm4_words);
  if (c.converted) {
    Count(s, T(kTConvertWords) | uint64_t(group) << 40 | 2, c.tmpl.size());
    Count(s, T(kTConvertWords) | uint64_t(group) << 40 | 3);
  } else {
    static std::atomic<uint32_t> told{0};
    if (told.fetch_add(1) < 20) {
      REXLOG_INFO("list trace: list {:08X} (group {}) not converted: {} at {}", cb, group,
                  nl::RefuseName(r.refuse), r.at);
    }
  }
  CountShaderLoads(s, c.pm4.data(), in.pm4_words, group, 0);
  if (c.converted) WriteKey(group, b.flags, in, nl::TemplateKey(c.tmpl.data()));
}

// The live list's static words against the template's key; where they differ from the first
// look when they do.
void CheckKey(Shm* s, uint8_t* base, uint32_t cb, const Buffer& b, CommandBuffer& c) {
  const uint32_t patch = Load32(base, cb + 144);
  // The live header with its flags in the files' form, as the template was converted.
  uint8_t header[nl::kHeaderBytes];
  std::memcpy(header, forza::GuestPtr(base, cb), sizeof(header));
  const uint32_t flags = FileFormFlags(b.flags);
  for (uint32_t i = 0; i < 4; ++i) header[nl::kHeaderFlags + i] = uint8_t(flags >> (24 - 8 * i));
  nl::Input in;
  in.header = header;
  in.patch_table = forza::GuestPtr(base, Plausible(patch) ? patch : cb);
  in.patch_words = uint32_t(c.patch.size() / 4);
  const uint32_t area_c = Load32(base, cb + 148);
  in.area_c = forza::GuestPtr(base, Plausible(area_c) ? area_c : cb);
  in.area_c_words = uint32_t(c.area_c.size() / 4);
  in.pm4 = forza::GuestPtr(base, PhysicalToGuest(b.phys));
  in.pm4_words = uint32_t(c.pm4.size() / 4);
  const bool words_same = b.words == in.pm4_words &&
                          Load32(base, cb + nl::kHeaderPatchWords) == in.patch_words &&
                          (in.area_c_words == 0 || Load32(base, cb + 0x80) == in.area_c_words);
  const bool same = words_same && nl::LiveKey(c.tmpl.data(), in) == nl::TemplateKey(c.tmpl.data());
  const bool untiled = UntiledNow(base, b);
  Count(s, T(kTLiveKey) | uint64_t(c.group) << 40 | uint64_t(same ? 0 : 1) << 8 | untiled);
  if (same) return;
  // The first word the key hashes that differs from the first look (as LiveKey reads them): 1 the
  // flags or masks, 2 the patch table, 3 the PM4 outside the places, 4 a length, 5 area C.
  uint32_t region = words_same ? 0 : 4, at = 0;
  if (!region && (nl::Word(in.header, nl::kHeaderFlags / 4) & ~nl::kFlagFixedUp) !=
                     (nl::Word(c.header.data(), nl::kHeaderFlags / 4) & ~nl::kFlagFixedUp)) {
    region = 1, at = nl::kHeaderFlags / 4;
  }
  for (uint32_t i = 0; !region && i < nl::kMaskWords; ++i) {
    const uint32_t w = nl::kHeaderMasks / 4 + i;
    if (nl::Word(in.header, w) != nl::Word(c.header.data(), w)) region = 1, at = w;
  }
  for (uint32_t i = 0; !region && i < in.patch_words; ++i) {
    if (nl::Word(in.patch_table, i) != nl::Word(c.patch.data(), i)) region = 2, at = i;
  }
  for (uint32_t i = 0; !region && i < in.area_c_words; ++i) {
    if (nl::Word(in.area_c, i) != nl::Word(c.area_c.data(), i)) region = 5, at = i;
  }
  {
    const uint32_t* t = c.tmpl.data();
    const uint32_t* slot = t + t[nl::kTSlots];
    const uint32_t* slots_end = slot + t[nl::kTSlotCount] * nl::kSlotWords;
    const uint32_t* bound = t + t[nl::kTTileBound];
    const uint32_t* bound_end = bound + t[nl::kTTileBoundCount] * 2;
    for (uint32_t d = 0; !region && d < in.pm4_words;) {
      if (slot != slots_end && slot[1] == d) {
        const uint32_t type = slot[0] & 0xFF, argument = slot[0] >> 8;
        const bool microcode =
            (type == nl::kSlotVertexShader || type == nl::kSlotPixelShader) && argument == 0;
        for (uint32_t k = 0; k < slot[3] && d < in.pm4_words; ++k, ++d) {
          if (microcode && (nl::Word(in.pm4, d) & 3) != (nl::Word(c.pm4.data(), d) & 3)) {
            region = 3, at = d;
          }
        }
        slot += nl::kSlotWords;
        continue;
      }
      uint32_t w = nl::Word(in.pm4, d), was = nl::Word(c.pm4.data(), d);
      if (bound != bound_end && bound[0] == d) {
        if (w == scene_tiling::NopPacket(bound[1])) w = bound[1];
        if (was == scene_tiling::NopPacket(bound[1])) was = bound[1];
        bound += 2;
      }
      if (w != was) region = 3, at = d;
      ++d;
    }
  }
  Count(s, T(kTLiveKeyWhere) | uint64_t(c.group) << 40 | uint64_t(region) << 8 | untiled);
  Count(s, T(kTKeyDword) | uint64_t(c.group) << 40 | uint64_t(region) << 32 | (at & 0xFFFFF));
  static std::atomic<uint32_t> told{0};
  if (told.fetch_add(1) < 20) {
    REXLOG_INFO("list trace: list {:08X} (group {}) has another key: region {} dword {}{}", cb,
                c.group, region, at, untiled ? " (untiled)" : "");
  }
}

}  // namespace

ContextState& CurrentContext() { return t_context; }

// sub_824467A8 (r3 the renderer, r4 the command buffer): on a thread whose TLS byte +8 is set and
// with a queue at renderer+9348, the list is queued; otherwise it runs on *(renderer+9312, or
// +9316 on such a thread)+20.
void RecordedList(PPCContext& ctx, uint8_t* base) {
  Shm* s = Get();
  if (!s) return;
  const uint32_t renderer = ctx.r3.u32, cb = ctx.r4.u32;
  if (!Plausible(renderer)) return;
  const uint32_t tls = Plausible(ctx.r13.u32) ? Load32(base, ctx.r13.u32) : 0;
  const uint32_t flag = Plausible(tls) ? (Load8(base, tls + 8) ? 1 : 0) : 0;
  const uint32_t queued = flag && Load32(base, renderer + 9348) ? 1 : 0;
  const uint32_t holder = Load32(base, renderer + (flag ? 9316 : 9312));
  const uint32_t dev = Plausible(holder) ? Load32(base, holder + 20) : 0;
  const Buffer b = Classify(base, cb);
  Count(s, T(kTRecordedCaller) | uint64_t(uint32_t(ctx.lr)) << 16 | uint64_t(queued) << 15 |
               uint64_t(flag) << 14 | uint64_t(b.cls) << 8 | uint64_t(t_context.kind) << 4);
  Count(s, T(kTRecordedDevice) | uint64_t(flag) << 16 | uint64_t(DeviceSlot(dev)) << 8 | queued);
}

// sub_824469B0 (r3 the device, r4 the command buffer, r5 the bin select of a predicated list).
void Run(PPCContext& ctx, uint8_t* base) {
  Shm* s = Get();
  if (!s) return;
  const uint32_t dev = ctx.r3.u32, cb = ctx.r4.u32;
  const uint32_t slot = DeviceSlot(dev);
  Count(s, T(kTDevice) | uint64_t(slot) << 48 | dev);
  const uint32_t mode = Plausible(dev) ? Load8(base, dev + 11068) : 0;
  const Buffer b = Classify(base, cb);
  const uint8_t group = GroupOf(t_context);
  const uint8_t context = uint8_t(t_context.kind);
  const bool untiled = UntiledNow(base, b);
  Count(s, T(kTCalls) | uint64_t(slot) << 52 | uint64_t(mode) << 44 |
               uint64_t(b.flags & 0xFFFF) << 28 | uint64_t(b.cls) << 24 | uint64_t(group) << 16 |
               uint64_t(context) << 12 | uint64_t(untiled) << 8 | uint64_t(b.type) << 4);
  Count(s, T(kTRunCaller) | uint64_t(uint32_t(ctx.lr)) << 16 | uint64_t(b.cls) << 8 |
               uint64_t(b.type) << 4);
  if (mode & 0x80) Count(s, T(kTRecording) | uint64_t(b.cls) << 48 | uint64_t(group) << 40);
  if (b.type != 9) return;
  RegisterSegments(s, base, cb, b, group, context);
  std::lock_guard<std::mutex> lock(g_mutex);
  auto [it, fresh] = g_buffers.try_emplace(cb);
  CommandBuffer& c = it->second;
  // Another list in the same command buffer object (the old one unloaded, the memory used
  // again): its segment, its length or its patch table's length changed. It counts as new.
  const bool reused = !fresh && (c.cls != b.cls || c.phys != b.phys || c.words != b.words ||
                                 c.patch_words != Load32(base, cb + nl::kHeaderPatchWords));
  if (reused) Count(s, T(kTReused) | uint64_t(c.cls) << 48 | uint64_t(b.cls) << 40);
  if (fresh || reused) {
    c = CommandBuffer{};
    FirstLook(s, base, cb, b, group, c);
    ++c.calls;
    return;
  }
  ++c.calls;
  uint32_t changed = 0;
  for (uint32_t i = 0; i < nl::kMaskWords; ++i) {
    changed |= Load32(base, cb + nl::kHeaderMasks + i * 4) != c.masks[i];
  }
  Count(s, T(kTHeaderChange) | uint64_t(b.cls) << 48 | uint64_t(c.group) << 40 |
               uint64_t(changed) << 32 | (b.flags ^ c.flags));
  if (!c.converted) return;
  if (c.dirty) {
    CountShaderLoads(s, forza::GuestPtr(base, PhysicalToGuest(b.phys)),
                     std::min<uint32_t>(b.words, uint32_t(c.pm4.size() / 4)), c.group, 1);
    c.dirty = false;
  }
  if (c.calls % CheckEvery() == 0) CheckKey(s, base, cb, b, c);
}

}  // namespace forza::list_trace

// The fixup dispatcher (r3: a pointer to the command buffer, r4: the record, its type in the low
// 4 bits; docs/native-renderer.md "M4"). With the D3D trace too, this hook opens its scope.
extern "C" REX_FUNC(__imp__sub_82D19210);
extern "C" REX_FUNC(sub_82D19210) {
  FORZA_D3D_TRACE_SCOPE(sub_82D19210);
  using namespace forza::list_trace;
  if (fh1list::Shm* s = Get()) {
    const uint32_t cb = Plausible(ctx.r3.u32) ? Load32(base, ctx.r3.u32) : 0;
    const uint32_t type = Plausible(ctx.r4.u32) ? Load32(base, ctx.r4.u32) & 15 : 0;
    uint8_t cls = 0, group = kGroupNone;
    {
      std::lock_guard<std::mutex> lock(g_mutex);
      auto it = g_buffers.find(cb);
      if (it != g_buffers.end()) {
        cls = it->second.cls;
        group = it->second.group;
        if (type == 9 || type == 10) it->second.dirty = true;
      }
    }
    Count(s, T(kTFixup) | uint64_t(type) << 48 | uint64_t(cls) << 40 | uint64_t(group) << 32);
  }
  __imp__sub_82D19210(ctx, base);
}

// RunCommandBuffer's third direct caller (called indirectly).
extern "C" REX_FUNC(__imp__sub_82D10908);
extern "C" REX_FUNC(sub_82D10908) {
  using namespace forza::list_trace;
  if (fh1list::Shm* s = Get()) Count(s, T(kTCaller10908) | uint32_t(ctx.lr));
  __imp__sub_82D10908(ctx, base);
}

// The end of sub_824467A8's queued path (its only caller).
extern "C" REX_FUNC(__imp__sub_82CE9258);
extern "C" REX_FUNC(sub_82CE9258) {
  using namespace forza::list_trace;
  if (fh1list::Shm* s = Get()) Count(s, T(kTCaller9258) | uint32_t(ctx.lr));
  __imp__sub_82CE9258(ctx, base);
}

// memcpy (r3 the destination, r4 the source, r5 the bytes): copies of 512 bytes or more into a
// physical view are kept for CountWriter.
extern "C" REX_FUNC(__imp__sub_82B3A7E0);
extern "C" REX_FUNC(sub_82B3A7E0) {
  using namespace forza::list_trace;
  const uint32_t dest = ctx.r3.u32, bytes = ctx.r5.u32;
  if (bytes >= 512 && dest >= 0xA0000000u && Get()) {
    GuestCopy& c = g_copies[g_copy_next.fetch_add(1, std::memory_order_relaxed) % kCopies];
    c.phys.store(dest >= 0xE0000000u ? (dest & 0x1FFFFFFFu) + 0x1000u : dest & 0x1FFFFFFFu,
                 std::memory_order_relaxed);
    c.bytes.store(bytes, std::memory_order_relaxed);
    c.lr.store(uint32_t(ctx.lr), std::memory_order_relaxed);
    c.src.store(ctx.r4.u32, std::memory_order_relaxed);
  }
  __imp__sub_82B3A7E0(ctx, base);
}

// D3D's command buffer clone (r3 the source command buffer, r4 flags; returns the copy in r3).
extern "C" REX_FUNC(__imp__sub_82AA8220);
extern "C" REX_FUNC(sub_82AA8220) {
  using namespace forza::list_trace;
  const uint32_t source = ctx.r3.u32, lr = uint32_t(ctx.lr);
  fh1list::Shm* s = Get();
  __imp__sub_82AA8220(ctx, base);
  if (!s) return;
  CloneOf c;
  c.lr = lr;
  c.source_cls = ClassOfSource(base, source);
  {
    std::lock_guard<std::mutex> lock(g_mutex);
    auto it = g_buffers.find(source);
    if (it != g_buffers.end()) c.source_group = it->second.group;
  }
  Count(s, T(kTClone) | uint64_t(lr) << 16 | uint64_t(c.source_cls) << 8 | (ctx.r3.u32 ? 1 : 0));
  if (ctx.r3.u32) {
    std::lock_guard<std::mutex> lock(g_clone_mutex);
    g_clones[ctx.r3.u32] = c;
  }
}
