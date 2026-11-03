// forza - host pointers for guest addresses, as the recompiled code computes them.
//
// Guest memory is `base + address`, except where the physical heap at 0xE0000000 and
// up sits 0x1000 bytes further on: macOS on ARM64, and a Windows build for systems
// before Windows 10 version 1803 (REX_WIN32_ROUNDED_PHYSICAL_VIEW), where the mapping
// granularity rounds that view's 4 KB offset away (ReIGlue 0405 maps it at its exact
// offset on other Windows builds). The generated code adds it back with
// REX_PHYS_HOST_OFFSET and the runtime with rex::memory::GuestPtr. Code here uses
// GuestPtr(base, address) for every guest pointer: a raw `base + address` reads other
// memory than the game and the runtime there. The runtime allocates its own guest
// objects in that heap (VdHSIOCalibrationLock, a critical section, is at 0xFFCAB000).

#pragma once

#include <rex/system/xmemory.h>

#include <cstdint>

namespace forza {

inline uint8_t* GuestPtr(uint8_t* base, uint32_t address) {
  return rex::memory::GuestPtr(base, address);
}

inline const uint8_t* GuestPtr(const uint8_t* base, uint32_t address) {
  return rex::memory::GuestPtr(const_cast<uint8_t*>(base), address);
}

}  // namespace forza
