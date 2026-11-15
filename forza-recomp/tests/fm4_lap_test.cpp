// platform/fm4_lap.h (#648): the lap line, laps and sectors, and the laps that do not count.
//   g++ -std=c++20 -O2 -Wall -Wextra -Werror -I forza-recomp/src/platform
//       forza-recomp/tests/fm4_lap_test.cpp -o fm4-lap-test && ./fm4-lap-test
#include "fm4_lap.h"

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

using namespace forza::fm4_lap;

static int failures = 0;
#define CHECK(c)                                                                  \
  do {                                                                            \
    if (!(c)) {                                                                   \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); \
      ++failures;                                                                 \
    }                                                                             \
  } while (0)

// A round circuit of radius 200 m (1257 m), 600 points from (200, 5, 0), anticlockwise.
static std::string Circle() {
  std::string t = "name Test Ring\nlength 1250\nsplits 0.25 0.5 0.75\npoints 600\n";
  for (int i = 0; i < 600; ++i) {
    const double a = 2 * M_PI * i / 600;
    char row[64];
    std::snprintf(row, sizeof row, "%.3f 5 %.3f\n", 200 * std::cos(a), 200 * std::sin(a));
    t += row;
  }
  return t;
}

static void At(double angle, float pos[3], float off = 0) {
  pos[0] = float((200 + off) * std::cos(angle));
  pos[1] = 5.5f;
  pos[2] = float((200 + off) * std::sin(angle));
}

// Drives round the circle at `speed` m/s, 30 updates a second, and keeps the events.
struct Drive {
  explicit Drive(Timer& t) : timer(t) {}
  Timer& timer;
  double t = 0, angle = 0;
  std::vector<Event> events;
  void Go(double seconds, double speed, double dir = 1) {
    for (double end = t + seconds; t < end; t += 1.0 / 30) {
      angle += dir * speed / 30 / 200;
      float pos[3];
      At(angle, pos);
      Event e = timer.Update(t, pos);
      if (e.kind != Kind::kNone) events.push_back(e);
    }
  }
  int Count(Kind k) const {
    int n = 0;
    for (const auto& e : events) n += e.kind == k;
    return n;
  }
};

int main() {
  Line line;
  CHECK(Parse(Circle(), line));
  CHECK(line.name == "Test Ring");
  CHECK(line.splits.size() == 3);
  CHECK(std::fabs(line.total - 2 * M_PI * 200) < 1);
  CHECK(Clock(83.456) == "1:23.46");
  CHECK(Clock(3725.0) == "1:02:05.00");
  CHECK(Clock(-1) == "-:--.--");
  Line bad;
  CHECK(!Parse("name x\npoints 3\n0 0 0\n1 1 1\n2 2 2\n", bad));

  {  // from behind the line: the first crossing starts lap 1, the next ones are timed laps
    Timer timer(line);
    Drive d(timer);
    d.angle = -0.2;               // 40 m behind the start line
    d.Go(1.5, 40);               // over the line
    CHECK(d.Count(Kind::kStarted) == 1);
    CHECK(timer.timing() && timer.lap() == 1);
    const double lap = line.total / 40;  // 31.4 s
    d.Go(2 * lap + 1, 40);
    CHECK(d.Count(Kind::kSplit) == 6);
    CHECK(d.Count(Kind::kLap) == 2);
    for (const auto& e : d.events) {
      if (e.kind == Kind::kLap) CHECK(std::fabs(e.time - lap) < 0.1);
      if (e.kind == Kind::kSplit && e.index == 1) CHECK(std::fabs(e.time - lap / 2) < 0.1);
    }
    CHECK(timer.lap() == 3);
    CHECK(std::fabs(timer.best() - lap) < 0.1);
    CHECK(timer.last_splits().size() == 3);
    // the ghost is the best lap: the same pace is level with it, half the pace falls behind
    CHECK(timer.ghost().size() > 200);
    CHECK(std::fabs(timer.Delta(d.t)) < 0.15);
    // slower now: the best stays
    d.events.clear();
    d.Go(lap / 2, 20);
    CHECK(timer.Delta(d.t) > lap / 4 - 1.5 && timer.Delta(d.t) < lap / 4 + 1.5);  // a quarter lap at half pace
    d.Go(lap * 3 / 2 + 1, 20);
    CHECK(d.Count(Kind::kLap) == 1);
    CHECK(!d.events.empty());
    for (const auto& e : d.events)  // (its first second still at 40 m/s)
      if (e.kind == Kind::kLap) CHECK(!e.best && e.time > 1.9 * lap && e.time < 2 * lap);
    CHECK(std::fabs(timer.best() - lap) < 0.1);
  }
  {  // the ghost file, and a ghost from an earlier session
    Trace trace{{2, 0, 1, 2, 3}, {100, 10, 4, 5, 6}, {200, 25, 7, 8, 9}};
    CHECK(TimeAt(trace, 150) == 17.5);
    CHECK(TimeAt(trace, 1) < 0 && TimeAt(trace, 250) < 0);
    double best = 0;
    Trace back;
    CHECK(ReadTrace(WriteTrace(25.25, trace), best, back));
    CHECK(best == 25.25 && back.size() == 3 && back[2].z == 9);
    CHECK(!ReadTrace("best 0\n", best, back));
    Timer timer(line);
    timer.SetGhost(30.0, trace);
    CHECK(timer.best() == 30.0 && timer.ghost().size() == 3);
    timer.SetGhost(31.0, Trace{{0, 0, 0, 0, 0}, {5, 1, 0, 0, 0}});  // slower: kept the first
    CHECK(timer.best() == 30.0 && timer.ghost().size() == 3);
    CHECK(std::isnan(timer.Delta(1)));  // not timing
  }
  {  // backwards over the line: not counted
    Timer timer(line);
    Drive d(timer);
    d.angle = -0.2;
    d.Go(1.5, 40);
    d.Go(3, 40, -1);
    CHECK(d.Count(Kind::kEnded) == 1);
    CHECK(!timer.timing());
  }
  {  // a jump along the track (a teleport, a shortcut through the infield)
    Timer timer(line);
    Drive d(timer);
    d.angle = -0.2;
    d.Go(1.5, 40);
    float pos[3];
    At(d.angle + 1.5, pos);  // 300 m on
    CHECK(timer.Update(d.t, pos).kind == Kind::kNone);  // beyond the part searched: off the line
    Event e = timer.Update(d.t + 0.03, pos);              // the whole line: found, 300 m on
    CHECK(e.kind == Kind::kEnded);
    CHECK(!timer.timing());
  }
  {  // a shortcut: off the line for 240 m along it, then back on: the lap does not count
    Timer timer(line);
    Drive d(timer);
    d.angle = -0.2;
    d.Go(1.5, 40);
    // round to just before the first sector end, then 50 m off the line past it, then back
    d.Go(line.total / 4 / 40 - 2, 40);
    for (int i = 0; i < 360; ++i) {
      d.angle += 40.0 / 30 / 200 / 2;
      float pos[3];
      At(d.angle, pos, 50);
      timer.Update(d.t += 1.0 / 30, pos);
    }
    d.Go(line.total / 40, 40);
    CHECK(d.Count(Kind::kEnded) == 1);
    CHECK(d.Count(Kind::kLap) == 0);
    CHECK(d.Count(Kind::kStarted) == 2);  // the crossing starts a new lap instead
  }
  {  // a rewind ends the lap; an earlier best is kept
    Timer timer(line);
    timer.SetBest(99.5);
    Drive d(timer);
    d.angle = -0.2;
    d.Go(1.5, 40);
    CHECK(timer.Invalidate("rewind used").kind == Kind::kEnded);
    CHECK(timer.Invalidate("rewind used").kind == Kind::kNone);
    CHECK(timer.best() == 99.5);
  }
  {  // the lap guide: the sat-nav destination stays ahead of the car, the shorter way round
    Line big;  // a ring of 5 km, a point every 5 m: 1500 m ahead, set again every 300 m
    {
      std::string t = "name Big Ring\nlength 5000\nsplits 0.5\npoints 1000\n";
      for (int i = 0; i < 1000; ++i) {
        const double a = 2 * M_PI * i / 1000;
        char row[64];
        std::snprintf(row, sizeof row, "%.3f 0 %.3f\n", 795.775 * std::cos(a), 795.775 * std::sin(a));
        t += row;
      }
      CHECK(Parse(t, big));
    }
    Guide guide(big);
    CHECK(std::fabs(guide.ahead() - 1500) < 0.01f && std::fabs(guide.every() - 300) < 0.01f);
    CHECK(!guide.set());
    int at = guide.Next(100);  // nothing set yet: the point 1500 m on
    CHECK(at >= 0 && std::fabs(big.along[size_t(at)] - 1600) < 6);
    CHECK(guide.Next(100) == at);  // asked again until the game has taken it
    guide.Set(at);
    CHECK(guide.set());
    CHECK(guide.Next(100) == -1 && guide.Next(250) == -1 && guide.Next(395) == -1);  // it still leads
    int next = guide.Next(410);  // 300 m nearer: moved on
    CHECK(next >= 0 && std::fabs(big.along[size_t(next)] - 1910) < 6);
    guide.Set(next);
    // a whole lap: set about every 300 m, always 1200 to 1800 m ahead, never behind the car
    int sets = 0;
    for (float s = 410; s < 410 + big.total; s += 2) {
      const float place = std::fmod(s, big.total);
      const int i = guide.Next(place);
      if (i >= 0) { guide.Set(i); ++sets; }
      CHECK(guide.Next(place) == -1);
    }
    CHECK(sets >= 15 && sets <= 18);
    // over the start line: the destination is on the next lap's first metres
    guide.Clear();
    at = guide.Next(4000);
    CHECK(at >= 0 && std::fabs(big.along[size_t(at)] - 500) < 6);
    guide.Set(at);
    CHECK(guide.Next(4200) == -1);
    // the car is suddenly elsewhere (a rewind, a reset, driven back): the route leads on from there
    next = guide.Next(2000);
    CHECK(next >= 0 && std::fabs(big.along[size_t(next)] - 3500) < 6);
    guide.Set(next);
    next = guide.Next(1600);  // 400 m back
    CHECK(next >= 0 && std::fabs(big.along[size_t(next)] - 3100) < 6);
    // a short lap: 0.4 of it ahead, so the way on stays the shorter one
    Guide small(line);  // the 1257 m ring
    CHECK(std::fabs(small.ahead() - 0.4f * line.total) < 0.01f && small.ahead() < line.total / 2);
    CHECK(std::fabs(small.every() - 0.2f * line.total) < 0.01f);
    at = small.Next(1200);
    CHECK(at >= 0 && std::fabs(line.along[size_t(at)] - std::fmod(1200 + small.ahead(), line.total)) < 4);
    small.Set(at);
    CHECK(small.Next(1250) == -1 && small.Next(40) == -1);
    CHECK(small.Next(260) >= 0);
    // no line, no guide; a point that is not one changes nothing
    Line none;
    Guide off(none);
    CHECK(off.Next(10) == -1);
    off.Set(3);
    CHECK(!off.set());
    CHECK(guide.Next(-1) == -1 && guide.Next(NAN) == -1);
    CHECK(!guide.crossing(0) && !guide.crossing(500) && !guide.crossing(5000));  // a plain ring has none
  }
  {  // the lap guide where the lap passes over itself: the destination is never at the crossing
    Line eight;  // a figure of eight of about 5.5 km, the second pass 6 m over the first
    {
      std::string t = "name Eight\nlength 5490\nsplits 0.5\npoints 2800\n";
      for (int i = 0; i < 2800; ++i) {
        const double a = 2 * M_PI * (i + 0.5) / 2800;
        char row[80];
        std::snprintf(row, sizeof row, "%.3f %.3f %.3f\n", 900 * std::sin(a), 3 * (1 - std::cos(a)),
                      900 * std::sin(a) * std::cos(a));
        t += row;
      }
      CHECK(Parse(t, eight));
    }
    const Guide guide(eight);
    const auto from_crossing = [&](int i) {
      return std::hypot(eight.points[size_t(i)][0], eight.points[size_t(i)][2]);
    };
    int marked = 0;
    for (int i = 0; i < 2800; ++i) {
      if (from_crossing(i) < 10) CHECK(guide.crossing(size_t(i)));   // both passes, the low and the high
      if (from_crossing(i) > 60) CHECK(!guide.crossing(size_t(i)));
      marked += guide.crossing(size_t(i));
    }
    CHECK(marked > 20 && marked < 120);
    // the high pass is half a lap round: a car 1500 m before it would get it as its destination
    const int high = 1400;
    CHECK(from_crossing(high) < 3 && eight.points[size_t(high)][1] > 5.9f);
    int moved = 0;
    for (float back = -40; back <= 40; back += 1) {
      const int at = guide.Next(eight.along[size_t(high)] - guide.ahead() + back);
      CHECK(at >= 0 && !guide.crossing(size_t(at)));
      const float on = eight.along[size_t(at)] - (eight.along[size_t(high)] + back);
      CHECK(on >= -0.01f && on < 70);  // the point asked for, or the first clear one after it
      moved += on > 4;
    }
    CHECK(moved > 20);
    // round the whole lap: set as often as on a ring, never at the crossing
    Guide lap(eight);
    int sets = 0;
    for (float place = 0; place < eight.total; place += 2) {
      const int i = lap.Next(place);
      if (i < 0) continue;
      CHECK(!lap.crossing(size_t(i)));
      lap.Set(i);
      ++sets;
      CHECK(lap.Next(place) == -1);
    }
    CHECK(sets >= 17 && sets <= 20);
  }
  if (failures) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return 1;
  }
  std::puts("fm4_lap_test: ok");
  return 0;
}
