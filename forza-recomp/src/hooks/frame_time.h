// forza - frame pacing and frame time at rates other than the console's 30
// (src/hooks/frame_time.cpp, docs/framerate.md).

#pragma once

#include <cstdint>

namespace forza::frame_time {

// frame_rate is the console's 30: the game paces and times itself as on the Xbox 360, and the
// hooks here and in crowd_anim.cpp leave it alone. At every other rate (20, 40, 60 to 240)
// the frame time comes from the guest tick clock and the render sync runs.
bool ConsoleRate();

// 20 or 40 fps: rates that divide 60 and 120 Hz screens, so each frame can stay on screen for
// the same number of refreshes; their presents are paced (PresentDelayNs).
bool EvenRate();

// Guest vblanks per game frame for the frame_rate setting: 2 at 30 (the console's), 3 at 20
// and 40 (a 60 and a 120 Hz vblank), and above 40 two when twice the frame rate fits under the
// SDK's limit for the guest refresh rate (480 Hz with SDK patch 0037, else 240), else one.
int VblanksPerFrame();

// How long the host present about to happen should wait (ns, 0 = present now): at 20 and
// 40 fps the first present of each guest frame waits until a fixed time after the start of
// its frame period, so the frames reach the screen evenly spaced.
int64_t PresentDelayNs();

// The guest vblank rate frame_rate asks for (frame_rate * VblanksPerFrame()).
int VblankHz();

// The guest vblank rate the game starts with (src/main.cpp): VblankHz() away from 30, where a
// video_mode_refresh_rate set in forza.toml no longer halves the frame rate (#756); at 30 the
// configured video_mode_refresh_rate.
double StartVblankHz();

// The frame time the game aims for, in ms: VblanksPerFrame() guest vblanks at the
// configured guest refresh rate.
double TargetMs();

// The render thread handed a frame to VdSwap (src/debug/frame_dump.cpp).
void OnGuestSwap();
void ResetRenderTiming();

}  // namespace forza::frame_time
