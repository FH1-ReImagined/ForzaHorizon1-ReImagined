// The content manifest the game sends to the Forza Live server (kernel/content_manifest.h, #567).
// g++ -std=c++20 -I forza-recomp/src/kernel forza-recomp/tests/content_manifest_test.cpp
#include "content_manifest.h"

#include <cstdio>
#include <set>
#include <string>

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

int main() {
  using namespace forza;
  // Names: ASCII, single spaces, the manifest's separators dropped.
  CHECK(CleanModName("Traffic Car Unlocker") == "Traffic Car Unlocker");
  CHECK(CleanModName("  two   words ") == "two words");
  CHECK(CleanModName("a,b;c=d") == "abcd");
  CHECK(CleanModName("\xE4\xB8\xAD\xE6\x96\x87") == "");
  CHECK(CleanModName("car_pack-1.2 (beta)") == "car_pack-1.2 (beta)");
  CHECK(CleanModName(std::string(100, 'x')) == std::string(kMaxModNameChars, 'x'));
  CHECK(CleanModName(std::string("a\0b\nc", 5)) == "a b c");

  // Which files make a mod matter online.
  CHECK(ChangesCars("db/gamedb.slt"));
  CHECK(ChangesCars("cars/FOO_Bar_12/FOO_Bar_12.carbin"));
  CHECK(ChangesCars("Wheels/AAA_Rim/rim.carbin"));
  CHECK(ChangesCars("DB\\gamedb.slt"));
  CHECK(!ChangesCars("audio/radio/radiosystem.xml"));
  CHECK(!ChangesCars("stringtables/en/main.str"));
  CHECK(!ChangesCars("ui/textures/db/x.xds"));

  // The text: sorted offers, sorted names.
  CHECK(FormatManifest({}, {}) == "dlc=;mods=");
  CHECK(FormatManifest({3060, 729, 1100}, {"B mod", "A mod"}) == "dlc=729,1100,3060;mods=A mod,B mod");
  CHECK(FormatManifest({729}, {}) == "dlc=729;mods=");
  CHECK(FormatManifest({}, {"", "x"}) == "dlc=;mods=x");

  // Limits: never longer than kMaxManifestBytes, never more than the caps.
  std::set<uint32_t> many;
  for (uint32_t i = 0; i < 500; ++i) many.insert(1000 + i);
  const std::string big = FormatManifest(many, {});
  CHECK(big.size() <= kMaxManifestBytes);
  CHECK(big.rfind("dlc=1000,1001,", 0) == 0);
  std::set<std::string> mods;
  for (int i = 0; i < 100; ++i) mods.insert("mod" + std::to_string(1000 + i) + std::string(30, 'z'));
  const std::string m = FormatManifest({}, mods);
  CHECK(m.size() <= kMaxManifestBytes);
  CHECK(m.find("mod1000") != std::string::npos);
  CHECK(m.find("mod1099") == std::string::npos);

  if (failures) return 1;
  std::printf("content manifest: all checks passed\n");
  return 0;
}
