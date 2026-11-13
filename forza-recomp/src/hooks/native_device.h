// forza - native renderer M2: the host device layer at the Xbox D3D library's flush points, in
// shadow mode (src/hooks/native_device.cpp, docs/native-renderer.md "M2: shadow mode").
//
// The flush points that other hooks already wrap (RunCommandBuffer in cockpit_gauges.cpp) open a
// FlushScope around their original call; the shadow mask hook (scene_resolution.cpp) starts the
// group of passes the layer records. With native_render=off (the default) a scope is one load
// and one branch.
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

// Around a flush point's original call.
class FlushScope {
 public:
  FlushScope(Point point, PPCContext& ctx, uint8_t* base);
  ~FlushScope();
  FlushScope(const FlushScope&) = delete;
  FlushScope& operator=(const FlushScope&) = delete;

 private:
  PPCContext& ctx_;
  uint8_t* base_;
  bool active_;
  bool record_ = false;
  Point point_;
  uint32_t args_[8] = {};
  uint64_t vertex_dirty_ = 0, pixel_dirty_ = 0;
  uint64_t entry_ns_ = 0;
};

// The shadow mask function starts (TU4 sub_824829D8): the shadow mask group of passes begins.
void ShadowMaskBegin(PPCContext& ctx, uint8_t* base);

}  // namespace forza::native_device
