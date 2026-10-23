// Forza Data Out: FH4/5/6 (324 bytes), optional FM7 Dash (311 bytes).
// Field offsets follow the published FH6 specification; no compiler struct packing.
#pragma once
#include "car_physics.h"
#include <cstddef>

namespace forza::data_out {
struct Sample {
  bool driving = false;
  telemetry::CarPhysics car;
  std::array<float, 3> position{}, velocity{}, acceleration{}, angular_velocity{}, angles{};
  std::array<float, 4> normalized_suspension{}, surface_rumble{}, temperature{};
  std::array<uint32_t, 4> rumble_strip{}, puddle{};
  int32_t ordinal = 0, car_class = 0, pi = 0, drivetrain = 0, cylinders = 0;
  uint32_t group = 0;
  float smash_velocity = 0, smash_mass = 0, power = 0, torque = 0, boost = 0, fuel = 0, distance = 0;
  float best_lap = 0, last_lap = 0, current_lap = 0, race_time = 0;
  uint16_t laps = 0;
  uint8_t race_position = 0;
  int8_t driving_line = 0, ai_brake = 0;
};

inline std::array<uint8_t, 324> Encode(const Sample& s, uint32_t timestamp, bool horizon = true) {
  std::array<uint8_t, 324> p{};
  auto word = [&](size_t offset, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) p[offset + i] = uint8_t(value >> (8 * i));
  };
  auto number = [&](size_t offset, float value) {
    word(offset, std::bit_cast<uint32_t>(std::isfinite(value) ? value : 0.0f));
  };
  auto vector = [&](size_t offset, const auto& values) {
    for (size_t i = 0; i < values.size(); ++i) number(offset + 4 * i, values[i]);
  };
  auto unit = [](float f) -> uint8_t {
    return std::isfinite(f) ? uint8_t(std::lround(std::clamp(f, 0.0f, 1.0f) * 255)) : 0;
  };
  word(4, timestamp);
  if (!s.driving || !s.car.valid) return p;
  word(0, 1); number(8, s.car.max_rpm); number(12, s.car.idle); number(16, s.car.rpm);
  vector(20, s.acceleration); vector(32, s.velocity);
  vector(44, s.angular_velocity); vector(56, s.angles);
  vector(68, s.normalized_suspension);
  const float direction = s.car.gear == 0 ? -1.0f : 1.0f;
  for (unsigned i = 0; i < 4; ++i) {
    number(84 + 4 * i, direction * s.car.slip[i]);
    number(100 + 4 * i, s.car.wheel_speed[i]);
    word(116 + 4 * i, s.rumble_strip[i]); word(132 + 4 * i, s.puddle[i]);
    number(148 + 4 * i, s.surface_rumble[i]); number(164 + 4 * i, s.car.slip_angle[i]);
    number(180 + 4 * i, std::hypot(s.car.slip[i], s.car.slip_angle[i]));
    number(196 + 4 * i, s.car.suspension[i]);
  }
  word(212, uint32_t(s.ordinal)); word(216, uint32_t(s.car_class)); word(220, uint32_t(s.pi));
  word(224, uint32_t(s.drivetrain)); word(228, uint32_t(s.cylinders));
  if (horizon) {
    word(232, s.group); number(236, s.smash_velocity); number(240, s.smash_mass);
  }
  const size_t d = horizon ? 244 : 232;
  vector(d, s.position); number(d + 12, std::abs(s.car.speed));
  number(d + 16, s.power); number(d + 20, s.torque); vector(d + 24, s.temperature);
  number(d + 40, s.boost); number(d + 44, s.fuel); number(d + 48, s.distance);
  number(d + 52, s.best_lap); number(d + 56, s.last_lap);
  number(d + 60, s.current_lap); number(d + 64, s.race_time);
  p[d + 68] = uint8_t(s.laps); p[d + 69] = uint8_t(s.laps >> 8);
  p[d + 70] = s.race_position;
  p[d + 71] = unit(s.car.controls[0]); p[d + 72] = unit(s.car.controls[1]);
  p[d + 73] = unit(s.car.controls[3]); p[d + 74] = unit(s.car.controls[4]);
  p[d + 75] = s.car.gear == 9 ? 11 : s.car.gear;
  const float steer = std::isfinite(s.car.controls[2]) ? s.car.controls[2] : 0.0f;
  p[d + 76] = uint8_t(int8_t(std::lround(std::clamp(steer, -1.0f, 1.0f) * 127)));
  p[d + 77] = uint8_t(s.driving_line); p[d + 78] = uint8_t(s.ai_brake);
  return p;
}
}  // namespace forza::data_out
