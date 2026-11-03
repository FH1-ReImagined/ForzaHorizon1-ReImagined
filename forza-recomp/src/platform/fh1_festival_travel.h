// Scoped shortcuts into FH1 TU4's original map/fast-travel state machine.
// tu_address_map: TU4 addresses
#pragma once

#include "guest_addr.h"
#include <cstdint>
#include <rex/ppc/context.h>

namespace forza::fh1_festival_travel {
enum class Destination : uint8_t { Garage, Autoshow };

#if FORZA_TU4 && defined(FORZA_NATIVE_PAUSE)
// Requires a live guest UI-thread context. Checks the native travel gate and
// the loaded destination activity; the map retains final target/fee checks.
bool Available(const PPCContext& context, uint8_t* base, Destination destination);

// Opens native Map=2, selects the destination, and presents FH1's original
// confirmation. No charge or teleport is performed by the shortcut itself.
// The caller should remember its Cars tab/selection before calling Open.
bool Open(const PPCContext& context, uint8_t* base, uint32_t pause_screen,
          Destination destination);
#else
inline bool Available(const PPCContext&, uint8_t*, Destination) { return false; }
inline bool Open(const PPCContext&, uint8_t*, uint32_t, Destination) { return false; }
#endif
} // namespace forza::fh1_festival_travel
