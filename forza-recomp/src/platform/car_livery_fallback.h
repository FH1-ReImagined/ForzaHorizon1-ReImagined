// forza - a garage car whose paint job can't be loaded gets its factory paint at Get In Car
// instead of ending the game (src/hooks/car_livery_fallback.cpp, #922). No runtime in here:
// forza-recomp/tests/car_livery_fallback_test.cpp.
//
// Making a garage car the current car (Get In Car in the garage, from Car Select or from View
// Car, and the current car when free roam loads) runs sub_825718D8 (TU4 sub_825E7720). The
// car's description keeps a handle to its paint job (a CCarLiveryResource) from when the car
// was shown; when the call doesn't ask for a fresh one, the game turns that handle into the
// driving variant (sub_82C9C440, TU4 sub_82D5D538, type 6, flags 0x80000, call site 82571990 /
// TU4 825E77D8). That needs the paint job loaded: sub_82E61748 (TU4 sub_82D0CAD0) waits for it
// and returns nothing when the load failed (state byte +40 == 6), and the caller reads field
// +0x90 of that nothing: "read of guest 0x98", lr 82D57C0C (TU4), every time for that car.
//
// A paint job is either the car's factory colour (Career_Garage.DefaultManufacturerColorIndex,
// LiveryFileName '') or a file of the profile (LiveryFileName 'Liveries\<garage id>.liv'). The
// file's load fails when the file is not there (a row that names a paint job the profile
// doesn't have) or can't be read. The game's own fresh path (sub_82547EB8, TU4 sub_825B7FA0,
// taken when the call asks for a fresh paint job) reads the row's CarId,
// DefaultManufacturerColorIndex and LiveryFileName again and makes the driving variant from
// them. The fallback clears a file paint job that failed (the game's own
// "UPDATE %s SET LiveryFileName='' WHERE Id=%u", sub_82534848 / TU4 sub_825A32C8, does the
// same when a paint job is removed) and takes that fresh path, so the car comes with its
// factory paint; the profile keeps the cleared column when the game next saves, and the .liv
// file, if there is one, stays where it is.

#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace forza::car_livery_fallback {

// The paint job resource's state byte (+40) after a load that failed.
inline constexpr uint8_t kStateFailed = 6;
// Its key type (+0x40): 0 = the factory colour or a paint job kept in memory, 1 = a file.
inline constexpr uint32_t kKeyFile = 1;
// The driving variant's flags, as the game passes them on both paths.
inline constexpr uint32_t kDrivingFlags = 0x80000;

// The game's garage table name, as the career car object holds it: letters, digits and '_'
// only, so the UPDATE below can't be anything else.
inline bool ValidTable(std::string_view table) {
  if (table.empty() || table.size() > 64) return false;
  for (char c : table) {
    const bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
                    c == '_';
    if (!ok) return false;
  }
  return true;
}

// The same statement as the game's sub_82534848 / TU4 sub_825A32C8. Empty for a bad table name.
inline std::string ClearLiverySql(std::string_view table, uint32_t garage_id) {
  if (!ValidTable(table)) return {};
  return "UPDATE " + std::string(table) + " SET LiveryFileName='' WHERE Id=" +
         std::to_string(garage_id);
}

// What to do with the car's paint job when the game is about to make its driving variant.
enum class Action {
  kGame,      // loaded (or still loading): the game's own path
  kFactory,   // a paint job file that failed: clear LiveryFileName, then the fresh path
  kFresh,     // another failure (the factory colour itself): the fresh path, column unchanged
};

inline Action Decide(bool load_failed, uint32_t key_type) {
  if (!load_failed) return Action::kGame;
  return key_type == kKeyFile ? Action::kFactory : Action::kFresh;
}

}  // namespace forza::car_livery_fallback
