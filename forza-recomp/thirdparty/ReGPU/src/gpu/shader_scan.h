#pragma once
#include <cstdint>
#include <vector>

#include "regpu/regpu.h"
using ScannedShader = regpu::ScannedShader;

// Scans guest physical memory for shader objects not reported before; results queue up
// for ShaderScanTake. Synchronous, with `threads` workers.
void ShaderScanNow(unsigned threads);
// Starts a low-priority thread that rescans every 1.5 s.
void ShaderScanStart();
// Moves up to maxCount queued shaders into out; false when none were queued.
bool ShaderScanTake(std::vector<ScannedShader>& out, size_t maxCount);
// Shaders found so far.
uint64_t ShaderScanTotal();
