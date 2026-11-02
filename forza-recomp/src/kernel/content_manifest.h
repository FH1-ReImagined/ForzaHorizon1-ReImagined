// forza - the content a game has that changes which cars exist (#567, mod sync step of #152).
//
// A player whose game has a car pack, a car unlocker or a database mod that another player's
// game lacks sees that player's car as a plain placeholder (the game's own fallback when a
// car is not in its database or its files are missing). The game tells the Forza Live server
// what it has, as a short text, and the server tells the players of one session what they
// do not share (server/forza_live/social/chat.py, chat op 5).
//
// The text is ASCII and at most kMaxManifestBytes long:
//   dlc=729,732,1100;mods=Car pack,Traffic unlocker
// dlc: the offers of the installed DLC packages (Media/<offer>.puboffer), ascending. mods:
// the names of the loaded mods that change cars or the database (files under media/cars,
// media/wheels or media/db), sorted; a radio or text mod is not listed.
//
// This header is the game-independent part (no SDK dependency): tests/content_manifest_test.cpp
// builds it on its own. The state lives in src/kernel/content_manifest.cpp.
#pragma once

#include <algorithm>
#include <cstdint>
#include <set>
#include <string>
#include <string_view>
#include <vector>

namespace forza {

inline constexpr size_t kMaxManifestBytes = 700;
inline constexpr size_t kMaxManifestMods = 24;
inline constexpr size_t kMaxManifestDlc = 96;
inline constexpr size_t kMaxModNameChars = 32;

// A mod's name as the server and the other players see it: ASCII letters, digits, spaces and
// . _ - ( ), no separators of the manifest, at most kMaxModNameChars. Empty when nothing is left.
inline std::string CleanModName(std::string_view raw) {
  std::string out;
  bool space = false;
  for (unsigned char c : raw) {
    const bool ok = (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                    c == '.' || c == '_' || c == '-' || c == '(' || c == ')';
    if (ok) {
      const bool sep = space && !out.empty();
      if (out.size() + (sep ? 1 : 0) + 1 > kMaxModNameChars) break;
      if (sep) out += ' ';
      space = false;
      out += char(c);
    } else if (c == ' ' || c < 0x20 || c == 0x7F) {
      space = true;
    }
  }
  return out;
}

// Only content that changes which cars exist matters for what other players see: the car
// database, car models and wheels (paths under game:\media\, as ModFile::name has them).
inline bool ChangesCars(std::string_view media_path) {
  auto starts = [&](std::string_view prefix) {
    if (media_path.size() < prefix.size()) return false;
    for (size_t i = 0; i < prefix.size(); ++i) {
      char c = media_path[i];
      if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
      if (c == '\\') c = '/';
      if (c != prefix[i]) return false;
    }
    return true;
  };
  return starts("db/") || starts("cars/") || starts("wheels/");
}

inline std::string FormatManifest(const std::set<uint32_t>& dlc, const std::set<std::string>& mods) {
  std::string out = "dlc=";
  size_t n = 0;
  for (uint32_t offer : dlc) {
    if (n++ >= kMaxManifestDlc) break;
    if (n > 1) out += ',';
    out += std::to_string(offer);
  }
  out += ";mods=";
  n = 0;
  for (const std::string& mod : mods) {
    if (mod.empty()) continue;
    if (n >= kMaxManifestMods) break;
    const std::string next = (n ? "," : "") + mod;
    if (out.size() + next.size() > kMaxManifestBytes) break;
    out += next;
    ++n;
  }
  if (out.size() > kMaxManifestBytes) out.resize(kMaxManifestBytes);
  return out;
}

}  // namespace forza
