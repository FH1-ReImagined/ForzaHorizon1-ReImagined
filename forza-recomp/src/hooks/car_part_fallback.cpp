// forza - a garage car with an upgrade part of another car loads with its stock part instead of
// ending the game (exit code 134 on START at the title screen, #836).
// What the creators do and why the record was NULL: src/platform/car_part_fallback.h.
//
// Each of the five part-record creators runs as before. Only when it returns without a record
// (*out == 0, the "doesn't match part's" branch) the expected parent's stock part is looked up
// in the game database (the creator's own database object, vtable slot 0x20 = Database::Query
// with a SQL string; recordset slots 0xB8 = row count, 0x90 = integer of a column) and the
// creator runs again with it, so the record is the one an unmodified car of that parent has.
// The creator releases the database reference its caller handed it (r4) before it returns:
// the retry gets a new one (AddRef, vtable slot 8). A warning names the car, the part, the
// foreign part id and the stock part, once per car and part.
//
// A record that was made is untouched, with one exception (#862): a record with no row (row
// count 0: the part id is not in the database) of a part every car has (cpf::Required) is
// released as the creator releases a mismatched one and made again with the stock part. A
// sub-part whose expected parent has no stock part of its kind, because that parent is not in
// the database (2147483647, from an empty Drivetrain or CarBody record) or lacks a required
// part, gets the car's own stock parent's part (the parent passed with it is that one).
//
// The car's id for that line comes from the two callers, the car physics loader (base
// sub_82BF4FB0, TU4 sub_82CB4500: r5 points to it) and the car's wheel and tyre geometry (base
// sub_8265BE18, TU4 sub_827051E0: r4).

#include "../platform/car_part_fallback.h"
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
#include <tuple>

extern "C" REX_FUNC(__imp__sub_82BED520);  // CreateCarPartUpgradeRecord
extern "C" REX_FUNC(__imp__sub_82BED788);  // CreateEnginePartUpgradeRecord
extern "C" REX_FUNC(__imp__sub_82BED9F0);  // CreateMotorPartUpgradeRecord
extern "C" REX_FUNC(__imp__sub_82BEDC58);  // CreateDrivetrainPartUpgradeRecord
extern "C" REX_FUNC(__imp__sub_82BEDEC0);  // CreateCarBodyPartUpgradeRecord
extern "C" REX_FUNC(__imp__sub_82BF4FB0);  // the car physics loader
extern "C" REX_FUNC(__imp__sub_8265BE18);  // the car's wheel and tyre geometry

namespace {

namespace cpf = forza::car_part_fallback;
using cpf::Creator;

constexpr uint32_t kQuerySlot = 0x20, kAddRefSlot = 8, kReleaseSlot = 12;
constexpr uint32_t kRowCountSlot = 0xB8, kIntegerSlot = 0x90;

// The car the creators are building for on this thread (0 when unknown). A car loads on one
// thread from start to end; the id only names the car in the log.
thread_local int32_t current_car = 0;

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

// object->vtable[slot](args) on a copy of the hook's context (its stack pointer is the
// caller's, as at a call).
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
      case 6: call.r6.u64 = value; break;
    }
  }
  fn(call, base);
  return call.r3.u32;
}

// The first column of the query's first row, if it has one. False also when the object is not
// the game database.
bool QueryInteger(const PPCContext& ctx, uint8_t* base, uint32_t db, const std::string& sql,
                  bool* found, int32_t* value) {
  *found = false;
  const uint32_t table = db ? Read(base, db) : 0;
  if (!table || Read(base, table + kQuerySlot) != FORZA_ADDR(0x82C19130)) return false;
  auto* memory = REX_KERNEL_MEMORY();
  const uint32_t block = memory->SystemHeapAlloc(uint32_t(8 + sql.size() + 1));
  if (!block) return false;
  Write(base, block, 0);
  std::memcpy(forza::GuestPtr(base, block + 8), sql.c_str(), sql.size() + 1);
  Virtual(ctx, base, db, kQuerySlot, {block, db, block + 8});
  const uint32_t records = Read(base, block);
  if (records) {
    if (int32_t(Virtual(ctx, base, records, kRowCountSlot, {records})) > 0) {
      *value = int32_t(Virtual(ctx, base, records, kIntegerSlot, {records, 0}));
      *found = true;
    }
    Virtual(ctx, base, records, kReleaseSlot, {records});
  }
  memory->SystemHeapFree(block);
  return records != 0;
}

bool FirstReport(int32_t car, uint32_t type, int32_t part) {
  static std::mutex lock;
  static std::set<std::tuple<int32_t, uint32_t, int32_t>> reported;
  std::lock_guard<std::mutex> hold(lock);
  return reported.size() < 256 && reported.emplace(car, type, part).second;
}

// The number of rows of a record (the loader's own test of the Engine and Motor records).
int32_t Rows(const PPCContext& ctx, uint8_t* base, uint32_t record) {
  return int32_t(Virtual(ctx, base, record, kRowCountSlot, {record}));
}

// Creator(out, database holder, type, parent, part, callback); the creator releases the holder.
void Create(PPCContext& ctx, uint8_t* base, PPCFunc* original, Creator creator) {
  const uint32_t out = ctx.r3.u32, holder = ctx.r4.u32, type = ctx.r5.u32, callback = ctx.r8.u32;
  const int32_t parent = ctx.r6.s32, part = ctx.r7.s32;
  const uint32_t db = holder ? Read(base, holder) : 0;
  original(ctx, base);
  if (!out || !db || !cpf::Handles(creator, type)) return;
  // A record with no row: the part id is not in the database (a removed mod's part, or -1).
  // Only a part every car has is replaced then (#862); an optional part's -1 stays empty.
  const uint32_t record = Read(base, out);
  if (record && (!cpf::Required(type) || Rows(ctx, base, record) > 0)) return;

  cpf::Outcome o;
  o.car = creator == Creator::kCar ? parent : current_car;
  o.creator = creator;
  o.type = type;
  o.part = part;
  o.parent = parent;
  o.missing = record != 0;
  o.used_parent = parent;
  int32_t stock = 0;
  if (!QueryInteger(ctx, base, db, cpf::StockPartSql(creator, type, parent), &o.found, &stock)) {
    if (FirstReport(o.car, type, part)) {
      REXLOG_ERROR("Car {}: its {} part {} doesn't belong to its {} {}, and the stock part could "
                   "not be looked up (#836)",
                   o.car, cpf::PartName(type), part, cpf::ParentColumn(creator), parent);
    }
    return;
  }
  if (!o.found && creator != Creator::kCar) {
    // The parent from an empty car-level record (2147483647), or one without this part: the
    // car's own stock parent's part (#862).
    int32_t any = 0;
    QueryInteger(ctx, base, db, cpf::ParentSql(creator, parent), &o.parent_known, &any);
    bool has_parent = false;
    int32_t car_parent = 0;
    if (cpf::UseCarParent(creator, type, o.parent_known) && o.car > 0 &&
        QueryInteger(ctx, base, db, cpf::CarParentSql(creator, o.car), &has_parent, &car_parent) &&
        has_parent && car_parent != parent &&
        QueryInteger(ctx, base, db, cpf::StockPartSql(creator, type, car_parent), &o.found,
                     &stock) &&
        o.found) {
      o.used_parent = car_parent;
    }
  }
  // An empty record with nothing better to load (an optional part, or no stock part): as it is.
  if (o.missing && !o.found) return;
  o.replacement = cpf::Replacement(o.found, stock);
  if (record) {
    // The empty record goes, as the caller's smart pointer would let it go.
    Virtual(ctx, base, record, kReleaseSlot, {record});
    Write(base, out, 0);
  }
  // The creator's caller still holds the database (its own reference); the retry needs one.
  Virtual(ctx, base, db, kAddRefSlot, {db});
  Write(base, holder, db);
  ctx.r3.u64 = out;
  ctx.r4.u64 = holder;
  ctx.r5.u64 = type;
  ctx.r6.s64 = o.used_parent;
  ctx.r7.s64 = o.replacement;
  ctx.r8.u64 = callback;
  original(ctx, base);
  o.made = Read(base, out) != 0;
  if (FirstReport(o.car, type, part)) {
    const std::string line = cpf::Describe(o);
    REXLOG_WARN("{}", line);
  }
}

template <PPCFunc* Original>
void ForCar(PPCContext& ctx, uint8_t* base, int32_t car) {
  const int32_t outer = current_car;
  current_car = car;
  Original(ctx, base);
  current_car = outer;
}

}  // namespace

extern "C" REX_FUNC(sub_82BED520) { Create(ctx, base, __imp__sub_82BED520, Creator::kCar); }
extern "C" REX_FUNC(sub_82BED788) { Create(ctx, base, __imp__sub_82BED788, Creator::kEngine); }
extern "C" REX_FUNC(sub_82BED9F0) { Create(ctx, base, __imp__sub_82BED9F0, Creator::kMotor); }
extern "C" REX_FUNC(sub_82BEDC58) {
  Create(ctx, base, __imp__sub_82BEDC58, Creator::kDrivetrain);
}
extern "C" REX_FUNC(sub_82BEDEC0) { Create(ctx, base, __imp__sub_82BEDEC0, Creator::kCarBody); }

extern "C" REX_FUNC(sub_82BF4FB0) {
  ForCar<__imp__sub_82BF4FB0>(ctx, base, ctx.r5.u32 ? int32_t(Read(base, ctx.r5.u32)) : 0);
}
extern "C" REX_FUNC(sub_8265BE18) { ForCar<__imp__sub_8265BE18>(ctx, base, ctx.r4.s32); }
