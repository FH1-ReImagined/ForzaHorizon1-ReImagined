// forza - the club garage's cars named from the game's own car list (src/hooks/database_query.cpp,
// #697). No runtime in here: forza-recomp/tests/club_garage_test.cpp.
//
// The CLUB GARAGE lists the cars a car club's members share (SharedCar.GetClubCars, TU4
// GetClubCarsEx). The game copies each row of the answer into its database with
//   INSERT INTO [Clan_Garage] ([SharedId], [Owner], ..., [ModelID], ..., [Year], [MakeID],
//   [FamilyModelId], ..., [MakeName], [DisplayName], [ContentID], ...) VALUES (...);
// and the garage shows the car's year, make, flag and name from those columns (its list joins
// Restrictions_Data_Car only for the thumbnail and ModelShort). Everything but the model and
// the stats the sharing game sent comes from the server: a Forza Live server knows the base
// game's cars at most (its optional copy of gamedb.slt) and no names (MakeName and ModelName
// are empty), so every car lacked its model name ("2012 Lamborghini", "2011 RUF") and a DLC
// car the server's database does not have (the BMW M3 GTS of the November Bondurant Car Pack)
// had no year, make, flag or name at all.
//
// The game has every car it can drive in Data_Car, DLC included, with the same columns the
// row fills, and the names as string references ("_&3444155919") the UI resolves like
// everywhere else. So each such insert gets a second statement that sets those columns from
// Data_Car when the game has the car; a car it does not have (DLC this player lacks) keeps
// what the server sent.

#pragma once

#include <cstring>
#include <string>
#include <string_view>

namespace forza::club_garage {

constexpr std::string_view kInsertPrefix = "INSERT INTO [Clan_Garage] (";

// Run after the game's insert, in the same query string: the row just inserted (SharedId is
// the table's INTEGER PRIMARY KEY, so its rowid), its car's identity from Data_Car. Every
// column is NOT NULL: COALESCE keeps the row's value where Data_Car has none.
constexpr char kFromGameCars[] =
    "UPDATE Clan_Garage SET "
    "Year = COALESCE((SELECT c.Year FROM Data_Car AS c WHERE c.Id = Clan_Garage.ModelID), Year), "
    "MakeID = COALESCE((SELECT c.MakeID FROM Data_Car AS c WHERE c.Id = Clan_Garage.ModelID), "
    "MakeID), "
    "FamilyModelId = COALESCE((SELECT c.FamilyModelID FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), FamilyModelId), "
    "FamilyBodyId = COALESCE((SELECT c.FamilyBodyID FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), FamilyBodyId), "
    "CarTypeId = COALESCE((SELECT c.CarTypeID FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), CarTypeId), "
    "RegionID = COALESCE((SELECT c.RegionID FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), RegionID), "
    "CountryId = COALESCE((SELECT c.CountryID FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), CountryId), "
    "FamilySpecialId = COALESCE((SELECT c.FamilySpecialID FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), FamilySpecialId), "
    "EnginePlacementID = COALESCE((SELECT c.EnginePlacementID FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), EnginePlacementID), "
    "MakeName = COALESCE((SELECT c.MakeName FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), MakeName), "
    "DisplayName = COALESCE((SELECT c.DisplayName FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), DisplayName), "
    "ContentID = COALESCE((SELECT c.ContentId FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), ContentID), "
    "BaseCost = COALESCE((SELECT c.BaseCost FROM Data_Car AS c "
    "WHERE c.Id = Clan_Garage.ModelID), BaseCost) "
    "WHERE SharedId = last_insert_rowid() "
    "AND ModelID IN (SELECT Id FROM Data_Car);";

// Whether a query string (NUL-terminated) is the game's insert of a shared car.
inline bool IsSharedCarInsert(const char* s) {
  return s && std::strncmp(s, kInsertPrefix.data(), kInsertPrefix.size()) == 0;
}

// The insert followed by kFromGameCars.
inline std::string WithGameCarNames(std::string_view insert) {
  std::string out(insert);
  while (!out.empty() && (out.back() == ' ' || out.back() == '\n')) out.pop_back();
  if (!out.empty() && out.back() != ';') out += ';';
  out += ' ';
  out += kFromGameCars;
  return out;
}

}  // namespace forza::club_garage
