// forza - native renderer M4 (docs/native-renderer.md, "M4, third step"): the model list
// registry, behind native_lists (off by default; "shadow": every run of a registered list is
// compared by the GPU plugin with its template, and nothing drawn changes).
//
// The RunCommandBuffer hook (cockpit_gauges.cpp) calls OnRun before the original; the track and
// procedural models' pass hooks (scene_tiling.cpp) open a GroupScope, so a list is registered
// with its pass group. Off, each is one load and one branch.
#pragma once

#include <rex/ppc/context.h>

#include <atomic>
#include <cstdint>

namespace forza::native_lists {

// 0 the switch not read yet, 1 shadow, 2 off.
extern std::atomic<int> g_state;
bool ReadSwitch();
inline bool Active() {
  const int s = g_state.load(std::memory_order_relaxed);
  return s == 1 || (s == 0 && ReadSwitch());
}

// RunCommandBuffer (TU4 sub_824469B0: r3 the device, r4 the command buffer), before the
// original: registers the list with the plugin if it is a model list not registered yet.
void OnRun(PPCContext& ctx, uint8_t* base);

// The pass group being drawn on this thread (pass * 3 + layer; passes 3 and 5 draw pass 4's).
void SetGroup(uint32_t group);
uint32_t Group();
class GroupScope {
 public:
  GroupScope(uint32_t pass, uint32_t layer) : active_(Active()) {
    if (!active_) return;
    saved_ = Group();
    SetGroup(GroupOf(pass, layer));
  }
  ~GroupScope() {
    if (active_) SetGroup(saved_);
  }
  GroupScope(const GroupScope&) = delete;
  GroupScope& operator=(const GroupScope&) = delete;
  static uint32_t GroupOf(uint32_t pass, uint32_t layer);

 private:
  bool active_;
  uint32_t saved_ = 0xFF;
};

}  // namespace forza::native_lists
