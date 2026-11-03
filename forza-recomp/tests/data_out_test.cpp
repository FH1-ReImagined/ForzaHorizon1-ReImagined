#include "../src/platform/data_out_packet.h"
#include "../src/platform/data_out_temperature.h"
#include <cassert>
#include <cstdio>
#include <limits>
#include <vector>

void Temperatures() {
  using namespace forza::data_out;
  constexpr uint32_t car = 0x200000;
  for (bool tu4 : {false, true}) {
    std::vector<uint8_t> bytes(0x4000);
    uint32_t blocked = 0;
    auto word = [&](uint32_t offset, uint32_t value) {
      for (unsigned i = 0; i < 4; ++i) bytes[offset + i] = uint8_t(value >> (24 - i * 8));
    };
    auto number = [&](uint32_t offset, float value) { word(offset, std::bit_cast<uint32_t>(value)); };
    auto read = [&](uint32_t address, void* target, size_t n) {
      if (address < car || uint64_t(address - car) + n > bytes.size() ||
          (blocked && address <= blocked && uint64_t(address) + n > blocked)) return false;
      std::memcpy(target, bytes.data() + address - car, n); return true;
    };
    word(0, tu4 ? 0x82003f0c : 0x82003eec);
    number(324, 500); number(452, 700); number(456, 750); number(1228, 100); number(3128, 20);
    bytes[2240] = 3;
    const uint32_t stride = tu4 ? 1040 : 1024;
    for (unsigned i = 0; i < 4; ++i) {
      const uint32_t wheel = 3936 + i * stride;
      number(wheel + 64, 70.0f + 10 * i);
      number(wheel + 52, 1.0f + 3 * i); number(wheel + 56, 7.0f + 3 * i);
      number(wheel + 60, 19.0f + 3 * i);
      number(wheel, .1f); number(wheel + (tu4 ? 876 : 860), .02f);
      bytes[wheel + (tu4 ? 800 : 796)] = 1;
    }
    auto temperatures = ReadTireTemperatures(car, tu4, read);
    assert(temperatures);
    for (unsigned i = 0; i < 4; ++i) assert((*temperatures)[i] == 79.0f + 13 * i);
    word(0, tu4 ? 0x8224a6e4 : 0x8223759c);
    assert(ReadTireTemperatures(car, tu4, read) == temperatures);
    // Losing any byte of a thermal read makes the optional sample unavailable.
    blocked = car + 3936 + 2 * stride + 61;
    assert(!ReadTireTemperatures(car, tu4, read)); blocked = 0;
    number(3936 + 3 * stride + 52, std::numeric_limits<float>::quiet_NaN());
    Sample sample; sample.driving = true;
    sample.car = forza::telemetry::ReadCarPhysics(car, tu4, read);
    sample.temperature = ReadTireTemperatures(car, tu4, read);
    assert(sample.car.valid && !sample.temperature);
    auto packet = Encode(sample, 0);
    assert(packet[0] == 1);  // Missing optional thermals cannot switch off valid physics/haptics.
    for (size_t i = 268; i < 284; ++i) assert(packet[i] == 0);  // Protocol unavailable values.
    number(3936 + 3 * stride + 52, std::numeric_limits<float>::infinity());
    assert(!ReadTireTemperatures(car, tu4, read));
    number(3936 + 3 * stride + 52, -2001);
    assert(!ReadTireTemperatures(car, tu4, read));
    number(3936 + 3 * stride + 52, -1000); number(3936 + 3 * stride + 64, -1000);
    assert(!ReadTireTemperatures(car, tu4, read));
    word(0, 0); assert(!ReadTireTemperatures(car, tu4, read));
    assert(!ReadTireTemperatures(0xffffffff, tu4, read));
  }
}

int main() {
  using namespace forza::data_out;
  Sample sample;
  sample.driving = true; sample.car.valid = true;
  sample.car.rpm = 4321.5f; sample.car.idle = 800; sample.car.max_rpm = 7500;
  sample.car.speed = -20; sample.car.gear = 0; sample.car.slip[0] = -2;
  sample.car.slip_angle[0] = 1.5f; sample.car.controls = {1, .5f, -1, .25f, 0};
  sample.position = {123, -4, 55}; sample.ordinal = 456; sample.car_class = 5;
  sample.pi = 789; sample.cylinders = 6; sample.group = 0x12345678;
  sample.smash_velocity = 12; sample.smash_mass = 42; sample.laps = 258;
  sample.temperature = std::array<float, 4>{32, 68, 104, 140};
  sample.race_position = 8; sample.driving_line = -127; sample.ai_brake = 126;
  for (bool horizon : {false, true}) {
    auto packet = Encode(sample, 0xfedcba98, horizon);
    auto word = [&](size_t i) {
      return uint32_t(packet[i]) | uint32_t(packet[i + 1]) << 8 |
             uint32_t(packet[i + 2]) << 16 | uint32_t(packet[i + 3]) << 24;
    };
    auto number = [&](size_t i) { return std::bit_cast<float>(word(i)); };
    const size_t dash = horizon ? 244 : 232;
    assert(word(0) == 1 && word(4) == 0xfedcba98);
    assert(number(8) == 7500 && number(12) == 800 && number(16) == 4321.5f);
    assert(number(84) == 2 && number(180) == 2.5f);
    assert(word(212) == 456 && word(220) == 789);
    if (horizon) assert(word(232) == 0x12345678 && number(236) == 12 && number(240) == 42);
    assert(number(dash) == 123 && number(dash + 12) == 20);
    for (unsigned i = 0; i < 4; ++i) {
      assert(number(dash + 24 + i * 4) == 32.0f + 36 * i);  // Fahrenheit on both wire layouts.
      assert((number(dash + 24 + i * 4) - 32) * 5 / 9 == 20.0f * i);
    }
    assert(packet[dash + 68] == 2 && packet[dash + 69] == 1 && packet[dash + 70] == 8);
    assert(packet[dash + 71] == 255 && packet[dash + 72] == 128 && packet[dash + 73] == 64);
    assert(packet[dash + 75] == 0 && int8_t(packet[dash + 76]) == -127);
    assert(int8_t(packet[dash + 77]) == -127 && packet[dash + 78] == 126);
    sample.car.gear = 9;
    assert(Encode(sample, 0, horizon)[dash + 75] == 11);
    sample.car.gear = 0;
  }
  sample.driving = false;
  auto inactive = Encode(sample, 5);
  for (size_t i = 0; i < inactive.size(); ++i) assert(inactive[i] == (i == 4 ? 5 : 0));
  sample.driving = true; sample.car.controls[2] = std::numeric_limits<float>::quiet_NaN();
  assert(Encode(sample, 0)[320] == 0);
  Temperatures();
  std::puts("Data Out: Horizon/FM7 offsets and disc/TU4 tire temperatures, units, mean and invalid fields pass");
}
