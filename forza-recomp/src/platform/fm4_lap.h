// forza - lap timing on a converted FM4 circuit (#648): the lap's line, the sectors and the
// timer, with no game calls (src/hooks/fm4_lap_timer.cpp feeds it; tests/fm4_lap_test.cpp).
//
// The line is FM4's AI line (tools/fm4_timing.py writes it as lap.txt): its point 0 is on the
// start/finish line and the points run round the lap. The car's place on the lap is the
// distance along the line of its nearest point (searched near the last one; the whole line
// again when the car has left it). A lap starts when the car crosses the line forwards, and it
// counts when the car crosses it again after passing every sector end in order. Crossing it
// backwards, a jump along the line (a teleport) or the game's rewind ends the lap uncounted.
//
// Time attack's ghost: each lap keeps its trace (time and place every few metres along the
// line); the best lap's is the ghost, and Delta() is the time to it at the car's place now.
//
// The lap guide (#877): Guide says where the game's sat-nav destination goes so that the game's
// own route, the green line on the minimap, leads round the lap.
#pragma once

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <string_view>
#include <vector>

namespace forza::fm4_lap {

struct Line {
  std::string name;
  float length = 0;                         // the database's lap length (metres), for the log
  std::vector<float> splits;                // shares of the lap where a sector ends, rising
  std::vector<std::array<float, 3>> points;
  std::vector<float> along;                 // distance along the line at each point
  float total = 0;                          // the line's length round the lap
};

// lap.txt: "name ...", "length m", "splits a b ...", "points n", then n lines "x y z".
inline bool Parse(std::string_view text, Line& line) {
  line = Line{};
  size_t at = 0, want = 0;
  bool in_points = false;
  while (at < text.size()) {
    size_t end = text.find('\n', at);
    if (end == std::string_view::npos) end = text.size();
    std::string row(text.substr(at, end - at));
    at = end + 1;
    while (!row.empty() && (row.back() == '\r' || row.back() == ' ')) row.pop_back();
    if (row.empty()) continue;
    if (in_points) {
      std::array<float, 3> p{};
      if (std::sscanf(row.c_str(), "%f %f %f", &p[0], &p[1], &p[2]) == 3) line.points.push_back(p);
      continue;
    }
    const auto space = row.find(' ');
    const std::string key = row.substr(0, space);
    const std::string value = space == std::string::npos ? "" : row.substr(space + 1);
    if (key == "name") line.name = value;
    else if (key == "length") line.length = std::strtof(value.c_str(), nullptr);
    else if (key == "splits") {
      const char* p = value.c_str();
      char* next = nullptr;
      for (float f = std::strtof(p, &next); next != p; f = std::strtof(p, &next)) {
        if (f > 0 && f < 1 && (line.splits.empty() || f > line.splits.back())) line.splits.push_back(f);
        p = next;
      }
    } else if (key == "points") {
      want = std::strtoul(value.c_str(), nullptr, 10);
      in_points = true;
    }
  }
  if (line.points.size() < 16 || (want && line.points.size() != want)) return false;
  line.along.resize(line.points.size());
  float d = 0;
  for (size_t i = 0; i < line.points.size(); ++i) {
    line.along[i] = d;
    const auto& a = line.points[i];
    const auto& b = line.points[(i + 1) % line.points.size()];
    d += std::sqrt((a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]));
  }
  line.total = d;
  return line.total > 100;
}

// m:ss.cc (or h:mm:ss.cc)
inline std::string Clock(double seconds) {
  if (!(seconds >= 0)) return "-:--.--";
  const long cs = std::lround(seconds * 100);
  char buf[32];
  if (cs >= 360000)
    std::snprintf(buf, sizeof buf, "%ld:%02ld:%02ld.%02ld", cs / 360000, cs / 6000 % 60, cs / 100 % 60, cs % 100);
  else
    std::snprintf(buf, sizeof buf, "%ld:%02ld.%02ld", cs / 6000, cs / 100 % 60, cs % 100);
  return buf;
}

enum class Kind { kNone, kStarted, kSplit, kLap, kEnded };

// A lap's trace: at `along` metres from the start line, `time` seconds into the lap, there.
struct Sample {
  float along, time, x, y, z;
};
using Trace = std::vector<Sample>;

// The ghost's time into its lap at `along` metres (linear between samples); <0 when it has
// no samples there.
inline double TimeAt(const Trace& trace, float along) {
  if (trace.size() < 2 || along < trace.front().along || along > trace.back().along) return -1;
  size_t lo = 0, hi = trace.size() - 1;
  while (hi - lo > 1) {
    const size_t mid = (lo + hi) / 2;
    (trace[mid].along <= along ? lo : hi) = mid;
  }
  const Sample& a = trace[lo];
  const Sample& b = trace[hi];
  const float span = b.along - a.along;
  return span > 0 ? a.time + (b.time - a.time) * (along - a.along) / span : a.time;
}

// ghost file: "best <seconds>", then a line per sample "along time x y z".
inline std::string WriteTrace(double best, const Trace& trace) {
  std::string out;
  char row[96];
  std::snprintf(row, sizeof row, "best %.3f\n", best);
  out += row;
  for (const auto& q : trace) {
    std::snprintf(row, sizeof row, "%.1f %.3f %.2f %.2f %.2f\n", q.along, q.time, q.x, q.y, q.z);
    out += row;
  }
  return out;
}
inline bool ReadTrace(std::string_view text, double& best, Trace& trace) {
  trace.clear();
  best = -1;
  size_t at = 0;
  while (at < text.size()) {
    size_t end = text.find('\n', at);
    if (end == std::string_view::npos) end = text.size();
    const std::string row(text.substr(at, end - at));
    at = end + 1;
    Sample q{};
    if (row.rfind("best ", 0) == 0) best = std::strtod(row.c_str() + 5, nullptr);
    else if (std::sscanf(row.c_str(), "%f %f %f %f %f", &q.along, &q.time, &q.x, &q.y, &q.z) == 5 &&
             (trace.empty() || q.along >= trace.back().along))
      trace.push_back(q);
  }
  return best > 0 && trace.size() >= 2;
}

struct Event {
  Kind kind = Kind::kNone;
  int index = 0;        // kSplit: the sector (0-based); kLap: the lap number
  double time = 0;      // kSplit: the lap time at the sector's end; kLap: the lap's time
  bool best = false;    // kLap: a new best lap
  const char* why = ""; // kEnded: why the lap stopped counting
};

class Timer {
 public:
  static constexpr float kNear = 30.0f;    // metres from the line that still count as on it
  static constexpr float kJump = 150.0f;   // metres along the line in one update: a teleport
  static constexpr size_t kWindow = 120;   // points searched either side of the last one
  static constexpr float kSampleEvery = 5.0f;  // metres along the line between trace samples

  explicit Timer(const Line& line) : line_(line) {}

  // `t`: seconds of driving (it does not run while paused); `pos`: the car. One per frame.
  Event Update(double t, const float pos[3]) {
    const size_t n = line_.points.size();
    if (!n) return {};
    float best_d2 = 1e30f;
    size_t best_i = at_;
    auto look = [&](size_t i) {
      const auto& q = line_.points[i % n];
      const float d2 = (q[0] - pos[0]) * (q[0] - pos[0]) + (q[1] - pos[1]) * (q[1] - pos[1]) +
                       (q[2] - pos[2]) * (q[2] - pos[2]);
      if (d2 < best_d2) { best_d2 = d2; best_i = i % n; }
    };
    if (!found_) {
      for (size_t i = 0; i < n; ++i) look(i);
    } else {
      for (size_t k = 0; k <= 2 * kWindow && k < n; ++k) look(at_ + n - kWindow + k);
    }
    // Off the line (or beyond the searched part of it after a jump): the whole line is
    // searched next time; the lap keeps running.
    if (std::sqrt(best_d2) > kNear) {
      found_ = false;
      return {};
    }
    const float s = line_.along[best_i];
    const bool had = have_;  // a place before, even one from before the car left the line
    found_ = have_ = true;
    at_ = best_i;
    const float prev = s_;
    s_ = s;
    if (!had) return {};
    float ds = s - prev;
    const bool forward_wrap = ds < -line_.total / 2, backward_wrap = ds > line_.total / 2;
    if (forward_wrap) ds += line_.total;
    if (backward_wrap) ds -= line_.total;
    if (std::fabs(ds) > kJump) return End("the car jumped along the track");
    if (backward_wrap) return End("wrong way over the start line");
    if (forward_wrap) {
      Event e;
      if (timing_ && next_split_ == line_.splits.size()) {
        e.kind = Kind::kLap;
        e.index = lap_;
        e.time = t - lap_start_;
        e.best = best_ < 0 || e.time < best_;
        last_ = e.time;
        last_splits_ = splits_;
        if (e.best) {
          best_ = e.time;
          best_splits_ = splits_;
          trace_.push_back({line_.total, float(e.time), pos[0], pos[1], pos[2]});
          ghost_ = trace_;
        }
      } else {
        e.kind = Kind::kStarted;
      }
      timing_ = true;
      lap_ = e.kind == Kind::kLap ? lap_ + 1 : 1;
      lap_start_ = t;
      next_split_ = 0;
      splits_.clear();
      trace_.clear();
      trace_.push_back({s, 0.0f, pos[0], pos[1], pos[2]});
      return e;
    }
    if (timing_ && (trace_.empty() || s - trace_.back().along >= kSampleEvery) && ds > 0)
      trace_.push_back({s, float(t - lap_start_), pos[0], pos[1], pos[2]});
    if (timing_ && next_split_ < line_.splits.size()) {
      const float at = line_.splits[next_split_] * line_.total;
      if (prev < at && s >= at && ds > 0) {
        splits_.push_back(t - lap_start_);
        Event e{Kind::kSplit, int(next_split_), splits_.back()};
        ++next_split_;
        return e;
      }
    }
    return {};
  }

  // The game's rewind, a reload: the lap so far stops counting.
  Event Invalidate(const char* why) { return timing_ ? End(why) : Event{}; }
  // The car is gone (a menu, a load): searched again when it is back, from its last place.
  void Lost() { found_ = false; }

  void SetBest(double best) { if (best > 0 && (best_ < 0 || best < best_)) best_ = best; }
  // The best lap's trace from an earlier session (its time is the best when it is the better).
  void SetGhost(double best, Trace trace) {
    if (best <= 0 || trace.size() < 2 || (best_ > 0 && best > best_ + 0.005)) return;
    best_ = best;
    ghost_ = std::move(trace);
  }
  // Seconds behind (+) or ahead (-) of the ghost at the car's place; NAN without one.
  double Delta(double t) const {
    if (!timing_ || ghost_.empty()) return NAN;
    const double g = TimeAt(ghost_, s_);
    return g < 0 ? NAN : (t - lap_start_) - g;
  }
  const Trace& ghost() const { return ghost_; }
  bool timing() const { return timing_; }
  int lap() const { return lap_; }
  double lap_start() const { return lap_start_; }
  double best() const { return best_; }
  double last() const { return last_; }
  const std::vector<double>& splits() const { return splits_; }
  const std::vector<double>& best_splits() const { return best_splits_; }
  const std::vector<double>& last_splits() const { return last_splits_; }
  float along() const { return s_; }
  bool on_line() const { return found_; }  // the car was on the line at the last update

 private:
  Event End(const char* why) {
    const bool was = timing_;
    timing_ = false;
    next_split_ = 0;
    splits_.clear();
    trace_.clear();
    Event e;
    if (was) { e.kind = Kind::kEnded; e.why = why; }
    return e;
  }

  const Line& line_;
  size_t at_ = 0;
  bool found_ = false, have_ = false;
  float s_ = 0;
  bool timing_ = false;
  int lap_ = 0;
  double lap_start_ = 0, best_ = -1, last_ = -1;
  size_t next_split_ = 0;
  std::vector<double> splits_, best_splits_, last_splits_;
  Trace trace_, ghost_;
};

// The lap guide: the game's sat-nav destination, kept ahead of the car along the line. The
// game plans the shorter way to it over the circuit's road, a ring, so the destination stays
// kAhead metres ahead (0.4 of a lap shorter than that would be more than half way round) and
// is set again when the car has come kEvery metres nearer. It is also set again when the car
// is no longer that far behind it (driven backwards, a rewind, a reset): the route leads on
// from where the car is.
//
// The game makes the destination the road node nearest the place it is given. Where the lap
// passes over or under itself (Suzuka's bridge: the two roads 6 m apart in height, a road's
// nodes 20 m apart) that can be the other road's node, and the route would lead the shorter
// way to it: back the way the car came. So a point with another stretch of the lap within
// kClear metres on the plan is never the destination: the next point clear of it is.
class Guide {
 public:
  static constexpr float kAhead = 1500.0f;
  static constexpr float kEvery = 300.0f;
  static constexpr float kClear = 20.0f;   // metres on the plan to another stretch of the lap
  static constexpr float kApart = 150.0f;  // metres along the lap from which it is another stretch

  explicit Guide(const Line& line) : line_(line), crossing_(line.points.size(), false) {
    const size_t n = line.points.size();
    if (n < 2 || !(line.total > 0) || line.along.size() != n) return;
    // compared with every few metres of the line, not every point: once, when the circuit loads
    const size_t step = std::max<size_t>(1, size_t(0.25f * kClear * float(n) / line.total));
    for (size_t i = 0; i < n; ++i) {
      for (size_t j = 0; j < n && !crossing_[i]; j += step) {
        float apart = std::fabs(line.along[i] - line.along[j]);
        apart = std::min(apart, line.total - apart);
        if (apart < kApart) continue;
        const float dx = line.points[i][0] - line.points[j][0], dz = line.points[i][2] - line.points[j][2];
        crossing_[i] = dx * dx + dz * dz < kClear * kClear;
      }
    }
  }

  float ahead() const { return std::min(kAhead, 0.4f * line_.total); }
  float every() const { return std::min(kEvery, 0.5f * ahead()); }
  // The line's point `index` has another stretch of the lap over, under or right beside it.
  bool crossing(size_t index) const { return index < crossing_.size() && crossing_[index]; }

  // The car is `along` metres round the line: the line's point to make the destination now,
  // or -1 while the one set still leads.
  int Next(float along) const {
    const size_t n = line_.points.size();
    if (n < 2 || !(line_.total > 0) || !(along >= 0)) return -1;
    if (set_) {
      float left = target_ - along;  // metres on to the destination, the way the lap runs
      if (left < 0) left += line_.total;
      if (left > ahead() - every() && left < ahead() + every()) return -1;
    }
    const float want = std::fmod(along + ahead(), line_.total);
    const auto it = std::lower_bound(line_.along.begin(), line_.along.end(), want);
    const size_t first = it == line_.along.end() ? 0 : size_t(it - line_.along.begin());
    // on to the next point clear of a crossing, within half a step (else the point as it is)
    size_t at = first;
    for (float gone = 0; crossing_[at] && gone < 0.5f * every();) {
      const size_t next = (at + 1) % n;
      gone += next ? line_.along[next] - line_.along[at] : line_.total - line_.along[at];
      at = next;
    }
    return int(crossing_[at] ? first : at);
  }
  // The game took the line's point `index` as its destination.
  void Set(int index) {
    if (index < 0 || size_t(index) >= line_.along.size()) return;
    set_ = true;
    target_ = line_.along[size_t(index)];
  }
  // The destination is gone (the guide was turned off, the game dropped it).
  void Clear() { set_ = false; }
  bool set() const { return set_; }

 private:
  const Line& line_;
  std::vector<bool> crossing_;
  bool set_ = false;
  float target_ = 0;
};

}  // namespace forza::fm4_lap
