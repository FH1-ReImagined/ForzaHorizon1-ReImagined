#pragma once
#include <cstdint>
namespace rex::memory {
inline uint8_t* GuestPtr(uint8_t* base, uint32_t address) { return base + address; }
}
