// The game's force feedback in the log, and the wheel's effects kept playing (SDK patches 0145 and
// 0146, src/input/wheel/wheel_ff_log.h and wheel_ff_keep_playing.h, #304): a block's line when it
// changes (not every frame), the 10 s summary of what the game sent and the wheel got, and the
// restarts for a wheel that ends effects by itself (wheel_ffb_keep_playing). Build
// against the patched SDK source (tools/build_rexglue_sdk.sh --source-only), from the repository
// root:
//   SDK=~/rexglue-sdk-src-forza/sdk; g++ -std=c++17 -Wall -Wextra -Werror -I $SDK/include
//       -I $SDK/src/input/wheel forza-recomp/tests/wheel_ff_log_test.cpp -o wheel-ff-log-test
//   ./wheel-ff-log-test
#include <wheel_ff_keep_playing.h>
#include <wheel_ff_log.h>

#include <cstdio>
#include <cstdlib>
#include <string>

using namespace rex::input::wheel;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}
bool Has(const std::string& s, const char* part) {
  return s.find(part) != std::string::npos;
}
FfEffect Block(uint32_t block, uint32_t type, uint32_t gain, int32_t word11 = 0, int32_t coeff = 0,
               int32_t sat = 0) {
  FfEffect e;
  e.block = block;
  e.type = type;
  e.gain = gain;
  e.magnitude = e.center = word11;
  e.pos_coeff = e.neg_coeff = coeff;
  e.pos_sat = e.neg_sat = sat;
  return e;
}
}  // namespace

int main() {
  // What is a change worth a line.
  const FfEffect spring = Block(4, kFfSpring, 255, 0, 63, 127);
  Check(!FfBlockChanged(spring, spring), "the same block");
  FfEffect s2 = spring;
  s2.gain = 40;
  Check(!FfBlockChanged(spring, s2), "the spring's gain moves: only in the summary");
  s2.gain = 0;
  Check(!FfBlockChanged(spring, s2), "the spring's gain through 0 (FH1, every few seconds): too");
  s2 = spring;
  s2.pos_coeff = 0;
  Check(FfBlockChanged(spring, s2), "a coefficient");
  s2 = spring;
  s2.deadband = 10;
  Check(FfBlockChanged(spring, s2), "the dead band");
  s2 = spring;
  s2.type = kFfDamper;
  Check(FfBlockChanged(spring, s2), "another type");
  const FfEffect force = Block(5, kFfConstant, 255, 40);
  Check(!FfBlockChanged(force, Block(5, kFfConstant, 255, -90)), "the steering force moves");
  Check(!FfBlockChanged(force, Block(5, kFfConstant, 128, 3)), "the constant's gain moves");
  Check(FfBlockChanged(force, Block(5, kFfConstant, 0, 3)), "the steering force's gain to 0");
  Check(FfBlockChanged(Block(5, kFfConstant, 0, 3), force), "and back from 0");
  Check(FfBlockChanged(force, Block(5, kFfSpring, 255, 0, 63, 127)), "the constant becomes a spring");

  // Lines: the first definition is the driver's; a change once, at most once a second per block.
  FfLog log;
  FfState st;
  double t = 100.0;
  auto define = [&](const FfEffect& e) {
    st.Define(e);
    return log.Defined(e, t);
  };
  Check(!define(force), "first definition: logged by the driver");
  Check(!define(spring), "first definition of another block");
  for (int i = 0; i < 600; i++) {  // 10 s of frames: the force moves, nothing to log
    t += 1.0 / 60;
    Check(!define(Block(5, kFfConstant, 255, (i % 200) - 100)), "a frame's steering force");
  }
  Check(!define(Block(4, kFfSpring, 0, 0, 63, 127)), "the spring's gain to 0: no line");
  Check(define(Block(5, kFfConstant, 0, 0)), "the steering force's gain to 0: a line");
  Check(define(Block(4, kFfSpring, 255, 0, 30, 127)), "the spring's coefficients: a line");
  t += 0.5;
  Check(!define(Block(4, kFfSpring, 255, 0, 63, 127)), "back within the second: not yet");
  t += 0.6;
  Check(define(Block(4, kFfSpring, 255, 0, 63, 127)), "the next update after the second: the line");
  t += 2.0;
  Check(!define(Block(4, kFfSpring, 0, 0, 63, 127)), "unchanged since its line but the gain");
  Check(define(Block(5, kFfDamper, 255, 0, 12, 25)), "another block is not held back");

  // Operations and controls: the first few of a window.
  int lines = 0;
  for (int i = 0; i < 20; i++) {
    lines += log.Operated(5, i % 2 ? kFfOpStop : 2);
  }
  Check(lines == static_cast<int>(kFfLogEventsPerWindow), "operations: the first 8 of a window");
  Check(!log.Controlled(), "a control after them: counted only");

  // The summary: 10 s windows, only while the game sends anything.
  FfLog sum;
  FfState ss;
  t = 0.0;
  Check(sum.Summary(ss, 0, 0, 1.0f, t).empty(), "the window starts");
  ss.Define(Block(1, kFfConstant, 255, 0));
  ss.Define(Block(4, kFfSpring, 255, 0, 63, 127));
  sum.Defined(Block(1, kFfConstant, 255, 0), t);
  sum.Defined(Block(4, kFfSpring, 255, 0, 63, 127), t);
  ss.Operate(1, 2);
  ss.Operate(4, 2);
  sum.Operated(1, 2);
  sum.Operated(4, 2);
  sum.Sdl(1, FfSdlCall::kCreate, 0);
  sum.Sdl(1, FfSdlCall::kStart);
  sum.Sdl(4, FfSdlCall::kCreate, 8127);
  sum.Sdl(4, FfSdlCall::kStart);
  for (int i = 0; i < 590; i++) {
    t += 1.0 / 60;
    const int32_t m = (i % 100) - 30;  // -30..69
    ss.Define(Block(1, kFfConstant, 255, m));
    sum.Defined(Block(1, kFfConstant, 255, m), t);
    sum.Sdl(1, FfSdlCall::kUpdate, -m * 258);
    const uint32_t g = 100 + i % 100;  // 100..199
    ss.Define(Block(4, kFfSpring, g, 0, 63, 127));
    sum.Defined(Block(4, kFfSpring, g, 0, 63, 127), t);
    sum.Rumbled();
    Check(sum.Summary(ss, 0b1001, 0, 1.0f, t).empty() || t >= 10.0, "no summary within 10 s");
  }
  sum.Sdl(1, FfSdlCall::kKeepPlaying);
  sum.Sdl(1, FfSdlCall::kKeepPlaying);
  sum.Sdl(4, FfSdlCall::kFailure);
  t = 10.0;
  Check(sum.SummaryDue(t), "due at 10 s");
  const std::string a = sum.Summary(ss, 0b1001, 0, 0.5f, t);
  Check(!sum.SummaryDue(t), "a new window");
  std::printf("%s\n", a.c_str());
  Check(Has(a, "the last 10 s:"), "the window's length");
  Check(Has(a, "the last 10 s: block 1 constant: game 591 updates, gain 255, force -30..69, "
               "1 start, playing; wheel 590 updates, 1 creation, 1 start, 2 restarts, "
               "level -17802..7740, running | block 4 spring:"),
        "the game's steering force, and what the wheel got for it");
  Check(Has(a, "block 4 spring: game 591 updates, gain 100..255, 1 start, playing; wheel 0 "
               "updates, 1 creation, 1 start, 1 failure, coefficient 8127, running |"),
        "the spring, game and wheel");
  Check(!Has(a, "block 2") && !Has(a, "block 3"), "undefined blocks left out");
  Check(Has(a, "running | device gain 255, 590 rumbles, forces at 50%"), "the device");
  // A window where the game sends nothing: no summary; a stopped block reads as such.
  t = 20.0;
  Check(sum.Summary(ss, 0b1001, 0, 1.0f, t).empty(), "a quiet window: nothing");
  ss.Operate(1, kFfOpStop);
  sum.Operated(1, kFfOpStop);
  sum.Sdl(1, FfSdlCall::kStop);
  ss.Control(kFfPause);
  ss.Control(kFfContinue);
  sum.Controlled();
  sum.Controlled();
  t = 30.5;
  const std::string b = sum.Summary(ss, 0b1000, 0b1000, 1.0f, t);
  std::printf("%s\n", b.c_str());
  Check(Has(b, "the last 10 s: block 1 constant: game 0 updates, gain 255, 1 stop, not playing; "
               "wheel 0 updates, 1 stop, not running | "),
        "a stopped steering force");
  Check(Has(b, "| block 4 spring: gain 189, playing, running (the wheel says it is not playing) | "
               "2 device controls, device gain 255, 0 rumbles, forces at 100%"),
        "a quiet block: where it stands, and what the wheel says; the counts start again");

  // Restarts for a wheel that ends effects by itself (#304): none by default (SDL patch 0147 sends
  // endless effects on Windows and macOS), every N s when set; an effect the wheel says has
  // stopped is started again unless the setting is -1.
  Check(FfKeepPlayingSeconds(0) == 0 && FfKeepPlayingSeconds(-1) == 0, "no restarts by default");
  Check(FfKeepPlayingSeconds(5) == 5, "every 5 s when set");
  Check(FfRestartStopped(0) && FfRestartStopped(5) && !FfRestartStopped(-1),
        "a stopped effect: started again unless -1");
  Check(!FfKeepPlayingDue(10.0, 14.9, 5) && FfKeepPlayingDue(10.0, 15.0, 5), "every 5 s");
  Check(!FfKeepPlayingDue(10.0, 1000.0, 0), "off: never");
  // Over a 30 minute drive at 60 fps with 5, an effect is never left more than 5 s (plus a frame)
  // without a start.
  double last = 0.0, longest = 0.0;
  for (int frame = 1; frame <= 30 * 60 * 60; frame++) {
    const double now = frame / 60.0;
    if (FfKeepPlayingDue(last, now, FfKeepPlayingSeconds(5))) {
      longest = std::max(longest, now - last);
      last = now;
    }
  }
  Check(longest < 5.0 + 1.5 / 60, "restarted within 5 s all along");

  // The status check every 10 s, with the default setting (0): a wheel that says an effect it was
  // told to play has stopped gets it started again once, counted as a restart in the summary; a
  // restart that fails is counted as a failure (and the driver logs it: NoteFfFailure, the warning).
  FfLog chk;
  FfState cs;
  double tc = 0.0;
  chk.SummaryDue(tc);
  cs.Define(Block(5, kFfConstant, 255, 10));
  cs.Operate(5, 2);
  chk.Defined(Block(5, kFfConstant, 255, 10), tc);
  int starts = 0;
  auto works = [&] { return ++starts > 0; };
  auto fails = [&] { return ++starts < 0; };
  FfStoppedCheck c = FfCheckStopped(0, 0, works);
  Check(c.stopped && c.restarted && !c.failed && starts == 1, "stopped: started again, once");
  FfNoteCheck(chk, 5, c);
  c = FfCheckStopped(1, 0, works);
  Check(!c.stopped && !c.restarted && !c.failed && starts == 1, "still playing: nothing");
  c = FfCheckStopped(-1, 0, works);
  Check(!c.stopped && !c.restarted && starts == 1, "the wheel cannot tell: nothing");
  c = FfCheckStopped(0, -1, works);
  Check(c.stopped && !c.restarted && !c.failed && starts == 1, "-1: logged, not started again");
  c = FfCheckStopped(0, 5, works);
  Check(c.restarted && starts == 2, "with periodic restarts too");
  c = FfCheckStopped(0, 0, fails);
  Check(c.stopped && !c.restarted && c.failed && starts == 3, "a restart that fails");
  FfNoteCheck(chk, 5, c);
  Check(FfLogThisCount(1) && !FfLogThisCount(2) && FfLogThisCount(10) && !FfLogThisCount(11) &&
            FfLogThisCount(100) && FfLogThisCount(1000000000),
        "warnings the 1st, 10th, 100th... time");
  tc = 10.0;
  const std::string d = chk.Summary(cs, 1u << 4, 1u << 4, 1.0f, tc);
  std::printf("%s\n", d.c_str());
  Check(Has(d, "block 5 constant: game 1 updates, gain 255, force 10, playing; wheel 0 updates, "
               "1 restart, 1 failure, running (the wheel says it is not playing)"),
        "the summary counts the restart and the failure");

  // The vibration (#467): the game's rumbles and the updates the wheel's vibration got, a second,
  // with the highest level; nothing about it while the wheel got none.
  tc = 20.0;
  for (int frame = 0; frame < 2400; frame++) {  // 10 s at 240 fps
    chk.Defined(Block(5, kFfConstant, 255, 10), 10.0 + frame / 240.0);
    chk.Rumbled();
  }
  for (int update = 0; update < 300; update++) {  // paced: 30 a second
    chk.Vibrated(update == 7 ? 0.25f : 0.1f);
  }
  const std::string v = chk.Summary(cs, 1u << 4, 0, 1.0f, tc);
  std::printf("%s\n", v.c_str());
  Check(Has(v, "device gain 255, 2400 rumbles, 300 vibration updates (30 a second, up to 25%), "
               "forces at 100%"),
        "the vibration a second");
  tc = 30.0;
  Check(!Has(chk.Summary(cs, 1u << 4, 0, 1.0f, tc), "vibration"), "a new window: no vibration");

  std::printf("wheel_ff_log_test: ok\n");
  return 0;
}
