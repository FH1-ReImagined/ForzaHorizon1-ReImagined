#pragma once
#include <cstdint>
union PPCRegister { uint64_t u64 = 0; uint32_t u32; };
struct PPCContext { PPCRegister r3, r4; struct { double f64 = 0; } f1; };
