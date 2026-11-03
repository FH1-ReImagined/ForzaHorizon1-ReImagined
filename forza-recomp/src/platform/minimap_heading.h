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
