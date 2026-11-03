// Which resolves "readback_resolve = some" does not copy back again after a gap (#45, patch
// 0181): src/graphics/vulkan/fh1_readback_history.h of the patched SDK source.
// Build with the patched SDK source's src/graphics/vulkan on the include path:
//   c++ -std=c++17 -I ~/rexglue-sdk-src-forza/sdk/src/graphics/vulkan
//       tools/test_readback_history.cpp -o test_readback_history
#include "fh1_readback_history.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace rh = rex::graphics::vulkan::fh1_readback_history;

#define CHECK(x)                                                    \
  do {                                                              \
    if (!(x)) {                                                     \
      std::fprintf(stderr, "readback history: FAIL: %s\n", #x);     \
      std::exit(1);                                                 \
    }                                                               \
  } while (false)

int main() {
  constexpr uint64_t kScene = 0x1000, kDepth = 0x2000, kPicture = 0x3000, kPhoto = 0x4000;
  rh::History h;
  // Nothing is hot before it was seen.
  CHECK(!h.IsHot(kScene));
  // The scene's targets, resolved every frame (the depth target several times a frame).
  for (uint64_t f = 1; f < rh::kHotRunFrames; ++f) {
    h.Resolved(kScene, f);
    h.Resolved(kDepth, f);
    h.Resolved(kDepth, f);
  }
  CHECK(!h.IsHot(kScene) && !h.IsHot(kDepth));
  h.Resolved(kScene, rh::kHotRunFrames);
  h.Resolved(kDepth, rh::kHotRunFrames);
  CHECK(h.IsHot(kScene) && h.IsHot(kDepth));
  // A car picture now and then: a few frames each time, never a long run.
  uint64_t f = 1000;
  for (int picture = 0; picture < 200; ++picture) {
    for (int i = 0; i < 3; ++i) h.Resolved(kPicture, f + i);
    f += 90;
  }
  CHECK(!h.IsHot(kPicture));
  // A photo: once.
  h.Resolved(kPhoto, f);
  CHECK(!h.IsHot(kPhoto));
  // A gap (the map) does not make the scene's targets cold; a short run after it neither
  // lowers their longest run.
  h.Resolved(kScene, f + 1200);
  h.Resolved(kScene, f + 1201);
  CHECK(h.IsHot(kScene));
  // Every second frame still counts as a run (a target resolved at half the frame rate).
  rh::History half;
  for (uint64_t g = 0; g < 2 * rh::kHotRunFrames; g += 2) half.Resolved(7, g);
  CHECK(half.IsHot(7));
  // Every third frame does not.
  rh::History third;
  for (uint64_t g = 0; g < 3 * rh::kHotRunFrames * 3; g += 3) third.Resolved(8, g);
  CHECK(!third.IsHot(8));
  // Forgotten after kForgetFrames without a resolve.
  h.Forget(f + 1201 + rh::kForgetFrames);
  CHECK(h.IsHot(kScene));
  h.Forget(f + 1202 + rh::kForgetFrames);
  CHECK(!h.IsHot(kScene));
  // At most kMaxKeys keys: the oldest go first.
  rh::History many;
  for (uint64_t k = 0; k < rh::kMaxKeys + 100; ++k) many.Resolved(100000 + k, 10 + k);
  for (uint64_t g = 1; g <= rh::kHotRunFrames; ++g) many.Resolved(99, 20000 + g);
  many.Forget(20000 + rh::kHotRunFrames);
  CHECK(many.size() == rh::kMaxKeys);
  CHECK(many.IsHot(99));

  // FH1 (#536): first-seen resolves that may be copied back without a wait for the GPU.
  using RT = rh::RepeatTracker;
  constexpr uint64_t kCar64 = 0xC64, kCar32 = 0xC32, kPhotoShape = 0xF07, kVinyl = 0x71;
  {
    RT t;
    // Nothing repeats yet: the first car of a session waits.
    RT::Key& first = t.Resolved(1, kCar64, 100);
    CHECK(!RT::Defers(t.Judge(first, 100)));
    CHECK(t.Judge(first, 100) == RT::Why::kNoRepeaters);
    // It is resolved every frame; so are cars 2 and 3, which arrive later (their first resolve
    // still waits: one repeater of the shape is too few).
    uint64_t f = 101;
    for (; f < 140; ++f) {
      t.Resolved(1, kCar64, f);
      if (f >= 105) t.Resolved(2, kCar64, f);
      if (f >= 110) t.Resolved(3, kCar64, f);
      if (f == 106) CHECK(t.Judge(t.Resolved(2, kCar64, f), f) == RT::Why::kFewRepeaters);
      if (f == 112) CHECK(t.Judge(t.Resolved(9, kCar64, f), f) == RT::Why::kShapeRepeats);  // two repeat
    }
    CHECK(t.FindShape(kCar64) && t.FindShape(kCar64)->repeaters == 3);
    // A fourth car at a fresh address: deferred, from its first resolve.
    RT::Key& car4 = t.Resolved(4, kCar64, f);
    CHECK(car4.resolves == 1 && car4.gap == 0);
    CHECK(t.Judge(car4, f) == RT::Why::kShapeRepeats);
    // Another shape, never seen repeating: waits (a thumbnail with the size of a car target).
    RT::Key& vinyl = t.Resolved(50, kVinyl, f);
    CHECK(t.Judge(vinyl, f) == RT::Why::kNoRepeaters);
    // A key that repeated, back after its buffer was evicted (a gap), repeats: deferred even in
    // a shape nobody else repeats in.
    RT lone;
    for (uint64_t g = 0; g < 10; ++g) lone.Resolved(7, kCar32, 10 + g);
    RT::Key& back = lone.Resolved(7, kCar32, 500);
    CHECK(back.gap > 400 && lone.Judge(back, 500) == RT::Why::kKeyRepeated);
    // A recurring target resolved every 3rd or 20th frame (a shadow cascade, a mirror) is no per-frame
    // repeater of its shape, but its own key repeats: deferred when it comes back after a gap.
    RT cascade;
    for (uint64_t g = 0; g < 40; ++g) cascade.Resolved(5, kCar32 + 1, 100 + 3 * g);
    CHECK(!cascade.FindShape(kCar32 + 1) || !cascade.FindShape(kCar32 + 1)->repeaters);
    RT::Key& back2 = cascade.Resolved(5, kCar32 + 1, 100 + 3 * 40 + 300);
    CHECK(back2.gap == 303 && cascade.Judge(back2, back2.last_frame) == RT::Why::kKeyRepeated);
    RT mirror;
    for (uint64_t g = 0; g < 6; ++g) mirror.Resolved(6, kCar32 + 2, 100 + 40 * g);
    CHECK(mirror.Judge(mirror.Resolved(6, kCar32 + 2, 1000), 1000) == RT::Why::kKeyRepeated);
    // Photos saved one after the other, minutes apart (the same key each time): never recurring.
    RT photos;
    for (uint64_t g = 0; g < 30; ++g) photos.Resolved(8, kPhotoShape, 1000 + 900 * g);
    CHECK(photos.Judge(photos.Resolved(8, kPhotoShape, 100000), 100000) != RT::Why::kKeyRepeated);
    // Resolves in the same frame do not extend a run.
    RT same;
    for (int i = 0; i < 20; ++i) same.Resolved(9, kPhotoShape, 77);
    CHECK(same.Judge(same.Resolved(9, kPhotoShape, 77), 77) == RT::Why::kNoRepeaters);
    // A run with a one-frame gap still counts; every third frame does not.
    RT half, third;
    for (uint64_t g = 0; g < 40; g += 2) half.Resolved(1, kCar64, g);
    for (uint64_t g = 0; g < 90; g += 3) third.Resolved(1, kCar64, g);
    CHECK(half.FindShape(kCar64)->repeaters == 1);
    CHECK(!third.FindShape(kCar64));
  }
  {
    // A shape with as many one-offs as repeaters (a picture and a render target of the same
    // size) is not deferred.
    RT t;
    for (uint64_t k = 0; k < 4; ++k) {
      for (uint64_t g = 0; g < 10; ++g) t.Resolved(k, kPhotoShape, 100 + g);
    }
    CHECK(t.Judge(t.Resolved(99, kPhotoShape, 200), 200) == RT::Why::kShapeRepeats);
    // Twenty photos, each resolved once, ending (found by the sweep).
    for (uint64_t k = 0; k < 20; ++k) t.Resolved(100 + k, kPhotoShape, 300 + 20 * k);
    t.Sweep(800);
    CHECK(t.FindShape(kPhotoShape)->one_offs >= 17);
    CHECK(t.Judge(t.Resolved(200, kPhotoShape, 820), 820) == RT::Why::kMixedShape);
    // The one-offs fade: a shape that repeats again an hour later is not held back by them.
    for (uint64_t g = 900; g <= 900 + 3 * RT::kShapeFreshFrames; g += 60) {
      for (uint64_t k = 0; k < 4; ++k) t.Resolved(k, kPhotoShape, g);  // the repeaters keep going
      t.Sweep(g);
    }
    CHECK(t.FindShape(kPhotoShape)->one_offs <= 3);
    // The same shape's repeaters, long ago: stale, then forgotten.
    RT old_shape;
    for (uint64_t k = 0; k < 4; ++k) {
      for (uint64_t g = 0; g < 10; ++g) old_shape.Resolved(k, kPhotoShape, 100 + g);
    }
    const uint64_t later = 110 + RT::kShapeFreshFrames + 100;
    CHECK(old_shape.Judge(old_shape.Resolved(201, kPhotoShape, later), later) == RT::Why::kStaleShape);
    old_shape.Sweep(100 + 5 * RT::kShapeFreshFrames);
    CHECK(!old_shape.FindShape(kPhotoShape));
  }
  {
    // A key that ended and came back is no longer a one-off.
    RT t;
    t.Resolved(1, kPhotoShape, 10);
    t.Sweep(100);
    CHECK(t.FindShape(kPhotoShape)->one_offs == 1);
    t.Resolved(1, kPhotoShape, 200);
    CHECK(t.FindShape(kPhotoShape)->one_offs == 0);
    // Old keys are forgotten.
    t.Sweep(200 + RT::kForgetKeyFrames + 100);
    CHECK(t.key_count() == 0);
  }
  std::printf("readback history: all checks passed\n");
  return 0;
}
