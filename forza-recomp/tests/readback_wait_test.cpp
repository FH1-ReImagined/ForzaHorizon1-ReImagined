// The owners of readback_resolve_wait_range (src/platform/readback_wait.h, #552, #576, #665):
// one owner's release never drops another's range, and the setting covers all of them.
//   g++ -std=c++20 -O2 -Wall -Wextra -Werror -I forza-recomp/src forza-recomp/tests/readback_wait_test.cpp
#include "platform/readback_wait.h"

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

  if (failures) return EXIT_FAILURE;
  std::printf("readback_wait: all checks passed\n");
  return EXIT_SUCCESS;
}
