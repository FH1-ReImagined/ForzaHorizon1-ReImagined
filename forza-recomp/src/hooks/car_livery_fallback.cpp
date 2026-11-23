// forza - a garage car whose paint job can't be loaded gets its factory paint when it becomes the
// current car instead of ending the game (#922). What the game does and why it ended:
// src/platform/car_livery_fallback.h.
//
// Only the two calls that make the current car's driving paint job from the handle its
// description keeps are looked at (sub_825718D8's call at 82571990, TU4 825E77D8: r29 = the
// career car table, r30 = the garage id; sub_82571A58's at 82571B18, TU4 825E7960: the same two
// in r30 and r29), and only when that handle's paint job failed to load: the hook waits for it as
// the game would (WaitForLoad, sub_82412688 / TU4 sub_82442638, the wait sub_82E61748 does).
// Otherwise the game's own call runs unchanged. For a failed one it makes the factory colour the
// way the game's fresh path falls back to it (sub_82C94398 / TU4 sub_82D55428 with the row's
// CarId and DefaultManufacturerColorIndex), puts it in the driving paint job (the caller's r1+84)
// and releases the handle as the game's call does at its end. Only when the paint job is a file
// that the profile doesn't have, or that is empty, does it clear the row's LiveryFileName too,
// and it says so only when a second query shows the column clear. A file that is there but
// failed stays named in the save; for the rest of the session the game's fresh path
// (sub_82547EB8 / TU4 sub_825B7FA0, which both callers also take, and which would queue the same
// file again) gives that row its factory colour instead. A warning names the garage id and the
// car once.

#include "../platform/car_livery_fallback.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/filesystem.h>
#include <rex/filesystem/entry.h>
#include <rex/filesystem/vfs.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <mutex>
#include <set>
#include <string>
#include <utility>

extern "C" REX_FUNC(__imp__sub_82C9C440);  // the paint job's variant of a type (r5) from a handle (r4)
extern "C" REX_FUNC(sub_82412688);         // WaitForLoad(resource): false when the load failed
extern "C" REX_FUNC(__imp__sub_82547EB8);  // the car's paint job from its Career_Garage row
extern "C" REX_FUNC(sub_82C94398);         // the factory colour paint job (out, CarId, colour)

namespace {

namespace clf = forza::car_livery_fallback;

// The two calls (TU4 825E77D8, 825E7960) and the profile database (TU4 0x833D2FFC); written out
// in FORZA_ADDR so that tools/tu_address_map.py --cpp finds them.
#define GET_IN_CALL FORZA_ADDR(0x82571990)
#define CAREER_CALL FORZA_ADDR(0x82571B18)
#define PROFILE_DB FORZA_ADDR(0x832E5510)
constexpr uint32_t kQuerySlot = 0x20, kReleaseSlot = 12;
constexpr uint32_t kRowCountSlot = 0xB8, kIntegerSlot = 0x90;
constexpr uint32_t kStateByte = 40, kKeyType = 0x40, kKeyName = 0x44;
constexpr uint32_t kFreshLocal = 92;  // both callers' r1+92, used only by their fresh branch

uint32_t Read(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

void Write(uint8_t* base, uint32_t address, uint32_t value) {
  uint8_t* p = forza::GuestPtr(base, address);
  p[0] = uint8_t(value >> 24);
  p[1] = uint8_t(value >> 16);
  p[2] = uint8_t(value >> 8);
  p[3] = uint8_t(value);
}

// object->vtable[slot](args) on a copy of the hook's context.
uint32_t Virtual(const PPCContext& ctx, uint8_t* base, uint32_t object, uint32_t slot,
                 std::initializer_list<uint32_t> args) {
  const uint32_t table = object ? Read(base, object) : 0;
  const uint32_t address = table ? Read(base, table + slot) : 0;
  PPCFunc* fn = address ? rex::runtime::ResolveIndirectFunction(address) : nullptr;
  if (!fn) return 0;
  PPCContext call = ctx;
  unsigned i = 3;
  for (uint32_t value : args) {
    switch (i++) {
      case 3: call.r3.u64 = value; break;
      case 4: call.r4.u64 = value; break;
      case 5: call.r5.u64 = value; break;
    }
  }
  fn(call, base);
  return call.r3.u32;
}

void Release(const PPCContext& ctx, uint8_t* base, uint32_t slot_address) {
  const uint32_t object = Read(base, slot_address);
  Write(base, slot_address, 0);
  if (object) Virtual(ctx, base, object, kReleaseSlot, {object});
}

// The career car table's name (an MSVC std::string at +4: size +20, capacity +24).
std::string TableName(uint8_t* base, uint32_t career_car) {
  if (!career_car) return {};
  const uint32_t size = Read(base, career_car + 20), capacity = Read(base, career_car + 24);
  if (size == 0 || size > 64 || capacity < size) return {};
  const uint32_t chars = capacity >= 16 ? Read(base, career_car + 4) : career_car + 4;
  if (!chars) return {};
  return std::string(reinterpret_cast<const char*>(forza::GuestPtr(base, chars)), size);
}

// Runs a statement on the profile database. With `value`, the first column of the first row
// (false when there is none). Without, true only when the database ran it and gave back a result.
bool Run(const PPCContext& ctx, uint8_t* base, const std::string& sql, int32_t* value) {
  const uint32_t db = Read(base, PROFILE_DB);
  if (!db || sql.empty()) return false;
  auto* memory = REX_KERNEL_MEMORY();
  const uint32_t block = memory->SystemHeapAlloc(uint32_t(8 + sql.size() + 1));
  if (!block) return false;
  Write(base, block, 0);
  std::memcpy(forza::GuestPtr(base, block + 8), sql.c_str(), sql.size() + 1);
  Virtual(ctx, base, db, kQuerySlot, {block, db, block + 8});
  bool found = false;
  const uint32_t records = Read(base, block);
  if (records) {
    if (!value) {
      found = true;
    } else if (int32_t(Virtual(ctx, base, records, kRowCountSlot, {records})) > 0) {
      *value = int32_t(Virtual(ctx, base, records, kIntegerSlot, {records, 0}));
      found = true;
    }
    Virtual(ctx, base, records, kReleaseSlot, {records});
  }
  memory->SystemHeapFree(block);
  return found;
}

// The key's file name ('<profile xuid>:\Liveries\<id>.liv'), printable characters only.
std::string KeyName(uint8_t* base, uint32_t livery) {
  const uint32_t chars = Read(base, livery + kKeyName);
  if (chars < 0x10000 || chars >= 0xE0000000) return {};
  const char* p = reinterpret_cast<const char*>(forza::GuestPtr(base, chars));
  std::string name;
  for (size_t i = 0; i < 260 && p[i]; ++i) {
    if (p[i] < 32 || p[i] > 126) return {};
    name += p[i];
  }
  return name;
}

// What the profile holds for that name; kUnknown when its profile isn't mounted.
clf::FileState HostFile(const std::string& name) {
  const std::string root = clf::ProfileRoot(name);
  auto* vfs = rex::system::kernel_state()->file_system();
  if (root.empty() || !vfs) return clf::FileState::kUnknown;
  std::string target;
  if (!vfs->FindSymbolicLink(root + ":", target)) return clf::FileState::kUnknown;
  rex::filesystem::Entry* entry = vfs->ResolvePath(name);
  if (!entry) return clf::FileState::kMissing;
  return entry->size() == 0 ? clf::FileState::kEmpty : clf::FileState::kPresent;
}

const char* Describe(clf::FileState file) {
  switch (file) {
    case clf::FileState::kMissing: return "is not in the profile";
    case clf::FileState::kEmpty: return "is empty";
    case clf::FileState::kPresent: return "could not be read";
    default: return "could not be loaded";
  }
}

// The rows (table, garage id) whose paint job file failed but stays named in the save.
std::mutex g_kept_lock;
std::set<std::pair<std::string, uint32_t>> g_kept;

void Keep(const std::string& table, uint32_t garage_id) {
  std::lock_guard<std::mutex> hold(g_kept_lock);
  if (g_kept.size() < 256) g_kept.emplace(table, garage_id);
}

bool Kept(const std::string& table, uint32_t garage_id) {
  std::lock_guard<std::mutex> hold(g_kept_lock);
  return g_kept.count({table, garage_id}) != 0;
}

// The row's CarId and DefaultManufacturerColorIndex.
bool FactoryColour(const PPCContext& ctx, uint8_t* base, const std::string& table,
                   uint32_t garage_id, int32_t* car, int32_t* colour) {
  return Run(ctx, base, clf::ColumnSql(table, "CarId", garage_id), car) &&
         Run(ctx, base, clf::ColumnSql(table, "DefaultManufacturerColorIndex", garage_id), colour);
}

// The factory colour paint job into `slot` (nulled first), as the game's fresh path makes it.
void MakeFactory(const PPCContext& ctx, uint8_t* base, uint32_t slot, int32_t car, int32_t colour) {
  Write(base, slot, 0);
  PPCContext make = ctx;
  make.r3.u64 = slot;
  make.r4.u64 = uint32_t(car);
  make.r5.u64 = uint32_t(colour) & 0xFF;
  sub_82C94398(make, base);
}

bool FirstReport(uint32_t garage_id) {
  static std::mutex lock;
  static std::set<uint32_t> reported;
  std::lock_guard<std::mutex> hold(lock);
  return reported.size() < 256 && reported.insert(garage_id).second;
}

}  // namespace

// r3: the driving paint job (out), r4: the handle, r5: the type, r6: flags.
extern "C" REX_FUNC(sub_82C9C440) {
  const uint32_t out = ctx.r3.u32, holder = ctx.r4.u32;
  const uint32_t livery = holder ? Read(base, holder) : 0;
  const uint32_t lr = uint32_t(ctx.lr);
  if ((lr != GET_IN_CALL && lr != CAREER_CALL) || !out || !livery) {
    __imp__sub_82C9C440(ctx, base);
    return;
  }
  const uint8_t* state = forza::GuestPtr(base, livery + kStateByte);
  bool failed = false;
  if (!(state[0] == 4 && state[1] == 4)) {
    PPCContext wait = ctx;
    wait.r3.u64 = livery;
    sub_82412688(wait, base);
    failed = (wait.r3.u32 & 0xFF) == 0;
  }
  if (!failed) {
    __imp__sub_82C9C440(ctx, base);
    return;
  }
  const uint32_t key_type = Read(base, livery + kKeyType);
  const std::string name = key_type == clf::kKeyFile ? KeyName(base, livery) : std::string();
  const clf::FileState file = name.empty() ? clf::FileState::kUnknown : HostFile(name);
  const clf::Action action = clf::Decide(failed, key_type, file);

  const bool get_in = lr == GET_IN_CALL;
  const uint32_t career_car = get_in ? ctx.r29.u32 : ctx.r30.u32;
  const uint32_t garage_id = get_in ? ctx.r30.u32 : ctx.r29.u32;
  const std::string table = TableName(base, career_car);
  int32_t car = 0, colour = 0;
  const bool row = FactoryColour(ctx, base, table, garage_id, &car, &colour);
  bool cleared = false;
  if (action == clf::Action::kDetach) {
    int32_t clear = 0;
    cleared = Run(ctx, base, clf::ClearLiverySql(table, garage_id), nullptr) &&
              Run(ctx, base, clf::ClearedSql(table, garage_id), &clear) && clear == 1;
  }
  if (key_type == clf::kKeyFile && !cleared && clf::ValidTable(table)) Keep(table, garage_id);

  // The factory colour, as the game's fresh path falls back to it; without the row, that path.
  const uint32_t local = ctx.r1.u32 + kFreshLocal;
  if (row) {
    MakeFactory(ctx, base, local, car, colour);
  } else {
    Write(base, local, 0);
    PPCContext make = ctx;
    make.r3.u64 = local;
    make.r4.u64 = career_car;
    make.r5.u64 = garage_id;
    make.r6.u64 = clf::kDrivingFlags;
    __imp__sub_82547EB8(make, base);
  }
  const uint32_t made = Read(base, local);
  bool made_failed = !made;
  if (!made_failed) {
    PPCContext wait = ctx;
    wait.r3.u64 = made;
    sub_82412688(wait, base);
    made_failed = (wait.r3.u32 & 0xFF) == 0;
  }

  if (FirstReport(garage_id)) {
    const char* what = key_type == clf::kKeyFile ? "its paint job file" : "its paint";
    const char* why = key_type == clf::kKeyFile ? Describe(file) : "could not be loaded";
    const char* save = cleared                              ? "; the save no longer names that file"
                       : action == clf::Action::kDetach     ? "; the save could not be changed"
                       : key_type == clf::kKeyFile          ? "; the save still names that file"
                                                            : "";
    if (made_failed) {
      REXLOG_ERROR("Cars: garage car {} (car {}): {} {}, and its factory paint could not be made "
                   "either{} (#922)",
                   garage_id, car, what, why, save);
    } else {
      REXLOG_WARN("Cars: garage car {} (car {}): {} {}; it gets its factory paint{} (#922)",
                  garage_id, car, what, why, save);
    }
  }

  const uint32_t previous = Read(base, out);
  Write(base, out, made);
  Write(base, local, 0);
  if (previous) Virtual(ctx, base, previous, kReleaseSlot, {previous});
  // The game's call releases the handle it was given before it returns.
  Release(ctx, base, holder);
}

// The game's fresh path: r3 = the paint job (out), r4 = the career car table, r5 = the garage id,
// r6 = flags; returns r3. For a row kept above, the factory colour instead of loading the file
// that failed again.
extern "C" REX_FUNC(sub_82547EB8) {
  const uint32_t out = ctx.r3.u32, career_car = ctx.r4.u32, garage_id = ctx.r5.u32;
  bool kept = false;
  {
    std::lock_guard<std::mutex> hold(g_kept_lock);
    kept = !g_kept.empty();
  }
  const std::string table = kept && out ? TableName(base, career_car) : std::string();
  int32_t car = 0, colour = 0;
  if (table.empty() || !Kept(table, garage_id) ||
      !FactoryColour(ctx, base, table, garage_id, &car, &colour)) {
    __imp__sub_82547EB8(ctx, base);
    return;
  }
  MakeFactory(ctx, base, out, car, colour);
  ctx.r3.u64 = out;
}
