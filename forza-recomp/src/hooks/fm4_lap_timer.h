#pragma once
// The lap timer on a converted FM4 circuit (#648, src/hooks/fm4_lap_timer.cpp).

#include <cstdint>
#include <filesystem>

namespace forza::fm4_lap {

// Times the circuit of `lap_file` (lap.txt); counted laps are added to `laps_file` (none: not
// kept). fm4_track_mount.cpp calls it for a mounted circuit.
void Configure(const std::filesystem::path& lap_file, const std::filesystem::path& laps_file);

// Once a world update (hooks/telemetry.cpp): `driving` the world is loaded and not paused,
// `rewinding` the game's rewind is on.
void WorldFrame(uint8_t* base, bool driving, bool rewinding);

}  // namespace forza::fm4_lap
