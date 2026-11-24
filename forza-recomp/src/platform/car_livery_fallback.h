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
// while the row holds the same car and the file the same contents, the fresh path gives that row
// its factory colour instead of queueing the same file again (a load that would fail later,
// after that path's own check).
//
// The game's paint job cache (TU4 0x8357C0B8: a lock, then a vector of resources at +28/+32,
// searched by key in sub_82D4C2C0) is weak: a resource leaves it when its last reference goes
// (sub_825AC6A8, only while flag 0x40 of byte +42 says it is listed). A failed one stays listed
// while anything holds it (a car shown in the garage), and every request for the same key gets
// it back: the factory colour asked for again is the same failed resource, and a repainted or
// replaced file isn't read again. So the failed resource is taken out of the cache first (as
// sub_825AC6A8 does, flag cleared: its holders keep it, and it goes with its last reference),
// and the next request for that key loads it afresh.
//
// A remembered row's file is compared by its size and contents, read again from the disk: the
// file system keeps each file's size from when it was first looked up, so a file replaced
// outside the game, or rewritten no longer than before, kept the size of the bad one and the row
// kept its factory colour (#959). A file that changed since it failed is loaded again once
// (through the game's fresh path, waited for at the Get In call) and used when it loads.
//
// The driving paint job is never left empty while the factory colour can be made: a factory
// colour that fails is taken out of the cache and made once more. It stays empty only when that
// fails too (the 256-byte block of a type-0 paint job can't be allocated) or when the row can't
// be read; the game's own fresh path (sub_825B7FA0) also leaves it empty when the row can't be
// read.

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

// The profile root of a key's paint job file name ('E030000004B490FA:\Liveries\60.liv' ->
// 'E030000004B490FA'): letters and digits before the first ':', followed by ":\Liveries\" and a
// file name, or empty. Anything else (another root's file, a name with "..") is not a profile
// paint job, so it is never detached.
inline std::string ProfileRoot(std::string_view path) {
  const size_t colon = path.find(':');
  if (colon == std::string_view::npos || colon == 0 || colon > 32) return {};
  if (path.find("..") != std::string_view::npos) return {};
  for (size_t i = 0; i < colon; ++i) {
    const char c = path[i];
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9'))) return {};
  }
  constexpr std::string_view kDir = ":\\liveries\\";
  if (path.size() <= colon + kDir.size()) return {};
  for (size_t i = 0; i < kDir.size(); ++i) {
    char c = path[colon + i];
    if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    if (c != kDir[i]) return {};
  }
  if (path.find('\\', colon + kDir.size()) != std::string_view::npos) return {};
  return std::string(path.substr(0, colon));
}

// A file's contents, as remembered (FNV-1a, 64 bits).
inline uint64_t Fingerprint(const uint8_t* data, size_t size) {
  uint64_t hash = 0xCBF29CE484222325ull;
  for (size_t i = 0; i < size; ++i) hash = (hash ^ data[i]) * 0x100000001B3ull;
  return hash;
}

// A row remembered after its paint job file failed (it stays named in the save): the file and
// what the file system said about it then, and the car in the row.
struct Remembered {
  std::string path;   // the key's file name
  FileState file = FileState::kUnknown;
  uint64_t size = 0;  // the file's size then (when kPresent or kEmpty)
  uint64_t hash = 0;  // its contents then (when kPresent; 0 when they couldn't be read)
  int32_t car = 0;    // the row's CarId then
};

// Whether a remembered row still gets the factory colour: the same car, and its file in the
// same state with the same size and contents. A repaint (whatever its size), a replaced or a
// deleted file, or another car in a reused garage id ends it, and the game's own path runs again.
inline bool StillRemembered(const Remembered& then, FileState file, uint64_t size, uint64_t hash,
                            int32_t car) {
  if (car != then.car || file != then.file) return false;
  return file != FileState::kPresent || (size == then.size && hash == then.hash);
}

// Whether a resource of the paint job cache is taken out of it: the one that failed, and any
// other failed load of the same file (`path`, when given) that something still holds. One with
// no reference left is the game's own to drop.
inline bool Evicts(bool the_failed_one, uint8_t state, uint32_t key_type, std::string_view name,
                   std::string_view path, uint32_t references) {
  if (the_failed_one) return true;
  return !path.empty() && references > 0 && state == kStateFailed && key_type == kKeyFile &&
         name == path;
}

}  // namespace forza::car_livery_fallback
