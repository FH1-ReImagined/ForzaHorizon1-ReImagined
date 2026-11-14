// D3D trace (FORZA_D3D_TRACE, docs/native-renderer.md): the shared memory between the game's
// counting wrappers around the Xbox D3D library (d3d_trace.cpp) and the GPU plugin's command
// processor (ReIGlue built with REXGLUE_FH1_PM4_TRACE, src/graphics/fh1_pm4_trace.cpp). Both sit
// in the game's process; the segment is /dev/shm/fh1pm4-<pid>, created by whichever side maps it
// first; the plugin removes the name at its first report block, when both have it mapped.
//
// The game tags every guest command-buffer dword a wrapped D3D function appended with that
// function's id (tags: the innermost wrapped frame, otags: the outermost one, the "entry
// point"); the command processor looks the tag up for every packet it executes and writes the
// per-block report, the game's call counters included.
//
// ReIGlue has its own copy of this file (src/graphics/fh1_pm4_trace_shm.h): the two must stay
// the same. kVersion changes with the layout; the plugin ignores a segment of another version.
#pragma once

#include <cstdint>

namespace fh1pm4 {

constexpr uint32_t kMagic = 0x46503401;  // written by the game once its wrappers run
constexpr uint32_t kVersion = 2;
constexpr int kIds = 256;  // 1-247 wrapped functions; 248-255 the plugin's classes
constexpr int kMaxId = 247;
constexpr int kLr = 1 << 15;
constexpr int kDevices = 64;
constexpr uint64_t kPhys = 512ull << 20;  // the guest's physical memory

struct LrEntry {
  uint64_t key;  // (return address << 8) | id; 0 = empty
  uint64_t count;
};

struct Shm {
  uint32_t magic;
  uint32_t version;
  uint64_t size;  // sizeof(Shm)
  uint32_t ndev;
  uint32_t devs[kDevices];  // D3D devices and recording contexts seen (r3 of the wrapped calls)
  uint64_t dev_bytes[kDevices];
  uint64_t calls[kIds];
  uint64_t outer_calls[kIds];  // calls with no wrapped caller
  uint64_t bytes[kIds];        // command-buffer bytes written (nested wrapped calls take theirs)
  uint64_t outer_bytes[kIds];  // bytes written while this id was the outermost wrapped frame
  uint64_t disc[kIds];         // the cursor moved back or by > 4 MB (a segment switch)
  uint64_t mask_pop[kIds][6];  // popcount of the device's +0..+40 dirty masks at entry (outermost)
  uint64_t mask_calls[kIds];
  uint64_t cursor_logged;
  uint64_t cursor_log[64][2];  // first cursor values seen: virtual, physical
  LrEntry lr[kLr];             // outermost calls by return address
  uint8_t tags[kPhys / 4];
  uint8_t otags[kPhys / 4];
};

// A guest virtual address in one of the physical views to its physical address.
inline uint32_t Phys(uint32_t virtual_address) {
  if (virtual_address >= 0xE0000000u) return (virtual_address & 0x1FFFFFFFu) + 0x1000u;
  return virtual_address & 0x1FFFFFFFu;
}

}  // namespace fh1pm4
