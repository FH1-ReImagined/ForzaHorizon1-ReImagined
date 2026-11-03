// forza - which head pose each game frame was drawn with (docs/vr.md, "Frame pairing"), without
// OpenXR headers so tests/xr_frame_tags_test.cpp can check it on its own. Not thread-safe: the
// callers in src/platform/xr.cpp hold a lock.
//
// The camera hook (main loop thread) poses the view matrix and remembers the posed matrix with
// a new tag. The render thread later hands that matrix to the render-side camera (the hook on
// CPresentationCamera::SetViewTransform finds the tag by the matrix's bytes) and swaps the frame
// (the VdSwap hook writes the tag into the swap packet). The GPU plugin hands the tag on with the
// guest output image (ReIGlue 0391), and the OpenXR thread looks the tag up here: the pose and
// field of view that image was drawn with go into the projection layer.
#pragma once

#include "xr_math.h"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace forza::xr {

struct FrameTag {
  uint32_t id = 0;  // 0: no tag
  Pose head;  // the drawn view's pose in LOCAL space: the head turned by the frustum's offset
  CockpitFrustum frustum;  // what the game drew with (vertical 0: nothing)
  int64_t build_time = 0;  // XrTime when the camera hook ran
  int64_t target_time = 0;  // XrTime the pose was predicted for
  vr::HeadTransform game;  // the head as the game's camera took it (camera space, the seat included)
};

// The last N tags by id.
template <size_t N>
class TagRing {
 public:
  void Put(const FrameTag& tag) { tags_[tag.id % N] = tag; }
  const FrameTag* Find(uint32_t id) const {
    if (!id) return nullptr;
    const FrameTag& tag = tags_[id % N];
    return tag.id == id ? &tag : nullptr;
  }

 private:
  std::array<FrameTag, N> tags_{};
};

// The last N posed view matrices (64 guest bytes each) and their tags.
template <size_t N>
class ViewMatcher {
 public:
  void Remember(uint32_t id, const uint8_t matrix[64]) {
    Entry& e = entries_[next_++ % N];
    e.id = id;
    std::memcpy(e.matrix, matrix, 64);
  }
  // The tag of a matrix remembered earlier, newest first; 0 when there is none.
  uint32_t Match(const uint8_t matrix[64]) const {
    for (size_t k = 1; k <= N; ++k) {
      const Entry& e = entries_[(next_ - k) % N];
      if (e.id && std::memcmp(e.matrix, matrix, 64) == 0) return e.id;
    }
    return 0;
  }

 private:
  struct Entry {
    uint32_t id = 0;
    uint8_t matrix[64] = {};
  };
  std::array<Entry, N> entries_{};
  size_t next_ = N * 1024;  // never wraps below zero in Match
};

// The tag of the frame the render thread draws. The render-side camera keeps its matrix until
// the next SetViewTransform, and the render thread also swaps frames for which the main loop gave
// it no new view (about one in five at 30 fps): those were drawn with the camera's last view, so
// the tag stays until that camera gets a matrix that is not posed (another camera, the chase
// view). Views of other cameras (mirrors, the UI's scene cameras) that match nothing are ignored.
class SwapTagger {
 public:
  void OnView(uint32_t camera, uint32_t id) {
    if (id) {
      camera_ = camera;
      current_ = id;
      ++views_;
    } else if (camera == camera_) {
      camera_ = 0;
      current_ = 0;
    }
  }
  // Called at the swap: this frame's tag (0: none) and how many posed views arrived since the
  // last swap (more than one: the render thread dropped a frame; 0: it drew the last view again).
  // The tag of the view the render thread draws now (0: none).
  uint32_t Current() const { return current_; }
  uint32_t TakeForSwap(uint32_t* views = nullptr) {
    if (views) *views = views_;
    views_ = 0;
    return current_;
  }

 private:
  uint32_t camera_ = 0;
  uint32_t current_ = 0;
  uint32_t views_ = 0;
};

enum class Layer { kNone, kQuad, kProjection };

// A tag older than this at display time is not a cockpit frame any more (the game stopped
// building in-car views: a menu, a loading screen): the picture goes back on the screen.
constexpr int64_t kMaxTagAgeNs = 300'000'000;

// What the OpenXR thread shows for a guest output: nothing before the first image, the
// projection layer for a recent cockpit frame whose tag it knows (cockpit mode only), else the
// screen.
inline Layer ChooseLayer(bool have_image, bool cockpit_mode, const FrameTag* tag, int64_t display_time) {
  if (!have_image) return Layer::kNone;
  const bool cockpit = cockpit_mode && tag && tag->frustum.vertical > 0 &&
                       display_time - tag->build_time <= kMaxTagAgeNs;
  return cockpit ? Layer::kProjection : Layer::kQuad;
}

// The swap packet VdSwap writes (64 dwords): a type-0 packet of 7 dwords setting the frontbuffer
// fetch constant, then the type-3 XE_SWAP packet (header, 'SWAP', address, width, height) and
// type-2 fillers. A tag goes in the first filler and the XE_SWAP packet grows by one dword: the
// SDK without ReIGlue 0391 skips it, with it the command processor reads it. Values are host
// order; `dwords` holds them as the guest stores them is up to the caller.
constexpr uint32_t kXeSwapHeader = (3u << 30) | ((4u - 1) << 16) | (0x64u << 8);
constexpr uint32_t kXeSwapTaggedHeader = (3u << 30) | ((5u - 1) << 16) | (0x64u << 8);
constexpr uint32_t kType2Filler = 2u << 30;
constexpr size_t kXeSwapHeaderIndex = 7;
constexpr size_t kXeSwapTagIndex = 12;

// Rewrites a swap packet (host-order dwords) to carry `tag`; false (unchanged) when the packet
// is not the layout above.
inline bool TagSwapPacket(uint32_t dwords[64], uint32_t swap_signature, uint32_t tag) {
  if (dwords[kXeSwapHeaderIndex] != kXeSwapHeader || dwords[kXeSwapHeaderIndex + 1] != swap_signature ||
      dwords[kXeSwapTagIndex] != kType2Filler) {
    return false;
  }
  dwords[kXeSwapHeaderIndex] = kXeSwapTaggedHeader;
  dwords[kXeSwapTagIndex] = tag;
  return true;
}

}  // namespace forza::xr
