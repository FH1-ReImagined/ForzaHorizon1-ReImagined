#pragma once
#include <cstdint>
union PPCRegister { uint64_t u64 = 0; uint32_t u32; int64_t s64; };
struct PPCContext { PPCRegister r3,r4,r5,r6,r7,r8; };
