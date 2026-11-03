// forza - the car, class/PI, event and place of the Discord status (#527, discord_details.cpp).
#pragma once
#include <cstdint>
#include <rex/ppc/context.h>

namespace forza::discord {
// Call once a frame on the main loop thread after the world update; samples every 2 s.
void WorldFrame(const PPCContext& ctx, uint8_t* base);
}  // namespace forza::discord
