// Garage occupancy includes cars queued by the native acquisition flows.
#pragma once
#include <cstdint>

namespace forza::garage {
constexpr uint32_t kCapacity = 1000;

inline bool Full(uint32_t stored, uint32_t begin, uint32_t end, uint32_t capacity_end) {
  // The native vector holds eight-byte entries. Reject malformed descriptors rather
  // than letting subtraction wrap and admit another car. Empty, unallocated is valid.
  if ((begin == 0 && (end != 0 || capacity_end != 0)) ||
      begin > end || end > capacity_end ||
      (begin & 3) || ((end - begin) & 7) || ((capacity_end - begin) & 7))
    return true;
  return uint64_t(stored) + (uint64_t(end) - begin) / 8 >= kCapacity;
}
}  // namespace forza::garage
