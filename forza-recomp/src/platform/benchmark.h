#pragma once
#include "benchmark_stats.h"
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>
struct PPCContext;
namespace rex {
struct PathConfig;
}
namespace forza::benchmark {
enum class Screen { hidden, setup, running, results };
struct View {
  Screen screen = Screen::hidden;
  std::string status, folder, settings;
  Statistics stats;
  std::vector<float> frame_ms;
  double elapsed = 0, duration = 60;
  bool valid = false;
};
bool Enabled();
bool WantsPad();
void ConfigurePaths(rex::PathConfig &paths);
void PrepareRuntime();
void Initialize(const std::filesystem::path &root);
void Show();
void Run();
void Close();
View Snapshot();
void TitleReady(uint32_t menu, uint32_t single_player_id);
void PreRaceReady(uint32_t screen);
uint16_t Pad(uint16_t buttons, bool &block_all);
void Tick(PPCContext &ctx, uint8_t *base);
void OnSwap();
// Seconds since the last guest frame (VdSwap), or -1 before the first.
double SecondsSinceSwap();
void FlowTick(PPCContext &ctx, uint8_t *base);
void Progress(const char *text, double elapsed, double duration);
void Completed(bool valid, const char *reason,
               const std::vector<double> &frames);
void Cancel();
void Fail(const char *reason);
} // namespace forza::benchmark
