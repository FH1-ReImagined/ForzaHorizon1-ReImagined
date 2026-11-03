#pragma once
#include <array>
#include <cstdint>
struct PPCContext {
  struct { uint32_t u32 = 0; } r3;
  struct { double f64 = 0; } f1;
  uint64_t lr = 0;
  std::array<uint64_t, 32> untouched{};
};
