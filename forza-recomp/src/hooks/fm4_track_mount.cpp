// An imported Forza Motorsport 4 circuit, mounted into the game's folder (#648, experimental).
//
// The launcher's Import FM4 tracks keeps a converted circuit in <data>/fm4-tracks/<track>:
//   media/tracks/<track>/...        the track (bin.zip, ribbons, collision, fm4_stream_blocks.txt)
//   media/audio/tracks/<Track>/...  its ambience sound banks
//   media/zipmanifest.xml           FH1's archive list with the track's bin.zip in it
//   stx/<track>_stx_numbers.bin     FM4's static texture pack numbers
//   spawn.txt                       x,y,z,fx,fz: the start position on the grid
// and passes that folder as fm4_track_dir (its database is a mod, loaded as mods are). These
// must be loose files of the game: FH1 opens a track's ribbons, sound banks and zip manifest by
// path. The folder is a read-only host device of its own, and its entries are put into the game
// folder's tree: media\tracks\<track> and media\audio\tracks\<Track> as folders of
// game:\media\..., media\zipmanifest.xml in place of the game's. So the game lists and opens them
// as its own files (a VFS symbolic link would not do: a file is opened through its folder's
// entry, and game:\media\tracks would not list the track), and the game folder on disk is never
// changed. One limit: a mounted folder opened as a handle of its own through its game folder
// (NtCreateFile on game:\media\tracks\<track> itself) fails, as the SDK checks that the folder
// exists in the game folder on disk. Listing it and opening the files in it work, which is
// enough for FH1 to load Suzuka and drive it.
//
// A mounted track with its fm4_stream_blocks.txt (the converter's marker) also gets the switches
// src/hooks/fm4_track.cpp reads, as defaults: a variable already set (a developer's) is kept.
//   FORZA_INSECURE_FILES  zipmanifest.xml|\tracks\<track>\  (the changed files open as plain ones)
//   FORZA_FM4_STX         <track>|<dir>/stx/<track>_stx_numbers.bin
//   FORZA_FM4_COL         1 (the collision read whole from the track's .col files)
//   FORZA_TRACK_NO_TRAFFIC 1
//   FORZA_TRACK_SPAWN     spawn.txt
// (a variable set to 0 turns its switch off), and the lap timer (fm4_lap_timer.cpp) times it from its lap.txt.
// FORZA_TRACK_DIAG (fm4_track.cpp's diagnostics) is left as it is: off unless a developer sets it.
#include "fm4_track_mount.h"
#include "fm4_lap_timer.h"

#include "../platform/mod_archive.h"

#include <rex/cvar.h>
#include <rex/filesystem/devices/host_path_device.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/vfs.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/thread/mutex.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <string>
#include <system_error>
#include <vector>

REXCVAR_DEFINE_STRING(fm4_track_dir, "", "Mods",
                      "An imported Forza Motorsport 4 circuit to drive (experimental): its folder "
                      "(<data>/fm4-tracks/<track>, made by the launcher's Import FM4 tracks); "
                      "empty: FH1's own Colorado");

namespace forza::fm4_mount {
namespace {

namespace fs = std::filesystem;
namespace mods = forza::mods;

constexpr const char* kDevice = "\\FM4_TRACK";

std::string track_path;

std::string Lower(std::string s) {
  for (auto& c : s) c = char(std::tolower(static_cast<unsigned char>(c)));
  return s;
}

void Default(const char* name, const std::string& value) {
  if (value.empty()) return;
  if (const char* have = std::getenv(name); have && std::string(have) == "0") {
    // "0": the developer turns the switch off (fm4_track.cpp reads any value as on)
#ifdef _WIN32
    _putenv_s(name, "");
#else
    unsetenv(name);
#endif
    REXLOG_INFO("FM4 track: {} off (set to 0)", name);
    return;
  }
  if (const char* have = std::getenv(name); have) {
    REXLOG_INFO("FM4 track: {} kept as set ({})", name, have);
    return;
  }
#ifdef _WIN32
  _putenv_s(name, value.c_str());
#else
  setenv(name, value.c_str(), 0);
#endif
  REXLOG_INFO("FM4 track: {}={}", name, value);
}

// The single folder under dir (case as stored), or "" when there is none or several.
std::string OnlyFolder(const fs::path& dir) {
  std::error_code ec;
  std::string found;
  for (fs::directory_iterator it(dir, ec); !ec && it != fs::directory_iterator(); it.increment(ec)) {
    std::error_code dir_ec;
    if (!it->is_directory(dir_ec)) continue;
    if (!found.empty()) return "";
    found = mods::Utf8(it->path().filename());
  }
  return found;
}

std::string FirstLine(const fs::path& p) {
  std::ifstream in(p, std::ios::binary);
  std::string line;
  std::getline(in, line);
  while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
  return line;
}

using rex::filesystem::Entry;

// An Entry's children, which only Entry and its device may touch: the member pointer named
// through a subclass is a pointer to Entry's member (as src/kernel/mod_overlay.cpp does).
struct ChildList : Entry {
  static std::vector<std::unique_ptr<Entry>>& Of(Entry* entry) { return entry->*(&ChildList::children_); }
};

// Entries of the game folder a mounted file took the place of, kept alive: nothing should
// refer to them, but nothing may dangle.
std::vector<std::unique_ptr<Entry>>& Replaced() {
  static std::vector<std::unique_ptr<Entry>> list;
  return list;
}

// Moves `own` (an entry of the track's device) into the game folder's entry `into`, in place of
// a child of the same name (any case) or after its children.
bool Graft(Entry* own, Entry* into) {
  Entry* from = own ? own->parent() : nullptr;
  if (!from || !into) return false;
  auto lock = rex::thread::global_critical_region::AcquireDirect();
  auto& src = ChildList::Of(from);
  auto it = std::find_if(src.begin(), src.end(), [&](const auto& c) { return c.get() == own; });
  if (it == src.end()) return false;
  std::unique_ptr<Entry> moved = std::move(*it);
  src.erase(it);
  const std::string name = Lower(own->name());
  for (auto& child : ChildList::Of(into)) {
    if (Lower(child->name()) != name) continue;
    Replaced().push_back(std::move(child));
    child = std::move(moved);
    return true;
  }
  ChildList::Of(into).push_back(std::move(moved));
  return true;
}

bool Mount(rex::filesystem::VirtualFileSystem* vfs, Entry* root, const std::string& own_rel,
           const std::string& game_dir) {
  Entry* own = root->ResolvePath(own_rel);
  Entry* into = vfs->ResolvePath(game_dir);
  if (!Graft(own, into)) {
    REXLOG_ERROR("FM4 track: could not put {} into {}", own_rel, game_dir);
    return false;
  }
  return true;
}

}  // namespace

const std::string& TrackPath() { return track_path; }

void Install(rex::filesystem::VirtualFileSystem* vfs) {
  const std::string setting = REXCVAR_GET(fm4_track_dir);
  if (setting.empty() || !vfs) return;
  const fs::path dir = mods::PathOf(setting);
  const fs::path media = dir / "media";
  const std::string track = OnlyFolder(media / "tracks");
  std::error_code ec;
  if (track.empty() || !fs::is_regular_file(media / "zipmanifest.xml", ec)) {
    REXLOG_ERROR("FM4 track: {} is not an imported circuit (media/tracks/<track>/ and "
                 "media/zipmanifest.xml): starting on FH1's own Colorado", setting);
    return;
  }
  auto device = std::make_unique<rex::filesystem::HostPathDevice>(kDevice, media, true);
  if (!device->Initialize()) {
    REXLOG_ERROR("FM4 track: could not read {}", mods::Utf8(media));
    return;
  }
  Entry* root = device->ResolvePath("");
  if (!root || !vfs->RegisterDevice(std::move(device))) {
    REXLOG_ERROR("FM4 track: could not mount {}", mods::Utf8(media));
    return;
  }
  const std::string key = Lower(track);
  bool ok = Mount(vfs, root, "zipmanifest.xml", "game:\\media") &&
            Mount(vfs, root, "tracks\\" + track, "game:\\media\\tracks");
  std::vector<std::string> sounds;
  for (fs::directory_iterator it(media / "audio" / "tracks", ec); !ec && it != fs::directory_iterator();
       it.increment(ec)) {
    std::error_code dir_ec;
    if (it->is_directory(dir_ec)) sounds.push_back(mods::Utf8(it->path().filename()));
  }
  for (const auto& name : sounds) {
    ok = ok && Mount(vfs, root, "audio\\tracks\\" + name, "game:\\media\\audio\\tracks");
  }
  if (!ok) return;
  track_path = "game:\\media\\tracks\\" + key;
  REXLOG_INFO("FM4 track: {} mounted at {} (experimental)", mods::Utf8(dir), track_path);

  // a world that reaches past 8,192 m says so (wide_world.cpp)
  if (fs::is_regular_file(dir / "wide_world.txt", ec)) Default("FORZA_WIDE_WORLD", "1");

  if (!fs::is_regular_file(media / "tracks" / track / "fm4_stream_blocks.txt", ec)) {
    REXLOG_WARN("FM4 track: {} has no fm4_stream_blocks.txt: not a converted track, no switches set",
                track);
    return;
  }
  Default("FORZA_INSECURE_FILES", "zipmanifest.xml|\\tracks\\" + key + "\\");
  const fs::path stx = dir / "stx" / (key + "_stx_numbers.bin");
  if (fs::is_regular_file(stx, ec)) Default("FORZA_FM4_STX", key + "|" + mods::Utf8(stx));
  Default("FORZA_FM4_COL", "1");
  Default("FORZA_TRACK_NO_TRAFFIC", "1");
  Default("FORZA_TRACK_SPAWN", FirstLine(dir / "spawn.txt"));
  // the lap timer (fm4_lap_timer.cpp): laps kept beside the imported circuits, so a new import
  // of the circuit keeps them
  if (!std::getenv("FORZA_FM4_LAP"))
    forza::fm4_lap::Configure(dir / "lap.txt", dir.parent_path() / "laps" / (key + ".jsonl"));
}

}  // namespace forza::fm4_mount

#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
extern "C" REX_FUNC(__imp__sub_824F4820);

extern "C" REX_FUNC(sub_824F4820) {
  // Both 1000 Club loaders call this to parse a car's challenge definitions.
  // Colorado's location challenges resolve road nodes that FM4 circuits do not have (#769).
  // Return the parser's existing "no challenges loaded" result and let its callers finish.
  if (!forza::fm4_mount::TrackPath().empty() &&
      (ctx.lr == 0x824F4FDC || ctx.lr == 0x824F504C)) {
    REXLOG_INFO("FM4 track: skipping Colorado's 1000 Club challenge definitions");
    ctx.r3.u64 = 0;
    return;
  }
  __imp__sub_824F4820(ctx, base);
}
#endif
