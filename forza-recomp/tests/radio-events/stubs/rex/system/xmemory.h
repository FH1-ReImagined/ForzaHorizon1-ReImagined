#pragma once
#include <cstdint>
namespace rex::memory { inline uint8_t* GuestPtr(uint8_t* b,uint32_t a) { return b+(a&0x7FFFFFFF); } }
