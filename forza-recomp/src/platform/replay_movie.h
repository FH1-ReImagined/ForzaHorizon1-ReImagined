// forza - the texture a replay movie's frames are read from (#880, src/hooks/replay_movie.cpp).
#pragma once

#include "readback_wait.h"

#include <cstdint>

namespace forza::replay_movie {

// The range of the capture texture for readback_resolve_wait_range (readback_wait::Pack; 0 for
// none): `scratch` is the address of the renderer's scratch block, where the texture's memory
// starts, as the game holds it (a view of physical memory, or 0 before the renderer has one).
// The texture is tiled, 32 bits a pixel: its width and height are padded to whole 32x32 tiles
// (640x360 takes 640x384, 1280x720 takes 1280x736). The movie's sizes are those two; anything
// else is refused.
constexpr uint64_t TextureRange(uint32_t scratch, uint32_t width, uint32_t height) {
  const uint32_t physical = scratch & 0x1FFFFFFF;
  if (!physical || !width || !height || width > 1280 || height > 720) return 0;
  const uint32_t bytes = ((width + 31) & ~31u) * ((height + 31) & ~31u) * 4;
  if (uint64_t(physical) + bytes > 0x20000000) return 0;
  return readback_wait::Pack(physical, bytes);
}

}  // namespace forza::replay_movie
