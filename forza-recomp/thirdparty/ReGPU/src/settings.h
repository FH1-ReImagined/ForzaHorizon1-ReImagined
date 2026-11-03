#pragma once
// Internal name for the game's Config (include/regpu/regpu.h).
#include "regpu/regpu.h"

using Settings = regpu::Config;
inline Settings& GetSettings() { return regpu::GetConfig(); }
