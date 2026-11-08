// src/hooks/fm4_track_mount.cpp (#648) against the SDK's own file system: an imported circuit's
// folder seen at game:\media\..., the game folder's other files left as they are, and the
// converted track's switches as defaults that a set variable outranks.
// Linux, with the SDK (SDK=<sdk>/linux-amd64):
//   clang++ -std=gnu++23 -msse4.1 -DSPDLOG_COMPILED_LIB -DSPDLOG_FMT_EXTERNAL -isystem $SDK/include \
//     forza-recomp/tests/fm4_track_mount_test.cpp forza-recomp/src/hooks/fm4_track_mount.cpp \
//     -L$SDK/lib -lrexruntime -Wl,-rpath,$SDK/lib -fuse-ld=lld -o fm4-mount-test && ./fm4-mount-test
#include "../src/hooks/fm4_track_mount.h"

#include <rex/cvar.h>
#include <rex/filesystem/devices/host_path_device.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/file.h>
#include <rex/filesystem/vfs.h>

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <unistd.h>

namespace fs = std::filesystem;

// The lap timer (src/hooks/fm4_lap_timer.cpp) is not linked here: what the mount asks it.
static std::filesystem::path lap_file, laps_file;
namespace forza::fm4_lap {
void Configure(const std::filesystem::path& lap, const std::filesystem::path& laps) {
  lap_file = lap;
  laps_file = laps;
}
}  // namespace forza::fm4_lap

static int failures = 0;
#define CHECK(c)                                                       \
  do {                                                                 \
    if (!(c)) {                                                        \
      std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); \
      ++failures;                                                      \
    }                                                                  \
  } while (0)

static void Put(const fs::path& p, const std::string& text) {
  fs::create_directories(p.parent_path());
  std::ofstream(p, std::ios::binary) << text;
}

static std::string Env(const char* name) {
  const char* v = std::getenv(name);
  return v ? v : "(unset)";
}

// Opens a file as the game does (NtCreateFile: the folder, then the name in it) and reads its size.
static bool Opens(rex::filesystem::VirtualFileSystem& vfs, const char* path, uint64_t size) {
  rex::filesystem::File* file = nullptr;
  rex::filesystem::FileAction action{};
  const auto status = vfs.OpenFile(nullptr, path, rex::filesystem::FileDisposition::kOpen,
                                   rex::filesystem::FileAccess::kFileReadData, false, true, &file, &action);
  if (status != 0 || !file) {
    std::fprintf(stderr, "%s: open failed (%08X)\n", path, unsigned(status));
    return false;
  }
  const bool ok = file->entry()->size() == size;
  file->Destroy();
  return ok;
}

int main() {
  const fs::path tmp = fs::temp_directory_path() / ("fm4-mount-test-" + std::to_string(::getpid()));
  fs::remove_all(tmp);
  const fs::path game = tmp / "game", track = tmp / "data" / "fm4-tracks" / "suzuka";
  Put(game / "media" / "zipmanifest.xml", "fh1");
  Put(game / "media" / "tracks" / "colorado" / "bin.zip", "colorado");
  Put(game / "media" / "audio" / "tracks" / "colorado" / "amb.fev", "co");
  Put(track / "media" / "zipmanifest.xml", "with suzuka");
  Put(track / "media" / "tracks" / "suzuka" / "bin.zip", "suzuka");
  Put(track / "media" / "tracks" / "suzuka" / "Ribbon_00" / "Suzuka_00.pvs", "pvs");
  Put(track / "media" / "tracks" / "suzuka" / "fm4_stream_blocks.txt", "42783");
  Put(track / "media" / "audio" / "tracks" / "Suzuka" / "amb.fev", "su");
  Put(track / "stx" / "suzuka_stx_numbers.bin", "\x01");
  Put(track / "spawn.txt", "605.6,598.6,-134.0,0.612,-0.791\n");

  rex::filesystem::VirtualFileSystem vfs;
  // as the runtime mounts the game folder (runtime.cpp)
  auto device = std::make_unique<rex::filesystem::HostPathDevice>("\\Device\\Harddisk0\\Partition1", game, true);
  CHECK(device->Initialize());
  CHECK(vfs.RegisterDevice(std::move(device)));
  CHECK(vfs.RegisterSymbolicLink("game:", "\\Device\\Harddisk0\\Partition1"));

  // nothing set: nothing mounted, nothing changed
  forza::fm4_mount::Install(&vfs);
  CHECK(forza::fm4_mount::TrackPath().empty());
  CHECK(vfs.ResolvePath("game:\\media\\tracks\\suzuka") == nullptr);
  CHECK(Env("FORZA_FM4_COL") == "(unset)");

  // a developer's own spawn outranks the track's
  setenv("FORZA_TRACK_SPAWN", "1,2,3", 1);
  setenv("FORZA_TRACK_NO_TRAFFIC", "0", 1);  // 0: that switch off
  rex::cvar::SetFlagByName("fm4_track_dir", track.string());
  forza::fm4_mount::Install(&vfs);
  CHECK(forza::fm4_mount::TrackPath() == "game:\\media\\tracks\\suzuka");
  auto* bin = vfs.ResolvePath("game:\\media\\tracks\\suzuka\\bin.zip");
  CHECK(bin && bin->size() == 6);
  auto* pvs = vfs.ResolvePath("game:\\media\\tracks\\SUZUKA\\ribbon_00\\suzuka_00.pvs");
  CHECK(pvs && pvs->size() == 3);
  auto* dir = vfs.ResolvePath(forza::fm4_mount::TrackPath());
  CHECK(dir && dir->GetChild("fm4_stream_blocks.txt") != nullptr);
  auto* manifest = vfs.ResolvePath("game:\\media\\zipmanifest.xml");
  CHECK(manifest && manifest->size() == 11);  // the track's, not FH1's
  auto* amb = vfs.ResolvePath("game:\\media\\audio\\tracks\\suzuka\\amb.fev");
  CHECK(amb && amb->size() == 2);
  // listed and opened as the game's own files (a file through its folder's entry)
  auto* tracks = vfs.ResolvePath("game:\\media\\tracks");
  CHECK(tracks && tracks->GetChild("suzuka") != nullptr && tracks->GetChild("colorado") != nullptr);
  CHECK(Opens(vfs, "game:\\media\\tracks\\suzuka\\bin.zip", 6));
  CHECK(Opens(vfs, "game:\\media\\zipmanifest.xml", 11));
  CHECK(Opens(vfs, "game:\\media\\audio\\tracks\\Suzuka\\amb.fev", 2));
  CHECK(Opens(vfs, "game:\\media\\tracks\\colorado\\bin.zip", 8));
  // the game's own files stay
  auto* colorado = vfs.ResolvePath("game:\\media\\tracks\\colorado\\bin.zip");
  CHECK(colorado && colorado->size() == 8);
  auto* co_amb = vfs.ResolvePath("game:\\media\\audio\\tracks\\colorado\\amb.fev");
  CHECK(co_amb && co_amb->size() == 2);
  CHECK(fs::file_size(game / "media" / "zipmanifest.xml") == 3);
  // the converted track's switches
  CHECK(Env("FORZA_INSECURE_FILES") == "zipmanifest.xml|\\tracks\\suzuka\\");
  CHECK(Env("FORZA_FM4_STX") == "suzuka|" + (track / "stx" / "suzuka_stx_numbers.bin").string());
  CHECK(Env("FORZA_FM4_COL") == "1");
  CHECK(Env("FORZA_TRACK_NO_TRAFFIC") == "(unset)");
  CHECK(Env("FORZA_TRACK_SPAWN") == "1,2,3");
  CHECK(lap_file == track / "lap.txt");
  CHECK(laps_file == tmp / "data" / "fm4-tracks" / "laps" / "suzuka.jsonl");

  fs::remove_all(tmp);
  if (failures) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return 1;
  }
  std::puts("fm4_track_mount_test: ok");
  return 0;
}
