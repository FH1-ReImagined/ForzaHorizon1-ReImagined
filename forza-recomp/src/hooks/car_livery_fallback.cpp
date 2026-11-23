// forza - Get In Car on a garage car whose paint job can't be loaded gives the car its factory
// paint instead of ending the game (#922). What the game does and why it ended:
// src/platform/car_livery_fallback.h.
//
// Only the call that makes the current car's driving paint job from the handle its description
// keeps (sub_825718D8's call at 82571990, TU4 825E77D8) is looked at, and only when that handle's
// paint job failed to load: the hook waits for it as the game would (WaitForLoad, sub_82412688 /
// TU4 sub_82442638, the wait sub_82E61748 does). Otherwise the game's own call runs unchanged.
// For a failed one it does what sub_825718D8 does when asked for a fresh paint job (its other
// branch: sub_82547EB8 / TU4 sub_825B7FA0 with the career car table, the garage id and 0x80000,
// into its local at r1+92, then into the driving paint job at r1+84) after clearing the row's
// LiveryFileName when the failed paint job was a file, and releases the handle as the game's
// call does at its end. A warning names the garage id and the car once.

#include "../platform/car_livery_fallback.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

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

extern "C" REX_FUNC(__imp__sub_82C9C440);  // the paint job's variant of a type (r5) from a handle (r4)
extern "C" REX_FUNC(sub_82412688);         // WaitForLoad(resource): false when the load failed
extern "C" REX_FUNC(sub_82547EB8);         // the car's paint job from its Career_Garage row

namespace {

namespace clf = forza::car_livery_fallback;

// sub_825718D8's call (TU4 825E77D8) and the profile database (TU4 0x833D2FFC); written out
// in FORZA_ADDR so that tools/tu_address_map.py --cpp finds them.
#define GET_IN_CALL FORZA_ADDR(0x82571990)
#define PROFILE_DB FORZA_ADDR(0x832E5510)
constexpr uint32_t kQuerySlot = 0x20, kReleaseSlot = 12;
constexpr uint32_t kRowCountSlot = 0xB8, kIntegerSlot = 0x90;
constexpr uint32_t kStateByte = 40, kKeyType = 0x40;
constexpr uint32_t kFreshLocal = 92;  // sub_825718D8's r1+92, used only by its fresh branch

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

// Runs a statement on the profile database; for a SELECT, the first column of the first row.
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
    if (value && int32_t(Virtual(ctx, base, records, kRowCountSlot, {records})) > 0) {
      *value = int32_t(Virtual(ctx, base, records, kIntegerSlot, {records, 0}));
      found = true;
    }
    Virtual(ctx, base, records, kReleaseSlot, {records});
  }
  memory->SystemHeapFree(block);
  return value ? found : true;
}

bool FirstReport(uint32_t garage_id) {
  static std::mutex lock;
  static std::set<uint32_t> reported;
  std::lock_guard<std::mutex> hold(lock);
  return reported.size() < 256 && reported.insert(garage_id).second;
}

}  // namespace

// r3: the driving paint job (out), r4: the handle, r5: the type, r6: flags. The caller's r29 is
// the career car table object and r30 the garage id at the Get In Car call.
extern "C" REX_FUNC(sub_82C9C440) {
  const uint32_t out = ctx.r3.u32, holder = ctx.r4.u32;
  const uint32_t livery = holder ? Read(base, holder) : 0;
  if (uint32_t(ctx.lr) != GET_IN_CALL || !out || !livery) {
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
  const clf::Action action = clf::Decide(failed, Read(base, livery + kKeyType));
  if (action == clf::Action::kGame) {
    __imp__sub_82C9C440(ctx, base);
    return;
  }

  const uint32_t career_car = ctx.r29.u32, garage_id = ctx.r30.u32;
  const std::string table = TableName(base, career_car);
  int32_t car = 0;
  if (clf::ValidTable(table)) {
    Run(ctx, base, "SELECT CarId FROM " + table + " WHERE Id=" + std::to_string(garage_id), &car);
  }
  bool cleared = false;
  if (action == clf::Action::kFactory) {
    cleared = Run(ctx, base, clf::ClearLiverySql(table, garage_id), nullptr);
  }
  if (FirstReport(garage_id)) {
    if (action == clf::Action::kFactory) {
      REXLOG_WARN("Cars: garage car {} (car {}): its paint job file could not be loaded; it gets "
                  "its factory paint{} (#922)",
                  garage_id, car,
                  cleared ? " and the save no longer names that file" : " (the save is unchanged)");
    } else {
      REXLOG_WARN("Cars: garage car {} (car {}): its paint could not be loaded; it is made again "
                  "from the save (#922)",
                  garage_id, car);
    }
  }

  // sub_825718D8's fresh branch, into the same local and then the driving paint job.
  const uint32_t local = ctx.r1.u32 + kFreshLocal;
  Write(base, local, 0);
  PPCContext fresh = ctx;
  fresh.r3.u64 = local;
  fresh.r4.u64 = career_car;
  fresh.r5.u64 = garage_id;
  fresh.r6.u64 = clf::kDrivingFlags;
  sub_82547EB8(fresh, base);
  const uint32_t made = Read(base, local);
  const uint32_t previous = Read(base, out);
  Write(base, out, made);
  Write(base, local, 0);
  if (previous) Virtual(ctx, base, previous, kReleaseSlot, {previous});
  // The game's call releases the handle it was given before it returns.
  Release(ctx, base, holder);
}
