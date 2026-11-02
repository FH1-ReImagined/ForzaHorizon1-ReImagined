// forza - the 1000 Club's smashable lookup no longer closes the game when its file is
// missing (#543).
//
// Title Update 4. When a world joins with 1000 Club challenges active (the car has them),
// the game loads game:\media\smashableslookup.bin once (sub_824FEB18, called from the
// constructor sub_824FF168 behind a one-time flag): it opens the file through the file
// manager (the object at 0x8353B808, vtable slot 30), reads 5-byte records from the stream
// and files them in a table (0x833CE180). The file is not part of the game's own media: it
// is in the 1000 Club Expansion Pack (Media/DLCZips/2700_pri_65/Media/smashableslookup.bin).
// The code never looks at the stream it gets back: with the file missing from the DLC
// copy (an incomplete or damaged pack, or a pack the game cannot see) it reads the stream's
// vtable at guest address 0 and the game closes, a second or two after free roam starts,
// every time the profile has an active challenge.
//
// This wraps sub_824FEB18: it opens the same file the same way, and only runs the original
// (which opens it again) when a stream came back. Without the file the table stays empty:
// the 1000 Club's smash counts do not advance, everything else plays.

#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <cstdint>
#include <initializer_list>

#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4

extern "C" REX_FUNC(__imp__sub_824FEB18);

namespace {

constexpr uint32_t kFileManager = 0x8353B808;       // pointer to the file manager object
constexpr uint32_t kOpenSlot = 120;                 // its vtable slot: open(out, name, flags, 0)
constexpr uint32_t kOpenForRead = 0x8000001B;       // the flags sub_824FEB18 passes
constexpr uint32_t kStringFromChars = 0x82480600;   // (string, chars): builds the name string
constexpr uint32_t kStringFree = 0x82441428;        // (string, 1, 0): releases its buffer
constexpr uint32_t kRelease = 0x824CEAB0;           // (&reference): releases a counted object

uint32_t Load32(uint8_t* base, uint32_t address) {
  const uint8_t* p = forza::GuestPtr(base, address);
  return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

// Calls a guest function on a copy of the context (its frames go below `sp`).
uint32_t Call(const PPCContext& from, uint8_t* base, uint32_t sp, uint32_t function,
              std::initializer_list<uint32_t> args) {
  PPCContext call = from;
  call.r1.u64 = sp;
  uint64_t* registers[] = {&call.r3.u64, &call.r4.u64, &call.r5.u64, &call.r6.u64, &call.r7.u64};
  size_t i = 0;
  for (uint32_t value : args) *registers[i++] = value;
  rex::runtime::ResolveIndirectFunction(function)(call, base);
  return call.r3.u32;
}

// True when the file manager hands back a stream for the named file.
bool Openable(const PPCContext& ctx, uint8_t* base, uint32_t name) {
  const uint32_t manager = Load32(base, kFileManager);
  if (!manager) return false;
  const uint32_t open = Load32(base, Load32(base, manager) + kOpenSlot);
  if (!open) return false;
  // Scratch below the caller's frame: a reference (4 bytes) and the name string (24 bytes).
  const uint32_t sp = ctx.r1.u32 - 0x200, reference = sp + 0x20, string = sp + 0x40;
  Call(ctx, base, sp, kStringFromChars, {string, name});
  Call(ctx, base, sp, open, {reference, manager, string, kOpenForRead, 0});
  Call(ctx, base, sp, kStringFree, {string, 1, 0});
  if (!Load32(base, reference)) return false;
  Call(ctx, base, sp, kRelease, {reference});
  return true;
}

}  // namespace

extern "C" REX_FUNC(sub_824FEB18) {
  if (!Openable(ctx, base, ctx.r3.u32)) {
    REXLOG_WARN("1000 Club: {} is not in the game files (it comes with the 1000 Club Expansion "
                "Pack, 2700); smashing counts are skipped (#543)",
                reinterpret_cast<const char*>(forza::GuestPtr(base, ctx.r3.u32)));
    return;
  }
  __imp__sub_824FEB18(ctx, base);
}

#endif  // FORZA_TITLE_UPDATE == 4
