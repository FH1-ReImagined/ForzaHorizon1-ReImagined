#pragma once
// A converted FM4 circuit's scenery further ahead of the car (#824, src/hooks/fm4_track.cpp):
// the zones the game's second streaming centre asks for, a hex and its six neighbours ahead of
// the car. The grid is FH1's own (a <track>_NN.hex file, tools/fm4_pvs.py HexGrid: flat-topped
// hexes of a radius in columns 1.5 radii apart, odd columns half a hex lower).

#include <array>
#include <cmath>
#include <cstdint>

namespace forza::fm4_lookahead {

struct Grid {
  float radius = 0, origin_x = 0, origin_z = 0;
  int32_t columns = 0, rows = 0;
  bool Valid() const {
    return radius > 1.0f && radius < 100000.0f && columns > 0 && rows > 0 && columns < 4096 &&
           rows < 4096 && std::isfinite(origin_x) && std::isfinite(origin_z);
  }
  // The distance between the centres of two neighbouring hexes.
  float Step() const { return std::sqrt(3.0f) * radius; }
  void Centre(int32_t column, int32_t row, float& x, float& z) const {
    const float h = Step();
    x = origin_x + (1.0f + 1.5f * float(column)) * radius;
    z = origin_z + h * 0.5f + h * float(row) + ((column & 1) ? h * 0.5f : 0.0f);
  }
  // The cell whose centre is nearest to (x, z); false outside the grid.
  bool CellOf(float x, float z, int32_t& column, int32_t& row) const {
    if (!Valid() || !std::isfinite(x) || !std::isfinite(z)) return false;
    const int32_t c0 = int32_t(std::floor((x - origin_x - radius) / (1.5f * radius)));
    const int32_t r0 = int32_t(std::floor((z - origin_z) / Step()));
    float best = -1.0f;
    for (int32_t c = c0 - 1; c <= c0 + 1; ++c) {
      for (int32_t r = r0 - 1; r <= r0 + 1; ++r) {
        if (c < 0 || c >= columns || r < 0 || r >= rows) continue;
        float cx, cz;
        Centre(c, r, cx, cz);
        const float d = (cx - x) * (cx - x) + (cz - z) * (cz - z);
        if (best < 0.0f || d < best) {
          best = d;
          column = c;
          row = r;
        }
      }
    }
    if (best < 0.0f) return false;
    // A point outside the grid has a nearest cell too: only one it lies in counts.
    return best <= radius * radius;
  }
};

// A cell and the (up to six) cells around it, the cell first: odd columns sit half a hex lower,
// so an odd column's side neighbours are in rows r and r + 1, an even one's in r - 1 and r.
// The number of cells written (those inside the grid).
inline int Around(const Grid& grid, int32_t column, int32_t row,
                  std::array<std::array<int32_t, 2>, 7>& out) {
  int n = 0;
  auto add = [&](int32_t c, int32_t r) {
    if (c >= 0 && c < grid.columns && r >= 0 && r < grid.rows) out[n++] = {c, r};
  };
  add(column, row);
  add(column, row - 1);
  add(column, row + 1);
  const int32_t side = (column & 1) ? 0 : -1;
  for (int32_t dc : {-1, 1}) {
    add(column + dc, row + side);
    add(column + dc, row + side + 1);
  }
  return n;
}

// The hex the second centre sits on: the one `steps` hexes ahead of the car along (dx, dz), its
// direction of travel. False when the car has no direction or the point is outside the grid.
inline bool AheadCell(const Grid& grid, float x, float z, float dx, float dz, float steps,
                      int32_t& column, int32_t& row) {
  const float length = std::sqrt(dx * dx + dz * dz);
  if (!(length > 1e-3f)) return false;
  const float reach = steps * grid.Step();
  return grid.CellOf(x + dx / length * reach, z + dz / length * reach, column, row);
}

// Whether the second centre is on: at speed, with room in the game's pools. It comes on at
// `on_speed` and stays on down to `off_speed` (m/s), so a car near one speed does not load and
// drop zones in turn; it goes off when more than `off_share` of a pool is in use and comes on
// only below `on_share` of both.
struct Limits {
  float on_speed = 25.0f, off_speed = 15.0f;    // 90 and 54 km/h
  float on_share = 0.60f, off_share = 0.85f;
};
inline bool Engaged(bool was_on, float speed, float blocks_share, float instances_share,
                    const Limits& limits = {}) {
  const float share = blocks_share > instances_share ? blocks_share : instances_share;
  if (!std::isfinite(speed) || !std::isfinite(share)) return false;
  if (was_on) return speed >= limits.off_speed && share <= limits.off_share;
  return speed >= limits.on_speed && share <= limits.on_share;
}

}  // namespace forza::fm4_lookahead
