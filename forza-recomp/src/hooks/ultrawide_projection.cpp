// Copyright (c) 2026 Forza Horizon 1: ReImagined contributors.
// SPDX-License-Identifier: MIT
// The aspect getter supplies both the camera projection and visibility culling.
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/ultrawide.h"
#include "../platform/xr.h"
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <array>
#include <cstring>
#include <limits>

extern "C" REX_FUNC(__imp__sub_823E22C8);
extern "C" REX_FUNC(sub_823E22C8) {
  // Capture argument registers before the original getter changes the context.
  const auto object = ctx.r3.u32;
  const auto return_address = uint32_t(ctx.lr);
  __imp__sub_823E22C8(ctx, base);
  constexpr uint32_t extent_offset = 8;
  constexpr uint32_t extent_bytes = 8;
  if (object > std::numeric_limits<uint32_t>::max() -
                   (extent_offset + extent_bytes - 1)) return;
  std::array<uint32_t, 2> dimensions;
  std::memcpy(dimensions.data(), forza::GuestPtr(base, object + extent_offset), extent_bytes);
  for (auto& dimension : dimensions) dimension = __builtin_bswap32(dimension);
  const bool from_world = return_address == FORZA_ADDR(0x829A48F4);
  const bool from_map = return_address == FORZA_ADDR(0x8263AE18);
  const double display_aspect = forza::ultrawide::Projection(
      from_world, from_map, dimensions[0], dimensions[1], ctx.f1.f64);
  const auto eye_aspect = forza::xr::PosedViewAspect();
  ctx.f1.f64 = eye_aspect > 0 ? eye_aspect : display_aspect;
}
