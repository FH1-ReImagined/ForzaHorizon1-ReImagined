#pragma once
// An imported FM4 circuit mounted into the game's folder (#648, src/hooks/fm4_track_mount.cpp).

#include <string>

namespace rex::filesystem {
class VirtualFileSystem;
}

namespace forza::fm4_mount {

// Mounts the circuit named by the fm4_track_dir setting (none: nothing happens) and gives the
// converted track's switches their defaults. Before the game opens its first file.
void Install(rex::filesystem::VirtualFileSystem* vfs);

// game:\media\tracks\<track> of the mounted circuit, "" when none is mounted.
const std::string& TrackPath();

}  // namespace forza::fm4_mount
