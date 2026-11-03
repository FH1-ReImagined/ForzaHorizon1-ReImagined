// FH1 TU4's native player-car recovery request.
// tu_address_map: TU4 addresses
#pragma once

#include "guest_addr.h"
#include <cstdint>
#include <rex/ppc/context.h>

namespace forza::fh1_car_recovery {
#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
// Call with a live guest UI-thread context. Checks USER0 and the original
// HandOfGod blockers, including NoHandOfGodReset and remote ownership.
bool Available(const PPCContext& context, uint8_t* base);

// Revalidates and sets only the native recovery-request byte. On success the
// caller must resume gameplay so the original recovery update can consume it.
// Does not teleport, bypass native gates, or dispatch a pause action itself.
bool Request(const PPCContext& context, uint8_t* base);
#else
inline bool Available(const PPCContext&, uint8_t*) { return false; }
inline bool Request(const PPCContext&, uint8_t*) { return false; }
#endif
} // namespace forza::fh1_car_recovery
