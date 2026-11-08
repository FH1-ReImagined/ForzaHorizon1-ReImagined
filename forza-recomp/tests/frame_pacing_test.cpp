// Frame pacing at 20 and 40 fps (src/platform/frame_pacing.h, #263):
//   g++ -std=c++20 -O2 -Wall -Wextra -Werror -I forza-recomp/src forza-recomp/tests/frame_pacing_test.cpp
#include "platform/frame_pacing.h"

#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <vector>

using namespace forza::frame_pacing;

namespace {

// A small deterministic generator (uniform in [0, 1)).
struct Lcg {
  uint64_t state = 12345;
  double Next() {
    state = state * 6364136223846793005ull + 1442695040888963407ull;
    return double(state >> 11) / double(1ull << 53);
  }
};

// The game's vblank counter with mainthread30fps on (sub_8247C678): +1 per vblank it is
// shown, releases the main loop and resets when it reaches 2.
struct GameCounter {
  int count;
  bool Vblank() {
    if (++count < 2) return false;
    count = 0;
    return true;
  }
};

// A FIFO screen of `period` ms: a frame presented at t is shown from the first refresh at
// or after t + phase. Returns how many refreshes each frame stayed on screen.
std::vector<int> Refreshes(const std::vector<double>& presents, double period, double phase) {
  std::vector<int> out;
  for (size_t i = 1; i < presents.size(); ++i) {
    out.push_back(int(std::ceil((presents[i] + phase) / period) -
                      std::ceil((presents[i - 1] + phase) / period)));
  }
  return out;
}

void TestDividers() {
  assert(VblanksPerFrame(30, 480) == 2);
  assert(VblanksPerFrame(20, 480) == 3 && VblanksPerFrame(20, 240) == 3);  // 60 Hz
  assert(VblanksPerFrame(40, 480) == 3 && VblanksPerFrame(40, 240) == 3);  // 120 Hz
  assert(VblanksPerFrame(60, 480) == 2 && VblanksPerFrame(144, 480) == 2);
  assert(VblanksPerFrame(144, 240) == 1 && VblanksPerFrame(240, 480) == 2);
  assert(VblanksPerFrame(20, 480, 2) == 2 && VblanksPerFrame(144, 480, 1) == 1);
  assert(VblanksPerFrame(30, 480, 1) == 2);                                // 30 stays the console's
  assert(VblanksPerFrame(240, 480, 3) == 2 && VblanksPerFrame(144, 480, 3) == 3);
}

// #756: frame_rate 60 started with video_mode_refresh_rate 60 left in forza.toml (an RTX 3080
// at 30 fps until an MSAA change was saved in the Graphics menu, which sets the vblank from
// frame_rate). The frame rate a start runs is its vblank rate over the divider.
void TestStartVblank() {
  auto fps = [](int rate, double configured, double max = 480) {
    return StartVblankHz(rate, configured, max) / VblanksPerFrame(rate, max);
  };
  assert(fps(60, 60) == 60 && StartVblankHz(60, 60, 480) == 120);  // was 30 fps
  assert(fps(60, 120) == 60 && fps(60, 144) == 60);
  assert(fps(144, 60) == 144 && StartVblankHz(144, 60, 480) == 288);
  assert(fps(144, 60, 240) == 144 && StartVblankHz(144, 60, 240) == 144);  // divider 1
  assert(fps(20, 120) == 20 && StartVblankHz(20, 120, 480) == 60);
  assert(fps(40, 60) == 40 && StartVblankHz(40, 60, 480) == 120);
  assert(StartVblankHz(30, 60, 480) == 60 && StartVblankHz(30, 120, 480) == 120);  // 30 keeps it
  assert(VblankHz(60, 480) == 120 && VblankHz(144, 480, 1) == 144 && VblankHz(30, 480) == 60);
}

void TestThirdVblank() {
  // Whatever the game's count and our phase at the start, the main loop is released every
  // third vblank once the first release happened.
  for (int start_count = 0; start_count < 2; ++start_count) {
    for (int skip = 0; skip < 3; ++skip) {
      ThirdVblank third;
      for (int i = 0; i < skip; ++i) third.Forward();
      GameCounter game{start_count};
      std::vector<int> releases;
      for (int vblank = 0; vblank < 300; ++vblank) {
        if (third.Forward() && game.Vblank()) releases.push_back(vblank);
      }
      assert(releases.size() >= 99);
      for (size_t i = 1; i < releases.size(); ++i) assert(releases[i] - releases[i - 1] == 3);
    }
  }
}

// Presents at `frame` ms intervals, each `elapsed(k)` after its frame period's start, paced or
// not; the share of frames a FIFO screen of `refresh` ms keeps exactly `want` refreshes at
// its worst phase (frames 1000 on).
struct Run {
  std::vector<double> raw, paced;
  PresentPacer pacer;
  int held = 0, measured = 0;

  template <typename Elapsed>
  Run(double frame, double ms, Elapsed elapsed) {
    for (int k = 0; k < 4000; ++k) {
      const double e = elapsed(k);
      const double wait = pacer.Delay(e, frame, ms);
      assert(wait >= 0 && e + wait <= 0.9 * frame + 1e-6);
      raw.push_back(k * frame + e);
      paced.push_back(k * frame + e + wait);
      if (k >= 1000) {
        ++measured;
        held += std::abs(e + wait - pacer.offset()) < 0.06 * ms;
      }
    }
  }

  static double Worst(const std::vector<double>& t, double refresh, int want) {
    const std::vector<double> tail(t.begin() + 1000, t.end());
    double worst = 1;
    for (int i = 0; i < 100; ++i) {
      int ok = 0;
      const auto r = Refreshes(tail, refresh, refresh * i / 100);
      for (int n : r) ok += n == want;
      worst = std::min(worst, double(ok) / double(r.size()));
    }
    return worst;
  }
};

void TestPacer20() {
  // 20 fps (ticks of 1 us): as measured in a race, the frames reach the present 18.1 to 19.8 ms
  // after their period's start; 1% come a 60 Hz vblank later.
  const double ms = 1000.0, frame = 50 * ms;
  Lcg rng;
  Run run(frame, ms, [&](int) {
    double e = (18.1 + 1.7 * rng.Next()) * ms;
    if (rng.Next() < 0.01) e += 16.667 * ms;
    return e;
  });
  assert(run.pacer.offset() > 19.5 * ms && run.pacer.offset() < 20.0 * ms);
  assert(run.held > run.measured * 95 / 100);
  const double raw = Run::Worst(run.raw, 1000.0 / 60 * ms, 3);
  const double paced = Run::Worst(run.paced, 1000.0 / 60 * ms, 3);
  std::printf("20 fps, 60 Hz screen, worst phase: %.1f%% of frames 3 refreshes unpaced, %.1f%% paced\n",
              100 * raw, 100 * paced);
  assert(paced > 0.97 && raw < 0.8);
}

void TestPacer40() {
  // 40 fps on a 120 Hz vblank: 95% of the frames swap on the second vblank of their period
  // (18.7 to 19.8 ms), 5% already on the first (10.8 ms).
  const double ms = 1000.0, frame = 25 * ms;
  Lcg rng;
  Run run(frame, ms, [&](int) {
    return rng.Next() < 0.05 ? (10.7 + 0.2 * rng.Next()) * ms : (18.7 + 1.1 * rng.Next()) * ms;
  });
  assert(run.held > run.measured * 95 / 100);
  const double raw = Run::Worst(run.raw, 1000.0 / 120 * ms, 3);
  const double paced = Run::Worst(run.paced, 1000.0 / 120 * ms, 3);
  std::printf("40 fps, 120 Hz screen, worst phase: %.1f%% of frames 3 refreshes unpaced, %.1f%% paced\n",
              100 * raw, 100 * paced);
  assert(paced > 0.97 && raw < 0.92);
}

void TestPacerLeavesOtherPresentsAlone() {
  const double ms = 1000.0, frame = 25 * ms;
  PresentPacer pacer;
  assert(pacer.Delay(frame, frame, ms) == 0);  // outside the period
  assert(pacer.Delay(-1, frame, ms) == 0);
  for (int i = 0; i < 400; ++i) pacer.Delay(24 * ms, frame, ms);  // all very late
  assert(pacer.offset() <= 0.9 * frame + 1e-9);  // never held past 90% of the period
  assert(pacer.Delay(23.5 * ms, frame, ms) == 0);
  // It comes back down when the frames come early again (about 1 ms in 20 s at 40 fps).
  for (int i = 0; i < 20000; ++i) pacer.Delay(5 * ms, frame, ms);
  assert(pacer.offset() < 6 * ms);
}

}  // namespace

int main() {
  TestDividers();
  TestStartVblank();
  TestThirdVblank();
  TestPacer20();
  TestPacer40();
  TestPacerLeavesOtherPresentsAlone();
  std::puts("frame pacing: ok");
  return 0;
}
