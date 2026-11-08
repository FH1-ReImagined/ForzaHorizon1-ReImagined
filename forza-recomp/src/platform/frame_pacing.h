// forza - frame pacing arithmetic of src/hooks/frame_time.cpp (docs/framerate.md, #263).
// No runtime in here: forza-recomp/tests/frame_pacing_test.cpp.

#pragma once

#include <algorithm>
#include <cstdint>

namespace forza::frame_pacing {

// Guest vblanks per game frame: 2 at the console's 30; 3 at 20 and 40 (a 60 and a 120 Hz
// vblank, the screens those rates divide); above that 2 while twice the rate fits under the
// SDK's limit for the guest refresh rate, else 1. `forced` (FORZA_VBLANK_DIVIDER, 0 = none)
// picks 1, 2, or 3 where that fits, for comparisons.
inline int VblanksPerFrame(int fps, double max_vblank_hz, int forced = 0) {
  if (fps == 30) return 2;
  if (forced == 1 || forced == 2 || (forced == 3 && 3 * fps <= max_vblank_hz)) return forced;
  if (fps < 60) return 3;
  return 2 * fps <= max_vblank_hz ? 2 : 1;
}

// The guest vblank rate (Hz) frame_rate asks for: VblanksPerFrame() vblanks a frame.
inline int VblankHz(int fps, double max_vblank_hz, int forced = 0) {
  return fps * VblanksPerFrame(fps, max_vblank_hz, forced);
}

// The guest vblank rate a start runs, from frame_rate and the video_mode_refresh_rate it was
// started with (#756). Away from 30, frame_rate sets it, as the in-game Graphics menu does at
// every Save: a refresh rate left in forza.toml from another frame rate (60 Hz with frame_rate
// 60) ran the game at half its frame rate until a graphics setting was saved. At the console's
// 30 a refresh rate set in forza.toml or on the command line stays.
inline double StartVblankHz(int fps, double configured_hz, double max_vblank_hz, int forced = 0) {
  return fps == 30 ? configured_hz : double(VblankHz(fps, max_vblank_hz, forced));
}

// A divider of three on top of the game's own counter, which (with mainthread30fps on)
// releases the main loop at every second vblank it is shown. Shown two of every three, it
// releases the main loop every third vblank, whatever its count was at the start.
class ThirdVblank {
 public:
  bool Forward() { return ++vblanks_ % 3 != 0; }

 private:
  uint32_t vblanks_ = 0;
};

// Present pacing. Where in its frame period a guest frame reaches the host present varies: the
// emulated GPU's swap ends on a guest vblank, the SDK's VSync thread fires a vblank up to
// about 1 ms after its time, and a frame ready a little later swaps a vblank later. Holding
// each frame's present until a fixed time after the start of its frame period puts the
// frames on the screen evenly spaced. That time is about the 98th percentile of the times
// the frames come at (tracked: 49 small steps down for each step up, starting from the
// first), so it adapts to the machine; a frame that comes later is not held.
class PresentPacer {
 public:
  // `elapsed`: ticks from the start of the frame period to now (0 to `period`), `period`:
  // ticks per frame, `ms`: ticks per millisecond. Returns the ticks to wait before presenting.
  double Delay(double elapsed, double period, double ms) {
    if (!(elapsed >= 0) || elapsed >= period) return 0;
    if (offset_ <= 0) offset_ = elapsed;
    offset_ += elapsed > offset_ ? kStepMs * ms : -kStepMs / 49 * ms;
    offset_ = std::clamp(offset_, 0.5 * ms, std::max(0.5 * ms, 0.9 * period));
    return elapsed < offset_ ? offset_ - elapsed : 0;
  }

  // Ticks after the start of the frame period that presents are held until.
  double offset() const { return offset_; }

 private:
  static constexpr double kStepMs = 0.05;
  double offset_ = 0;
};

}  // namespace forza::frame_pacing
