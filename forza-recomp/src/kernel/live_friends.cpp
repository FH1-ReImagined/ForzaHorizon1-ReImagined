// forza - friends (#92): the friends list the Forza Live server keeps for us
// (server/forza_live/social/friends.py), the friends screen over the game, and its notifications.
//
// Games up to v0.1.14 counted every player in the server room as a friend. Now the server
// keeps each player's friends (requests by gamertag, accept or decline, remove, block) and
// this game, which says so in its HELLO (features 0x02 and its friends key), asks for its
// list every 10 s (every 3 s while the screen shows; the server also sends it after every
// change). With a list:
//
// - The game's friends list (XFriendsCreateEnumerator, XamUserAreUsersFriends;
//   live_xmsg.cpp) has only our friends: those in our room with their presence from the
//   roster (joinable sessions), friends online elsewhere without one, offline friends last.
// - The invite picker (live_invites.cpp) lists the friends online first, then everyone else
//   online; blocked players are not listed, and the server drops their invites to us.
// - The friends screen: F8 (live_friends_key) or holding the left stick press (unused by the
//   game; F on the keyboard with mnk_mode) opens it, B or Escape closes it. Tabs (LB/RB or
//   Left/Right): Friends, Requests, Add friend, Blocked. A or Enter on a player opens what
//   can be done (accept, decline, remove, block, unblock, take back a request). On Add
//   friend, typing searches the gamertags (players online here, and players the server
//   knows); with a controller, Y or A on the search line opens the on-screen keyboard
//   (live_osk.h). The game gets no input while it shows.
// - A friend request or an accepted one shows a short notification.
// - Text chat (server/forza_live/social/chat.py), the screen's Chat tab (F9, live_chat_key, opens it
//   there): Up/Down picks where a message goes (Session: the players in a session we host or
//   joined, World: our Horizon World, Club: the members of our car club who are online (only
//   when the server says we are in one: CLUBTAG), or a friend online), typing writes it and
//   Enter sends; lines the server sends back (a refusal, the answer to /club) show in the log;
//   with a controller A or Y opens the on-screen keyboard and Start sends. A message that
//   arrives while the tab is not shown pops up for a few seconds. The server relays and
//   rate limits; mute (a player's menu) and blocks hide a player's messages.
//
// Before the server sends a list (an older server), everyone in the room stays a friend.

#include "../platform/hud_overlay.h"
#include "gamertag_text.h"
#include "identity.h"
#include "live.h"
#include "live_osk.h"

#include <rex/cvar.h>
#include <rex/logging.h>
#include <rex/ui/keybinds.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <deque>
#include <mutex>
#include <string>
#include <unordered_set>
#include <vector>
#include <utility>

REXCVAR_DEFINE_STRING(live_friends_key, "F8", "Network/Forza Live",
                      "Key that opens the friends screen (a controller: hold the left stick "
                      "press)");

REXCVAR_DEFINE_BOOL(live_friends_pad_shortcut, true, "Network/Forza Live",
                    "Holding the left stick press opens the friends screen; off leaves the left "
                    "stick to the game (the keys still work, #194)");

REXCVAR_DEFINE_STRING(live_chat_key, "F9", "Network/Forza Live",
                      "Key that opens the chat (a controller: the friends screen's Chat tab)");

namespace forza {
namespace {

constexpr uint8_t kList = 1, kSearch = 2, kRequest = 3, kAccept = 4, kDecline = 5, kRemove = 6,
                  kBlock = 7, kUnblock = 8;
constexpr uint8_t kRList = 16, kRSame = 17, kRResults = 18, kRNotice = 19;
constexpr uint32_t kOnline = 0x1, kPlaying = 0x2;
constexpr int64_t kHoldMs = 500;        // the left stick press held this long opens the screen
constexpr int64_t kIdleMs = 120000;     // the screen closes after this without input
constexpr int64_t kNoticeMs = 6000;     // a notification shows this long
constexpr int64_t kStatusMs = 5000;     // a server message on the screen
constexpr int64_t kSearchDelayMs = 300; // typing pauses this long before a server search
constexpr size_t kRows = 8;
constexpr size_t kMaxSearch = 15 * 3;
// Chat (server/forza_live/social/chat.py).
constexpr uint8_t kChatSessions = 1, kChatSay = 2, kChatMute = 3, kChatClub = 4, kChatContent = 5;
constexpr uint8_t kChatRMessage = 16, kChatRNotice = 17, kChatRMuted = 18, kChatRClubTag = 19;
// Where a message goes (the server's channel numbers); 0 in the log: a line from the server.
constexpr uint8_t kToSession = 1, kToWorld = 2, kToDirect = 3, kToClub = 4;
constexpr int64_t kClubSyncMs = 10000;  // the club tag is asked for this often
constexpr size_t kMaxMessage = 200;
constexpr size_t kChatLog = 50;
constexpr size_t kChatRowsShown = 7;
constexpr int64_t kChatToastMs = 6000;

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

uint16_t Load16(const uint8_t* p) { return uint16_t(p[0] << 8 | p[1]); }
uint32_t Load32(const uint8_t* p) { return uint32_t(p[0]) << 24 | p[1] << 16 | p[2] << 8 | p[3]; }
uint64_t Load64(const uint8_t* p) { return uint64_t(Load32(p)) << 32 | Load32(p + 4); }

void Put64(std::vector<uint8_t>& b, uint64_t v) {
  for (int i = 7; i >= 0; --i) b.push_back(uint8_t(v >> (8 * i)));
}
void PutText(std::vector<uint8_t>& b, const std::string& s) {
  const size_t n = std::min<size_t>(s.size(), 255);
  b.push_back(uint8_t(n));
  b.insert(b.end(), s.begin(), s.begin() + n);
}
// A u8-length string at `pos`; false when it does not fit.
bool ReadText(const uint8_t* p, size_t n, size_t& pos, std::string& out) {
  if (pos >= n || pos + 1 + p[pos] > n) return false;
  out.assign(reinterpret_cast<const char*>(p + pos + 1), p[pos]);
  pos += 1 + p[pos];
  return true;
}

void SendOp(uint8_t op, uint64_t xuid, const std::string& name = {}, bool with_name = false) {
  std::vector<uint8_t> body{op};
  Put64(body, xuid);
  if (with_name) PutText(body, name);
  LiveSendFriend(body);
}

struct Result {
  uint64_t xuid = 0;
  std::string gamertag;
  bool online = false;
};

std::mutex mutex;
// --- the list (network thread writes) ---
bool active = false;
uint32_t version = 0;
std::vector<LiveRelation> relations, parts;
uint16_t parts_seen = 0;
uint32_t parts_version = 0;
std::atomic<int64_t> next_list{0};
// --- search results ---
uint16_t search_request = 0;
std::vector<Result> results;
// --- notifications ---
std::deque<std::string> notices;
int64_t notice_until = 0;
// --- chat ---
struct ChatLine {
  uint8_t channel = 0;  // 1 session, 2 world, 3 direct, 4 club, 0 a line from the server
  std::string from, to, text;  // `to`: a direct message we sent
  bool mine = false;
};
std::deque<ChatLine> chat_log;
size_t unread = 0;
bool chat_toast = false;  // a message to pop up
std::unordered_set<uint64_t> muted;
std::vector<uint64_t> sessions_sent;
int64_t next_sessions = 0;
std::string club_tag;  // the car club the server says we are in (CLUBTAG), empty: none
int64_t next_club = 0;
int64_t next_content = 0;  // the content manifest (op 5, #567): at sign-in and every 30 s

// --- the screen (game thread) ---
enum Tab { kFriends, kRequests, kAdd, kBlocked, kChat, kTabs };
enum Action { kDoAccept, kDoDecline, kDoRemove, kDoBlock, kDoUnblock, kDoRequest, kDoCancel,
              kDoMute, kDoUnmute, kDoMessage, kDoBack, kDoJoin };
struct Row {
  int kind = 1;  // 0 the search line, 1 a player, 2 "send a request to <search>"
  uint64_t xuid = 0;
  std::string gamertag, note;
  LiveRelationKind rel{};
  bool has_rel = false;
};
std::atomic<bool> screen{false};
std::atomic<bool> toggle_request{false}, chat_request{false};
std::atomic<int> page_request{-1};
uint64_t join_request = 0;  // consumed after releasing the friends mutex
Tab tab = kFriends;
// The Chat tab: where a message goes (kTo...; a friend online for kToDirect), the text.
uint8_t chat_to = kToSession;
uint64_t chat_friend = 0;
std::string chat_friend_name;
std::string chat_text;
size_t cursor[kTabs] = {};
std::string search;
int64_t search_changed = 0;
bool search_sent = true;
Osk osk;
bool menu = false;
size_t menu_cursor = 0;
Row menu_row;
std::vector<std::pair<std::string, Action>> menu_items;
std::string status;
int64_t status_until = 0, idle_until = 0, last_hud = 0;
bool dirty = true;
PadReader reader;
uint16_t latched = 0;  // buttons hidden from the game until released
int64_t ls_since = 0;

const LiveRelation* FindLocked(uint64_t xuid) {
  for (const auto& r : relations) {
    if (r.xuid == xuid) return &r;
  }
  return nullptr;
}

size_t CountLocked(LiveRelationKind kind) {
  size_t n = 0;
  for (const auto& r : relations) n += r.kind == kind;
  return n;
}

std::string RelNote(LiveRelationKind kind) {
  switch (kind) {
    case LiveRelationKind::kFriend: return "friend";
    case LiveRelationKind::kAsksUs: return "asked you";
    case LiveRelationKind::kWeAsked: return "request sent";
    default: return "blocked";
  }
}

// The rows of the current tab (mutex held).
std::vector<Row> RowsLocked() {
  std::vector<Row> rows;
  const auto roster = LiveRoster();
  auto in_room = [&](uint64_t xuid) {
    for (const auto& f : roster) {
      if (f.xuid == xuid) return &f;
    }
    return static_cast<const LiveFriend*>(nullptr);
  };
  switch (tab) {
    case kFriends:
      for (const auto& r : relations) {
        if (r.kind != LiveRelationKind::kFriend) continue;
        const LiveFriend* f = in_room(r.xuid);
        std::string note = !r.state ? "offline" : f ? "online" : "online (another room)";
        const std::string& line = f ? f->status : r.status;
        if (r.state && !line.empty()) note += ": " + line.substr(0, 40);
        rows.push_back({1, r.xuid, r.gamertag, note, r.kind, true});
      }
      break;
    case kRequests:
      for (const auto& r : relations) {
        if (r.kind == LiveRelationKind::kAsksUs) {
          rows.push_back({1, r.xuid, r.gamertag, "wants to be your friend", r.kind, true});
        }
      }
      for (const auto& r : relations) {
        if (r.kind == LiveRelationKind::kWeAsked) {
          rows.push_back({1, r.xuid, r.gamertag, "waiting for an answer", r.kind, true});
        }
      }
      break;
    case kBlocked:
      for (const auto& r : relations) {
        if (r.kind == LiveRelationKind::kBlocked) rows.push_back({1, r.xuid, r.gamertag, "", r.kind, true});
      }
      break;
    default: {
      rows.push_back({0, 0, "", "", {}, false});
      const std::string needle = Lower(search);
      std::unordered_set<uint64_t> seen{IdentityXuid()};
      bool exact = false;
      auto add = [&](uint64_t xuid, const std::string& tag, bool online) {
        if (!xuid || tag.empty() || !seen.insert(xuid).second) return;
        if (!needle.empty() && Lower(tag).find(needle) == std::string::npos) return;
        Row row{1, xuid, tag, online ? "online" : "offline", {}, false};
        if (const LiveRelation* r = FindLocked(xuid)) {
          row.rel = r->kind;
          row.has_rel = true;
          row.note += ", " + RelNote(r->kind);
        }
        exact = exact || Lower(tag) == needle;
        rows.push_back(row);
      };
      std::vector<const LiveFriend*> online;
      for (const auto& f : roster) online.push_back(&f);
      std::sort(online.begin(), online.end(), [](const LiveFriend* a, const LiveFriend* b) {
        return Lower(a->gamertag) < Lower(b->gamertag);
      });
      for (const LiveFriend* f : online) add(f->xuid, f->gamertag, true);
      if (!needle.empty()) {
        for (const auto& r : results) add(r.xuid, r.gamertag, r.online);
        if (!exact) rows.push_back({2, 0, search, "", {}, false});
      }
      break;
    }
  }
  return rows;
}

const char* TabName(Tab t) {
  switch (t) {
    case kFriends: return "Friends";
    case kRequests: return "Requests";
    case kAdd: return "Add friend";
    case kChat: return "Chat";
    default: return "Blocked";
  }
}

// The friends online a direct message can go to (mutex held).
std::vector<const LiveRelation*> ChatFriendsLocked() {
  std::vector<const LiveRelation*> out;
  for (const auto& r : relations) {
    if (r.kind == LiveRelationKind::kFriend && r.state) out.push_back(&r);
  }
  return out;
}

struct ChatTarget {
  uint8_t channel;
  uint64_t xuid;  // the friend (kToDirect)
  std::string name;
};

// What Up/Down goes through: Session, World, Club (in a club), the friends online (mutex held).
std::vector<ChatTarget> ChatTargetsLocked() {
  std::vector<ChatTarget> out{{kToSession, 0, {}}, {kToWorld, 0, {}}};
  if (!club_tag.empty()) out.push_back({kToClub, 0, {}});
  for (const auto* r : ChatFriendsLocked()) out.push_back({kToDirect, r->xuid, r->gamertag});
  return out;
}

// Where the Chat tab sends, after the list of online friends or our club changed (mutex held).
void FixChatTargetLocked() {
  if (chat_to == kToClub && club_tag.empty()) chat_to = kToSession;
  if (chat_to != kToDirect) return;
  for (const auto* r : ChatFriendsLocked()) {
    if (r->xuid == chat_friend) return;
  }
  chat_to = kToSession;
}

std::string ChatTargetLocked() {
  if (chat_to == kToSession) return "Session";
  if (chat_to == kToWorld) return "Horizon World";
  if (chat_to == kToClub) return "Club " + club_tag;
  return chat_friend_name + " (direct)";
}

std::string ChatLineText(const ChatLine& l) {
  if (l.channel == kToDirect) {
    return l.mine ? "[to " + l.to + "] You: " + l.text : "[direct] " + l.from + ": " + l.text;
  }
  if (l.channel == 0) return "* " + l.text;
  const char* where = l.channel == kToSession ? "Session" : l.channel == kToWorld ? "World" : "Club";
  return std::string("[") + where + "] " + (l.mine ? "You" : l.from) + ": " + l.text;
}

void ChatMoveLocked(int delta) {
  const auto targets = ChatTargetsLocked();
  const int n = int(targets.size());
  int at = 0;
  for (int i = 0; i < n; ++i) {
    if (targets[i].channel == chat_to &&
        (chat_to != kToDirect || targets[i].xuid == chat_friend)) {
      at = i;
    }
  }
  const ChatTarget& to = targets[(at + delta % n + n) % n];
  chat_to = to.channel;
  if (to.channel == kToDirect) {
    chat_friend = to.xuid;
    chat_friend_name = to.name;
  }
  dirty = true;
}

void ChatSendLocked() {
  if (chat_text.find_first_not_of(' ') == std::string::npos) {
    chat_text.clear();
    return;
  }
  const uint8_t channel = chat_to;
  std::vector<uint8_t> body{kChatSay, channel};
  Put64(body, channel == kToDirect ? chat_friend : 0);
  const size_t n = std::min(chat_text.size(), kMaxMessage * 4);
  body.push_back(uint8_t(n >> 8));
  body.push_back(uint8_t(n));
  body.insert(body.end(), chat_text.begin(), chat_text.begin() + n);
  LiveSendChat(body);
  REXLOG_INFO("FORZA_LIVE: chat: to {}: {}", ChatTargetLocked(), chat_text);
  ChatLine line{channel, "", channel == kToDirect ? chat_friend_name : "", chat_text, true};
  chat_log.push_back(std::move(line));
  if (chat_log.size() > kChatLog) chat_log.pop_front();
  chat_text.clear();
  dirty = true;
}

void ShowScreenLocked(int64_t now) {
  last_hud = now;
  const bool log = dirty;
  dirty = false;
  std::string body;
  for (int t = 0; t < kTabs; ++t) {
    std::string name = TabName(Tab(t));
    size_t n = 0;
    if (t == kFriends) n = CountLocked(LiveRelationKind::kFriend);
    if (t == kRequests) n = CountLocked(LiveRelationKind::kAsksUs);
    if (t == kBlocked) n = CountLocked(LiveRelationKind::kBlocked);
    if (t == kChat) n = unread;
    if (n) name += " " + std::to_string(n);
    body += t == tab ? "[" + name + "]   " : " " + name + "    ";
  }
  body += "\n";
  if (!active) body += "(waiting for the server's friends list)\n";
  std::string hint;
  if (menu) {
    body += menu_row.gamertag + ":\n";
    for (size_t i = 0; i < menu_items.size(); ++i) {
      body += (i == menu_cursor ? std::string(1, kHudHighlight) + "> " : std::string("  ")) +
              menu_items[i].first + "\n";
    }
    hint = "Up/Down: move   A or Enter: choose   B or Escape: back";
  } else if (tab == kChat) {
    FixChatTargetLocked();
    unread = 0;
    body += "To: " + ChatTargetLocked() + "   (Up/Down: Session, World" +
            (club_tag.empty() ? " or a friend online)\n" : ", Club or a friend online)\n");
    const size_t first = chat_log.size() > kChatRowsShown ? chat_log.size() - kChatRowsShown : 0;
    if (chat_log.empty()) {
      body += club_tag.empty() ? "  No messages yet\n"
                               : "  No messages yet   (/club lists your clubmates)\n";
    }
    for (size_t i = first; i < chat_log.size(); ++i) body += "  " + ChatLineText(chat_log[i]) + "\n";
    body += std::string(1, kHudHighlight) + "> " + chat_text + "_\n";
    if (osk.open) {
      body += osk.Render();
      hint = Osk::kSendHint;
    } else {
      hint = "Type, Enter: send   A or Y: on-screen keyboard   LB/RB: tabs   B or Escape: close";
    }
  } else if (osk.open) {
    body += "Search: " + search + "_\n" + osk.Render();
    hint = Osk::kHint;
  } else {
    const auto rows = RowsLocked();
    size_t& at = cursor[tab];
    if (at >= rows.size()) at = rows.empty() ? 0 : rows.size() - 1;
    if (rows.empty()) {
      body += tab == kFriends    ? "  No friends yet: add some on Add friend\n"
              : tab == kRequests ? "  No friend requests\n"
                                 : "  No one blocked\n";
    }
    size_t first = at > kRows / 2 ? at - kRows / 2 : 0;
    if (first + kRows > rows.size()) first = rows.size() > kRows ? rows.size() - kRows : 0;
    if (first > 0) body += "  ...\n";
    for (size_t i = first; i < rows.size() && i < first + kRows; ++i) {
      const Row& r = rows[i];
      std::string line;
      if (r.kind == 0) {
        line = "Search: " + search + "_";
      } else if (r.kind == 2) {
        line = "Send a friend request to \"" + r.gamertag + "\"";
      } else {
        line = r.gamertag + (r.note.empty() ? "" : "   " + r.note);
      }
      body += (i == at ? std::string(1, kHudHighlight) + "> " : std::string("  ")) + line + "\n";
    }
    if (first + kRows < rows.size()) body += "  ...\n";
    hint = tab == kAdd ? "Type a gamertag (Y: keyboard)   A or Enter: pick   LB/RB: tabs   "
                         "B or Escape: close"
                       : "Up/Down: move   A or Enter: pick   LB/RB or Left/Right: tabs   "
                         "B or Escape: close";
  }
  if (now < status_until) body += status + "\n";
  body += hint;
  HudShow("Friends", body, 2.5, log);
}

void OpenMenuLocked(const Row& row) {
  menu_items.clear();
  if (row.has_rel) {
    switch (row.rel) {
      case LiveRelationKind::kFriend:
        menu_items = {{"Send a message", kDoMessage}};
        break;
      case LiveRelationKind::kAsksUs:
        menu_items = {{"Accept", kDoAccept}, {"Decline", kDoDecline}, {"Block", kDoBlock}};
        break;
      case LiveRelationKind::kWeAsked:
        menu_items = {{"Take back the request", kDoCancel}, {"Block", kDoBlock}};
        break;
      default:
        menu_items = {{"Unblock", kDoUnblock}};
    }
  } else {
    menu_items = {{"Send a friend request", kDoRequest}, {"Block", kDoBlock}};
  }
  if (!row.has_rel || row.rel == LiveRelationKind::kFriend) {
    for (const auto& player : LiveRoster()) {
      if (player.xuid == row.xuid && (player.state & 0x13) == 0x13 &&
          (!(player.state & 0x100) || (row.has_rel && row.rel == LiveRelationKind::kFriend))) {
        menu_items.insert(menu_items.begin(), {"Join game", kDoJoin});
        break;
      }
    }
    if (muted.count(row.xuid)) {
      menu_items.push_back({"Unmute in chat", kDoUnmute});
    } else {
      menu_items.push_back({"Mute in chat", kDoMute});
    }
  }
  if (row.has_rel && row.rel == LiveRelationKind::kFriend) {
    menu_items.push_back({"Block", kDoBlock});
    menu_items.push_back({"Remove friend", kDoRemove});
  }
  menu_items.push_back({"Back", kDoBack});
  menu_row = row;
  menu_cursor = 0;
  menu = true;
  dirty = true;
}

void DoLocked(Action a, const Row& row) {
  const char* what = "";
  switch (a) {
    case kDoJoin:
      join_request = row.xuid;
      return;
    case kDoMessage:  // the Chat tab, to this friend
      tab = kChat;
      chat_to = kToDirect;  // FixChatTargetLocked finds the friend among those online
      chat_friend = row.xuid;
      chat_friend_name = row.gamertag;
      dirty = true;
      return;
    case kDoAccept: SendOp(kAccept, row.xuid); what = "accept"; break;
    case kDoDecline: SendOp(kDecline, row.xuid); what = "decline"; break;
    case kDoCancel: SendOp(kDecline, row.xuid); what = "take back the request to"; break;
    case kDoRemove: SendOp(kRemove, row.xuid); what = "remove"; break;
    case kDoBlock: SendOp(kBlock, row.xuid, row.gamertag, true); what = "block"; break;
    case kDoUnblock: SendOp(kUnblock, row.xuid); what = "unblock"; break;
    case kDoRequest: SendOp(kRequest, row.xuid, row.gamertag, true); what = "ask"; break;
    case kDoMute:
    case kDoUnmute: {
      std::vector<uint8_t> body{kChatMute};
      Put64(body, row.xuid);
      body.push_back(a == kDoMute);
      LiveSendChat(body);
      what = a == kDoMute ? "mute" : "unmute";
      break;
    }
    default: return;
  }
  REXLOG_INFO("FORZA_LIVE: friends: {} {} ({:016X})", what, row.gamertag, row.xuid);
  next_list = 0;  // the server sends the new list too; ask anyway
}

void OpenScreenLocked(int64_t now, bool for_chat) {
  screen = true;
  tab = for_chat || unread                        ? kChat
        : CountLocked(LiveRelationKind::kAsksUs) ? kRequests
                                                  : kFriends;
  menu = false;
  osk.open = false;
  reader.Reset();
  idle_until = now + kIdleMs;
  dirty = true;
  next_list = 0;
  HudWantKeys(true);
  HudTakeKeys();
  REXLOG_INFO("FORZA_LIVE: friends screen opened ({} friends, {} requests)",
              CountLocked(LiveRelationKind::kFriend), CountLocked(LiveRelationKind::kAsksUs));
}

void CloseScreenLocked(uint16_t buttons, const char* why) {
  screen = false;
  latched |= buttons;
  menu = false;
  osk.open = false;
  HudWantKeys(false);
  HudClear();
  REXLOG_INFO("FORZA_LIVE: friends screen closed ({})", why);
}

void SetSearchLocked(const std::string& s, int64_t now) {
  if (s == search) return;
  search = s;
  search_changed = now;
  search_sent = search.empty();
  results.clear();
  cursor[kAdd] = 0;
  dirty = true;
}

// The screen's input (mutex held).
void ScreenInputLocked(uint16_t buttons, int left_x, int left_y, int64_t now) {
  using namespace pad;
  const HudKeys keys = HudTakeKeys();
  uint16_t p = reader.Poll(buttons, left_x, left_y, keys.key_frames);
  if (keys.up) p |= kUp;
  if (keys.down) p |= kDown;
  const bool any_key = !keys.typed.empty() || keys.backspace || keys.enter || keys.escape ||
                       keys.up || keys.down || keys.left || keys.right;
  if (p || any_key) {
    idle_until = now + kIdleMs;
    dirty = true;
  }
  if (tab == kChat && !menu) {  // the Chat tab: typing writes the message
    if (!keys.typed.empty() || keys.backspace) {
      chat_text += keys.typed;
      for (int i = 0; i < keys.backspace; ++i) PopUtf8(chat_text);
      if (chat_text.size() > kMaxMessage) chat_text.resize(kMaxMessage);
    }
    if (osk.open) {
      if (keys.enter) p |= kStart;
      if (keys.escape) p |= kB;
      if (osk.Press(p, chat_text, kMaxMessage) == Osk::kDone) ChatSendLocked();
      return;
    }
    if (keys.enter) ChatSendLocked();
    if ((p & kB) || keys.escape) {
      CloseScreenLocked(buttons, "closed");
      return;
    }
    if (p & kUp) ChatMoveLocked(-1);
    if (p & kDown) ChatMoveLocked(1);
    if (p & (kA | kY)) osk.Open();
    if ((p & (kRB | kRight)) || keys.right) tab = Tab((tab + 1) % kTabs);
    if ((p & (kLB | kLeft)) || keys.left) tab = Tab((tab + kTabs - 1) % kTabs);
    return;
  }
  // Typing: the search (on Add friend).
  if (!keys.typed.empty() || keys.backspace) {
    std::string s = search;
    if (!menu) {
      if (tab != kAdd) tab = kAdd;
      s += keys.typed;
      for (int i = 0; i < keys.backspace; ++i) PopUtf8(s);
      if (s.size() > kMaxSearch) s.resize(kMaxSearch);
      SetSearchLocked(s, now);
    }
  }
  if (osk.open) {
    std::string s = search;
    if (keys.enter || keys.escape) p |= kB;
    osk.Press(p, s, kMaxSearch);
    SetSearchLocked(s, now);
    return;
  }
  if (menu) {
    const size_t n = menu_items.size();
    if (p & kUp) menu_cursor = (menu_cursor + n - 1) % n;
    if (p & kDown) menu_cursor = (menu_cursor + 1) % n;
    if ((p & kA) || keys.enter) {
      DoLocked(menu_items[menu_cursor].second, menu_row);
      menu = false;
    } else if ((p & kB) || keys.escape) {
      menu = false;
    }
    return;
  }
  if ((p & kB) || keys.escape) {
    CloseScreenLocked(buttons, "closed");
    return;
  }
  if ((p & (kRB | kRight)) || keys.right) tab = Tab((tab + 1) % kTabs);
  if ((p & (kLB | kLeft)) || keys.left) tab = Tab((tab + kTabs - 1) % kTabs);
  const auto rows = RowsLocked();
  size_t& at = cursor[tab];
  if (!rows.empty()) {
    if (p & kUp) at = (at + rows.size() - 1) % rows.size();
    if (p & kDown) at = (at + 1) % rows.size();
    at = std::min(at, rows.size() - 1);
  }
  if (p & kY) {
    tab = kAdd;
    cursor[kAdd] = 0;
    osk.Open();
    return;
  }
  if (((p & kA) || keys.enter) && at < rows.size()) {
    const Row& row = rows[at];
    if (row.kind == 0) {
      if (p & kA) osk.Open();
    } else if (row.kind == 2) {
      DoLocked(kDoRequest, row);
    } else {
      OpenMenuLocked(row);
    }
  }
}

}  // namespace

bool LiveFriendsActive() {
  std::lock_guard<std::mutex> lock(mutex);
  return active;
}

void LiveChatSay(uint8_t channel, const std::string& text) {
  std::lock_guard<std::mutex> lock(mutex);
  if (channel != kToSession && channel != kToWorld && channel != kToClub) return;
  const auto to = chat_to;
  chat_to = channel;
  chat_text = text.substr(0, kMaxMessage);
  ChatSendLocked();
  chat_to = to;
}

std::vector<LiveRelation> LiveRelations() {
  std::lock_guard<std::mutex> lock(mutex);
  return relations;
}

std::vector<LiveFriend> LiveFriendsList() {
  auto roster = LiveRoster();
  std::vector<LiveRelation> rels;
  {
    std::lock_guard<std::mutex> lock(mutex);
    if (!active) return roster;
    rels = relations;
  }
  std::vector<LiveFriend> here, elsewhere, offline;
  for (const auto& r : rels) {
    if (r.kind != LiveRelationKind::kFriend) continue;
    auto it = std::find_if(roster.begin(), roster.end(),
                           [&](const LiveFriend& f) { return f.xuid == r.xuid; });
    if (it != roster.end()) {
      here.push_back(*it);
      continue;
    }
    LiveFriend f;
    f.xuid = r.xuid;
    f.gamertag = r.gamertag;
    f.state = r.state & (kOnline | kPlaying);  // no session to join from another room
    f.status = r.status;
    (f.state ? elsewhere : offline).push_back(std::move(f));
  }
  here.insert(here.end(), elsewhere.begin(), elsewhere.end());
  here.insert(here.end(), offline.begin(), offline.end());
  return here;
}

bool LiveIsFriend(uint64_t xuid) {
  {
    std::lock_guard<std::mutex> lock(mutex);
    if (active) {
      const LiveRelation* r = FindLocked(xuid);
      return r && r->kind == LiveRelationKind::kFriend;
    }
  }
  for (const auto& f : LiveRoster()) {
    if (f.xuid == xuid) return true;
  }
  return false;
}

bool LiveIsBlocked(uint64_t xuid) {
  std::lock_guard<std::mutex> lock(mutex);
  const LiveRelation* r = FindLocked(xuid);
  return r && r->kind == LiveRelationKind::kBlocked;
}

void LiveFriendsTick() {
  if (!LiveSignin()) return;
  const int64_t now = NowMs();
  // Chat: the sessions we are in, when they change and every 10 s (the first time also
  // asks for our mute list).
  auto ids = LiveSessionIds();
  if (ids.size() > 4) ids.resize(4);
  bool first_sessions;
  {
    std::lock_guard<std::mutex> lock(mutex);
    first_sessions = next_sessions == 0;
    if (ids != sessions_sent || now >= next_sessions) {
      std::vector<uint8_t> body{kChatSessions, uint8_t(first_sessions ? 0xFF : ids.size())};
      if (!first_sessions) {
        for (uint64_t id : ids) Put64(body, id);
      }
      if (LiveSendChat(body)) {
        if (!first_sessions) sessions_sent = ids;
        next_sessions = first_sessions ? 1 : now + 10000;
      }
    }
  }
  {  // the club: ask for the tag of our car club (the server marks this game as knowing the
     // Club channel, and says when the tag changes)
    std::lock_guard<std::mutex> lock(mutex);
    if (now >= next_club && LiveSendChat({kChatClub})) next_club = now + kClubSyncMs;
  }
  {
    // The content manifest, so the server can tell the players of a session what they do not
    // share (server/forza_live/social/chat.py op 5; a server without it ignores the message, #567).
    std::lock_guard<std::mutex> lock(mutex);
    if (next_sessions != 0 && now >= next_content) {
      const std::string text = CurrentContentManifest();
      std::vector<uint8_t> body{kChatContent, uint8_t(text.size() >> 8), uint8_t(text.size())};
      body.insert(body.end(), text.begin(), text.end());
      if (LiveSendChat(body)) next_content = now + 30000;
    }
  }
  if (now < next_list) return;
  uint32_t v;
  {
    std::lock_guard<std::mutex> lock(mutex);
    v = version;
  }
  std::vector<uint8_t> body{kList, uint8_t(v >> 24), uint8_t(v >> 16), uint8_t(v >> 8), uint8_t(v)};
  if (LiveSendFriend(body)) next_list = now + (screen ? 3000 : 10000);
}

// The network thread: a FRIEND message from the server.
void LiveFriendMessage(const uint8_t* p, size_t n) {
  if (!n) return;
  bool friends_changed = false;
  {
    std::lock_guard<std::mutex> lock(mutex);
    switch (p[0]) {
      case kRList: {
        if (n < 11) return;
        const uint32_t v = Load32(p + 1);
        const uint16_t total = Load16(p + 5), first = Load16(p + 7), count = Load16(p + 9);
        if (first == 0) {
          parts.clear();
          parts_seen = 0;
          parts_version = v;
        }
        if (first != parts_seen || v != parts_version) return;  // a part went missing
        size_t pos = 11;
        for (uint16_t i = 0; i < count; ++i) {
          if (pos + 13 > n) return;
          LiveRelation r;
          r.xuid = Load64(p + pos);
          r.kind = LiveRelationKind(p[pos + 8]);
          r.state = Load32(p + pos + 9);
          pos += 13;
          if (!ReadText(p, n, pos, r.gamertag) || !ReadText(p, n, pos, r.status)) return;
          r.gamertag = SanitizeGamertag(r.gamertag);
          parts.push_back(std::move(r));
          ++parts_seen;
        }
        if (parts_seen < total) return;
        auto friend_set = [](const std::vector<LiveRelation>& v) {
          std::vector<uint64_t> out;
          for (const auto& r : v) {
            if (r.kind == LiveRelationKind::kFriend) out.push_back(r.xuid);
          }
          std::sort(out.begin(), out.end());
          return out;
        };
        friends_changed = !active || friend_set(parts) != friend_set(relations);
        if (!active) {
          REXLOG_INFO("FORZA_LIVE: friends list from the server: {} friends, {} requests",
                      friend_set(parts).size(), std::count_if(parts.begin(), parts.end(), [](const LiveRelation& r) {
                        return r.kind == LiveRelationKind::kAsksUs;
                      }));
        } else if (friends_changed) {
          REXLOG_INFO("FORZA_LIVE: friends: now {}", friend_set(parts).size());
        }
        relations = std::move(parts);
        parts.clear();
        parts_seen = 0;
        version = v;
        active = true;
        dirty = true;
        break;
      }
      case kRSame:
        active = true;
        break;
      case kRResults: {
        if (n < 4 || Load16(p + 1) != search_request) return;
        results.clear();
        size_t pos = 4;
        for (uint8_t i = 0; i < p[3]; ++i) {
          if (pos + 10 > n) break;
          Result r;
          r.xuid = Load64(p + pos);
          r.online = p[pos + 8] != 0;
          pos += 10;
          if (!ReadText(p, n, pos, r.gamertag)) break;
          r.gamertag = SanitizeGamertag(r.gamertag);
          results.push_back(std::move(r));
        }
        dirty = true;
        break;
      }
      case kRNotice: {
        if (n < 10) return;
        const uint8_t event = p[1];
        size_t pos = 10;
        std::string name, text;
        if (!ReadText(p, n, pos, name) || !ReadText(p, n, pos, text)) return;
        std::string line = event == 1   ? name + " sent you a friend request"
                           : event == 2 ? name + " accepted your friend request"
                                        : text;
        if (line.empty()) return;
        REXLOG_INFO("FORZA_LIVE: friends: {}", line);
        if (screen) {
          status = line;
          status_until = NowMs() + kStatusMs;
          dirty = true;
        } else if (notices.size() < 5) {
          if (event == 1) line += "\nAnswer it on the friends screen: " +
                                  REXCVAR_GET(live_friends_key) +
                                  (REXCVAR_GET(live_friends_pad_shortcut) ? " or hold the left stick" : "");
          notices.push_back(line);
        }
        next_list = 0;
        break;
      }
      default:
        return;
    }
  }
  if (friends_changed) LiveRosterChanged(true);  // the game's friends list changed
}

// The network thread: a CHAT message from the server.
void LiveChatMessage(const uint8_t* p, size_t n) {
  if (!n) return;
  std::lock_guard<std::mutex> lock(mutex);
  switch (p[0]) {
    case kChatRMessage: {
      if (n < 11) return;
      ChatLine line;
      line.channel = p[1];
      size_t pos = 10;
      if (!ReadText(p, n, pos, line.from) || pos + 2 > n) return;
      const size_t size = std::min<size_t>(Load16(p + pos), n - pos - 2);
      line.text.assign(reinterpret_cast<const char*>(p + pos + 2), size);
      REXLOG_INFO("FORZA_LIVE: chat: {}", ChatLineText(line));
      chat_log.push_back(std::move(line));
      if (chat_log.size() > kChatLog) chat_log.pop_front();
      if (!screen || tab != kChat) {
        ++unread;
        chat_toast = true;
      }
      dirty = true;
      return;
    }
    case kChatRNotice: {
      if (n < 3) return;
      const std::string text(reinterpret_cast<const char*>(p + 3),
                             std::min<size_t>(Load16(p + 1), n - 3));
      REXLOG_INFO("FORZA_LIVE: chat: {}", text);
      chat_log.push_back(ChatLine{0, "", "", text, false});  // all of a reply stay in the log
      if (chat_log.size() > kChatLog) chat_log.pop_front();
      if (screen) {
        status = text;
        status_until = NowMs() + kStatusMs;
        dirty = true;
      } else if (notices.size() < 5) {
        notices.push_back(text);
      }
      return;
    }
    case kChatRClubTag: {
      std::string tag;
      size_t pos = 1;
      if (!ReadText(p, n, pos, tag)) return;
      if (tag != club_tag) REXLOG_INFO("FORZA_LIVE: chat: car club {}", tag.empty() ? "none" : tag);
      club_tag = tag;
      dirty = true;
      return;
    }
    case kChatRMuted: {
      if (n < 3) return;
      muted.clear();
      for (size_t i = 0, count = Load16(p + 1); i < count && 3 + 8 * i + 8 <= n; ++i) {
        muted.insert(Load64(p + 3 + 8 * i));
      }
      dirty = true;
      return;
    }
    default:
      return;
  }
}

bool LiveFriendsScreenOpen() { return screen; }
void LiveOpenFriends(bool chat) {
  if (chat) chat_request = true;
  else toggle_request = true;
}
void LiveOpenFriendsPage(LiveFriendsPage page) {
  const auto value=static_cast<int>(page);
  if(value>=0&&value<kTabs)page_request=value;
}
LiveChatPreview LiveGetChatPreview() {
  std::lock_guard<std::mutex> lock(mutex);
  return {unread,chat_log.empty()?std::string{}:ChatLineText(chat_log.back())};
}

void InstallFriendsKey() {
  if (const std::string key = REXCVAR_GET(live_friends_key); !key.empty()) {
    rex::ui::RegisterBind("bind_friends", key, "Open the Forza Live friends screen",
                          [] { toggle_request = true; });
  }
  if (const std::string key = REXCVAR_GET(live_chat_key); !key.empty()) {
    rex::ui::RegisterBind("bind_chat", key, "Open the Forza Live chat",
                          [] { chat_request = true; });
  }
}

uint16_t LiveFriendsPad(uint16_t buttons, int left_x, int left_y, bool& block_all) {
  using namespace pad;
  block_all = false;
  const bool busy = LiveInviteBusy();  // before our lock: that one takes the invites' lock
  std::unique_lock<std::mutex> lock(mutex);
  latched &= buttons;
  if (!LiveSignin()) return latched;
  const int64_t now = NowMs();
  const bool toggle = toggle_request.exchange(false);
  const bool chat_key = chat_request.exchange(false);
  const int requested=busy?-1:page_request.exchange(-1);
  if (screen) {
    if(requested>=0){
      tab=static_cast<Tab>(requested);menu=false;osk.open=false;dirty=true;
      idle_until=now+kIdleMs;
      REXLOG_INFO("FORZA_LIVE: requested friends page {}",TabName(tab));
    }
    if (toggle || (chat_key && tab == kChat)) {
      CloseScreenLocked(buttons, "key");
      return latched;
    }
    if (chat_key) {
      tab = kChat;
      menu = false;
      osk.open = false;
      dirty = true;
    }
    ScreenInputLocked(buttons, left_x, left_y, now);
    if (join_request) {
      const auto xuid = std::exchange(join_request, 0);
      CloseScreenLocked(buttons, "join game");
      lock.unlock(); // Invite validation reads friends; keep the lock order.
      if (!LiveRequestJoin(xuid)) HudShow("Join game", "That game is no longer joinable", 3);
      block_all = true;
      return 0xFFFF;
    }
    if (!screen) return latched;
    if (now >= idle_until) {
      CloseScreenLocked(buttons, "no input");
      return latched;
    }
    // A server search once the typing pauses.
    if (!search_sent && now - search_changed >= kSearchDelayMs) {
      search_sent = true;
      std::vector<uint8_t> body{kSearch, uint8_t(++search_request >> 8), uint8_t(search_request)};
      PutText(body, search);
      LiveSendFriend(body);
    }
    if (dirty || now - last_hud >= 500) ShowScreenLocked(now);
    block_all = true;
    return 0xFFFF;
  }
  // Closed: the left stick press held (or the key) opens it. Players who hold the left stick
  // while driving turn that off (live_friends_pad_shortcut, #194); the keys still work.
  if ((buttons & kLS) && REXCVAR_GET(live_friends_pad_shortcut)) {
    if (!ls_since) ls_since = now;
  } else {
    ls_since = 0;
  }
  if (!busy && (toggle || chat_key || requested>=0 ||
                (ls_since && now - ls_since >= kHoldMs && !(latched & kLS)))) {
    ls_since = 0;
    OpenScreenLocked(now, chat_key);
    if(requested>=0){
      tab=static_cast<Tab>(requested);
      REXLOG_INFO("FORZA_LIVE: requested friends page {}",TabName(tab));
    }
    ShowScreenLocked(now);
    latched |= buttons;
    block_all = true;
    return 0xFFFF;
  }
  if (!busy && chat_toast) {  // the last messages, for a few seconds
    chat_toast = false;
    std::string body;
    const size_t first = chat_log.size() > 3 ? chat_log.size() - 3 : 0;
    for (size_t i = first; i < chat_log.size(); ++i) body += ChatLineText(chat_log[i]) + "\n";
    body += "Reply: " + REXCVAR_GET(live_chat_key);
    if (REXCVAR_GET(live_friends_pad_shortcut)) body += ", or hold the left stick (Chat tab)";
    HudShow("Chat", body, kChatToastMs / 1000.0);
    notice_until = now + kChatToastMs;
  } else if (!busy && !notices.empty() && now >= notice_until) {
    HudShow("Friends", notices.front(), kNoticeMs / 1000.0);
    notices.pop_front();
    notice_until = now + kNoticeMs;
  }
  return latched;
}

}  // namespace forza
