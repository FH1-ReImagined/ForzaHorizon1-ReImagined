// forza - native renderer M2: the host device layer at the Xbox D3D library's flush points, in
// shadow mode (src/hooks/native_device.cpp, docs/native-renderer.md "M2: shadow mode"). The
// pass ledger (pass_ledger.h) marks its passes at the same flush points.
//
// The flush points that other hooks already wrap (RunCommandBuffer in cockpit_gauges.cpp) open a
// FlushScope around their original call; the shadow mask hook (scene_resolution.cpp) starts the
// group of passes the layer records. With native_render=off (the default) a scope is one load
// and one branch.
//
// M3 (native_render=hybrid, docs/native-renderer.md "M3"): the shadow mask's blurs and their
// resolves are not made as D3D calls; their records ask the GPU plugin to draw them (ReIGlue
// 0630). The blur hook (shadow_filter.cpp) decides that once per group, before any call is skipped,
// and only while the plugin's status word says it can.
#pragma once

#include <rex/ppc/context.h>

#include <atomic>
#include <cstdint>

namespace forza::native_device {

// The flush points, for the per-frame counts.
enum Point : uint32_t {
  kPointDrawVertices,
  kPointDrawIndexed,
  kPointBeginVertices,
  kPointDrawVerticesUp,
  kPointResolve,
  kPointClear,
  kPointDrawIndexedUp,
  kPointRunCommandBuffer,
  kPointPredication,
  kPointTiling,
  kPointSegmentKick,
  kPointFence,
  kPointWaitA,
  kPointWaitB,
  kPointWaitC,
  kPointSwap,
  kPointCount,
};

extern std::atomic<bool> g_shadow_mode;
inline bool Enabled() { return g_shadow_mode.load(std::memory_order_relaxed); }
// What the flush points do besides their call: the shadow mode's records, the pass ledger's
// marks (pass_ledger.h). Zero: each flush point is one load and one branch.
constexpr uint8_t kHookShadow = 1, kHookLedger = 2;
extern std::atomic<uint8_t> g_hooks;
inline bool Hooked() { return g_hooks.load(std::memory_order_relaxed) != 0; }

// The pass ledger's state of one flush call (pass_ledger.cpp).
// Set by pass_ledger::FlushBegin before anything reads it (no initializers: the flush points'
// FlushScope stays as cheap to make while the ledger is off).
struct LedgerFlush {
  uint64_t start_ns;
  uint32_t kind;
  bool counted;
};

// Around a flush point's original call.
class FlushScope {
 public:
  FlushScope(Point point, PPCContext& ctx, uint8_t* base);
  ~FlushScope();
  FlushScope(const FlushScope&) = delete;
  FlushScope& operator=(const FlushScope&) = delete;
  // M3: the call is drawn by the plugin from its record; the original is not called.
  bool Skip() const { return skip_; }

 private:
  void ShadowEnd();
  PPCContext& ctx_;
  uint8_t* base_;
  bool active_;
  bool record_ = false, skip_ = false;
  uint32_t flags_ = 0;
  Point point_;
  uint32_t args_[8] = {};
  uint64_t vertex_dirty_ = 0, pixel_dirty_ = 0;
  uint64_t entry_ns_ = 0;
  bool ledger_ = false;
  LedgerFlush ledger_flush_;
};

// A host packet (ReIGlue 0610: a type-3 NOP whose first word is "FH1N"; `n` words in host order,
// at most 16) at the device's cursor, making space first as D3D's own writers do. False if there
// is no room. M4's model list registry (native_lists.cpp) sends its packets with it.
bool EmitHostPacket(PPCContext& ctx, uint8_t* base, uint32_t dev, const uint32_t* host, uint32_t n);

// The shadow mask function starts (TU4 sub_824829D8): the shadow mask group of passes begins.
void ShadowMaskBegin(PPCContext& ctx, uint8_t* base);

// M3: whether the current group's blurs and the mask's resolves after them go native. Decided at
// the group's first blur (hybrid mode, native_shadow_mask on, the plugin's status bit set, world
// AO off, `quad_ok`: the blur's quad covers the screen) and kept for the group.
bool ShadowMaskBlurNative(uint8_t* base, bool quad_ok);
// Around one call of the blur (sub_8241FD00) of the shadow renderer's own mask: the records made
// in it are the blur's, of `kind` (src/platform/shadow_filter.h), and native ones skip the call.
class BlurScope {
 public:
  explicit BlurScope(uint32_t kind);
  ~BlurScope();
  BlurScope(const BlurScope&) = delete;
  BlurScope& operator=(const BlurScope&) = delete;

 private:
  bool active_;
};
// Around a resolve of the mask (sub_824933E0): after the group's first blur it is the mask's.
class MaskResolveScope {
 public:
  MaskResolveScope();
  ~MaskResolveScope();
  MaskResolveScope(const MaskResolveScope&) = delete;
  MaskResolveScope& operator=(const MaskResolveScope&) = delete;

 private:
  bool active_;
};

}  // namespace forza::native_device
