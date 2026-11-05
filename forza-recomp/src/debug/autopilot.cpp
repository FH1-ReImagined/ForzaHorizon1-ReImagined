// forza - a dev autopilot for test laps on converted FM4 circuits (#648).
//
// FORZA_AUTOPILOT=<line file>[,<speed scale>] drives player 1's car along a line (a point per
// row, "x y z speed_m_s": tools/fm4_line.py) once the car is near it: the left stick steers
// at a point ahead on the line (pure pursuit; the look-ahead grows with speed), the triggers
// hold the line's speed. The log gets the car's place every second ("AUTOPILOT:" lines).
// Off unless the variable is set.

#include "../platform/telemetry.h"

#include <rex/logging.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

namespace forza::autopilot {
namespace {

struct Point {
  float x, y, z, v;
};

struct Line {
  std::vector<Point> points;
  float scale = 1.0f;
  bool on = false;
};

const Line& Loaded() {
  static const Line line = [] {
    Line l;
    const char* p = std::getenv("FORZA_AUTOPILOT");
    if (!p) return l;
    std::string spec = p;
    const auto comma = spec.find(',');
    if (comma != std::string::npos) {
      l.scale = std::strtof(spec.c_str() + comma + 1, nullptr);
      spec.resize(comma);
    }
    if (std::FILE* f = std::fopen(spec.c_str(), "r")) {
      Point q;
      while (std::fscanf(f, "%f %f %f %f", &q.x, &q.y, &q.z, &q.v) == 4) l.points.push_back(q);
      std::fclose(f);
    }
    l.on = l.points.size() > 10;
    REXLOG_INFO("AUTOPILOT: {} points from {}, speed x{:.2f}", l.points.size(), spec, l.scale);
    return l;
  }();
  return line;
}

double Now() {
  return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

}  // namespace

bool Drive(uint8_t* base, int16_t& steer, uint8_t& brake, uint8_t& throttle) {
  const Line& line = Loaded();
  if (!line.on) return false;
  float pos[3], fwd[3];
  if (!forza::telemetry::LocalCarPose(base, pos, fwd)) return false;
  static size_t at = 0;
  static bool found = false;
  static double last_t = 0, last_log = 0;
  static float last_x = 0, last_z = 0, speed = 0, vx = 0, vz = 0;
  const size_t n = line.points.size();
  auto d2 = [&](size_t i) {
    const Point& q = line.points[i % n];
    return (q.x - pos[0]) * (q.x - pos[0]) + (q.z - pos[2]) * (q.z - pos[2]) +
           (q.y - pos[1]) * (q.y - pos[1]);
  };
  // FORZA_AUTOPILOT_DELAY=s: leave the pad alone for s seconds after the car is first seen.
  static double first_seen = 0;
  if (first_seen == 0) first_seen = Now();
  static const double delay = std::getenv("FORZA_AUTOPILOT_DELAY")
                                  ? std::strtod(std::getenv("FORZA_AUTOPILOT_DELAY"), nullptr)
                                  : 0.0;
  if (Now() - first_seen < delay) return false;
  if (!found) {  // the nearest point anywhere, then only ahead
    size_t best = 0;
    for (size_t i = 1; i < n; ++i)
      if (d2(i) < d2(best)) best = i;
    if (d2(best) > 30.0f * 30.0f) return false;
    at = best;
    found = true;
  }
  for (size_t k = 1; k < 40; ++k)
    if (d2(at + k) < d2(at)) at = (at + k) % n;
  const double t = Now();
  if (last_t > 0 && t - last_t > 0.02) {
    const float dt = float(t - last_t);
    const float v = std::hypot(pos[0] - last_x, pos[2] - last_z) / dt;
    vx = (pos[0] - last_x) / dt;
    vz = (pos[2] - last_z) / dt;
    if (v < 120) speed += (v - speed) * std::min(1.0f, dt * 4);
    last_t = t;
    last_x = pos[0];
    last_z = pos[2];
  } else if (last_t == 0) {
    last_t = t;
    last_x = pos[0];
    last_z = pos[2];
  }
  // Look ahead 6 m plus 0.5 s.
  const float ahead = 6.0f + 0.5f * speed;
  size_t target = at;
  float walked = 0;
  while (walked < ahead) {
    const Point& a = line.points[target % n];
    const Point& b = line.points[(target + 1) % n];
    walked += std::hypot(b.x - a.x, b.z - a.z);
    target = (target + 1) % n;
  }
  const Point& g = line.points[target];
  const float tx = g.x - pos[0], tz = g.z - pos[2];
  const float fn = std::hypot(fwd[0], fwd[2]), tn = std::hypot(tx, tz);
  if (fn < 0.1f || tn < 0.1f) return false;
  // Right of the car is (fz, -fx) (the orientation rows of the game's teleports).
  const float side = (tx * fwd[2] - tz * fwd[0]) / (fn * tn);
  const float along = (tx * fwd[0] + tz * fwd[2]) / (fn * tn);
  float angle = std::atan2(side, along);
  static const float gain = std::getenv("FORZA_AUTOPILOT_GAIN")
                                ? std::strtof(std::getenv("FORZA_AUTOPILOT_GAIN"), nullptr)
                                : 1.8f;
  // The stick moves at most 4 full widths a second (a full lock at speed spins the car).
  static float s = 0;
  static double last_steer = 0;
  const float want_s = std::clamp(angle * gain, -1.0f, 1.0f);
  const float step = last_steer > 0 ? float(std::min(0.1, t - last_steer)) * 4.0f : 0.05f;
  last_steer = t;
  s += std::clamp(want_s - s, -step, step);
  steer = int16_t(s * 32767);
  // The slowest speed in the next 30 m of the line.
  float want = 1e9f;
  walked = 0;
  for (size_t i = at; walked < 30.0f; i = (i + 1) % n) {
    want = std::min(want, line.points[i].v);
    const Point& a = line.points[i];
    const Point& b = line.points[(i + 1) % n];
    walked += std::hypot(b.x - a.x, b.z - a.z);
  }
  want *= line.scale;
  const float err = want - speed;
  // Gently on the throttle (a full one from low speed spins the car), the brake only well
  // over the line's speed and never slow (the brake trigger reverses a stopped car).
  throttle = uint8_t(std::clamp(err * 30.0f + 70.0f, 0.0f, speed < 8.0f ? 150.0f : 220.0f));
  brake = speed > 6.0f ? uint8_t(std::clamp(-err * 40.0f - 40.0f, 0.0f, 200.0f)) : 0;
  if (brake) throttle = 0;
  // Stuck (against a wall, in the gravel) once under way: reverse for 2 s, the wheel the
  // other way.
  static double slow_since = 0, reverse_until = 0;
  static bool moved = false;
  if (speed > 4.0f) moved = true;
  if (moved && speed < 1.0f && want > 3.0f) {
    if (slow_since == 0) slow_since = t;
    if (t - slow_since > 2.0 && t > reverse_until + 1.0) reverse_until = t + 2.0;
  } else {
    slow_since = 0;
  }
  if (t < reverse_until) {
    throttle = 0;
    brake = 255;
    steer = int16_t(-s * 32767);
  }
  // FORZA_AUTOPILOT_TRACE=<file>: every 0.1 s, a row of time, place, speeds, inputs and
  // the four wheels (contact, suspension, wheel speed, slip ratio, slip angle).
  static std::FILE* trace = [] {
    const char* p = std::getenv("FORZA_AUTOPILOT_TRACE");
    return p ? std::fopen(p, "w") : nullptr;
  }();
  static double last_trace = 0;
  if (trace && t - last_trace >= 0.1) {
    last_trace = t;
    forza::telemetry::CarPhysicsView ph{};
    const bool have = forza::telemetry::LocalCarPhysics(base, ph);
    std::fprintf(trace, "%.2f %d %.2f %.2f %.2f %.2f %.2f %.3f %.3f %.3f %.2f %.2f %d", t, int(at),
                 pos[0], pos[1], pos[2], speed * 3.6f, have ? ph.speed * 3.6f : -1.0f, s,
                 throttle / 255.0f, brake / 255.0f, std::atan2(fwd[0], fwd[2]) * 57.3f,
                 std::atan2(vx, vz) * 57.3f, have ? ph.gear : -1);
    if (have) {
      std::fprintf(trace, " ctl %.2f %.2f %.2f", ph.controls[0], ph.controls[1], ph.controls[2]);
      for (int w = 0; w < 4; ++w)
        std::fprintf(trace, " w%d %d %.3f %.1f %.3f %.3f", w, int(ph.contact[w]), ph.suspension[w],
                     ph.wheel_speed[w], ph.slip[w], ph.slip_angle[w]);
    }
    std::fprintf(trace, "\n");
    std::fflush(trace);
  }
  if (t - last_log > 1.0) {
    last_log = t;
    REXLOG_INFO("AUTOPILOT: at {} of {} ({:.1f},{:.1f},{:.1f}) {:.0f} km/h want {:.0f}, off {:.1f} m, "
                "steer {:.2f}, slip {:.0f} deg, to target {:.0f} deg", at, n, pos[0], pos[1], pos[2],
                speed * 3.6f, want * 3.6f, std::sqrt(d2(at)), s,
                std::atan2(vx * fwd[2] - vz * fwd[0], vx * fwd[0] + vz * fwd[2]) * 57.3f,
                angle * 57.3f);
  }
  return true;
}

}  // namespace forza::autopilot
