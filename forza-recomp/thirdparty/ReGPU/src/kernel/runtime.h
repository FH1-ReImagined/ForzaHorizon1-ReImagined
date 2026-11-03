#pragma once
// Paths from the game's Config (include/regpu/regpu.h).
#include <filesystem>
#include "regpu/regpu.h"

inline const std::filesystem::path& RegpuUserDir() { return regpu::GetConfig().userDir; }
