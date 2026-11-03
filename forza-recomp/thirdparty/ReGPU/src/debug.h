#pragma once
// The live debug server (REGPU_DEBUG=1, port REGPU_DEBUG_PORT or 7341, 127.0.0.1 only): drive and inspect the
// running game without restarting it. tools/regpuctl.py is its client; the commands are listed there.
#include <cstdint>
#include <string>

void DebugServerStart();

#include "regpu/regpu.h"

// Input from the server (regpu.h DebugPadState).
using DebugPad = regpu::DebugPad;
inline bool DebugPadState(DebugPad& pad) { return regpu::DebugPadState(pad); }

// The renderer's side: each swap, and screenshots (a pending one names its file).
void DebugOnSwap(uint64_t frame);
bool DebugShotPending(std::string& path);
void DebugShotDone(const std::string& path);
