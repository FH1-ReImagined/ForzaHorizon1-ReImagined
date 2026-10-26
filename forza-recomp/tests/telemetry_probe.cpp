// Windows native producer probe: sparse, synthetic guest memory, no game data.
// Link with platform/telemetry.cpp, rexruntime and ws2_32, then receive FH1T UDP.
#include "../src/platform/telemetry.h"
#include "../src/platform/guest_addr.h"
#include "../src/platform/driving_gate.h"
#include <windows.h>
#include <bit>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <limits>

int main(int argc, char** argv) {
  const bool bad_temperature = argc > 1 && std::strcmp(argv[1], "--bad-temperature") == 0;
  auto* base = static_cast<uint8_t*>(VirtualAlloc(nullptr, 0x100000000ull, MEM_RESERVE, PAGE_NOACCESS));
  if (!base) return 1;
  auto word = [&](uint32_t address, uint32_t value) {
    if (!VirtualAlloc(base + (address & ~0xfffu), 4096, MEM_COMMIT, PAGE_READWRITE)) std::abort();
    value = std::byteswap(value);
    std::memcpy(base + address, &value, 4);
  };
  const uint32_t root = 0x1000000, holder = 0x1010000, list = 0x1020000,
                 slots = 0x1030000, vehicle = 0x1040000, car = 0x1060000;
  word(FORZA_ADDR(0x832DF024), root); word(root+4, holder); word(holder+4, list);
  word(list+0x80, slots); word(list+0x84, slots+4); word(slots, vehicle);
  word(vehicle+0x2DD0, 0); word(vehicle+0x2D90, car);
  word(car, FORZA_TU4 ? 0x82003f0c : 0x82003eec);
  auto number = [&](uint32_t offset, float value) { word(car + offset, std::bit_cast<uint32_t>(value)); };
  number(324, 500); number(452, 700); number(456, 750); number(1228, 100); number(3128, 20);
  word(car + 2240, 0x03000000); word(car + 3020, 1);
  for (unsigned i = 0; i < 5; ++i) number(3904 + i * 4, .5f);
  for (unsigned i = 0; i < 4; ++i) {
    const auto wheel = 3936 + i * (FORZA_TU4 ? 1040u : 1024u);
    number(wheel, .1f); number(wheel + (FORZA_TU4 ? 876 : 860), .02f); number(wheel + 12, 100);
    number(wheel + 496, float(i) + .25f); number(wheel + 520, -.5f);
    number(wheel + 64, 70.0f + 10 * i);
    number(wheel + 52, 1.0f + 3 * i); number(wheel + 56, 7.0f + 3 * i);
    number(wheel + 60, 19.0f + 3 * i);
    word(car + wheel + (FORZA_TU4 ? 800 : 796), 0x01000000);
  }
  if (bad_temperature) number(3936 + 52, std::numeric_limits<float>::quiet_NaN());
  const auto now = forza::telemetry::DrivingNowMs();
  for (auto time : {now - 600, now - 300, now})
    forza::telemetry::Player().Frame(time, {.stepped = true});
  auto position = [&](float x) {
    for (int i = 0; i < 3; ++i) {
      word(car-0xA20+i*4, std::bit_cast<uint32_t>(i == 0 ? x : 0.f));
      word(car-0xA40+i*4, std::bit_cast<uint32_t>(i == 2 ? 1.f : 0.f));
    }
  };
  for (int step = 0; step < 6; ++step) {
    Sleep(25);
    position(step < 3 ? step*.1f : 10000.f); // Teleport resets derivatives.
    if (step == 4) word(vehicle+0x2DD0, 2); // Remote car must not be sampled.
    if (step == 5) word(FORZA_ADDR(0x832DF024), 0x70000000); // Unmapped pointer.
    forza::telemetry::Rumble(0, 1000);  // driving: the game's rumble is running
    forza::telemetry::Input(step != 3, 255, 128, 0x1000, -32768);
    forza::telemetry::Physics(base);
  }
  if (argc > 1) {
    word(FORZA_ADDR(0x832DF024), root);
    word(vehicle+0x2DD0, 0);
    for (int step = 0; step < 20; ++step) {
      Sleep(step == 10 ? 220 : 25); // Queued commands must expire across a pause.
      position(0);
      forza::telemetry::Rumble(0, 1000);
      forza::telemetry::Input(true, 255, 128, 0, 0);
      forza::telemetry::Physics(base);
    }
  }
  VirtualFree(base, 0, MEM_RELEASE);
}
