// forza - native renderer M4, third step (docs/native-renderer.md "M4, third step"): the model
// list registry and its shadow compare, behind native_lists (off by default).
//
// With native_lists=shadow, at each RunCommandBuffer call (TU4 sub_824469B0, the hook in
// cockpit_gauges.cpp) on the game's D3D device (the device of the first model list call: a call
// on any other is counted and not registered), of a model's list (a command buffer of type 9
// with the files' marker after its patch table) that is not registered yet:
// - the list is converted (src/platform/native_list.h) from a copy of its header (with its flags
//   in the files' form, FilesFormFlags), its patch table and its PM4; area C is not kept in
//   memory, so the key and the slots come from those alone. A list scene_msaa has untiled (the
//   scene pass's lists run for passes 3 and 5 with scene_msaa=2x|off and in the game's 12- and
//   16-car races: their first run is already untiled) is converted from a copy with its
//   tile-bound headers put back from the ones scene_tiling.cpp kept;
// - its template is written into a ring of guest memory and sent once per key (kOpListDefine);
//   the plugin acknowledges what it has read, and the ring reuses only that;
// - its physical address is bound to the template (kOpListBind) with its state word (a word of
//   a guest table: the generation, which the fixup dispatcher's hook makes odd before a fixup
//   writes the list and even again after it), its command buffer, patch table and pass group.
//   A list the converter refuses is bound too, with the reason, so the plugin can count its
//   runs.
// The packets go into the device's command buffer just before the call's own words (ReIGlue
// 0650 handles them on the decoder, as it reads them), so the list is registered before its
// indirect buffer is decoded. A command buffer object that holds another list now (the old one
// unloaded) is dropped and registered again; a model's unload (TU4 sub_82D19668, which frees its
// headers and its PM4) drops its lists (kOpListDrop) at the next call. The plugin also checks
// the indirect buffer's length and the command buffer at every lookup, and the list's static
// words at every run.
//
// The switch is read once, at the first call. Off, each hook is one load and one branch.
//
// tu_address_map: TU4 addresses

#include "native_lists.h"

#include "../debug/d3d_trace.h"
#include "../debug/list_trace.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/native_list.h"
#include "../platform/native_list_registry.h"
#include "../platform/native_shadow_record.h"
#include "../platform/scene_tiling.h"
#include "native_device.h"

#include <fmt/format.h>
#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

REXCVAR_DEFINE_STRING(native_lists, "off", "Display",
                      "Testing (native renderer): off; shadow (the track's model lists are "
                      "converted and registered with the GPU plugin, which compares every run "
                      "of one with its template; nothing drawn changes). Read at the start");

namespace forza::native_lists {

std::atomic<int> g_state{0};
namespace {
thread_local uint32_t t_group = 0xFF;
}  // namespace

bool ReadSwitch() {
  static std::once_flag once;
  std::call_once(once, [] {
    const std::string mode = REXCVAR_GET(native_lists);
    const bool on = mode == "shadow";
    g_state.store(on ? 1 : 2, std::memory_order_relaxed);
    if (on)
      REXLOG_INFO("native_lists: shadow (the model lists are registered with the GPU plugin, "
                  "which compares each run with its template; nothing drawn changes)");
  });
  return g_state.load(std::memory_order_relaxed) == 1;
}

void SetGroup(uint32_t group) { t_group = group; }
uint32_t Group() { return t_group; }
uint32_t GroupScope::GroupOf(uint32_t pass, uint32_t layer) {
  const uint32_t group = scene_tiling::RecordedListPass(pass) * 3 + layer;
  return group < 21 ? group : 0xFF;
}

}  // namespace forza::native_lists

#if FORZA_TU4

namespace forza::scene_tiling_hooks {
// scene_tiling.cpp: the headers it replaced with NOPs in list cb (guest address, header).
std::vector<std::pair<uint32_t, uint32_t>> SavedHeaders(uint32_t cb);
}  // namespace forza::scene_tiling_hooks

namespace forza::native_lists {
namespace {

namespace nl = forza::native_list;

uint32_t Load32(uint8_t* base, uint32_t address) {
  uint32_t v;
  std::memcpy(&v, forza::GuestPtr(base, address), 4);
  return __builtin_bswap32(v);
}
void Store32(uint8_t* base, uint32_t address, uint32_t value) {
  value = __builtin_bswap32(value);
  std::memcpy(forza::GuestPtr(base, address), &value, 4);
}
bool Plausible(uint32_t address) { return address >= 0x00100000 && address < 0xF0000000; }
uint32_t PhysicalToGuest(uint32_t address) { return 0xA0000000u | (address & 0x1FFFFFFFu); }
// A guest address of a physical view as a physical address (the 0xE0000000 view is 4 KB off).
uint32_t PhysicalOf(uint32_t address) {
  if (address >= 0xE0000000u) return (address & 0x1FFFFFFFu) + 0x1000u;
  if (address >= 0xA0000000u) return address & 0x1FFFFFFFu;
  return 0xFFFFFFFFu;
}
uint64_t NowNs() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}

constexpr uint32_t kRingBytes = 4u << 20;
constexpr uint32_t kStateWords = 65536;
constexpr uint32_t kDeviceMode = 11068;      // the device's mode byte (0x80: recording)
constexpr uint32_t kCbChain = 116, kCbPatch = 144;

// What the counters say (the log line every 30 s).
struct Counts {
  uint64_t calls = 0, lists = 0, current = 0, fresh = 0, reused = 0, converted = 0, retiled = 0,
           refused = 0, defined = 0, define_words = 0, bound = 0, dropped = 0, unloads = 0,
           unload_by_header = 0, unload_by_address = 0, not_device = 0, recording = 0,
           ring_full = 0, no_room = 0, fixups = 0, convert_ns = 0, run_ns = 0;
  uint64_t refusals[nl::kRefuseTooLarge + 1] = {};
};

struct State {
  std::mutex mutex;
  nl::Lists lists;
  nl::Templates templates;
  nl::DefineRing ring{kRingBytes};
  nl::Slots slots{kStateWords};
  nl::Converter converter;
  std::vector<nl::Bound> drops;  // unloaded, to go out at the next call
  uint32_t ring_base = 0, ack = 0, states = 0;
  uint32_t* define_ack_word = nullptr;
  uint32_t device = 0;  // the device of the first model list call
  bool memory_failed = false;
  std::vector<uint32_t> tmpl;
  std::vector<uint8_t> header, patch, pm4;
  Counts n;
  uint64_t next_log_ns = 0;
};
State& S() {
  static State* s = new State;  // kept until exit
  return *s;
}

bool LooksLikeDevice(uint8_t* base, uint32_t r3) {
  // As native_device.cpp: the cursor in a physical view, below the hard limit.
  namespace ns = forza::native_shadow;
  if (r3 < 0x40000000u || r3 >= 0x80000000u || (r3 & 3)) return false;
  const uint32_t c = Load32(base, r3 + ns::kCursor), hard = Load32(base, r3 + ns::kHardLimit);
  return c >= 0xA0000000u && !(c & 3) && hard >= c && hard - c <= (32u << 20);
}
const char* RefusalName(uint32_t r) {
  return r < uint32_t(nl::Refuse::kCount) ? nl::RefuseName(nl::Refuse(r))
         : r == nl::kRefuseRetile          ? "retile"
         : r == nl::kRefuseNoSlot          ? "no-state-word"
         : r == nl::kRefuseCollision       ? "key-collision"
                                           : "too-large";
}

bool AllocateMemory(State& s) {
  if (s.ring_base) return true;
  if (s.memory_failed) return false;
  auto* memory = REX_KERNEL_MEMORY();
  s.ring_base = memory->SystemHeapAlloc(kRingBytes, 0x1000);
  s.ack = memory->SystemHeapAlloc(nl::kDefineAckAlignment, nl::kDefineAckAlignment);
  s.states = memory->SystemHeapAlloc(kStateWords * 4, 0x1000);
  if (!s.ring_base || !s.ack || !s.states) {
    s.memory_failed = true;
    s.ring_base = 0;
    REXLOG_WARN("native_lists: no guest memory for the template ring and the state words");
    return false;
  }
  s.define_ack_word = std::construct_at(memory->TranslateVirtual<uint32_t*>(s.ack), 0u);
  return true;
}

bool Emit(PPCContext& ctx, uint8_t* base, uint32_t dev, const uint32_t* words, uint32_t n) {
  return forza::native_device::EmitHostPacket(ctx, base, dev, words, n);
}

void Log(State& s, uint64_t now) {
  const Counts& n = s.n;
  std::string refusals;
  for (uint32_t r = 0; r <= nl::kRefuseTooLarge; ++r) {
    if (!n.refusals[r]) continue;
    refusals += fmt::format(" {} {};", RefusalName(r), n.refusals[r]);
  }
  REXLOG_INFO(
      "native_lists: {} RunCommandBuffer calls, {} of model lists ({} registered already, {} "
      "new, {} in a reused command buffer); {} converted ({} from a retiled copy), {} refused "
      "({}); {} templates sent ({} words), {} lists bound now, {} dropped ({} unloads: {} by "
      "header, {} by address); not registered: {} on another device, {} recording, {} ring "
      "full, {} without room; {} fixups of bound lists; conversion {:.1f} ms in all, the hook "
      "{:.3f} ms in all ({:.2f} us a call)",
      n.calls, n.lists, n.current, n.fresh, n.reused, n.converted, n.retiled, n.refused,
      refusals.empty() ? " none" : refusals.c_str(), n.defined, n.define_words, s.lists.size(),
      n.dropped, n.unloads, n.unload_by_header, n.unload_by_address, n.not_device, n.recording,
      n.ring_full, n.no_room, n.fixups, double(n.convert_ns) * 1e-6, double(n.run_ns) * 1e-6,
      n.calls ? double(n.run_ns) * 1e-3 / double(n.calls) : 0.0);
  s.next_log_ns = now + 30'000'000'000ull;
}

// Sends the queued drops; false if there was no room (they stay queued).
bool SendDrops(PPCContext& ctx, uint8_t* base, uint32_t dev, State& s) {
  while (!s.drops.empty()) {
    const nl::Bound& b = s.drops.back();
    const uint32_t drop[nl::kListDropWords] = {nl::kMagicHost, nl::kOpListDrop, b.phys, b.words};
    if (!Emit(ctx, base, dev, drop, nl::kListDropWords)) return false;
    ++s.n.dropped;
    s.drops.pop_back();
  }
  return true;
}

// Converts command buffer cb (segment at phys, `words` long) and registers it.
void Register(PPCContext& ctx, uint8_t* base, uint32_t dev, uint32_t cb, uint32_t phys,
              uint32_t words, uint32_t segments, State& s) {
  const uint64_t t0 = NowNs();
  const uint32_t patch = Load32(base, cb + kCbPatch);
  const uint32_t patch_words = Load32(base, cb + nl::kHeaderPatchWords);
  uint32_t refusal = 0;
  bool refused = false, retiled = false;
  if (segments != 1) {
    refused = true;
    refusal = uint32_t(nl::Refuse::kSegments);
  } else if (!Plausible(patch) || patch_words > (1u << 16) || !words ||
             words > nl::kMaxListWords) {
    refused = true;
    refusal = uint32_t(nl::Refuse::kListSize);
  }
  if (!refused) {
    s.header.resize(nl::kHeaderBytes);
    std::memcpy(s.header.data(), forza::GuestPtr(base, cb), nl::kHeaderBytes);
    const uint32_t flags = nl::FilesFormFlags(nl::Word(s.header.data(), nl::kHeaderFlags / 4));
    const uint32_t g = __builtin_bswap32(flags);
    std::memcpy(s.header.data() + nl::kHeaderFlags, &g, 4);
    s.patch.resize(size_t(patch_words) * 4);
    if (patch_words) std::memcpy(s.patch.data(), forza::GuestPtr(base, patch), s.patch.size());
    s.pm4.resize(size_t(words) * 4);
    std::memcpy(s.pm4.data(), forza::GuestPtr(base, PhysicalToGuest(phys)), s.pm4.size());
    // Untiled now (word 4 a NOP in a list recorded for the tiles): the original headers back.
    const uint32_t fifth = words > 4 ? nl::Word(s.pm4.data(), 4) : 0;
    if ((flags & nl::kFlagTiles) && fifth >> 30 == 3 &&
        ((fifth >> 8) & 0x7F) == scene_tiling::kPm4Nop) {
      retiled = nl::Retile(s.pm4.data(), words, PhysicalToGuest(phys),
                           forza::scene_tiling_hooks::SavedHeaders(cb));
      if (!retiled) {
        refused = true;
        refusal = nl::kRefuseRetile;
      }
    }
  }
  uint32_t id = nl::kListRefused;
  nl::Key key;
  if (!refused) {
    nl::Input in;
    in.header = s.header.data();
    in.patch_table = s.patch.data();
    in.patch_words = patch_words;
    in.pm4 = s.pm4.data();
    in.pm4_words = words;
    const nl::Result r = s.converter.Convert(in, &s.tmpl);
    if (!r) {
      refused = true;
      refusal = uint32_t(r.refuse);
    } else {
      key = nl::TemplateKey(s.tmpl.data());
      bool collision = false;
      id = s.templates.Id(key, nl::TemplateHash(s.tmpl.data(), s.tmpl.size()),
                          uint32_t(s.tmpl.size()), &collision);
      if (collision) {
        refused = true;
        refusal = nl::kRefuseCollision;
        id = nl::kListRefused;
      }
    }
  }
  s.n.convert_ns += NowNs() - t0;
  if (!refused && !s.templates.Sent(key) && !s.ring.Fits(uint32_t(s.tmpl.size() * 4))) {
    // Never fits: bound as refused, not converted again at every call.
    refused = true;
    refusal = nl::kRefuseTooLarge;
    id = nl::kListRefused;
  }
  if (!refused && !s.templates.Sent(key)) {
    const uint32_t bytes = uint32_t(s.tmpl.size() * 4);
    uint32_t offset = 0, sequence = 0;
    if (!s.ring.Reserve(bytes, nl::LoadDefineAck(*s.define_ack_word), &offset, &sequence)) {
      ++s.n.ring_full;
      return;  // the next call tries again
    }
    uint32_t* to = reinterpret_cast<uint32_t*>(forza::GuestPtr(base, s.ring_base + offset));
    for (size_t i = 0; i < s.tmpl.size(); ++i) to[i] = __builtin_bswap32(s.tmpl[i]);
    const uint32_t define[nl::kListDefineWords] = {
        nl::kMagicHost, nl::kOpListDefine, id, s.ring_base + offset, uint32_t(s.tmpl.size()),
        sequence, nl::TemplateHash(s.tmpl.data(), s.tmpl.size()), s.ack};
    if (!Emit(ctx, base, dev, define, nl::kListDefineWords)) {
      s.ring.CancelUnpublished(sequence);
      ++s.n.no_room;
      return;
    }
    s.templates.MarkSent(key);
    ++s.n.defined;
    s.n.define_words += s.tmpl.size();
  }
  nl::Bound b;
  b.phys = phys;
  b.words = words;
  b.patch_words = patch_words;
  b.template_id = id;
  b.slot = nl::kNone;
  if (!refused && !s.slots.Take(&b.slot)) {
    refused = true;
    refusal = nl::kRefuseNoSlot;
    b.template_id = nl::kListRefused;
    b.slot = nl::kNone;
  }
  const uint32_t state = b.slot != nl::kNone ? s.states + b.slot * 4 : 0;
  const uint32_t bind[nl::kListBindWords] = {
      nl::kMagicHost, nl::kOpListBind, phys,        words, b.template_id, refusal, state, cb,
      patch,          patch_words,     Group(), retiled ? nl::kListRetiled : 0u};
  if (!Emit(ctx, base, dev, bind, nl::kListBindWords)) {
    if (b.slot != nl::kNone) s.slots.Give(b.slot);
    ++s.n.no_room;
    return;
  }
  s.lists.Set(cb, b);
  ++s.n.bound;
  if (refused) {
    ++s.n.refused;
    ++s.n.refusals[std::min<uint32_t>(refusal, nl::kRefuseTooLarge)];
    static std::atomic<uint32_t> told{0};
    if (told.fetch_add(1) < 10)
      REXLOG_INFO("native_lists: list {:08X} (group {}) not converted: {}", cb, Group(),
                  RefusalName(refusal));
  } else {
    ++s.n.converted;
    if (retiled) ++s.n.retiled;
  }
}

}  // namespace

void OnRun(PPCContext& ctx, uint8_t* base) {
  const uint64_t t0 = NowNs();
  State& s = S();
  const uint32_t dev = ctx.r3.u32, cb = ctx.r4.u32;
  std::lock_guard<std::mutex> lock(s.mutex);
  ++s.n.calls;
  struct Timed {
    State& s;
    uint64_t t0;
    ~Timed() {
      const uint64_t now = NowNs();
      s.n.run_ns += now - t0;
      if (now >= s.next_log_ns) Log(s, now);
    }
  } timed{s, t0};
  if (!Plausible(cb) || (Load32(base, cb) & 15) != 9) return;
  if (!LooksLikeDevice(base, dev) || (s.device && dev != s.device)) {
    ++s.n.not_device;
    return;
  }
  // A model's list: the files' marker a few words after its patch table (the loader keeps it).
  const uint32_t patch = Load32(base, cb + kCbPatch);
  const uint32_t patch_words = Load32(base, cb + nl::kHeaderPatchWords);
  if (!Plausible(patch) || patch_words >= (1u << 16)) return;
  bool model = false;
  for (uint32_t i = 0, after = patch + patch_words * 4; i < 8 && !model; ++i) {
    model = Load32(base, after + i * 4) == 0x11223344u &&
            Load32(base, after + i * 4 + 4) == 0x55667789u;
  }
  if (!model) return;
  if (!s.device) s.device = dev;  // the game's device: every later call is checked against it
  ++s.n.lists;
  if (forza::GuestPtr(base, dev)[kDeviceMode] & 0x80) {
    ++s.n.recording;  // into another command buffer: never seen (docs, "M4, second step")
    return;
  }
  // Its first segment, and the number of segments.
  const uint32_t chain = Load32(base, cb + kCbChain);
  if (!Plausible(chain) || !Load32(base, chain + 4)) return;
  uint32_t segments = 0;
  for (uint32_t link = chain, i = 0; Plausible(link) && i < 64; link = Load32(base, link), ++i) {
    segments += Load32(base, link + 4);
  }
  const uint32_t words = Load32(base, chain + 8) & 0xFFFFFF;
  const uint32_t phys = Load32(base, chain + 12) & 0x1FFFFFFFu;
  nl::Bound old;
  const nl::Lists::Look look = s.lists.Find(cb, phys, words, patch_words, &old);
  if (look == nl::Lists::Look::kCurrent && s.drops.empty()) {
    ++s.n.current;
    return;
  }
  if (!AllocateMemory(s)) return;
  if (!SendDrops(ctx, base, dev, s)) {
    ++s.n.no_room;
    return;
  }
  if (look == nl::Lists::Look::kCurrent) {
    ++s.n.current;
    return;
  }
  if (look == nl::Lists::Look::kReused) {
    // The object holds another list now: the old one goes.
    const uint32_t drop[nl::kListDropWords] = {nl::kMagicHost, nl::kOpListDrop, old.phys,
                                               old.words};
    if (!Emit(ctx, base, dev, drop, nl::kListDropWords)) {
      ++s.n.no_room;
      return;
    }
    ++s.n.dropped;
    ++s.n.reused;
    s.lists.Erase(cb, &old);
    if (old.slot != nl::kNone) s.slots.Give(old.slot);
  } else {
    ++s.n.fresh;
  }
  Register(ctx, base, dev, cb, phys, words, segments, s);
}

}  // namespace forza::native_lists

namespace nls = forza::native_lists;

// The fixup dispatcher (r3: a pointer to the command buffer, r4: the record; docs "M4"): a bound
// list's generation is odd while the fixup writes it, even again after.
extern "C" REX_FUNC(__imp__sub_82D19210);
extern "C" REX_FUNC(sub_82D19210) {
  FORZA_D3D_TRACE_SCOPE(sub_82D19210);
  FORZA_LIST_TRACE_FIXUP();
  if (!nls::Active()) {
    __imp__sub_82D19210(ctx, base);
    return;
  }
  const uint32_t cb = nls::Plausible(ctx.r3.u32) ? nls::Load32(base, ctx.r3.u32) : 0;
  uint32_t word = 0;
  {
    nls::State& s = nls::S();
    std::lock_guard<std::mutex> lock(s.mutex);
    const forza::native_list::Bound* b = s.lists.Get(cb);
    if (b && b->slot != forza::native_list::kNone && s.states) {
      word = s.states + b->slot * 4;
      nls::Store32(base, word, nls::Load32(base, word) + 1);
      ++s.n.fixups;
    }
  }
  // A seqlock's writer: the odd generation stored before the fixup's writes, the even one after
  // them (the plugin reads the generation, the slots, then the generation again). On x86 the
  // fences only hold the compiler back; a weakly ordered CPU needs them as barriers.
  if (word) std::atomic_thread_fence(std::memory_order_release);
  __imp__sub_82D19210(ctx, base);
  if (word) {
    std::atomic_thread_fence(std::memory_order_release);
    nls::State& s = nls::S();
    std::lock_guard<std::mutex> lock(s.mutex);
    nls::Store32(base, word, nls::Load32(base, word) + 1);
  }
}

// A model's memory freed (r3: its block: +0 the model it shares its data with, or 0; +4 the
// headers, +8 the PM4, +16 / +20 their bytes): its lists are dropped at the next call.
extern "C" REX_FUNC(__imp__sub_82D19668);
extern "C" REX_FUNC(sub_82D19668) {
  if (nls::Active() && nls::Plausible(ctx.r3.u32) && !nls::Load32(base, ctx.r3.u32)) {
    const uint32_t block = ctx.r3.u32;
    const uint32_t headers = nls::Load32(base, block + 4), header_bytes = nls::Load32(base, block + 16);
    const uint32_t pm4 = nls::Load32(base, block + 8), pm4_bytes = nls::Load32(base, block + 20);
    nls::State& s = nls::S();
    std::lock_guard<std::mutex> lock(s.mutex);
    uint32_t by_header = 0, by_address = 0;
    const uint32_t phys = nls::PhysicalOf(pm4);
    std::vector<forza::native_list::Bound> gone =
        s.lists.Unload(headers, headers ? header_bytes : 0, phys,
                       phys != 0xFFFFFFFFu ? pm4_bytes : 0, &by_header, &by_address);
    if (!gone.empty()) ++s.n.unloads;
    s.n.unload_by_header += by_header;
    s.n.unload_by_address += by_address;
    for (const auto& b : gone) {
      if (b.slot != forza::native_list::kNone) s.slots.Give(b.slot);
      s.drops.push_back(b);
    }
  }
  __imp__sub_82D19668(ctx, base);
}

#endif  // FORZA_TU4
