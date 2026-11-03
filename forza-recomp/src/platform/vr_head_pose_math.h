// forza - head pose math for the VR prototype (docs/vr.md, #333).
//
// The game's view matrix (camera director + 0xF0, docs/vr.md) is world-to-camera in
// Direct3D's row-vector convention: rows 0-2 hold the camera's right, up and forward axes as
// columns, row 3 the translation. Camera space is left-handed: x right, y up, z forward.
// A head pose is a rotation and an offset of the eye in that camera space; the posed view
// is V * inverse(H), where H moves head space into camera space.
#pragma once

#include <cmath>

namespace forza::vr {

struct HeadPose {
  float yaw = 0;    // degrees, positive turns the head left
  float pitch = 0;  // degrees, positive looks up
  float roll = 0;   // degrees, positive tilts the head left (counterclockwise)
  float x = 0;      // metres along the camera's right axis
  float y = 0;      // metres up
  float z = 0;      // metres forward
  bool IsIdentity() const {
    return yaw == 0 && pitch == 0 && roll == 0 && x == 0 && y == 0 && z == 0;
  }
};

// Rows of the head's rotation in camera space: x_camera = x_head * R (roll, then pitch,
// then yaw, so yaw turns about the car's up axis and pitch about the turned head's right).
inline void HeadRotation(const HeadPose& p, float r[3][3]) {
  constexpr float kDeg = 3.14159265358979f / 180.0f;
  const float cy = std::cos(p.yaw * kDeg), sy = std::sin(p.yaw * kDeg);
  const float cp = std::cos(p.pitch * kDeg), sp = std::sin(p.pitch * kDeg);
  const float cr = std::cos(p.roll * kDeg), sr = std::sin(p.roll * kDeg);
  const float yaw[3][3] = {{cy, 0, sy}, {0, 1, 0}, {-sy, 0, cy}};
  const float pitch[3][3] = {{1, 0, 0}, {0, cp, -sp}, {0, sp, cp}};
  const float roll[3][3] = {{cr, sr, 0}, {-sr, cr, 0}, {0, 0, 1}};
  float rp[3][3];
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j)
      rp[i][j] = roll[i][0] * pitch[0][j] + roll[i][1] * pitch[1][j] + roll[i][2] * pitch[2][j];
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j)
      r[i][j] = rp[i][0] * yaw[0][j] + rp[i][1] * yaw[1][j] + rp[i][2] * yaw[2][j];
}

// A head pose as a rotation (rows, x_camera = x_head * r) and an offset t in camera space: H.
struct HeadTransform {
  float r[3][3] = {{1, 0, 0}, {0, 1, 0}, {0, 0, 1}};
  float t[3] = {0, 0, 0};
};

inline HeadTransform ToTransform(const HeadPose& p) {
  HeadTransform h;
  HeadRotation(p, h.r);
  h.t[0] = p.x, h.t[1] = p.y, h.t[2] = p.z;
  return h;
}

// view[16]: the row-major 4x4 view matrix, changed in place to view * inverse(H).
inline void ApplyHeadTransform(float view[16], const HeadTransform& h) {
  const auto& r = h.r;
  // inverse(H) = [R^T 0; -t R^T 1]
  float inv[4][4] = {};
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) inv[i][j] = r[j][i];
  const float* t = h.t;
  for (int j = 0; j < 3; ++j)
    inv[3][j] = -(t[0] * r[j][0] + t[1] * r[j][1] + t[2] * r[j][2]);
  inv[3][3] = 1;
  float out[16];
  for (int i = 0; i < 4; ++i)
    for (int j = 0; j < 4; ++j)
      out[i * 4 + j] = view[i * 4 + 0] * inv[0][j] + view[i * 4 + 1] * inv[1][j] +
                       view[i * 4 + 2] * inv[2][j] + view[i * 4 + 3] * inv[3][j];
  for (int k = 0; k < 16; ++k) view[k] = out[k];
}

inline void ApplyHeadPose(float view[16], const HeadPose& p) { ApplyHeadTransform(view, ToTransform(p)); }

}  // namespace forza::vr
