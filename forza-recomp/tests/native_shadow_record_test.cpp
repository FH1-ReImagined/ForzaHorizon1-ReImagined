// Native renderer M2 (src/platform/native_shadow_record.h): the draw record the host device layer
// sends the GPU plugin, built from a D3D device's shadow state.
//   g++ -std=c++20 -I forza-recomp/src forza-recomp/tests/native_shadow_record_test.cpp
#include "platform/native_shadow_record.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

namespace ns = forza::native_shadow;

#define CHECK(x)                                                    \
  do {                                                              \
    if (!(x)) {                                                     \
      std::fprintf(stderr, "native shadow record: FAIL: %s\n", #x); \
      std::exit(1);                                                 \
    }                                                               \
  } while (false)

namespace {
// A device whose every shadow word is its own byte offset (guest order).
std::vector<uint8_t> Device() {
  std::vector<uint8_t> d(ns::kDeviceBytes);
  for (uint32_t o = 0; o < ns::kDeviceBytes; o += 4) {
    const uint32_t g = ns::Guest(o);
    std::memcpy(&d[o], &g, 4);
  }
  return d;
}
uint32_t Host(const uint32_t* w, uint32_t i) { return ns::Guest(w[i]); }
}  // namespace

int main() {
  const std::vector<uint8_t> dev = Device();
  std::vector<uint32_t> out(ns::kMaxRecordWords);

  // The fixed sections, in guest order (copied as they are).
  ns::Call call;
  call.island = 7;
  call.index = 3;
  call.kind = ns::kKindDrawIndexed;
  call.primitive = 4;
  call.count = 36;
  call.index_address = 0x1234560;
  call.vertex_shader = 0x40001000;
  call.pixel_shader = 0x40002000;
  call.vertex_dirty = (uint64_t(1) << 63) | 1;
  call.pixel_dirty = uint64_t(1) << 61;
  const uint32_t fixed = ns::kRecordHeaderWords + ns::kRegisterShadowWords + ns::kFetchWords;
  uint32_t n = ns::BuildRecord(dev.data(), call, false, 1, out.data());
  CHECK(n == fixed);
  CHECK(Host(out.data(), 0) == ns::kMagic);
  CHECK(Host(out.data(), 1) == ns::kOpRecord);
  CHECK(Host(out.data(), 2) == 7 && Host(out.data(), 3) == 3);
  CHECK(Host(out.data(), 4) == ns::kKindDrawIndexed);
  CHECK(Host(out.data(), 5) == 4 && Host(out.data(), 6) == 36);
  CHECK(Host(out.data(), 7) == 0x1234560);
  CHECK(Host(out.data(), 8) == 0x40001000 && Host(out.data(), 9) == 0x40002000);
  CHECK(Host(out.data(), 10) == 0);
  CHECK(Host(out.data(), 11) == 0x80000000u && Host(out.data(), 12) == 1);
  CHECK(Host(out.data(), 13) == 0x20000000u && Host(out.data(), 14) == 0);
  CHECK(Host(out.data(), 15) == 1);
  // Register shadow from device +10112, fetch constants from +1152.
  CHECK(Host(out.data(), ns::kRecordHeaderWords) == ns::kRegisterShadowOffset);
  CHECK(Host(out.data(), ns::kRecordHeaderWords + ns::kRegisterShadowWords - 1) ==
        ns::kRegisterShadowOffset + 4 * (ns::kRegisterShadowWords - 1));
  CHECK(Host(out.data(), ns::kRecordHeaderWords + ns::kRegisterShadowWords) == ns::kFetchOffset);
  CHECK(Host(out.data(), fixed - 1) == ns::kAluOffset - 4);

  // The dirty chunks, in order: vertex chunk 0 (bit 63) and 63 (bit 0), pixel chunk 2 (bit 61).
  uint8_t chunks[ns::kChunks];
  CHECK(ns::DirtyChunks(call.vertex_dirty, call.pixel_dirty, false, chunks) == 3);
  CHECK(chunks[0] == 0 && chunks[1] == 63 && chunks[2] == 66);
  CHECK(ns::DirtyChunks(0, 0, false, chunks) == 0);
  CHECK(ns::DirtyChunks(call.vertex_dirty, 0, true, chunks) == ns::kChunks);
  CHECK(chunks[0] == 0 && chunks[127] == 127);
  // A constants packet: index, then 16 words from +1920 + 64 * chunk.
  const uint8_t three[3] = {0, 63, 66};
  n = ns::BuildConstants(dev.data(), 7, ns::kConstantsSeed, three, 3, out.data());
  CHECK(n == ns::kConstantsHeaderWords + 3 * (1 + ns::kChunkWords));
  CHECK(Host(out.data(), 0) == ns::kMagic && Host(out.data(), 1) == ns::kOpConstants);
  CHECK(Host(out.data(), 2) == 7 && Host(out.data(), 3) == ns::kConstantsSeed &&
        Host(out.data(), 4) == 3);
  uint32_t at = ns::kConstantsHeaderWords;
  for (uint32_t chunk : three) {
    CHECK(Host(out.data(), at) == chunk);
    CHECK(Host(out.data(), at + 1) == ns::kAluOffset + 64 * chunk);
    CHECK(Host(out.data(), at + ns::kChunkWords) == ns::kAluOffset + 64 * chunk + 60);
    at += 1 + ns::kChunkWords;
  }
  // A full packet fits, and a seed takes 4.
  CHECK(ns::kMaxConstantsWords == ns::kConstantsHeaderWords + 32 * 17);
  CHECK(ns::kChunks / ns::kChunksPerPacket == 4);

  // A seed record says so.
  n = ns::BuildRecord(dev.data(), call, true, 4, out.data());
  CHECK(n == fixed && Host(out.data(), 10) == ns::kRecordSeed && Host(out.data(), 15) == 4);

  // An UP draw's vertex data: at most kVertexDataWords, padded with zeros.
  uint32_t quad[8];
  for (uint32_t i = 0; i < 8; ++i) quad[i] = ns::Guest(100 + i);
  call.vertex_data = reinterpret_cast<const uint8_t*>(quad);
  call.vertex_bytes = sizeof(quad);
  n = ns::BuildRecord(dev.data(), call, false, 0, out.data());
  CHECK(n == fixed + ns::kVertexDataWords);
  CHECK(Host(out.data(), 10) == ns::kRecordVertexData && Host(out.data(), 15) == 0);
  CHECK(Host(out.data(), fixed) == 100 && Host(out.data(), fixed + 7) == 107);
  CHECK(out[fixed + 8] == 0 && out[fixed + ns::kVertexDataWords - 1] == 0);
  std::vector<uint8_t> big(4096);
  call.vertex_data = big.data();
  call.vertex_bytes = 4096;
  CHECK(ns::BuildRecord(dev.data(), call, false, 0, out.data()) == ns::kMaxRecordWords);

  std::puts("native shadow record: ok");
  return 0;
}
