// forza - writing live_identity.txt and checking that it was written (identity.cpp).
//
// The write used to go unchecked. On Windows, Controlled folder access (Ransomware protection)
// blocks unknown programs from writing to Documents, and a read-only or OneDrive folder can
// fail the same way: the game then made a new online identity at every start, so the player
// came back as a new player each time (no friends list, no online level) with nothing in the
// log saying why.

#pragma once

#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <system_error>

namespace forza::identity_file {

// Writes `text` to `path` (its folder created first) and reads it back. False when the file
// on disk is not what was written; `why` then says what failed.
inline bool WriteChecked(const std::filesystem::path& path, const std::string& text,
                         std::string& why) {
  std::error_code ec;
  std::filesystem::create_directories(path.parent_path(), ec);
  {
    std::ofstream out(path, std::ios::trunc);
    if (!out) {
      why = "the file cannot be opened for writing";
      if (ec) why += " (creating its folder failed: " + ec.message() + ")";
      return false;
    }
    out << text;
    out.flush();
    if (!out) {
      why = "writing the file failed";
      return false;
    }
  }
  std::ifstream in(path);
  if (!in) {
    why = "the file cannot be read back after writing it";
    return false;
  }
  const std::string back((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
  if (back != text) {
    why = "the file reads back different from what was written";
    return false;
  }
  return true;
}

// The log line when WriteChecked failed: the path, the reason, and what to check.
inline std::string WarningText(const std::string& path, const std::string& why) {
  return "could not save the online identity to " + path + ": " + why +
         ". Until it can be saved this game signs in as a new player at every start (no "
         "friends list, no online level). Check that the folder is writable: on Windows, allow "
         "forza.exe through Controlled folder access (Windows Security > Virus & threat "
         "protection > Ransomware protection), make sure Documents is not read-only, and that "
         "OneDrive keeps it on this PC";
}

}  // namespace forza::identity_file
