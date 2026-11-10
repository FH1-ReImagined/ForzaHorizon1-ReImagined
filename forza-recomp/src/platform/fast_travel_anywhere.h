// forza - Fast Travel Anywhere owned without its Token purchase (src/hooks/fast_travel_anywhere.cpp,
// #424). The decisions on a profile value, without guest memory, so
// tests/fast_travel_anywhere_test.cpp can check them.
//
// Title Update 4 sold Fast Travel Anywhere in the map for Tokens, bought on Xbox LIVE with real
// money; the purchase is gone with LIVE. The game reads the unlock from the profile's value tree:
// the bool at "Main/FastTravelAnywhere". Each reader looks it up with the path getter
// (sub_82553198: r3 the value it fills, r4 the tree, r5 the path) and takes the bool from the
// value's byte +8 (sub_8249B880). The value as the getter leaves it (20 bytes, big-endian):
//   +0  vtable
//   +8  data (8 bytes): a bool is the byte at +8
//   +16 type (u32): 0xFFFFFFFF empty (a profile that never had it), 0 bool
// (sub_82CC0C18 makes an empty value, sub_82CC7248 a bool one, sub_82CC6330 changes the type.)
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace forza::fast_travel_anywhere {

// The path string in Title Update 4's executable, as both readers pass it.
constexpr uint32_t kPathAddress = 0x8201949C;
constexpr char kPath[] = "Main/FastTravelAnywhere";

constexpr uint32_t kValueData = 8;
constexpr uint32_t kValueType = 16;
constexpr uint32_t kValueSize = 20;
constexpr uint32_t kTypeEmpty = 0xFFFFFFFFu;
constexpr uint32_t kTypeBool = 0;

inline uint32_t LoadType(const uint8_t* value) {
  uint32_t v;
  std::memcpy(&v, value + kValueType, 4);
  return __builtin_bswap32(v);
}

// The executable has the path at kPathAddress (`bytes` the guest bytes there, at least
// sizeof(kPath)): a check that this is the executable the address was found in.
inline bool PathMatches(const uint8_t* bytes, size_t size) {
  return size >= sizeof(kPath) && std::memcmp(bytes, kPath, sizeof(kPath)) == 0;
}

// Whether the profile itself has the unlock (it was bought before LIVE closed).
inline bool Owned(const uint8_t* value) {
  return LoadType(value) == kTypeBool && value[kValueData] != 0;
}

// Make a value the getter filled read as owned: an empty value or a false bool becomes a true
// bool. Any other type is not the game's, and stays as it is. Returns whether it changed.
// Neither an empty nor a bool value holds memory, so nothing needs freeing.
inline bool Grant(uint8_t* value) {
  const uint32_t type = LoadType(value);
  if (type != kTypeEmpty && type != kTypeBool) return false;
  if (Owned(value)) return false;
  const uint32_t bool_type = __builtin_bswap32(kTypeBool);
  std::memcpy(value + kValueType, &bool_type, 4);
  std::memset(value + kValueData, 0, 8);
  value[kValueData] = 1;
  return true;
}

}  // namespace forza::fast_travel_anywhere
