#pragma once
#include "context.h"
#define REX_FUNC(name) void name(PPCContext& ctx, uint8_t* base)
namespace rex::runtime {
using GuestFunction = void(*)(PPCContext&, uint8_t*);
GuestFunction ResolveIndirectFunction(uint32_t address);
}
