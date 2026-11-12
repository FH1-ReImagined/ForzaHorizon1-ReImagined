// forza - what other Forza Live code needs from the virtual network (net.cpp).

#pragma once

#include <cstdint>
#include <array>
#include <optional>
#include <set>
#include <string>
#include <vector>
#include "../platform/social_presence.h"

namespace forza {

// The virtual network is on (FORZA_NET or a Forza Live server).
bool NetworkOn();

// A validated server welcome arrived within the last 20 seconds. Unlike
// NetworkOn/LiveSignin, this checks recent server contact, not configuration.
bool LiveConnected();

// The Forza Live server's IPv4 address (host order) and the TCP port of its title
// servers (HTTP). False without FORZA_SERVER, or before the server welcomed us.
// TCP connections the game opens to this address are sent to that port.
bool LiveServerHttp(uint32_t& ip, uint16_t& port);

// Another player in our room on the Forza Live server: a "friend" of a Live player.
struct LiveFriend {
  uint32_t id = 0;  // server id
  uint64_t xuid = 0;
  std::string gamertag;
  uint32_t state = 0;           // X_ONLINE_FRIENDSTATE flags it reported
  uint8_t session[0x3C] = {};   // XSESSION_INFO of its joinable session, zeros if none
  std::string status;           // presence line
  std::vector<uint8_t> extra;   // its hosted session's slots and QoS data (live_xmsg.cpp)
};

// The other players in the room, as of the server's last answer (refreshed every 3 s).
std::vector<LiveFriend> LiveRoster();

// Our presence: X_ONLINE_FRIENDSTATE flags, the XSESSION_INFO friends can join (null:
// none) and a status line. Sent to the server now and with every keep-alive.
void LiveSetPresence(uint32_t state, const uint8_t* session_info, const std::string& status,
                     const std::vector<uint8_t>& extra = {});

// This instance's XNADDR (36 bytes, guest layout).
void LiveSelfXnAddr(uint8_t* xnaddr);

// Game invites through the server: send one to a player in the room (with our joinable
// session), and take the one the player accepted (false when none is waiting;
// live_invites.cpp).
struct LiveInvite {
  uint64_t inviter_xuid = 0;
  std::string inviter;
  uint8_t session[0x3C] = {};
};
void LiveSendInvite(uint64_t xuid, const uint8_t* session_info);
bool LiveTakeInvite(LiveInvite& out);

// The invite a previous run of the game kept as launch data when it restarted itself to
// join (X_INVITE_INFO, 0x54 bytes; xam_loader.cpp), once; false when there is none.
bool LoaderTakeBootInvite(uint8_t* out);

// Called by the network thread when an invite arrives: the player is asked whether to
// join (live_invites.cpp, #90).
void LiveInviteReceived(const LiveInvite& invite);

// The invite picker (live_invites.cpp): the player searches the online players, picks
// whom to invite to `session_info` (XSESSION_INFO) and sends. `from` names the caller in
// the log.
void LiveOpenInvitePicker(const uint8_t* session_info, const char* from);

// Player 1's pad for the picker and the invite prompt (XamInputGetState): returns the
// buttons to hide from the game; `block_all`: the game gets no input at all.
uint16_t LiveInvitePad(uint16_t buttons, int left_y, bool& block_all);

// Matchmaking sessions on the server (live_xmsg.cpp; RecompiledHaloReach's
// docs/online_plan.md section 5.4): publish ours or search for a playlist's (bodies as
// forza_live_server.py documents); the network thread hands the server's answers to
// LiveMatchResults. The last published records (ours, our standby) are sent again with
// every keep-alive, so a lost datagram or a server restart does not hide our session.
// False when nothing was sent (the server has not welcomed us).
bool LiveSendMatch(bool search, const std::vector<uint8_t>& body);
void LiveMatchResults(const uint8_t* body, size_t size);

// The playlist our last matchmaking search was for (Find Game: the hopper id; 0 before the
// first search). Hooks use it to tell the Horizon World from other playlists.
uint32_t LiveMatchPlaylist();
// The playlists that run as a Horizon World (server/forza_live/title_services/lsp.py WORLD_HOPPERS): the
// Horizon World (3) and the Co-op Festival (20, #309: the same world among friends, whose
// host starts festival events with /event in World chat).
inline bool LiveIsWorldPlaylist(uint32_t playlist) { return playlist == 3 || playlist == 20; }

// Read the current title-published local activity and region together. False
// until both XUser contexts have been received; no network request is made.
bool LiveLocalActivity(uint32_t& presence, uint32_t& region);

// Called about every second by the network thread: reports host changes of the
// matchmaking session we joined (live_xmsg.cpp).
void LiveMatchTick();

// WORLD_EVENT from the server (net.cpp): an event waits for the Horizon World we host
// (hooks/world_events.cpp takes it), or a campaign round's CPU skill for our world.
void LiveWorldEvent(const uint8_t* body, size_t size);

// Head-to-head challenges (hooks/world_challenge.cpp, server/forza_live/competition/challenge.py): send a
// CHALLENGE message to the server; the network thread hands the server's to LiveChallenge.
void LiveSendChallenge(const std::vector<uint8_t>& body);
void LiveChallenge(const uint8_t* body, size_t size);

// Xbox LIVE stats on the server (live_xmsg.cpp, server/forza_live/competition/stats.py): send a STATS message
// (a write or a read); the network thread hands the server's rows to LiveStatsRows.
void LiveSendStats(const std::vector<uint8_t>& body);
void LiveStatsRows(const uint8_t* body, size_t size);

// The online game's "this console hosts the session" flag: 1, 0, or -1 without an online
// game (hooks/world_drop_in.cpp).
int OnlineGameIsHost();

// The machine slept for `seconds` (src/platform/resume.cpp, #140): sign in to the Forza Live
// server again now, as it forgets a player it has not heard from for 30 s. (A game that was
// stopped is noticed by the network thread itself: it did not run either.)
void LiveResumed(double seconds);

// Called by the network thread when the roster changed (live_xmsg.cpp): tells the
// game its friends' presence changed, or that friends came or went.
void LiveRosterChanged(bool membership_changed);

// --- Friends (live_friends.cpp, server/forza_live/social/friends.py, #92) ---
// The server keeps each player's friends; a game that has its list shows only those
// players as friends. Until the server sent one (an older server), every player in the
// room counts as a friend, as before.

// A player on our friends list, a request either way, or a player we blocked.
enum class LiveRelationKind : uint8_t { kFriend = 1, kAsksUs = 2, kWeAsked = 3, kBlocked = 4 };
struct LiveRelation {
  uint64_t xuid = 0;
  std::string gamertag;
  LiveRelationKind kind = LiveRelationKind::kFriend;
  uint32_t state = 0;  // a friend's X_ONLINE_FRIENDSTATE flags (0: offline)
  std::string status;  // a friend's presence line
};

// The server answered with a friends list (else: everyone in the room is a friend).
bool LiveFriendsActive();
std::vector<LiveRelation> LiveRelations();

// The game's friends list: with the server's list, our friends (those in our room with the
// presence of the roster, the others online or offline without a joinable session), online
// first; else the room's roster.
std::vector<LiveFriend> LiveFriendsList();
bool LiveIsFriend(uint64_t xuid);
bool LiveIsBlocked(uint64_t xuid);

// A FRIEND message to the server (false when it was not sent), and the server's (network
// thread). LiveFriendsTick: about every second from the network thread (asks for the list).
bool LiveSendFriend(const std::vector<uint8_t>& body);
void LiveFriendMessage(const uint8_t* body, size_t size);
void LiveFriendsTick();
// A notice about online (the server refused us, net.cpp), shown over the game as the
// friends notices are, when nothing else shows. Any thread.
void LiveOnlineNotice(const std::string& text);

// The friends screen over the game: F8 (live_friends_key) or holding the left stick press;
// F9 (live_chat_key) opens it on the Chat tab.
// Player 1's pad every XamInputGetState (after LiveInvitePad): returns the buttons to hide
// from the game; `block_all`: the game gets no input.
uint16_t LiveFriendsPad(uint16_t buttons, int left_x, int left_y, bool& block_all);
bool LiveFriendsScreenOpen();
// Queue the existing friends editor/chat through its input-thread request path.
void LiveOpenFriends(bool chat = false);
enum class LiveFriendsPage { Friends, Requests, AddFriend, Blocked, Chat };
void LiveOpenFriendsPage(LiveFriendsPage page);
struct LiveChatPreview {
  size_t unread = 0;
  std::string latest;
};
// Read-only copy under the friends/chat lock. Does not mark messages read.
LiveChatPreview LiveGetChatPreview();
// ForzaApp::OnCreateDialogs: registers the keyboard bindings.
void InstallFriendsKey();

// The invite picker or an invite prompt shows (live_invites.cpp).
bool LiveInviteBusy();

// --- Text chat (live_friends.cpp's Chat tab, server/forza_live/social/chat.py, #92) ---
// A CHAT message to the server, and the server's (network thread).
bool LiveSendChat(const std::vector<uint8_t>& body);
// Sends a chat line as the Chat tab would (channel 1 session, 2 world, 4 club) and shows it in
// the tab: scripted tests (FORZA_AUTOPRESS_FIFO "SAY:<text>" says it in World chat, "SAYC:" in
// Club chat).
void LiveChatSay(uint8_t channel, const std::string& text);
// Sends a server command in World chat ("/event 4": the Co-op Festival's start from an event's
// map card, hooks/coop_map_event.cpp), not shown in the Chat tab: the server answers with
// notices. False when the game is not signed in to a Forza Live server with chat.
bool LiveChatCommand(const std::string& text);
void LiveChatMessage(const uint8_t* body, size_t size);
// The ids of the sessions we host or joined (live_xmsg.cpp): the "session" chat channel
// reaches the players in one of them.
std::vector<uint64_t> LiveSessionIds();
// The content this game has that changes which cars exist (kernel/content_manifest.h, #567):
// the installed DLC offers (dlc_log.cpp) and the loaded mods that change cars or the database
// (mod_overlay.cpp). The Chat message "content" (op 4) tells the server, which tells the
// players of a session what they do not share.
void SetInstalledDlcOffers(const std::set<uint32_t>& offers);
void NoteLoadedMod(const std::string& name, bool changes_cars);
std::string CurrentContentManifest();

// Newest presence session, copied under the session lock. Members are XUIDs
// observed through XSessionJoin/Leave, never the unrelated server-room roster.
struct LiveSessionView {
  uint64_t id = 0;
  uint32_t flags = 0;
  bool hosted = false;
  uint32_t players = 0, capacity = 0;
  std::array<uint8_t, 0x3C> info{};
  std::vector<uint64_t> members;
  social_presence::Car local_car;
};
std::optional<LiveSessionView> LiveCurrentSession();
void LiveSetCarDetails(const social_presence::Car& car);
// Explicit join confirmation through the existing native invite-accept path.
// Revalidates presence, blocking and friends-only restrictions before showing.
bool LiveRequestJoin(uint64_t xuid);

}  // namespace forza
