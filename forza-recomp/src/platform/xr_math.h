// forza - pose and field-of-view maths for the OpenXR path (docs/vr.md, #333), without OpenXR
// headers so tests/xr_math_test.cpp can check it on its own.
//
// OpenXR spaces are right-handed: x right, y up, z backwards (the view looks along -z). The
// game's camera space is left-handed: x right, y up, z forwards (vr_head_pose_math.h). A head
// pose in the recentred LOCAL space becomes a HeadTransform by flipping z on both sides.
#pragma once

#include "vr_head_pose_math.h"

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace forza::xr {

struct Quat {
  float x = 0, y = 0, z = 0, w = 1;
};
struct Vec3 {
  float x = 0, y = 0, z = 0;
};
// An OpenXR pose: orientation and position.
struct Pose {
  Quat q;
  Vec3 p;
};
// XrFovf: radians, left and down negative.
struct Fov {
  float left = 0, right = 0, up = 0, down = 0;
};

inline Quat Mul(const Quat& a, const Quat& b) {
  return {a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y,
          a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x,
          a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w,
          a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z};
}
inline Quat Conj(const Quat& q) { return {-q.x, -q.y, -q.z, q.w}; }
inline Quat Normalize(const Quat& q) {
  const float n = std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w);
  if (n < 1e-6f) return {};
  return {q.x / n, q.y / n, q.z / n, q.w / n};
}
inline Vec3 Rotate(const Quat& q, const Vec3& v) {
  const Quat r = Mul(Mul(q, Quat{v.x, v.y, v.z, 0}), Conj(q));
  return {r.x, r.y, r.z};
}
// Rotation about +y by `radians` (positive turns left).
inline Quat AxisY(float radians) { return {0, std::sin(radians / 2), 0, std::cos(radians / 2)}; }
inline Quat AxisX(float radians) { return {std::sin(radians / 2), 0, 0, std::cos(radians / 2)}; }
inline Quat AxisZ(float radians) { return {0, 0, std::sin(radians / 2), std::cos(radians / 2)}; }

// The heading of an orientation (the rotation about +y that turns -z to where it looks,
// projected on the floor), so a recentre keeps the horizon level.
inline Quat YawOnly(const Quat& q) {
  const Vec3 forward = Rotate(q, {0, 0, -1});
  if (std::fabs(forward.x) < 1e-6f && std::fabs(forward.z) < 1e-6f) return {};  // straight up/down
  return AxisY(std::atan2(-forward.x, -forward.z));
}

// `head` relative to `origin` (both in the same space): origin^-1 * head.
inline Pose Relative(const Pose& origin, const Pose& head) {
  const Quat inv = Conj(origin.q);
  const Vec3 d{head.p.x - origin.p.x, head.p.y - origin.p.y, head.p.z - origin.p.z};
  return {Normalize(Mul(inv, head.q)), Rotate(inv, d)};
}
// origin * local: a pose given relative to `origin`, in origin's space.
inline Pose Compose(const Pose& origin, const Pose& local) {
  const Vec3 d = Rotate(origin.q, local.p);
  return {Normalize(Mul(origin.q, local.q)), {origin.p.x + d.x, origin.p.y + d.y, origin.p.z + d.z}};
}

// The recentre origin for a head pose: where the head is, turned like it on the floor.
inline Pose RecenterOrigin(const Pose& head) { return {YawOnly(head.q), head.p}; }

// The game's head transform for a head pose relative to the recentre origin, with the seat's
// height offset (metres, positive raises the eye).
inline vr::HeadTransform ToGameHead(const Pose& rel, float seat_height) {
  const Quat q = Normalize(rel.q);
  const float xx = q.x * q.x, yy = q.y * q.y, zz = q.z * q.z;
  const float xy = q.x * q.y, xz = q.x * q.z, yz = q.y * q.z;
  const float wx = q.w * q.x, wy = q.w * q.y, wz = q.w * q.z;
  // Column-vector rotation in OpenXR space: v_space = m * v_head.
  const float m[3][3] = {{1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)},
                         {2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)},
                         {2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)}};
  // Flip z on both sides (S m S, S = diag(1, 1, -1)) and transpose for the game's row vectors.
  const float s[3] = {1, 1, -1};
  vr::HeadTransform h;
  for (int i = 0; i < 3; ++i)
    for (int j = 0; j < 3; ++j) h.r[i][j] = s[i] * s[j] * m[j][i];
  h.t[0] = rel.p.x;
  h.t[1] = rel.p.y + seat_height;
  h.t[2] = -rel.p.z;
  return h;
}

// The cockpit view the game draws: a symmetric frustum `vertical` radians tall whose width is
// set by `aspect` = tan(horizontal half-angle) / tan(vertical half-angle), around an axis turned
// `yaw` (positive left) and `pitch` (positive up) from the head's forward. The game's picture
// keeps its pixels (16:9 at the guest's resolution): with an aspect other than the picture's
// the pixels are not square, and none fall outside the frustum.
struct CockpitFrustum {
  float vertical = 0;
  float aspect = 0;
  float yaw = 0, pitch = 0;
  float HalfHorizontal() const { return std::atan(std::tan(vertical / 2) * aspect); }
  // The XrFovf of the picture, relative to the turned axis.
  Fov ImageFov() const {
    const float h = HalfHorizontal();
    return {-h, h, vertical / 2, -vertical / 2};
  }
  // The turn from the head to the drawn axis, as an OpenXR rotation (yaw about +y, then pitch
  // about the turned +x).
  Quat Offset() const { return Normalize(Mul(AxisY(yaw), AxisX(pitch))); }
};

// A test pose (vr::HeadPose: yaw left, pitch up, roll left in degrees; x right, y up, z forward in
// metres, as the game's camera takes them) as an OpenXR pose in the recentred space: ToGameHead of
// it is ToTransform of the test pose.
inline Pose FromHeadPose(const vr::HeadPose& p) {
  constexpr float kDeg = 3.14159265f / 180.0f;
  return {Normalize(Mul(Mul(AxisY(p.yaw * kDeg), AxisX(p.pitch * kDeg)), AxisZ(p.roll * kDeg))),
          {p.x, p.y, -p.z}};
}

// A recentred head pose with a test offset on top (vr_head_pose_test in the VR cockpit): turned by
// the offset's rotation about the recentred axes and moved by its position, as if the head had
// done that too. Lets a scripted look-around run through the real runtime with the headset still.
inline Pose AddOffset(const Pose& rel, const Pose& offset) {
  return {Normalize(Mul(offset.q, rel.q)),
          {rel.p.x + offset.p.x, rel.p.y + offset.p.y, rel.p.z + offset.p.z}};
}

// The yaw (positive left) and pitch (positive up) of where an orientation looks.
inline void YawPitch(const Quat& q, float& yaw, float& pitch) {
  const Vec3 f = Rotate(q, {0, 0, -1});
  yaw = std::atan2(-f.x, -f.z);
  pitch = std::atan2(f.y, std::sqrt(f.x * f.x + f.z * f.z));
}

// The smallest cockpit frustum that covers both eyes' fields of view plus `margin` (radians on
// each side). `eye_rotation` is each eye's orientation relative to the head (identity for the
// parallel displays of a Quest 3; canted displays shift their edges). The union of the eyes is
// usually taller below the horizon than above (Quest 3: 44 up, 55 down), so the drawn axis is
// pitched to its middle instead of drawing a symmetric frustum as tall as the larger side.
// Half-angles are capped at max_half (tan() stays finite) and the height at max_vertical.
inline CockpitFrustum CoverEyes(const Fov eyes[2], const Quat eye_rotation[2], float margin,
                                float max_half, float max_vertical) {
  // The axis: the middle of the eyes' edges (an eye turned left, positive yaw, moves its edges
  // toward negative angles).
  float left = 0, right = 0, up = 0, down = 0;
  for (int i = 0; i < 2; ++i) {
    float yaw, pitch;
    YawPitch(eye_rotation[i], yaw, pitch);
    const float l = eyes[i].left - yaw, r = eyes[i].right - yaw;
    const float u = eyes[i].up + pitch, d = eyes[i].down + pitch;
    left = i ? std::min(left, l) : l;
    right = i ? std::max(right, r) : r;
    up = i ? std::max(up, u) : u;
    down = i ? std::min(down, d) : d;
  }
  CockpitFrustum f;
  f.yaw = -(left + right) / 2;
  f.pitch = (up + down) / 2;
  // The extent: each eye's frustum corners seen from the turned axis (a turned frustum's widest
  // and tallest points are its corners; pitching the axis widens the corners a little).
  const Quat to_axis = Conj(f.Offset());
  float tan_h = 0, tan_v = 0;
  for (int i = 0; i < 2; ++i) {
    const Quat eye = Mul(to_axis, eye_rotation[i]);
    for (float h : {eyes[i].left, eyes[i].right}) {
      for (float v : {eyes[i].up, eyes[i].down}) {
        const Vec3 d = Rotate(eye, {std::tan(h), std::tan(v), -1});
        const float depth = std::max(-d.z, 1e-3f);
        tan_h = std::max(tan_h, std::fabs(d.x) / depth);
        tan_v = std::max(tan_v, std::fabs(d.y) / depth);
      }
    }
  }
  const float half_h = std::clamp(std::atan(tan_h) + margin, 0.05f, max_half);
  const float half_v = std::clamp(std::atan(tan_v) + margin, 0.05f, std::min(max_half, max_vertical / 2));
  f.vertical = 2 * half_v;
  f.aspect = std::tan(half_h) / std::tan(half_v);
  return f;
}

// True stereo (ReIGlue 0396): the part of the cockpit picture (fractions of its width, left to
// right) that an eye's field of view covers, `margin` radians wider on each side. Each eye is
// drawn only there (about 70 % of the picture for a Quest 3's eyes); the cockpit picture covers
// both eyes and the margin. Unlike CoverEyes the eye is not centred: its own frustum, seen from
// the picture's axis.
inline void EyePictureRange(const Fov& eye, const Quat& eye_rotation, const CockpitFrustum& f,
                            float margin, float& lo, float& hi) {
  const Quat to_axis = Mul(Conj(f.Offset()), eye_rotation);
  float min_t = 1e9f, max_t = -1e9f;
  for (float h : {eye.left, eye.right}) {
    for (float v : {eye.up, eye.down}) {
      const Vec3 d = Rotate(to_axis, {std::tan(h), std::tan(v), -1});
      const float depth = std::max(-d.z, 1e-3f);
      min_t = std::min(min_t, d.x / depth);
      max_t = std::max(max_t, d.x / depth);
    }
  }
  const float t = std::tan(f.HalfHorizontal());
  const float limit = 1.5f;  // tangents beyond ~86 degrees: the picture's edge anyway
  lo = std::clamp(0.5f + 0.5f * std::tan(std::clamp(std::atan(min_t) - margin, -limit, limit)) / t,
                  0.0f, 1.0f);
  hi = std::clamp(0.5f + 0.5f * std::tan(std::clamp(std::atan(max_t) + margin, -limit, limit)) / t,
                  0.0f, 1.0f);
}

// The frustum version 1 drew, for comparison (docs/vr.md): a symmetric vertical field of view
// covering the larger half-angle of either axis at the picture's own aspect.
inline CockpitFrustum SymmetricCover(const Fov eyes[2], float picture_aspect, float margin,
                                     float max_vertical) {
  float half_h = 0, half_v = 0;
  for (int i = 0; i < 2; ++i) {
    half_h = std::max({half_h, -eyes[i].left, eyes[i].right});
    half_v = std::max({half_v, eyes[i].up, -eyes[i].down});
  }
  constexpr float kLimit = 1.5f;
  half_h = std::min(half_h + margin, kLimit);
  half_v = std::min(half_v + margin, kLimit);
  const float from_h = std::atan(std::tan(half_h) / std::max(picture_aspect, 0.1f));
  CockpitFrustum f;
  f.vertical = std::min(2 * std::max(half_v, from_h), max_vertical);
  f.aspect = picture_aspect;
  return f;
}

// The renderer's object size cull (hooks/vr_culling.cpp) keeps an item while k d^2 <= D^2 size,
// with k = 3 tan^2 of the view's vertical half-angle: the factor that turns the cockpit's k into
// the in-car camera's own (both vertical fields of view in radians), below 1 for a wider cockpit.
inline float CullScale(float own_vertical, float cockpit_vertical) {
  const float own = std::tan(own_vertical / 2), cockpit = std::tan(cockpit_vertical / 2);
  return cockpit > 1e-4f ? std::min(1.0f, (own * own) / (cockpit * cockpit)) : 1.0f;
}

// Pixels per degree at the centre of a picture `pixels` wide (or tall) drawn over a symmetric
// rectilinear half-angle `half`.
inline float CentrePixelsPerDegree(float pixels, float half) {
  return pixels / 2 / std::tan(half) * 3.14159265f / 180;
}

// Where the eye may go relative to where it was recentred (metres; OpenXR LOCAL: x right, y up,
// +z back). Turning the head 60-90 degrees moves the eyes 8-12 cm aside and 5-10 cm back with the
// neck (the third headset test's trace: up to 12.5 aside, 10.4 back, 5 up). Version 3 held the eye
// in an ellipsoid 10 cm aside, 8 back and 6 up, eased from 0.8 of the way out: every look at a
// door or a mirror held the eye 2-4 cm short of the head, so the cabin moved with the head ("the
// camera moves"). What leaves the cabin is going up, aside and back together, into the roof that
// slopes down toward the doors and the rear window: in the test save's Aventador, 10 cm aside, 8
// back and 5 up cut the roof open with the eye 4 cm below the game's driver's eye, and nothing
// without the up part (docs/vr.md "Version 4"). So version 4 lowers the eye 4 cm (the seat's
// default) and limits sideways and backward motion only far out (18 and 15 cm, eased from 0.85;
// 15 cm back is where the seat begins), while how far it may go up shrinks to nothing as it goes
// aside and back: an ellipse 18 x 15 cm on the floor and a dome over it 6 cm high. Forward
// (toward the wheel) and down are free.
struct HeadBox {
  float side = 0.18f, back = 0.15f, up = 0.06f;
};
// Fraction of each limit the eye moves freely in; beyond it the motion is eased so that the eye
// approaches the limit without a hard stop.
constexpr float kHeadBoxKnee = 0.85f;

// Eases r (a fraction of a limit, >= 0) to at most 1: unchanged up to the knee.
inline float EaseToLimit(float r) {
  if (r <= kHeadBoxKnee) return r;
  const float soft = 1.0f - kHeadBoxKnee;
  return kHeadBoxKnee + soft * std::tanh((r - kHeadBoxKnee) / soft);
}

// How far `rel` is out toward the floor ellipse's edge: 1 on it.
inline float HeadBoxRadius(const Pose& rel, const HeadBox& box) {
  const float x = rel.p.x / std::max(box.side, 1e-3f);
  const float z = std::max(rel.p.z, 0.0f) / std::max(box.back, 1e-3f);
  return std::sqrt(x * x + z * z);
}
// How far up the eye may go at its place on the floor ellipse (radius r after easing): the dome.
inline float HeadBoxUp(float r, const HeadBox& box) { return box.up * std::max(0.0f, 1.0f - r * r); }

// A recentred head pose with its position held in the box: sideways and back eased toward the
// floor ellipse (both scaled alike, so the direction is kept), then up eased toward the dome above
// that place. Unchanged inside the knees.
inline Pose LimitHead(Pose rel, const HeadBox& box) {
  const float r = HeadBoxRadius(rel, box);
  float held = r;
  if (r > kHeadBoxKnee) {
    held = EaseToLimit(r);
    const float scale = held / r;
    rel.p.x *= scale;
    if (rel.p.z > 0) rel.p.z *= scale;
  }
  if (rel.p.y > 0) {
    const float up = HeadBoxUp(held, box);
    rel.p.y = up > 1e-4f ? up * EaseToLimit(rel.p.y / up) : 0.0f;
  }
  return rel;
}

// An eye's pose for a view pose (OpenXR: x right): `eye` 0 the left, 1 the right, `ipd` metres apart.
inline Pose EyePose(const Pose& view, int eye, float ipd) {
  const Vec3 d = Rotate(view.q, {(eye ? 0.5f : -0.5f) * ipd, 0, 0});
  return {view.q, {view.p.x + d.x, view.p.y + d.y, view.p.z + d.z}};
}

// A panel `distance` metres from the recentred eyes in the direction `pitch` radians up (negative:
// down), facing them: its pose relative to the recentre origin (OpenXR quads face +z).
inline Pose PanelPose(float distance, float pitch) {
  const Quat q = AxisX(pitch);
  return {q, Rotate(q, {0, 0, -distance})};
}

// The head transform for a view turned by the frustum's offset (yaw, then pitch, in head space).
inline Pose TurnView(const Pose& head, const CockpitFrustum& f) {
  return {Normalize(Mul(head.q, f.Offset())), head.p};
}

// Yaw (positive left), pitch (positive up) and roll (positive tilts left) in radians of an
// orientation, applied in that order (as vr::HeadPose): for logs.
inline void YawPitchRoll(const Quat& q, float& yaw, float& pitch, float& roll) {
  YawPitch(q, yaw, pitch);
  // Roll: the head's up axis against the up the yaw and pitch alone would give.
  const Quat no_roll = Mul(AxisY(yaw), AxisX(pitch));
  const Vec3 up = Rotate(Mul(Conj(no_roll), q), {0, 1, 0});
  roll = std::atan2(-up.x, up.y);
}

// The headset refresh rate to ask for (XR_FB_display_refresh_rate), 0 for none. `requested` > 0:
// the offered rate nearest to it. 0: one that shows every game frame for the same number of
// refreshes, the lowest such (72 fps on a Quest 3's 72/80/90/120 Hz: 72; 60 fps: 120; 30: 90),
// else the lowest rate at or above the frame rate, else the highest. A game frame held for one
// refresh and the next for two (72 fps at 90 Hz: 4 of every 5 frames once, 1 twice) makes the
// world outside judder while the reprojected cabin stays smooth (the third headset test).
// requested < 0 or no frame rate: leave it to the runtime.
inline float ChooseRefreshRate(const float* rates, uint32_t count, int frame_rate, int requested) {
  if (requested < 0 || count == 0) return 0;
  if (requested > 0) {
    float best = rates[0];
    for (uint32_t i = 1; i < count; ++i)
      if (std::fabs(rates[i] - requested) < std::fabs(best - requested)) best = rates[i];
    return best;
  }
  if (frame_rate <= 0) return 0;
  float multiple = 0, above = 0, highest = 0;
  for (uint32_t i = 0; i < count; ++i) {
    const float r = rates[i];
    highest = std::max(highest, r);
    const float k = std::round(r / float(frame_rate));
    if (k >= 1 && std::fabs(r - k * float(frame_rate)) < 0.5f && (multiple == 0 || r < multiple))
      multiple = r;
    if (r >= float(frame_rate) - 0.5f && (above == 0 || r < above)) above = r;
  }
  return multiple ? multiple : above ? above : highest;
}

// A latency estimate (ns): an exponential moving average of measured samples, clamped.
class LatencyEstimate {
 public:
  LatencyEstimate(int64_t initial, int64_t lo, int64_t hi) : value_(initial), lo_(lo), hi_(hi) {}
  void Add(int64_t sample) {
    if (sample <= 0) return;
    value_ += (std::clamp(sample, lo_, 4 * hi_) - value_) / 8;
  }
  int64_t Value() const { return std::clamp(value_, lo_, hi_); }
  int64_t Raw() const { return value_; }

 private:
  int64_t value_, lo_, hi_;
};

}  // namespace forza::xr
