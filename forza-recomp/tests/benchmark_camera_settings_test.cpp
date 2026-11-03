// Exercise the production benchmark snapshot writer and restore reader. Only
// native game entry/allocator boundaries are stubbed; no game is started.
#include <rex/system/kernel_state.h>
struct FixtureMemory {
  uint32_t SystemHeapAlloc(size_t) { return 0; }
  void SystemHeapFree(uint32_t) {}
};
FixtureMemory fixture_memory;
#undef REX_KERNEL_MEMORY
#define REX_KERNEL_MEMORY() (&fixture_memory)
#include "../src/platform/benchmark_session.cpp"

#include <array>
#include <cstdlib>
#include <stdexcept>
#include <cstdio>

static void Check(bool passed) {
  if (!passed) throw std::runtime_error("Benchmark camera preference did not round-trip");
}

REXCVAR_DEFINE_INT32(forza_cockpit_fov, 0, "Display", "Fixture cockpit base");
REXCVAR_DEFINE_INT32(forza_chase_near_fov, 0, "Display", "Fixture near base");
REXCVAR_DEFINE_INT32(forza_chase_far_fov, 0, "Display", "Fixture far base");
REXCVAR_DEFINE_INT32(forza_hood_fov, 0, "Display", "Fixture hood base");
REXCVAR_DEFINE_INT32(forza_bumper_fov, 0, "Display", "Fixture bumper base");
REXCVAR_DEFINE_INT32(forza_unrelated_fixture, 0, "Test", "Not a graphics preference");

namespace forza {
void RestartForBenchmark() { Check(false && "No process restart in this fixture"); }
}
namespace forza::benchmark {
bool Enabled() { return false; }
void Fail(const char*) { Check(false && "No game flow in this fixture"); }
void Cancel() { Check(false && "No game flow in this fixture"); }
}
extern "C" REX_FUNC(__imp__sub_8281CF68) {
  Check(false && "No guest calls in this fixture");
}

int RoundTrip() {
  using namespace forza::benchmark;
  const std::array<const char*, 5> keys{
      "forza_cockpit_fov", "forza_chase_near_fov", "forza_chase_far_fov",
      "forza_hood_fov", "forza_bumper_fov"};
  const std::array<const char*, 5> selected{"40", "51", "62", "73", "90"};
  const auto folder = std::filesystem::temp_directory_path() /
      ("forza-benchmark-camera-" + std::to_string(Clock::now().time_since_epoch().count()));
  Check(std::filesystem::create_directory(folder));
  source_root = folder;
  settings_snapshot = folder / "benchmark-settings.txt";

  for (size_t i = 0; i < keys.size(); ++i)
    Check(rex::cvar::SetFlagByName(keys[i], selected[i]));
  Check(rex::cvar::SetFlagByName("forza_unrelated_fixture", "1"));
  SaveSettings();
  for (const auto* key : keys) Check(rex::cvar::SetFlagByName(key, "0"));
  Check(rex::cvar::SetFlagByName("forza_unrelated_fixture", "2"));
  PrepareRuntime();
  for (size_t i = 0; i < keys.size(); ++i)
    Check(rex::cvar::GetFlagByName(keys[i]) == selected[i]);
  Check(rex::cvar::GetFlagByName("forza_unrelated_fixture") == "2");

  // Stock must replace a previous explicit base on the return journey too.
  for (const auto* key : keys) Check(rex::cvar::SetFlagByName(key, "0"));
  SaveSettings();
  for (const auto* key : keys) Check(rex::cvar::SetFlagByName(key, "90"));
  PrepareRuntime();
  for (const auto* key : keys) Check(rex::cvar::GetFlagByName(key) == "0");
  std::filesystem::remove(settings_snapshot);
  std::filesystem::remove(folder);
  std::puts("production benchmark camera settings round trip: OK");
  return 0;
}

int main() {
  try { return RoundTrip(); }
  catch (const std::exception& error) { std::fprintf(stderr, "%s\n", error.what()); return 1; }
}

