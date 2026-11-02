#include "../src/platform/car_physics.h"
#include <cassert>
#include <cstdio>
#include <limits>
#include <vector>

int main() {
  constexpr uint32_t car = 0x200000;
  for (bool tu4 : {false, true}) {
    std::vector<uint8_t> bytes(0x4000);
    auto word = [&](uint32_t offset, uint32_t value) {
      for (unsigned i = 0; i < 4; ++i) bytes[offset + i] = uint8_t(value >> (24 - i * 8));
    };
    auto number = [&](uint32_t offset, float value) { word(offset, std::bit_cast<uint32_t>(value)); };
    auto read = [&](uint32_t address, void* target, size_t n) {
      if (address < car || uint64_t(address - car) + n > bytes.size()) return false;
      std::memcpy(target, bytes.data() + address - car, n); return true;
    };
    word(0, tu4 ? 0x82003f0c : 0x82003eec);
    number(324, 500); number(452, 700); number(456, 750); number(1228, 100); number(3128, 20);
    bytes[2240] = 3; word(3020, 1);
    for (unsigned i = 0; i < 5; ++i) number(3904 + i * 4, i * .1f);
    const auto stride = tu4 ? 1040u : 1024u;
    const auto contact_offset = tu4 ? 800u : 796u;
    for (unsigned i = 0; i < 4; ++i) {
      const auto wheel = 3936 + i * stride;
      if (tu4) number(wheel + 860, 154.f);  // Old offset is not suspension.
      if (tu4) bytes[wheel + 796] = 0x3b;  // Old offset is float data in TU4.
      bytes[wheel + contact_offset] = 1;
      number(wheel + 496, float(i) + .25f); number(wheel + 520, -.5f);
      number(wheel + 12, 100 + float(i)); number(wheel, .1f); number(wheel + (tu4 ? 876 : 860), .02f);
    }
    auto sample = forza::telemetry::ReadCarPhysics(car, tu4, read);
    assert(sample.valid && sample.abs && sample.gear == 3);
    for (float v : sample.suspension) assert(std::abs(v - .08f) < .0001f);
    assert(std::abs(sample.rpm - 4774.648f) < .1f);
    for (unsigned i = 0; i < 4; ++i) assert(sample.slip[i] == float(i) + .25f);
    bytes[3936 + 3 * stride + contact_offset] = 0;
    sample = forza::telemetry::ReadCarPhysics(car, tu4, read);
    assert(sample.valid && sample.slip[3] == 0 && sample.slip_angle[3] == 0);
    number(324, std::numeric_limits<float>::quiet_NaN());
    assert(!forza::telemetry::ReadCarPhysics(car, tu4, read).valid);
    number(324, 500); word(0, 0);
    assert(!forza::telemetry::ReadCarPhysics(car, tu4, read).valid);
    assert(!forza::telemetry::ReadCarPhysics(0xffffffff, tu4, read).valid);
  }
  std::puts("car physics: disc/TU4 stride, units, contact and invalid reads pass");
}
