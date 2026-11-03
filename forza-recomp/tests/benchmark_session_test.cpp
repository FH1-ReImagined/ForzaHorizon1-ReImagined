// Exercise the real restart/session implementation without starting a game or GPU.
#include "../src/platform/benchmark_session.cpp"
#include <cassert>
#include <iostream>
#include <vector>

REXCVAR_DEFINE_INT32(frame_rate, 30, "Display", "Test frame cap");

namespace { int restarts = 0; }
namespace forza { void RestartForBenchmark() { ++restarts; } }
namespace forza::benchmark {
bool Enabled() { return automatic.load(); }
void Cancel() {}
void Fail(const char*) {}
}
#if FORZA_TITLE_UPDATE == 4
extern "C" REX_FUNC(__imp__sub_8281CF68) {}
#endif

int main(int argc, char** argv) try {
  using namespace forza::benchmark;
  assert(argc == 3);
  const std::string scenario = argv[1];
  const bool success = scenario.starts_with("success");
  const auto root = std::filesystem::path(argv[2]) /
      std::to_string(Clock::now().time_since_epoch().count());
  assert(!std::filesystem::exists(root));
  std::filesystem::create_directories(root);
  rex::PathConfig paths;
  paths.user_data_root = root;
  PPCContext ctx{};
  if (scenario == "confirmation") {
    ConfigurePaths(paths);
    bool blocked = false;
    Show();
    Pad(0, blocked);
    Pad(0x2000, blocked);
    FlowTick(ctx, nullptr);
    assert(Snapshot().screen == Screen::hidden && restarts == 0);
    // No hidden-screen poll occurs between dismissing and reopening the overlay.
    // The title's A press must not also confirm the new dialog.
    Show();
    Pad(0x1000, blocked);
    assert(action.load() == 0);
    Pad(0x1000, blocked);
    assert(action.load() == 0); // holding the opening press does not confirm
    Pad(0, blocked);
    Pad(0x1000, blocked);
    FlowTick(ctx, nullptr);
    assert(restarts == 1);
    assert(std::filesystem::exists(root / "benchmark-request.txt"));
  } else {
    const auto profile = root / "1234567890ABCDEF" / "4D5309C9" / "00000001";
    if (success) {
      std::filesystem::create_directories(profile);
      std::ofstream(profile / "save") << "original profile";
      std::ofstream(root / "benchmark-settings.txt") << "\"frame_rate\" \"37\"\n";
    }
    const auto package = std::filesystem::path("0000000000000000/4D5309C9/00000002") /
        std::string(40, 'A') / "Media/DLCZips/1600_pri_65/Media/UI/Textures/Horizon/Map/Icons/RallyMapIcons/CHT" /
        std::string(64, 'a') / "MapIconSheetSmall.xds";
    if (scenario == "success-dlc") {
      const auto file = BenchmarkFilePath(root / package);
      std::filesystem::create_directories(file.parent_path());
      std::ofstream(file) << "immutable DLC fixture";
    }
    std::ofstream(root / "benchmark-request.txt")
        << (scenario == "invalid" ? "invalid" : "forza-benchmark-v1") << '\n';
    ConfigurePaths(paths);
    if (scenario == "invalid") {
      assert(Snapshot().screen == Screen::hidden && !Enabled());
      assert(paths.user_data_root == root);
    } else {
      assert(!std::filesystem::exists(root / "benchmark-request.txt"));
#if FORZA_TITLE_UPDATE == 4
      if (success) {
        assert(Enabled() && Snapshot().screen == Screen::running);
        assert(paths.user_data_root != root);
        assert(!std::filesystem::equivalent(profile / "save",
            paths.user_data_root / "1234567890ABCDEF/4D5309C9/00000001/save"));
        std::ofstream(paths.user_data_root / "1234567890ABCDEF/4D5309C9/00000001/save")
            << "benchmark progress";
        std::string original;
        std::getline(std::ifstream(profile / "save"), original);
        assert(original == "original profile");
        if (scenario == "success-dlc") {
          const auto linked = BenchmarkFilePath(paths.user_data_root / package);
          assert(linked.native().size() > 260);
          assert(std::filesystem::equivalent(BenchmarkFilePath(root / package), linked));
        }
        PrepareRuntime();
        assert(rex::cvar::GetFlagByName("frame_rate") == "37");
        // The title list (#540): FlowTick must not run the choice handler on the tick that
        // listed the rows (its scene is not loaded yet; the game closed). Boot's A presses
        // choose Single Player, and the menu leaving the listed state (12) for Single
        // Player's id ends the title step.
        {
          std::vector<uint8_t> arena(0x300000);
          const uint32_t menu = 0x200000, state = menu + 0x260;
          auto write = [&](uint32_t address, uint32_t value) {
            auto *p = forza::GuestPtr(arena.data(), address);
            p[0] = uint8_t(value >> 24); p[1] = uint8_t(value >> 16);
            p[2] = uint8_t(value >> 8); p[3] = uint8_t(value);
          };
          assert(automatic.load() && boot_input.load() && !title_started);
          TitleReady(menu, 10);
          write(menu + 0x170, 0x210000); // the component the old call passed
          write(state, 10);
          FlowTick(ctx, arena.data());
          assert(!title_started && boot_input.load()); // Single Player before the list was shown
          write(state, 12);
          FlowTick(ctx, arena.data()); // would have called the handler: no guest code here
          assert(!title_started && boot_input.load());
          write(state, 10);
          FlowTick(ctx, arena.data());
          assert(title_started && !boot_input.load());
        }
        Close();
        FlowTick(ctx, nullptr);
        assert(restarts == 1 && std::filesystem::exists(root / "benchmark-return.txt"));
        paths.user_data_root = root;
        automatic.store(false); // model a new process, preserving only its environment
        ConfigurePaths(paths);
        assert(paths.user_data_root == root && !Enabled());
        assert(!std::filesystem::exists(root / "benchmark-return.txt"));
        assert(!std::getenv("FORZA_BENCHMARK_SESSION") ||
               !*std::getenv("FORZA_BENCHMARK_SESSION"));
        rex::cvar::SetFlagByName("frame_rate", "30");
        PrepareRuntime();
        assert(rex::cvar::GetFlagByName("frame_rate") == "37");
      } else {
        assert(!Enabled() && Snapshot().screen == Screen::setup);
        assert(paths.user_data_root == root);
        assert(Snapshot().status == "Play and save once before running the benchmark");
        // A failed request is consumed; a later normal boot cannot retry it.
        ConfigurePaths(paths);
        assert(!Enabled() && paths.user_data_root == root && restarts == 0);
      }
#else
      assert(!Enabled() && Snapshot().screen == Screen::setup);
      assert(Snapshot().status == "Benchmark mode requires Title Update 4");
      assert(paths.user_data_root == root && restarts == 0);
#endif
    }
  }
  std::filesystem::remove_all(BenchmarkFilePath(root));
  std::cout << "benchmark session " << scenario << " passed\n";
} catch (const std::exception &error) {
  std::cerr << "Benchmark session test failed: " << error.what() << '\n';
  return 1;
}
