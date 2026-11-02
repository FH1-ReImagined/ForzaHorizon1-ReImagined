#pragma once
#include <cstdint>
union PPCRegister { uint64_t u64=0; uint32_t u32; };
struct PPCContext { uint64_t lr=0; PPCRegister r3,r4,r5,r6,r7; struct { double f64=0; } f1; };
