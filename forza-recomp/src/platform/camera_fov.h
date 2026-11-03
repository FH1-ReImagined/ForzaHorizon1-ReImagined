#pragma once

#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <optional>

namespace forza::camera {
inline std::atomic<uint32_t> camera_world{0};
constexpr float kPi = 3.14159265358979323846f;
constexpr float Radians(float degrees) { return degrees * (kPi / 180.0f); }
constexpr bool ValidBaseFov(int degrees) {
  return degrees == 0 || (degrees >= 40 && degrees <= 90);
}
inline uint32_t Read32(const uint8_t* p) {
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}
inline float ReadFloat(const uint8_t* p) {
  const auto bits = Read32(p);
  float value;
  std::memcpy(&value, &bits, 4);
  return value;
}
inline bool LiveCameraWorld(const uint8_t* world) {
  if (!world || world[0x0C] || !world[0x0D]) return false;
  const float scale = ReadFloat(world + 0x28);
  return std::isfinite(scale) && scale > 0.0f;
}

enum class Kind { kOther, kChaseNear, kChaseFar, kCockpit, kHood, kBumper, kBlend };
struct Settings {
  int chase_near = 0, chase_far = 0, cockpit = 0, hood = 0, bumper = 0;
  bool Stock() const { return !(chase_near || chase_far || cockpit || hood || bumper); }
  int Base(Kind kind) const {
    switch (kind) {
      case Kind::kChaseNear: return chase_near;
      case Kind::kChaseFar: return chase_far;
      case Kind::kCockpit: return cockpit;
      case Kind::kHood: return hood;
      case Kind::kBumper: return bumper;
      default: return 0;
    }
  }
};
struct Vtables {
  uint32_t chase_near, chase_far, cockpit, hood, bumper, blend;
  Kind Classify(uint32_t table) const {
    if (table == chase_near) return Kind::kChaseNear;
    if (table == chase_far) return Kind::kChaseFar;
    if (table == cockpit) return Kind::kCockpit;
    if (table == hood) return Kind::kHood;
    if (table == bumper) return Kind::kBumper;
    if (table == blend) return Kind::kBlend;
    return Kind::kOther;
  }
};
struct Node {
  Kind kind = Kind::kOther;
  float base = 0, scale = 1, t = 0;
  uint32_t from = 0, to = 0;
};

// Memory(address, byte_count) returns a readable guest span, or null. Unknown
// classes are leaves: do not interpret their different object layouts.
template <class Memory>
std::optional<Node> ReadNode(uint32_t address, const Memory& memory, const Vtables& tables) {
  if (address > UINT32_MAX - 304) return std::nullopt;
  const auto* head = memory(address, 4);
  if (!head) return std::nullopt;
  Node node;
  node.kind = tables.Classify(Read32(head));
  if (node.kind == Kind::kOther) return node;
  if (node.kind == Kind::kBlend) {
    const auto* blend = memory(address + 192, 12);
    if (!blend) return std::nullopt;
    node.from = Read32(blend);
    node.to = Read32(blend + 4);
    node.t = ReadFloat(blend + 8);
    if (!std::isfinite(node.t) || node.t < 0 || node.t > 1) return std::nullopt;
  } else if (node.kind == Kind::kChaseNear || node.kind == Kind::kChaseFar) {
    const auto* config = memory(address + 192, 5);
    if (!config) return std::nullopt;
    const auto settings = Read32(config);
    if (settings > UINT32_MAX - 28) return std::nullopt;
    const auto* base = memory(settings + 28, 4);
    if (!base) return std::nullopt;
    node.base = Radians(ReadFloat(base));
    node.scale = config[4] ? 0.5f : 1.0f;
  } else {
    const auto* base = memory(address + 272, 4);
    const auto* split = memory(address + 304, 1);
    if (!base || !split) return std::nullopt;
    node.base = ReadFloat(base);
    node.scale = *split ? 0.5f : 1.0f;
  }
  if (node.kind != Kind::kBlend &&
      (!std::isfinite(node.base) || node.base <= 0 || node.base >= kPi)) return std::nullopt;
  return node;
}

namespace detail {
template <class Reader>
std::optional<float> Offset(uint32_t address, const Settings& settings, const Reader& read,
                           std::array<uint32_t, 8>& ancestors, size_t depth, unsigned& budget) {
  if (!address || depth == ancestors.size() || !budget) return std::nullopt;
  --budget;
  for (size_t i = 0; i < depth; ++i)
    if (ancestors[i] == address) return std::nullopt;
  ancestors[depth] = address;
  const auto node = read(address);
  if (!node) return std::nullopt;
  if (node->kind != Kind::kBlend) {
    const int selected = settings.Base(node->kind);
    if (!selected || !ValidBaseFov(selected)) return 0.0f;
    return (Radians(float(selected)) - node->base) * node->scale;
  }
  const auto from = Offset(node->from, settings, read, ancestors, depth + 1, budget);
  const auto to = Offset(node->to, settings, read, ancestors, depth + 1, budget);
  if (!from || !to) return std::nullopt;
  return std::fma(*to - *from, node->t, *from);
}
}  // namespace detail

// Add only the selected base adjustment to the game's finished FOV. CameraPhysics
// widening survives. No director/camera bytes are written, so repeated readers
// never accumulate it. A blend interpolates endpoint adjustments using the same
// t as the original getter; an unrelated camera contributes zero.
template <class Reader>
float AdjustedFov(float original, uint32_t active, const Settings& settings,
                  const Reader& read, bool live_world = true, bool vr_active = false) {
  if (settings.Stock() || !live_world || vr_active ||
      !std::isfinite(original) || original <= 0 || original >= kPi) return original;
  std::array<uint32_t, 8> ancestors{};
  unsigned budget = 31;
  const auto offset = detail::Offset(active, settings, read, ancestors, 0, budget);
  if (!offset || *offset == 0) return original;
  const float result = original + *offset;
  return std::isfinite(result) && result > 0 && result < kPi ? result : original;
}
}  // namespace forza::camera
