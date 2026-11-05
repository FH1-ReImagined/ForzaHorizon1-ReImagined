// forza - who holds the SDK's readback_resolve_wait_range (ReIGlue 0411).
//
// With readback_resolve = some (the default) a resolve is copied back to guest memory only
// when its key is new, so the CPU sees the first picture of a target the game resolves every
// frame. A range in readback_resolve_wait_range is copied back on every resolve, as with
// readback_resolve = full. The setting holds one range, and three hooks need one: the Big Shot
// (the front buffers, #552), the online photo (all of physical memory at the shutter, #576)
// and photo mode's focus (the world depth target, #665). Each asks for its own range here; the
// setting is the smallest range that covers all of them, so no hook drops another's.
#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace forza::readback_wait {

enum class Owner : size_t { kBigShot, kOnlinePhoto, kPhotoFocus, kCount };

// The setting's format: (address << 32) | length in bytes, 0 for none.
constexpr uint64_t Pack(uint32_t address, uint32_t length) {
  return length ? uint64_t(address) << 32 | length : 0;
}

class Ranges {
 public:
  // Sets the owner's range (0: none) and returns the range that covers every owner's.
  uint64_t Set(Owner owner, uint64_t range) {
    ranges_[size_t(owner)] = range;
    uint64_t start = UINT64_MAX, end = 0;
    for (const uint64_t r : ranges_) {
      if (!uint32_t(r)) continue;
      start = std::min(start, r >> 32);
      end = std::max(end, (r >> 32) + uint32_t(r));
    }
    if (!end) return 0;
    // A cover over 4 GB cannot be written; the physical address space is 512 MB.
    return Pack(uint32_t(start), uint32_t(std::min<uint64_t>(end - start, UINT32_MAX)));
  }

 private:
  std::array<uint64_t, size_t(Owner::kCount)> ranges_{};
};

// Asks for the owner's range (0 releases it) and sets readback_resolve_wait_range to the cover
// of all owners' ranges. False when the SDK has no such setting.
bool Request(Owner owner, uint64_t range);

}  // namespace forza::readback_wait
