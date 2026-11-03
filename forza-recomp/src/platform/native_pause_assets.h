#pragma once
#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <unordered_set>
#include <vector>

namespace forza::native_pause_assets {
// A missing native scene may stall inside FH1's request function before the UI
// can enforce a timeout. Check the staged archive directory before requesting
// any donor. This reads names/metadata only, including native method21 entries.
inline bool DonorsAvailable(const std::filesystem::path& archive) {
  std::ifstream file(archive, std::ios::binary);
  if (!file) return false;
  file.seekg(0, std::ios::end);
  const auto end = file.tellg();
  if (end < 22) return false;
  const auto size = static_cast<uint64_t>(end);
  const auto tail_size = size_t(std::min<uint64_t>(size, 65557));
  std::vector<uint8_t> tail(tail_size);
  file.seekg(std::streamoff(size - tail_size));
  if (!file.read(reinterpret_cast<char*>(tail.data()), std::streamsize(tail.size()))) return false;
  const auto word = [](const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8; };
  const auto dword = [](const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
  };
  size_t eocd = tail.size();
  for (size_t i = tail.size() - 21; i-- > 0;) {
    if (dword(tail.data() + i) == 0x06054b50 &&
        i + 22 + word(tail.data() + i + 20) == tail.size()) { eocd = i; break; }
  }
  if (eocd == tail.size()) return false;
  const auto* record = tail.data() + eocd;
  const auto count = word(record + 10), directory_size = dword(record + 12);
  // Stager produces a single standard directory. Unsupported layouts keep
  // stock available instead of starting a partially understood native load.
  if (word(record + 4) || word(record + 6) || word(record + 8) != count ||
      count == 0xffff || directory_size == 0xffffffff || directory_size > 16 * 1024 * 1024)
    return false;
  const uint64_t directory_end = size - tail_size + eocd;
  if (directory_size > directory_end) return false;
  std::vector<uint8_t> directory(directory_size);
  file.seekg(std::streamoff(directory_end - directory_size));
  if (!file.read(reinterpret_cast<char*>(directory.data()), std::streamsize(directory.size()))) return false;
  std::unordered_set<std::string> required;
  const auto require = [&](const std::string& scene) {
    for (const char* extension : {".bgf", ".bsg", ".fbf"})
      required.insert("ui/scenes/ui4/" + scene + extension);
  };
  require("native_menu_header"); require("native_menu_map");
  for (unsigned i = 0; i < 6; ++i) require("native_menu_tile_" + std::to_string(i));
  for (unsigned i = 0; i < 4; ++i) require("native_menu_tab_" + std::to_string(i));
  for (const char* name : {"change_car", "tune_car", "garage", "autoshow", "social_friends", "social_world", "social_game", "social_requests", "social_chat", "social_connection"})
    required.insert(std::string("ui/textures/ui4/native_menu/")+name+".xds");
  size_t position = 0;
  for (uint32_t i = 0; i < count; ++i) {
    if (position > directory.size() || directory.size() - position < 46) return false;
    const auto* entry = directory.data() + position;
    if (dword(entry) != 0x02014b50) return false;
    const size_t name_size = word(entry + 28);
    const size_t length = 46 + name_size + word(entry + 30) + word(entry + 32);
    if (length > directory.size() - position) return false;
    std::string name(reinterpret_cast<const char*>(entry + 46), name_size);
    for (char& c : name) {
      if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
      if (c == '\\') c = '/';
    }
    if (required.contains(name)) {
      if ((word(entry + 8) & 1) || !dword(entry + 20) || !dword(entry + 24)) return false;
      required.erase(name);
    }
    position += length;
  }
  return required.empty();
}
} // namespace forza::native_pause_assets
