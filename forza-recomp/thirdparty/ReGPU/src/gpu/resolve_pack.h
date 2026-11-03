#pragma once
// Resolve destinations in guest memory: how the console stores a resolved pixel in each
// destination format (RB_COPY_DEST_INFO format), for writing a resolve back so the CPU (or
// anything else that reads memory) sees the bytes the GPU would have written. The value
// packed is the resolve's colour after the exponent bias; formats are the Xenos texture
// format numbers. Channels are packed red first from bit 0, as the resolve hardware does.
#include <cstddef>
#include <cstdint>

namespace regpu
{
namespace resolve
{
// log2 of the bytes per pixel of a destination format; false for a format a resolve can't write.
bool DestLog2Bytes(uint32_t format, uint32_t& log2Bytes);

// Packs one pixel into its little-endian words (1 to 4 dwords by size; 1- and 2-byte formats in
// the low bits of out[0]). False for a format a resolve can't write.
bool PackPixel(uint32_t format, const float rgba[4], uint32_t out[4]);

// Where the byte the GPU would put at `address` lands after the destination endian swap
// (the swap works within aligned 2-, 4-, 8- or 16-byte units, so a pixel smaller than the unit
// moves into its neighbour's bytes).
size_t SwappedByte(size_t address, uint32_t endian);

// An IEEE half as a float (a host resolve texture's RGBA16F texel).
float HalfToFloat(uint16_t h);

// Applies a destination endian mode (RB_COPY_DEST_INFO bits 0-2: 0 none, 1 8-in-16,
// 2 8-in-32, 3 16-in-32, 4 8-in-64, 5 8-in-128) to bytes in place; `bytes` is a multiple of the
// swap unit.
void SwapEndian(uint8_t* data, size_t bytes, uint32_t endian);
} // namespace resolve
} // namespace regpu
