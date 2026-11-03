#pragma once
#include <cstdint>
uint8_t* TestGuestPtr(uint32_t address);
namespace rex::memory {
inline uint8_t* GuestPtr(uint8_t*, uint32_t address) { return TestGuestPtr(address); }
}
