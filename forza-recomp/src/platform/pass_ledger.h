// forza - the pass ledger's game side without guest memory (src/hooks/pass_ledger.cpp,
// docs/rendering.md "Pass ledger"): the marks the render thread writes into its command stream
// at the D3D flush points (ReIGlue 0670, rex/graphics/fh1_host_packet.h kOpPassClass and
// kOpPassEnd: keep the two the same), the draw class of a flush call, the draws of a command
// list, and the label of a segment of the frame.
//
// A segment ends after each Resolve and before the swap. Its label is the first presentation
// pass (the track's or the procedural models' pass argument) drawn in it, with how many segments
// of that pass came before it in the frame ("p6 #1"); in a segment with only cars, their pass
// argument ("c2 #0": cars number their passes otherwise); "mask #k" inside the sun's shadow mask
// function; otherwise the last named segment's label and how many segments after it this one is
// ("p4 #0 +3": the third after the scene's first).
//
// The deletion switch (test only; docs/rendering.md "Pass deletion"): with FORZA_TEST_SKIP_PASS
// set, every class mark also carries the segment's pass id so far (Labeler::Provisional, a tenth
// word) and a class mark is written whenever it changes, so that the plugin knows a draw's pass
// when it records it; FORZA_TEST_SKIP_LIST_GROUPS names the list groups (pass * 3 + layer) whose
// command lists the render thread does not run (SkipGroups).
#pragma once

#include <cstdint>
#include <cstring>
#include <utility>
#include <vector>

namespace forza::pass_ledger {

constexpr uint32_t kMagic = 0x4648314Eu;  // "FH1N"
constexpr uint32_t kOpPassClass = 0x30, kOpPassEnd = 0x31;
constexpr uint32_t kClassWords = 9, kEndWords = 11;
constexpr uint32_t kClassWordsWithPass = 10;  // FORZA_TEST_SKIP_PASS: [9] the pass id so far

enum Class : uint32_t {
  kClassD3D = 0,
  kClassUp = 1,
  kClassClear = 2,
  kClassTrack = 3,
  kClassProcedural = 4,
  kClassCar = 5,
  kClassOtherList = 6,
  kClassNone = 7,
};
constexpr uint32_t kNoClass = 0xFF;  // nothing emitted yet: the next draw's class is marked
constexpr uint32_t ClassWord(uint32_t cls, uint32_t layer) { return cls | (layer & 3) << 4; }

enum Kind : uint32_t { kKindStart = 1, kKindPresentation = 2, kKindShadowMask = 3, kKindCars = 5 };
constexpr uint32_t Label(uint32_t kind, uint32_t pass, uint32_t occurrence, uint32_t after) {
  return (kind & 0xFF) << 24 | (pass & 0xFF) << 16 | (occurrence & 0xFF) << 8 | (after & 0xFF);
}

// The flush points (src/hooks/native_device.h Point), as the ledger sees them.
enum class Flush : uint32_t { kDraw, kUp, kClear, kList, kResolve, kSwap, kTiling, kOther };

// The class of a flush call's commands: a list takes the scope it runs in (the track, the
// procedural models, a car; none: another list); D3D's own draws are D3D, UP or clear wherever
// they are. kNoClass for calls that draw nothing (they keep the class in progress).
constexpr uint32_t ClassOf(Flush flush, uint32_t scope_class, uint32_t scope_layer) {
  switch (flush) {
    case Flush::kDraw:
      return ClassWord(kClassD3D, 0);
    case Flush::kUp:
      return ClassWord(kClassUp, 0);
    case Flush::kClear:
      return ClassWord(kClassClear, 0);
    case Flush::kList:
      if (scope_class == kClassTrack || scope_class == kClassProcedural)
        return ClassWord(scope_class, scope_layer);
      if (scope_class == kClassCar) return ClassWord(kClassCar, 0);
      return ClassWord(kClassOtherList, 0);
    default:
      return kNoClass;
  }
}

// The draw packets of a command list's PM4 (DRAW_INDX and DRAW_INDX_2, predicated or not), in
// `count` words (host order); -1 if a packet runs past the end.
inline int CountDraws(const uint32_t* words, uint32_t count) {
  int draws = 0;
  for (uint32_t i = 0; i < count;) {
    const uint32_t h = words[i];
    uint32_t size;
    switch (h >> 30) {
      case 0:
        size = 2 + ((h >> 16) & 0x3FFF);
        break;
      case 1:
        size = 3;
        break;
      case 2:
        size = 1;
        break;
      default: {
        size = 2 + ((h >> 16) & 0x3FFF);
        const uint32_t opcode = (h >> 8) & 0x7F;
        if (opcode == 0x22 || opcode == 0x36) ++draws;
      } break;
    }
    if (size > count - i) return -1;
    i += size;
  }
  return draws;
}

// What a segment saw, and the labels of the frame's segments.
class Labeler {
 public:
  // At a flush in the segment: the presentation pass of the scope it is in (-1 none), whether
  // that scope is a car's, and whether the sun's shadow mask function is running.
  void Saw(int pass, bool car, bool mask) {
    if (mask) mask_ = true;
    if (pass < 0 || pass >= 8) return;
    int& first = car ? car_pass_ : pass_;
    if (first < 0) first = pass;
  }
  // The segment ends: its label.
  uint32_t End() {
    uint32_t label;
    if (mask_) {
      label = base_ = Label(kKindShadowMask, 0, mask_count_++, 0);
      after_ = 0;
    } else if (pass_ >= 0) {
      label = base_ = Label(kKindPresentation, uint32_t(pass_), pass_count_[pass_]++, 0);
      after_ = 0;
    } else if (car_pass_ >= 0) {
      label = base_ = Label(kKindCars, uint32_t(car_pass_), car_count_[car_pass_]++, 0);
      after_ = 0;
    } else {
      label = base_ | (++after_ < 0xFF ? after_ : 0xFF);
    }
    mask_ = false;
    pass_ = car_pass_ = -1;
    if (predict_) {
      frame_.emplace_back(last_, label);
      last_ = label;
      predicted_ = Successor(label);
    }
    return label;
  }
  // The frame ends (after the swap's segment end).
  void Frame() {
    base_ = Label(kKindStart, 0, 0, 0);
    after_ = 0;
    mask_count_ = 0;
    std::memset(pass_count_, 0, sizeof(pass_count_));
    std::memset(car_count_, 0, sizeof(car_count_));
    if (predict_) {
      // The frame the switch was read in is a part of one (the game reads it at the shadow
      // mask): no prediction from it.
      if (partial_) {
        partial_ = false;
        previous_.clear();
      } else {
        previous_.swap(frame_);
      }
      frame_.clear();
      last_ = kFrameStart;
      predicted_ = Successor(kFrameStart);
    }
  }

  // The deletion switch: the label the segment in progress has so far, for the draws the plugin
  // records before the segment ends. What the segment has drawn decides it as End() would, except
  // that before a shadow mask or a presentation pass has been seen (the frame's first draws, a
  // clear before a pass's lists, a car drawn before the track's lists), the label the segment
  // after the same label had in the previous frame stands for it. End() can still differ (the
  // frame's structure changed): the plugin counts the draws whose pass id was not their
  // segment's. Off (without SetPrediction), only End() is used.
  // `mid_frame`: switched on inside a frame, whose labels predict nothing.
  void SetPrediction(bool on, bool mid_frame = true) {
    predict_ = on;
    partial_ = mid_frame;
  }
  uint32_t Provisional() const {
    if (mask_) return Label(kKindShadowMask, 0, mask_count_, 0);
    if (pass_ >= 0) return Label(kKindPresentation, uint32_t(pass_), pass_count_[pass_], 0);
    if (car_pass_ >= 0) {
      if (predicted_ && (predicted_ >> 24) == kKindPresentation) return predicted_;
      return Label(kKindCars, uint32_t(car_pass_), car_count_[car_pass_], 0);
    }
    if (predicted_) return predicted_;
    return base_ | (after_ + 1 < 0xFF ? after_ + 1 : 0xFF);
  }

 private:
  static constexpr uint32_t kFrameStart = 0;  // not a label (kinds start at 1)
  uint32_t Successor(uint32_t label) const {
    for (const auto& p : previous_) {
      if (p.first == label) return p.second;
    }
    return 0;
  }
  bool mask_ = false;
  int pass_ = -1, car_pass_ = -1;
  uint32_t base_ = Label(kKindStart, 0, 0, 0);
  uint32_t after_ = 0;
  uint32_t mask_count_ = 0;
  uint32_t pass_count_[8] = {}, car_count_[8] = {};
  // The prediction: this frame's (label, the next label) pairs so far, the previous frame's, the
  // last label ended and the label predicted for the segment in progress (0: none).
  bool predict_ = false, partial_ = false;
  std::vector<std::pair<uint32_t, uint32_t>> frame_, previous_;
  uint32_t last_ = kFrameStart, predicted_ = 0;
};

// The numbers of the flush calls of one class since the previous mark.
struct Interval {
  uint64_t flush_ns = 0;
  uint32_t calls = 0, draws = 0, runs = 0;
};

inline uint32_t Clamp32(uint64_t v) { return v > 0xFFFFFFFFull ? 0xFFFFFFFFu : uint32_t(v); }

// kOpPassClass (host order): the class changes from `old_class` to `new_class`. With the
// deletion switch, `out` has kClassWordsWithPass words and [9] is the pass id so far.
inline void ClassMark(uint32_t seq, uint32_t new_class, uint32_t old_class, const Interval& i,
                      uint32_t* out) {
  const uint32_t w[kClassWords] = {kMagic,      kOpPassClass, seq,     new_class, old_class,
                                   Clamp32(i.flush_ns), i.calls, i.draws, i.runs};
  std::memcpy(out, w, sizeof(w));
}
inline void ClassMarkWithPass(uint32_t seq, uint32_t new_class, uint32_t old_class,
                              const Interval& i, uint32_t pass_id, uint32_t* out) {
  ClassMark(seq, new_class, old_class, i, out);
  out[kClassWords] = pass_id;
}
// kOpPassEnd (host order).
inline void EndMark(uint32_t seq, uint32_t label, uint64_t cpu_ns, uint32_t cls, const Interval& i,
                    uint32_t other_device, uint32_t* out) {
  const uint32_t w[kEndWords] = {kMagic,  kOpPassEnd, seq,     label,  Clamp32(cpu_ns),
                                 cls,     Clamp32(i.flush_ns), i.calls, i.draws, i.runs,
                                 other_device};
  std::memcpy(out, w, sizeof(w));
}

// FORZA_TEST_SKIP_LIST_GROUPS: list groups (pass * 3 + layer, 0-20) separated by commas, as a
// mask (bit g: group g). `bad` counts the parts that are not a group.
inline uint32_t ParseSkipGroups(const char* text, uint32_t* bad) {
  uint32_t mask = 0, wrong = 0;
  while (text && *text) {
    while (*text == ' ' || *text == ',') ++text;
    if (!*text) break;
    uint32_t value = 0, digits = 0;
    while (*text >= '0' && *text <= '9' && digits < 4) {
      value = value * 10 + uint32_t(*text++ - '0');
      ++digits;
    }
    const bool end = !*text || *text == ',' || *text == ' ';
    if (digits && end && value < 21) {
      mask |= 1u << value;
    } else {
      ++wrong;
      while (*text && *text != ',') ++text;
    }
  }
  if (bad) *bad = wrong;
  return mask;
}

}  // namespace forza::pass_ledger
