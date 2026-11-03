#pragma once
// The game's threads, through Host (include/regpu/regpu.h).
#include <string>
#include "regpu/regpu.h"

// KPCR field offsets (the processor control region of the CPU a guest thread runs on).
namespace kpcr
{
constexpr uint32_t CurrentCpu = 0x10C;
}

inline std::string RegpuGuestCpuUsage(double wallSeconds)
{
    auto f = regpu::GetHost().cpuUsage;
    return f ? f(wallSeconds) : std::string();
}
