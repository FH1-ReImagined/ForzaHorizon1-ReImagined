#include "platform/native_pause_assets.h"

#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string_view>

namespace {
using Bytes = std::vector<uint8_t>;

void Put16(Bytes& bytes, size_t offset, uint16_t value) {
  bytes.at(offset) = uint8_t(value);
  bytes.at(offset + 1) = uint8_t(value >> 8);
}

void Put32(Bytes& bytes, size_t offset, uint32_t value) {
  Put16(bytes, offset, uint16_t(value));
  Put16(bytes, offset + 2, uint16_t(value >> 16));
}

struct Entry {
  std::string name;
  uint16_t flags = 0;
  uint16_t method = 0;
  uint32_t packed = 1;
  uint32_t unpacked = 1;
};

std::vector<Entry> RequiredEntries() {
  std::vector<Entry> entries;
  const auto add = [&](const std::string& name) {
    for (const auto* extension : {"bgf", "bsg", "fbf"})
      entries.push_back({"ui/scenes/ui4/" + name + "." + extension});
  };
  add("native_menu_header");
  add("native_menu_map");
  for (unsigned i = 0; i < 6; ++i) add("native_menu_tile_" + std::to_string(i));
  for (unsigned i = 0; i < 4; ++i) add("native_menu_tab_" + std::to_string(i));
  for (const char* name : {"change_car", "tune_car", "garage", "autoshow", "social_friends", "social_world", "social_game", "social_requests", "social_chat", "social_connection"})
    entries.push_back({std::string("ui/textures/ui4/native_menu/")+name+".xds"});
  return entries;
}

struct Archive {
  Bytes bytes;
  size_t directory = 0;
  size_t eocd = 0;
};

// The payload is deliberately opaque. This contract checks directory metadata,
// not decompression; method21 must work without an XMem codec or game data.
Archive Zip(const std::vector<Entry>& entries, std::string_view comment = {}) {
  Archive result;
  std::vector<uint32_t> offsets;
  for (const auto& entry : entries) {
    const auto start = result.bytes.size();
    offsets.push_back(uint32_t(start));
    result.bytes.resize(start + 30, 0);
    Put32(result.bytes, start, 0x04034b50);
    Put16(result.bytes, start + 4, 20);
    Put16(result.bytes, start + 6, entry.flags);
    Put16(result.bytes, start + 8, entry.method);
    Put32(result.bytes, start + 14, 0x8cdc1683); // CRC32 of 'x'.
    Put32(result.bytes, start + 18, entry.packed);
    Put32(result.bytes, start + 22, entry.unpacked);
    Put16(result.bytes, start + 26, uint16_t(entry.name.size()));
    result.bytes.insert(result.bytes.end(), entry.name.begin(), entry.name.end());
    result.bytes.insert(result.bytes.end(), entry.packed, uint8_t('x'));
  }
  result.directory = result.bytes.size();
  for (size_t i = 0; i < entries.size(); ++i) {
    const auto& entry = entries[i];
    const auto start = result.bytes.size();
    result.bytes.resize(start + 46, 0);
    Put32(result.bytes, start, 0x02014b50);
    Put16(result.bytes, start + 4, 20);
    Put16(result.bytes, start + 6, 20);
    Put16(result.bytes, start + 8, entry.flags);
    Put16(result.bytes, start + 10, entry.method);
    Put32(result.bytes, start + 16, 0x8cdc1683);
    Put32(result.bytes, start + 20, entry.packed);
    Put32(result.bytes, start + 24, entry.unpacked);
    Put16(result.bytes, start + 28, uint16_t(entry.name.size()));
    Put32(result.bytes, start + 42, offsets[i]);
    result.bytes.insert(result.bytes.end(), entry.name.begin(), entry.name.end());
  }
  result.eocd = result.bytes.size();
  result.bytes.resize(result.eocd + 22, 0);
  Put32(result.bytes, result.eocd, 0x06054b50);
  Put16(result.bytes, result.eocd + 8, uint16_t(entries.size()));
  Put16(result.bytes, result.eocd + 10, uint16_t(entries.size()));
  Put32(result.bytes, result.eocd + 12, uint32_t(result.eocd - result.directory));
  Put32(result.bytes, result.eocd + 16, uint32_t(result.directory));
  Put16(result.bytes, result.eocd + 20, uint16_t(comment.size()));
  result.bytes.insert(result.bytes.end(), comment.begin(), comment.end());
  return result;
}

class Fixture {
 public:
  Fixture() {
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    for (unsigned i = 0; i < 100; ++i) {
      folder_ = std::filesystem::temp_directory_path() /
                ("native-pause-assets-" + std::to_string(nonce) + "-" + std::to_string(i));
      if (std::filesystem::create_directory(folder_)) return;
    }
    throw std::runtime_error("Cannot create private fixture directory");
  }
  ~Fixture() {
    std::error_code ignored;
    std::filesystem::remove_all(folder_, ignored);
  }
  void Check(const Bytes& bytes, bool expected, std::string_view label) {
    const auto path = folder_ / "fixture.zip";
    {
      std::ofstream file(path, std::ios::binary | std::ios::trunc);
      file.write(reinterpret_cast<const char*>(bytes.data()), std::streamsize(bytes.size()));
      if (!file) throw std::runtime_error("Cannot write fixture");
    }
    Expect(path, expected, label);
  }
  void Missing() { Expect(folder_ / "does-not-exist.zip", false, "absent file"); }

 private:
  static void Expect(const std::filesystem::path& path, bool expected, std::string_view label) {
    if (forza::native_pause_assets::DonorsAvailable(path) != expected)
      throw std::runtime_error(std::string(label) + ": unexpected availability");
  }
  std::filesystem::path folder_;
};

void Run(std::string_view name) {
  Fixture fixture;
  auto entries = RequiredEntries();
  if (entries.size() != 46) throw std::runtime_error("Expected twelve complete triplets and ten attributed icons");
  if (name == "complete") {
    fixture.Check(Zip(entries).bytes, true, name);
  } else if (name == "missing_each") {
    for (size_t i = 0; i < entries.size(); ++i) {
      auto missing = entries;
      missing.erase(missing.begin() + i);
      fixture.Check(Zip(missing).bytes, false, entries[i].name);
    }
  } else if (name == "absent") {
    fixture.Missing();
  } else if (name == "empty") {
    fixture.Check({}, false, "empty file");
    fixture.Check(Zip({}).bytes, false, "empty ZIP");
  } else if (name == "empty_member") {
    entries.back().packed = 0;
    fixture.Check(Zip(entries).bytes, false, "zero compressed size");
    entries.back().packed = 1;
    entries.back().unpacked = 0;
    fixture.Check(Zip(entries).bytes, false, "zero uncompressed size");
  } else if (name == "encrypted") {
    entries.back().flags = 1;
    fixture.Check(Zip(entries).bytes, false, "encrypted required entry");
  } else if (name == "truncated") {
    const auto complete = Zip(entries);
    for (const auto size : {size_t(0), size_t(21), complete.directory + 20,
                            complete.eocd, complete.bytes.size() - 1}) {
      auto bytes = complete.bytes;
      bytes.resize(size);
      fixture.Check(bytes, false, "truncated archive");
    }
  } else if (name == "invalid_directory") {
    auto archive = Zip(entries);
    Put32(archive.bytes, archive.directory, 0);
    fixture.Check(archive.bytes, false, "bad directory signature");
    archive = Zip(entries);
    Put16(archive.bytes, archive.directory + 28, 0xffff);
    fixture.Check(archive.bytes, false, "name extends beyond directory");
    archive = Zip(entries);
    Put16(archive.bytes, archive.directory + 30, 0xffff);
    fixture.Check(archive.bytes, false, "extra field extends beyond directory");
    archive = Zip(entries);
    Put16(archive.bytes, archive.eocd + 8, 34);
    Put16(archive.bytes, archive.eocd + 10, 34);
    fixture.Check(archive.bytes, false, "entry count exceeds directory");
  } else if (name == "directory_bounds") {
    for (const auto size : {uint32_t(45), uint32_t(0xffffffff), uint32_t(16 * 1024 * 1024 + 1)}) {
      auto archive = Zip(entries);
      Put32(archive.bytes, archive.eocd + 12, size);
      fixture.Check(archive.bytes, false, "invalid directory size");
    }
    auto archive = Zip(entries);
    Put32(archive.bytes, archive.eocd + 12, uint32_t(archive.eocd + 1));
    fixture.Check(archive.bytes, false, "directory begins before file");
  } else if (name == "unsupported_layout") {
    for (const auto field : {4u, 6u, 8u, 10u}) {
      auto archive = Zip(entries);
      Put16(archive.bytes, archive.eocd + field, field == 10 ? 0xffff : 1);
      fixture.Check(archive.bytes, false, "multidisk/count mismatch/ZIP64");
    }
  } else if (name == "comments") {
    fixture.Check(Zip(entries, "commentPK\x05\x06 trailing signature").bytes, true, "signature in comment");
    fixture.Check(Zip(entries, std::string(65535, 'c')).bytes, true, "maximum ZIP comment");
    auto archive = Zip(entries, "hello");
    Put16(archive.bytes, archive.eocd + 20, 4);
    fixture.Check(archive.bytes, false, "incorrect comment length");
  } else if (name == "case_method21") {
    for (auto& entry : entries) {
      entry.method = 21;
      for (auto& c : entry.name) {
        if (c >= 'a' && c <= 'z') c = char(c - 'a' + 'A');
        if (c == '/') c = '\\';
      }
    }
    fixture.Check(Zip(entries).bytes, true, "native method21 uppercase backslash paths");
  } else if (name == "unrelated_entries") {
    entries.insert(entries.begin(), {"other/empty-encrypted.bin", 1, 21, 0, 0});
    std::reverse(entries.begin(), entries.end());
    fixture.Check(Zip(entries).bytes, true, "unrelated metadata and arbitrary ordering");
  } else {
    throw std::runtime_error("Unknown test case: " + std::string(name));
  }
}
} // namespace

int main(int argc, char** argv) {
  try {
    if (argc != 2) throw std::runtime_error("Expected a test case name");
    Run(argv[1]);
    std::cout << argv[1] << " PASS\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
