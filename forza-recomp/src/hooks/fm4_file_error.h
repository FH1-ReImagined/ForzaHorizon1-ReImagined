#pragma once
// The game's own rule for a fatal file error (#825, src/hooks/fm4_track.cpp).

#include <cstdint>

namespace forza::fm4_track {

// Whether the game's file error check (TU4 sub_82CBF3E0) stops on a result: kind is the file
// object's +8 field (its r3), ok the operation's result (r4, 0 a failure), code the error code
// (r5). A failure stops it only with kind 1 and ERROR_FILE_CORRUPT or ERROR_DISK_CORRUPT
// (1392, 1393), or kind 2 and any code but ERROR_HANDLE_EOF (38), ERROR_IO_INCOMPLETE (996) and
// ERROR_IO_PENDING (997); never with kind 0.
constexpr bool GameFatalFileError(int32_t kind, uint32_t ok, uint32_t code) {
  if (ok) return false;
  if (kind == 1) return code == 1392 || code == 1393;
  if (kind == 2) return code != 38 && code != 996 && code != 997;
  return false;
}

}  // namespace forza::fm4_track
