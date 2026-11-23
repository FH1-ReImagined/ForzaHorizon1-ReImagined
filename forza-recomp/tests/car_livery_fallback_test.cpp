// A garage car whose paint job can't be loaded (src/platform/car_livery_fallback.h, #922): which
// failures get the factory paint, which ones also stop the save naming the file, and the
// statements used for that.
// Build from the repository root:
//   g++ -std=c++20 -Wall -Wextra -Werror -Iforza-recomp/src forza-recomp/tests/car_livery_fallback_test.cpp
//       -o car-livery-fallback-test && ./car-livery-fallback-test
#include "platform/car_livery_fallback.h"

#include <cstdio>
#include <cstdlib>

namespace clf = forza::car_livery_fallback;
using clf::Action;
using clf::FileState;

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
  // work, whatever the file system says.
  Check(clf::Decide(false, 0, FileState::kUnknown) == Action::kGame, "a loaded factory paint");
  Check(clf::Decide(false, clf::kKeyFile, FileState::kPresent) == Action::kGame,
        "a loaded paint job file");
  Check(clf::Decide(false, clf::kKeyFile, FileState::kMissing) == Action::kGame,
        "a loaded paint job is never detached");
  // A paint job file that is gone: factory paint, and the row stops naming it.
  Check(clf::Decide(true, clf::kKeyFile, FileState::kMissing) == Action::kDetach, "a missing file");
  Check(clf::Decide(true, clf::kKeyFile, FileState::kEmpty) == Action::kDetach, "an empty file");
  // A file that is there (a bad header, a read error) or that can't be checked stays in the
  // save: factory paint for now only.
  Check(clf::Decide(true, clf::kKeyFile, FileState::kPresent) == Action::kFactory,
        "an existing file that failed is never detached");
  Check(clf::Decide(true, clf::kKeyFile, FileState::kUnknown) == Action::kFactory,
        "a file whose profile isn't mounted is never detached");
  // Any other failure: factory paint, the row is left alone.
  Check(clf::Decide(true, 0, FileState::kMissing) == Action::kFactory, "a failed factory paint");
  Check(clf::Decide(true, 2, FileState::kUnknown) == Action::kFactory, "another kind of paint job");

  // The game's own statement (sub_82534848 / TU4 sub_825A32C8), and the check that it took.
  Check(clf::ClearLiverySql("Career_Garage", 51) ==
            "UPDATE Career_Garage SET LiveryFileName='' WHERE Id=51",
        "the clearing statement");
  Check(clf::ClearLiverySql("Profile0_Career_Garage", 4294967295u) ==
            "UPDATE Profile0_Career_Garage SET LiveryFileName='' WHERE Id=4294967295",
        "the largest garage id");
  Check(clf::ClearedSql("Career_Garage", 60) ==
            "SELECT COUNT(*) FROM Career_Garage WHERE Id=60 AND LiveryFileName=''",
        "the check");
  Check(clf::ColumnSql("Career_Garage", "CarId", 60) == "SELECT CarId FROM Career_Garage WHERE Id=60",
        "a column");
  // Only a plain table name goes into them; a bad one gives no statement (so nothing is run and
  // nothing is reported as changed).
  Check(clf::ClearLiverySql("", 1).empty(), "no table");
  Check(clf::ClearLiverySql("Career_Garage; DROP TABLE x", 1).empty(), "no statement in the name");
  Check(clf::ClearLiverySql("Career Garage", 1).empty(), "no space");
  Check(clf::ClearLiverySql("Career_Garage'", 1).empty(), "no quote");
  Check(clf::ClearLiverySql(std::string(65, 'a'), 1).empty(), "no overlong name");
  Check(clf::ClearedSql("x;y", 1).empty(), "no check on a bad name");
  Check(clf::ColumnSql("Career_Garage", "CarId FROM x;", 1).empty(), "no bad column");
  Check(clf::ValidTable("Career_Garage"), "the garage table");

  // The profile root of the key's file name.
  Check(clf::ProfileRoot("E030000004B490FA:\\Liveries\\60.liv") == "E030000004B490FA", "the root");
  Check(clf::ProfileRoot("Liveries\\60.liv").empty(), "no root");
  Check(clf::ProfileRoot(":\\Liveries\\60.liv").empty(), "an empty root");
  Check(clf::ProfileRoot("E03:Liveries").empty(), "no backslash after the root");
  Check(clf::ProfileRoot("E03:\\..\\x.liv").empty(), "no ..");
  Check(clf::ProfileRoot("a b:\\x.liv").empty(), "no space in the root");

  Check(clf::kStateFailed == 6, "the failed state");
  Check(clf::kDrivingFlags == 0x80000, "the driving variant's flags");
  std::puts("car_livery_fallback_test: ok");
  return 0;
}
