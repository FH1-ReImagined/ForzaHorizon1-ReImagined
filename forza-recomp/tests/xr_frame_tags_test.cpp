// Standalone check of the VR frame pairing (src/platform/xr_frame_tags.h): which head pose each
// game frame was drawn with, from the camera hook to the swap packet and the OpenXR layer.
// c++ -std=c++20 -Wall -Wextra -Werror -I forza-recomp/src forza-recomp/tests/xr_frame_tags_test.cpp -o /tmp/xr_frame_tags_test && /tmp/xr_frame_tags_test
#include "platform/xr_frame_tags.h"

#include <cstdio>
#include <cstring>

namespace {
using namespace forza::xr;
int failures = 0;

void Check(bool ok, const char* what) {
  if (!ok) {
    std::printf("FAIL: %s\n", what);
    ++failures;
  }
}

void Matrix(uint8_t out[64], int seed) {
  for (int i = 0; i < 64; ++i) out[i] = uint8_t(seed * 31 + i);
}

void TestRing() {
  TagRing<4> ring;
  for (uint32_t id = 1; id <= 6; ++id) ring.Put({id, {}, {0.1f * float(id), 1.0f}, int64_t(id), 0, {}});
  Check(!ring.Find(1) && !ring.Find(2), "old tags are overwritten");
  Check(ring.Find(5) && ring.Find(5)->build_time == 5, "recent tags are found");
  Check(!ring.Find(0), "0 is no tag");
  Check(!ring.Find(10), "a tag never put");  // 10 % 4 == 2, slot holds 6
}

void TestMatcher() {
  ViewMatcher<4> views;
  uint8_t a[64], b[64], c[64];
  Matrix(a, 1), Matrix(b, 2), Matrix(c, 3);
  views.Remember(7, a);
  views.Remember(8, b);
  Check(views.Match(a) == 7 && views.Match(b) == 8, "posed matrices find their tags");
  Check(views.Match(c) == 0, "an unposed matrix (mirror, UI camera) has none");
  // The same matrix posed again (head still) belongs to the newer frame.
  views.Remember(9, a);
  Check(views.Match(a) == 9, "newest first");
  for (uint32_t id = 10; id < 14; ++id) views.Remember(id, c);
  Check(views.Match(b) == 0, "forgotten after N newer ones");
}

void TestSwapTagger() {
  constexpr uint32_t kCamera = 0x40001000, kMirror = 0x40002000;
  SwapTagger tagger;
  uint32_t views = 0;
  Check(tagger.TakeForSwap(&views) == 0 && views == 0, "no view: untagged frame");
  tagger.OnView(kMirror, 0);  // another camera's view that matched nothing changes nothing
  tagger.OnView(kCamera, 4);
  Check(tagger.TakeForSwap(&views) == 4 && views == 1, "the frame's view");
  Check(tagger.TakeForSwap(&views) == 4 && views == 0, "a frame drawn again keeps the camera's view");
  tagger.OnView(kCamera, 5);
  tagger.OnView(kMirror, 0);
  tagger.OnView(kCamera, 6);
  Check(tagger.TakeForSwap(&views) == 6 && views == 2, "the last view before the swap wins");
  tagger.OnView(kCamera, 0);  // the same camera, unposed: the chase camera
  Check(tagger.TakeForSwap(&views) == 0 && views == 0, "an unposed view of the camera clears it");
}

// The whole path: camera hook -> render thread -> swap -> GPU plugin -> OpenXR thread.
void TestPipeline() {
  TagRing<64> tags;
  ViewMatcher<8> views;
  SwapTagger tagger;
  uint8_t m1[64], m2[64];
  Matrix(m1, 11), Matrix(m2, 12);
  // Main loop: frames 1 and 2 are built before the render thread draws frame 1.
  tags.Put({1, {{0, 0.1f, 0, 1}, {}}, {1.9f, 1.2f, 0, -0.1f}, 100, 140, {}});
  views.Remember(1, m1);
  tags.Put({2, {{0, 0.2f, 0, 1}, {}}, {1.9f, 1.2f, 0, -0.1f}, 133, 173, {}});
  views.Remember(2, m2);
  // Render thread draws frame 1 and swaps it.
  tagger.OnView(0x40001000, views.Match(m1));
  const uint32_t swapped = tagger.TakeForSwap();
  Check(swapped == 1, "the swap carries frame 1's tag although frame 2 was built since");
  const FrameTag* tag = tags.Find(swapped);
  Check(tag && tag->head.q.y == 0.1f, "the OpenXR thread finds frame 1's pose");
  const int64_t display = tag->build_time + 60'000'000;
  Check(ChooseLayer(true, true, tag, display) == Layer::kProjection, "cockpit frame: projection layer");
  Check(ChooseLayer(true, false, tag, display) == Layer::kQuad, "screen mode: always the quad");
  Check(ChooseLayer(true, true, nullptr, display) == Layer::kQuad, "untagged frame (menu, chase camera): quad");
  Check(ChooseLayer(false, true, tag, display) == Layer::kNone, "nothing before the first picture");
  Check(ChooseLayer(true, true, tag, tag->build_time + kMaxTagAgeNs + 1) == Layer::kQuad,
        "a stale tag (no in-car views any more): quad");
  FrameTag no_fov = *tag;
  no_fov.frustum.vertical = 0;
  Check(ChooseLayer(true, true, &no_fov, display) == Layer::kQuad, "no field of view: quad");
}

void TestSwapPacket() {
  constexpr uint32_t kSignature = 'S' << 24 | 'W' << 16 | 'A' << 8 | 'P';
  uint32_t packet[64];
  // As the SDK's VdSwap writes it.
  packet[0] = (0u << 30) | (5u << 16) | 0x4800;  // type 0, fetch constant, 6 dwords
  for (int i = 1; i < 7; ++i) packet[i] = 0x1000u + uint32_t(i);
  packet[7] = kXeSwapHeader;
  packet[8] = kSignature;
  packet[9] = 0x1E000000;
  packet[10] = 1280;
  packet[11] = 720;
  for (int i = 12; i < 64; ++i) packet[i] = kType2Filler;
  Check(kXeSwapHeader == 0xC0036400u, "XE_SWAP header as MakePacketType3(0x64, 4)");
  uint32_t tagged[64];
  std::memcpy(tagged, packet, sizeof(packet));
  Check(TagSwapPacket(tagged, kSignature, 0x12345678), "tags the packet");
  Check(tagged[7] == 0xC0046400u && tagged[12] == 0x12345678, "five dwords, the tag last");
  for (int i = 0; i < 64; ++i)
    if (i != 7 && i != 12) Check(tagged[i] == packet[i], "nothing else changes");
  // A command processor without ReIGlue 0391 reads 4 dwords and skips count - 4: the tag.
  const uint32_t count = ((tagged[7] >> 16) & 0x3FFF) + 1;
  Check(count == 5 && 8 + count == 13, "the next packet starts after the tag");
  uint32_t other[64];
  std::memcpy(other, packet, sizeof(packet));
  other[8] = 0;
  Check(!TagSwapPacket(other, kSignature, 1) && other[7] == kXeSwapHeader, "another layout is left alone");
  std::memcpy(other, tagged, sizeof(other));
  Check(!TagSwapPacket(other, kSignature, 2) && other[12] == 0x12345678, "tagged only once");
}
}  // namespace

int main() {
  TestRing();
  TestMatcher();
  TestSwapTagger();
  TestPipeline();
  TestSwapPacket();
  if (failures) return 1;
  std::printf("xr_frame_tags_test: ok\n");
  return 0;
}
