#include "debug/performance_settings.h"

#include <cassert>
#include <map>
#include <set>
#include <vector>

int main() {
  using namespace forza::performance_settings;
  std::map<std::string, std::string> flags{
      {"scene_msaa", "game"}, {"resolution_scale", "1"},
      {"readback_resolve", "some"}, {"vsync", "false"},
      {"live_server", "private.example"}, {"user_data_root", "private/path"}};
  // A config override applied after defaults must be read as it stands now.
  flags["resolution_scale"] = "2";
  std::set<std::string> requested;
  std::vector<std::string> lines;
  Snapshot([&](std::string_view key) -> std::optional<std::string> {
    assert(requested.insert(std::string(key)).second);
    const auto it = flags.find(std::string(key));
    return it == flags.end() ? std::nullopt : std::optional(it->second);
  }, [&](const std::string& line) { lines.push_back(line); });
  assert(lines.size() == 4);
  assert(lines[0].find("resolution_scale=2;") != std::string::npos);
  assert(lines[0].find("vsync=false;") != std::string::npos);
  assert(lines[0].find("gpu_backend=<unavailable>;") != std::string::npos);
  assert(lines[1].find("scene_msaa=game;") != std::string::npos);
  assert(lines[2].find("readback_resolve=some;") != std::string::npos);
  assert(requested.find("live_server") == requested.end());
  assert(requested.find("user_data_root") == requested.end());
  for (const auto& line : lines) assert(line.find("private") == std::string::npos);
  assert(Value(std::nullopt) == "<unavailable>");
  assert(Value(std::string()) == "<empty>");
  assert(Value(std::string("x\r\ny\tz")) == "x??y?z");
  assert(Value(std::string(1000, 'x')) == std::string(64, 'x') + "...");
}
