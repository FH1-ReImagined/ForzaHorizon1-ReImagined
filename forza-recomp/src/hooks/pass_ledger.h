// forza - the pass ledger (docs/rendering.md "Pass ledger"; src/platform/pass_ledger.h): which
// pass and draw class the render thread's commands belong to, marked in its command stream for
// the GPU plugin (ReIGlue 0670), which times them on its threads. A measurement tool: on only
// while the plugin's fh1_pass_cost_csv names a file (read once, at the first shadow mask).
//
// The D3D flush points (native_device.cpp FlushScope) write the marks; the track's, the
// procedural models' and the cars' draw hooks open a ClassScope (scene_tiling.cpp,
// cockpit_gauges.cpp), and the shadow mask function a mask scope (native_device.cpp). Off, a
// scope is one load and one branch, and the flush points test the same word as before
// (native_device::Hooked).
#pragma once

#include "native_device.h"

#include "../platform/pass_ledger.h"

#include <rex/ppc/context.h>

#include <cstdint>

namespace forza::pass_ledger {

inline bool On() {
  return native_device::g_hooks.load(std::memory_order_relaxed) & native_device::kHookLedger;
}
// The track's, the procedural models' and the cars' draw hooks open a ClassScope: with the
// ledger on, or with the test switch FORZA_TEST_SKIP_LIST_GROUPS (one test of the same word).
inline bool Scoped() {
  return native_device::g_hooks.load(std::memory_order_relaxed) &
         (native_device::kHookLedger | native_device::kHookSkipLists);
}
// Reads the switch (once; the shadow mask function's first call, on the render thread), and the
// test switches FORZA_TEST_SKIP_LIST_GROUPS and FORZA_TEST_SKIP_PASS (docs/rendering.md "Pass
// deletion").
void ReadSwitch();

// The track's, the procedural models' and the cars' draws on this thread: their class, pass and
// layer (platform/pass_ledger.h Class), and whether FORZA_TEST_SKIP_LIST_GROUPS skips their
// lists. The hooks open one only while Scoped(), in a branch of their own, so that off they keep
// their tail calls.
struct Scope {
  uint32_t cls = 0xFF, pass = 0xFF, layer = 0;
  bool skip = false;
};
Scope& CurrentScope();
// Whether FORZA_TEST_SKIP_LIST_GROUPS names the group of a track or procedural scope.
bool SkipsGroup(uint32_t cls, uint32_t pass, uint32_t layer);
// The RunCommandBuffer hook's own flag (cockpit_gauges.cpp): it returns before the original.
void SetListSkip(bool skip);
class ClassScope {
 public:
  ClassScope(uint32_t cls, uint32_t pass, uint32_t layer) {
    Scope& s = CurrentScope();
    saved_ = s;
    s.cls = cls;
    s.pass = pass;
    s.layer = layer;
    s.skip = SkipsGroup(cls, pass, layer);
    if (s.skip != saved_.skip) SetListSkip(s.skip);
  }
  ~ClassScope() {
    Scope& s = CurrentScope();
    if (s.skip != saved_.skip) SetListSkip(saved_.skip);
    s = saved_;
  }
  ClassScope(const ClassScope&) = delete;
  ClassScope& operator=(const ClassScope&) = delete;

 private:
  Scope saved_;
};

// FORZA_TEST_SKIP_LIST_GROUPS: a RunCommandBuffer call the switch skips (native_device.cpp
// FlushScope, instead of the ledger's FlushBegin; the label still sees its pass), and once a
// frame at the swap (the warning every 10 s).
void SkippedList();
void TestSwitchSwap();

// The shadow mask function runs (from its start to the world pass's resolve of the mask).
void SetMask(bool running);

// Around a flush point's original call (native_device::FlushScope, while the ledger is on).
void FlushBegin(native_device::Point point, PPCContext& ctx, uint8_t* base,
                native_device::LedgerFlush& f);
void FlushEnd(PPCContext& ctx, uint8_t* base, native_device::LedgerFlush& f);

}  // namespace forza::pass_ledger
