// forza - the placeholder car, in forza.log (#567).
//
// A car the game cannot build here (its id is not in this game's database, as with a car
// pack or mod the player lacks, or its files are missing) is not an error: the game puts
// NUL_Car_00 in its place, a plain black stand-in model with no paint. The car's description
// is built by sub_82570358 (TU4 sub_825E61B8): it queries Data_Car for the car's id and, when
// no row comes back, calls sub_824C3C98 (TU4 sub_82531BE0) on the description, which stores
// the stand-in's id 1215 (0x4BF) in the car id (+0), the original id in +0x2C8 and sets +0x2C5.
// The car list's vehicles keep that description at +0x2E50 (flag at +0x3115). In a game with
// other players the lobby then names the car "DLC Car" and the race draws the stand-in.
//
// Tested with two instances on a local server (docs/online_vision.md, "A car the other game
// lacks"): a player whose database lacks the host's car, or has the row but not its files,
// sees the host's car as the stand-in, in the lobby and in the race; the remote car is there,
// solid and driven, not ghosted. This hook only logs the swap, once per car id, so a report
// about "invisible" or "wrong" cars says which car ids the game could not show.

#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>

#include <cstdint>
#include <mutex>
#include <set>

extern "C" REX_FUNC(__imp__sub_824C3C98);

namespace {

constexpr uint32_t kPlaceholderId = 0x4BF;  // Data_Car.Id of NUL_Car_00
constexpr size_t kMaxLogged = 64;

uint32_t Load32(uint8_t* base, uint32_t addr) {
  const uint8_t* p = forza::GuestPtr(base, addr);
  return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3];
}

}  // namespace

// r3: the car description (its first word is the car id).
extern "C" REX_FUNC(sub_824C3C98) {
  const uint32_t description = ctx.r3.u32;
  const uint32_t id = description ? Load32(base, description) : 0;
  __imp__sub_824C3C98(ctx, base);
  if (id == kPlaceholderId) return;  // already the stand-in (the game does this twice)
  static std::mutex mutex;
  static std::set<uint32_t> logged;
  std::lock_guard<std::mutex> lock(mutex);
  if (logged.size() >= kMaxLogged || !logged.insert(id).second) return;
  REXLOG_INFO("Cars: car {} is not in this game's database or its files are missing: the "
              "placeholder car (a plain black stand-in) is used for it. Another player's car "
              "looks like this when you do not have the car pack or mod it comes from",
              id);
}
