// Read-only ICarDynamics samples. Layout evidence and limits: docs/telemetry.md.
#pragma once
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <cstring>

namespace forza::telemetry {
struct CarPhysics {
  bool valid = false, abs = false;
  float rpm = 0, redline = 0, idle = 0, max_rpm = 0, speed = 0;
  // The game's processed controls: gas, brake, steer, clutch, handbrake.
  std::array<float, 5> controls{};
  std::array<float, 4> slip{}, slip_angle{}, wheel_speed{}, suspension{};
  std::array<bool, 4> contact{};
  uint8_t gear = 9;  // FH1: reverse 0, forward 1-8, neutral 9.
};

// Reader(address, destination, size) must validate the whole readable guest range.
// No guest calls, writes, heap allocation or assumptions about host endianness.
template<class Reader>
CarPhysics ReadCarPhysics(uint32_t car, bool tu4, Reader read) {
  CarPhysics out;
  if (car < 0x100a60 || car > 0xefff0000) return out;
  auto word = [&](uint32_t address, uint32_t& value) {
    std::array<uint8_t, 4> bytes{};
    if (!read(address, bytes.data(), bytes.size())) return false;
    value = uint32_t(bytes[0]) << 24 | uint32_t(bytes[1]) << 16 |
            uint32_t(bytes[2]) << 8 | bytes[3];
    return true;
  };
  auto number = [&](uint32_t address, float& value, float limit) {
    uint32_t bits;
    if (!word(address, bits)) return false;
    value = std::bit_cast<float>(bits);
    return std::isfinite(value) && std::abs(value) <= limit;
  };
  uint32_t vtable, abs;
  if (!word(car, vtable) ||
      (vtable != (tu4 ? 0x82003f0cu : 0x82003eecu) &&
       vtable != (tu4 ? 0x8224a6e4u : 0x8223759cu))) return out;
  constexpr float rpm_scale = 9.5492965855f;
  if (!number(car + 324, out.rpm, 10000) ||
      !number(car + 452, out.redline, 10000) ||
      !number(car + 456, out.max_rpm, 10000) ||
      !number(car + 1228, out.idle, 10000) ||
      !number(car + 3128, out.speed, 300) ||
      !word(car + 3020, abs) || !read(car + 2240, &out.gear, 1)) return {};
  if (out.rpm < 0 || out.idle < 0 || out.redline <= 0 || out.max_rpm <= 0 || out.gear > 9) return {};
  out.rpm *= rpm_scale; out.redline *= rpm_scale;
  out.idle *= rpm_scale; out.max_rpm *= rpm_scale; out.abs = abs != 0;
  for (unsigned i = 0; i < out.controls.size(); ++i) {
    if (!number(car + 3904 + 4 * i, out.controls[i], 1.01f)) return {};
    out.controls[i] = std::clamp(out.controls[i], i == 2 ? -1.0f : 0.0f, 1.0f);
  }
  const uint32_t stride = tu4 ? 1040 : 1024;
  for (unsigned i = 0; i < 4; ++i) {
    const uint32_t wheel = car + 3936 + i * stride;
    uint8_t contact;
    float spring, position;
    if (!read(wheel + (tu4 ? 800 : 796), &contact, 1) || contact > 1 ||
        !number(wheel + 496, out.slip[i], 1000) ||
        !number(wheel + 520, out.slip_angle[i], 1000) ||
        !number(wheel + 12, out.wheel_speed[i], 10000) ||
        !number(wheel, position, 10) || !number(wheel + (tu4 ? 876 : 860), spring, 10)) return {};
    out.contact[i] = contact != 0;
    if (!contact) out.slip[i] = out.slip_angle[i] = 0;
    out.suspension[i] = position - spring;
  }
  out.valid = true;
  return out;
}
}  // namespace forza::telemetry
