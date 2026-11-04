// forza - mods (#149): the player's mods, laid over the game's files at start, without
// changing a file on disk.
//
// The game merges its archives by priority and the title update's media.zip (update:\media.zip,
// mounted at game:\media\ with priority 100000) outranks all of them, loose files included
// (src/platform/mod_archive.h has the details). So the mods' files become members of that
// archive: its VFS entry is replaced by one whose bytes are the update's archive plus the mods'
// files and a new central directory (forza::mods::BuildArchive), read from the original
// archive and the mod files as the game asks for them. The game's own zip reader then finds
// a mod's file where it finds the update's, with no change to the game's code.
//
// A mod file that also exists as a loose file of the game (game:\media\..., e.g. db\gamedb.slt
// on the disc) replaces that file's entry too, for code that opens it by path.
//
// The base game (no title update) asks for update:\media.zip too; it gets an empty archive
// (in <cache>/mods_update) on a device of its own, with the mods' files added the same way.
//
// Settings (the launcher sets both): forza_mods_dir, the mods folder (default <user data>/mods);
// forza_mods, the folders to load in load order, '|'-separated (default "*": every folder, by
// name). forza.log lists the mods, each file they replace or add, and the first time the game
// reads each one; a mod that cannot be read is skipped with the reason.

#include "../platform/mod_archive.h"
#include "content_manifest.h"
#include "live.h"

#include <rex/cvar.h>
#include <rex/filesystem.h>
#include <rex/filesystem/devices/host_path_device.h>
#include <rex/filesystem/devices/host_path_entry.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/file.h>
#include <rex/filesystem/vfs.h>
#include <rex/logging.h>
#include <rex/system/xtypes.h>
#include <rex/thread/mutex.h>

#include <algorithm>
#include <atomic>
#include <cstdlib>
#include <exception>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <string>
#include <system_error>
#include <vector>

REXCVAR_DEFINE_STRING(forza_mods, "*", "Mods",
                      "Mods to load, folder names in load order separated by | (a later mod wins "
                      "over an earlier one); * loads every folder in forza_mods_dir by name");
REXCVAR_DEFINE_STRING(forza_mods_dir, "", "Mods",
                      "Folder of mods (one folder per mod, its files under media/ as the game's "
                      "paths are); empty: mods in the user data folder");

namespace {

namespace mods = forza::mods;
namespace fs = std::filesystem;
using rex::filesystem::Entry;
using rex::X_STATUS;

// The host files an overlaid entry reads, shared by every open of the entry. The update's
// archive stays open; at most kOpenModFiles mod files are open at once (HandleCache), and one
// that cannot be opened reads as zeros until a later read opens it.
constexpr size_t kOpenModFiles = 64;

struct Sources {
  struct Source {
    fs::path path;
    std::string label;  // for the log: "media/db/gamedb.slt (Mod name)", or the archive
    bool from_mod = false;
    std::atomic<bool> read_logged{false}, short_logged{false}, open_logged{false};
  };
  std::vector<std::unique_ptr<Source>> list;
  std::unique_ptr<mods::HandleCache<rex::filesystem::FileHandle>> handles;

  // After the last source is added.
  void Finish() {
    std::vector<bool> pinned;
    for (const auto& s : list) pinned.push_back(!s->from_mod);
    handles = std::make_unique<mods::HandleCache<rex::filesystem::FileHandle>>(
        std::move(pinned), kOpenModFiles, [this](size_t index) {
          return rex::filesystem::FileHandle::OpenExisting(
              list[index]->path, rex::filesystem::FileAccess::kGenericRead);
        });
  }

  size_t Read(int32_t index, uint64_t offset, uint8_t* buffer, size_t size) {
    Source& s = *list[size_t(index)];
    std::shared_ptr<rex::filesystem::FileHandle> handle = handles->Get(size_t(index));
    if (!handle) {
      if (!s.open_logged.exchange(true)) {
        REXLOG_ERROR("Mods: cannot open {} ({}); the game reads zeros in its place until it "
                     "opens again", s.label, mods::Utf8(s.path));
      }
      return 0;
    }
    size_t done = 0;
    while (done < size) {  // a short read is not the end of the file yet
      size_t got = 0;
      if (!handle->Read(size_t(offset + done), buffer + done, size - done, &got) || !got) break;
      done += got;
    }
    return done;
  }
};

struct Overlay {
  mods::Layout layout;
  std::shared_ptr<Sources> sources;
  std::vector<int32_t> segment_source;  // per segment: the mod source it reads, or -1
};

// The game's view of an overlaid file: its bytes come from the layout.
class OverlayFile : public rex::filesystem::File {
 public:
  OverlayFile(uint32_t access, Entry* entry, std::shared_ptr<const Overlay> overlay)
      : File(access, entry), overlay_(std::move(overlay)) {}

  void Destroy() override { delete this; }

  X_STATUS ReadSync(std::span<uint8_t> buffer, size_t byte_offset,
                    size_t* out_bytes_read) override {
    *out_bytes_read = 0;
    if (!(file_access_ & (rex::filesystem::FileAccess::kGenericRead |
                          rex::filesystem::FileAccess::kFileReadData))) {
      return X_STATUS_ACCESS_DENIED;
    }
    if (byte_offset >= overlay_->layout.size) return X_STATUS_END_OF_FILE;
    Sources& sources = *overlay_->sources;
    *out_bytes_read = mods::ReadLayout(
        overlay_->layout, byte_offset, buffer.data(), buffer.size(),
        [&](int32_t source, uint64_t offset, uint8_t* out, size_t size) {
          return sources.Read(source, offset, out, size);
        },
        [&](size_t segment) {
          const int32_t source = overlay_->segment_source[segment];
          if (source < 0) return;
          auto& s = *sources.list[size_t(source)];
          if (!s.read_logged.exchange(true)) REXLOG_INFO("Mods: the game reads {}", s.label);
          static const bool trace = [] {
            const char* p = std::getenv("FORZA_MODS_READ_TRACE");
            return p && *p && *p != '0';
          }();
          static std::atomic<int> traced{0};
          if (trace && traced.fetch_add(1) < 100000) {
            REXLOG_INFO("Mods: read {} bytes at {}: {}", buffer.size(), byte_offset, s.label);
          }
        },
        [&](int32_t source) {
          auto& s = *sources.list[size_t(source)];
          if (!s.short_logged.exchange(true)) {
            REXLOG_ERROR("Mods: {} is shorter than when the game started (changed while it "
                         "ran?); the game reads zeros for the rest. Restart the game after "
                         "changing a mod.",
                         s.label);
          }
        });
    return X_STATUS_SUCCESS;
  }

  X_STATUS WriteSync(std::span<const uint8_t>, size_t, size_t* out_bytes_written) override {
    *out_bytes_written = 0;
    return X_STATUS_ACCESS_DENIED;
  }

 private:
  std::shared_ptr<const Overlay> overlay_;
};

// Takes the place of the original entry in its parent (a HostPathEntry, so the VFS checks
// that see one keep working); everything about the file but its size and bytes stays the
// original's.
class OverlayEntry : public rex::filesystem::HostPathEntry {
 public:
  OverlayEntry(rex::filesystem::HostPathEntry* original, std::shared_ptr<const Overlay> overlay)
      : HostPathEntry(original->device(), original->parent(), original->path(),
                      original->host_path()),
        overlay_(std::move(overlay)) {
    attributes_ = original->attributes() | rex::filesystem::kFileAttributeReadOnly;
    size_ = size_t(overlay_->layout.size);
    allocation_size_ = (size_ + 4095) & ~size_t(4095);
    create_timestamp_ = original->create_timestamp();
    access_timestamp_ = original->access_timestamp();
    write_timestamp_ = original->write_timestamp();
  }

  X_STATUS Open(uint32_t desired_access, rex::filesystem::File** out_file) override {
    *out_file = new OverlayFile(desired_access, this, overlay_);
    return X_STATUS_SUCCESS;
  }
  bool can_map() const override { return false; }
  std::unique_ptr<rex::memory::MappedMemory> OpenMapped(rex::memory::MappedMemory::Mode, size_t,
                                                        size_t) override {
    return nullptr;
  }
  void update() override {}  // the size is the overlay's, not the host file's
  bool Truncate() override { return false; }
  bool SetAttributes(uint64_t) override { return false; }
  bool SetCreateTimestamp(uint64_t) override { return false; }
  bool SetAccessTimestamp(uint64_t) override { return false; }
  bool SetWriteTimestamp(uint64_t) override { return false; }

 private:
  std::shared_ptr<const Overlay> overlay_;
};

// An Entry's children, which only Entry and its device may touch: the member pointer named
// through a subclass is a pointer to Entry's member.
struct ChildList : Entry {
  static std::vector<std::unique_ptr<Entry>>& Of(Entry* entry) {
    return entry->*(&ChildList::children_);
  }
};

// The original entries, kept alive: nothing should refer to them, but nothing may dangle.
std::vector<std::unique_ptr<Entry>>& Replaced() {
  static std::vector<std::unique_ptr<Entry>> list;
  return list;
}

bool ReplaceEntry(Entry* original, std::unique_ptr<Entry> replacement) {
  Entry* parent = original->parent();
  if (!parent) return false;
  auto lock = rex::thread::global_critical_region::AcquireDirect();
  for (auto& child : ChildList::Of(parent)) {
    if (child.get() != original) continue;
    Replaced().push_back(std::move(child));
    child = std::move(replacement);
    return true;
  }
  return false;
}

std::shared_ptr<Overlay> MakeOverlay(mods::Layout layout, std::shared_ptr<Sources> sources) {
  auto overlay = std::make_shared<Overlay>();
  overlay->layout = std::move(layout);
  overlay->sources = std::move(sources);
  for (const auto& s : overlay->layout.segments) {
    const bool from_mod = s.source >= 0 && overlay->sources->list[size_t(s.source)]->from_mod;
    overlay->segment_source.push_back(from_mod ? s.source : -1);
  }
  return overlay;
}

std::string Backslashes(std::string path) {
  std::replace(path.begin(), path.end(), '/', '\\');
  return path;
}

// The base game (no title update, so no update: device): update:\\media.zip as an empty archive
// in `folder`, on a device of its own (\\Device\\Harddisk0\\PartitionUpdate would be taken by the
// runtime's null device for \\Device\\Harddisk0, registered before it).
bool MountEmptyUpdate(rex::filesystem::VirtualFileSystem* vfs, const fs::path& folder) {
  std::error_code ec;
  fs::create_directories(folder, ec);
  const fs::path zip = folder / "media.zip";
  std::string empty;
  mods::Put32(empty, mods::kEndSignature);
  empty.append(18, '\0');
  if (fs::file_size(zip, ec) != empty.size() || ec) {
    std::ofstream out(zip, std::ios::binary | std::ios::trunc);
    out.write(empty.data(), std::streamsize(empty.size()));
    if (!out) return false;
  }
  constexpr const char* kMount = "\\FORZA_MODS_UPDATE";
  auto device = std::make_unique<rex::filesystem::HostPathDevice>(kMount, folder, true);
  if (!device->Initialize() || !vfs->RegisterDevice(std::move(device)) ||
      !vfs->RegisterSymbolicLink("update:", kMount)) {
    return false;
  }
  REXLOG_INFO("Mods: no title update: the mods go into an archive of their own at "
              "update:\\media.zip, which the game reads as it would the update's");
  return true;
}

std::vector<std::string> ModsToLoad(const fs::path& dir) {
  const std::string setting = REXCVAR_GET(forza_mods);
  if (setting != "*") return mods::SplitModList(setting);
  std::vector<std::string> all;
  std::error_code ec;
  fs::directory_iterator it(dir, ec);
  for (; !ec && it != fs::directory_iterator(); it.increment(ec)) {
    const std::string name = mods::Utf8(it->path().filename());
    std::error_code dir_ec;
    if (it->is_directory(dir_ec) && !name.empty() && name[0] != '.') all.push_back(name);
  }
  std::sort(all.begin(), all.end());
  return all;
}

}  // namespace

namespace forza {

namespace {

void InstallModsUnguarded(rex::filesystem::VirtualFileSystem* vfs, const fs::path& user_data_root,
                          const fs::path& cache_root) {
  const std::string dir_setting = REXCVAR_GET(forza_mods_dir);
  const fs::path dir = dir_setting.empty() ? user_data_root / "mods" : mods::PathOf(dir_setting);
  std::error_code ec;
  const std::vector<std::string> wanted = ModsToLoad(dir);
  if (wanted.empty()) {
    if (fs::is_directory(dir, ec)) REXLOG_INFO("Mods: none enabled ({})", mods::Utf8(dir));
    return;
  }
  std::string order;
  for (const auto& name : wanted) order += (order.empty() ? "" : ", ") + name;
  REXLOG_INFO("Mods: {} in load order (a later mod wins): {}", mods::Utf8(dir), order);

  std::vector<mods::ScannedMod> scanned;
  for (const auto& name : wanted) {
    mods::ScannedMod mod = mods::ScanMod(dir, name);
    for (const auto& note : mod.ignored) REXLOG_WARN("Mods: {}: left out {}", name, note);
    // A file over 64 MB cannot be a member of the archive: it may only take the place of a
    // loose file of the game.
    for (const auto& f : mod.files) {
      if (!mod.error.empty() || !f.loose_only) continue;
      auto* loose = dynamic_cast<rex::filesystem::HostPathEntry*>(
          vfs->ResolvePath("game:\\media\\" + Backslashes(f.name)));
      if (!loose || (loose->attributes() & rex::filesystem::kFileAttributeDirectory)) {
        mod.error = "media/" + f.name + " is over 64 MB, more than the game's archives hold, and "
                    "the game has no loose file of that name for it to replace";
      }
    }
    if (!mod.error.empty()) {
      REXLOG_ERROR("Mods: skipped {}: {}", name, mod.error);
    } else {
      REXLOG_INFO("Mods: {}: {} file(s)", mod.Title(), mod.files.size());
      // The content manifest the server gets (#567): mods that change which cars exist.
      const bool changes_cars = std::any_of(mod.files.begin(), mod.files.end(), [](const auto& f) {
        return forza::ChangesCars(f.name);
      });
      forza::NoteLoadedMod(mod.manifest.name.empty() ? mod.folder : mod.manifest.name, changes_cars);
    }
    scanned.push_back(std::move(mod));
  }
  std::vector<mods::Conflict> conflicts;
  std::vector<mods::MergedFile> files = mods::MergeMods(scanned, &conflicts);
  for (const auto& c : conflicts) {
    REXLOG_WARN("Mods: media/{} is in {} and {}: {}'s is used (later in the load order)", c.name,
                scanned[c.loser].folder, scanned[c.winner].folder, scanned[c.winner].folder);
  }
  if (files.empty()) {
    REXLOG_WARN("Mods: nothing to load");
    return;
  }

  // The archive: the title update's media.zip with the mods' files. A game without the title
  // update looks for update:\\media.zip all the same: it gets an empty one to add them to.
  std::string link;
  if (!vfs->FindSymbolicLink("update:", link) && !MountEmptyUpdate(vfs, cache_root / "mods_update")) {
    REXLOG_ERROR("Mods: not loaded: could not make update:\\media.zip for them in {}",
                 mods::Utf8(cache_root / "mods_update"));
    return;
  }
  Entry* target = vfs->ResolvePath("update:\\media.zip");
  auto* archive = dynamic_cast<rex::filesystem::HostPathEntry*>(target);
  if (!archive) {
    REXLOG_ERROR("Mods: not loaded: the title update folder (update:) has no media.zip to add "
                 "them to");
    return;
  }
  auto sources = std::make_shared<Sources>();
  auto add_source = [&](const fs::path& path, std::string label, bool from_mod) {
    auto s = std::make_unique<Sources::Source>();
    s->path = path;
    s->label = std::move(label);
    s->from_mod = from_mod;
    sources->list.push_back(std::move(s));
    return int32_t(sources->list.size() - 1);
  };
  const int32_t base = add_source(archive->host_path(), "update:\\media.zip", false);
  mods::ZipDirectory directory;
  std::string error;
  {
    auto handle = rex::filesystem::FileHandle::OpenExisting(
        archive->host_path(), rex::filesystem::FileAccess::kGenericRead);
    const mods::ReadAt read = [&](uint64_t offset, void* buffer, size_t size) {
      size_t got = 0;
      return handle && handle->Read(size_t(offset), buffer, size, &got) && got == size;
    };
    if (!handle || !mods::ReadZipDirectory(read, archive->size(), &directory, &error)) {
      REXLOG_ERROR("Mods: not loaded: cannot read update:\\media.zip ({}): {}",
                   mods::Utf8(archive->host_path()), handle ? error : "cannot open it");
      return;
    }
  }
  std::vector<mods::AddedFile> added;
  std::vector<int32_t> file_source;
  for (const auto& f : files) {
    const auto& mod = scanned[f.mod];
    const std::string label =
        "media/" + f.file.name + " (" + (mod.manifest.name.empty() ? mod.folder : mod.manifest.name) + ")";
    file_source.push_back(add_source(f.file.path, label, true));
    if (!f.file.loose_only) added.push_back({f.file.name, f.file.size, f.file.crc, file_source.back()});
  }
  sources->Finish();
  mods::Layout layout;
  std::vector<bool> replaced;
  if (!mods::BuildArchive(&directory, base, added, &layout, &replaced, &error)) {
    REXLOG_ERROR("Mods: not loaded: {}", error);
    return;
  }
  const size_t entries_before = directory.entries.size();
  size_t replacing = 0;
  for (bool r : replaced) replacing += r;
  std::vector<int> in_archive(files.size(), -1);  // index in `added`, or -1 (loose only)
  for (size_t i = 0, a = 0; i < files.size(); ++i)
    if (!files[i].file.loose_only) in_archive[i] = int(a++);
  auto overlay = MakeOverlay(std::move(layout), sources);
  const uint64_t archive_size = overlay->layout.size;
  if (!ReplaceEntry(archive, std::make_unique<OverlayEntry>(archive, overlay))) {
    REXLOG_ERROR("Mods: not loaded: could not put the archive in place of update:\\media.zip");
    return;
  }

  // Loose files of the game with the same paths (opened by path, not through the archives).
  size_t loose = 0;
  for (size_t i = 0; i < files.size(); ++i) {
    const auto& f = files[i];
    const std::string game_path = "game:\\media\\" + Backslashes(f.file.name);
    auto* entry = dynamic_cast<rex::filesystem::HostPathEntry*>(vfs->ResolvePath(game_path));
    std::string also;
    if (entry && !(entry->attributes() & rex::filesystem::kFileAttributeDirectory)) {
      mods::Layout whole;
      whole.AddFile(file_source[i], 0, f.file.size);
      if (ReplaceEntry(entry, std::make_unique<OverlayEntry>(entry, MakeOverlay(std::move(whole), sources)))) {
        also = ", and the loose file " + game_path;
        ++loose;
      }
    }
    const char* what = in_archive[i] < 0                ? "over 64 MB, not in the archive"
                       : replaced[size_t(in_archive[i])] ? "in place of the title update's copy"
                                                         : "added (over any copy in the game's other archives)";
    REXLOG_INFO("Mods: media/{} from {}: {}{}", f.file.name, scanned[f.mod].folder, what, also);
  }
  REXLOG_INFO("Mods: update:\\media.zip holds {} files ({} replaced, {} added by mods; {} loose "
              "file(s) replaced), {:.1f} MB",
              entries_before - replacing + added.size(), replacing, added.size() - replacing,
              loose, archive_size / 1048576.0);
}

}  // namespace

// Mods are optional: whatever goes wrong here (a filesystem error the checks above did not
// catch, a name the platform cannot convert) skips them and the game starts as it is.
void InstallMods(rex::filesystem::VirtualFileSystem* vfs, const fs::path& user_data_root,
                 const fs::path& cache_root) {
  try {
    InstallModsUnguarded(vfs, user_data_root, cache_root);
  } catch (const std::exception& e) {
    REXLOG_ERROR("Mods: not loaded: {}", e.what());
  }
}

}  // namespace forza
