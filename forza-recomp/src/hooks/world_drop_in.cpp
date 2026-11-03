// forza - the Horizon World as one endless free roam that players drop into
// (docs/online_vision.md, "Joining a world in progress").
//
// The online game (the object at *(0x83305CC4)) runs in a mode (+40), set by sub_828733A8
// when a game starts: Find Game is 7 and Find Custom Game 10 (sub_8287F2B8 picks them from
// the matchmaking kind), a custom game's host has 8 (race) or 9 (free roam). Three things
// follow from the mode. Horizon World is Find Game's free roam (the Forza Live server's
// playlist 3, rounds of GameTypePreset 7); while one of its rounds runs in mode 7 it gets
// the custom free roam's version of each:
//
// 1. Join in progress. sub_82865340(mode) answers "is it a custom game" (8, 9, 10); only
//    then does the lobby of a game in progress say FREE ROAM IN PROGRESS and offer JUMP IN,
//    the host take a joiner's ready flag while the game runs, and every peer add the car
//    of a player whose status becomes "in game". In Find Game the joiner waits for the
//    round to end. The check answers yes at those call sites (by return address).
// 2. The flow. The launch (sub_8286A2E0) runs a custom game as race type 18 (the online
//    free roam flow, game_start_multiplayer_playground: co-op challenges, joiners placed
//    into the running world) with HUD mode 17, anything else as race type 6 (an online
//    race: TIME/RACE/LAP timers, PLACE, a timed end) with HUD mode 21. A player who jumped
//    into a race-type world stayed on the loading screen. The world launches as type 18.
// 3. Solo start. The host launches once sub_82876D90 says ready, which in Find Game needs
//    the team/bucket check (+2148, sub_82895018) to have run; it runs when a remote player's
//    status changes (+2152, set in sub_8287DEC8), never for a host alone in the lobby, who
//    waited for a second player. For a world host alone in its lobby the flag is set, so the
//    check runs, the intermission starts and the world loads with one player.
//
// 4. The car class. The restrictions of a game's buckets say "any class" with 255 (a custom
//    game's ANY; the SQL of the garage filter skips the class for it). The options loader
//    (sub_825F5AA8) replaces a 255 of the buckets of an online game by the class of the
//    options' Min/MaxRecommendedCarClass (both 255: 6 = S, equal: that class, else the vote's
//    pick), so the world's lobby listed only S-class cars (View Setup: CAR CLASS S). In the
//    world's playlist the 255 stays: any car is allowed (#6).
//
// The lobby and the playlist's options (GetGameOptions, per round) stay Find Game's. As a
// free roam the world has no timed end: the host's END FREE ROAM sends everyone back to the
// lobby, which starts the next round after the intermission. world_drop_in = false turns
// all of it off; FORZA_WORLD_TRACE=1 logs each custom check call site the first time it is
// asked in mode 7, and what the other two hooks do. OnlineGameIsHost (bottom) tells
// live_xmsg.cpp which member of a world the game made its host after the host left.

#include "../kernel/live.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"
#include "../platform/world_session.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <atomic>
#include <cstdint>
#include <cstdlib>

REXCVAR_DEFINE_BOOL(world_drop_in, true, "Network/Forza Live",
                    "Horizon World (Find Game's shared free roam) runs as an online free roam "
                    "that players drop into (JUMP IN) and that starts with one player");

namespace {
constexpr uint32_t kModeFindGame = 7;
constexpr uint32_t kOnlineGame = FORZA_ADDR(0x83305CC4);    // pointer to the online game object
constexpr uint32_t kGameModeInfo = FORZA_ADDR(0x832DF00C);  // pointer; +52 is the HUD/game mode value
constexpr uint32_t kRaceTypeOnlineRace = 6, kRaceTypeOnlineFreeRoam = 18;
constexpr uint32_t kHudModeFreeRoam = 17;

// Online game fields (world_events.cpp has the rest). Title Update 4 moved those from the
// game options (+0x1B4) on: the preset by 4 bytes, the launch check's by 24.
constexpr uint32_t kHost = 344, kInGame = 48;
constexpr uint32_t kPreset = FORZA_TU4 ? 440 : 436;
constexpr uint32_t kBucketCheck = FORZA_TU4 ? 2172 : 2148, kBucketCheckDue = kBucketCheck + 4;

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
void Store32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v >> 24);
  p[1] = uint8_t(v >> 16);
  p[2] = uint8_t(v >> 8);
  p[3] = uint8_t(v);
}

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_WORLD_TRACE");
    return v && *v && *v != '0';
  }();
  return on;
}

// A world needs Find Game mode and a free-roam round. An invite has no local
// matchmaking playlist yet; known other playlists and non-free-roam rounds
// still fail closed (docs/online_vision.md, phase 2).
uint32_t WorldGame(uint8_t* base) {
  if (!REXCVAR_GET(world_drop_in)) return 0;
  const uint32_t game = Load32(forza::GuestPtr(base, kOnlineGame));
  if (!game) return 0;
  const uint8_t* g = forza::GuestPtr(base, game);
  if (!forza::world_session::IsWorld(forza::LiveMatchPlaylist(),Load32(g+40),g[kPreset])) return 0;
  return game;
}

// Return addresses of the sub_82865340 calls that make a custom free roam joinable.
bool JoinInProgressSite(uint32_t lr, const PPCContext& ctx) {
  switch (lr) {
    case FORZA_ADDR(0x8270101C):  // lobby screen update: JUMP IN available (also needs the game's start time)
    case FORZA_ADDR(0x828196F4):  // lobby countdown in state 10: time until JUMP IN
    case FORZA_ADDR(0x8281B364):  // lobby state 10 label: FREE ROAM IN PROGRESS, not WAITING FOR FINISH
    case FORZA_ADDR(0x82893B20):  // host: a player's ready flag while the game runs (JUMP IN sets it)
    case FORZA_ADDR(0x8288DCD8):  // every peer: a player whose status became "in game" gets its car
    case FORZA_ADDR(0x82624B04):  // in the free roam, as in a custom one
      return true;
    case FORZA_ADDR(0x826E5354):  // lobby button filter, "custom games only": the JUMP IN button (index 6)
      return ctx.r29.u32 == 6;
    default:  // the game's options (0x828837A8: the hopper's), the other lobby buttons, ...
      return false;
  }
}
}  // namespace

extern "C" REX_FUNC(__imp__sub_82865340);
extern "C" REX_FUNC(__imp__sub_825548A0);
extern "C" REX_FUNC(__imp__sub_82875FB8);
extern "C" REX_FUNC(__imp__sub_825F5AA8);

// bool IsCustomGameMode(int mode)
extern "C" REX_FUNC(sub_82865340) {
  const uint32_t mode = ctx.r3.u32, lr = uint32_t(ctx.lr);
  __imp__sub_82865340(ctx, base);
  if (mode != kModeFindGame || !REXCVAR_GET(world_drop_in)) return;
  const bool world = WorldGame(base) != 0;
  const bool custom = world && JoinInProgressSite(lr, ctx);
  if (Trace()) {
    static std::atomic<uint32_t> seen[32];
    bool known = false;
    for (auto& s : seen) {
      uint32_t v = s.load();
      if (v == lr) {
        known = true;
        break;
      }
      if (!v && s.compare_exchange_strong(v, lr)) break;
    }
    if (!known) {
      REXLOG_INFO("WORLD: custom-game check at {:08X} (r29 {}), world {}: {}", lr, ctx.r29.u32,
                  world, custom ? "yes" : "no");
    }
  }
  if (custom) ctx.r3.u64 = 1;
}

// sub_825548A0(settings, race type, &game type): the launch (sub_8286A2E0, returning to
// 0x8286A418) builds the race settings for the game it starts.
extern "C" REX_FUNC(sub_825548A0) {
  if (uint32_t(ctx.lr) == FORZA_ADDR(0x8286A418) && ctx.r4.u32 == kRaceTypeOnlineRace && WorldGame(base)) {
    ctx.r4.u64 = kRaceTypeOnlineFreeRoam;
    if (const uint32_t info = Load32(forza::GuestPtr(base, kGameModeInfo))) {
      Store32(forza::GuestPtr(base, info + 52), kHudModeFreeRoam);
    }
    if (Trace()) REXLOG_INFO("WORLD: launching the world as an online free roam");
  }
  __imp__sub_825548A0(ctx, base);
}

// sub_82875FB8(game): the host's team/bucket check before a Find Game launch (runs when
// +2152 is set, result in +2148; called every frame from the lobby update sub_8287F670).
extern "C" REX_FUNC(sub_82875FB8) {
  if (const uint32_t game = WorldGame(base)) {
    uint8_t* g = forza::GuestPtr(base, game);
    const bool host = g[kHost] != 0, in_game = Load32(g + kInGame) == 1;
    if (host && !in_game && !Load32(g + kBucketCheck) && !g[kBucketCheckDue]) {
      g[kBucketCheckDue] = 1;
      static std::atomic<int> logged{0};
      if (Trace() && logged.fetch_add(1) < 3) {
        REXLOG_INFO("WORLD: the world's host is alone in its lobby: checking the launch");
      }
    }
  }
  __imp__sub_82875FB8(ctx, base);
}

// sub_825F5AA8(options): sets the class of each bucket that restricts to "any" (255) from the
// options' recommended class range (see 4. above). The world keeps "any".
extern "C" REX_FUNC(sub_825F5AA8) {
  if (REXCVAR_GET(world_drop_in) &&
      (forza::LiveIsWorldPlaylist(forza::LiveMatchPlaylist()) || WorldGame(base))) {
    if (Trace()) REXLOG_INFO("WORLD: the buckets keep any car class");
    return;
  }
  __imp__sub_825F5AA8(ctx, base);
}

namespace forza {
// The online game's host flag (+344, sub_82865378), for the network thread (live_xmsg.cpp:
// which member of a world the game made its new host after the host left).
int OnlineGameIsHost() {
  auto* kernel = rex::system::kernel_state();
  if (!kernel || !kernel->memory()) return -1;
  uint8_t* base = kernel->memory()->virtual_membase();
  const uint32_t game = Load32(forza::GuestPtr(base, kOnlineGame));
  if (!game) return -1;
  return forza::GuestPtr(base, game)[kHost] != 0;
}
}  // namespace forza
