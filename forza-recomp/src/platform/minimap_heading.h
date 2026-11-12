#pragma once
#include <array>
#include <atomic>
#include <bit>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <optional>

namespace forza::minimap {
struct Heading { float x, z; };
inline std::optional<Heading> CameraHeading(const std::array<float, 16>& view) {
  for (float value : view) if (!std::isfinite(value)) return {};
  // Native row-vector world-to-view: the third column is camera forward.
  const float x = view[2], z = view[10], length = std::hypot(x, z);
  if (!std::isfinite(length) || length < 0.0001f) return {};
  return Heading{x / length, z / length};
}
// The stock HUD map does not pivot on the car. Its camera update (TU4 82584FC0) moves the
// map camera's ground point ahead of the car, so the car sits below the map's centre (#816).
// That offset, in the frame of the heading the native map was built with: forward along the
// heading, right along (heading.z, -heading.x), the map camera's own right axis (82DA2388).
struct LookAhead { float forward, right; };
struct Ground { float x, z; }; // a point on the map's ground plane
inline std::optional<Heading> Flat(float x, float z) {
  const float length = std::hypot(x, z);
  if (!std::isfinite(length) || length < 0.0001f) return {};
  return Heading{x / length, z / length};
}
inline std::optional<LookAhead> MeasureLookAhead(Ground pivot, Ground car, Heading native) {
  const float dx = pivot.x - car.x, dz = pivot.z - car.z;
  const LookAhead offset{dx * native.x + dz * native.z, dx * native.z - dz * native.x};
  if (!std::isfinite(offset.forward) || !std::isfinite(offset.right)) return {};
  return offset;
}
// The ground point of a map turned from `native` to `heading` that keeps the car where the
// stock map draws it: the same offset from the car, turned with the map.
inline Ground TurnPivot(Ground pivot, Heading native, Heading heading, LookAhead offset) {
  const float car_x = pivot.x - offset.forward * native.x - offset.right * native.z;
  const float car_z = pivot.z - offset.forward * native.z + offset.right * native.x;
  return {car_x + offset.forward * heading.x + offset.right * heading.z,
          car_z + offset.forward * heading.z - offset.right * heading.x};
}
// The camera update builds the map camera around the car before it moves the ground point
// (82DA2388: world row 3 = car - height * row 2), so the car is read back from those rows.
inline std::optional<Ground> CarFromMapCamera(const std::array<float, 16>& world, float height) {
  const Ground car{world[12] + height * world[8], world[14] + height * world[10]};
  if (!std::isfinite(car.x) || !std::isfinite(car.z)) return {};
  return car;
}
// The newest offset per map camera, written on the game thread and read by the map pass.
class LookAheadMailbox {
 public:
  void Publish(uint32_t camera, LookAhead offset) {
    sequence_.fetch_add(1, std::memory_order_acq_rel);
    camera_.store(camera, std::memory_order_relaxed);
    vector_.store(uint64_t(std::bit_cast<uint32_t>(offset.forward)) << 32 |
        std::bit_cast<uint32_t>(offset.right), std::memory_order_relaxed);
    sequence_.fetch_add(1, std::memory_order_release);
  }
  std::optional<LookAhead> Read(uint32_t camera) const {
    const auto before = sequence_.load(std::memory_order_acquire);
    if (before & 1) return {};
    const auto owner = camera_.load(std::memory_order_relaxed);
    const auto vector = vector_.load(std::memory_order_relaxed);
    std::atomic_thread_fence(std::memory_order_acquire);
    if (before != sequence_.load(std::memory_order_acquire) || !camera || owner != camera) return {};
    return LookAhead{std::bit_cast<float>(uint32_t(vector >> 32)),
                     std::bit_cast<float>(uint32_t(vector))};
  }
 private:
  std::atomic<uint32_t> sequence_{0}, camera_{0};
  std::atomic<uint64_t> vector_{0};
};
inline LookAheadMailbox look_ahead;
inline uint64_t NowMs() {
  return uint64_t(std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::steady_clock::now().time_since_epoch()).count());
}
// The native game-thread view submission is the sole writer. Render reads a
// bounded snapshot instead of racing the game's live director matrix.
class HeadingMailbox {
 public:
  void Publish(const std::array<float, 16>& view, uint32_t world, uint64_t now) {
    const auto heading = CameraHeading(view);
    sequence_.fetch_add(1, std::memory_order_acq_rel);
    vector_.store(heading ? (uint64_t(std::bit_cast<uint32_t>(heading->x)) << 32) |
        std::bit_cast<uint32_t>(heading->z) : 0, std::memory_order_relaxed);
    world_.store(heading ? world : 0, std::memory_order_relaxed);
    time_.store(now, std::memory_order_relaxed);
    sequence_.fetch_add(1, std::memory_order_release);
  }
  std::optional<Heading> Read(uint32_t world, uint64_t now) const {
    const auto before = sequence_.load(std::memory_order_acquire);
    if (before & 1) return {};
    const auto vector = vector_.load(std::memory_order_relaxed);
    const auto owner = world_.load(std::memory_order_relaxed);
    const auto time = time_.load(std::memory_order_relaxed);
    std::atomic_thread_fence(std::memory_order_acquire);
    if (before != sequence_.load(std::memory_order_acquire) || !world || owner != world ||
        now < time || now - time > 250) return {};
    return Heading{std::bit_cast<float>(uint32_t(vector >> 32)),
                   std::bit_cast<float>(uint32_t(vector))};
  }
 private:
  std::atomic<uint32_t> sequence_{0}, world_{0};
  std::atomic<uint64_t> vector_{0}, time_{0};
};
inline HeadingMailbox heading;
} // namespace forza::minimap
