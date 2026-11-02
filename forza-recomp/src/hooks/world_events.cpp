// forza - events from the Horizon World (docs/online_vision.md, phase 2).
//
// The Horizon World (Find Game's playlist 3, world_drop_in.cpp) runs one endless free
// roam per session. The Forza Live server turns a world's next round into one of the
// game's festival events (server/forza_live/world/world.py); this file is the client side, on the
// world session's host:
//
// 1. Taking an event. The server sends WORLD_EVENT to the world's host (net.cpp ->
//    LiveWorldEvent). The host's game asks for the next round's options again
//    (sub_8286A800(game, 1), the vote-to-skip path; the game asks for round N+1 when
//    round N starts, so the answer it holds is the world's), which the server answers
//    with the event, then ends the free roam the way the pause menu's END FREE ROAM does:
//    its own player's status becomes 5 (sub_8286A468; CSetPlayerReadyToReturnToLobby with
//    the host flag), which every peer's CIsFreeRoamSessionOver (sub_825ADB30) reads from
//    the host, and everyone returns to the lobby, where the event is the next round.
// 2. The wait lobby. An event round's intermission is world_event_wait_seconds (30)
//    instead of the world playlist's 5 s (+0x7E4/+0x7E8 of the online game, copied from
//    the playlist's search settings), so the players in the world can pick a car under
//    the event's restrictions and late ones can join. A grid full of humans starts at once.
// 3. CPU drivers. The game fills the lobby with them itself (sub_8287BBE0:
//    min(MaxPlayers - humans, MaxAIPlayers)); their difficulty (+0x44A, applied by
//    sub_82877470 after every fill) follows the human share of the grid: at most 1/3
//    Easy, 2/3 or more Hard, in between Medium. The host creates them, and when the race
//    loads it hands some of them to the other consoles, which run their simulation. The
//    race's status steps never moved them: they follow the host's own player into "in
//    game", are never kicked (kicking one threw the host out), a peer neither sets their
//    status nor lets them stop its load, the results screen doesn't wait for them, and
//    when the race unloads each console removes only the ones it runs (removing another
//    console's the local way corrupted the session's message queue).
// 4. A removal (kicked, voted out) ended the online game re-entrantly: the session
//    teardown (sub_82CF7278) pumps the session's messages, a queued second removal ended
//    the game again inside it and tore the session down under it, and the outer teardown
//    read the freed session (null). The nested removal is dropped.
// 5. Campaign rounds (#309, docs/coop_campaign.md): in a Co-op Festival world (playlist 20)
//    the host's /event makes a festival event the next round (server/forza_live/world/campaign.py).
//    The server sends every console of the world the event's own CPU skill (WORLD_EVENT
//    op 2: its AISkillMed / AITemperamentMed rows, the career's Medium); the online AI's
//    levels come from MPAIDifficultyMap (Easy 20/8 is stronger than a festival event's Pro).
//    For a round on that event's track the host's AI difficulty is Medium, and each CPU
//    driver gets the event's rows wherever its driver data is set (sub_82568BD8, the
//    MPAIDifficultyMap lookup) and on every console at the round start, before the load.
//    The career adds each named driver's AIPlayers modifiers (mostly 0); these drivers are
//    the online fill's, so the event's values are used as they are.
//
// FORZA_EVENT_TRACE=1 logs the round options, AI fill, intermission and launch, the
// players' status changes and removals, and the CPU drivers (which console runs them,
// their difficulty).

#include "../kernel/live.h"
#include "../platform/guest_addr.h"
#include "../platform/guest_memory.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xmemory.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <string>
#include <vector>

REXCVAR_DEFINE_BOOL(world_events, true, "Network/Forza Live",
                    "Horizon World: the world's host takes the events the Forza Live server "
                    "starts (ends the free roam into the event's lobby)");
REXCVAR_DEFINE_INT32(world_event_wait_seconds, 30, "Network/Forza Live",
                     "Horizon World: the wait lobby before an event, in seconds");
REXCVAR_DEFINE_BOOL(world_event_cpu_with_peers, true, "Network/Forza Live",
                    "Horizon World: CPU drivers also in events with more than one human (off: "
                    "only a solo event gets CPU drivers)");
REXCVAR_DEFINE_BOOL(world_event_cpu_ready, true, "Network/Forza Live",
                    "Horizon World: after an event the results screen doesn't wait for the "
                    "CPU drivers to be ready to return to the lobby");
REXCVAR_DEFINE_INT32(world_event_ai_grid, 0, "Network/Forza Live",
                     "Horizon World test knob: the grid size the CPU drivers' difficulty rule "
                     "uses (0: the event's grid; e.g. 4 makes 2 humans Medium, 3 Hard)");

namespace {
constexpr uint32_t kModeFindGame = 7;
constexpr uint8_t kPresetFreeRoam = 7;
constexpr uint32_t kOnlineGame = FORZA_ADDR(0x83305CC4);  // pointer to the online game object
constexpr uint32_t kIsAiPlayer = FORZA_ADDR(0x82865528);  // predicate for sub_828754A0: CPU driver
constexpr uint8_t kStatusHostEndsFreeRoam = 5;

// Online game fields (offsets from *(kOnlineGame)). Title Update 4 put 4 bytes in front
// of the game options (preset at +4) and 8 more at their end (0x2DC bytes), so the fields
// from the options on moved (docs/dlc.md, "Title Update 4 online").
constexpr uint32_t kMode = 0x28, kState = 0x2C, kInGame = 0x30, kLaunchTime = 0x4C;
constexpr uint32_t kPlayers = 0x10C, kHost = 0x158;
constexpr uint32_t kOptions = 0x1B4;  // the round's game options (0x2D0 bytes)
// Within game options: GameTypePreset, TrackId, MaxPlayers, MaxAIPlayers, AI difficulty.
constexpr uint32_t kOptPreset = FORZA_TU4 ? 4 : 0, kOptTrack = kOptPreset + 4;
constexpr uint32_t kOptMaxPlayers = kOptPreset + 0x18, kOptMaxAi = kOptPreset + 0x19;
constexpr uint32_t kOptAiDifficulty = kOptPreset + 0x296;
constexpr uint32_t kPreset = kOptions + kOptPreset, kTrack = kOptions + kOptTrack;
constexpr uint32_t kMaxPlayers = kOptions + kOptMaxPlayers, kMaxAi = kOptions + kOptMaxAi;
constexpr uint32_t kAiDifficulty = kOptions + kOptAiDifficulty;
// The next round's options (kNextReady set once they arrived).
constexpr uint32_t kNextReady = FORZA_TU4 ? 0x490 : 0x484;
constexpr uint32_t kNextOptions = FORZA_TU4 ? 0x494 : 0x488, kNextPreset = kNextOptions + kOptPreset;
constexpr uint32_t kRound = FORZA_TU4 ? 0x7F4 : 0x7DC;  // the hopper's round counter (16 bits)
constexpr uint32_t kIntermission = FORZA_TU4 ? 0x7FC : 0x7E4;
constexpr uint32_t kShortIntermission = kIntermission + 4;

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
void Store32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v >> 24);
  p[1] = uint8_t(v >> 16);
  p[2] = uint8_t(v >> 8);
  p[3] = uint8_t(v);
}

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_EVENT_TRACE");
    return v && *v && *v != '0';
  }();
  return on;
}

int64_t NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

// The server's request (network thread -> game thread).
std::atomic<uint32_t> requested_event{0};
std::atomic<int64_t> requested_at{0};

// Host side of taking an event (game thread).
enum class Taking { kIdle, kAsked, kEnded };
Taking taking = Taking::kIdle;
uint32_t taking_event = 0;
int64_t taking_since = 0;

// The world playlist's own intermission, restored after an event.
uint32_t world_intermission[2] = {0, 0};
bool intermission_changed = false;
uint8_t last_difficulty = 0xFF;
uint8_t event_max_ai = 0xFF;  // the event round's MaxAIPlayers (0xFF: none seen)

// A campaign round's CPU skill (WORLD_EVENT op 2, network thread -> game thread): the
// event, its track and the AISkills / AITemperaments row ids its CPU drivers get.
std::atomic<uint32_t> campaign_event{0}, campaign_track{0}, campaign_skill{0},
    campaign_temperament{0};
uint32_t campaign_logged = 0;  // the event whose skill was last logged as applied
// Driver data (player vfunc 0x78): level, AISkills row, AITemperaments row (sub_82568BD8).
constexpr uint32_t kDriverLevel = 0x25B, kDriverSkill = 0x260, kDriverTemperament = 0x264;
constexpr uint8_t kAiMedium = 1;

// The online game while Find Game runs the world playlist, else 0.
uint32_t WorldPlaylistGame(uint8_t* base) {
  if (!forza::LiveIsWorldPlaylist(forza::LiveMatchPlaylist())) return 0;
  const uint32_t game = Load32(forza::GuestPtr(base, kOnlineGame));
  if (!game || Load32(forza::GuestPtr(base, game + kMode)) != kModeFindGame) return 0;
  return game;
}

// Calls a guest function on a copy of the hooked function's context; returns r3.
uint32_t CallGuest(PPCContext& ctx, uint8_t* base, PPCFunc* fn, uint32_t r3, uint32_t r4 = 0,
                   uint32_t r5 = 0) {
  PPCContext saved = ctx;
  ctx.r3.u64 = r3;
  ctx.r4.u64 = r4;
  ctx.r5.u64 = r5;
  fn(ctx, base);
  const uint32_t result = ctx.r3.u32;
  ctx = saved;
  return result;
}

// The rule of the vision: the human share of the grid sets the CPU drivers' level
// (MPAIDifficultyMap rows 0 Easy, 1 Medium, 2 Hard). server/forza_live/world/world.py has the same.
uint8_t AiDifficulty(uint32_t humans, uint32_t grid) {
  if (3 * humans <= grid) return 0;
  if (3 * humans >= 2 * grid) return 2;
  return 1;
}

// The online world game when its round is the campaign round the server described (an
// event round on that event's track), else 0.
uint32_t CampaignGame(uint8_t* base) {
  constexpr uint32_t kCoopPlaylist = 20;  // server/forza_live/title_services/lsp.py COOP_HOPPER
  if (forza::LiveMatchPlaylist() != kCoopPlaylist) return 0;
  const uint32_t game = REXCVAR_GET(world_events) ? WorldPlaylistGame(base) : 0;
  if (!game || !campaign_event.load() || !campaign_skill.load()) return 0;
  const uint8_t* g = forza::GuestPtr(base, game);
  if (g[kPreset] == kPresetFreeRoam || Load32(g + kTrack) != campaign_track.load()) return 0;
  return game;
}

// Gives a CPU driver's driver data the campaign event's skill (the level stays Medium).
void SetCampaignSkill(uint8_t* base, uint32_t driver) {
  if (!driver) return;
  uint8_t* d = forza::GuestPtr(base, driver);
  d[kDriverLevel] = kAiMedium;
  Store32(d + kDriverSkill, campaign_skill.load());
  Store32(d + kDriverTemperament, campaign_temperament.load());
}

void LogGame(const char* what, uint8_t* base, uint32_t game, uint32_t lr) {
  const uint8_t* g = forza::GuestPtr(base, game);
  REXLOG_INFO("EVENT: {} (lr {:08X}): mode {} state {} in game {} host {} players {} preset {} "
              "track {} max {} max AI {} AI diff {} next {} preset {} round {} intermission "
              "{}/{} launch {}",
              what, lr, Load32(g + kMode), Load32(g + kState), Load32(g + kInGame), g[kHost],
              Load32(g + kPlayers), g[kPreset], Load32(g + kTrack), g[kMaxPlayers],
              g[kMaxAi], g[kAiDifficulty], g[kNextReady], g[kNextPreset],
              (g[kRound] << 8) | g[kRound + 1], Load32(g + kIntermission),
              Load32(g + kShortIntermission), Load32(g + kLaunchTime));
}
}  // namespace

extern "C" REX_FUNC(__imp__sub_8286A800);
extern "C" REX_FUNC(__imp__sub_8287E538);
extern "C" REX_FUNC(__imp__sub_8287BBE0);
namespace forza {
void LobbyLogTick(uint8_t* base, uint32_t game);
bool LobbyLogEnabled();
}
extern "C" REX_FUNC(__imp__sub_8286A250);
extern "C" REX_FUNC(__imp__sub_828786E8);
extern "C" REX_FUNC(__imp__sub_8286E660);
extern "C" REX_FUNC(__imp__sub_825ADB30);
extern "C" REX_FUNC(__imp__sub_8287F670);
extern "C" REX_FUNC(__imp__sub_82877470);
extern "C" REX_FUNC(__imp__sub_8286FC98);
extern "C" REX_FUNC(__imp__sub_82876378);
extern "C" REX_FUNC(__imp__sub_828653D8);
extern "C" REX_FUNC(__imp__sub_8286A468);
extern "C" REX_FUNC(__imp__sub_828754A0);
extern "C" REX_FUNC(__imp__sub_8286A2E0);
extern "C" REX_FUNC(__imp__sub_82878800);
extern "C" REX_FUNC(__imp__sub_82875840);
extern "C" REX_FUNC(__imp__sub_8247F018);
extern "C" REX_FUNC(__imp__sub_82577AD8);
extern "C" REX_FUNC(__imp__sub_828736E0);
extern "C" REX_FUNC(__imp__sub_823FD208);
extern "C" REX_FUNC(__imp__sub_82880A90);
extern "C" REX_FUNC(__imp__sub_82880A88);
extern "C" REX_FUNC(__imp__sub_828653D8);
extern "C" REX_FUNC(sub_8286A800);
extern "C" REX_FUNC(sub_8286A468);

namespace rex::runtime {
PPCFunc* ResolveIndirectFunction(uint32_t guest_address);
}

namespace forza {
void WorldChallengeFrame(PPCContext& ctx, uint8_t* base);  // world_challenge.cpp
}

namespace {
// Calls the guest virtual method at `offset` of `object` (r3 = object, r4 = arg); r3.
uint32_t CallVirtual(PPCContext& ctx, uint8_t* base, uint32_t object, uint32_t offset,
                     uint32_t r4 = 0) {
  const uint32_t vtable = Load32(forza::GuestPtr(base, object));
  PPCFunc* fn = rex::runtime::ResolveIndirectFunction(Load32(forza::GuestPtr(base, vtable + offset)));
  return fn ? CallGuest(ctx, base, fn, object, r4) : 0;
}

// The player manager's (sub_82577AD8) players: its list method at `method` (0x18, 0x1C or
// 0x20: the lists the online game walks) fills a guest std::list, which lives on the
// guest stack below the hooked function's frame while the calls run.
std::vector<uint32_t> ManagerPlayers(PPCContext& ctx, uint8_t* base, uint32_t method) {
  std::vector<uint32_t> players;
  PPCContext saved = ctx;
  const uint32_t sp = ctx.r1.u32, list = sp - 0x40, alloc = sp - 0x38;
  ctx.r1.u64 = sp - 0x100;
  ctx.r3.u64 = list;
  ctx.r4.u64 = alloc;
  __imp__sub_8247F018(ctx, base);
  __imp__sub_82577AD8(ctx, base);
  const uint32_t manager = ctx.r3.u32;
  const uint32_t vtable = Load32(forza::GuestPtr(base, manager));
  if (PPCFunc* fill = rex::runtime::ResolveIndirectFunction(
          Load32(forza::GuestPtr(base, vtable + method)))) {
    ctx.r3.u64 = manager;
    ctx.r4.u64 = list;
    fill(ctx, base);
    const uint32_t head = Load32(forza::GuestPtr(base, list));
    uint32_t node = Load32(forza::GuestPtr(base, head));
    for (int n = 0; node != head && n < 64; ++n) {
      players.push_back(Load32(forza::GuestPtr(base, node + 8)));
      node = Load32(forza::GuestPtr(base, node));
    }
  }
  ctx.r3.u64 = list;
  __imp__sub_828736E0(ctx, base);
  ctx.r3.u64 = Load32(forza::GuestPtr(base, list));
  __imp__sub_823FD208(ctx, base);
  ctx = saved;
  return players;
}

// A player's lobby status record (player->vfunc 0x70): status +6, CPU driver flag +7.
uint32_t StatusRecord(PPCContext& ctx, uint8_t* base, uint32_t player) {
  return CallVirtual(ctx, base, player, 0x70);
}
// Humans in the online game: its players (+0x10C) less the CPU drivers.
uint32_t Humans(PPCContext& ctx, uint8_t* base, uint32_t game) {
  const uint32_t players = Load32(forza::GuestPtr(base, game + kPlayers));
  const uint32_t ai = CallGuest(ctx, base, __imp__sub_828754A0, game, kIsAiPlayer);
  return players > ai ? players - ai : 0;
}

// FORZA_EVENT_TRACE: the CPU drivers this console has, whether it runs them (local), their
// status and the difficulty their driver (player->vfunc 0x78) got from sub_82568BD8: the
// level (+0x25B) and its MPAIDifficultyMap values (+0x260, +0x264).
void LogCpuDrivers(PPCContext& ctx, uint8_t* base, const char* what) {
  std::string out;
  for (const uint32_t p : ManagerPlayers(ctx, base, 0x1C)) {
    const uint32_t record = StatusRecord(ctx, base, p);
    if (!record || forza::GuestPtr(base, record)[7] != 1) continue;
    const uint32_t local = CallVirtual(ctx, base, p, 0x9C) & 0xFF;
    const uint32_t driver = CallVirtual(ctx, base, p, 0x78);
    const uint8_t* d = driver ? forza::GuestPtr(base, driver) : nullptr;
    char buf[96];
    std::snprintf(buf, sizeof(buf), " [%08X local %u status %u level %u %08X %08X]", p, local,
                  forza::GuestPtr(base, record)[6], d ? d[0x25B] : 0xFF,
                  d ? Load32(d + 0x260) : 0, d ? Load32(d + 0x264) : 0);
    out += buf;
  }
  REXLOG_INFO("EVENT: {}: CPU drivers{}", what, out.empty() ? " none" : out);
}

// A campaign round: every CPU driver this console has gets the event's skill (the host's
// and those another console runs; the race's AI reads them when it loads).
void ApplyCampaignSkill(PPCContext& ctx, uint8_t* base, const char* what) {
  if (!CampaignGame(base)) return;
  int count = 0;
  for (const uint32_t p : ManagerPlayers(ctx, base, 0x1C)) {
    const uint32_t record = StatusRecord(ctx, base, p);
    if (!record || forza::GuestPtr(base, record)[7] != 1) continue;
    SetCampaignSkill(base, CallVirtual(ctx, base, p, 0x78));
    ++count;
  }
  if (count && (campaign_logged != campaign_event.load() || Trace())) {
    campaign_logged = campaign_event.load();
    REXLOG_INFO("WORLD: campaign event {} ({}): {} CPU driver(s) at the event's skill {} "
                "temperament {}", campaign_event.load(), what, count, campaign_skill.load(),
                campaign_temperament.load());
  }
}
}  // namespace

// sub_8286A800(game, skip): the host asks for the next round's options (Find Game: the
// hopper's GetGameOptions, round counter kRound).
extern "C" REX_FUNC(sub_8286A800) {
  if (Trace()) LogGame("next round options requested", base, ctx.r3.u32, uint32_t(ctx.lr));
  __imp__sub_8286A800(ctx, base);
}

// sub_8287E538(game, options): new game options for the online game.
extern "C" REX_FUNC(sub_8287E538) {
  if (Trace()) {
    const uint8_t* o = forza::GuestPtr(base, ctx.r4.u32);
    REXLOG_INFO("EVENT: options set (lr {:08X}): preset {} track {} max {} max AI {} AI diff {}",
                uint32_t(ctx.lr), o[kOptPreset], Load32(o + kOptTrack), o[kOptMaxPlayers],
                o[kOptMaxAi], o[kOptAiDifficulty]);
  }
  __imp__sub_8287E538(ctx, base);
}

// sub_8287BBE0(game, flag): the host adds or removes CPU drivers (min(MaxPlayers -
// humans, MaxAIPlayers)). With world_event_cpu_with_peers off, an event with more than one
// human runs without them: MaxAIPlayers is 0 for the fill while two or more humans are in
// the lobby, and the event's own value otherwise.
extern "C" REX_FUNC(sub_8287BBE0) {
  const uint32_t game = ctx.r3.u32;
  if (Trace()) LogGame("AI fill", base, game, uint32_t(ctx.lr));
  if (REXCVAR_GET(world_events) && game && game == WorldPlaylistGame(base)) {
    uint8_t* g = forza::GuestPtr(base, game);
    if (g[kHost] && g[kPreset] != kPresetFreeRoam) {
      if (event_max_ai == 0xFF) event_max_ai = g[kMaxAi];
      const bool peers = Humans(ctx, base, game) > 1;
      const uint8_t max_ai = peers && !REXCVAR_GET(world_event_cpu_with_peers) ? 0 : event_max_ai;
      if (g[kMaxAi] != max_ai) {
        REXLOG_INFO("WORLD: {} CPU drivers at most ({} human(s))", max_ai, Humans(ctx, base, game));
        g[kMaxAi] = max_ai;
      }
    } else {
      event_max_ai = 0xFF;
    }
  }
  __imp__sub_8287BBE0(ctx, base);
}

// sub_8286A250(game, launch time, second time): the host starts the intermission.
extern "C" REX_FUNC(sub_8286A250) {
  if (Trace()) LogGame("intermission", base, ctx.r3.u32, uint32_t(ctx.lr));
  __imp__sub_8286A250(ctx, base);
}

// sub_828786E8(game): the host launches the round.
extern "C" REX_FUNC(sub_828786E8) {
  if (Trace()) LogGame("launch", base, ctx.r3.u32, uint32_t(ctx.lr));
  __imp__sub_828786E8(ctx, base);
}

// sub_8286E660(game): a round started (in game); its host asks for the next one.
extern "C" REX_FUNC(sub_8286E660) {
  if (Trace()) LogGame("round started", base, ctx.r3.u32, uint32_t(ctx.lr));
  __imp__sub_8286E660(ctx, base);
}

// sub_8286A2E0(game, ...): the launch builds the race settings and loads the round.
extern "C" REX_FUNC(sub_8286A2E0) {
  if (Trace()) LogGame("load", base, ctx.r3.u32, uint32_t(ctx.lr));
  __imp__sub_8286A2E0(ctx, base);
}

// sub_82878800(game): a peer learns that the host launched (state 11).
extern "C" REX_FUNC(sub_82878800) {
  if (Trace()) LogGame("peer launch", base, ctx.r3.u32, uint32_t(ctx.lr));
  __imp__sub_82878800(ctx, base);
}

// sub_82875840(game): the round starts on every peer and loads it (sub_8286A2E0).
extern "C" REX_FUNC(sub_82875840) {
  const uint32_t game = ctx.r3.u32, lr = uint32_t(ctx.lr);
  if (Trace()) LogGame("round start", base, game, lr);
  ApplyCampaignSkill(ctx, base, "round start");
  __imp__sub_82875840(ctx, base);
  ApplyCampaignSkill(ctx, base, "round started");
  if (Trace()) {
    LogGame("round start done", base, game, lr);
    if (WorldPlaylistGame(base)) LogCpuDrivers(ctx, base, "round start");
  }
}

// sub_8286A468(game, player, status): this console sets a player's lobby status (0-2 in
// the lobby, 3 loading, 4 in game, 5 ready to return to the lobby / the host ends a free
// roam, 6 gone). The CPU drivers of an online lobby are the host's players, and nothing
// sets them in game: the race waits for every player to be in game
// (CWaitForAllPlayersToHaveLoadedToInGame, up to 45 s), then kicks those who aren't
// (CKickFailedLoadPlayers), the CPU drivers with them. In an event they follow the host's
// own player into "in game".
extern "C" REX_FUNC(sub_8286A468) {
  const uint32_t game = ctx.r3.u32, player = ctx.r4.u32, status = ctx.r5.u32;
  if (Trace()) {
    REXLOG_INFO("EVENT: status of player {:08X} -> {} (lr {:08X})", player, status,
                uint32_t(ctx.lr));
  }
  // A peer's round start walks the players it flags local (+152), which on a peer include
  // CPU drivers too, and sets them loading: their statuses are the host's to set.
  if (REXCVAR_GET(world_events) && game && game == WorldPlaylistGame(base) &&
      !forza::GuestPtr(base, game)[kHost]) {
    const uint32_t record = StatusRecord(ctx, base, player);
    if (record && forza::GuestPtr(base, record)[7] == 1) return;
  }
  __imp__sub_8286A468(ctx, base);
  if (status != 4 || !REXCVAR_GET(world_events) || game != WorldPlaylistGame(base)) return;
  if (player == CallGuest(ctx, base, __imp__sub_828653D8, game)) {
    ApplyCampaignSkill(ctx, base, "in game");
  }
  if (Trace() && forza::GuestPtr(base, game)[kPreset] != kPresetFreeRoam &&
      player == CallGuest(ctx, base, __imp__sub_828653D8, game)) {
    LogCpuDrivers(ctx, base, "in game");
  }
  uint8_t* g = forza::GuestPtr(base, game);
  if (!g[kHost] || g[kPreset] == kPresetFreeRoam) return;
  if (player != CallGuest(ctx, base, __imp__sub_828653D8, game)) return;
  int count = 0;
  for (const uint32_t p : ManagerPlayers(ctx, base, 0x20)) {
    const uint32_t record = StatusRecord(ctx, base, p);
    const uint8_t* r = forza::GuestPtr(base, record);
    if (record && r[7] == 1 && r[6] != status) {
      CallGuest(ctx, base, sub_8286A468, game, p, status);
      ++count;
    }
  }
  if (count) REXLOG_INFO("WORLD: the host is in game: {} CPU driver(s) too", count);
}

// sub_82880A88(status record): a player's status. The round start (sub_82875840) skips
// loading the round when a player it flags local is already loading or in game (3-5: it
// joined a game in progress). On a peer the CPU drivers are flagged local too and the host
// has just set them loading, so a peer never loaded an event (it waited in the lobby until
// the host's race kicked it after 45 s). CPU drivers don't count for that check.
extern "C" REX_FUNC(sub_82880A88) {
  if (uint32_t(ctx.lr) == FORZA_ADDR(0x82875A10)) {
    const uint8_t* r = forza::GuestPtr(base, ctx.r3.u32);
    if (r[7] == 1) {
      ctx.r3.u64 = 0;
      return;
    }
  }
  __imp__sub_82880A88(ctx, base);
}

// sub_828655E0(status record): CWaitForAllPlayersReadyToReturnToLobby's test (sub_82878530,
// the results screen's "WAITING FOR OTHER PLAYERS..."): a player is ready unless loading or
// in game (3, 4). CPU drivers stay in game until the lobby removes them, so the results
// screen waited its 45 s for them after every event. In a world event they count as ready
// (their status is left alone: set to 5 they crashed the host when the lobby removed them).
extern "C" REX_FUNC(__imp__sub_828655E0);
extern "C" REX_FUNC(sub_828655E0) {
  if (REXCVAR_GET(world_event_cpu_ready) && REXCVAR_GET(world_events)) {
    const uint8_t* r = forza::GuestPtr(base, ctx.r3.u32);
    if (r[7] == 1) {
      if (const uint32_t game = WorldPlaylistGame(base)) {
        if (forza::GuestPtr(base, game)[kPreset] != kPresetFreeRoam) {
          ctx.r3.u64 = 1;
          return;
        }
      }
    }
  }
  __imp__sub_828655E0(ctx, base);
}

// sub_82880A90(status record, status): the store behind every status change (logged with
// FORZA_EVENT_TRACE or FORZA_LOBBY_LOG).
extern "C" REX_FUNC(sub_82880A90) {
  if (Trace() || forza::LobbyLogEnabled()) {
    const uint8_t* r = forza::GuestPtr(base, ctx.r3.u32);
    if (r[6] != ctx.r4.u8) {
      REXLOG_INFO("EVENT: status record {:08X}{} {} -> {} (lr {:08X})", ctx.r3.u32,
                  r[7] == 1 ? " (CPU)" : "", r[6], ctx.r4.u8, uint32_t(ctx.lr));
    }
  }
  __imp__sub_82880A90(ctx, base);
}

// sub_825ADB30: CIsFreeRoamSessionOver's update, every frame of the online free roam on
// every peer ("yes" once the host's status is 5). On the world's host it takes the event
// the server asked for.
extern "C" REX_FUNC(sub_825ADB30) {
  if (const uint32_t world = WorldPlaylistGame(base)) {
    if (forza::GuestPtr(base, world)[kPreset] == kPresetFreeRoam) forza::WorldChallengeFrame(ctx, base);
  }
  const uint32_t game = REXCVAR_GET(world_events) ? WorldPlaylistGame(base) : 0;
  if (game) {
    uint8_t* g = forza::GuestPtr(base, game);
    const bool world = g[kPreset] == kPresetFreeRoam && g[kHost] != 0;
    const uint32_t event = requested_event.load();
    const int64_t now = NowMs();
    if (!world) {
      taking = Taking::kIdle;
    } else if (taking == Taking::kIdle && event && now - requested_at.load() < 10000) {
      taking = Taking::kAsked;
      taking_event = event;
      taking_since = now;
      requested_event.store(0);
      REXLOG_INFO("WORLD: the server starts event {}: asking for the next round", event);
      CallGuest(ctx, base, sub_8286A800, game, 1);
    } else if (taking == Taking::kAsked) {
      const bool next_is_event = g[kNextReady] && g[kNextPreset] != kPresetFreeRoam;
      if (next_is_event || now - taking_since > 15000) {
        const uint32_t player = CallGuest(ctx, base, __imp__sub_828653D8, game);
        REXLOG_INFO("WORLD: {} the free roam for event {} (next round preset {})",
                    next_is_event ? "ending" : "no event round came; ending anyway",
                    taking_event, g[kNextPreset]);
        if (player) CallGuest(ctx, base, sub_8286A468, game, player, kStatusHostEndsFreeRoam);
        taking = Taking::kEnded;
      }
    }
  }
  __imp__sub_825ADB30(ctx, base);
}

// sub_8287F670(game): the lobby's update, every frame (the host starts the intermission
// and launches). An event round of the world playlist waits world_event_wait_seconds and
// starts at once when its grid is all humans.
extern "C" REX_FUNC(sub_8287F670) {
  forza::LobbyLogTick(base, ctx.r3.u32);  // FORZA_LOBBY_LOG (src/debug/lobby_log.cpp)
  if (const uint32_t game = REXCVAR_GET(world_events) ? WorldPlaylistGame(base) : 0) {
    uint8_t* g = forza::GuestPtr(base, game);
    const bool event = g[kPreset] != kPresetFreeRoam;
    if (event) taking = Taking::kIdle;  // the free roam that took the event is over
    if (event) {
      const uint32_t wait = uint32_t(std::max(0, REXCVAR_GET(world_event_wait_seconds))) * 1000;
      if (!intermission_changed) {
        world_intermission[0] = Load32(g + kIntermission);
        world_intermission[1] = Load32(g + kShortIntermission);
        intermission_changed = true;
      }
      if (Load32(g + kIntermission) != wait) {
        Store32(g + kIntermission, wait);
        Store32(g + kShortIntermission, wait);
        REXLOG_INFO("WORLD: event round (preset {}, track {}): {} s wait lobby", g[kPreset],
                    Load32(g + kTrack), wait / 1000);
      }
      const uint32_t launch = Load32(g + kLaunchTime);
      if (g[kHost] && launch > 1 && Load32(g + kState) == 4) {
        const uint32_t humans = Humans(ctx, base, game);
        if (humans >= g[kMaxPlayers]) {
          Store32(g + kLaunchTime, 1);  // the launch time has passed: launch now
          REXLOG_INFO("WORLD: the event's grid is full of humans ({}): starting now", humans);
        }
      }
    } else if (!event && intermission_changed) {
      Store32(g + kIntermission, world_intermission[0]);
      Store32(g + kShortIntermission, world_intermission[1]);
      intermission_changed = false;
      REXLOG_INFO("WORLD: world round: {} ms intermission again", world_intermission[0]);
    }
  }
  __imp__sub_8287F670(ctx, base);
}

// sub_82877470(game): the host gives every CPU driver the round's AI difficulty (+0x44A),
// after each AI fill. In a world event it follows the human share of the grid.
extern "C" REX_FUNC(sub_82877470) {
  if (const uint32_t game = REXCVAR_GET(world_events) ? WorldPlaylistGame(base) : 0) {
    uint8_t* g = forza::GuestPtr(base, game);
    if (g[kPreset] != kPresetFreeRoam && g[kHost]) {
      const uint32_t humans = Humans(ctx, base, game);
      const int32_t knob = REXCVAR_GET(world_event_ai_grid);
      const uint32_t grid = knob > 0 ? uint32_t(knob) : g[kMaxPlayers] ? g[kMaxPlayers] : 8;
      // a campaign round: the event's own skill at Medium (sub_82568BD8 below)
      const uint8_t level = CampaignGame(base) ? kAiMedium : AiDifficulty(humans, grid);
      g[kAiDifficulty] = level;
      if (level != last_difficulty) {
        static const char* const kNames[] = {"Easy", "Medium", "Hard"};
        REXLOG_INFO("WORLD: {} human(s) of {}: CPU drivers {}{}", humans, grid, kNames[level],
                    CampaignGame(base) ? " (campaign event)" : "");
        last_difficulty = level;
      }
    }
  }
  __imp__sub_82877470(ctx, base);
  if (Trace() && WorldPlaylistGame(base)) LogCpuDrivers(ctx, base, "AI difficulty applied");
}

// sub_82568BD8(driver, level): a CPU driver's level and its MPAIDifficultyMap rows (skill,
// temperament), from the online AI fill (sub_82877470) and wherever else a driver's level
// is set. In a campaign round the driver gets the event's rows instead.
extern "C" REX_FUNC(__imp__sub_82568BD8);
extern "C" REX_FUNC(sub_82568BD8) {
  const uint32_t driver = ctx.r3.u32;
  __imp__sub_82568BD8(ctx, base);
  if (CampaignGame(base)) SetCampaignSkill(base, driver);
}

// sub_82876378(game, player, flag): kick a player (CKickFailedLoadPlayers, vote kicks).
// A CPU driver is the host's own player, so kicking one shows the host "you have been
// voted out" and ends the host's online game (reason 3). CPU drivers are not kicked;
// the AI fill removes them when humans need their slots.
extern "C" REX_FUNC(sub_82876378) {
  const uint32_t record = REXCVAR_GET(world_events) && WorldPlaylistGame(base)
                              ? StatusRecord(ctx, base, ctx.r4.u32)
                              : 0;
  if (record && forza::GuestPtr(base, record)[7] == 1) {
    REXLOG_INFO("WORLD: not kicking CPU driver {:08X} (status {})", ctx.r4.u32,
                forza::GuestPtr(base, record)[6]);
    return;
  }
  __imp__sub_82876378(ctx, base);
}

// sub_8286FC98(game, reason): the local player was removed from the online game (3: voted
// out, 7: kicked for inactivity): a message box, then the online game ends (sub_8288B840
// -> sub_8287DA10, which tears the session down and pumps its messages). A second kick
// message pumped there re-entered this and tore the session down inside the outer
// teardown, which then read the freed session. Nested calls are dropped.
extern "C" REX_FUNC(sub_8286FC98) {
  static thread_local int depth = 0;
  if (Trace()) REXLOG_INFO("EVENT: removed from the online game, reason {} (lr {:08X})", ctx.r4.u32, uint32_t(ctx.lr));
  if (depth > 0) {
    REXLOG_INFO("WORLD: removed from the online game again (reason {}) while ending it: ignored",
                ctx.r4.u32);
    return;
  }
  ++depth;
  __imp__sub_8286FC98(ctx, base);
  --depth;
}

// Removal trace (FORZA_EVENT_TRACE): who removes which player, from where.
namespace {
std::string Backtrace(const PPCContext& ctx, const uint8_t* base, int depth) {
  std::string out;
  uint32_t frame = ctx.r1.u32;
  for (int i = 0; i < depth && frame; ++i) {
    const uint32_t parent = Load32(forza::GuestPtr(base, frame));
    if (parent <= frame || parent - frame > 0x10000 || (parent & 7)) break;
    const uint32_t lr = Load32(forza::GuestPtr(base, parent - 8));
    if (lr < 0x82000000 || lr >= 0x84000000) break;
    char buf[16];
    std::snprintf(buf, sizeof(buf), " %08X", lr);
    out += buf;
    if (!(parent & 0xFFFF)) break;
    frame = parent;
  }
  return out;
}

void TracePlayer(const char* what, PPCContext& ctx, uint8_t* base, uint32_t player, uint32_t arg) {
  const uint32_t record = player ? StatusRecord(ctx, base, player) : 0;
  const uint32_t local = player ? CallVirtual(ctx, base, player, 0x9C) & 0xFF : 0;
  const uint8_t* r = record ? forza::GuestPtr(base, record) : nullptr;
  REXLOG_INFO("EVENT: {} player {:08X} arg {} record {:08X} status {} cpu {} local {} (lr {:08X}){}",
              what, player, arg, record, r ? r[6] : 0xFF, r ? r[7] : 0xFF, local,
              uint32_t(ctx.lr), Backtrace(ctx, base, 8));
}
}  // namespace

extern "C" REX_FUNC(__imp__sub_82573660);
extern "C" REX_FUNC(__imp__sub_825723B0);
extern "C" REX_FUNC(__imp__sub_825761E0);
extern "C" REX_FUNC(__imp__sub_82CF7278);

// sub_82573660(manager, player, broadcast): the player manager removes a player of this
// console (its CPU drivers' names are freed in the database), then sub_825723B0.
// The race's unload (CRemoveAIPlayersOnUnloadComponent, sub_826337A8, lr 0x82633824) calls
// it for every CPU driver, also for those another console runs (player vfunc 0x9C false:
// the host hands some of them to each peer when the race loads). For those the local path
// is the wrong one (the game's own AI fill calls sub_82869CE0 first for them; a player of
// another console leaves through the session, sub_825761E0), and the owner's removal comes
// on top: with two consoles, one of them crashed or hung right there every time (the
// session's message pump on a corrupted queue). Each console now removes only the CPU
// drivers it runs; the others go when their owner's removal arrives through the session.
extern "C" REX_FUNC(sub_82573660) {
  const uint32_t player = ctx.r4.u32, lr = uint32_t(ctx.lr);
  if (Trace()) TracePlayer("remove local", ctx, base, player, ctx.r5.u32);
  if (lr == FORZA_ADDR(0x82633824) && REXCVAR_GET(world_events) && WorldPlaylistGame(base)) {
    const uint32_t record = StatusRecord(ctx, base, player);
    if (record && forza::GuestPtr(base, record)[7] == 1 &&
        !(CallVirtual(ctx, base, player, 0x9C) & 0xFF)) {
      REXLOG_INFO("WORLD: the race unloads: CPU driver {:08X} is another console's, not removed here",
                  player);
      return;
    }
  }
  __imp__sub_82573660(ctx, base);
}

// sub_825723B0(manager, player): every removal ends here (lists, status 6 via
// sub_8287DEC8, a "player left" event).
extern "C" REX_FUNC(sub_825723B0) {
  const uint32_t player = ctx.r4.u32;
  if (Trace()) TracePlayer("remove", ctx, base, player, 0);
  __imp__sub_825723B0(ctx, base);
  if (Trace()) REXLOG_INFO("EVENT: removed {:08X}", player);
}

// sub_82869CE0(game, player): the AI fill, before removing a CPU driver another console owns.
extern "C" REX_FUNC(__imp__sub_82869CE0);
extern "C" REX_FUNC(sub_82869CE0) {
  if (Trace()) TracePlayer("AI fill: another console's CPU driver", ctx, base, ctx.r4.u32, 0);
  __imp__sub_82869CE0(ctx, base);
}

// sub_825761E0(manager + 8, player + 4, flag): the session says a player left.
extern "C" REX_FUNC(sub_825761E0) {
  if (Trace()) TracePlayer("session removes", ctx, base, ctx.r4.u32 ? ctx.r4.u32 - 4 : 0, ctx.r5.u32);
  __imp__sub_825761E0(ctx, base);
}

// sub_82CF7278(session, flag): the session is torn down.
extern "C" REX_FUNC(sub_82CF7278) {
  if (Trace()) {
    REXLOG_INFO("EVENT: session teardown flag {} (lr {:08X}){}", ctx.r4.u32, uint32_t(ctx.lr),
                Backtrace(ctx, base, 8));
  }
  __imp__sub_82CF7278(ctx, base);
}

namespace forza {
// WORLD_EVENT from the server (net.cpp): op u8 (1: an event waits for this world's host),
// event id u32, world session id (8 bytes). Op 2 (to every console of a Co-op Festival
// world, server/forza_live/world/campaign.py ai_message): the campaign round's event id u32, world
// session id, track u32, AISkills row u32, AITemperaments row u32.
void LiveWorldEvent(const uint8_t* p, size_t n) {
  if (n >= 25 && p[0] == 2) {
    const uint32_t event = Load32(p + 1), track = Load32(p + 13), skill = Load32(p + 17),
                   temperament = Load32(p + 21);
    if (campaign_event.exchange(event) != event || campaign_skill.load() != skill) {
      REXLOG_INFO("FORZA_LIVE: campaign event {} on track {}: CPU drivers at skill {} "
                  "temperament {}", event, track, skill, temperament);
    }
    campaign_track.store(track);
    campaign_temperament.store(temperament);
    campaign_skill.store(skill);
    return;
  }
  if (n < 5 || p[0] != 1) return;
  const uint32_t event = Load32(p + 1);
  if (requested_event.exchange(event) != event || Trace()) {
    REXLOG_INFO("FORZA_LIVE: the server starts event {} in our world", event);
  }
  requested_at.store(NowMs());
}
}  // namespace forza
