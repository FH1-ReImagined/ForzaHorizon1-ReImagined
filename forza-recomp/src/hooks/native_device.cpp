// forza - native renderer M2: the host device layer behind the Xbox D3D library's flush points,
// in shadow mode (docs/native-renderer.md, "M2: shadow mode"). Nothing it does changes what is
// drawn.
//
// The D3D setters write no command-buffer words; they fill the device's shadow state, which the
// draws, RunCommandBuffer, the resolve and the clear flush. Those flush points, with predication,
// tiling, the segment kick, the fence, the waits and the swap, are hooked here (15 TU4 functions,
// and RunCommandBuffer in cockpit_gauges.cpp). With native_render=shadow, for one group of
// passes (the sun's shadow mask: from the shadow mask function, sub_824829D8, to the world
// pass's resolve of the mask) each flush call makes a draw record from the device's shadow state,
// as a native renderer would draw from it (src/platform/native_shadow_record.h). A record and its
// constants go into a ring in guest memory, and one host packet (ReIGlue 0610) behind the call's
// own words in the device's command buffer points at them: D3D guarantees only a few dozen words
// of room there. The GPU plugin checks each record against its register file at that point of
// the stream, counts the group's draws and resolves against the calls made, and every
// native_shadow_compare_every groups compares the mask the emulated passes produced with the one
// a native blur computes from the records (ReIGlue 0620). In shadow mode every flush point is
// also counted, a frame; off, a hook is one load and one branch before the original call.
//
// M3, native_render=hybrid: the same records, and the shadow mask's blurs and the mask's resolves
// after them are not made as D3D calls: their records (kRecordNative) ask the plugin to draw them
// (ReIGlue 0630). The blur hook (shadow_filter.cpp) decides it at the group's first blur, before
// any call is skipped: only while the plugin's status word (written at each group's begin) says
// it can draw them with the current settings, with world AO off (its apply draw lives in the
// blur's render pass) and for a blur quad that covers the screen (the no-sun path blurs a zeroed
// one after a clear, and the world pass's resolve must copy that clear).
//
// tu_address_map: TU4 addresses

#include "native_device.h"

#include "../debug/d3d_trace.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/native_shadow_record.h"

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

REXCVAR_DEFINE_STRING(native_render, "off", "Display",
                      "Testing (native renderer): off; shadow (the host device layer records "
                      "the shadow mask's passes and the GPU plugin compares them with the "
                      "emulated ones; nothing drawn changes); or hybrid (also draws the groups "
                      "of passes switched on below natively)");
REXCVAR_DEFINE_BOOL(native_shadow_mask, true, "Display",
                    "Testing (native_render=hybrid): the shadow mask's blurs and their resolves "
                    "drawn natively by the GPU plugin");
REXCVAR_DEFINE_INT32(native_shadow_compare_every, 60, "Display",
                     "Testing (native_render=shadow): compare the shadow mask's contents with a "
                     "native result every N groups of passes (0: never)");
REXCVAR_DEFINE_BOOL(native_shadow_seed_every_record, false, "Display",
                    "Testing (native_render=shadow): every record carries all ALU constants, "
                    "not only the ones the device marked dirty");

namespace forza::native_device {

std::atomic<bool> g_shadow_mode{false};
std::atomic<bool> g_hybrid{false};

}  // namespace forza::native_device

#if FORZA_TU4

namespace ns = forza::native_shadow;

// D3D's make-space call: ends the command buffer's segment and returns the new position (r3).
extern "C" REX_FUNC(sub_8243CF18);

namespace forza::native_device {
namespace {

constexpr uint32_t kIndexBuffer = 12812;  // the device's index buffer object (SetIndices)
// The world pass (sub_8242A160) resolves the mask after the shadow mask function returns.
constexpr uint32_t kWorldPassMaskResolve = 0x8242A538;

constexpr const char* kPointNames[kPointCount] = {
    "DrawVertices", "DrawIndexed", "BeginVertices", "DrawVerticesUP", "Resolve",
    "Clear",        "DrawIndexedUP", "RunCommandBuffer", "Predication", "Tiling",
    "SegmentKick",  "Fence",        "WaitA",          "WaitB",       "WaitC", "Swap"};
constexpr uint32_t kPointKind[kPointCount] = {
    ns::kKindDrawVertices,   ns::kKindDrawIndexed, ns::kKindBeginVertices,
    ns::kKindDrawVerticesUp, ns::kKindResolve,     ns::kKindClear,
    ns::kKindDrawIndexedUp,  ns::kKindRunCommandBuffer};

uint32_t Load32(uint8_t* base, uint32_t address) {
  uint32_t v;
  std::memcpy(&v, forza::GuestPtr(base, address), 4);
  return __builtin_bswap32(v);
}
void Store32(uint8_t* base, uint32_t address, uint32_t value) {
  value = __builtin_bswap32(value);
  std::memcpy(forza::GuestPtr(base, address), &value, 4);
}
uint64_t NowNs() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}

// A D3D device: its cursor in a physical view, below the hard limit, the soft limit close.
bool LooksLikeDevice(uint8_t* base, uint32_t r3) {
  if (r3 < 0x40000000u || r3 >= 0x80000000u || (r3 & 3)) return false;
  const uint32_t c = Load32(base, r3 + ns::kCursor), hard = Load32(base, r3 + ns::kHardLimit),
                 soft = Load32(base, r3 + ns::kSoftLimit);
  return c >= 0xA0000000u && !(c & 3) && hard >= c && hard - c <= (32u << 20) && soft <= hard &&
         hard - soft <= (32u << 20);
}

// Flush-point calls, counted per thread (no shared cache line between the threads that call
// D3D) and added up at the swap.
struct ThreadCalls {
  std::atomic<uint64_t> calls[kPointCount] = {};
  ThreadCalls* next = nullptr;
};
std::atomic<ThreadCalls*> thread_calls{nullptr};
void CountCall(Point point) {
  thread_local ThreadCalls* mine = [] {
    auto* t = new ThreadCalls;  // kept until exit: the swap reads it
    t->next = thread_calls.load(std::memory_order_relaxed);
    while (!thread_calls.compare_exchange_weak(t->next, t, std::memory_order_release,
                                               std::memory_order_relaxed)) {
    }
    return t;
  }();
  auto& c = mine->calls[point];
  c.store(c.load(std::memory_order_relaxed) + 1, std::memory_order_relaxed);
}
uint64_t PointCalls(uint32_t point) {
  uint64_t n = 0;
  for (ThreadCalls* t = thread_calls.load(std::memory_order_acquire); t; t = t->next)
    n += t->calls[point].load(std::memory_order_relaxed);
  return n;
}
struct Totals {
  std::atomic<uint64_t> islands{0}, records{0}, written{0}, unwritten{0}, seeds{0}, words{0},
      not_device{0}, make_space{0}, record_ns{0}, frames{0};
  // M3: groups drawn natively, calls skipped, groups the game drew itself and why.
  std::atomic<uint64_t> native_islands{0}, skipped{0}, unexpected{0}, by_status{0}, by_ao{0},
      by_quad{0}, by_switch{0};
};
Totals totals;

// A record and the constants packets before it, ready to go into the command buffer.
struct Packets {
  static constexpr uint32_t kMaxLiteralLoads = 16, kMaxLiteralWords = 1024;
  static constexpr uint32_t kMaxPackets =
      ns::kChunks / ns::kChunksPerPacket + kMaxLiteralLoads + 1;
  uint32_t words[(ns::kChunks / ns::kChunksPerPacket) * ns::kMaxConstantsWords +
                 kMaxLiteralLoads * ns::kLiteralsHeaderWords + kMaxLiteralWords +
                 ns::kMaxRecordWords];
  uint32_t size[kMaxPackets];
  uint32_t count = 0, used = 0;
  bool seed = false;
  uint32_t vertex = 0;  // BeginVertices: where its caller copies the vertices
};

// The group of passes being recorded, on the render thread.
struct Island {
  bool open = false, begun = false, need_seed = true, compare = false;
  // M3: the native decision was made (at the first blur), its result, a blur was made.
  bool decided = false, native = false, blurred = false;
  uint32_t id = 0, dev = 0, index = 0, written = 0, unwritten = 0;
  uint32_t calls[ns::kKindCount] = {};
  Packets now;
  // A BeginVertices record waits for its vertices: its caller copies them after the call, and
  // the cursor moves past them only then. It goes out at the next flush point (or the end).
  bool pending = false;
  Packets later;
};
// Per thread (only the render thread records), allocated when shadow mode first needs it.
Island& CurrentIsland() {
  thread_local std::unique_ptr<Island> island;
  if (!island) island = std::make_unique<Island>();
  return *island;
}
thread_local int t_depth = 0;
// M3: inside a blur call of the mask (its kind) or a resolve of the mask after the first blur.
thread_local int t_blur_kind = -1;
thread_local bool t_mask_resolve = false;
// The shader literal constants the flush being recorded loads (LOAD_ALU_CONSTANT, sub_82441B80):
// guest address, first word from register 0x4000, words.
struct LiteralLoads {
  bool on = false;
  uint32_t count = 0, words = 0;
  uint32_t address[Packets::kMaxLiteralLoads], first[Packets::kMaxLiteralLoads],
      size[Packets::kMaxLiteralLoads];
};
thread_local LiteralLoads t_literals;
std::atomic<uint32_t> island_ids{0};
std::atomic<int32_t> compare_every{60};
std::atomic<bool> seed_every{false};
std::atomic<bool> shadow_mask_switch{true};
// The plugin's status word (ReIGlue 0630, kStatus*), in guest memory.
uint32_t status_word = 0;

void MakeSpace(PPCContext& ctx, uint8_t* base, uint32_t dev) {
  // D3D's call may change any register the caller of the flush point does not expect it to
  // (link-time code generation): all of them are put back.
  const PPCContext saved = ctx;
  ctx.r3.u64 = dev;
  sub_8243CF18(ctx, base);
  ctx = saved;
  totals.make_space.fetch_add(1, std::memory_order_relaxed);
}

// One type-3 NOP with `count` payload words (guest order) at the device's cursor. As D3D's own
// writers: past the soft limit, D3D makes space first; the room it guarantees beyond it is a few
// dozen words (larger packets go through the message ring below).
bool Emit(PPCContext& ctx, uint8_t* base, uint32_t dev, const uint32_t* words, uint32_t count) {
  const uint32_t bytes = 4 * (count + 1);
  uint32_t cursor = Load32(base, dev + ns::kCursor);
  if (cursor > Load32(base, dev + ns::kSoftLimit)) {
    MakeSpace(ctx, base, dev);
    cursor = Load32(base, dev + ns::kCursor);
  }
  if (uint64_t(cursor) + bytes > Load32(base, dev + ns::kHardLimit)) return false;
  Store32(base, cursor + 4, 0xC0000000u | ((count - 1) << 16) | (0x10u << 8));
  std::memcpy(forza::GuestPtr(base, cursor + 8), words, size_t(count) * 4);
  Store32(base, dev + ns::kCursor, cursor + bytes);
  totals.words.fetch_add(count + 1, std::memory_order_relaxed);
  return true;
}

bool EmitHost(PPCContext& ctx, uint8_t* base, uint32_t dev, const uint32_t* host, uint32_t n) {
  uint32_t guest[16];
  for (uint32_t i = 0; i < n; ++i) guest[i] = ns::Guest(host[i]);
  return Emit(ctx, base, dev, guest, n);
}

// The message ring (guest memory): a record and its constants packets go there, and one small
// packet in the command buffer points the plugin at them (ReIGlue's kOpRef).
constexpr uint32_t kRingBytes = 2u << 20;
uint32_t ring = 0, ring_at = 0, ring_sequence = 0;
bool ring_failed = false;

// The constants packets, then the record, as one message. A lost one makes the next record a
// seed.
void EmitPackets(PPCContext& ctx, uint8_t* base, Packets& p) {
  Island& is = CurrentIsland();
  if (!ring && !ring_failed) {
    ring = REX_KERNEL_MEMORY()->SystemHeapAlloc(kRingBytes, 0x1000);
    ring_failed = !ring;
    if (ring_failed) REXLOG_WARN("native_render shadow: no guest memory for the record ring");
  }
  uint32_t total = 2 + p.count + p.used;
  bool ok = ring && total * 4 <= kRingBytes;
  if (ok) {
    if (ring_at + total * 4 > kRingBytes) ring_at = 0;
    const uint32_t at = ring + ring_at;
    const uint32_t sequence = ++ring_sequence;
    uint32_t* m = reinterpret_cast<uint32_t*>(forza::GuestPtr(base, at));
    m[0] = ns::Guest(sequence);
    m[1] = ns::Guest(total);
    uint32_t w = 2;
    const uint32_t* src = p.words;
    for (uint32_t i = 0; i < p.count; ++i) {
      m[w++] = ns::Guest(p.size[i]);
      std::memcpy(m + w, src, p.size[i] * 4);
      w += p.size[i];
      src += p.size[i];
    }
    const uint32_t ref[5] = {ns::kMagic, ns::kOpRef, at, total, sequence};
    ok = EmitHost(ctx, base, is.dev, ref, 5);
    if (ok) ring_at += (total * 4 + 63) & ~63u;
  }
  if (ok) {
    ++is.written;
    totals.written.fetch_add(1, std::memory_order_relaxed);
    if (p.seed) totals.seeds.fetch_add(1, std::memory_order_relaxed);
  } else {
    ++is.unwritten;
    is.need_seed = true;
    totals.unwritten.fetch_add(1, std::memory_order_relaxed);
  }
}

void EmitPending(PPCContext& ctx, uint8_t* base) {
  Island& is = CurrentIsland();
  if (!is.pending) return;
  is.pending = false;
  if (is.later.vertex)  // the vertices are in place now: the record's last words
    std::memcpy(is.later.words + is.later.used - ns::kVertexDataWords,
                forza::GuestPtr(base, is.later.vertex), ns::kVertexDataWords * 4);
  EmitPackets(ctx, base, is.later);
}

void EndIsland(PPCContext& ctx, uint8_t* base) {
  Island& is = CurrentIsland();
  if (!is.open) return;
  if (is.begun) {
    EmitPending(ctx, base);
    uint32_t end[5 + ns::kKindCount] = {ns::kMagic, ns::kOpIslandEnd, is.id, is.written,
                                        is.unwritten};
    for (uint32_t k = 0; k < ns::kKindCount; ++k) end[5 + k] = is.calls[k];
    EmitHost(ctx, base, is.dev, end, uint32_t(std::size(end)));
  }
  is.open = false;
}

void LogFrames() {
  const uint64_t frames = totals.frames.fetch_add(1, std::memory_order_relaxed) + 1;
  if (frames % 600) return;
  static uint64_t last_calls[kPointCount], last_records, last_ns, last_islands, last_words;
  std::string calls;
  for (uint32_t p = 0; p < kPointCount; ++p) {
    const uint64_t c = PointCalls(p);
    calls += fmt::format("{}{} {:.1f}", p ? ", " : "", kPointNames[p],
                         double(c - last_calls[p]) / 600.0);
    last_calls[p] = c;
  }
  const uint64_t records = totals.records.load(), ns_total = totals.record_ns.load(),
                 islands = totals.islands.load(), words = totals.words.load();
  REXLOG_INFO(
      "native_render shadow: {} frames; last 600: {} groups, {:.1f} records a frame, record "
      "building {:.2f} us a frame ({:.2f} us a record), {:.0f} words a frame; all: {} records, "
      "{} written ({} seeds), {} without room, {} calls on another device, {} make-space calls",
      frames, islands - last_islands, double(records - last_records) / 600.0,
      double(ns_total - last_ns) * 1e-3 / 600.0,
      records != last_records ? double(ns_total - last_ns) * 1e-3 / double(records - last_records)
                              : 0.0,
      double(words - last_words) / 600.0, records, totals.written.load(), totals.seeds.load(),
      totals.unwritten.load(), totals.not_device.load(), totals.make_space.load());
  REXLOG_INFO("native_render shadow: D3D flush points a frame: {}", calls);
  if (g_hybrid.load(std::memory_order_relaxed))
    REXLOG_INFO(
        "native_render hybrid: {} groups drawn natively, {} calls skipped ({} unexpected calls "
        "made in a native blur); the game drew the shadow mask itself: plugin status {}, world "
        "AO {}, quad {}, switch off {}",
        totals.native_islands.load(), totals.skipped.load(), totals.unexpected.load(),
        totals.by_status.load(), totals.by_ao.load(), totals.by_quad.load(),
        totals.by_switch.load());
  last_records = records;
  last_ns = ns_total;
  last_islands = islands;
  last_words = words;
}

}  // namespace

void ShadowMaskBegin(PPCContext& ctx, uint8_t* base) {
  static std::once_flag once;
  std::call_once(once, [] {
    const auto apply = [](std::string_view mode) {
      const bool hybrid = mode == "hybrid";
      const bool on = mode == "shadow" || hybrid;
      const bool was_hybrid = g_hybrid.exchange(hybrid);
      if (on != g_shadow_mode.exchange(on) || hybrid != was_hybrid)
        REXLOG_INFO("native_render: {}",
                    hybrid ? "hybrid (the shadow mask's passes are recorded; its blurs are drawn "
                             "natively by the GPU plugin when it can)"
                    : on   ? "shadow (the shadow mask's passes are recorded and compared; "
                             "nothing drawn changes)"
                           : "off");
    };
    apply(REXCVAR_GET(native_render));
    compare_every = REXCVAR_GET(native_shadow_compare_every);
    seed_every = REXCVAR_GET(native_shadow_seed_every_record);
    shadow_mask_switch = REXCVAR_GET(native_shadow_mask);
    rex::cvar::RegisterChangeCallback("native_shadow_mask",
                                      [](std::string_view, std::string_view v) {
                                        shadow_mask_switch = v == "true";
                                      });
    rex::cvar::RegisterChangeCallback("native_render",
                                      [apply](std::string_view, std::string_view v) { apply(v); });
  });
  if (!Enabled()) return;
  Island& is = CurrentIsland();
  EndIsland(ctx, base);  // one the world pass did not close
  is.open = true;
  is.begun = false;
  is.need_seed = true;
  is.pending = false;
  is.id = island_ids.fetch_add(1, std::memory_order_relaxed) + 1;
  const int32_t every = compare_every.load(std::memory_order_relaxed);
  is.compare = every > 0 && is.id % uint32_t(every) == 0;
  is.dev = 0;
  is.decided = is.native = is.blurred = false;
  is.index = is.written = is.unwritten = 0;
  std::memset(is.calls, 0, sizeof(is.calls));
  totals.islands.fetch_add(1, std::memory_order_relaxed);
}

FlushScope::FlushScope(Point point, PPCContext& ctx, uint8_t* base)
    : ctx_(ctx), base_(base), active_(Enabled()), point_(point) {
  if (!active_) return;
  CountCall(point);
  if (t_depth++ != 0) return;  // only the call the game made counts as the flush point
  Island& is = CurrentIsland();
  if (!is.open) return;
  const uint64_t t0 = NowNs();
  if (point == kPointSwap) {  // a group the world pass did not close
    EndIsland(ctx, base);
    totals.record_ns.fetch_add(NowNs() - t0, std::memory_order_relaxed);
    return;
  }
  // Whatever a call writes goes after the BeginVertices record still waiting.
  if (point > kPointRunCommandBuffer) {
    EmitPending(ctx, base);
    return;
  }
  const uint32_t dev = ctx.r3.u32;
  if (!is.dev && LooksLikeDevice(base, dev)) is.dev = dev;
  if (!is.dev || dev != is.dev) {
    totals.not_device.fetch_add(1, std::memory_order_relaxed);
    return;
  }
  EmitPending(ctx, base);
  if (!is.begun) {
    if (!status_word) {
      // The plugin writes it at each begin; zero until it does (a plugin without 0630 never).
      status_word = REX_KERNEL_MEMORY()->SystemHeapAlloc(64, 64);
      if (status_word) Store32(base, status_word, 0);
    }
    const bool hybrid = g_hybrid.load(std::memory_order_relaxed);
    const uint32_t begin[6] = {
        ns::kMagic, ns::kOpIslandBegin, is.id, ns::kIslandShadowMask,
        (is.compare && !hybrid ? ns::kIslandCompareContents : 0u) | (hybrid ? ns::kIslandNative : 0u),
        status_word};
    EmitHost(ctx, base, dev, begin, 6);
    is.begun = true;
  }
  record_ = true;
  // M3: the blur's draw and the mask's resolves; skipped in a native group.
  if (t_blur_kind >= 0) {
    if (point == kPointDrawVerticesUp) {
      flags_ = ns::kRecordBlur | (uint32_t(t_blur_kind) << ns::kRecordBlurKindShift);
    } else if (is.native) {
      totals.unexpected.fetch_add(1, std::memory_order_relaxed);
    }
  } else if (t_mask_resolve && point == kPointResolve) {
    flags_ = ns::kRecordMaskResolve;
  }
  if (flags_ && is.native) {
    flags_ |= ns::kRecordNative;
    skip_ = true;
    totals.skipped.fetch_add(1, std::memory_order_relaxed);
  }
  t_literals.on = true;
  t_literals.count = t_literals.words = 0;
  ++is.calls[kPointKind[point]];
  const uint8_t* d = forza::GuestPtr(base, dev);
  vertex_dirty_ = ns::LoadGuest64(d + ns::kVertexDirty);
  pixel_dirty_ = ns::LoadGuest64(d + ns::kPixelDirty);
  args_[0] = ctx.r4.u32;
  args_[1] = ctx.r5.u32;
  args_[2] = ctx.r6.u32;
  args_[3] = ctx.r7.u32;
  args_[4] = ctx.r8.u32;
  args_[5] = ctx.r9.u32;
  args_[6] = ctx.r10.u32;
  if (point == kPointDrawIndexedUp) args_[7] = Load32(base, ctx.r1.u32 + 84);  // the stride
  entry_ns_ = NowNs() - t0;
}

FlushScope::~FlushScope() {
  if (!active_) return;
  --t_depth;
  if (point_ == kPointSwap) LogFrames();
  if (!record_) return;
  const uint64_t t0 = NowNs();
  t_literals.on = false;
  Island& is = CurrentIsland();
  uint8_t* base = base_;
  const uint32_t dev = is.dev;
  ns::Call call;
  call.island = is.id;
  call.index = is.index++;
  call.kind = kPointKind[point_];
  call.vertex_dirty = vertex_dirty_;
  call.pixel_dirty = pixel_dirty_;
  call.flags = flags_;
  call.vertex_shader = Load32(base, dev + ns::kVertexShader);
  call.pixel_shader = Load32(base, dev + ns::kPixelShader);
  switch (point_) {
    case kPointDrawVertices:  // (device, primitive, first vertex, count)
      call.primitive = args_[0];
      call.count = args_[2];
      break;
    case kPointDrawIndexed: {  // (device, primitive, base vertex, first index, count)
      call.primitive = args_[0];
      call.count = args_[3];
      const uint32_t ib = Load32(base, dev + kIndexBuffer);
      if (ib) {
        const uint32_t size = (Load32(base, ib) & 0x80000000u) ? 4 : 2;
        call.index_address = (Load32(base, ib + 24) + args_[2] * size) & 0x1FFFFFFFu;
      }
      break;
    }
    case kPointBeginVertices:  // (device, primitive, count, stride) -> where the vertices go
      call.primitive = args_[0];
      call.count = args_[1];
      call.vertex_data = forza::GuestPtr(base, ctx_.r3.u32);  // filled in when it goes out
      call.vertex_bytes = ctx_.r3.u32 ? ns::kVertexDataWords * 4 : 0;
      break;
    case kPointDrawVerticesUp:  // (device, primitive, count, data, stride)
      call.primitive = args_[0];
      call.count = args_[1];
      if (args_[2]) {
        call.vertex_data = forza::GuestPtr(base, args_[2]);
        call.vertex_bytes = args_[1] * args_[3];
      }
      break;
    // (device, primitive, first vertex, vertices, indices, index data, index format, vertex
    // data; the stride on the stack)
    case kPointDrawIndexedUp:
      call.primitive = args_[0];
      call.count = args_[3];
      if (args_[6]) {
        call.vertex_data = forza::GuestPtr(base, args_[6] + args_[1] * args_[7]);
        call.vertex_bytes = args_[2] * args_[7];
      }
      break;
    default:
      break;
  }
  // The constants a native renderer would upload for this draw: all of them in the group's
  // first record (the seed), then the chunks the device marked dirty.
  const bool seed = is.need_seed || seed_every.load(std::memory_order_relaxed);
  is.need_seed = false;
  const uint8_t* d = forza::GuestPtr(base, dev);
  totals.records.fetch_add(1, std::memory_order_relaxed);
  Packets& p = point_ == kPointBeginVertices ? is.later : is.now;
  uint8_t chunks[ns::kChunks];
  const uint32_t nchunks = ns::DirtyChunks(vertex_dirty_, pixel_dirty_, seed, chunks);
  p.count = p.used = 0;
  p.seed = seed;
  for (uint32_t c = 0; c < nchunks; c += ns::kChunksPerPacket) {
    const uint32_t k = nchunks - c < ns::kChunksPerPacket ? nchunks - c : ns::kChunksPerPacket;
    p.size[p.count++] = ns::BuildConstants(d, is.id, seed && !c ? ns::kConstantsSeed : 0,
                                           chunks + c, k, p.words + p.used);
    p.used += p.size[p.count - 1];
  }
  t_literals.on = false;
  for (uint32_t i = 0; i < t_literals.count; ++i) {
    uint32_t* w = p.words + p.used;
    const uint32_t head[5] = {ns::kMagic, ns::kOpLiterals, is.id, t_literals.first[i],
                              t_literals.size[i]};
    for (uint32_t k = 0; k < 5; ++k) w[k] = ns::Guest(head[k]);
    std::memcpy(w + 5, forza::GuestPtr(base, t_literals.address[i]), t_literals.size[i] * 4);
    p.size[p.count++] = 5 + t_literals.size[i];
    p.used += 5 + t_literals.size[i];
  }
  const uint32_t constant_packets = p.count;
  p.size[p.count++] = ns::BuildRecord(d, call, seed, constant_packets, p.words + p.used);
  p.used += p.size[p.count - 1];
  if (point_ == kPointBeginVertices) {
    p.vertex = call.vertex_bytes ? ctx_.r3.u32 : 0;
    is.pending = true;
  } else {
    EmitPackets(ctx_, base, p);
  }
  totals.record_ns.fetch_add(entry_ns_ + NowNs() - t0, std::memory_order_relaxed);
  // The world pass's resolve of the mask is the group's last call (ResolveShadowMask below).
}

bool ShadowMaskBlurNative(uint8_t* base, bool quad_ok) {
  if (!g_hybrid.load(std::memory_order_relaxed)) return false;
  Island& is = CurrentIsland();
  if (!is.open) return false;
  if (is.decided) return is.native;
  is.decided = true;
  if (!shadow_mask_switch.load(std::memory_order_relaxed)) {
    totals.by_switch.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  if (!status_word || !is.dev || !(Load32(base, status_word) & ns::kStatusShadowMaskBlur)) {
    totals.by_status.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  // World AO multiplies its factor into the mask in the blur's render pass (ReIGlue
  // fh1_world_ao.cpp); natively there is none yet.
  const std::string ao = rex::cvar::GetFlagByName("world_ambient_occlusion");
  if (ao == "low" || ao == "high") {
    totals.by_ao.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  if (!quad_ok) {
    totals.by_quad.fetch_add(1, std::memory_order_relaxed);
    return false;
  }
  is.native = true;
  totals.native_islands.fetch_add(1, std::memory_order_relaxed);
  return true;
}

BlurScope::BlurScope(uint32_t kind) : active_(Enabled()) {
  if (!active_) return;
  t_blur_kind = int(kind);
  CurrentIsland().blurred = true;
}
BlurScope::~BlurScope() {
  if (active_) t_blur_kind = -1;
}
MaskResolveScope::MaskResolveScope() : active_(Enabled()) {
  if (!active_) return;
  const Island& is = CurrentIsland();
  t_mask_resolve = is.open && is.blurred;
}
MaskResolveScope::~MaskResolveScope() {
  if (active_) t_mask_resolve = false;
}

}  // namespace forza::native_device

namespace nd = forza::native_device;

#define NATIVE_FLUSH_POINT(point, function)                \
  extern "C" REX_FUNC(__imp__##function);                  \
  extern "C" REX_FUNC(function) {                          \
    FORZA_D3D_TRACE_SCOPE(function);                       \
    if (!nd::Enabled()) {                                  \
      __imp__##function(ctx, base);                        \
      return;                                              \
    }                                                      \
    nd::FlushScope scope(point, ctx, base);                \
    if (!scope.Skip()) __imp__##function(ctx, base);       \
  }
// The same, for functions the D3D trace does not wrap.
#define NATIVE_FLUSH_POINT_UNTRACED(point, function)       \
  extern "C" REX_FUNC(__imp__##function);                  \
  extern "C" REX_FUNC(function) {                          \
    if (!nd::Enabled()) {                                  \
      __imp__##function(ctx, base);                        \
      return;                                              \
    }                                                      \
    nd::FlushScope scope(point, ctx, base);                \
    if (!scope.Skip()) __imp__##function(ctx, base);       \
  }

NATIVE_FLUSH_POINT(nd::kPointDrawVertices, sub_82445F18)
NATIVE_FLUSH_POINT(nd::kPointDrawIndexed, sub_82446330)
NATIVE_FLUSH_POINT(nd::kPointBeginVertices, sub_8243DC20)
NATIVE_FLUSH_POINT_UNTRACED(nd::kPointDrawVerticesUp, sub_82490958)
NATIVE_FLUSH_POINT(nd::kPointResolve, sub_8243F488)
NATIVE_FLUSH_POINT(nd::kPointClear, sub_8243E0E0)
NATIVE_FLUSH_POINT_UNTRACED(nd::kPointDrawIndexedUp, sub_8242A040)
NATIVE_FLUSH_POINT(nd::kPointPredication, sub_8246BFE8)
NATIVE_FLUSH_POINT_UNTRACED(nd::kPointTiling, sub_82AB2F60)
NATIVE_FLUSH_POINT(nd::kPointSegmentKick, sub_82439348)
NATIVE_FLUSH_POINT(nd::kPointFence, sub_82496CB0)
NATIVE_FLUSH_POINT(nd::kPointWaitA, sub_82416250)
NATIVE_FLUSH_POINT(nd::kPointWaitB, sub_82416720)
NATIVE_FLUSH_POINT(nd::kPointWaitC, sub_82416A90)
NATIVE_FLUSH_POINT(nd::kPointSwap, sub_82AACAA0)

// D3D's load of a shader's literal constants from the shader object (sub_82441B80: device, the
// shader's data, its base address), inside a draw's flush: the device shadow does not hold them,
// so the record being made sends them too. The table: at data + *(data + 20) + 20, entries of a
// 16-bit first constant, a 16-bit word count and a 32-bit offset from the base, up to
// *(table - 4) bytes.
extern "C" REX_FUNC(__imp__sub_82441B80);
extern "C" REX_FUNC(sub_82441B80) {
  FORZA_D3D_TRACE_SCOPE(sub_82441B80);
  if (nd::Enabled() && nd::t_literals.on && ctx.r4.u32) {
    auto& lit = nd::t_literals;
    const uint32_t data = ctx.r4.u32, offset = nd::Load32(base, data + 20);
    if (offset) {
      uint32_t at = data + offset + 20;
      const uint32_t end = at + nd::Load32(base, data + offset + 16);
      for (; at < end; at += 8) {
        const uint32_t head = nd::Load32(base, at);
        const uint32_t first = (head >> 16) * 4, words = head & 0xFFFF;
        if (!words) break;
        if (lit.count < nd::Packets::kMaxLiteralLoads &&
            lit.words + words <= nd::Packets::kMaxLiteralWords && first + words <= ns::kAluWords) {
          lit.address[lit.count] = nd::Load32(base, at + 4) + ctx.r5.u32;
          lit.first[lit.count] = first;
          lit.size[lit.count] = words;
          ++lit.count;
          lit.words += words;
        }
      }
    }
  }
  __imp__sub_82441B80(ctx, base);
}

// ResolveShadowMask (sub_824933E0): called by the world pass (return address 0x8242A538) it is
// the shadow mask group's last call; the blur's own calls (src/hooks/shadow_filter.cpp) go to
// the original directly.
extern "C" REX_FUNC(__imp__sub_824933E0);
extern "C" REX_FUNC(sub_824933E0) {
  if (!nd::Enabled()) {
    __imp__sub_824933E0(ctx, base);
    return;
  }
  const uint32_t lr = uint32_t(ctx.lr);
  {
    nd::MaskResolveScope mask_resolve;
    __imp__sub_824933E0(ctx, base);
  }
  if (lr == nd::kWorldPassMaskResolve && nd::CurrentIsland().open && !nd::t_depth) {
    const uint64_t t0 = nd::NowNs();
    nd::EndIsland(ctx, base);
    nd::totals.record_ns.fetch_add(nd::NowNs() - t0, std::memory_order_relaxed);
  }
}

#else  // the disc build

// The native renderer's hooks and records are Title Update 4's (docs/native-renderer.md). The
// shadow filter's hooks (shadow_filter.cpp) are in both builds and open these scopes around the
// mask's blurs and resolves: without a native device they do nothing, and no blur is native.
namespace forza::native_device {

bool ShadowMaskBlurNative(uint8_t*, bool) { return false; }
BlurScope::BlurScope(uint32_t) : active_(false) {}
BlurScope::~BlurScope() { (void)active_; }
MaskResolveScope::MaskResolveScope() : active_(false) {}
MaskResolveScope::~MaskResolveScope() { (void)active_; }

}  // namespace forza::native_device

#endif  // FORZA_TU4
