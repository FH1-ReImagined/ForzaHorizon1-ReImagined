// forza - signed requests to the Forza Live server (#515): the game's state (live_auth.h).
//
// Our Live identity signs with an Ed25519 key (its seed in live_identity.txt, ed25519.h); a
// server keeps only the public key. Sign-in, with a server that knows signed requests:
//   1. The first HELLO has an empty friends key slot. The WELCOME brings the server's clock (we
//      keep its offset from ours), our challenge and how our XUID is bound there.
//   2. The next HELLO, sent at once, signs the challenge and carries our public key: an unbound
//      XUID is bound to it. Every HELLO after that signs it too.
//   3. On the main server's names, an XUID still bound to the friends key (a game before signed
//      requests signed in with it) is upgraded: that HELLO also carries the raw friends key,
//      once. From then on the server refuses the raw key for our XUID. Other servers never get
//      the friends key: one bound to it lets us in only if its operator allows it.
// Title service calls are signed from the start (the server's clock comes with the WELCOME,
// which comes before any call).

#include "live_auth.h"

#include "identity.h"
#include "live_signing.h"
#include "lsp_request_header.h"

#include <rex/logging.h>

#include <ctime>
#include <mutex>
#include <random>

#ifndef FORZA_BUILD_ID  // CMake sets it (FORZA_BUILD_ID: the launcher's version, or git-<commit>)
#define FORZA_BUILD_ID "dev"
#endif

namespace forza::live_auth {

namespace ls = forza::live_signing;

namespace {

std::mutex mutex;
struct State {
  bool ready = false;
  std::string name;  // the server's name in signatures
  bool main = false;
  ls::SigningKey key;
  std::string friends_key;  // for the one upgrade (main names only)
  std::string old_key;      // for a server before signed requests (not main names)
  bool welcomed = false;        // a WELCOME came
  bool signed_server = false;   // ... with the auth part: the server knows signed requests
  ls::WelcomeAuth last;         // the newest auth part
  int64_t offset = 0;           // server clock - ours, seconds
  std::string answered;         // the challenge our last signature answered
  std::string hurried;          // the challenge a HELLO was sent at once for
  std::string upgraded;         // the challenge the upgrade was sent with
  bool told_old = false, told_taken = false, told_legacy = false;
} st;

int64_t Now() { return int64_t(std::time(nullptr)) + st.offset; }

std::string RandomBytes(size_t n) {
  static std::random_device rd;
  std::string out;
  while (out.size() < n) {
    const uint32_t r = rd();
    for (int b = 0; b < 4 && out.size() < n; ++b) out += char(r >> (8 * b));
  }
  return out;
}

}  // namespace

void SetServer(const std::string& host) {
  const std::string& friends_key = forza::IdentityFriendsKey();
  const std::string& seed = forza::IdentitySigningSeed();
  std::lock_guard<std::mutex> lock(mutex);
  st = State{};
  if (friends_key.size() != 16 || seed.size() != 32) return;
  st.name = ls::ServerName(host);
  if (st.name.empty() || st.name.size() > ls::kMaxName) {
    REXLOG_WARN("FORZA_LIVE: server name {} cannot be signed for: requests go unsigned", host);
    return;
  }
  st.main = ls::IsMain(st.name);
  st.key = ls::SigningKey::FromSeed(seed);
  st.friends_key = friends_key;
  st.old_key = ls::OldServerKey(friends_key, st.name);
  st.ready = true;
  REXLOG_INFO("FORZA_LIVE: requests to {} are signed (public key {}); the friends key is not sent",
              st.name, ls::Hex(st.key.Public()).substr(0, 16));
}

bool Ready() {
  std::lock_guard<std::mutex> lock(mutex);
  return st.ready;
}

bool OnWelcome(const uint8_t* tail, size_t size) {
  std::lock_guard<std::mutex> lock(mutex);
  if (!st.ready) return false;
  const bool first = !st.welcomed;
  st.welcomed = true;
  ls::WelcomeAuth w;
  if (!ls::ParseWelcomeAuth(tail, size, w)) {
    st.signed_server = false;
    if (!st.told_old) {
      st.told_old = true;
      if (st.main) {
        REXLOG_WARN("FORZA_LIVE: {} does not take signed requests yet: friends and chat stay off "
                    "until it does (the friends key is not sent)", st.name);
      } else {
        REXLOG_INFO("FORZA_LIVE: {} does not take signed requests: it gets its own key, made from "
                    "the friends key, never the friends key itself", st.name);
      }
    }
    return first && !st.main;  // this server's key goes out now
  }
  st.signed_server = true;
  st.offset = w.server_time - int64_t(std::time(nullptr));
  st.last = w;
  if (w.flags & ls::kProven) return false;
  const bool refused = (w.flags & ls::kChecked) && st.answered == w.challenge;
  if (refused && (w.flags & ls::kBound) && !st.told_taken) {
    st.told_taken = true;
    REXLOG_WARN("FORZA_LIVE: {} has this XUID linked to another installation: friends and chat "
                "stay off (ask on Discord to get it back)", st.name);
  }
  if (refused && (w.flags & ls::kLegacy) && !st.main && !st.told_legacy) {
    st.told_legacy = true;
    REXLOG_WARN("FORZA_LIVE: {} keeps this XUID under the friends key of an older version of the "
                "game, which only goes to the main server: friends and chat stay off until its "
                "operator lets this installation in", st.name);
  }
  if (st.answered != w.challenge && st.hurried != w.challenge) {
    st.hurried = w.challenge;
    return true;
  }
  return false;
}

std::vector<uint8_t> HelloTail(const std::vector<uint8_t>& signed_part) {
  std::lock_guard<std::mutex> lock(mutex);
  std::string data;
  if (st.ready && st.welcomed && st.signed_server) {
    // The upgrade: only the main server's names get the friends key, once for a challenge.
    const bool upgrade = st.main && (st.last.flags & ls::kLegacy) && st.upgraded != st.last.challenge;
    data = ls::HelloBlock(st.key, st.name, forza::IdentityXuid(), Now(), st.last.challenge,
                          std::string(signed_part.begin(), signed_part.end()),
                          upgrade ? &st.friends_key : nullptr);
    st.answered = st.last.challenge;
    if (upgrade) {
      st.upgraded = st.last.challenge;
      REXLOG_INFO("FORZA_LIVE: {} has this XUID under the friends key of an older version: "
                  "moving it to this installation's public key", st.name);
    }
  } else if (st.ready && st.welcomed && !st.main) {
    data = st.old_key;  // a server before signed requests: its own key
  }
  std::vector<uint8_t> out;
  out.push_back(uint8_t(data.size()));
  out.insert(out.end(), data.begin(), data.end());
  return out;
}

std::string RequestLines(const std::string& head, const std::string& body) {
  // The build goes with every request, signed or not (a game without a Live identity names it
  // too): the server keeps it with the leaderboard rows the call writes (#517).
  std::string lines = LspRequestHeader::BuildLine(head, FORZA_BUILD_ID, FORZA_TITLE_UPDATE);
  std::lock_guard<std::mutex> lock(mutex);
  if (!st.ready || LspRequestHeader::Has(head, "x-forza-auth")) return lines;
  lines += ls::AuthHeaderLine(st.key, st.name, forza::IdentityXuid(), Now(),
                              ls::Hex(RandomBytes(16)), head, body);
  if (st.welcomed && !st.signed_server && !st.main) {
    lines += "X-Friends-Key: " + ls::Hex(st.old_key, true) + "\r\n";
  }
  return lines;
}

}  // namespace forza::live_auth
