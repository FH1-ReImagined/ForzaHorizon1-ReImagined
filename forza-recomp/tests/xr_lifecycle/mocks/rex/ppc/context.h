#pragma once
#include <cstdint>
struct PPCContext {
  union Register { uint32_t u32; uint64_t u64 = 0; } r3, r4;
  struct { double f64 = 0; } f1;
};
