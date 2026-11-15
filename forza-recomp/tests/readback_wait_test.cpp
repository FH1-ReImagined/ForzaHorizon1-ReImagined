// The owners of readback_resolve_wait_range (src/platform/readback_wait.h, #552, #576, #665,
// #880): one owner's release never drops another's range, and the setting covers all of them.
// And the range of a replay movie's capture texture (src/platform/replay_movie.h).
//   g++ -std=c++20 -O2 -Wall -Wextra -Werror -I forza-recomp/src forza-recomp/tests/readback_wait_test.cpp
#include "platform/readback_wait.h"
#include "platform/replay_movie.h"

#include <cstdio>
#include <cstdlib>

using forza::readback_wait::Owner;
using forza::readback_wait::Pack;
using forza::readback_wait::Ranges;

namespace {
int failures = 0;
void Check(bool ok, const char* what) {
  if (!ok) {
    std::printf("FAIL: %s\n", what);
    ++failures;
  }
}
}  // namespace

int main() {
  // The TU4 targets: the front buffers (Big Shot) and the world depth (photo mode's focus).
  const uint64_t front = Pack(0x1BDB1000, 0x1C149000 + 0x398000 - 0x1BDB1000);
  const uint64_t depth = Pack(0x1DAC5000, 0x398000);
  const uint64_t all = Pack(0, 0x20000000);

  Check(Pack(0x1DAC5000, 0) == 0, "an empty range is none");
  Check(Pack(0x1DAC5000, 0x398000) == (0x1DAC5000ull << 32 | 0x398000), "the setting's format");

  Ranges r;
  Check(r.Set(Owner::kPhotoFocus, depth) == depth, "one owner: its range");
  Check(r.Set(Owner::kPhotoFocus, depth) == depth, "asked again: the same");
  Check(r.Set(Owner::kPhotoFocus, 0) == 0, "released: none");

  Check(r.Set(Owner::kBigShot, front) == front, "Big Shot alone");
  const uint64_t both = r.Set(Owner::kPhotoFocus, depth);
  Check(both == Pack(0x1BDB1000, 0x1DAC5000 + 0x398000 - 0x1BDB1000), "both: the cover");
  Check(r.Set(Owner::kPhotoFocus, 0) == front, "focus released: Big Shot's range stays");
  Check(r.Set(Owner::kPhotoFocus, depth) == both, "focus again");
  Check(r.Set(Owner::kBigShot, 0) == depth, "Big Shot done: focus's range stays");

  Check(r.Set(Owner::kOnlinePhoto, all) == all, "the online shutter: all of physical memory");
  Check(r.Set(Owner::kOnlinePhoto, 0) == depth, "back in the camera: focus's range");
  Check(r.Set(Owner::kPhotoFocus, 0) == 0, "everyone released: none");

  // A paint job drawn at 2x (#680) holds all of physical memory while it is drawn; the focus's
  // range comes back when it is done.
  Check(r.Set(Owner::kPhotoFocus, depth) == depth, "focus");
  Check(r.Set(Owner::kLiveryBake, all) == all, "a paint job drawn at 2x: all of physical memory");
  Check(r.Set(Owner::kLiveryBake, 0) == depth, "paint job done: focus's range stays");
  Check(r.Set(Owner::kPhotoFocus, 0) == 0, "released: none");

  // A replay movie (#880): the capture texture starts at the renderer's scratch block, tiled, so
  // its height is padded to 32 rows. WEB is 640x360 and LARGE 1280x720.
  using forza::replay_movie::TextureRange;
  const uint32_t scratch = 0xBE759000;  // a view of physical 0x1E759000
  const uint64_t web = TextureRange(scratch, 640, 360);
  const uint64_t large = TextureRange(scratch, 1280, 720);
  Check(web == Pack(0x1E759000, 640 * 384 * 4), "a WEB movie: 640x384 tiles of 32 bits");
  Check(large == Pack(0x1E759000, 1280 * 736 * 4), "a LARGE movie: 1280x736");
  Check(TextureRange(0x1E759000, 640, 360) == web, "a physical address is taken as it is");
  Check(TextureRange(0, 640, 360) == 0, "no scratch block: none");
  Check(TextureRange(scratch, 0, 360) == 0 && TextureRange(scratch, 640, 0) == 0, "no size: none");
  Check(TextureRange(scratch, 1312, 720) == 0 && TextureRange(scratch, 1280, 721) == 0,
        "larger than a LARGE movie: none");
  Check(TextureRange(0xBFFFF000, 640, 360) == 0, "past the end of physical memory: none");
  Check(r.Set(Owner::kPhotoFocus, depth) == depth, "focus");
  Check(r.Set(Owner::kReplayMovie, web) == Pack(0x1DAC5000, 0x1E759000 + 640 * 384 * 4 - 0x1DAC5000),
        "a movie with the focus held: the cover");
  Check(r.Set(Owner::kPhotoFocus, 0) == web, "focus released: the movie's texture stays");
  Check(r.Set(Owner::kReplayMovie, 0) == 0, "the movie done: none");

  if (failures) return EXIT_FAILURE;
  std::printf("readback_wait: all checks passed\n");
  return EXIT_SUCCESS;
}
