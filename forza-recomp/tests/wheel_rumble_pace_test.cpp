// The game's rumble on a wheel's vibration at a fixed pace, whatever the frame rate (ReIGlue
// src/input/wheel/wheel_rumble_pace.h, #467). The game sets its rumble once a frame; the same
// rumble (a road and an engine motor, as functions of time) is played at 20 to 240 fps, with
// uneven frames and the driver's state polls in between, and what the wheel gets is compared: how
// many updates a second, and the vibration's energy a second (each level held until the next
// update). Before, every frame's change went to the wheel; that is modelled too, and on a wheel
// that starts its sine again at every update (DirectInput without DIEP_NORESTART) the jumps that
// leaves are measured. `-v` prints the table. Build against the SDK source
// (tools/build_rexglue_sdk.sh --source-only), from the repository root:
//   SDK=~/rexglue-sdk-src-forza/sdk; g++ -std=c++17 -Wall -Wextra -Werror -I $SDK/include
//       -I $SDK/src/input/wheel forza-recomp/tests/wheel_rumble_pace_test.cpp
//       -o wheel-rumble-pace-test
//   ./wheel-rumble-pace-test -v
#include <wheel_rumble.h>
#include <wheel_rumble_pace.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

using namespace rex::input::wheel;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}

constexpr double kPi = 3.14159265358979323846;
constexpr double kSeconds = 60.0;
// The driver's rate is a setting (wheel_vibration_rate, 60 a second by default); the schedule is
// checked at 30, the console's frame, so 60 fps sends fewer updates than before.
constexpr int kRate = 30;
const double kRumblePaceSeconds = RumblePaceSeconds(kRate);
const double kRumblePaceEarlySeconds = kRumblePaceSeconds * kRumbleEarlyFraction;

double Noise(uint32_t n) {  // 0 to 1, the same for the same n
  n = n * 1664525u + 1013904223u;
  n ^= n >> 15;
  n *= 2246822519u;
  n ^= n >> 13;
  return (n & 0xFFFFFF) / double(0xFFFFFF);
}

// The game's rumble as a function of time (0 to 1, before the driver's strengths), changing every
// frame at any frame rate: the road motor a mix of quick swings with a kerb (full for 80 ms) every
// 2 s; the engine motor a slow swell.
double Road(double t) {
  if (std::fmod(t, 2.0) < 0.08) {
    return 1.0;
  }
  return 0.3 + 0.15 * std::sin(2 * kPi * 3.1 * t) + 0.1 * std::sin(2 * kPi * 7.7 * t + 1.0);
}
double Engine(double t) { return 0.5 + 0.3 * std::sin(2 * kPi * 0.5 * t); }

struct Update {
  double t;
  float level;
};

// What one vibration effect gets at `fps`: `paced` the driver now, otherwise every frame's change
// (before). Frames are up to 10% early or late; the game polls the state 4 times a frame.
std::vector<Update> Play(double (*game)(double), int fps, bool paced) {
  std::vector<Update> out;
  RumblePace pace;
  int sent = -1;
  auto send = [&](double t, float level) {
    const int magnitude = RumbleMagnitude(level);
    if (magnitude != sent) {  // PlayWave updates the effect only when its magnitude changes
      sent = magnitude;
      out.push_back({t, level});
    }
  };
  const double frame = 1.0 / fps;
  for (int k = 0;; k++) {
    const double t = k * frame + frame * 0.1 * (2 * Noise(k * 7 + fps) - 1);
    if (t >= kSeconds) {
      break;
    }
    const float level = static_cast<float>(game(t) * kFineGain);
    if (!paced) {
      send(t, level);
      continue;
    }
    pace.Set(t, level, kRumblePaceSeconds);  // the game's rumble (ApplyRumbleLocked, PaceWave)
    if (sent <= 0 && RumbleMagnitude(level) > 0) {
      pace.Take(t, kRumblePaceSeconds);  // from silence a vibration starts at once, with the game's level
      send(t, level);
    } else if (pace.Due(t, kRumblePaceSeconds)) {
      send(t, pace.Take(t, kRumblePaceSeconds));
    }
    for (int p = 1; p < 4; p++) {  // the state polls (PaceRumble)
      const double tp = t + frame * p / 4;
      if (pace.Due(tp, kRumblePaceSeconds)) {
        send(tp, pace.Take(tp, kRumblePaceSeconds));
      }
    }
  }
  return out;
}

// The vibration's energy a second: each level squared, held until the next update.
double Energy(const std::vector<Update>& u) {
  double e = 0;
  for (size_t i = 0; i < u.size(); i++) {
    const double end = i + 1 < u.size() ? u[i + 1].t : kSeconds;
    e += double(u[i].level) * u[i].level * (end - u[i].t);
  }
  return e / kSeconds;
}

// The game's own energy a second (its level squared over time, finely sampled).
double GameEnergy(double (*game)(double)) {
  double e = 0;
  const double dt = 1e-4;
  for (double t = 0; t < kSeconds; t += dt) {
    const double l = game(t) * kFineGain;
    e += l * l * dt;
  }
  return e / kSeconds;
}

// A wheel that starts its sine again at every update: the played waveform, sampled at 8 kHz, and
// the jumps in it a second (the squared steps of the waveform beyond a sine's own), relative to
// the steps of a sine that is never started again at the same levels.
double RestartJumps(const std::vector<Update>& u, double period_s) {
  const double fs = 8000;
  double jumps = 0, smooth = 0, prev = 0, prev_smooth = 0;
  size_t i = 0;
  double start = u.empty() ? 0 : u[0].t;
  for (int n = 0; n < kSeconds * fs; n++) {
    const double t = n / fs;
    while (i + 1 < u.size() && u[i + 1].t <= t) {
      start = u[++i].t;
    }
    const double level = u.empty() || t < u[0].t ? 0 : u[i].level;
    const double y = level * std::sin(2 * kPi * (t - start) / period_s);
    const double ys = level * std::sin(2 * kPi * t / period_s);
    if (n) {
      jumps += (y - prev) * (y - prev);
      smooth += (ys - prev_smooth) * (ys - prev_smooth);
    }
    prev = y;
    prev_smooth = ys;
  }
  return smooth > 0 ? jumps / smooth : 0;
}

bool Near(double a, double b, double rel) { return std::fabs(a - b) <= rel * std::fabs(b); }
}  // namespace

int main(int argc, char** argv) {
  const bool verbose = argc > 1 && std::strcmp(argv[1], "-v") == 0;

  // --- the pace itself ------------------------------------------------------------------------
  RumblePace p;
  Check(!p.Due(0, kRumblePaceSeconds), "nothing is due before the game's first rumble");
  p.Set(10.0, 0.5f, kRumblePaceSeconds);
  Check(!p.Due(10.0 + kRumblePaceSeconds / 2, kRumblePaceSeconds), "not due within the first pace");
  Check(p.Due(10.0 + kRumblePaceSeconds - kRumblePaceEarlySeconds / 2, kRumblePaceSeconds), "due a pace on (a frame "
        "slightly early counts)");
  Check(std::fabs(p.Take(10.0 + kRumblePaceSeconds, kRumblePaceSeconds) - 0.5f) < 1e-6f, "a held level is itself");
  // Half the interval at 1, half at 0: the root mean square, sqrt(1/2).
  p.Set(10.0 + kRumblePaceSeconds, 1.0f, kRumblePaceSeconds);
  p.Set(10.0 + 1.5 * kRumblePaceSeconds, 0.0f, kRumblePaceSeconds);
  Check(std::fabs(p.Take(10.0 + 2 * kRumblePaceSeconds, kRumblePaceSeconds) - std::sqrt(0.5f)) < 1e-4f,
        "the root mean square over the interval");
  // A one-frame bump at 240 fps (4 ms at full) between two updates is still felt.
  p.Set(10.0 + 2 * kRumblePaceSeconds + 0.010, 1.0f, kRumblePaceSeconds);
  p.Set(10.0 + 2 * kRumblePaceSeconds + 0.010 + 1.0 / 240, 0.0f, kRumblePaceSeconds);
  const float bump = p.Take(10.0 + 3 * kRumblePaceSeconds, kRumblePaceSeconds);
  Check(std::fabs(bump - std::sqrt((1.0 / 240) / kRumblePaceSeconds)) < 1e-3, "a short bump");
  // After a hitch (the game away for 300 ms) one update, not a burst to catch up.
  p.Set(10.0 + 3 * kRumblePaceSeconds + 0.3, 0.2f, kRumblePaceSeconds);
  Check(p.Due(10.0 + 3 * kRumblePaceSeconds + 0.3, kRumblePaceSeconds), "due after a hitch");
  p.Take(10.0 + 3 * kRumblePaceSeconds + 0.3, kRumblePaceSeconds);
  Check(!p.Due(10.0 + 3 * kRumblePaceSeconds + 0.3 + 0.001, kRumblePaceSeconds), "no burst after a hitch");
  // The game's last rumble is 0 and it stops calling: the polls stop the vibration within a pace.
  RumblePace q;
  q.Set(0.0, 0.8f, kRumblePaceSeconds);
  q.Set(0.01, 0.0f, kRumblePaceSeconds);
  double t = 0.01;
  float last = 1.0f;
  while (t < 0.01 + 2 * kRumblePaceSeconds) {
    t += 0.004;
    if (q.Due(t, kRumblePaceSeconds)) {
      last = q.Take(t, kRumblePaceSeconds);
    }
  }
  Check(last == 0.0f, "a vibration the game set to 0 stops");

  // --- the same rumble at every frame rate ------------------------------------------------------
  struct Wave {
    const char* name;
    double (*game)(double);
    double period_s;
  } waves[] = {{"road", Road, kFineRoadPeriodMs / 1000.0},
               {"engine", Engine, kFineEnginePeriodMs / 1000.0}};
  const int rates[] = {60, 20, 30, 40, 90, 120, 144, 165, 240};  // 60 first: the reference
  if (verbose) {
    std::printf("%-6s %4s | %-30s | %-30s\n", "wave", "fps", "before: updates/s energy/s jumps",
                "after: updates/s energy/s jumps");
    std::printf("(energy: level^2 a second, x 1e4; jumps: on a wheel that restarts its sine at "
                "every update, relative to a sine never restarted)\n");
  }
  for (const Wave& w : waves) {
    const double game = GameEnergy(w.game);
    double energy_60 = 0, jumps_60 = 0, before_jumps_60 = 0, before_jumps_240 = 0;
    for (int fps : rates) {
      const auto before = Play(w.game, fps, false);
      const auto after = Play(w.game, fps, true);
      const double ups = after.size() / kSeconds, ups_before = before.size() / kSeconds;
      const double e = Energy(after), e_before = Energy(before);
      Check(!after.empty() && after[0].t < 1.2 / fps, "the vibration starts at the first frame");
      const double j = RestartJumps(after, w.period_s), j_before = RestartJumps(before, w.period_s);
      if (verbose) {
        std::printf("%-6s %4d | %8.1f %10.2f %9.2f | %8.1f %10.2f %9.2f\n", w.name, fps,
                    ups_before, e_before * 1e4, j_before, ups, e * 1e4, j);
      }
      if (fps == 60) {
        energy_60 = e;
        jumps_60 = j;
        before_jumps_60 = j_before;
      }
      if (fps == 240) {
        before_jumps_240 = j_before;
      }
      // About 30 updates a second at every cap (the polls between frames keep the pace at 20 fps).
      Check(ups <= 1.0 / kRumblePaceSeconds + 0.5, "at most 30 updates a second");
      Check(ups >= 1.0 / kRumblePaceSeconds - 2, "about 30 updates a second at every cap");
      // The energy a second the game sent, at every frame rate.
      Check(Near(e, e_before, 0.02), "the pace keeps the energy a second the game sent");
      Check(Near(e, game, 0.05), "the vibration's energy a second is the game's");
      if (energy_60 > 0) {
        // (The game's frames sample its rumble a little differently at each rate: before, the
        // energy a second spread as much.)
        Check(Near(e, energy_60, 0.08), "the same energy a second at every frame rate");
        // On a wheel that starts its sine again at every update, about the same jumps at every cap
        // from 30 fps.
        Check(fps < 30 || Near(j, jumps_60, 0.3), "the same restarts at every frame rate");
      }
    }
    // Before: an update every frame, so a wheel that restarts its sine jumped about 4 times as
    // much at 240 fps as at 60 (#467: "through the entire rig").
    Check(before_jumps_240 > 3 * before_jumps_60, "before, the jumps grew with the frame rate");
    Check(jumps_60 < before_jumps_60, "fewer jumps than before at 60 fps");
  }
  std::puts("wheel_rumble_pace_test: ok");
  return 0;
}
