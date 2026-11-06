// The club garage's shared cars named from the game's car list (src/platform/club_garage.h,
// #697): which query strings get the second statement, and how it is appended. Build from the
// repository root:
//   g++ -std=c++20 -Wall -Wextra -Werror -Iforza-recomp/src forza-recomp/tests/club_garage_test.cpp
//       -o club-garage-test && ./club-garage-test
#include "platform/club_garage.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <string_view>

namespace cg = forza::club_garage;

namespace {
void Check(bool ok, const char* what) {
  if (!ok) {
    std::fprintf(stderr, "FAIL: %s\n", what);
    std::exit(1);
  }
}

size_t Count(std::string_view s, std::string_view part) {
  size_t n = 0;
  for (size_t at = s.find(part); at != std::string_view::npos; at = s.find(part, at + 1)) ++n;
  return n;
}
}  // namespace

int main() {
  // The game's insert (TU4 has IsRally after BaseCost; the disc version does not), shortened.
  const std::string insert =
      "INSERT INTO [Clan_Garage] ([SharedId], [Owner], [SharedDate], [LastUsedDate], [ModelID]) "
      "VALUES ( '12' , 'DriverA' , '0' , '0' , '1598' );";

  Check(cg::IsSharedCarInsert(insert.c_str()), "the game's insert");
  Check(!cg::IsSharedCarInsert(nullptr), "no string");
  Check(!cg::IsSharedCarInsert("SELECT Garage.* FROM Clan_Garage AS Garage"), "the garage's list");
  Check(!cg::IsSharedCarInsert("UPDATE Clan_Garage SET HasNetworkTexture=1 WHERE SharedID=12"),
        "the thumbnail's update");
  Check(!cg::IsSharedCarInsert("INSERT INTO [Garage] ([Id]) VALUES ('1');"), "another table");
  Check(!cg::IsSharedCarInsert("INSERT INTO [Clan_Garage"), "a cut prefix");

  // The insert stays first and whole, the update follows as its own statement.
  const std::string out = cg::WithGameCarNames(insert);
  Check(out.starts_with(insert), "the insert unchanged, first");
  Check(out.ends_with(cg::kFromGameCars), "the update last");
  Check(Count(out, ";") == 2, "two statements");
  Check(cg::WithGameCarNames(insert.substr(0, insert.size() - 1)) == out,
        "an insert without its ';' gets one");
  Check(cg::WithGameCarNames(insert + " \n") == out, "trailing space dropped");

  // The update: only the row just inserted, only a car the game has, every column kept when
  // Data_Car has no value (they are NOT NULL), the names and identity the garage shows.
  const std::string_view update(cg::kFromGameCars);
  Check(update.starts_with("UPDATE Clan_Garage SET "), "an update of the garage");
  Check(update.ends_with("WHERE SharedId = last_insert_rowid() AND ModelID IN "
                         "(SELECT Id FROM Data_Car);"),
        "the inserted row, a car the game has");
  for (const char* column : {"Year", "MakeID", "FamilyModelId", "FamilyBodyId", "CarTypeId",
                             "RegionID", "CountryId", "FamilySpecialId", "EnginePlacementID",
                             "MakeName", "DisplayName", "ContentID", "BaseCost"}) {
    const std::string set = std::string(column) + " = COALESCE((SELECT c.";
    Check(Count(update, set) == 1, column);
    Check(Count(update, std::string("), ") + column + ")") == 1 ||
              Count(update, std::string("), ") + column + ") ") == 1,
          "kept when Data_Car has none");
  }
  Check(Count(update, "COALESCE(") == 13, "thirteen columns");
  Check(Count(update, "WHERE c.Id = Clan_Garage.ModelID") == 13, "each from the row's model");
  Check(Count(update, "PerformanceIndex") == 0 && Count(update, "Rating") == 0,
        "the shared car's own stats stay as sent");
  std::puts("club_garage_test: ok");
  return 0;
}
