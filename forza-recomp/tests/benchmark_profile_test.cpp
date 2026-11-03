#include "../src/platform/benchmark_profile.h"
#include <cassert>
#include <chrono>
#include <fstream>
#include <iostream>
using forza::benchmark::CopyProfiles;
int main() try {
  namespace fs = std::filesystem;
  auto root =
      fs::temp_directory_path() /
      ("forza-profile-test-" +
       std::to_string(
           std::chrono::steady_clock::now().time_since_epoch().count()));
  auto source = root / "source", copied = root / "copy";
  auto relative =
      fs::path("B13EBABEB13EBABE") / "4D5309C9" / "00000001" / "ForzaProfile";
  fs::create_directories((source / relative).parent_path());
  std::ofstream(source / relative) << "original";
  fs::create_directories(source / "0000000000000000");
  std::ofstream(source / "0000000000000000" / "not-a-save") << "package";
  std::ofstream(source / "launcher.json") << "not a profile";
  assert(CopyProfiles(source, copied) == 1);
  assert(!fs::exists(copied / "launcher.json") &&
         !fs::exists(copied / "0000000000000000"));
  std::ofstream(copied / relative) << "benchmark progress";
  std::string bytes;
  std::ifstream(source / relative) >> bytes;
  assert(bytes == "original");
  bool rejected = false;
  try {
    CopyProfiles(source, copied);
  } catch (const std::runtime_error &) {
    rejected = true;
  }
  assert(rejected);
  fs::create_directories(root / "empty");
  rejected = false;
  try {
    CopyProfiles(root / "empty", root / "empty-copy");
  } catch (const std::runtime_error &) {
    rejected = true;
  }
  assert(rejected);
  // #374: installed DLC can fit under MAX_PATH while the timestamped benchmark
  // destination does not. Exercise the actual package-link helper, not just path text.
  const auto packages = root / "dlc-source";
  const auto run = root / "benchmarks" / "recaro-17928413896901674";
  const auto package_file = fs::path("0000000000000000") / "4D5309C9" / "00000002" /
      "113E2064584A4BA7759BA105051C2199ABC5FA5554D" / "Media/DLCZips/2500_pri_65" /
      "Media/audio/cars/Engines/Soundbanks" / std::string(64, 'a') / "fixture.fsb";
  const auto source_file = forza::benchmark::BenchmarkFilePath(packages / package_file);
  const auto linked_file = forza::benchmark::BenchmarkFilePath(run / package_file);
  fs::create_directories(source_file.parent_path());
  std::ofstream(source_file) << "immutable DLC fixture";
  forza::benchmark::LinkPackages(packages, run);
  assert(fs::equivalent(source_file, linked_file));
  assert(fs::hard_link_count(source_file) == 2);
  std::string linked_bytes;
  std::getline(std::ifstream(linked_file), linked_bytes);
  assert(linked_bytes == "immutable DLC fixture");
  // Missing DLC is valid, and saves are still independent files (checked above).
  forza::benchmark::LinkPackages(root / "no-dlc", root / "no-dlc-copy");
  assert(!fs::exists(root / "no-dlc-copy"));
#ifdef _WIN32
  assert((run / package_file).native().size() > 260);
  assert(forza::benchmark::BenchmarkFilePath(fs::path(L"\\\\server\\share\\file")) ==
         fs::path(L"\\\\?\\UNC\\server\\share\\file"));
  assert(forza::benchmark::BenchmarkFilePath(linked_file) == linked_file);
#endif
  fs::remove_all(forza::benchmark::BenchmarkFilePath(root));
  std::cout << "Benchmark save isolation passed\n";
} catch (const std::exception &error) {
  std::cerr << "Benchmark profile test failed: " << error.what() << '\n';
  return 1;
}
