// Read-only tire temperatures; native HUD unit/mean evidence: docs/telemetry.md.
#pragma once
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>
#include <optional>

namespace forza::data_out {
// A missing/malformed optional thermal field must not invalidate core physics or haptics.
// Values are Fahrenheit, matching Data Out. No guest calls, writes or cached pointers.
template<class Reader>
std::optional<std::array<float, 4>> ReadTireTemperatures(uint32_t car, bool tu4, Reader read) {
  if (car < 0x100a60 || car > 0xefff0000) return std::nullopt;
  auto word = [&](uint32_t address, uint32_t& value) {
    std::array<uint8_t, 4> bytes{};
    if (!read(address, bytes.data(), bytes.size())) return false;
    value = uint32_t(bytes[0]) << 24 | uint32_t(bytes[1]) << 16 |
            uint32_t(bytes[2]) << 8 | bytes[3];
    return true;
  };
  uint32_t vtable;
  if (!word(car, vtable) ||
      (vtable != (tu4 ? 0x82003f0cu : 0x82003eecu) &&
       vtable != (tu4 ? 0x8224a6e4u : 0x8223759cu))) return std::nullopt;
  auto number = [&](uint32_t address, float& value) {
    uint32_t bits;
    if (!word(address, bits)) return false;
    value = std::bit_cast<float>(bits);
    return std::isfinite(value) && std::abs(value) <= 2000.0f;
  };
  std::array<float, 4> temperature;
  const uint32_t stride = tu4 ? 1040 : 1024;
  for (unsigned i = 0; i < temperature.size(); ++i) {
    const uint32_t wheel = car + 3936 + i * stride;
    float baseline, inner, middle, outer;
    if (!number(wheel + 64, baseline) || !number(wheel + 52, inner) ||
        !number(wheel + 56, middle) || !number(wheel + 60, outer)) return std::nullopt;
    // Each HUD zone getter returns baseline + zone delta; its per-tire view averages all three.
    temperature[i] = baseline + (inner + middle + outer) / 3.0f;
    if (temperature[i] < -459.67f || temperature[i] > 2000.0f) return std::nullopt;
  }
  return temperature;
}
}  // namespace forza::data_out
