// The pass ledger's game side without guest memory (src/platform/pass_ledger.h): the draw class
// of a flush call, the draws of a command list's packets, the segment labels of a frame, and the
// marks' words (the GPU plugin's ReIGlue 0670 reads them: rex/graphics/fh1_host_packet.h).
//   g++ -std=c++20 -O2 -I forza-recomp/src forza-recomp/tests/pass_ledger_test.cpp
#include "platform/pass_ledger.h"

#include <cstdio>
#include <cstdlib>
#include <vector>

namespace pl = forza::pass_ledger;

#define CHECK(x)                                                               \
  do {                                                                         \
    if (!(x)) {                                                                \
      std::fprintf(stderr, "pass ledger: FAIL line %d: %s\n", __LINE__, #x);   \
      std::exit(1);                                                            \
    }                                                                          \
  } while (false)

namespace {

uint32_t Type3(uint32_t opcode, uint32_t payload) {
  return 0xC0000000u | ((payload - 1) << 16) | (opcode << 8);
}

void Classes() {
  CHECK(pl::ClassOf(pl::Flush::kDraw, pl::kClassTrack, 1) == pl::kClassD3D);
  CHECK(pl::ClassOf(pl::Flush::kUp, 0xFF, 0) == pl::kClassUp);
  CHECK(pl::ClassOf(pl::Flush::kClear, 0xFF, 0) == pl::kClassClear);
  CHECK(pl::ClassOf(pl::Flush::kList, pl::kClassTrack, 2) == (pl::kClassTrack | 2u << 4));
  CHECK(pl::ClassOf(pl::Flush::kList, pl::kClassProcedural, 1) ==
        (pl::kClassProcedural | 1u << 4));
  CHECK(pl::ClassOf(pl::Flush::kList, pl::kClassCar, 2) == pl::kClassCar);  // no layer
  CHECK(pl::ClassOf(pl::Flush::kList, 0xFF, 0) == pl::kClassOtherList);
  CHECK(pl::ClassOf(pl::Flush::kResolve, pl::kClassTrack, 0) == pl::kNoClass);
  CHECK(pl::ClassOf(pl::Flush::kTiling, 0xFF, 0) == pl::kNoClass);
  CHECK(pl::ClassOf(pl::Flush::kSwap, 0xFF, 0) == pl::kNoClass);
}

void Draws() {
  std::vector<uint32_t> w;
  w.push_back(Type3(0x2D, 3));  // SET_CONSTANT: 3 words
  w.insert(w.end(), {0, 0, 0});
  w.push_back(Type3(0x22, 3));  // DRAW_INDX
  w.insert(w.end(), {0, 0, 0});
  w.push_back(Type3(0x36, 1) | 1);  // DRAW_INDX_2, predicated
  w.push_back(0);
  w.push_back(0x00010000u | 0x2000);  // type 0, two registers
  w.insert(w.end(), {0, 0});
  w.push_back(0x80000000u);  // type 2
  w.push_back(0x40000000u);  // type 1
  w.insert(w.end(), {0, 0});
  w.push_back(Type3(0x10, 2));  // NOP
  w.insert(w.end(), {0, 0});
  CHECK(pl::CountDraws(w.data(), uint32_t(w.size())) == 2);
  CHECK(pl::CountDraws(w.data(), 0) == 0);
  // A packet that runs past the end.
  CHECK(pl::CountDraws(w.data(), 3) == -1);
}

void Labels() {
  pl::Labeler l;
  // HUD to texture (nothing named), the four shadow maps, the pre-pass, the mask's three
  // segments, the scene, then post-processing.
  CHECK(l.End() == pl::Label(pl::kKindStart, 0, 0, 1));
  for (uint32_t c = 0; c < 4; ++c) {
    l.Saw(2, true, false);  // a car first: the track's pass still names the segment
    l.Saw(6, false, false);
    l.Saw(4, false, false);  // the first track pass seen names it
    CHECK(l.End() == pl::Label(pl::kKindPresentation, 6, c, 0));
  }
  l.Saw(0, false, false);
  CHECK(l.End() == pl::Label(pl::kKindPresentation, 0, 0, 0));
  for (uint32_t m = 0; m < 3; ++m) {
    l.Saw(-1, false, true);
    CHECK(l.End() == pl::Label(pl::kKindShadowMask, 0, m, 0));
  }
  CHECK(l.End() == pl::Label(pl::kKindShadowMask, 0, 2, 1));  // after the mask
  l.Saw(3, true, false);  // only cars
  CHECK(l.End() == pl::Label(pl::kKindCars, 3, 0, 0));
  l.Saw(3, true, false);
  CHECK(l.End() == pl::Label(pl::kKindCars, 3, 1, 0));
  l.Saw(4, false, false);
  CHECK(l.End() == pl::Label(pl::kKindPresentation, 4, 0, 0));
  CHECK(l.End() == pl::Label(pl::kKindPresentation, 4, 0, 1));
  CHECK(l.End() == pl::Label(pl::kKindPresentation, 4, 0, 2));
  // Not a presentation pass the ledger knows.
  l.Saw(9, false, false);
  CHECK(l.End() == pl::Label(pl::kKindPresentation, 4, 0, 3));
  l.Frame();
  CHECK(l.End() == pl::Label(pl::kKindStart, 0, 0, 1));
  l.Saw(6, false, false);
  CHECK(l.End() == pl::Label(pl::kKindPresentation, 6, 0, 0));  // occurrences start again
}

void Marks() {
  pl::Interval i;
  i.flush_ns = 123456;
  i.calls = 7;
  i.draws = 40;
  i.runs = 3;
  uint32_t c[pl::kClassWords];
  pl::ClassMark(5, pl::kClassTrack | 1u << 4, pl::kClassD3D, i, c);
  CHECK(c[0] == pl::kMagic && c[1] == pl::kOpPassClass && c[2] == 5 &&
        c[3] == (pl::kClassTrack | 1u << 4) && c[4] == pl::kClassD3D && c[5] == 123456 &&
        c[6] == 7 && c[7] == 40 && c[8] == 3);
  uint32_t e[pl::kEndWords];
  i.flush_ns = 0x1FFFFFFFFull;  // clamped
  pl::EndMark(6, pl::Label(pl::kKindPresentation, 4, 0, 0), 2500000, pl::kClassCar, i, 9, e);
  CHECK(e[0] == pl::kMagic && e[1] == pl::kOpPassEnd && e[2] == 6 && e[3] == 0x02040000u &&
        e[4] == 2500000 && e[5] == pl::kClassCar && e[6] == 0xFFFFFFFFu && e[7] == 7 &&
        e[8] == 40 && e[9] == 3 && e[10] == 9);
}

// The deletion switch's pass ids (FORZA_TEST_SKIP_PASS): the label so far, with the previous
// frame's labels standing in before the segment has drawn anything that names it.
void PassIds() {
  pl::Labeler l;
  l.SetPrediction(true);  // in the middle of a frame: its labels predict nothing
  l.Saw(4, false, false);
  l.End();
  l.Frame();
  const uint32_t start1 = pl::Label(pl::kKindStart, 0, 0, 1);
  const uint32_t p6 = pl::Label(pl::kKindPresentation, 6, 0, 0);
  const uint32_t p0 = pl::Label(pl::kKindPresentation, 0, 0, 0);
  const uint32_t mask0 = pl::Label(pl::kKindShadowMask, 0, 0, 0);
  const uint32_t mask_after = pl::Label(pl::kKindShadowMask, 0, 0, 1);
  const uint32_t c3 = pl::Label(pl::kKindCars, 3, 0, 0);
  const uint32_t p4 = pl::Label(pl::kKindPresentation, 4, 0, 0);
  const uint32_t p4_1 = pl::Label(pl::kKindPresentation, 4, 0, 1);
  // A frame: the shadow maps (D3D draws and a car before the track's lists), the pre-pass, the
  // mask and the segment after it, cars only, the scene, one segment after it.
  const auto frame = [&](bool first) {
    // Nothing seen: the first frame knows no labels, later ones take the previous frame's.
    CHECK(l.Provisional() == (first ? start1 : p6));
    l.Saw(2, true, false);  // a car: still the predicted presentation pass
    CHECK(l.Provisional() == (first ? pl::Label(pl::kKindCars, 2, 0, 0) : p6));
    l.Saw(6, false, false);
    CHECK(l.Provisional() == p6);
    CHECK(l.End() == p6);
    CHECK(l.Provisional() == (first ? pl::Label(pl::kKindPresentation, 6, 0, 1) : p0));
    l.Saw(0, false, false);
    CHECK(l.Provisional() == p0);
    CHECK(l.End() == p0);
    l.Saw(-1, false, true);
    CHECK(l.Provisional() == mask0);
    CHECK(l.End() == mask0);
    CHECK(l.Provisional() == mask_after);  // the same with or without the prediction
    CHECK(l.End() == mask_after);
    CHECK(l.Provisional() == (first ? pl::Label(pl::kKindShadowMask, 0, 0, 2) : c3));
    l.Saw(3, true, false);
    CHECK(l.Provisional() == c3);
    CHECK(l.End() == c3);
    l.Saw(4, false, false);
    CHECK(l.Provisional() == p4);
    CHECK(l.End() == p4);
    CHECK(l.Provisional() == p4_1);
    CHECK(l.End() == p4_1);
    l.Frame();
  };
  frame(true);
  frame(false);
  frame(false);
  // The structure changes: a second shadow map where the pre-pass was. Before it draws, the
  // prediction says the pre-pass (the plugin counts such draws); its lists correct it.
  CHECK(l.Provisional() == p6);
  l.Saw(6, false, false);
  CHECK(l.End() == p6);
  CHECK(l.Provisional() == p0);
  l.Saw(6, false, false);
  CHECK(l.Provisional() == pl::Label(pl::kKindPresentation, 6, 1, 0));
  CHECK(l.End() == pl::Label(pl::kKindPresentation, 6, 1, 0));
  // A label the previous frame did not have: no prediction.
  CHECK(l.Provisional() == pl::Label(pl::kKindPresentation, 6, 1, 1));
  // Without the prediction (the ledger alone) Provisional is End()'s rule.
  pl::Labeler plain;
  CHECK(plain.Provisional() == start1);
  plain.Saw(2, true, false);
  CHECK(plain.Provisional() == pl::Label(pl::kKindCars, 2, 0, 0));
  plain.Saw(6, false, false);
  CHECK(plain.Provisional() == plain.End());
  plain.Frame();
  CHECK(plain.Provisional() == start1);

  uint32_t c[pl::kClassWordsWithPass];
  pl::Interval i;
  pl::ClassMarkWithPass(9, pl::kClassD3D, pl::kClassTrack, i, p4, c);
  CHECK(c[1] == pl::kOpPassClass && c[2] == 9 && c[3] == pl::kClassD3D && c[9] == p4);
}

void SkipGroups() {
  uint32_t bad = 99;
  CHECK(pl::ParseSkipGroups("18,19,20", &bad) == (7u << 18) && bad == 0);
  CHECK(pl::ParseSkipGroups(" 0 , 2,", &bad) == 5u && bad == 0);
  CHECK(pl::ParseSkipGroups("21,x,7a,18", &bad) == (1u << 18) && bad == 3);
  CHECK(pl::ParseSkipGroups("12345", &bad) == 0 && bad == 1);
  CHECK(pl::ParseSkipGroups("", &bad) == 0 && bad == 0);
  CHECK(pl::ParseSkipGroups(nullptr, nullptr) == 0);
}

}  // namespace

int main() {
  Classes();
  Draws();
  Labels();
  Marks();
  PassIds();
  SkipGroups();
  std::printf("pass ledger: all checks passed\n");
  return 0;
}
