#pragma once

#include <atomic>
#include <bit>
#include <cstdint>

namespace forza::resource {

// Guest fields are big endian, four-byte aligned storage, not std::atomic objects.
// Every competing owner must use this exchange; an ordinary load followed by a
// clear can still return a pointer another thread has already taken and released.
inline uint32_t TakeData(uint32_t& field) {
  static_assert(std::atomic_ref<uint32_t>::required_alignment <= alignof(uint32_t));
  uint32_t value = std::atomic_ref<uint32_t>(field).exchange(0);
  if constexpr (std::endian::native == std::endian::little) value = __builtin_bswap32(value);
  return value;
}

}  // namespace forza::resource
