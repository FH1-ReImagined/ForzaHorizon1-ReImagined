// forza - native renderer M2 (docs/native-renderer.md, "M2: shadow mode"): the draw records the
// host device layer builds from the Xbox D3D device's shadow state at a D3D flush point, as the
// words of the GPU plugin's host packet (ReIGlue 0610). The game builds against the nightly SDK's
// headers, not ReIGlue's, so this is the game's copy of the layout in ReIGlue's
// rex/graphics/fh1_host_packet.h: keep the two the same (the plugin counts a packet it cannot
// read as malformed in its summary).
//
// A record holds what a native renderer would draw from: the registers the D3D device shadows
// (8 blocks, 161 registers), the 32 fetch constants, the draw call's own arguments and the bound
// shader objects. Its ALU constants go just before it, in constants packets of at most 32 chunks
// of 4 constants: all 512 for an island's first record (the "seed"), afterwards only the chunks
// the device had marked dirty, as a native renderer would upload them. The plugin keeps the
// constants it was sent and compares every record with its register file at that point of the
// stream.
//
// Pure functions over a copy of the device's first kDeviceBytes, for tests.
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <initializer_list>

namespace forza::native_shadow {

constexpr uint32_t kMagic = 0x4648314Eu;  // "FH1N"
constexpr uint32_t kMaxWords = 4096;      // payload words of one host packet
constexpr uint32_t kOpIslandBegin = 0x10, kOpRecord = 0x11, kOpIslandEnd = 0x12,
                   kOpConstants = 0x13, kOpRef = 0x14,
                   kOpLiterals = 0x15;
constexpr uint32_t kLiteralsHeaderWords = 5;
constexpr uint32_t kConstantsSeed = 1, kConstantsHeaderWords = 5, kChunksPerPacket = 32;
constexpr uint32_t kIslandShadowMask = 1;
constexpr uint32_t kIslandCompareContents = 1;

enum Kind : uint32_t {
  kKindNone = 0,
  kKindDrawVertices = 1,
  kKindDrawIndexed = 2,
  kKindBeginVertices = 3,
  kKindDrawVerticesUp = 4,
  kKindResolve = 5,
  kKindClear = 6,
  kKindRunCommandBuffer = 7,
  kKindDrawIndexedUp = 8,
  kKindCount = 9,
};

constexpr uint32_t kRecordHeaderWords = 16;
constexpr uint32_t kRecordSeed = 1, kRecordVertexData = 2;
constexpr uint32_t kRegisterShadowOffset = 10112, kRegisterShadowWords = 188;
constexpr uint32_t kFetchOffset = 1152, kFetchWords = 192;
constexpr uint32_t kAluOffset = 1920, kAluWords = 2048, kChunkWords = 16, kChunks = 128;
constexpr uint32_t kVertexDataWords = 20;
// Device fields: the two ALU dirty masks (+0 vertex, +8 pixel), the command buffer (+48 the last
// dword written, +52 the hard limit, +56 the soft one), the shader objects.
constexpr uint32_t kVertexDirty = 0, kPixelDirty = 8, kCursor = 48, kHardLimit = 52,
                   kSoftLimit = 56, kVertexShader = 13072, kPixelShader = 13068;
// What a record reads of the device.
constexpr uint32_t kDeviceBytes = kRegisterShadowOffset + kRegisterShadowWords * 4;
static_assert(kAluOffset + kAluWords * 4 == kRegisterShadowOffset);
static_assert(kFetchOffset + kFetchWords * 4 == kAluOffset);
constexpr uint32_t kMaxRecordWords =
    kRecordHeaderWords + kRegisterShadowWords + kFetchWords + kVertexDataWords;
constexpr uint32_t kMaxConstantsWords = kConstantsHeaderWords + kChunksPerPacket * (1 + kChunkWords);
static_assert(kMaxConstantsWords <= kMaxWords);
static_assert(kMaxRecordWords <= kMaxWords);

// A guest-order word from a host value.
inline uint32_t Guest(uint32_t v) { return __builtin_bswap32(v); }
inline uint64_t LoadGuest64(const uint8_t* p) {
  uint64_t v;
  std::memcpy(&v, p, 8);
  return __builtin_bswap64(v);
}

// The flush call a record is made for.
struct Call {
  uint32_t island = 0, index = 0, kind = kKindNone;
  uint32_t primitive = 0, count = 0, index_address = 0;
  uint32_t vertex_shader = 0, pixel_shader = 0;
  // The ALU dirty masks as they were when the call started (the flush clears them).
  uint64_t vertex_dirty = 0, pixel_dirty = 0;
  // An UP draw's vertex data (guest order), or null.
  const uint8_t* vertex_data = nullptr;
  uint32_t vertex_bytes = 0;
};

// The ALU chunks dirty in the masks, in order (0-63 vertex, 64-127 pixel; bit 63 of a mask is
// chunk 0), or all 128 for a seed. Returns how many were written to `chunks` (128 entries).
inline uint32_t DirtyChunks(uint64_t vertex_dirty, uint64_t pixel_dirty, bool seed,
                            uint8_t* chunks) {
  uint32_t n = 0;
  if (seed) {
    for (uint32_t c = 0; c < kChunks; ++c) chunks[n++] = uint8_t(c);
    return n;
  }
  for (uint32_t half = 0; half < 2; ++half) {
    uint64_t mask = half ? pixel_dirty : vertex_dirty;
    while (mask) {
      const uint32_t i = uint32_t(__builtin_clzll(mask));
      mask &= ~(uint64_t(1) << (63 - i));
      chunks[n++] = uint8_t(half * 64 + i);
    }
  }
  return n;
}

// One constants packet's words (guest order) for `count` (at most kChunksPerPacket) chunks.
inline uint32_t BuildConstants(const uint8_t* device, uint32_t island, uint32_t flags,
                               const uint8_t* chunks, uint32_t count, uint32_t* out) {
  uint32_t n = 0;
  for (uint32_t w : {kMagic, kOpConstants, island, flags, count}) out[n++] = Guest(w);
  for (uint32_t k = 0; k < count; ++k) {
    out[n++] = Guest(chunks[k]);
    std::memcpy(out + n, device + kAluOffset + chunks[k] * kChunkWords * 4, kChunkWords * 4);
    n += kChunkWords;
  }
  return n;
}

// Writes the record for `call` from `device` (the device's first kDeviceBytes, guest order) to
// `out` (kMaxRecordWords words), in guest order, ready to be copied into a command buffer behind
// a type-3 NOP header. `seed`: its constants packets carried all 512 constants;
// `constant_packets`: how many were written for it. Returns the number of words.
inline uint32_t BuildRecord(const uint8_t* device, const Call& call, bool seed,
                            uint32_t constant_packets, uint32_t* out) {
  uint32_t flags = seed ? kRecordSeed : 0;
  const uint32_t vertex_words = call.vertex_data ? (call.vertex_bytes / 4 < kVertexDataWords
                                                        ? call.vertex_bytes / 4
                                                        : kVertexDataWords)
                                                 : 0;
  if (vertex_words) flags |= kRecordVertexData;
  const uint32_t header[kRecordHeaderWords] = {
      kMagic,
      kOpRecord,
      call.island,
      call.index,
      call.kind,
      call.primitive,
      call.count,
      call.index_address,
      call.vertex_shader,
      call.pixel_shader,
      flags,
      uint32_t(call.vertex_dirty >> 32),
      uint32_t(call.vertex_dirty),
      uint32_t(call.pixel_dirty >> 32),
      uint32_t(call.pixel_dirty),
      constant_packets,
  };
  uint32_t n = 0;
  for (uint32_t w : header) out[n++] = Guest(w);
  std::memcpy(out + n, device + kRegisterShadowOffset, kRegisterShadowWords * 4);
  n += kRegisterShadowWords;
  std::memcpy(out + n, device + kFetchOffset, kFetchWords * 4);
  n += kFetchWords;
  if (vertex_words) {
    std::memcpy(out + n, call.vertex_data, vertex_words * 4);
    n += vertex_words;
    for (uint32_t i = vertex_words; i < kVertexDataWords; ++i) out[n++] = 0;
  }
  return n;
}

}  // namespace forza::native_shadow
