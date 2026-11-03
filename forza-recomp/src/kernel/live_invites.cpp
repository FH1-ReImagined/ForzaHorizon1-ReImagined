// forza - game invites with the player's say (#90): the invite picker and the accept prompt.
//
// There is no Xbox Guide. Games up to v0.1.12 answered the Guide's friends list
// (XamShowFriendsUI, the party lobby's "Invite Friends") by inviting every player in the
// server room, and accepted every invite they got at once, so one press pulled everybody
// online into that party (in free roam by restarting their game, #81). Now:
//
// - Sending: XamShowFriendsUI, XamShowGameInviteUI and XInviteSend (live_xmsg.cpp) open the
//   invite picker, a list over the game (platform/hud_overlay.h). The player searches
//   gamertags (typing on the keyboard), picks up to kMaxPicks players and sends; only those
//   get an invite. Recipients the game names itself (XInviteSend, XamShowGameInviteUI) are
//   not invited on their own. The picker lists sections: our friends online in the room
//   (the server's friends list, live_friends.cpp, #92), then everyone else online in our
//   room (LiveRoster, the server's list of up to 100; players we blocked are left out).
//   With an older server, which keeps no friends list, there is only the second section.
//   The server also drops a player's invites beyond 4 per 30 s and those from a player the
//   invitee blocked (server/forza_live_server.py, INVITE_LIMIT).
// - Receiving: an invite shows a prompt for kPromptMs; holding Back + A (keyboard: the keys
//   bound to Back and A, Tab + Space by default) or pressing Enter joins, Back + B or Escape
//   ignores (and hides that player's invites for kMuteMs), no answer ignores. Only a "join"
//   tells the game (XN_LIVE_INVITE_ACCEPTED); the game then takes the invite from
//   XInviteGetAcceptedInfo (LiveTakeInvite) and joins, restarting itself in free roam.
//   One prompt at a time; other invites meanwhile are logged and dropped.
//
// Input: both run on player 1's pad (LiveInvitePad, called by XamInputGetState in
// input_autopress.cpp) and on the keyboard (HudTakeKeys). While the picker is open the game
// gets no input; while a prompt shows it does not get Back and Start (Enter is Start with
// mnk_mode), nor A and B while Back is held. Buttons held when either closes stay hidden
// until released. With mnk_mode the keyboard also drives the pad (W/S the stick, Space A,
// Backspace B), so in the picker pad input counts only when it is released with no
// keyboard key down meanwhile (typing "sw" must not move the cursor).

#include "../platform/hud_overlay.h"
#include "identity.h"
#include "live.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/system/kernel_state.h>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

REXCVAR_DEFINE_BOOL(live_accept_invites, true, "Network/Forza Live",
                    "Show other players' game invites and let you join them (hold Back + A, or "
                    "Enter); false ignores every invite");

namespace forza {
namespace {

constexpr uint16_t kUp = 0x1, kDown = 0x2, kStart = 0x10, kBack = 0x20, kLB = 0x100,
                   kRB = 0x200, kA = 0x1000, kB = 0x2000;
constexpr int64_t kPromptMs = 20000;   // an unanswered invite is ignored after this
constexpr int64_t kHoldMs = 600;       // Back + A held this long joins
constexpr int64_t kMuteMs = 120000;    // an ignored player's invites are hidden this long
constexpr int64_t kAcceptedMs = 30000; // the game takes an accepted invite within this
constexpr int64_t kPickerIdleMs = 90000;
constexpr size_t kMaxPicks = 4;        // the server's INVITE_LIMIT
constexpr size_t kRows = 8;            // players shown at once
constexpr int kStickThreshold = 16000;

int64_t NowMs() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

std::string Lower(std::string s) {
  for (char& c : s) {
    if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
  }
  return s;
}

struct Candidate {
  uint64_t xuid = 0;
  std::string gamertag;
  std::string note;  // status shown after the name
};

struct Section {
  std::string title;
  std::vector<Candidate> players;
};

// The picker's first section: our friends (the server's friends list) online in our room.
// Empty with an older server (no list) or without friends online.
Section FriendSection() {
  Section s{"Friends", {}};
  if (!LiveFriendsActive()) return s;
  for (const auto& f : LiveRoster()) {
    if (!f.xuid || f.gamertag.empty() || !LiveIsFriend(f.xuid)) continue;
    s.players.push_back({f.xuid, f.gamertag, f.status});
  }
  std::sort(s.players.begin(), s.players.end(), [](const Candidate& a, const Candidate& b) {
    return Lower(a.gamertag) < Lower(b.gamertag);
  });
  return s;
}

// Everyone else online in our room, but players we blocked.
Section OnlineSection() {
  const bool friends = LiveFriendsActive();
  Section s{friends ? "Everyone online" : "Online", {}};
  for (const auto& f : LiveRoster()) {
    if (!f.xuid || f.gamertag.empty() || LiveIsBlocked(f.xuid)) continue;
    if (friends && LiveIsFriend(f.xuid)) continue;
    s.players.push_back({f.xuid, f.gamertag, f.status});
  }
  std::sort(s.players.begin(), s.players.end(), [](const Candidate& a, const Candidate& b) {
    return Lower(a.gamertag) < Lower(b.gamertag);
  });
  return s;
}

std::mutex mutex;
uint16_t latched = 0;  // buttons hidden from the game until released

// --- The accept prompt ---
bool prompt = false;
bool presence_join = false;
LiveInvite prompt_invite;
int64_t prompt_deadline = 0, both_since = 0, last_prompt_hud = 0;
std::unordered_map<uint64_t, int64_t> muted;  // inviter XUID -> until
LiveInvite accepted;
int64_t accepted_at = 0;

// --- The picker ---
struct Row {
  int section = -1;  // -1: a section title
  Candidate player;
};
bool picker = false;
std::vector<Section> sections;
std::string search;
std::vector<uint64_t> picked;
std::vector<Row> rows;  // the players matching the search, with their section titles
size_t cursor = 0;      // into the selectable rows (players, then "Send")
uint8_t picker_session[0x3C] = {};
int64_t picker_idle = 0, last_picker_hud = 0;
bool picker_dirty = true;
uint16_t picker_prev = 0;          // pad buttons at the last poll
uint16_t pad_down = 0;             // buttons pressed since they were last all released
unsigned pad_key_frames = 0;       // HudKeys::key_frames when they were pressed
int stick_dir = 0;                 // left stick: -1 up, 1 down, 0 centred
unsigned stick_key_frames = 0;
unsigned key_frames = 0;

void ShowPrompt(int64_t now, bool log = false) {
  last_prompt_hud = now;
  const int64_t left = (prompt_deadline - now + 999) / 1000;
  HudShow(presence_join ? "Join game" : "Game invite",
          (presence_join ? "Join " + prompt_invite.inviter + "'s game?\n" :
                           prompt_invite.inviter + " invited you to their game\n") +
              "Hold Back + A to join (keyboard: Tab + Space, or Enter)\n" +
              "Back + B or Escape: ignore   (" + std::to_string(left) + ")",
          1.5, log);
}

void ClosePrompt(uint16_t buttons, const char* why, const std::string& hud) {
  prompt = false;
  latched |= buttons & (kBack | kStart | kA | kB);
  REXLOG_INFO("FORZA_LIVE: {} from {}: {}", presence_join ? "join request" : "invite", prompt_invite.inviter, why);
  HudShow(presence_join ? "Join game" : "Game invite", hud, 3, false);
  HudWantKeys(false);
}

std::vector<size_t> Selectable() {
  std::vector<size_t> out;
  for (size_t i = 0; i < rows.size(); ++i) {
    if (rows[i].section >= 0) out.push_back(i);
  }
  return out;
}

void Filter() {
  const std::string needle = Lower(search);
  rows.clear();
  for (size_t s = 0; s < sections.size(); ++s) {
    bool titled = false;
    for (const auto& p : sections[s].players) {
      if (!needle.empty() && Lower(p.gamertag).find(needle) == std::string::npos) continue;
      if (!titled) rows.push_back({-1, {0, sections[s].title, ""}});
      titled = true;
      rows.push_back({int(s), p});
    }
  }
  cursor = std::min(cursor, Selectable().size());  // == size: on "Send"
  picker_dirty = true;
}

bool Picked(uint64_t xuid) { return std::find(picked.begin(), picked.end(), xuid) != picked.end(); }

void ShowPicker(int64_t now) {
  last_picker_hud = now;
  const bool changed = picker_dirty;  // logged when it changed
  picker_dirty = false;
  const auto selectable = Selectable();
  std::string body = "Search: " + search + "_\n";
  if (rows.empty()) {
    body += search.empty() ? "  (no other players online)\n" : "  (no gamertag matches)\n";
  }
  // The rows around the cursor.
  const size_t at = cursor < selectable.size() ? selectable[cursor] : rows.size();
  size_t first = at > kRows / 2 ? at - kRows / 2 : 0;
  if (first + kRows > rows.size()) first = rows.size() > kRows ? rows.size() - kRows : 0;
  if (first > 0) body += "  ...\n";
  for (size_t i = first; i < rows.size() && i < first + kRows; ++i) {
    const Row& r = rows[i];
    if (r.section < 0) {
      body += r.player.gamertag + "\n";
      continue;
    }
    std::string line = std::string(Picked(r.player.xuid) ? "[x] " : "[ ] ") + r.player.gamertag;
    if (!r.player.note.empty()) line += "   " + r.player.note.substr(0, 40);
    body += (i == at ? std::string(1, kHudHighlight) + "> " : std::string("  ")) + line + "\n";
  }
  if (first + kRows < rows.size()) body += "  ...\n";
  const std::string send = "Send invite" + std::string(picked.size() == 1 ? "" : "s") + " (" +
                           std::to_string(picked.size()) + " of up to " +
                           std::to_string(kMaxPicks) + ")";
  body += (at == rows.size() ? std::string(1, kHudHighlight) + "> " : std::string("  ")) + send +
          "\n";
  body += "Type to search   Up/Down: move   A or Enter: pick   Start: send   B or Escape: "
          "cancel";
  HudShow("Invite to your game", body, 2.5, changed);
}

void ClosePicker(uint16_t buttons, const std::string& hud) {
  picker = false;
  latched |= buttons;
  HudWantKeys(false);
  if (hud.empty()) {
    HudClear();
  } else {
    HudShow("Invite to your game", hud, 3, false);
  }
}

void Move(int delta) {
  const size_t n = Selectable().size() + 1;  // + "Send"
  cursor = (cursor + n + size_t(delta % int(n) + int(n))) % n;
  picker_dirty = true;
}

// LB / RB: the first player whose gamertag starts with the previous / next letter.
void Jump(int dir) {
  const auto selectable = Selectable();
  if (selectable.empty()) return;
  const size_t from = std::min(cursor, selectable.size() - 1);
  const char letter = Lower(rows[selectable[from]].player.gamertag).substr(0, 1)[0];
  for (size_t step = 1; step < selectable.size(); ++step) {
    const size_t i = (from + selectable.size() + (dir > 0 ? step : selectable.size() - step)) %
                     selectable.size();
    const std::string tag = Lower(rows[selectable[i]].player.gamertag);
    if (!tag.empty() && tag[0] != letter) {
      if (dir < 0) {  // the first of that letter
        size_t j = i;
        while (j > 0 && Lower(rows[selectable[j - 1]].player.gamertag)[0] == tag[0]) --j;
        cursor = j;
      } else {
        cursor = i;
      }
      picker_dirty = true;
      return;
    }
  }
}

// A / Enter: pick the player under the cursor, or send.
bool Activate() {
  const auto selectable = Selectable();
  if (cursor >= selectable.size()) return true;  // "Send"
  const Candidate& p = rows[selectable[cursor]].player;
  auto it = std::find(picked.begin(), picked.end(), p.xuid);
  if (it != picked.end()) {
    picked.erase(it);
  } else if (picked.size() < kMaxPicks) {
    picked.push_back(p.xuid);
  }
  picker_dirty = true;
  return false;
}

// Sends the picked invites (mutex held); returns the HUD line.
std::string SendPicked() {
  if (picked.empty()) return "No one invited";
  std::string names;
  for (uint64_t xuid : picked) {
    std::string tag;
    for (const auto& s : sections) {
      for (const auto& p : s.players) {
        if (p.xuid == xuid) tag = p.gamertag;
      }
    }
    LiveSendInvite(xuid, picker_session);
    REXLOG_INFO("FORZA_LIVE: invite sent to {} ({:016X})", tag, xuid);
    names += (names.empty() ? "" : ", ") + tag;
  }
  return "Invited " + names;
}

// The picker's pad and keyboard (mutex held).
void PickerInput(uint16_t buttons, int left_y, int64_t now) {
  HudKeys keys = HudTakeKeys();
  key_frames = keys.key_frames;
  bool send = false, cancel = false;
  if (!keys.typed.empty() || keys.backspace) {
    search += keys.typed;
    for (int i = 0; i < keys.backspace && !search.empty(); ++i) {
      // Drop one UTF-8 character.
      size_t end = search.size() - 1;
      while (end > 0 && (uint8_t(search[end]) & 0xC0) == 0x80) --end;
      search.erase(end);
    }
    if (search.size() > 15 * 3) search.resize(15 * 3);
    cursor = 0;
    Filter();
  }
  if (keys.up) Move(-1);
  if (keys.down) Move(1);
  if (keys.enter) send = Activate();
  if (keys.escape) cancel = true;
  if (keys.typed.size() || keys.backspace || keys.up || keys.down || keys.enter || keys.escape) {
    picker_idle = now + kPickerIdleMs;
  }

  // The pad: a button counts when released with no keyboard key down since it was pressed.
  const uint16_t pressed = buttons & ~picker_prev;
  if (pressed && !pad_down) pad_key_frames = key_frames;
  pad_down |= pressed;
  picker_prev = buttons;
  if (!buttons && pad_down) {
    const uint16_t done = pad_down;
    pad_down = 0;
    if (key_frames == pad_key_frames) {
      picker_idle = now + kPickerIdleMs;
      if (done & kUp) Move(-1);
      if (done & kDown) Move(1);
      if (done & kLB) Jump(-1);
      if (done & kRB) Jump(1);
      if (done & kA) send |= Activate();
      if (done & kStart) send = true;
      if (done & kB) cancel = true;
    }
  }
  const int dir = left_y > kStickThreshold ? -1 : left_y < -kStickThreshold ? 1 : 0;
  if (dir && !stick_dir) stick_key_frames = key_frames;
  if (!dir && stick_dir && key_frames == stick_key_frames) {
    Move(stick_dir);
    picker_idle = now + kPickerIdleMs;
  }
  stick_dir = dir;

  if (send) {
    const std::string hud = SendPicked();
    ClosePicker(buttons, hud);
  } else if (cancel) {
    REXLOG_INFO("FORZA_LIVE: invite picker closed, no one invited");
    ClosePicker(buttons, "");
  } else if (now >= picker_idle) {
    REXLOG_INFO("FORZA_LIVE: invite picker closed (no input)");
    ClosePicker(buttons, "");
  } else if (picker_dirty || now - last_picker_hud >= 500) {
    ShowPicker(now);
  }
}

}  // namespace

void LiveOpenInvitePicker(const uint8_t* session_info, const char* from) {
  std::vector<Section> found;
  if (Section f = FriendSection(); !f.players.empty()) found.push_back(std::move(f));
  found.push_back(OnlineSection());
  if (LiveFriendsScreenOpen()) {
    REXLOG_INFO("FORZA_LIVE: {}: the friends screen is open", from);
    return;
  }
  std::lock_guard<std::mutex> lock(mutex);
  if (picker) return;
  if (prompt) {
    REXLOG_INFO("FORZA_LIVE: {}: answer the invite first", from);
    return;
  }
  sections = std::move(found);
  size_t players = 0;
  for (const auto& s : sections) players += s.players.size();
  REXLOG_INFO("FORZA_LIVE: {}: invite picker opened ({} players online)", from, players);
  std::memcpy(picker_session, session_info, sizeof(picker_session));
  picker = true;
  search.clear();
  picked.clear();
  cursor = 0;
  picker_prev = 0xFFFF;  // what is held now (the press that opened it) is not a press
  pad_down = 0;
  stick_dir = 0;
  picker_idle = NowMs() + kPickerIdleMs;
  Filter();
  HudWantKeys(true);
  HudTakeKeys();
  ShowPicker(NowMs());
}

// The network thread: an invite arrived (net.cpp).
void LiveInviteReceived(const LiveInvite& invite) {
  if (!LiveSignin() || !REXCVAR_GET(live_accept_invites)) {
    REXLOG_INFO("FORZA_LIVE: {} invited you (ignored: live_accept_invites is off)", invite.inviter);
    return;
  }
  const int64_t now = NowMs();
  std::lock_guard<std::mutex> lock(mutex);
  if (auto it = muted.find(invite.inviter_xuid); it != muted.end() && now < it->second) {
    REXLOG_INFO("FORZA_LIVE: {} invited you (ignored: you ignored their last invite)", invite.inviter);
    return;
  }
  if (picker || LiveFriendsScreenOpen() ||
      (prompt && prompt_invite.inviter_xuid != invite.inviter_xuid)) {
    REXLOG_INFO("FORZA_LIVE: {} invited you (ignored: {})", invite.inviter,
                picker                   ? "the invite picker is open"
                : LiveFriendsScreenOpen() ? "the friends screen is open"
                                          : "another invite is waiting");
    return;
  }
  REXLOG_INFO("FORZA_LIVE: {} invited you; asking", invite.inviter);
  const bool fresh = !prompt;
  presence_join = false;
  prompt = true;
  prompt_invite = invite;
  prompt_deadline = now + kPromptMs;
  if (fresh) {
    both_since = 0;
    HudWantKeys(true);
    HudTakeKeys();
  }
  ShowPrompt(now, true);
}

static std::optional<LiveFriend> JoinTarget(uint64_t xuid) {
  if (!LiveConnected() || !xuid || xuid == IdentityXuid() || LiveIsBlocked(xuid)) return {};
  const bool is_friend = LiveIsFriend(xuid);
  const auto ids = LiveSessionIds();
  for (const auto& player : LiveRoster()) {
    if (player.xuid != xuid) continue;
    uint64_t id = 0;
    for (unsigned i = 0; i < 8; ++i) id = (id << 8) | player.session[i];
    if (!social_presence::Joinable(player.state,id,is_friend,false,
          std::find(ids.begin(),ids.end(),id)!=ids.end())) return {};
    if(player.extra.size()>=4){
      const unsigned capacity=unsigned(player.extra[0])+player.extra[1];
      if(capacity&&unsigned(player.extra[2])+player.extra[3]>=capacity)return {};
    }
    return player;
  }
  return {};
}

bool LiveRequestJoin(uint64_t xuid) {
  const auto player = JoinTarget(xuid);
  if (!player) return false;
  std::lock_guard lock(mutex);
  if (picker || prompt) return false;
  prompt_invite = {};
  prompt_invite.inviter_xuid = xuid;
  prompt_invite.inviter = player->gamertag;
  std::copy_n(player->session, sizeof(prompt_invite.session), prompt_invite.session);
  presence_join = prompt = true;
  both_since = 0;
  prompt_deadline = NowMs() + kPromptMs;
  HudWantKeys(true);
  HudTakeKeys();
  ShowPrompt(NowMs(), true);
  REXLOG_INFO("FORZA_LIVE: explicit join request for {}", player->gamertag);
  return true;
}

bool LiveInviteBusy() {
  std::lock_guard<std::mutex> lock(mutex);
  return picker || prompt;
}

bool LiveTakeInvite(LiveInvite& out) {
  std::lock_guard<std::mutex> lock(mutex);
  if (!accepted.inviter_xuid || NowMs() - accepted_at > kAcceptedMs) return false;
  out = accepted;
  accepted = {};
  return true;
}

// Player 1's pad, every XamInputGetState (input_autopress.cpp): returns the buttons to hide
// from the game; `block_all`: hide everything (sticks and triggers too).
uint16_t LiveInvitePad(uint16_t buttons, int left_y, bool& block_all) {
  block_all = false;
  bool notify = false;
  uint16_t hide;
  uint64_t joining = 0;
  { std::lock_guard lock(mutex); if (prompt && presence_join) joining = prompt_invite.inviter_xuid; }
  // No invite lock while consulting friends/roster; preserve the lock order.
  const auto target = joining ? JoinTarget(joining) : std::optional<LiveFriend>{};
  {
    std::lock_guard<std::mutex> lock(mutex);
    latched &= buttons;
    const int64_t now = NowMs();
    if (picker) {
      PickerInput(buttons, left_y, now);
      block_all = true;
      return 0xFFFF;
    }
    if (prompt && presence_join && joining == prompt_invite.inviter_xuid) {
      if (!target || std::memcmp(target->session, prompt_invite.session, 8))
        ClosePrompt(buttons, "no longer joinable", "That game is no longer joinable");
      else std::copy_n(target->session, sizeof(prompt_invite.session), prompt_invite.session);
    }
    if (prompt) {
      const HudKeys keys = HudTakeKeys();
      const bool both = (buttons & (kBack | kA)) == (kBack | kA);
      if (!both) both_since = 0;
      if (both && !both_since) both_since = now;
      if (keys.enter || (both_since && now - both_since >= kHoldMs)) {
        accepted = prompt_invite;
        accepted_at = now;
        notify = true;
        ClosePrompt(buttons, "joining", "Joining " + prompt_invite.inviter + "'s game...");
      } else if (keys.escape || (buttons & (kBack | kB)) == (kBack | kB)) {
        if (!presence_join) muted[prompt_invite.inviter_xuid] = now + kMuteMs;
        ClosePrompt(buttons, "ignored", presence_join ? "Join cancelled" : "Invite from " + prompt_invite.inviter + " ignored");
      } else if (now >= prompt_deadline) {
        ClosePrompt(buttons, "ignored (no answer)",
                    presence_join ? "Join cancelled" : "Invite from " + prompt_invite.inviter + " ignored");
      } else if (now - last_prompt_hud >= 250) {
        ShowPrompt(now);
      }
    }
    hide = latched;
    if (prompt) hide |= kBack | kStart | ((buttons & kBack) ? (kA | kB) : 0);
  }
  // XN_LIVE_INVITE_ACCEPTED (parameter: the user index): the game asks
  // XInviteGetAcceptedInfo for it and joins, or tells the player it can't right now.
  if (notify) {
    if (auto* kernel = REX_KERNEL_STATE()) kernel->BroadcastNotification(0x02000002, 0);
  }
  return hide;
}

}  // namespace forza
