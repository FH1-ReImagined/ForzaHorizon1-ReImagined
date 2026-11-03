// Raise the central garage-full predicate from 550 to 1,000. All native add,
// purchase, gift and reward callers keep their existing failure and storage paths.
// TU0 8252FCD0 / TU4 8259DC10: database COUNT plus pending vector at +68/+72.
// The track-presentation pool's unrelated 550 and the 6 MiB save allocation stay intact.
// tu_address_map: TU4 addresses
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/garage_capacity.h"
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#if FORZA_TU4
#define GarageFull sub_8259DC10
#define GarageCount sub_8259DB60
#else
#define GarageFull sub_8252FCD0
#define GarageCount sub_8252FC30
#endif

extern "C" REX_FUNC(GarageCount);

extern "C" REX_FUNC(GarageFull) {
  const auto* garage = forza::GuestPtr(base, ctx.r3.u32);
  const auto load = [garage](uint32_t offset) {
    const auto* p = garage + offset;
    return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 |
           uint32_t(p[2]) << 8 | uint32_t(p[3]);
  };
  // Snapshot pending entries before the native SQL helper, matching the original.
  const uint32_t begin = load(68), end = load(72), capacity_end = load(76);
  auto count = ctx;
  GarageCount(count, base);
  ctx.r3.u64 = forza::garage::Full(count.r3.u32, begin, end, capacity_end) ? 1 : 0;
}
