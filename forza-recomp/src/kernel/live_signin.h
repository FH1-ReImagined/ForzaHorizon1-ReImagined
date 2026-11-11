// forza - signing in to the Forza Live server: when the game may ask for the room's players,
// what the server's errors mean to a player, and how many of each are logged (net.cpp).
//
// The server answers any packet but a HELLO from an address it holds no session for with
// error 1, "not registered" (server/forza_live/server.py). The game used to send its first
// player list request (LIST) before its first HELLO, so almost every log showed that error
// right before "signed in to": players took the harmless line for the reason they could not
// get online. LIST now waits for a fresh WELCOME, and error 1 is logged as information.

#pragma once

#include <chrono>
#include <cstdint>
#include <string>

namespace forza::live_signin {

using Clock = std::chrono::steady_clock;

// The server answers every HELLO (one each 5 s once signed in) with a WELCOME. With none for
// longer than this (two lost in a row, or the network gone) the server may have dropped us:
// it does after 30 s without a HELLO.
constexpr auto kWelcomeFresh = std::chrono::seconds(12);
// An error 1 without a WELCOME this long after it is worth a warning.
constexpr auto kResignGrace = std::chrono::seconds(5);
// Log lines per error code (a refused HELLO is answered once a second).
constexpr int kLinesPerCode = 10;

// Whether the game may send the server packets of a signed-in player (LIST): the server has
// welcomed us, and recently, and not before a pause or a sleep the server may have dropped us
// during (`held`: no WELCOME since then).
inline bool MaySendSignedIn(uint32_t player_id, bool held, Clock::time_point welcomed,
                            Clock::time_point now) {
  return player_id && !held && welcomed != Clock::time_point{} && now >= welcomed &&
         now - welcomed <= kWelcomeFresh;
}

// Error 1 ("not registered") means "sign in again": the game does at once. It is a problem
// only when no WELCOME follows.
class Resign {
 public:
  void Asked(Clock::time_point now) {
    if (since_ == Clock::time_point{}) since_ = now;
  }
  void Welcomed() {
    since_ = {};
    warned_ = false;
  }
  // True once per error 1 that no WELCOME answered within kResignGrace.
  bool ShouldWarn(Clock::time_point now) {
    if (since_ == Clock::time_point{} || warned_ || now - since_ < kResignGrace) return false;
    warned_ = true;
    return true;
  }
  bool Pending() const { return since_ != Clock::time_point{}; }

 private:
  Clock::time_point since_{};
  bool warned_ = false;
};

// Counts log lines per error code, so a flood of one code (error 1 after a server restart,
// a refused HELLO each second) cannot hide another (a kick or a ban).
class ErrorLog {
 public:
  // True while `code` has lines left; `last` is set on its last one.
  bool Take(uint8_t code, bool& last) {
    int& n = counts_[code];
    last = n == kLinesPerCode - 1;
    if (n >= kLinesPerCode) return false;
    ++n;
    return true;
  }

 private:
  int counts_[256] = {};
};

// The server refused our HELLO: online stays off on this server while it does.
inline bool IsRefusal(uint8_t code) { return code >= 2 && code <= 5; }

// A plain-language line for the log: what the error means and what to do. `server` names the
// server, `detail` is the server's own text.
inline std::string ErrorText(uint8_t code, const std::string& server, const std::string& detail) {
  const std::string pick = "pick another server in the launcher (Online > Servers)";
  switch (code) {
    case 1:
      return server + " asked this game to sign in again (it restarted, or dropped this game "
                      "after a pause or 30 s without contact); signing in again";
    case 2:
      return server + " refused this game: it speaks another version of the online protocol (" +
             detail + "). Update the game, or " + pick;
    case 3:
      return server + " refused this game: the server is full (" + detail +
             "). Try again later, or " + pick;
    case 4:
      return server + " refused this game: this player was kicked or banned (" + detail +
             "). Online stays off on this server; only its operator can lift that, or " + pick;
    case 5:
      return server + " refused this game: it only lets in games with event-unlock support, "
                      "which this build does not have (" + detail +
             "). Pick another server in the launcher (Online > Servers)";
    case 6:
      return server + " asked this game to slow down: too many sign-ins from this address (" +
             detail + "); trying again in a moment";
    default:
      return server + " sent error " + std::to_string(code) + ": " + detail;
  }
}

// The same for the screen: a short title and body, or empty when the error needs none.
inline std::string NoticeText(uint8_t code) {
  switch (code) {
    case 2:
      return "This server runs another version of the online protocol: online is off.\n"
             "Update the game, or pick another server in the launcher (Online > Servers).";
    case 3:
      return "The server is full: online is off for now.\n"
             "Try again later, or pick another server in the launcher (Online > Servers).";
    case 4:
      return "This player was kicked or banned from the server: online is off.\n"
             "Pick another server in the launcher (Online > Servers).";
    case 5:
      return "This server needs event-unlock support this game does not have: online is off.\n"
             "Pick another server in the launcher (Online > Servers).";
    default:
      return {};
  }
}

}  // namespace forza::live_signin
