// Get In Car on a garage car whose paint job can't be loaded (src/platform/car_livery_fallback.h,
// #922): which failures get the factory paint, and the statement that clears the paint job file.
// Build from the repository root:
//   g++ -std=c++20 -Wall -Wextra -Werror -Iforza-recomp/src forza-recomp/tests/car_livery_fallback_test.cpp
//       -o car-livery-fallback-test && ./car-livery-fallback-test
#include "platform/car_livery_fallback.h"

#include <cstdio>
#include <cstdlib>

namespace clf = forza::car_livery_fallback;
using clf::Action;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}
}  // namespace

int main() {
  // A paint job that loaded (or is still loading) is the game's: nothing changes for cars that
  // work.
  Check(clf::Decide(false, 0) == Action::kGame, "a loaded factory paint is the game's");
  Check(clf::Decide(false, clf::kKeyFile) == Action::kGame, "a loaded paint job file is the game's");
  // A paint job file that failed (the profile has no such file, or it can't be read): factory
  // paint, and the row stops naming it.
  Check(clf::Decide(true, clf::kKeyFile) == Action::kFactory, "a failed paint job file");
  // Any other failure: made again from the row, which is left alone.
  Check(clf::Decide(true, 0) == Action::kFresh, "a failed factory paint");
  Check(clf::Decide(true, 2) == Action::kFresh, "another kind of paint job");

  // The game's own statement (sub_82534848 / TU4 sub_825A32C8).
  Check(clf::ClearLiverySql("Career_Garage", 51) ==
            "UPDATE Career_Garage SET LiveryFileName='' WHERE Id=51",
        "the clearing statement");
  Check(clf::ClearLiverySql("Profile0_Career_Garage", 4294967295u) ==
            "UPDATE Profile0_Career_Garage SET LiveryFileName='' WHERE Id=4294967295",
        "the largest garage id");
  // Only a plain table name goes into it.
  Check(clf::ClearLiverySql("", 1).empty(), "no table");
  Check(clf::ClearLiverySql("Career_Garage; DROP TABLE x", 1).empty(), "no statement in the name");
  Check(clf::ClearLiverySql("Career Garage", 1).empty(), "no space");
  Check(clf::ClearLiverySql("Career_Garage'", 1).empty(), "no quote");
  Check(clf::ClearLiverySql(std::string(65, 'a'), 1).empty(), "no overlong name");
  Check(clf::ValidTable("Career_Garage"), "the garage table");

  Check(clf::kStateFailed == 6, "the failed state");
  Check(clf::kDrivingFlags == 0x80000, "the driving variant's flags");
  std::puts("car_livery_fallback_test: ok");
  return 0;
}
