#pragma once
// A world past 8,192 m: positions kept with 17 fraction bits instead of 18
// (src/hooks/wide_world.cpp).

namespace forza::wide_world {

// Reads the FORZA_WIDE_WORLD switch. Once, before the game runs and after
// forza::fm4_mount::Install has given the mounted world's switches their defaults.
void Configure();

bool Enabled();

}  // namespace forza::wide_world
