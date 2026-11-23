// forza - a garage car whose paint job can't be loaded gets its factory paint when it becomes the
// current car, instead of ending the game (src/hooks/car_livery_fallback.cpp, #922). No runtime
// in here: forza-recomp/tests/car_livery_fallback_test.cpp.
//
// Making a garage car the current car (Get In Car in the garage, from Car Select or from View
// Car, and the current car when free roam loads) runs sub_825718D8 (TU4 sub_825E7720); a second
// function, sub_82571A58 (TU4 sub_825E78A0), does the same from a career object. The car's
// description keeps a handle to its paint job (a CCarLiveryResource) from when the car was
// shown; when there is one, the game turns that handle into the driving variant (sub_82C9C440,
// TU4 sub_82D5D538, type 6, flags 0x80000; call sites 82571990 / TU4 825E77D8 and 82571B18 /
// TU4 825E7960). That needs the paint job loaded: sub_82E61748 (TU4 sub_82D0CAD0) waits for it
// and returns nothing when the load failed (state byte +40 == 6), and the caller reads field
// +0x90 of that nothing: "read of guest 0x98", lr 82D57C0C (TU4), every time for that car.
//
// A paint job is either the car's factory colour (Career_Garage.DefaultManufacturerColorIndex,
// LiveryFileName '') or a file of the profile (LiveryFileName 'Liveries\<garage id>.liv', which
// the paint job's key at +0x44 names as '<profile xuid>:\Liveries\<garage id>.liv'). The file's
// load fails when the file is not there (a row that names a paint job the profile doesn't have),
// is empty, or can't be read or parsed. The game's own "fresh" path (sub_82547EB8, TU4
// sub_825B7FA0) falls back to the factory colour (sub_82C94398, TU4 sub_82D55428, with the row's
// CarId and DefaultManufacturerColorIndex, at TU4 825B8134) only when the file's paint job is
// already failed in the resource cache; a new load of the file is queued and fails later, so
// that path alone doesn't help. The fallback therefore makes the factory colour itself, the same
// call with the row's values. It changes the save only when the file is really gone: when the
// key is a file and the profile has no such file, or it is 0 bytes, it clears the row's
// LiveryFileName (the game's own "UPDATE %s SET LiveryFileName='' WHERE Id=%u", sub_82534848 /
// TU4 sub_825A32C8, does the same when a paint job is removed). A file that is there but fails
// (a bad header, a read error) stays named in the save, so it is never detached from the car;
// for the rest of the session the fresh path gives that row its factory colour instead of
// queueing the same file again (a load that would fail later, after that path's own check).

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

// What the profile holds for a paint job file, as the file system sees it.
enum class FileState {
  kUnknown,  // not a file, no name, or the profile isn't mounted: nothing is known
  kMissing,  // the profile is mounted and has no such file
  kEmpty,    // the file is there with 0 bytes
  kPresent,  // the file is there with data (it failed for another reason)
};

// What to do with the car's paint job when the game is about to make its driving variant.
enum class Action {
  kGame,      // loaded (or still loading): the game's own path
  kFactory,   // failed: the factory colour, the save unchanged
  kDetach,    // a paint job file that is gone (missing or empty): the factory colour, and the
              // row stops naming the file
};

inline Action Decide(bool load_failed, uint32_t key_type, FileState file) {
  if (!load_failed) return Action::kGame;
  if (key_type == kKeyFile && (file == FileState::kMissing || file == FileState::kEmpty)) {
    return Action::kDetach;
  }
  return Action::kFactory;
}

// The game's garage table name, as the career car object holds it: letters, digits and '_'
// only, so the statements below can't be anything else.
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

// 1 when the row's LiveryFileName is clear (the UPDATE above took). Empty for a bad table name.
inline std::string ClearedSql(std::string_view table, uint32_t garage_id) {
  if (!ValidTable(table)) return {};
  return "SELECT COUNT(*) FROM " + std::string(table) + " WHERE Id=" + std::to_string(garage_id) +
         " AND LiveryFileName=''";
}

// A row's integer column. Empty for a bad table name; the column is one of ours.
inline std::string ColumnSql(std::string_view table, std::string_view column, uint32_t garage_id) {
  if (!ValidTable(table) || !ValidTable(column)) return {};
  return "SELECT " + std::string(column) + " FROM " + std::string(table) +
         " WHERE Id=" + std::to_string(garage_id);
}

// The profile root of a key's file name ('E030000004B490FA:\Liveries\60.liv' ->
// 'E030000004B490FA'): letters and digits before the first ':', or empty. A name without one,
// or with "..", is not a profile file.
inline std::string ProfileRoot(std::string_view path) {
  const size_t colon = path.find(':');
  if (colon == std::string_view::npos || colon == 0 || colon > 32) return {};
  if (path.size() < colon + 3 || path[colon + 1] != '\\') return {};
  if (path.find("..") != std::string_view::npos) return {};
  for (size_t i = 0; i < colon; ++i) {
    const char c = path[i];
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'))) return {};
  }
  return std::string(path.substr(0, colon));
}

}  // namespace forza::car_livery_fallback
