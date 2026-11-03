#pragma once
#include <cstdint>
namespace rex::memory {
// Test memory keeps the guest pointer's low 16 bits in a bounded fixture.
// Production still compiles against the SDK's real alias-aware GuestPtr.
inline uint8_t* GuestPtr(uint8_t* base, uint32_t address) { return base + (address & 0xFFFF); }
}
