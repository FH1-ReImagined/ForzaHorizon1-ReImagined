// The H-pattern shifter's gear request (SDK patch 0132, src/input/wheel/wheel_gear_request.h,
// #303): a gear is asked of the game when the lever moves, not whenever the game sets the gear
// itself; wheel_shifter_follow_slot keeps the old behaviour. And (ReIGlue 0404, #468) a lever
// crossing neutral between two gates goes from one gear to the other, so the game, whose neutral
// is gear 9, sees an upshift as one. Build against the patched SDK source
// (tools/build_rexglue_sdk.sh --source-only), from the repository root:
//   SDK=~/rexglue-sdk-src-forza/sdk; g++ -std=c++17 -Wall -Wextra -Werror -I $SDK/include
//       -I $SDK/src/input/wheel forza-recomp/tests/wheel_gear_request_test.cpp -o wheel-gear-request-test
//   ./wheel-gear-request-test
#include <wheel_gear_request.h>

#include <cstdio>
#include <cstdlib>
#include <vector>

using namespace rex::input::wheel;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}

// What the game reads for `frames` frames 1/30 s apart with the engaged gear `engaged`, starting at
// *t (moved on): the requests it sees, -100 when they differ from frame to frame.
int Frames(GearRequest& r, int engaged, int frames, double* t, bool follow = false) {
  int seen = -1000;
  for (int i = 0; i < frames; i++) {
    const int g = GearToReport(r, engaged, *t, true, follow);
    seen = seen == -1000 || seen == g ? g : -100;
    *t += 1000.0 / 30.0;
  }
  return seen;
}

// The game's gear request byte (sub_824B7360) for a thumb_rx value: 10 is no request.
int GameRequest(int thumb_rx) {
  if (thumb_rx == -1) return 0;
  if (thumb_rx == 0) return 9;
  if (thumb_rx < 9) return thumb_rx;
  return 10;
}
}  // namespace

int main() {
  double t = 1000.0;
  {
    // The lever in 3rd when the game starts: not a movement, no request at any start.
    GearRequest r;
    Check(Frames(r, 3, 120, &t) == kGearNoRequest, "a lever left in a slot asks for nothing");
    Check(GameRequest(GearToReport(r, 3, t, true, false)) == 10, "the game sees no request");
    // The lever moves to 4: asked for at least kGearRequestMs over kGearRequestReads reads...
    Check(GearToReport(r, 4, t, true, false) == 4, "a lever move asks for its gear");
    t += 1000.0 / 30.0;
    Check(Frames(r, 4, 6, &t) == 4, "held while the game reads it");
    // ... then nothing: the paddles' gear stays.
    Check(Frames(r, 4, 3, &t) == -100, "the request ends");
    Check(Frames(r, 4, 300, &t) == kGearNoRequest, "and stays ended while the lever stays");
    // Out to neutral (between two slots): neutral is asked for, then the next gear.
    Check(Frames(r, kGearNeutral, 2, &t) == kGearNeutral, "out of the slot: neutral");
    Check(GameRequest(kGearNeutral) == 9, "the game's neutral");
    Check(Frames(r, 5, 1, &t) == 5, "into the next slot: its gear");
    Check(Frames(r, 5, 30, &t) == -100, "asked for a moment");
    Check(GearToReport(r, 5, t, true, false) == kGearNoRequest, "then nothing");
    // Reverse.
    Check(GearToReport(r, kGearReverse, t, true, false) == kGearReverse &&
              GameRequest(kGearReverse) == 0,
          "reverse");
  }
  {
    // A slow game: the request lasts until it has read enough states, however long that takes.
    GearRequest r;
    GearToReport(r, 1, t, true, false);
    Check(GearToReport(r, 2, t, true, false) == 2, "moved");
    t += 2000.0;  // a loading hitch: no reads for 2 s
    for (int i = 2; i < kGearRequestReads; i++) {
      Check(GearToReport(r, 2, t, true, false) == 2, "still asked for until the game has read it");
    }
    Check(GearToReport(r, 2, t, true, false) == 2, "the last read that ends it still sees it");
    Check(GearToReport(r, 2, t, true, false) == kGearNoRequest, "ended");
    // Keystroke polls do not count as reads.
    Check(GearToReport(r, 3, t, true, false) == 3, "moved again");
    t += 1000.0;
    for (int i = 0; i < 20; i++) {
      Check(GearToReport(r, 3, t, false, false) == 3, "keystroke polls do not end it");
    }
  }
  {
    // A fast game (many reads per frame): the request still lasts kGearRequestMs.
    GearRequest r;
    GearToReport(r, 0, t, true, false);
    const double start = t;
    Check(GearToReport(r, 1, t, true, false) == 1, "moved into 1st");
    while (t - start < kGearRequestMs - 1.0) {
      Check(GearToReport(r, 1, t, true, false) == 1, "held for its time");
      t += 1.0;
    }
    t += 2.0;
    GearToReport(r, 1, t, true, false);
    Check(GearToReport(r, 1, t, true, false) == kGearNoRequest, "then ended");
  }
  {
    // wheel_shifter_follow_slot: the engaged gear on every read, as before #303.
    GearRequest r;
    Check(Frames(r, 3, 100, &t, true) == 3, "follow the slot: 3rd all the time");
    Check(Frames(r, kGearNeutral, 10, &t, true) == kGearNeutral, "and neutral");
    // Switching to the edge mode starts from where the lever is.
    Check(Frames(r, kGearNeutral, 10, &t) == kGearNoRequest, "edge mode after it: no request");
  }
  {
    // With the clutch needed (EngageGear): a lever moved without it engages neutral, the clutch
    // then lets the gear in; each is a request.
    GearRequest r;
    int engaged = EngageGear(2, kGearNeutral, true, 1.0f, 0.5f);
    Check(GearToReport(r, engaged, t, true, false) == kGearNoRequest, "first read");
    engaged = EngageGear(3, engaged, true, 0.0f, 0.5f);
    Check(engaged == kGearNeutral && GearToReport(r, engaged, t, true, false) == kGearNeutral,
          "into 3rd without the clutch: neutral asked for");
    t += 500.0;
    Frames(r, engaged, 5, &t);
    engaged = EngageGear(3, engaged, true, 0.8f, 0.5f);
    Check(engaged == 3 && GearToReport(r, engaged, t, true, false) == 3, "clutch down: 3rd");
  }
  {
    // #468: the game numbers neutral 9 and takes a higher number for an upshift, so 2 > N > 3 was
    // a downshift for it (N > 3) and its paddle animation showed one.
    Check(GameUpshift(2, 3) && !GameUpshift(3, 2), "2 > 3 up, 3 > 2 down");
    Check(!GameUpshift(kGearNeutral, 3) && GameUpshift(2, kGearNeutral), "from neutral: down");
    Check(GameUpshift(kGearReverse, 1) && !GameUpshift(1, kGearReverse), "R > 1 up");
    // The lever as the state polls (4 a frame at 60 fps) read it, 2 > neutral for `ms` > 3: the
    // gears the game is given, in order (repeats dropped).
    auto shift = [](int from, int to, double ms, double hold) {
      LeverHold h;
      double now = 1000.0;
      std::vector<int> seen;
      auto poll = [&](int lever, double until) {
        for (; now < until; now += 1000.0 / 240.0) {
          const int g = LeverThroughNeutral(h, lever, now, hold);
          if (seen.empty() || seen.back() != g) {
            seen.push_back(g);
          }
        }
      };
      poll(from, 1500.0);
      poll(kGearNeutral, 1500.0 + ms);
      poll(to, 3000.0 + ms);
      return seen;
    };
    using V = std::vector<int>;
    Check(shift(2, 3, 150.0, kNeutralTransitMs) == V{2, 3}, "2 > N (150 ms) > 3: 2 > 3, up");
    Check(shift(2, 3, 390.0, kNeutralTransitMs) == V{2, 3}, "2 > N (390 ms) > 3: 2 > 3");
    Check(shift(4, 3, 150.0, kNeutralTransitMs) == V{4, 3}, "a downshift: 4 > 3, down");
    Check(shift(kGearReverse, 1, 200.0, kNeutralTransitMs) == V{kGearReverse, 1}, "R > 1");
    Check(shift(2, 2, 200.0, kNeutralTransitMs) == V{2}, "out and back: 2nd all along");
    Check(shift(2, 3, 600.0, kNeutralTransitMs) == V{2, kGearNeutral, 3},
          "neutral held longer: neutral then");
    Check(shift(2, 3, 150.0, 0.0) == V{2, kGearNeutral, 3}, "0: neutral at once, as before");
    const V before = shift(2, 3, 150.0, 0.0), now = shift(2, 3, 150.0, kNeutralTransitMs);
    Check(!GameUpshift(before[1], before[2]) && GameUpshift(now[0], now[1]),
          "before, the game's last change was a downshift; now an upshift");
    // A lever left in neutral from the start is neutral; a gear from there is a change from it.
    LeverHold h;
    Check(LeverThroughNeutral(h, kGearNeutral, 0.0, kNeutralTransitMs) == kGearNeutral &&
              LeverThroughNeutral(h, 1, 10.0, kNeutralTransitMs) == 1,
          "from neutral at the start");
  }
  std::puts("wheel gear request: ok");
  return 0;
}
