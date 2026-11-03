// Request FH1's existing recovery flow; native code owns the physical reset.
// tu_address_map: TU4 addresses
#include "../platform/fh1_car_recovery.h"

#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
#include "../platform/guest_memory.h"
#include <rex/logging.h>
#include <rex/ppc/func.h>
#include <atomic>

extern "C" REX_FUNC(__imp__sub_825280E0);
extern "C" REX_FUNC(__imp__sub_825217E8);

namespace forza::fh1_car_recovery {
namespace {
uint32_t Read(uint8_t* base, uint32_t address) {
  const auto* p = GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 |
         uint32_t(p[2]) << 8 | p[3];
}

class Native {
public:
  PPCContext ctx;
  uint8_t* base;
  Native(const PPCContext& context, uint8_t* memory) : ctx(context), base(memory) {}
  uint32_t Call(uint32_t function, uint32_t object = 0) {
    ctx.r3.u64 = object;
    rex::runtime::ResolveIndirectFunction(function)(ctx, base);
    return ctx.r3.u32;
  }
  uint32_t Virtual(uint32_t object, uint32_t slot) {
    if (!object) return 0;
    const auto table = Read(base, object);
    const auto function = table ? Read(base, table + slot) : 0;
    return function ? Call(function, object) : 0;
  }
  uint32_t EligibleCar() {
    if (!base) return 0;
    const auto game = Read(base, 0x833CAAE4);
    if (!game || !Call(0x82F12410, game)) return 0;
    // Exact USER0 chain used by CHandOfGodControl::Tick (8288CEE0).
    const auto facade = Call(0x825EC700);
    const auto player = Virtual(facade, 0x28);
    const auto car = Virtual(player, 0x44);
    if (!car) return 0;
    const auto* bytes = GuestPtr(base, car);
    // Original 825280E0 early exits. Also reject an already queued/active
    // recovery so repeated input cannot restart the native transition.
    if (bytes[0x390B] || !Read(base, car + 0x2E08) || bytes[0x3112] ||
        Read(base, car + 0x2DD0) != 0 || Read(base, car + 0x3C30) != 0 ||
        bytes[0x3C34]) return 0;
    const auto settings = Call(0x824A94B8);
    // 825653B8 binds this byte to the authored NoHandOfGodReset setting.
    if (!settings || GuestPtr(base, settings)[0x1365] ||
        (Call(0x8250B440, car) & 0xFF)) return 0;
    return car;
  }
};

// Observation only: never changes guest state or triggers another reset.
std::atomic<uint32_t> pending_car{0};
std::atomic<unsigned> pending_ticks{0};
} // namespace

bool Available(const PPCContext& context, uint8_t* base) {
  Native native(context, base);
  return native.EligibleCar() != 0;
}

bool Request(const PPCContext& context, uint8_t* base) {
  Native native(context, base);
  const auto car = native.EligibleCar();
  if (!car) return false;
  pending_ticks.store(0);
  pending_car.store(car);
  GuestPtr(base, car)[0x3C34] = 1;
  REXLOG_INFO("Car recovery: requested native HandOfGod for USER0 car {:08X}", car);
  return true;
}
} // namespace forza::fh1_car_recovery

extern "C" REX_FUNC(sub_825280E0) {
  using namespace forza::fh1_car_recovery;
  const auto car = ctx.r3.u32;
  const bool observe = pending_car.load() == car;
  const auto before = observe ? Read(base, car + 0x3C30) : 0;
  __imp__sub_825280E0(ctx, base);
  if (!observe || pending_car.load() != car) return;
  const auto after = Read(base, car + 0x3C30);
  if (before != after)
    REXLOG_INFO("Car recovery: native state {} -> {} for car {:08X}", before, after, car);
  if (++pending_ticks > 600) {
    pending_car.store(0);
    REXLOG_WARN("Car recovery: observation timed out for car {:08X}", car);
  }
}

extern "C" REX_FUNC(sub_825217E8) {
  using namespace forza::fh1_car_recovery;
  const auto car = ctx.r3.u32;
  const bool observe = pending_car.load() == car;
  __imp__sub_825217E8(ctx, base);
  if (observe) {
    pending_car.store(0);
    REXLOG_INFO("Car recovery: native position/orientation reset executed for car {:08X}", car);
  }
}
#endif
