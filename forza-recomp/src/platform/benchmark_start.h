// forza - how long the benchmark waits for its race to start (src/platform/benchmark_session.cpp).
// Without guest memory or cvars, so tests/benchmark_start_test.cpp can check it.
//
// It used to give up 5 minutes after the start (4 without the event requested), which a slow
// integrated GPU's first load can need. The wait follows progress instead: it ends when no frame
// has been presented for kNoFrameSeconds (the game stopped drawing), and otherwise only at the far
// limits. Measured on an Intel UHD with an empty Direct3D shader cache of the driver (#725): the
// game kept presenting at about 10 fps after frames of 7-12 s of shader compiles, but the event
// did not load in 30 minutes either (2 of 2 runs), so that load stalls outright; this limit only
// stops a slow first load from being cut short.
#pragma once

namespace forza::benchmark {

constexpr double kNoFrameSeconds = 60;
constexpr double kEventRequestSeconds = 15 * 60;  // to get from the boot to the event's request
constexpr double kStartSeconds = 30 * 60;         // to get from the boot to the race

// The reason to give up, or nullptr to keep waiting. `since_frame` < 0: no frame yet.
inline const char *StartTimeout(double since_start, double since_frame, bool event_started) {
  if (since_frame >= kNoFrameSeconds) return "benchmark_stalled";
  if (!event_started && since_start >= kEventRequestSeconds) return "event_load_timeout";
  if (since_start >= kStartSeconds) return "benchmark_start_timeout";
  return nullptr;
}

}  // namespace forza::benchmark
