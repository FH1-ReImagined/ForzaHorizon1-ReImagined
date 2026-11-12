#pragma once
#include <array>
#include <cstdint>
struct PPCContext;
namespace forza::minimap {
// Wraps the HUD map camera update: reads back how far ahead of the car the native map
// centres (look_ahead in minimap_heading.h) so MapScope can turn the map about the car.
class CameraUpdate {
 public:
  explicit CameraUpdate(uint8_t* base);
  ~CameraUpdate();
  CameraUpdate(const CameraUpdate&) = delete;
  CameraUpdate& operator=(const CameraUpdate&) = delete;
 private:
  uint8_t* base_ = nullptr;
  uint32_t camera_ = 0;
  std::array<uint8_t, 12> pivot_{};
  std::array<uint8_t, 32> rows_{};
};
// Only the gameplay HUD record is borrowed for its native material pass.
// Its ground point, heading and matrices are restored before the next native user.
class MapScope {
 public:
  MapScope(const PPCContext&, uint8_t* base);
  ~MapScope();
  MapScope(const MapScope&) = delete;
  MapScope& operator=(const MapScope&) = delete;
 private:
  uint8_t* base_ = nullptr;
  uint32_t camera_ = 0;
  std::array<uint8_t, 12> pivot_{};
  std::array<uint8_t, 12> direction_{};
  std::array<uint8_t, 256> matrices_{};
  std::array<uint8_t, 12> applied_pivot_{};
  std::array<uint8_t, 12> applied_direction_{};
  std::array<uint8_t, 256> applied_matrices_{};
};
} // namespace forza::minimap
