// forza - who the local player is. (Adapted from RecompiledHaloReach's Reach Live client.)
//
// A profile has two XUIDs on the 360: the offline one of the profile itself and, once it
// is a Live account, an online one (0009xxxxxxxxxxxx).
//
// - Offline XUID: the runtime's one hard-coded profile is 0xB13EBABEBABEBABE ("User").
//   Forza ties its save to it, so it only changes on request: profile_xuid /
//   FORZA_XUID=<hex> (to continue a save made under another profile, e.g. in Xenia).
// - Online XUID and gamertag: with a Forza Live server (live_server / FORZA_SERVER) the
//   player needs an identity that stays the same between runs and is unique among the
//   players: the first Live run creates DataDir()/live_identity.txt with an online XUID
//   and a gamertag (the login name, or "Driver" and 4 digits). Edit the file to change
//   the gamertag; gamertag / FORZA_GAMERTAG override it, also offline.

#include "identity.h"

#include <rex/cvar.h>
#include <rex/filesystem.h>
#include <rex/logging.h>

#include <cctype>
#include <cstdlib>
#include <fstream>
#include <random>
#include <sstream>

// Settings (forza.toml next to the executable, or the F4 settings overlay; they take
// effect on the next start). The environment variables override them.
REXCVAR_DEFINE_STRING(live_server, "", "Network/Forza Live",
                      "Forza Live server, host[:port] (port 21200 by default). Empty: offline "
                      "(FORZA_SERVER overrides)");
REXCVAR_DEFINE_BOOL(live_signin, true, "Network/Forza Live",
                    "Sign in to Xbox LIVE through the Forza Live server: friends, Live lobbies "
                    "(FORZA_LIVE_SIGNIN overrides)");
REXCVAR_DEFINE_STRING(live_room, "", "Network/Forza Live",
                      "Only players in the same room on the server see each other; Title "
                      "Update 4 players are in the room plus \"#TU4\" (FORZA_ROOM overrides)");
REXCVAR_DEFINE_STRING(gamertag, "", "Network/Forza Live",
                      "Your gamertag, 15 characters. Empty: the one in live_identity.txt "
                      "(FORZA_GAMERTAG overrides)");
REXCVAR_DEFINE_STRING(profile_xuid, "", "Profile",
                      "Offline XUID of the signed-in profile, 16 hex digits: that of the Xenia "
                      "profile a copied save was made with (Forza ties its save to it). Empty: "
                      "the runtime's default (FORZA_XUID overrides)");

namespace forza {

namespace {

// An environment variable when set, else a setting.
std::string EnvOr(const char* name, const std::string& setting) {
  const char* v = std::getenv(name);
  return v ? std::string(v) : setting;
}

struct Identity {
  uint64_t xuid = 0;
  std::string gamertag;
  std::string friends_key;  // 16 random bytes (#92; the file keeps them in hex)
  std::string signing_seed;  // 32 random bytes: the Ed25519 key of signed requests (#515)
};

bool IsHex(const std::string& s) {
  for (char c : s) {
    if (!std::isxdigit(static_cast<unsigned char>(c))) return false;
  }
  return true;
}

std::string CleanGamertag(const std::string& raw) {
  std::string out;
  for (char c : raw) {
    if (std::isalnum(static_cast<unsigned char>(c)) || c == ' ') out += c;
    if (out.size() == 15) break;
  }
  while (!out.empty() && out.back() == ' ') out.pop_back();
  return out;
}

Identity LoadOrCreateLiveIdentity() {
  Identity id;
  const std::filesystem::path path = DataDir() / "live_identity.txt";
  if (std::ifstream in(path); in) {
    std::string line;
    while (std::getline(in, line)) {
      auto eq = line.find('=');
      if (eq == std::string::npos) continue;
      std::string key = line.substr(0, eq), value = line.substr(eq + 1);
      if (key == "xuid") id.xuid = std::strtoull(value.c_str(), nullptr, 16);
      if (key == "gamertag") id.gamertag = CleanGamertag(value);
      if (key == "friends_key" && value.size() == 32 && IsHex(value)) {
        for (size_t i = 0; i < 16; ++i) {
          id.friends_key += char(std::stoi(value.substr(2 * i, 2), nullptr, 16));
        }
      }
      if (key == "signing_seed" && value.size() == 64 && IsHex(value)) {
        for (size_t i = 0; i < 32; ++i) {
          id.signing_seed += char(std::stoi(value.substr(2 * i, 2), nullptr, 16));
        }
      }
    }
  }
  bool changed = false;
  std::random_device rd;
  if ((id.xuid >> 48) != 0x0009) {
    id.xuid = 0x0009000000000000ull | ((uint64_t(rd()) << 32 | rd()) & 0x0000FFFFFFFFFFFFull);
    changed = true;
  }
  if (id.friends_key.size() != 16) {
    id.friends_key.clear();
    for (int i = 0; i < 4; ++i) {
      const uint32_t r = rd();
      for (int b = 0; b < 4; ++b) id.friends_key += char(r >> (8 * b));
    }
    changed = true;
  }
  if (id.signing_seed.size() != 32) {
    // Kept with the friends key: an identity file copied to another PC keeps its sign-in.
    id.signing_seed.clear();
    for (int i = 0; i < 8; ++i) {
      const uint32_t r = rd();
      for (int b = 0; b < 4; ++b) id.signing_seed += char(r >> (8 * b));
    }
    changed = true;
  }
  if (id.gamertag.empty()) {
    const char* user = std::getenv("USER");
    id.gamertag = CleanGamertag(user ? user : "");
    if (id.gamertag.size() < 3) id.gamertag = "Driver" + std::to_string(1000 + rd() % 9000);
    changed = true;
  }
  if (changed) {
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    std::ofstream out(path);
    char xuid[17];
    std::snprintf(xuid, sizeof(xuid), "%016llX", (unsigned long long)id.xuid);
    char key[33] = {}, seed[65] = {};
    for (size_t i = 0; i < 16; ++i) {
      std::snprintf(key + 2 * i, 3, "%02X", unsigned(uint8_t(id.friends_key[i])));
    }
    for (size_t i = 0; i < 32; ++i) {
      std::snprintf(seed + 2 * i, 3, "%02X", unsigned(uint8_t(id.signing_seed[i])));
    }
    out << "# Forza Live identity: your online XUID and gamertag (15 characters max).\n"
        << "# friends_key and signing_seed prove to the server that your friends list is yours:\n"
        << "# keep them private and with the XUID (copy this file to play as you on another\n"
        << "# computer). signing_seed signs every request; servers keep only its public key, and\n"
        << "# friends_key is sent once, to move an older binding to it.\n"
        << "xuid=" << xuid << "\ngamertag=" << id.gamertag << "\nfriends_key=" << key
        << "\nsigning_seed=" << seed << "\n";
    REXLOG_INFO("FORZA_LIVE: created identity {} ({})", id.gamertag, path.string());
  }
  return id;
}

const Identity& Get() {
  static const Identity identity = [] {
    Identity id;
    if (LiveMode()) id = LoadOrCreateLiveIdentity();
    if (std::string tag = CleanGamertag(EnvOr("FORZA_GAMERTAG", REXCVAR_GET(gamertag)));
        !tag.empty()) {
      id.gamertag = tag;
    }
    return id;
  }();
  return identity;
}

}  // namespace

std::filesystem::path DataDir() {
  // The runtime's user folder, where it keeps the profile and saves too:
  // $XDG_DATA_HOME or ~/.local/share on Linux, Documents on Windows.
  return rex::filesystem::GetUserFolder() / "forza" / "4D5309C9";
}

const std::string& LiveServerSpec() {
  static const std::string spec = EnvOr("FORZA_SERVER", REXCVAR_GET(live_server));
  return spec;
}

const std::string& LiveRoom() {
  // A Title Update 4 game and a base one can't play together (each one's join of the
  // other's session fails at once, and each player ends up alone in a session of its own;
  // docs/dlc.md, "Title Update 4 online"), so a TU4 build joins the room with "#TU4" added.
#if defined(FORZA_TITLE_UPDATE) && FORZA_TITLE_UPDATE == 4
  static const std::string room = EnvOr("FORZA_ROOM", REXCVAR_GET(live_room)).substr(0, 32) + "#TU4";
#else
  static const std::string room = EnvOr("FORZA_ROOM", REXCVAR_GET(live_room)).substr(0, 32);
#endif
  return room;
}

bool LiveMode() { return !LiveServerSpec().empty(); }

bool LiveSignin() {
  static const bool on = [] {
    const char* v = std::getenv("FORZA_LIVE_SIGNIN");
    const bool want = v ? (*v && *v != '0') : REXCVAR_GET(live_signin);
    return LiveMode() && want;
  }();
  return on;
}

uint64_t IdentityXuid() { return Get().xuid; }

const std::string& IdentityFriendsKey() { return Get().friends_key; }

const std::string& IdentitySigningSeed() { return Get().signing_seed; }

uint64_t ProfileXuid() {
  static const uint64_t xuid = [] {
    std::string v = EnvOr("FORZA_XUID", REXCVAR_GET(profile_xuid));
    const uint64_t x = v.empty() ? 0 : std::strtoull(v.c_str(), nullptr, 16);
    if (x) REXLOG_INFO("Profile: offline XUID {:016X}", x);
    return x;
  }();
  return xuid;
}

const std::string& IdentityGamertag() { return Get().gamertag; }

}  // namespace forza
