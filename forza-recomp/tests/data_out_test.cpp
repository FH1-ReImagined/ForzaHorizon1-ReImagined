#include "../src/platform/data_out_packet.h"
#include <cassert>
#include <cstdio>
#include <limits>

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
  std::puts("Data Out: Horizon/FM7 field offsets, units, inputs, reverse/neutral and inactive packets pass");
}
