#pragma once
#include <array>
#include <cstdint>
struct PPCContext;
namespace forza::minimap {
// Only the gameplay HUD record is borrowed for its native material pass.
// Its heading/settings and matrices are restored before the next native user.
class MapScope {
 public:
  MapScope(const PPCContext&, uint8_t* base);
  ~MapScope();
  MapScope(const MapScope&) = delete;
  MapScope& operator=(const MapScope&) = delete;
 private:
  uint8_t* base_ = nullptr;
  uint32_t camera_ = 0;
  std::array<uint8_t, 12> direction_{};
  std::array<uint8_t, 256> matrices_{};
  std::array<uint8_t, 12> applied_direction_{};
  std::array<uint8_t, 256> applied_matrices_{};
};
} // namespace forza::minimap
