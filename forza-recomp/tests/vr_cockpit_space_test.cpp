// Standalone check of the VR cockpit's cabin offset maths (src/platform/vr_cockpit_space.h).
// c++ -std=c++20 -Wall -Wextra -Werror -I forza-recomp/src forza-recomp/tests/vr_cockpit_space_test.cpp -o /tmp/vr_cockpit_space_test && /tmp/vr_cockpit_space_test
#include "platform/vr_cockpit_space.h"

#include <cmath>
#include <cstdio>

namespace {
using namespace forza::vr;
int failures = 0;

void Check(bool ok, const char* what) {
  if (!ok) {
    std::printf("FAIL: %s\n", what);
    ++failures;
  }
}
bool Near(float a, float b, float eps = 1e-4f) { return std::fabs(a - b) < eps; }

void TestFixedPoint() {
  Check(ToFixed18(1.0f) == 262144, "1.0 is 2^18");
  Check(ToFixed18(-1.0f) == -262144, "-1.0 is -2^18");
  Check(ToFixed18(1.9999999f / 262144.0f) == 1, "rounded toward zero");
  Check(ToFixed18(-1.9999999f / 262144.0f) == -1, "rounded toward zero below zero");
  Check(ToFixed18(1e9f) == 2147483647, "saturated high");
  Check(ToFixed18(-1e9f) == -2147483647 - 1, "saturated low");
  Check(ToFixed18(std::nanf("")) == 0, "NaN is 0");
  Check(SubSat(2147483647, -1) == 2147483647, "subtraction saturates");
}

// The offset the game computes: the camera's position minus the point, exact to 1/2^18 m far from
// the origin (the world is a few km across), where a float subtraction would lose millimetres.
void TestOffset() {
  const float point_m[4] = {87.9843f, 101.6482f, -3920.9001f, 1.0f};
  int32_t point[4];
  for (int i = 0; i < 4; ++i) point[i] = ToFixed18(point_m[i]);
  const float camera[4] = {87.9843f + 0.06f, 101.6482f - 0.04f, -3920.9001f + 0.03f, 1.0f};
  float off[4];
  CockpitOffset(camera, point, off);
  Check(Near(off[0], 0.06f, 2e-4f) && Near(off[1], -0.04f, 2e-4f) && Near(off[2], 0.03f, 5e-4f),
        "offset is camera minus point");
  Check(Near(off[3], 0.0f), "w cancels");
  Check(GameSnaps(off), "the game snaps 7.8 cm to zero");
  const float far_camera[4] = {87.9843f + 0.12f, 101.6482f, -3920.9001f, 1.0f};
  CockpitOffset(far_camera, point, off);
  Check(!GameSnaps(off), "the game keeps 12 cm");
  // Edge of the game's sphere: 9.9 cm snaps, 10.1 cm does not (measured 9.9-10.1 cm in v7).
  const float in[4] = {87.9843f, 101.6482f - 0.099f, -3920.9001f, 1.0f};
  const float out[4] = {87.9843f, 101.6482f - 0.101f, -3920.9001f, 1.0f};
  CockpitOffset(in, point, off);
  Check(GameSnaps(off), "9.9 cm snaps");
  CockpitOffset(out, point, off);
  Check(!GameSnaps(off), "10.1 cm does not");
}

void TestRigidInverse() {
  // A camera turned 30 degrees about y at (0.06, -0.04, 0.03): the view takes its position to the
  // origin and the axes back to the identity.
  const float c = std::cos(0.5236f), s = std::sin(0.5236f);
  const float m[16] = {c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0.06f, -0.04f, 0.03f, 1};
  float v[16];
  RigidInverse(m, v);
  float p[4] = {0, 0, 0, 0};
  const float pos[4] = {0.06f, -0.04f, 0.03f, 1};
  for (int j = 0; j < 4; ++j)
    for (int k = 0; k < 4; ++k) p[j] += pos[k] * v[4 * k + j];
  Check(Near(p[0], 0) && Near(p[1], 0) && Near(p[2], 0) && Near(p[3], 1), "the camera's position goes to the origin");
  float prod[16] = {};
  for (int i = 0; i < 4; ++i)
    for (int j = 0; j < 4; ++j)
      for (int k = 0; k < 4; ++k) prod[4 * i + j] += m[4 * i + k] * v[4 * k + j];
  bool identity = true;
  for (int i = 0; i < 4; ++i)
    for (int j = 0; j < 4; ++j) identity &= Near(prod[4 * i + j], i == j ? 1.0f : 0.0f);
  Check(identity, "camera matrix times view is the identity");
}
// The head's offset in world axes is where the posed camera is, less where the camera itself is.
void TestHeadOffset() {
  const HeadTransform h = ToTransform({35.0f, -12.0f, 4.0f, 0.06f, -0.04f, 0.03f});
  // The camera's own camera-to-world matrix: turned 70 degrees about y, pitched 5, at p.
  const float a = 1.2217f, b = -0.0873f;
  const float ya[3][3] = {{std::cos(a), 0, -std::sin(a)}, {0, 1, 0}, {std::sin(a), 0, std::cos(a)}};
  const float pi[3][3] = {{1, 0, 0}, {0, std::cos(b), std::sin(b)}, {0, -std::sin(b), std::cos(b)}};
  float c[3][3] = {};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j)
      for (int k = 0; k < 3; ++k) c[i][j] += pi[i][k] * ya[k][j];
  const float p[3] = {87.98f, 101.65f, -3920.9f};
  // posed = H * C: rows 0-2 R * C, row 3 t * C + p
  float m[16] = {};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j)
      for (int k = 0; k < 3; ++k) m[4 * i + j] += h.r[i][k] * c[k][j];
  for (int j = 0; j < 3; ++j) m[12 + j] = h.t[0] * c[0][j] + h.t[1] * c[1][j] + h.t[2] * c[2][j] + p[j];
  m[15] = 1;
  float out[4] = {0, 0, 0, 7};
  HeadOffset(h, m, out);
  bool ok = out[3] == 7;
  for (int j = 0; j < 3; ++j) ok &= Near(out[j], m[12 + j] - p[j], 2e-3f);
  Check(ok, "head offset is the posed camera less the camera's own position");
  const float len = std::sqrt(out[0] * out[0] + out[1] * out[1] + out[2] * out[2]);
  Check(Near(len, std::sqrt(0.06f * 0.06f + 0.04f * 0.04f + 0.03f * 0.03f), 1e-4f), "a rigid move keeps its length");
}
}  // namespace

int main() {
  TestFixedPoint();
  TestOffset();
  TestRigidInverse();
  TestHeadOffset();
  if (failures) {
    std::printf("%d failure(s)\n", failures);
    return 1;
  }
  std::printf("vr_cockpit_space_test: all passed\n");
  return 0;
}
