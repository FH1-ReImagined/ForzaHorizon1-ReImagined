// forza - Xbox Live services from a Forza Live server: the XAM messages a title sends
// to the XLiveBase (0xFC) and XGI (0xFB) apps, and the logon queries.
//
// With Live sign-in (identity.h: LiveSignin) the game believes it is signed in to Xbox
// Live and asks for:
//
// - XStringVerify (0xFC/0x5000C): the profanity check of user text. Every string is
//   accepted (the SDK failed it, and the game retried every frame).
// - XFriendsCreateEnumerator (0xFC/0x58020): the friends list: our friends on the server
//   (live_friends.cpp, #92), those in our room with the presence they reported (online,
//   playing, joinable session), so lobbies show which friends are online and their
//   joinable games; friends elsewhere or offline after them. With an older server (no
//   friends list) every other player in our room is a friend, as before. Invites never go
//   to this list: the player picks the invitees (live_invites.cpp, #90). The SDK failed it.
// - XSessionCreate (0xFB/0xB0010) for a session we host: the SDK leaves XSESSION_INFO
//   empty. We fill it (an online peer session id, our XNADDR, a random key) and, for a
//   presence session, publish it, so friends see it as joinable. Delete and modify
//   update that.
// - XSessionMigrateHost (0xFB/0xB001E): when the host leaves, the new host's info gets our
//   address and its session is published as ours. Forza's game does not call it (its
//   free-roam host migration stays inside the game), so a guest also publishes a standby
//   record of the matchmaking session it joined, for the server to advertise once the host
//   is gone (PublishStandbyLocked).
// - XPresenceSubscribe / Unsubscribe / CreateEnumerator (0xFC/0x5801E, 0x58044,
//   0x58019): the lobby's active roster asks for the presence (state, session id) of
//   the friends it lists; answered from the room's roster.
// - XSessionSearchByIds / ByID (0xFB/0xB0060, 0xB001B): friends' session ids to
//   XSESSION_INFO, from what each player published.
// - XNetQosListen / XNetQosLookup: the host's game description travels with its
//   presence; lookups are answered from the roster.
// - XamShowFriendsUI / XamShowGameInviteUI / XInviteSend (0xFC/0x50002): there is no
//   Guide; the party lobby's "Invite Friends" opens our invite picker (live_invites.cpp,
//   #90), where the player searches gamertags and picks whom to invite. The invites go
//   through the server; the friend's game asks its player, and XInviteGetAcceptedInfo
//   (0xFC/0x58023) hands the game the invite they accepted, which then joins our session.
// - XNetLogonGetTitleID / XNetLogonGetMachineID: the SDK stubs return garbage.
// - XUserReadStats / XSessionWriteStats (0xFB/0xB0021, 0xB0025): Xbox LIVE stats rows
//   (Forza's only view is TrueSkill) kept and rated by the server (server/forza_live/competition/stats.py);
//   the SDK left the results empty and Forza crashed creating its party session.
//
// Everything else goes to the SDK. FORZA_NETTRACE=1 logs every XMsg call (as net.cpp
// did before) and what we answered. Message layouts follow the Xenia netplay fork
// (AdrianCassar/xenia-canary, BSD), see RecompiledHaloReach's docs/online_plan.md.
// Parts that follow Xenia: Copyright (c) 2022, Ben Vanik and Xenia project contributors,
// BSD 3-Clause License (https://github.com/xenia-project/xenia/blob/master/LICENSE).

#include "../platform/guest_memory.h"
#include "../platform/sdk_import.h"
#include "gamertag_text.h"
#include "identity.h"
#include "live.h"
#include "../platform/discord_presence.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ppc/context.h>
#include <rex/ppc/func.h>
#include <rex/system/kernel_state.h>
#include <rex/system/xenumerator.h>
#include <rex/system/xevent.h>
#include <rex/system/xmemory.h>
#include <rex/system/xthread.h>
#include <rex/system/xtypes.h>


#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <random>
#include <string>
#include <unordered_map>
#include <vector>

using forza::GuestPtr;

namespace {

using GuestFunc = void (*)(PPCContext&, uint8_t*);

constexpr uint32_t kAppXgi = 0xFB;
constexpr uint32_t kAppXLiveBase = 0xFC;
constexpr uint32_t kTitleId = 0x4D5309C9;  // Forza Horizon
constexpr uint32_t kNotOurs = 0xFFFFFFFF;  // handler result: let the SDK answer
constexpr uint32_t kSuccess = 0;
constexpr uint32_t kInvalidArg = 0x80070057;  // X_E_INVALIDARG
constexpr uint32_t kIoPending = 0x3E5;

// X_ONLINE_FRIENDSTATE flags.
constexpr uint32_t kFriendOnline = 0x1;
constexpr uint32_t kFriendPlaying = 0x2;

// XSESSION_CREATE flags.
constexpr uint32_t kSessionHost = 0x1;
constexpr uint32_t kSessionUsesPresence = 0x2;

constexpr size_t kSessionInfoSize = 0x3C;  // XSESSION_INFO
constexpr size_t kFriendSize = 0xC4;       // X_ONLINE_FRIEND

bool Trace() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_NETTRACE");
    return v && *v && *v != '0' && std::string(v) != "packets";
  }();
  return on;
}

uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
void Store32(uint8_t* p, uint32_t v) {
  p[0] = uint8_t(v >> 24);
  p[1] = uint8_t(v >> 16);
  p[2] = uint8_t(v >> 8);
  p[3] = uint8_t(v);
}
void Store64(uint8_t* p, uint64_t v) {
  Store32(p, uint32_t(v >> 32));
  Store32(p + 4, uint32_t(v));
}

uint64_t RandomU64() {
  static std::mutex mutex;
  static std::mt19937_64 rng(std::random_device{}());
  std::lock_guard<std::mutex> lock(mutex);
  return rng();
}

uint64_t FileTimeNow() {
  using namespace std::chrono;
  const uint64_t unix_100ns =
      duration_cast<nanoseconds>(system_clock::now().time_since_epoch()).count() / 100;
  return unix_100ns + 116444736000000000ull;
}

// The value of argument `index` of an X_ARGUMENT_LIST-style block: 16-byte entries
// {native size u32, pad, value pointer u64}.
uint32_t ArgPointer(const uint8_t* base, uint32_t args, int index) {
  return Load32(GuestPtr(base, args + 16 * index + 12));
}

// The presence our player shows when not hosting a joinable session.
std::vector<uint8_t> IdlePresenceExtra();
void SetIdlePresence() {
  forza::LiveSetPresence(kFriendOnline | kFriendPlaying, nullptr, "Forza Horizon", IdlePresenceExtra());
}

// --- XLiveBase -------------------------------------------------------------------

// XStringVerify: the message is an XLIVEBASE_ASYNC_MESSAGE whose task holds a
// marshalled request (title id, flags, locale size u16, string count u16 — the wire
// format is little-endian) and a results buffer: STRING_VERIFY_RESPONSE {count u16,
// results pointer u32} followed by one HRESULT per string.
uint32_t XStringVerify(uint8_t* base, uint32_t message) {
  if (!message) return kInvalidArg;
  const uint32_t task = Load32(GuestPtr(base, message));
  if (!task) return kInvalidArg;
  const uint32_t request = Load32(GuestPtr(base, task + 0x18)), request_size = Load32(GuestPtr(base, task + 0x1C));
  const uint32_t results = Load32(GuestPtr(base, task + 0x2C)), results_size = Load32(GuestPtr(base, task + 0x30));
  if (!request || request_size < 12 || !results || results_size < 6) return kInvalidArg;
  uint32_t count = base[request + 10] | base[request + 11] << 8;
  count = std::min<uint32_t>(count, (results_size - 6) / 4);
  std::memset(GuestPtr(base, results), 0, results_size);
  base[results] = uint8_t(count >> 8);
  base[results + 1] = uint8_t(count);
  Store32(GuestPtr(base, results + 2), results + 6);  // all results stay S_OK
  return kSuccess;
}

// XFriendsCreateEnumerator(user, first, count, buffer size out, handle out): arguments
// in an X_ARGUMENT_LIST. Items are X_ONLINE_FRIEND.
uint32_t XFriendsCreateEnumerator(uint8_t* base, uint32_t args) {
  using namespace rex;
  using namespace rex::system;
  if (!args) return kInvalidArg;
  const uint32_t first_ptr = ArgPointer(base, args, 1), count_ptr = ArgPointer(base, args, 2);
  const uint32_t size_out = ArgPointer(base, args, 3), handle_out = ArgPointer(base, args, 4);
  if (!handle_out || !size_out || !count_ptr) return kInvalidArg;
  Store32(GuestPtr(base, handle_out), 0);
  const uint32_t first = first_ptr ? Load32(GuestPtr(base, first_ptr)) : 0;
  const uint32_t count = std::min<uint32_t>(Load32(GuestPtr(base, count_ptr)), 100);
  if (!count) return kInvalidArg;
  Store32(GuestPtr(base, size_out), count * kFriendSize);

  auto e = make_object<XStaticUntypedEnumerator>(REX_KERNEL_STATE(), count, kFriendSize);
  if (XFAILED(e->Initialize(0xFFFFFFFF, kAppXLiveBase, 0x58021, 0x58022, 0))) return kInvalidArg;
  const auto roster = forza::LiveFriendsList();
  const uint64_t now = FileTimeNow();
  for (size_t i = first; i < roster.size() && e->item_count() < count; ++i) {
    const forza::LiveFriend& f = roster[i];
    uint8_t* item = e->AppendItem();
    std::memset(item, 0, kFriendSize);
    Store64(item, f.xuid);
    forza::StoreGamertag(item + 8, 16, f.gamertag);
    Store32(item + 0x18, f.state);
    std::memcpy(item + 0x1C, f.session, 8);  // session id
    if (f.state & kFriendPlaying) Store32(item + 0x24, kTitleId);
    Store64(item + 0x28, now);
    const size_t chars = std::min<size_t>(f.status.size(), 63);
    for (size_t c = 0; c < chars; ++c) item[0x45 + 2 * c] = uint8_t(f.status[c]);  // UTF-16BE
    Store32(item + 0x40, uint32_t(chars ? chars + 1 : 0));
  }
  // Logged when it changes (the game asks every few seconds in its lobbies).
  std::string names;
  for (const auto& f : roster) {
    names += (names.empty() ? "" : ", ") + f.gamertag + (f.state ? "" : " (offline)");
  }
  static std::mutex logged_mutex;
  static std::string logged = "-";
  std::lock_guard<std::mutex> lock(logged_mutex);
  if (Trace() || names != logged) {
    REXLOG_INFO("FORZA_LIVE: the game's friends list: {} of {}: {}", e->item_count(),
                roster.size(), names.empty() ? "(none)" : names);
    logged = names;
  }
  Store32(GuestPtr(base, handle_out), e->handle());
  return kSuccess;
}

// One X_ONLINE_PRESENCE (0xA4 bytes): XUID, state, session id, title id, time, rich
// presence (X_ONLINE_FRIEND has the same fields with the gamertag after the XUID).
constexpr size_t kPresenceSize = 0xA4;

void WritePresence(uint8_t* item, const forza::LiveFriend& f) {
  std::memset(item, 0, kPresenceSize);
  Store64(item, f.xuid);
  Store32(item + 0x8, f.state);
  std::memcpy(item + 0xC, f.session, 8);  // session id
  if (f.state & kFriendPlaying) Store32(item + 0x14, kTitleId);
  Store64(item + 0x18, FileTimeNow());
  const size_t chars = std::min<size_t>(f.status.size(), 63);
  for (size_t c = 0; c < chars; ++c) item[0x25 + 2 * c] = uint8_t(f.status[c]);  // UTF-16BE
  Store32(item + 0x20, uint32_t(chars ? chars + 1 : 0));
}

// XPresenceCreateEnumerator(user, peer count, peer XUIDs, first, max, buffer size out,
// handle out): the presence of each requested player we know (the room's players).
uint32_t XPresenceCreateEnumerator(uint8_t* base, uint32_t args) {
  using namespace rex;
  using namespace rex::system;
  if (!args) return kInvalidArg;
  const uint32_t count_ptr = ArgPointer(base, args, 1), xuids = ArgPointer(base, args, 2);
  const uint32_t first_ptr = ArgPointer(base, args, 3);
  const uint32_t size_out = ArgPointer(base, args, 5), handle_out = ArgPointer(base, args, 6);
  if (!handle_out || !size_out || !count_ptr || !xuids) return kInvalidArg;
  Store32(GuestPtr(base, handle_out), 0);
  const uint32_t count = std::min<uint32_t>(Load32(GuestPtr(base, count_ptr)), 100);
  const uint32_t first = first_ptr ? Load32(GuestPtr(base, first_ptr)) : 0;
  if (!count) return kInvalidArg;
  Store32(GuestPtr(base, size_out), count * kPresenceSize);
  auto e = make_object<XStaticUntypedEnumerator>(REX_KERNEL_STATE(), count, kPresenceSize);
  if (XFAILED(e->Initialize(0, kAppXLiveBase, 0x5801A, 0x5801B, 0))) return kInvalidArg;
  const auto roster = forza::LiveRoster();
  for (uint32_t i = first; i < count; ++i) {
    const uint64_t xuid =
        uint64_t(Load32(GuestPtr(base, xuids + 8 * i))) << 32 | Load32(GuestPtr(base, xuids + 8 * i + 4));
    for (const auto& f : roster) {
      if (f.xuid == xuid) {
        WritePresence(e->AppendItem(), f);
        break;
      }
    }
  }
  if (Trace()) {
    REXLOG_INFO("FORZA_LIVE: presence of {} players: {} known", count, e->item_count());
  }
  Store32(GuestPtr(base, handle_out), e->handle());
  return kSuccess;
}

// --- XGI sessions ----------------------------------------------------------------

struct HostedSession {
  uint8_t info[kSessionInfoSize];
  uint32_t flags;
  uint32_t max_public, max_private;     // slots
  uint32_t filled_public, filled_private;
  uint64_t order;                       // creation order: the newest presence session wins
  uint32_t playlist = 0;                // a joined matchmaking session's (what we searched,
                                        // then the game type the game sets)
  std::vector<uint8_t> qos;             // a joined session's QoS data (from the search, or our
                                        // game's own once it made us the host)
  std::vector<uint64_t> members;
};
std::mutex sessions_mutex;
std::unordered_map<uint32_t, HostedSession> hosted_sessions;  // by session object
std::unordered_map<uint64_t, std::vector<uint8_t>> qos_data;  // XNetQosListen data by key id
uint64_t session_order = 0;
forza::social_presence::Car local_car_details;
uint64_t local_car_session = 0;
uint8_t joined_session[kSessionInfoSize] = {};  // the last session we joined as a guest
// Sessions we joined as a guest, by session object: their standby record (Matchmaking,
// below) and, if a host migration makes us the host (XSessionMigrateHost), the session we
// then publish as ours.
std::unordered_map<uint32_t, HostedSession> guest_sessions;

uint64_t SessionId(const uint8_t* info) {
  return uint64_t(Load32(info)) << 32 | Load32(info + 4);
}
uint64_t CurrentInteractiveSessionIdLocked() {
  const HostedSession* best=nullptr;
  for(const auto* sessions:{&hosted_sessions,&guest_sessions})for(const auto& [object,s]:*sessions)
    if(forza::social_presence::InteractiveSession(s.flags,SessionId(s.info))&&(!best||s.order>best->order))best=&s;
  return best?SessionId(best->info):0;
}

void PublishMatchLocked();    // Matchmaking, below
void PublishStandbyLocked();  // the same, for a session we joined
uint32_t SearchedPlaylistLocked();
std::vector<uint8_t> FoundQos(uint64_t session_id);

// The bytes our presence carries after the status line (friends read them back in
// LiveFriend::extra): slots (max public, max private, filled public, filled private, one
// byte each), then the session's QoS data (u16 length + bytes).
std::vector<uint8_t> PresenceExtra(const HostedSession& s) {
  std::vector<uint8_t> extra = {uint8_t(std::min<uint32_t>(s.max_public, 255)),
                                uint8_t(std::min<uint32_t>(s.max_private, 255)),
                                uint8_t(std::min<uint32_t>(s.filled_public, 255)),
                                uint8_t(std::min<uint32_t>(s.filled_private, 255))};
  auto it = qos_data.find(SessionId(s.info));
  const size_t size = it == qos_data.end() ? 0 : std::min<size_t>(it->second.size(), 1000);
  extra.push_back(uint8_t(size >> 8));
  extra.push_back(uint8_t(size));
  if (size) extra.insert(extra.end(), it->second.begin(), it->second.begin() + size);
  if(local_car_session==SessionId(s.info))forza::social_presence::Append(extra, local_car_details);
  return extra;
}
std::vector<uint8_t> IdlePresenceExtra() {
  std::vector<uint8_t> extra(6);
  if(local_car_session==CurrentInteractiveSessionIdLocked())forza::social_presence::Append(extra, local_car_details);
  return extra;
}

// Use the newest presence session, hosted or joined. This contains this session's
// human members; the Forza Live room roster includes unrelated games as well.
void UpdateDiscordPartyLocked() {
  const HostedSession* best = nullptr;
  for (const auto* sessions : {&hosted_sessions, &guest_sessions}) {
    for (const auto& [object, s] : *sessions) {
      if ((s.flags & kSessionUsesPresence) && (!best || s.order > best->order)) best = &s;
    }
  }
  forza::discord::Party(best ? best->filled_public + best->filled_private : 0,
                        best ? best->max_public + best->max_private : 0);
}

// Publishes the newest joinable party/gameplay session we host, or idle presence. Needs
// sessions_mutex.
void PublishPresenceLocked() {
  UpdateDiscordPartyLocked();
  const HostedSession* best = nullptr;
  for (const auto& [object, s] : hosted_sessions) {
    const bool joinable = forza::social_presence::JoinState(s.flags) != 0;
    if (joinable && (!best || s.order > best->order)) best = &s;
  }
  if (!best) {
    SetIdlePresence();
    return;
  }
  const uint32_t state = forza::social_presence::JoinState(best->flags);
  forza::LiveSetPresence(state, best->info, "Forza Horizon", PresenceExtra(*best));
}

// XSessionCreate {session object, flags, public slots, private slots, user index,
// XSESSION_INFO*, nonce*}: for a session we host, fill the info before the SDK
// completes the request.
void XSessionCreate(uint8_t* base, uint32_t buffer) {
  const uint32_t object = Load32(GuestPtr(base, buffer)), flags = Load32(GuestPtr(base, buffer + 4));
  const uint32_t info_ptr = Load32(GuestPtr(base, buffer + 20)), nonce_ptr = Load32(GuestPtr(base, buffer + 24));
  if (!info_ptr) return;
  if (!(flags & kSessionHost)) {  // joining someone's session: remember it for invites
    HostedSession g{};
    g.flags = flags;
    g.max_public = Load32(GuestPtr(base, buffer + 8));
    g.max_private = Load32(GuestPtr(base, buffer + 12));
    std::memcpy(g.info, GuestPtr(base, info_ptr), kSessionInfoSize);
    REXLOG_INFO("FORZA_LIVE: joining session {:016X} (flags {:08X}, {} public + {} private slots)",
                SessionId(g.info), flags, g.max_public, g.max_private);
    g.qos = FoundQos(SessionId(g.info));
    std::lock_guard<std::mutex> lock(sessions_mutex);
    std::memcpy(joined_session, g.info, kSessionInfoSize);
    g.order = ++session_order;
    g.playlist = SearchedPlaylistLocked();
    guest_sessions[object] = g;
    PublishStandbyLocked();
    PublishPresenceLocked();
    return;
  }
  HostedSession s{};
  s.flags = flags;
  s.max_public = Load32(GuestPtr(base, buffer + 8));
  s.max_private = Load32(GuestPtr(base, buffer + 12));
  Store64(s.info, RandomU64());
  s.info[0] = uint8_t((s.info[0] & 0x0F) | 0x80);  // XNET_XNKID_ONLINE_PEER
  forza::LiveSelfXnAddr(s.info + 8);
  Store64(s.info + 0x2C, RandomU64());
  Store64(s.info + 0x34, RandomU64());
  std::memcpy(GuestPtr(base, info_ptr), s.info, kSessionInfoSize);
  if (nonce_ptr) Store64(GuestPtr(base, nonce_ptr), RandomU64());
  REXLOG_INFO("FORZA_LIVE: hosting session {:016X} (flags {:08X}, {} public + {} private slots)",
              SessionId(s.info), flags, s.max_public, s.max_private);
  std::lock_guard<std::mutex> lock(sessions_mutex);
  s.order = ++session_order;
  hosted_sessions[object] = s;
  PublishPresenceLocked();
  PublishMatchLocked();
}

// XSessionDelete {session object, ...}
void XSessionDelete(uint8_t* base, uint32_t buffer) {
  std::lock_guard<std::mutex> lock(sessions_mutex);
  if (auto g = guest_sessions.find(Load32(GuestPtr(base, buffer))); g != guest_sessions.end()) {
    qos_data.erase(SessionId(g->second.info));  // what we described it with as its game host
    guest_sessions.erase(g);
    PublishStandbyLocked();
    PublishPresenceLocked();
  }
  auto it = hosted_sessions.find(Load32(GuestPtr(base, buffer)));
  if (it == hosted_sessions.end()) return;
  qos_data.erase(SessionId(it->second.info));
  hosted_sessions.erase(it);
  PublishPresenceLocked();
  PublishMatchLocked();
}

// XSessionModify {session object, flags, public slots, private slots}: only the join
// flags (XSESSION_CREATE_MODIFIERS_MASK) change.
void XSessionModify(uint8_t* base, uint32_t buffer) {
  constexpr uint32_t kModifiers = 0xF00;
  std::lock_guard<std::mutex> lock(sessions_mutex);
  auto it = hosted_sessions.find(Load32(GuestPtr(base, buffer)));
  if (it == hosted_sessions.end()) return;
  it->second.flags = (it->second.flags & ~kModifiers) | (Load32(GuestPtr(base, buffer + 4)) & kModifiers);
  it->second.max_public = Load32(GuestPtr(base, buffer + 8));
  it->second.max_private = Load32(GuestPtr(base, buffer + 12));
  REXLOG_INFO("FORZA_LIVE: session {:016X} modified: flags {:08X}, {} public + {} private slots",
              SessionId(it->second.info), it->second.flags, it->second.max_public,
              it->second.max_private);
  PublishPresenceLocked();
  PublishMatchLocked();
}

// XSessionJoin{Local,Remote} (0xB0012) / Leave (0xB0013) {session object, user count,
// XUIDs (null: local users), user indices, private-slot flags}: keeps the slot counts.
void XSessionMembers(uint8_t* base, uint32_t buffer, bool join) {
  const uint32_t count = Load32(GuestPtr(base, buffer + 4)), privates = Load32(GuestPtr(base, buffer + 16));
  std::lock_guard<std::mutex> lock(sessions_mutex);
  const uint32_t object = Load32(GuestPtr(base, buffer));
  auto it = hosted_sessions.find(object);
  const bool hosted = it != hosted_sessions.end();
  auto guest = guest_sessions.find(object);
  if (!hosted && guest == guest_sessions.end()) return;
  HostedSession& s = hosted ? it->second : guest->second;
  const auto xuids = Load32(GuestPtr(base, buffer + 8));
  for (uint32_t i = 0; i < count && i < 16; ++i) {
    const uint64_t xuid = xuids ? SessionId(GuestPtr(base, xuids + 8 * i)) : forza::IdentityXuid();
    if (xuid) {
      const auto member = std::find(s.members.begin(), s.members.end(), xuid);
      if (join && member == s.members.end()) s.members.push_back(xuid);
      if (!join && member != s.members.end()) s.members.erase(member);
    }
    const bool is_private = join && privates && Load32(GuestPtr(base, privates + 4 * i)) != 0;
    uint32_t& filled = is_private ? s.filled_private : s.filled_public;
    if (join) {
      ++filled;
    } else if (s.filled_public) {
      --s.filled_public;
    } else if (s.filled_private) {
      --s.filled_private;
    }
  }
  REXLOG_INFO("FORZA_LIVE: {} session {:016X}: {} {} player(s), {}/{} public slots filled",
              hosted ? "hosted" : "joined", SessionId(s.info), join ? "joined" : "left", count,
              s.filled_public, s.max_public);
  UpdateDiscordPartyLocked();
  if (!hosted) return;
  PublishPresenceLocked();
  PublishMatchLocked();
}

// XSessionMigrateHost (0xB001E) {session object, XSESSION_INFO*, user index, 3 reserved}:
// after the host left, the console that takes over calls it with a local user index and
// receives the session's new info (the session id stays, the host address becomes
// ours); the others call it with XUSER_INDEX_NONE and the info the new host sent them.
// The SDK answers success and leaves the info alone, so the new host still advertised
// the old one. We publish the migrated session as ours (matchmaking and presence), so
// players who search or join later find the new host.
void XSessionMigrateHost(uint8_t* base, uint32_t buffer) {
  const uint32_t object = Load32(GuestPtr(base, buffer)), info_ptr = Load32(GuestPtr(base, buffer + 4));
  const uint32_t user = Load32(GuestPtr(base, buffer + 8));
  if (!info_ptr) return;
  std::lock_guard<std::mutex> lock(sessions_mutex);
  auto guest = guest_sessions.find(object);
  if (user == 0xFF) {  // XUSER_INDEX_NONE: someone else is the new host
    if (guest != guest_sessions.end()) std::memcpy(guest->second.info, GuestPtr(base, info_ptr), kSessionInfoSize);
    std::memcpy(joined_session, GuestPtr(base, info_ptr), kSessionInfoSize);
    REXLOG_INFO("FORZA_LIVE: host migration: session {:016X} has a new host",
                SessionId(GuestPtr(base, info_ptr)));
    return;
  }
  HostedSession s{};
  if (guest != guest_sessions.end()) {
    s = guest->second;
    guest_sessions.erase(guest);
  } else if (hosted_sessions.count(object)) {
    s = hosted_sessions[object];
  } else {
    std::memcpy(s.info, joined_session, kSessionInfoSize);
    s.flags = 0x2D;  // host, stats, matchmaking, peer network: Forza's game sessions
    s.max_public = 8;
  }
  s.flags |= kSessionHost;
  forza::LiveSelfXnAddr(s.info + 8);
  std::memcpy(GuestPtr(base, info_ptr), s.info, kSessionInfoSize);
  s.order = ++session_order;
  hosted_sessions[object] = s;
  REXLOG_INFO("FORZA_LIVE: host migration: we host session {:016X} now (flags {:08X}, {}/{} "
              "public slots filled)", SessionId(s.info), s.flags, s.filled_public, s.max_public);
  if (!s.playlist) s.playlist = SearchedPlaylistLocked();
  PublishPresenceLocked();
  PublishMatchLocked();
  PublishStandbyLocked();
}

// XSessionSearchByIds {user, id count, ids, results size, results, ...} (0xB0060) and
// XSessionSearchByID {user, id (8 bytes), results size, results} (0xB001B): the sessions
// the room's players published. Results: XSESSION_SEARCHRESULT_HEADER {count, pointer}
// followed by 0x5C-byte XSESSION_SEARCHRESULTs (info, open public, open private,
// filled public, filled private, property count, context count, pointers).
uint32_t XSessionSearch(uint8_t* base, const std::vector<uint64_t>& ids, uint32_t results,
                        uint32_t results_size) {
  constexpr uint32_t kResultSize = 0x5C;
  if (!results || results_size < 8) return kInvalidArg;
  std::memset(GuestPtr(base, results), 0, results_size);
  Store32(GuestPtr(base, results + 4), results + 8);
  const auto roster = forza::LiveRoster();
  uint32_t found = 0;
  for (uint64_t id : ids) {
    for (const auto& f : roster) {
      if (!id || SessionId(f.session) != id) continue;
      if (8 + (found + 1) * kResultSize > results_size) break;
      uint8_t* r = GuestPtr(base, results + 8 + found * kResultSize);
      std::memcpy(r, f.session, kSessionInfoSize);
      if (f.extra.size() >= 4) {
        const uint32_t max_public = f.extra[0], max_private = f.extra[1];
        const uint32_t filled_public = f.extra[2], filled_private = f.extra[3];
        Store32(r + 0x3C, max_public > filled_public ? max_public - filled_public : 0);
        Store32(r + 0x40, max_private > filled_private ? max_private - filled_private : 0);
        Store32(r + 0x44, filled_public);
        Store32(r + 0x48, filled_private);
      }
      ++found;
      break;
    }
  }
  Store32(GuestPtr(base, results), found);
  if (Trace()) REXLOG_INFO("FORZA_LIVE: session search: {} of {} found", found, ids.size());
  return kSuccess;
}

uint32_t XSessionSearchByIds(uint8_t* base, uint32_t buffer) {
  const uint32_t count = std::min<uint32_t>(Load32(GuestPtr(base, buffer + 4)), 100);
  const uint32_t ids_ptr = Load32(GuestPtr(base, buffer + 8));
  std::vector<uint64_t> ids;
  for (uint32_t i = 0; i < count && ids_ptr; ++i) {
    ids.push_back(uint64_t(Load32(GuestPtr(base, ids_ptr + 8 * i))) << 32 | Load32(GuestPtr(base, ids_ptr + 8 * i + 4)));
  }
  return XSessionSearch(base, ids, Load32(GuestPtr(base, buffer + 16)), Load32(GuestPtr(base, buffer + 12)));
}

uint32_t XSessionSearchById(uint8_t* base, uint32_t buffer) {
  const uint64_t id = uint64_t(Load32(GuestPtr(base, buffer + 4))) << 32 | Load32(GuestPtr(base, buffer + 8));
  return XSessionSearch(base, {id}, Load32(GuestPtr(base, buffer + 16)), Load32(GuestPtr(base, buffer + 12)));
}

}  // namespace

// The ids of the sessions we host or joined (the "session" chat channel, live_friends.cpp).
std::vector<uint64_t> forza::LiveSessionIds() {
  std::vector<uint64_t> ids;
  std::lock_guard<std::mutex> lock(sessions_mutex);
  for (const auto* map : {&hosted_sessions, &guest_sessions}) {
    for (const auto& [object, s] : *map) {
      const uint64_t id = SessionId(s.info);
      if (id && std::find(ids.begin(), ids.end(), id) == ids.end()) ids.push_back(id);
    }
  }
  return ids;
}

std::optional<forza::LiveSessionView> forza::LiveCurrentSession() {
  std::lock_guard<std::mutex> lock(sessions_mutex);
  const HostedSession* best = nullptr;
  bool host = false;
  for (const auto* map : {&hosted_sessions, &guest_sessions}) {
    for (const auto& [object, s] : *map) {
      if (!social_presence::InteractiveSession(s.flags, SessionId(s.info)) ||
          (best && s.order <= best->order)) continue;
      best = &s;
      host = map == &hosted_sessions;
    }
  }
  if (!best) return std::nullopt;
  LiveSessionView view;
  view.id = SessionId(best->info);
  view.flags = best->flags;
  view.hosted = host;
  view.players = best->members.empty() ? best->filled_public + best->filled_private
                                      : uint32_t(best->members.size());
  view.capacity = best->max_public + best->max_private;
  std::copy_n(best->info, view.info.size(), view.info.begin());
  view.members = best->members;
  if(local_car_session==view.id)view.local_car = local_car_details;
  return view;
}
void forza::LiveSetCarDetails(const social_presence::Car& car) {
  std::lock_guard lock(sessions_mutex);
  const auto session=CurrentInteractiveSessionIdLocked();
  if (local_car_details == car && local_car_session==session) return;
  local_car_details = car;
  local_car_session = session;
  PublishPresenceLocked();
}

namespace {

// The session a friend we invite should join: the newest joinable session we host, else
// the one we joined. False when there is none.
bool InviteSession(uint8_t* out) {
  std::lock_guard<std::mutex> lock(sessions_mutex);
  const HostedSession* best = nullptr;
  for (const auto& [object, s] : hosted_sessions) {
    if ((s.flags & kSessionUsesPresence) && (!best || s.order > best->order)) best = &s;
  }
  if (best) {
    std::memcpy(out, best->info, kSessionInfoSize);
    return true;
  }
  std::memcpy(out, joined_session, kSessionInfoSize);
  for (size_t i = 0; i < 8; ++i) {
    if (out[i]) return true;
  }
  return false;
}

// XInviteSend (0xFC/0x50002): an XLIVEBASE_ASYNC_MESSAGE whose marshalled request is
// (big-endian) user index, invitee count, invitee XUIDs, display text, message handle.
// The game's friends list is every player in the room, so its invitees are not invited on
// their own (#90): the player picks them in the invite picker.
uint32_t XInviteSend(uint8_t* base, uint32_t message) {
  if (!message) return kInvalidArg;
  const uint32_t task = Load32(GuestPtr(base, message));
  if (!task) return kInvalidArg;
  const uint32_t request = Load32(GuestPtr(base, task + 0x18)), size = Load32(GuestPtr(base, task + 0x1C));
  if (!request || size < 8) return kInvalidArg;
  const uint32_t count = std::min<uint32_t>(Load32(GuestPtr(base, request + 4)), (size - 8) / 8);
  uint8_t session[kSessionInfoSize];
  if (!InviteSession(session)) return 0x80155206;  // X_ONLINE_E_SESSION_NOT_FOUND
  REXLOG_INFO("FORZA_LIVE: XInviteSend for {} players: the player picks whom to invite", count);
  forza::LiveOpenInvitePicker(session, "XInviteSend");
  return kSuccess;
}

// XInviteGetAcceptedInfo (0xFC/0x58023): (user index, X_INVITE_INFO*) in an argument
// list. X_INVITE_INFO: invitee XUID, inviter XUID, title id, the session (XSESSION_INFO),
// from-game-invite flag.
uint32_t XInviteGetAcceptedInfo(uint8_t* base, uint32_t args) {
  if (!args) return kInvalidArg;
  const uint32_t info = ArgPointer(base, args, 1);
  forza::LiveInvite invite;
  if (!info) return 0x80155206;  // session not found
  if (!forza::LiveTakeInvite(invite)) {
    // The game restarted itself to join (xam_loader.cpp): the invite it kept.
    if (!forza::LoaderTakeBootInvite(GuestPtr(base, info))) return 0x80155206;
    REXLOG_INFO("FORZA_LIVE: joining the game of the invite accepted before the restart");
    return kSuccess;
  }
  std::memset(GuestPtr(base, info), 0, 0x54);
  Store64(GuestPtr(base, info), forza::IdentityXuid());
  Store64(GuestPtr(base, info + 8), invite.inviter_xuid);
  Store32(GuestPtr(base, info + 0x10), kTitleId);
  std::memcpy(GuestPtr(base, info + 0x14), invite.session, kSessionInfoSize);
  Store32(GuestPtr(base, info + 0x50), 1);
  REXLOG_INFO("FORZA_LIVE: joining {}'s game", invite.inviter);
  return kSuccess;
}

// --- Matchmaking ---------------------------------------------------------------------
//
// Matchmaking (RecompiledHaloReach's docs/online_plan.md section 5.4) searches for
// sessions with XSessionSearchWeighted (Reach) or XSessionSearch (Forza), filtered by the
// playlist (the searcher's property 0x10000015, Reach's hopper id), and hosts one with XSessionCreate (flag
// XSESSION_CREATE_USES_MATCHMAKING) when it finds none. The session we host is published
// on the server with our playlist and the record below; a search asks the server for the
// room's sessions with the searched playlist. The record: XSESSION_INFO, slots (max
// public, max private, filled public, filled private: u8 each), our user properties
// (u8 count, then id u32, type u8, value 8 bytes) and contexts (u8 count, then id u32,
// value u32), the QoS data (u16 length + bytes).

constexpr uint32_t kSessionUsesMatchmaking = 0x8;
constexpr uint32_t kPropertyHopper = 0x10000015;       // Reach's hopper id
constexpr uint32_t kPropertyForzaHopper = 0x10000030;  // Forza's (Find Game's searches)

struct UserValue {
  uint8_t type = 0;
  uint8_t value[8] = {};
};
std::unordered_map<uint32_t, UserValue> user_properties;  // ours, by property id
std::unordered_map<uint32_t, uint32_t> user_contexts;     // by context id
std::unordered_map<uint32_t, uint32_t> local_activity;    // primary user, including offline
bool match_published = false;
bool standby_published = false;
bool standby_game_host = false;  // the game made us its host (forza::OnlineGameIsHost)
uint32_t match_playlist = 0;  // what we last searched for: the playlist our session is for
std::atomic<uint32_t> match_playlist_now{0};  // the same, for hooks (forza::LiveMatchPlaylist)

// A session a search found: its record, and the QoS data lookups answer with.
struct FoundSession {
  uint64_t xuid = 0;
  uint8_t info[kSessionInfoSize] = {};
  uint8_t slots[4] = {};
  std::vector<std::pair<uint32_t, UserValue>> properties;
  std::vector<std::pair<uint32_t, uint32_t>> contexts;
  std::vector<uint8_t> qos;
};
std::mutex match_mutex;
std::condition_variable match_answered;
uint32_t match_request = 0, match_answer = 0;
std::vector<FoundSession> match_results;                    // of the last answer
uint32_t match_partial_request = 0;     // an answer in several datagrams, so far
std::vector<FoundSession> match_partial;
std::unordered_map<uint64_t, std::vector<uint8_t>> found_qos;  // by session id

// XUserSetProperty {user index, unused, XUID, property id, size, value pointer}: the
// property's type is its id's top nibble; types with an inline value are kept.
void XUserSetProperty(uint8_t* base, uint32_t buffer) {
  const uint32_t id = Load32(GuestPtr(base, buffer + 16)), size = Load32(GuestPtr(base, buffer + 20));
  const uint32_t value = Load32(GuestPtr(base, buffer + 24));
  const uint8_t type = uint8_t(id >> 28);
  if (!value || type == 4 || type == 6 || size > 8) return;  // strings, binary
  UserValue v;
  v.type = type;
  std::memcpy(v.value, GuestPtr(base, value), size);
  if (Trace()) REXLOG_INFO("NETTRACE XUserSetProperty {:08X} = {:08X}", id, Load32(v.value));
  std::lock_guard<std::mutex> lock(sessions_mutex);
  user_properties[id] = v;
  // In a custom game every console sets the game type (the search key of Find Custom
  // Game) whenever the host changes the setup; Find Game's rounds leave it alone (#391).
  // A session we joined is advertised under the key it has now, not the one we found it by.
  const uint32_t key = Load32(v.value);
  if (id != kPropertyForzaHopper || key == 0xFFFFFFFF) return;  // -1: the party lobby's
  bool changed = false;
  for (auto& [object, g] : guest_sessions) {
    if ((g.flags & kSessionUsesMatchmaking) && g.playlist != key) {
      g.playlist = key;
      changed = true;
    }
  }
  if (changed && standby_published) PublishStandbyLocked();
}

// XUserSetContext {user index, unused, XUID, context id, value}
void XUserSetContext(uint8_t* base, uint32_t buffer) {
  if (Trace()) {
    REXLOG_INFO("NETTRACE XUserSetContext {:08X} = {:08X}", Load32(GuestPtr(base, buffer + 16)),
                Load32(GuestPtr(base, buffer + 20)));
  }
  std::lock_guard<std::mutex> lock(sessions_mutex);
  user_contexts[Load32(GuestPtr(base, buffer + 16))] = Load32(GuestPtr(base, buffer + 20));
}

// Our session's playlist: the property if we set it, else what we search for (Reach
// sets the hopper id only on its searches).
uint32_t PlaylistLocked() {
  auto it = user_properties.find(kPropertyForzaHopper);
  if (it == user_properties.end()) it = user_properties.find(kPropertyHopper);
  return it == user_properties.end() ? match_playlist : Load32(it->second.value);
}

// A session record (above) for MATCH_PUBLISH: op (1 hosted, 2 standby, 4 standby of the
// game's host), playlist, then the record advertising `info`.
std::vector<uint8_t> MatchRecord(uint8_t op, uint32_t playlist, const uint8_t* info,
                                 const HostedSession& s, const std::vector<uint8_t>& qos) {
  std::vector<uint8_t> body(5);
  body[0] = op;
  Store32(body.data() + 1, playlist);
  body.insert(body.end(), info, info + kSessionInfoSize);
  for (uint32_t n : {s.max_public, s.max_private, s.filled_public, s.filled_private}) {
    body.push_back(uint8_t(std::min<uint32_t>(n, 255)));
  }
  body.push_back(uint8_t(std::min<size_t>(user_properties.size(), 32)));
  size_t count = 0;
  for (const auto& [id, v] : user_properties) {
    if (count++ == 32) break;
    body.resize(body.size() + 13);
    uint8_t* p = body.data() + body.size() - 13;
    Store32(p, id);
    p[4] = v.type;
    std::memcpy(p + 5, v.value, 8);
  }
  body.push_back(uint8_t(std::min<size_t>(user_contexts.size(), 32)));
  count = 0;
  for (const auto& [id, value] : user_contexts) {
    if (count++ == 32) break;
    body.resize(body.size() + 8);
    Store32(body.data() + body.size() - 8, id);
    Store32(body.data() + body.size() - 4, value);
  }
  const size_t size = std::min<size_t>(qos.size(), 1000);
  body.push_back(uint8_t(size >> 8));
  body.push_back(uint8_t(size));
  body.insert(body.end(), qos.begin(), qos.begin() + size);
  return body;
}

// Publishes the newest matchmaking session we host, or withdraws ours. Needs
// sessions_mutex.
void PublishMatchLocked() {
  const HostedSession* best = nullptr;
  for (const auto& [object, s] : hosted_sessions) {
    if ((s.flags & kSessionUsesMatchmaking) && (!best || s.order > best->order)) best = &s;
  }
  if (!best) {
    if (match_published) forza::LiveSendMatch(false, {0});
    match_published = false;
    return;
  }
  auto it = qos_data.find(SessionId(best->info));
  const uint32_t playlist = best->playlist ? best->playlist : PlaylistLocked();
  forza::LiveSendMatch(false, MatchRecord(1, playlist, best->info, *best,
                                          it == qos_data.end() ? std::vector<uint8_t>() : it->second));
  match_published = true;
}

// Forza's game host can leave a free-roam world without the session changing hands at
// the XSession level: the game picks a new host among the others itself and calls no
// XSessionMigrateHost, so nobody advertised the world any more and later players
// opened new ones. A guest therefore publishes the matchmaking session it joined as a
// standby record: its info with our address as the host, its playlist and the QoS data
// we found it with. The server advertises it only while no player hosts that session
// id (forza_live_server.py), i.e. after the host left, so searchers join the players
// still in that world. Which of them the game made its new host is not visible at the
// XSession level either; forza::LiveMatchTick watches the game's own host flag, and the
// record then goes out as op 4 (standby of the game's host), which the server advertises
// before the others. Needs sessions_mutex.
void PublishStandbyLocked() {
  UpdateDiscordPartyLocked();
  const HostedSession* best = nullptr;
  for (const auto& [object, g] : guest_sessions) {
    if ((g.flags & kSessionUsesMatchmaking) && (!best || g.order > best->order)) best = &g;
  }
  if (!best) {
    if (standby_published) forza::LiveSendMatch(false, {3});
    standby_published = false;
    standby_game_host = false;
    return;
  }
  uint8_t info[kSessionInfoSize];
  std::memcpy(info, best->info, kSessionInfoSize);
  forza::LiveSelfXnAddr(info + 8);
  HostedSession slots = *best;
  slots.filled_public = 1;  // the server counts the session's remaining players
  slots.filled_private = 0;
  forza::LiveSendMatch(false, MatchRecord(standby_game_host ? 4 : 2, best->playlist, info, slots,
                                          best->qos));
  standby_published = true;
}

uint32_t SearchedPlaylistLocked() { return match_playlist; }

std::vector<uint8_t> FoundQos(uint64_t session_id) {
  std::lock_guard<std::mutex> lock(match_mutex);
  auto it = found_qos.find(session_id);
  return it == found_qos.end() ? std::vector<uint8_t>() : it->second;
}

// Parses one record of a search answer; false when it is cut short.
bool ParseRecord(const uint8_t* p, size_t n, FoundSession& s) {
  size_t pos = kSessionInfoSize + 4;
  if (n < pos + 1) return false;
  std::memcpy(s.info, p, kSessionInfoSize);
  std::memcpy(s.slots, p + kSessionInfoSize, 4);
  for (size_t i = 0, count = p[pos++]; i < count; ++i, pos += 13) {
    if (pos + 13 > n) return false;
    UserValue v;
    v.type = p[pos + 4];
    std::memcpy(v.value, p + pos + 5, 8);
    s.properties.emplace_back(Load32(p + pos), v);
  }
  if (pos + 1 > n) return false;
  for (size_t i = 0, count = p[pos++]; i < count; ++i, pos += 8) {
    if (pos + 8 > n) return false;
    s.contexts.emplace_back(Load32(p + pos), Load32(p + pos + 4));
  }
  if (pos + 2 > n) return false;
  const size_t qos = size_t(p[pos]) << 8 | p[pos + 1];
  if (pos + 2 + qos > n) return false;
  s.qos.assign(p + pos + 2, p + pos + 2 + qos);
  return true;
}

// A matchmaking search: the sessions the room's players host for the searched playlist.
// XUSER_PROPERTY is {id, pad, XUSER_DATA {type u8, pad, value at +8}} (0x18 bytes),
// XUSER_CONTEXT {id, value}. Results as XSessionSearch above, with each session's
// properties and contexts after them.
uint32_t SearchSessions(uint8_t* base, uint32_t procedure, uint32_t max_results,
                        uint32_t property_count, uint32_t properties, uint32_t results_size,
                        uint32_t results) {
  constexpr uint32_t kResultSize = 0x5C, kPropertySize = 0x18;
  if (!results || results_size < 8) return kInvalidArg;
  uint32_t playlist = 0;
  for (uint32_t i = 0; i < property_count && i < 64 && properties; ++i) {
    const uint8_t* prop = GuestPtr(base, properties + kPropertySize * i);
    if (Load32(prop) == kPropertyHopper || Load32(prop) == kPropertyForzaHopper) {
      playlist = Load32(prop + 0x10);
    }
  }
  // Find Game (procedure 4) searches for its playlist's hopper id in 0x10000030, Find
  // Custom Game (procedure 5) for the game type tab's value, which public custom games
  // set in the same property on their host (free roam: 8): either way the key matches.
  {
    std::lock_guard<std::mutex> lock(sessions_mutex);
    if (playlist != match_playlist) {
      match_playlist = playlist;
      match_playlist_now = playlist;
      PublishMatchLocked();
    }
  }
  // The request and the answer are single UDP datagrams to and from the server: either
  // can be lost, so the request goes out up to three times (0.4 s apart) before the game
  // gets no sessions; an answer cut short (a datagram of several lost) still counts.
  // Not signed in to the server: nobody to ask, no sessions at once.
  std::vector<FoundSession> found;
  {
    std::unique_lock<std::mutex> lock(match_mutex);
    const uint32_t request = ++match_request;
    std::vector<uint8_t> body(8);
    Store32(body.data(), playlist);
    Store32(body.data() + 4, request);
    for (int attempt = 0; attempt < 3; ++attempt) {
      if (!forza::LiveSendMatch(true, body)) break;
      if (match_answered.wait_for(lock, std::chrono::milliseconds(400),
                                  [&] { return match_answer == request; })) {
        found = match_results;
        break;
      }
    }
    if (match_answer != request && match_partial_request == request) found = match_partial;
  }
  std::memset(GuestPtr(base, results), 0, results_size);
  const uint32_t count = std::min<uint32_t>(
      {uint32_t(found.size()), max_results, (results_size - 8) / kResultSize});
  Store32(GuestPtr(base, results), count);
  Store32(GuestPtr(base, results + 4), results + 8);
  uint32_t extra = results + 8 + count * kResultSize;  // properties and contexts go here
  const uint32_t end = results + results_size;
  for (uint32_t i = 0; i < count; ++i) {
    const FoundSession& s = found[i];
    uint8_t* r = GuestPtr(base, results + 8 + i * kResultSize);
    std::memcpy(r, s.info, kSessionInfoSize);
    Store32(r + 0x3C, s.slots[0] > s.slots[2] ? s.slots[0] - s.slots[2] : 0);
    Store32(r + 0x40, s.slots[1] > s.slots[3] ? s.slots[1] - s.slots[3] : 0);
    Store32(r + 0x44, s.slots[2]);
    Store32(r + 0x48, s.slots[3]);
    const uint32_t props_size = uint32_t(s.properties.size()) * kPropertySize;
    const uint32_t contexts_size = uint32_t(s.contexts.size()) * 8;
    if (extra + props_size + contexts_size > end) continue;
    Store32(r + 0x4C, uint32_t(s.properties.size()));
    Store32(r + 0x50, uint32_t(s.contexts.size()));
    Store32(r + 0x54, extra);
    for (const auto& [id, v] : s.properties) {
      Store32(GuestPtr(base, extra), id);
      base[extra + 8] = v.type;
      std::memcpy(GuestPtr(base, extra + 0x10), v.value, 8);
      extra += kPropertySize;
    }
    Store32(r + 0x58, extra);
    for (const auto& [id, value] : s.contexts) {
      Store32(GuestPtr(base, extra), id);
      Store32(GuestPtr(base, extra + 4), value);
      extra += 8;
    }
  }
  REXLOG_INFO("FORZA_LIVE: matchmaking search (procedure {}, key {}): {} sessions", procedure,
              playlist, count);
  return kSuccess;
}

// XSessionSearchWeighted {procedure, user, max results, weighted property count u16,
// weighted context count u16, their pointers, property count u16, context count u16,
// properties, contexts, results size, results}.
uint32_t XSessionSearchWeighted(uint8_t* base, uint32_t buffer) {
  const uint8_t* b = GuestPtr(base, buffer);
  return SearchSessions(base, Load32(b), Load32(b + 8), Load32(b + 0x18) >> 16,
                        Load32(b + 0x1C), Load32(b + 0x24), Load32(b + 0x28));
}

// XSessionSearch (0xFB/0xB0016) {procedure, user, max results, property count u16,
// context count u16, properties, contexts, results size, results} and XSessionSearchEx
// (0xB001C, the same and a user count; Forza's hopper search): the SDK answers
// success with no results. Forza's searches carry the playlist (hopper id) in property
// 0x10000030, so a search finds the sessions hosted for the same playlist.
uint32_t XSessionSearchPlain(uint8_t* base, uint32_t buffer) {
  const uint8_t* b = GuestPtr(base, buffer);
  std::string query;  // the searched properties (id=value) and contexts (id:value)
  for (uint32_t i = 0, n = Load32(b + 12) >> 16; i < n && i < 16 && Load32(b + 16); ++i) {
    const uint8_t* prop = GuestPtr(base, Load32(b + 16) + 0x18 * i);
    query += fmt::format(" {:08X}={:X}", Load32(prop), Load32(prop + 0x10));
  }
  for (uint32_t i = 0, n = Load32(b + 12) & 0xFFFF; i < n && i < 16 && Load32(b + 20); ++i) {
    const uint8_t* ctx = GuestPtr(base, Load32(b + 20) + 8 * i);
    query += fmt::format(" {:08X}:{:X}", Load32(ctx), Load32(ctx + 4));
  }
  REXLOG_INFO("FORZA_LIVE: XSessionSearch procedure {} max {}:{}", Load32(b), Load32(b + 8),
              query);
  return SearchSessions(base, Load32(b), Load32(b + 8), Load32(b + 12) >> 16, Load32(b + 16),
                        Load32(b + 24), Load32(b + 28));
}

// --- Xbox LIVE stats (server/forza_live/competition/stats.py) ----------------------------------------
//
// Forza's SPA defines one stats view, the TrueSkill view 0xFFFF0000 (columns 61 skill, 62
// games played, 63 mu, 64 sigma). The game writes it at the end of an online game
// (XSessionWriteStats: each player's relative score and team) and reads it for its party
// (XUserReadStats). Both go to the Forza Live server, which rates the games and keeps a row
// per player; its leaderboards proper are title services (Scoreboard, forza_live/competition/scores.py).

struct StatsColumn {
  uint16_t id = 0;
  uint8_t type = 0;
  uint8_t value[8] = {};
};
struct StatsRow {
  uint64_t xuid = 0;
  uint32_t rank = 0;
  uint64_t rating = 0;
  std::string gamertag;
  std::vector<StatsColumn> columns;
};
struct StatsView {
  uint32_t id = 0, total = 0;
  std::vector<StatsRow> rows;
};
std::mutex stats_mutex;
std::condition_variable stats_answered;
uint32_t stats_request = 0, stats_answer = 0;
std::vector<StatsView> stats_views;  // of the last answer

uint64_t Load64(const uint8_t* p) { return uint64_t(Load32(p)) << 32 | Load32(p + 4); }
void Put32(std::vector<uint8_t>& out, uint32_t v) {
  for (int i = 3; i >= 0; --i) out.push_back(uint8_t(v >> (8 * i)));
}
void Put64(std::vector<uint8_t>& out, uint64_t v) {
  Put32(out, uint32_t(v >> 32));
  Put32(out, uint32_t(v));
}

// XSessionWriteStats {session object, pad, XUID u64, view count, views} (0xFB/0xB0025; the
// XDK's wrapper is sub_83164D60): XSESSION_VIEW_PROPERTIES {view id, property count,
// properties} (12 bytes), XUSER_PROPERTY {id, pad, XUSER_DATA {type u8, pad, value at +8}}
// (0x18 bytes). Sent to the server as it is; answered success.
uint32_t XSessionWriteStats(uint8_t* base, uint32_t buffer) {
  const uint8_t* b = GuestPtr(base, buffer);
  const uint32_t object = Load32(b), count = std::min<uint32_t>(Load32(b + 16), 16);
  const uint32_t views = Load32(b + 20);
  const uint64_t xuid = Load64(b + 8);
  uint64_t session = 0;
  {
    std::lock_guard<std::mutex> lock(sessions_mutex);
    if (auto it = hosted_sessions.find(object); it != hosted_sessions.end()) {
      session = SessionId(it->second.info);
    } else if (auto g = guest_sessions.find(object); g != guest_sessions.end()) {
      session = SessionId(g->second.info);
    }
  }
  std::vector<uint8_t> body{1};
  Put64(body, session);
  Put64(body, xuid);
  body.push_back(uint8_t(views ? count : 0));
  std::string text;
  for (uint32_t v = 0; v < count && views; ++v) {
    const uint8_t* view = GuestPtr(base, views + 12 * v);
    const uint32_t props = Load32(view + 8), n = props ? std::min<uint32_t>(Load32(view + 4), 64) : 0;
    Put32(body, Load32(view));
    body.push_back(uint8_t(n));
    text += fmt::format(" view {:08X}:", Load32(view));
    for (uint32_t i = 0; i < n; ++i) {
      const uint8_t* prop = GuestPtr(base, props + 0x18 * i);
      Put32(body, Load32(prop));
      body.push_back(prop[8]);
      body.insert(body.end(), prop + 0x10, prop + 0x18);
      text += fmt::format(" {:08X}={:X}", Load32(prop), Load32(prop + 0x10));
    }
  }
  forza::LiveSendStats(body);
  REXLOG_INFO("FORZA_LIVE: XSessionWriteStats session {:016X} XUID {:016X}:{}", session, xuid,
              text);
  return kSuccess;
}

// XUserReadStats {title id, XUID count, XUIDs, spec count, specs, results size, results}
// (0xFB/0xB0021): the rows of the asked players in the asked views, from the server
// (waiting up to 500 ms for its answer). A player the server has no row for, or every
// player when it does not answer, gets a new player's defaults: TrueSkill columns at their
// starting values, everything else null. (The SDK answered success with the results
// untouched, and Forza dereferenced the empty view list: the skill view, right after it
// created its party session.)
//
// XUSER_STATS_SPEC: u32 view id, u32 column count, u16 column ids[64] (136 bytes).
// Results: XUSER_STATS_READ_RESULTS {u32 view count, views*}; XUSER_STATS_VIEW {u32 view
// id, u32 total rows, u32 row count, rows*}; XUSER_STATS_ROW {u64 XUID, u32 rank, pad,
// i64 rating, char gamertag[16], u32 column count, columns*} (48 bytes);
// XUSER_STATS_COLUMN {u16 id, pad, XUSER_DATA {u8 type, pad, 8-byte value}} (24 bytes).
uint32_t XUserReadStats(uint8_t* base, uint32_t buffer) {
  const uint8_t* b = GuestPtr(base, buffer);
  const uint32_t xuid_count = Load32(b + 4), xuids = Load32(b + 8);
  const uint32_t spec_count = Load32(b + 12), specs = Load32(b + 16);
  const uint32_t results_size = Load32(b + 20), results = Load32(b + 24);
  if (!results || xuid_count > 100 || spec_count > 64) return kNotOurs;
  uint32_t needed = 8 + 16 * spec_count;
  for (uint32_t v = 0; v < spec_count; ++v) {
    const uint32_t columns = std::min<uint32_t>(Load32(GuestPtr(base, specs + 136 * v + 4)), 64);
    needed += xuid_count * (48 + 24 * columns);
  }
  if (needed > results_size) {
    REXLOG_WARN("FORZA_LIVE: XUserReadStats needs {} bytes, the game gave {}", needed, results_size);
    return kNotOurs;
  }
  // Ask the server: op 2, request, XUIDs, then each spec's view and column ids.
  std::vector<StatsView> answer;
  bool answered = false;
  {
    std::unique_lock<std::mutex> lock(stats_mutex);
    const uint32_t request = ++stats_request;
    std::vector<uint8_t> body{2};
    Put32(body, request);
    body.push_back(uint8_t(xuid_count));
    for (uint32_t r = 0; r < xuid_count; ++r) Put64(body, Load64(GuestPtr(base, xuids + 8 * r)));
    body.push_back(uint8_t(spec_count));
    for (uint32_t v = 0; v < spec_count; ++v) {
      const uint8_t* spec = GuestPtr(base, specs + 136 * v);
      const uint32_t columns = std::min<uint32_t>(Load32(spec + 4), 64);
      Put32(body, Load32(spec));
      body.push_back(uint8_t(columns));
      body.insert(body.end(), spec + 8, spec + 8 + 2 * columns);
    }
    forza::LiveSendStats(body);
    answered = stats_answered.wait_for(lock, std::chrono::milliseconds(500),
                                       [&] { return stats_answer == request; });
    if (answered) answer = stats_views;
  }
  constexpr uint8_t kTypeInt64 = 2, kTypeDouble = 3, kTypeNull = 0xFF;
  // X_STATS_COLUMN_SKILL_*: skill (1..50), games played, mu, sigma.
  constexpr uint16_t kSkill = 61, kGamesPlayed = 62, kMu = 63, kSigma = 64;
  std::unordered_map<uint64_t, std::string> tags;
  for (const auto& f : forza::LiveRoster()) tags[f.xuid] = f.gamertag;
  tags[forza::IdentityXuid()] = forza::IdentityGamertag();
  std::memset(GuestPtr(base, results), 0, needed);
  uint32_t view = results + 8, next = view + 16 * spec_count;
  Store32(GuestPtr(base, results), spec_count);
  Store32(GuestPtr(base, results + 4), spec_count ? view : 0);
  uint32_t from_server = 0;
  std::string text;
  for (uint32_t v = 0; v < spec_count; ++v, view += 16) {
    const uint8_t* spec = GuestPtr(base, specs + 136 * v);
    const uint32_t columns = std::min<uint32_t>(Load32(spec + 4), 64);
    const StatsView* known = nullptr;
    for (const auto& a : answer) {
      if (a.id == Load32(spec)) known = &a;
    }
    uint8_t* out = GuestPtr(base, view);
    Store32(out, Load32(spec));
    Store32(out + 4, known ? std::max<uint32_t>(known->total, xuid_count) : xuid_count);
    Store32(out + 8, xuid_count);
    Store32(out + 12, xuid_count ? next : 0);
    text += fmt::format(" {:08X}", Load32(spec));
    const uint32_t rows = next;
    next += 48 * xuid_count;
    for (uint32_t r = 0; r < xuid_count; ++r) {
      uint8_t* row = GuestPtr(base, rows + 48 * r);
      const uint64_t xuid = Load64(GuestPtr(base, xuids + 8 * r));
      const StatsRow* got = nullptr;
      for (size_t i = 0; known && i < known->rows.size(); ++i) {
        if (known->rows[i].xuid == xuid) got = &known->rows[i];
      }
      Store64(row, xuid);
      std::string tag = got && !got->gamertag.empty() ? got->gamertag : "";
      if (tag.empty()) {
        if (auto t = tags.find(xuid); t != tags.end()) tag = t->second;
      }
      forza::StoreGamertag(row + 24, 16, tag);
      if (got) {
        ++from_server;
        Store32(row + 8, got->rank);
        Store64(row + 16, got->rating);
        text += fmt::format(" ({} rank {} rating {})", tag, got->rank, got->rating);
      }
      Store32(row + 40, columns);
      Store32(row + 44, columns ? next : 0);
      for (uint32_t c = 0; c < columns; ++c, next += 24) {
        uint8_t* column = GuestPtr(base, next);
        const uint16_t id = uint16_t(spec[8 + 2 * c] << 8 | spec[9 + 2 * c]);
        column[0] = uint8_t(id >> 8);
        column[1] = uint8_t(id);
        const StatsColumn* col = nullptr;
        for (size_t i = 0; got && i < got->columns.size(); ++i) {
          if (got->columns[i].id == id) col = &got->columns[i];
        }
        if (col) {
          column[8] = col->type;
          std::memcpy(column + 16, col->value, 8);
          continue;
        }
        const auto set_double = [column](double d) {
          uint64_t bits;
          std::memcpy(&bits, &d, 8);
          column[8] = kTypeDouble;
          Store64(column + 16, bits);
        };
        switch (id) {
          case kSkill:
            column[8] = kTypeInt64;
            Store64(column + 16, 1);
            break;
          case kGamesPlayed:
            column[8] = kTypeInt64;
            break;
          case kMu:
            set_double(3.0);  // X_STATS_SKILL_MU_DEFAULT
            break;
          case kSigma:
            set_double(1.0);  // X_STATS_SKILL_SIGMA_DEFAULT
            break;
          default:
            column[8] = kTypeNull;
            break;
        }
      }
    }
  }
  REXLOG_INFO("FORZA_LIVE: XUserReadStats: {} view(s) x {} player(s){}: {} row(s) from the server{}",
              spec_count, xuid_count, text, from_server,
              answered ? "" : " (no answer: defaults)");
  return kSuccess;
}

// Our part of a message: kNotOurs lets the SDK answer (after any bookkeeping above).
uint32_t Handle(uint8_t* base, uint32_t app, uint32_t message, uint32_t arg1, uint32_t arg2) {
  // Observe the game's primary-user presence before the Live sign-in gate: Discord
  // status also works in offline play. Leave the original SDK request unchanged.
  if (app == 0xFB && message == 0x000B0006 && arg1 && Load32(GuestPtr(base, arg1)) == 0) {
    const auto id = Load32(GuestPtr(base, arg1 + 16));
    const auto value = Load32(GuestPtr(base, arg1 + 20));
    if (id == 0x8001 || id == 0x15) {
      std::lock_guard<std::mutex> lock(sessions_mutex);
      local_activity[id] = value;
    }
    forza::discord::Context(id, value);
  }
  // XamApp (0xFE) message 0x2B003: Xenia's XamApp answers success for this title ("Games
  // used in: 4D5309C9", a buffer of three u64s, unknown); the SDK fails it.
  if (app == 0xFE && message == 0x0002B003) return kSuccess;
  if (!forza::LiveSignin()) return kNotOurs;
  static std::once_flag presence_once;
  std::call_once(presence_once, SetIdlePresence);
  if (app == kAppXLiveBase) {
    switch (message) {
      case 0x0005000C:
        return XStringVerify(base, arg1);
      case 0x00058020:
        return XFriendsCreateEnumerator(base, arg2);
      case 0x00058019:
        return XPresenceCreateEnumerator(base, arg2);
      case 0x00050002:
        return XInviteSend(base, arg1);
      case 0x00058023:
        return XInviteGetAcceptedInfo(base, arg2);
      case 0x0005801E:  // XPresenceSubscribe: every room player's presence is known
      case 0x00058044:  // XPresenceUnsubscribe
      // XUserMuteListQuery {user index, XUID}: nobody is muted (the result stays false).
      // In a matchmade game Reach asks it for every other player several times a second;
      // the SDK fails it and logs each call.
      case 0x0005800E:
        return kSuccess;
    }
  } else if (app == kAppXgi && arg1) {
    switch (message) {
      case 0x000B0010:
        XSessionCreate(base, arg1);
        break;
      case 0x000B0011:
        XSessionDelete(base, arg1);
        break;
      case 0x000B0018:
        XSessionModify(base, arg1);
        break;
      case 0x000B001E:  // the SDK answers success; we fill the info first
        XSessionMigrateHost(base, arg1);
        break;
      case 0x000B0012:
        XSessionMembers(base, arg1, true);
        break;
      case 0x000B0013:  // XSessionLeave: the SDK has no handler and fails it
        XSessionMembers(base, arg1, false);
        return kSuccess;
      case 0x000B001B:
        return XSessionSearchById(base, arg1);
      case 0x000B0060:
        return XSessionSearchByIds(base, arg1);
      case 0x000B0006:
        XUserSetContext(base, arg1);
        break;
      case 0x000B0007:
        XUserSetProperty(base, arg1);
        break;
      case 0x000B0065:
        return XSessionSearchWeighted(base, arg1);
      case 0x000B0021:
        return XUserReadStats(base, arg1);
      case 0x000B0025:
        return XSessionWriteStats(base, arg1);
      case 0x000B0016:  // XSessionSearch
      case 0x000B001C:  // XSessionSearchEx: the same, then a user count
        return XSessionSearchPlain(base, arg1);
    }
  }
  return kNotOurs;
}

void LogMessage(const char* name, const uint32_t (&in)[6], uint32_t result, bool ours) {
  if (!Trace() || in[0] == 0xFA) return;  // the music player is noise
  REXLOG_INFO("NETTRACE {}(app {:02X}, msg {:08X}, {:08X}, {:08X}, {:08X}, {:08X}) -> {:08X}{}",
              name, in[0], in[1], in[2], in[3], in[4], in[5], result, ours ? " (FORZA_LIVE)" : "");
}

GuestFunc Sdk(const char* name) { return forza::SdkImport(name); }

}  // namespace

// DWORD XMsgInProcessCall(DWORD app, DWORD message, void* arg1, void* arg2)
extern "C" REX_FUNC(__imp__XMsgInProcessCall) {
  static GuestFunc sdk = Sdk("__imp__XMsgInProcessCall");
  const uint32_t in[6] = {ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, ctx.r8.u32};
  // XMPGetPlaybackController (music player app 0xFA, message 0x7001B; buffer: client,
  // controller*, locked*; the game's wrapper is sub_82A63FE8). The SDK answers "no
  // controller, not locked" after a 10 ms sleep on threads other than the main thread
  // (from Xenia, for games that poll it in a tight loop). RecompiledHaloReach's audio
  // thread asks once per frame and the sleep held it to 45 fps; Forza polls it in a loop
  // instead (about 46 calls/s with the sleep, 10,500/s without: one core spinning) and
  // free roam stays at 30 fps either way (docs/perf.md), so the SDK's answer stays.
  // FORZA_XMP_NOSLEEP=1 answers at once (for frame-rate experiments above 30 fps).
  static const bool xmp_nosleep = [] {
    const char* v = std::getenv("FORZA_XMP_NOSLEEP");
    return v && *v == '1';
  }();
  if (in[0] == 0xFA && in[1] == 0x7001B && in[2] && xmp_nosleep) {
    const uint32_t controller_ptr = Load32(GuestPtr(base, in[2] + 4));
    const uint32_t locked_ptr = Load32(GuestPtr(base, in[2] + 8));
    if (controller_ptr) Store32(GuestPtr(base, controller_ptr), 0);
    if (locked_ptr) Store32(GuestPtr(base, locked_ptr), 0);
    ctx.r3.u64 = 0;  // X_E_SUCCESS
    return;
  }
  const uint32_t result = Handle(base, in[0], in[1], in[2], in[3]);
  if (result != kNotOurs) {
    ctx.r3.u64 = result;
  } else if (sdk) {
    sdk(ctx, base);
  }
  LogMessage("XMsgInProcessCall", in, ctx.r3.u32, result != kNotOurs);
}

namespace {
// XMsgStartIORequest[Ex](app, message, XOVERLAPPED*, buffer, buffer size[, unknown]):
// like the SDK, a request is answered at once and its XOVERLAPPED completed with the
// result.
void StartIORequest(const char* name, GuestFunc sdk, PPCContext& ctx, uint8_t* base) {
  const uint32_t in[6] = {ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, ctx.r6.u32, ctx.r7.u32, ctx.r8.u32};
  const uint32_t overlapped = in[2];
  const uint32_t result = Handle(base, in[0], in[1], in[3], in[4]);
  if (result == kNotOurs) {
    if (sdk) sdk(ctx, base);
    LogMessage(name, in, ctx.r3.u32, false);
    return;
  }
  if (overlapped) {
    REX_KERNEL_STATE()->CompleteOverlappedImmediate(overlapped, result);
    ctx.r3.u64 = kIoPending;
  } else {
    ctx.r3.u64 = result;
  }
  rex::system::XThread::SetLastError(0);
  LogMessage(name, in, ctx.r3.u32, true);
}
}  // namespace

extern "C" REX_FUNC(__imp__XMsgStartIORequest) {
  static GuestFunc sdk = Sdk("__imp__XMsgStartIORequest");
  StartIORequest("XMsgStartIORequest", sdk, ctx, base);
}

extern "C" REX_FUNC(__imp__XMsgStartIORequestEx) {
  static GuestFunc sdk = Sdk("__imp__XMsgStartIORequestEx");
  StartIORequest("XMsgStartIORequestEx", sdk, ctx, base);
}

namespace {
// The Guide's friends list and game invite UI: there is no Guide. Forza's party lobby
// "Invite Friends" calls XamShowFriendsUI (the player picks friends in the Guide, which
// sends the invites): our invite picker stands in (live_invites.cpp), for the session a
// friend should join (InviteSession: the newest joinable session we host, else the one we
// joined). Up to v0.1.12 this invited every player in the room (#90).
uint32_t InvitePicker(const char* from) {
  uint8_t session[kSessionInfoSize];
  if (!forza::LiveSignin() || !InviteSession(session)) {
    REXLOG_INFO("FORZA_LIVE: {}: no session to invite friends to", from);
    return 0;
  }
  REXLOG_INFO("FORZA_LIVE: {}: inviting to session {:016X}", from, SessionId(session));
  forza::LiveOpenInvitePicker(session, from);
  return 0;  // ERROR_SUCCESS
}
}  // namespace

// DWORD XamShowFriendsUI(DWORD user_index)
extern "C" REX_FUNC(__imp__XamShowFriendsUI) {
  ctx.r3.u64 = InvitePicker("XamShowFriendsUI");
}

// DWORD XamShowGameInviteUI(DWORD user_index, const XUID* recipients, DWORD count, text)
extern "C" REX_FUNC(__imp__XamShowGameInviteUI) {
  ctx.r3.u64 = InvitePicker("XamShowGameInviteUI");
}

namespace forza {
bool LiveLocalActivity(uint32_t& presence, uint32_t& region) {
  std::lock_guard<std::mutex> lock(sessions_mutex);
  const auto p = local_activity.find(0x8001);
  const auto r = local_activity.find(0x15);
  if (p == local_activity.end() || r == local_activity.end()) return false;
  presence = p->second;
  region = r->second;
  return true;
}
// The playlist of our last matchmaking search (Find Game: the hopper id), 0 before one.
uint32_t LiveMatchPlaylist() { return match_playlist_now.load(std::memory_order_relaxed); }

// Every second (net.cpp): while we publish a standby record of a session someone else
// hosted, tell the server when the game makes us (or stops making us) its host.
void LiveMatchTick() {
  const int host = OnlineGameIsHost();
  std::lock_guard<std::mutex> lock(sessions_mutex);
  if (!standby_published || host < 0 || bool(host) == standby_game_host) return;
  standby_game_host = host;
  REXLOG_INFO("FORZA_LIVE: the game {} the host of the session we joined",
              host ? "made us" : "no longer has us as");
  PublishStandbyLocked();
}

// The server's answer to a matchmaking search: request u32, count u16, then per session
// the host's id u32, XUID u64 and record (u16 length + bytes). Bit 15 of the count: more
// datagrams of this answer follow (the sessions are gathered until the last one).
void LiveMatchResults(const uint8_t* p, size_t n) {
  if (n < 6) return;
  std::vector<FoundSession> found;
  size_t pos = 6;
  const bool more = p[4] & 0x80;
  for (uint32_t i = 0, count = uint32_t(p[4] & 0x7F) << 8 | p[5]; i < count; ++i) {
    if (pos + 14 > n) break;
    const size_t size = size_t(p[pos + 12]) << 8 | p[pos + 13];
    if (pos + 14 + size > n) break;
    FoundSession s;
    s.xuid = uint64_t(Load32(p + pos + 4)) << 32 | Load32(p + pos + 8);
    if (ParseRecord(p + pos + 14, size, s)) found.push_back(std::move(s));
    pos += 14 + size;
  }
  std::lock_guard<std::mutex> lock(match_mutex);
  const uint32_t request = Load32(p);
  if (request != match_request || match_answer == request) return;  // late, or answered
  if (match_partial_request != request) {
    match_partial_request = request;
    match_partial.clear();
  }
  for (auto& s : found) {  // a repeated request is answered again: keep each session once
    found_qos[SessionId(s.info)] = s.qos;
    bool known = false;
    for (const auto& k : match_partial) known |= SessionId(k.info) == SessionId(s.info);
    if (!known) match_partial.push_back(std::move(s));
  }
  if (more) return;
  match_results = std::move(match_partial);
  match_partial.clear();
  match_answer = request;
  match_answered.notify_all();
}

// The server's rows for a stats read (server/forza_live/competition/stats.py): request u32, view count u8,
// per view: id u32, total rows u32, row count u8, per row: XUID u64, rank u32, rating i64,
// gamertag (u8 length + bytes), column count u8, per column: id u16, type u8, value (8).
void LiveStatsRows(const uint8_t* p, size_t n) {
  if (n < 5) return;
  std::vector<StatsView> views;
  size_t pos = 5;
  for (uint32_t v = 0; v < p[4]; ++v) {
    if (pos + 9 > n) return;
    StatsView view;
    view.id = Load32(p + pos);
    view.total = Load32(p + pos + 4);
    const uint32_t rows = p[pos + 8];
    pos += 9;
    for (uint32_t r = 0; r < rows; ++r) {
      if (pos + 21 > n) return;
      StatsRow row;
      row.xuid = Load64(p + pos);
      row.rank = Load32(p + pos + 8);
      row.rating = Load64(p + pos + 12);
      const size_t tag = std::min<size_t>(p[pos + 20], 15);
      pos += 21;
      if (pos + tag + 1 > n) return;
      row.gamertag = forza::SanitizeGamertag(std::string_view(reinterpret_cast<const char*>(p + pos), tag));
      const uint32_t columns = p[pos + tag];
      pos += tag + 1;
      for (uint32_t c = 0; c < columns; ++c) {
        if (pos + 11 > n) return;
        StatsColumn col;
        col.id = uint16_t(p[pos] << 8 | p[pos + 1]);
        col.type = p[pos + 2];
        std::memcpy(col.value, p + pos + 3, 8);
        row.columns.push_back(col);
        pos += 11;
      }
      view.rows.push_back(std::move(row));
    }
    views.push_back(std::move(view));
  }
  std::lock_guard<std::mutex> lock(stats_mutex);
  stats_views = std::move(views);
  stats_answer = Load32(p);
  stats_answered.notify_all();
}

// Notifications for the game's friends code (XN_FRIENDS_PRESENCE_CHANGED,
// XN_FRIENDS_FRIEND_ADDED; the parameter is the user index).
void LiveRosterChanged(bool membership_changed) {
  if (!LiveSignin()) return;
  auto* kernel = REX_KERNEL_STATE();
  if (!kernel) return;
  if (membership_changed) kernel->BroadcastNotification(0x04000002, 0);
  kernel->BroadcastNotification(0x04000001, 0);
  if (Trace()) REXLOG_INFO("FORZA_LIVE: friends changed{}", membership_changed ? " (came/went)" : "");
}
}  // namespace forza

// DWORD XamUserAreUsersFriends(DWORD user_index, XUID* xuids, DWORD count,
//                              BOOL* are_friends, XOVERLAPPED* overlapped)
// Our friends on the server (with an older server: the players in our room).
extern "C" REX_FUNC(__imp__XamUserAreUsersFriends) {
  static GuestFunc sdk = Sdk("__imp__XamUserAreUsersFriends");
  if (!forza::LiveSignin()) {
    if (sdk) sdk(ctx, base);
    return;
  }
  const uint32_t xuids = ctx.r4.u32, count = ctx.r5.u32, result = ctx.r6.u32;
  const uint32_t overlapped = ctx.r7.u32;
  bool all = count > 0;
  for (uint32_t i = 0; i < count && xuids; ++i) {
    const uint64_t xuid = uint64_t(Load32(GuestPtr(base, xuids + 8 * i))) << 32 | Load32(GuestPtr(base, xuids + 8 * i + 4));
    all = all && forza::LiveIsFriend(xuid);
  }
  if (result) Store32(GuestPtr(base, result), all ? 1 : 0);
  if (overlapped) {
    REX_KERNEL_STATE()->CompleteOverlappedImmediate(overlapped, kSuccess);
    ctx.r3.u64 = kIoPending;
  } else {
    ctx.r3.u64 = kSuccess;
  }
}

// --- QoS ---------------------------------------------------------------------------

// INT XNetQosListen(const XNKID* id, const BYTE* data, UINT size, DWORD bits_per_sec,
//                   DWORD flags): a host publishes its game description (Reach: the
// same bitstream as the System Link reply) for players probing it. We keep the data
// per key id and send it with our presence.
extern "C" REX_FUNC(__imp__NetDll_XNetQosListen) {
  static GuestFunc sdk = Sdk("__imp__NetDll_XNetQosListen");
  if (!forza::NetworkOn()) {
    if (sdk) sdk(ctx, base);
    return;
  }
  constexpr uint32_t kSetData = 0x4, kDisable = 0x2, kRelease = 0x10;
  const uint32_t id_ptr = ctx.r4.u32, data = ctx.r5.u32, size = ctx.r6.u32, flags = ctx.r8.u32;
  if (forza::LiveSignin() && id_ptr) {
    const uint64_t id = uint64_t(Load32(GuestPtr(base, id_ptr))) << 32 | Load32(GuestPtr(base, id_ptr + 4));
    std::lock_guard<std::mutex> lock(sessions_mutex);
    if ((flags & kSetData) && data) {
      qos_data[id].assign(GuestPtr(base, data), GuestPtr(base, data) + std::min<uint32_t>(size, 1000));
    }
    if (flags & (kDisable | kRelease)) qos_data.erase(id);
    if (Trace()) {
      REXLOG_INFO("NETTRACE XNetQosListen {:016X}: flags {:X}, {} bytes", id, flags,
                  (flags & kSetData) && data ? size : 0);
    }
    PublishPresenceLocked();
    PublishMatchLocked();
    // A session we joined: the game made us its host and describes it now (Find Custom
    // Game's details: game type, track, class, time, the host's name). Its standby record
    // carries the newest description from then on, not the one we found it with (#391).
    bool described = false;
    for (auto& [object, g] : guest_sessions) {
      if (SessionId(g.info) == id && (flags & kSetData) && data) {
        g.qos.assign(GuestPtr(base, data), GuestPtr(base, data) + std::min<uint32_t>(size, 1000));
        described = true;
      }
    }
    if (described) PublishStandbyLocked();
  }
  ctx.r3.u64 = 0;
}

// INT XNetQosLookup(UINT cxna, const XNADDR* apxna[], const XNKID* apxnkid[],
//     const XNKEY* apxnkey[], UINT cina, const IN_ADDR aina[], const DWORD
//     adwServiceId[], UINT cProbes, DWORD dwBitsPerSec, DWORD dwFlags, HANDLE hEvent,
//     XNQOS** ppxnqos): probing hosts before joining. Answered at once from the room's
// roster: each Live host's published QoS data, a nominal round trip and bandwidth.
// XNetQosRelease (the SDK) frees the result with SystemHeapFree.
extern "C" REX_FUNC(__imp__NetDll_XNetQosLookup) {
  static GuestFunc sdk = Sdk("__imp__NetDll_XNetQosLookup");
  if (!forza::LiveSignin()) {
    if (sdk) sdk(ctx, base);
    return;
  }
  auto stack_arg = [&](int index) { return Load32(GuestPtr(base, ctx.r1.u32 + 0x54 + (index - 8) * 8)); };
  const uint32_t count = std::min<uint32_t>(ctx.r4.u32, 64), xnas = ctx.r5.u32;
  const uint32_t xnkids = ctx.r6.u32;
  const uint32_t probes = stack_arg(8), event = stack_arg(11), out = stack_arg(12);
  const auto roster = forza::LiveRoster();
  std::vector<std::vector<uint8_t>> data(count);
  std::vector<bool> known(count, false);
  for (uint32_t i = 0; i < count && xnas; ++i) {
    const uint32_t xna = Load32(GuestPtr(base, xnas + 4 * i));
    if (!xna) continue;
    const uint32_t ina = Load32(GuestPtr(base, xna));
    if ((ina & 0xFF000000) != 0xF0000000) continue;  // not a Forza Live player
    if (const uint32_t kid = xnkids ? Load32(GuestPtr(base, xnkids + 4 * i)) : 0) {
      std::lock_guard<std::mutex> lock(match_mutex);  // a matchmaking session we found
      auto it = found_qos.find(uint64_t(Load32(GuestPtr(base, kid))) << 32 | Load32(GuestPtr(base, kid + 4)));
      if (it != found_qos.end()) {
        known[i] = true;
        data[i] = it->second;
        continue;
      }
    }
    for (const auto& f : roster) {
      if (f.id != (ina & 0x00FFFFFF)) continue;
      known[i] = true;
      if (f.extra.size() >= 6) {
        const size_t size = std::min<size_t>(f.extra[4] << 8 | f.extra[5], f.extra.size() - 6);
        data[i].assign(f.extra.begin() + 6, f.extra.begin() + 6 + size);
      }
      break;
    }
  }
  constexpr uint32_t kInfoSize = 0x18;
  uint32_t total = 8 + kInfoSize * std::max<uint32_t>(count, 1);
  for (const auto& d : data) total += uint32_t(d.size());
  const uint32_t qos = REX_KERNEL_MEMORY()->SystemHeapAlloc(total);
  if (!qos) {
    ctx.r3.u64 = 0x8007000E;  // E_OUTOFMEMORY
    return;
  }
  std::memset(GuestPtr(base, qos), 0, total);
  Store32(GuestPtr(base, qos), count);  // cxnqos; cxnqosPending stays 0
  uint32_t data_ptr = qos + 8 + kInfoSize * std::max<uint32_t>(count, 1);
  for (uint32_t i = 0; i < count; ++i) {
    uint8_t* info = GuestPtr(base, qos + 8 + kInfoSize * i);
    // XNQOSINFO: flags, reserved, probes sent u16, probes received u16, data size u16,
    // data pointer, RTT min u16, RTT median u16, up and down bits per second.
    const uint16_t sent = uint16_t(std::max<uint32_t>(probes, 1));
    info[0] = known[i] ? uint8_t(0x01 | 0x02 | (data[i].empty() ? 0 : 0x08)) : 0x01;
    info[2] = uint8_t(sent >> 8);
    info[3] = uint8_t(sent);
    if (known[i]) {
      info[4] = uint8_t(sent >> 8);
      info[5] = uint8_t(sent);
      info[6] = uint8_t(data[i].size() >> 8);
      info[7] = uint8_t(data[i].size());
      if (!data[i].empty()) {
        Store32(info + 8, data_ptr);
        std::memcpy(GuestPtr(base, data_ptr), data[i].data(), data[i].size());
        data_ptr += uint32_t(data[i].size());
      }
      info[0xD] = 20;  // ms
      info[0xF] = 25;
      Store32(info + 0x10, 2000000);
      Store32(info + 0x14, 2000000);
    }
  }
  if (out) Store32(GuestPtr(base, out), qos);
  if (event) {
    if (auto ev = REX_KERNEL_OBJECTS()->LookupObject<rex::system::XEvent>(event)) ev->Set(0, false);
  }
  if (Trace()) {
    uint32_t answered = 0;
    for (bool k : known) answered += k;
    REXLOG_INFO("FORZA_LIVE: QoS lookup of {} hosts: {} answered", count, answered);
  }
  ctx.r3.u64 = 0;
}

// DWORD XNetLogonGetTitleID()
extern "C" REX_FUNC(__imp__XNetLogonGetTitleID) {
  ctx.r3.u64 = kTitleId;
}

// HRESULT XNetLogonGetMachineID(ULONGLONG* machine_id): Live machine ids are
// 0xFA000000xxxxxxxx; ours comes from the XUID.
extern "C" REX_FUNC(__imp__XNetLogonGetMachineID) {
  if (ctx.r3.u32) {
    Store64(GuestPtr(base, ctx.r3.u32), 0xFA00000000000000ull | (forza::IdentityXuid() & 0xFFFFFFFFull));
  }
  ctx.r3.u64 = 0;
}
