// The trigger effects' "player is driving" gate (src/platform/driving_gate.h, #224, #692): the
// game's rumble turns it on, a hold bridges quiet moments, and it releases in a menu, also online. And the racing
// wheel's (#256, #304): the game's simulation state once a frame, counted in frames so that a
// hitch, a wall hit, the window losing focus or a long drive never fade the forces. Build from the
// repository root:
//   g++ -std=c++23 -Wall -Wextra -Werror -Iforza-recomp/src forza-recomp/tests/driving_gate_test.cpp
//       -o driving-gate-test && ./driving-gate-test
#include "platform/driving_gate.h"

#include <cstdio>
#include <cstdlib>

using forza::telemetry::Drive;
using forza::telemetry::DrivingGate;
using forza::telemetry::kDrivingHoldMs;
using forza::telemetry::kDrivingSettleMs;
using forza::telemetry::kMenuMs;
using forza::telemetry::PlayerGate;
using forza::telemetry::WorldFrame;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}

// The game's main loop: each frame polls the pad 4 times and updates the world once.
struct Game {
  PlayerGate gate;
  uint64_t t = 1000;
  int flips = 0;  // changes between driving and not driving
  Drive last = Drive::kUnknown;
  void Frame(const WorldFrame& f, uint64_t frame_ms = 16, int polls = 4) {
    for (int i = 0; i < polls; ++i) gate.Poll(t);
    gate.Frame(t, f);
    const Drive now = gate.State();
    if ((now == Drive::kDriving) != (last == Drive::kDriving) && last != Drive::kUnknown) flips++;
    last = now;
    t += frame_ms;
  }
  void Frames(int n, const WorldFrame& f, uint64_t frame_ms = 16) {
    for (int i = 0; i < n; ++i) Frame(f, frame_ms);
  }
  // Menus: the pad is polled, no world is updated.
  void MenuFrames(int n, uint64_t frame_ms = 16) {
    for (int i = 0; i < n; ++i) {
      for (int p = 0; p < 4; ++p) gate.Poll(t);
      last = gate.State();
      t += frame_ms;
    }
  }
  bool Driving() { return gate.State() == Drive::kDriving; }
};
WorldFrame Driving() { WorldFrame f; f.stepped = true; return f; }
WorldFrame Paused() { WorldFrame f; f.paused = true; return f; }
WorldFrame Rewinding() { WorldFrame f; f.rewinding = true; f.stepped = true; return f; }
WorldFrame Loading() { WorldFrame f; f.loaded = false; return f; }
WorldFrame NoStep() { return WorldFrame{}; }
}  // namespace

int main() {
  DrivingGate gate;
  Check(!gate.Driving(1000), "not driving before any rumble");
  gate.Rumble(0, 0, 1000);
  Check(!gate.Driving(1000), "zero rumble (menus send it) does not count");
  gate.Rumble(0, 500, 2000);
  Check(gate.Driving(2000), "rumble means driving");
  gate.Rumble(0, 0, 2500);
  Check(gate.Driving(2500 + 100), "zero rumble does not cancel the hold");
  Check(gate.Driving(2000 + kDrivingHoldMs - 1), "held through a quiet moment");
  Check(!gate.Driving(2000 + kDrivingHoldMs), "released once the hold ends (menu)");
  gate.Rumble(300, 0, 9000);
  Check(gate.Driving(9500), "comes back when driving resumes");
  DrivingGate fresh;
  fresh.Rumble(1, 1, 0);
  Check(fresh.Driving(10), "time zero is a valid rumble time");
  forza::telemetry::Gate().Rumble(1, 0, forza::telemetry::DrivingNowMs());
  Check(forza::telemetry::IsDriving(), "the shared flag follows the rumble");

  // The wheel's gate. Boot: the title and main menus poll the pad and update no world.
  Game g;
  Check(g.gate.State() == Drive::kUnknown, "nothing said before the game runs");
  g.MenuFrames(2);
  Check(g.gate.State() == Drive::kUnknown, "a few polls are no menu yet");
  g.MenuFrames(30);
  Check(g.gate.State() == Drive::kMenu, "polls without a world update: a menu");
  // Loading free roam: the world updates before it has started, then its physics runs.
  g.Frames(10, Loading());
  Check(g.gate.State() == Drive::kLoading, "a world that has not started: loading");
  g.Frames(3, Driving());
  Check(!g.Driving(), "three frames of physics are not enough: the settle time");
  g.Frames(int(kDrivingSettleMs / 16) + 1, Driving());
  Check(g.Driving(), "physics for the settle time: driving");
  g.flips = 0;

  // A long drive: 5 minutes at 60 fps, with the odd frame of 0.1 ms that steps no physics, and
  // the pad polled 4 times a frame. Never a flip.
  for (int i = 0; i < 5 * 60 * 60; ++i) {
    if (i % 97 == 0) g.Frame(NoStep(), 0);
    g.Frame(Driving());
  }
  Check(g.Driving() && g.flips == 0, "a 5 minute drive stays driving");

  // A wall hit: thousands of substeps in one frame, then a hitch of 700 ms (no frame, no poll), a
  // few frames without physics while the car is stuck... the physics still steps every frame.
  g.Frame(Driving(), 700);
  g.Frame(Driving(), 1500);  // a longer hitch: a shader compiled on the crash
  g.Frames(5, Driving());
  Check(g.Driving() && g.flips == 0, "a wall hit and its hitches stay driving");
  // A hitch with the pad polled a few times around it (the polls of one or two frames).
  for (int p = 0; p < 8; ++p) g.gate.Poll(g.t);
  g.t += 900;
  for (int p = 0; p < 4; ++p) g.gate.Poll(g.t);
  g.Frame(Driving());
  Check(g.Driving() && g.flips == 0, "polls around a hitch are no menu");

  // The window losing focus (alt-tab): the input stops reaching the game, the frames go on.
  g.Frames(600, Driving());
  // Minimised: no frames and no polls for 5 s, then the game goes on.
  g.t += 5000;
  g.Frames(10, Driving());
  Check(g.Driving() && g.flips == 0, "losing focus or being minimised stays driving");

  // An unknown stop of the physics with the world running (no pause flag): only once it lasts.
  g.Frames(20, NoStep());
  Check(g.Driving(), "a short run of frames without physics stays driving");
  g.Frames(40, NoStep());
  Check(g.gate.State() == Drive::kStopped, "a long one: the car's physics stopped");
  g.Frames(int(kDrivingSettleMs / 16) + 2, Driving());
  Check(g.Driving(), "and back");
  g.flips = 0;

  // The pause menu: the pause flag set.
  g.Frames(2, Paused());
  Check(g.Driving(), "two frames are no pause yet (the flag is set for a frame at Resume)");
  g.Frame(Paused());
  Check(g.gate.State() == Drive::kPause, "the pause flag for three frames: pause");
  g.Frames(200, Paused());
  g.Frames(int(kDrivingSettleMs / 16) + 2, Driving());
  Check(g.Driving() && g.flips == 2, "resumed: driving after the settle time");

  // A rewind: the car plays backwards (time scale -1, its physics still stepping), then the
  // rewind menu (paused), then Resume from here (paused for a second, then physics).
  g.Frame(Paused());
  g.Frames(100, Rewinding());
  Check(g.gate.State() == Drive::kRewind, "the car playing backwards: rewind");
  g.Frames(60, Paused());
  Check(g.gate.State() == Drive::kRewind, "the rewind menu is still the rewind, not a pause");
  g.Frames(60, Paused());
  g.Frames(int(kDrivingSettleMs / 16) + 2, Driving());
  Check(g.Driving(), "Resume from here: driving");
  g.Frames(3, Paused());
  Check(g.gate.State() == Drive::kPause, "a later pause is a pause again");
  g.Frames(int(kDrivingSettleMs / 16) + 2, Driving());

  // Quick toggles: pause and resume within a few frames, over and over. The state follows each
  // that lasts, and the last one, driving, wins.
  for (int i = 0; i < 50; ++i) {
    g.Frames(1 + i % 4, Paused());
    g.Frames(1 + i % 7, Driving());
  }
  g.Frames(int(kDrivingSettleMs / 16) + 2, Driving());
  Check(g.Driving(), "quick toggles end driving");

  // Leaving for a menu (end free roam, an event's menus): polls and no world update.
  g.MenuFrames(int(kMenuMs / 16) + 4);
  Check(g.gate.State() == Drive::kMenu, "a menu");
  g.Frames(3, Paused());
  Check(g.gate.State() == Drive::kPause, "a world again: its state");

  // Frames at 240 fps: three frames are 12 ms, the settle time still 500 ms.
  Game fast;
  fast.Frames(3, Paused(), 4);
  Check(fast.gate.State() == Drive::kPause, "240 fps: pause");
  fast.Frames(100, Driving(), 4);
  Check(!fast.Driving(), "240 fps: not driving before the settle time");
  fast.Frames(30, Driving(), 4);
  Check(fast.Driving(), "240 fps: driving after it");

  // The trigger effects (#692): an online pause menu keeps the world running, so PlayerGate says
  // driving; the game's rumble stopping turns the triggers off. Without any rumble (vibration off
  // in the game's options, a wheel) PlayerGate alone decides.
  using forza::telemetry::TriggerGate;
  using forza::telemetry::Triggers;
  DrivingGate quiet;
  Check(TriggerGate(Drive::kDriving, quiet, 5000) == Triggers::kOn,
        "triggers: a game that never rumbles follows PlayerGate");
  Check(TriggerGate(Drive::kPause, quiet, 5000) == Triggers::kNotDriving,
        "triggers: off in the pause menu offline");
  DrivingGate online;
  online.Rumble(0, 900, 10000);
  Check(TriggerGate(Drive::kDriving, online, 10500) == Triggers::kOn, "triggers: on while driving");
  online.Rumble(0, 0, 11000);  // the pause menu opens: the game stops its rumble, the world runs on
  Check(TriggerGate(Drive::kDriving, online, 10000 + kDrivingHoldMs) == Triggers::kNoRumble,
        "triggers: off in the online pause menu once the rumble hold ends");
  Check(TriggerGate(Drive::kMenu, online, 10500) == Triggers::kNotDriving,
        "triggers: off in a menu even with rumble");
  online.Rumble(400, 0, 30000);  // resumed
  Check(TriggerGate(Drive::kDriving, online, 30016) == Triggers::kOn, "triggers: back when driving");

  std::puts("PASS");
}
