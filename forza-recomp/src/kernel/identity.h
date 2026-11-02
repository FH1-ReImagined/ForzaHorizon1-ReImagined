// forza - who the local player is (see identity.cpp).

#pragma once

#include <cstdint>
#include <filesystem>
#include <string>

namespace forza {

// Directory for per-installation data, next to the runtime's saves: forza/4D5309C9 in
// $XDG_DATA_HOME (default ~/.local/share) on Linux, in Documents on Windows.
std::filesystem::path DataDir();

// The Forza Live server, host[:port]: FORZA_SERVER, else the live_server setting.
const std::string& LiveServerSpec();

// The room on the server: FORZA_ROOM, else the live_room setting.
const std::string& LiveRoom();

// A Forza Live server is configured.
bool LiveMode();

// Live mode and the live_signin setting (on by default; FORZA_LIVE_SIGNIN=0/1
// overrides): the profile reports "signed in to Xbox Live".
bool LiveSignin();

// Online XUID of the signed-in player: that of the saved Live identity in Live mode, else
// 0 (no Live account).
uint64_t IdentityXuid();

// The friends key of the saved Live identity (16 bytes; empty outside Live mode): servers bound
// the XUID's friends list to it before signed requests (#92); the game sends it only once, to
// the main server, to move that binding to its public key (live_auth.cpp, #515).
const std::string& IdentityFriendsKey();

// The Ed25519 seed of the saved Live identity (32 bytes; empty outside Live mode): every
// request to the Forza Live server is signed with it, and servers keep only its public key
// (live_auth.cpp, #515).
const std::string& IdentitySigningSeed();

// Offline XUID of the signed-in profile when overridden (profile_xuid / FORZA_XUID), else 0
// (keep the runtime's profile). Forza's save is tied to it.
uint64_t ProfileXuid();

// Gamertag of the signed-in player: FORZA_GAMERTAG, else the saved Live identity's in
// Live mode, else empty (keep the SDK's profile).
const std::string& IdentityGamertag();

}  // namespace forza
