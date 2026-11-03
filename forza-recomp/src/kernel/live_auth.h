// forza - signed requests to the Forza Live server (#515), the game's side (live_auth.cpp).
//
// No secret of ours is sent: the HELLO signs the server's challenge and every title service call
// is signed with our Live identity's Ed25519 key (live_signing.h, ed25519.h; server/forza_live/core/auth.py
// has the server's side), and servers keep only the public key. The server's WELCOME tells its
// clock and how our XUID is bound there. The friends key goes out once, to the main server, to
// move a binding a game before signed requests made with it; a server that does not know signed
// requests (a community server on an older version) gets a key made from the friends key and the
// server's name, and the main server nothing.

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace forza::live_auth {

// The host of live_server / FORZA_SERVER (net.cpp, once, before the first HELLO).
void SetServer(const std::string& host);

// A WELCOME's bytes after its title server port (none from a server before signed requests).
// True when a HELLO should go out now: there is a challenge to answer.
bool OnWelcome(const uint8_t* tail, size_t size);

// What follows the HELLO's features byte: u8 length + an auth block, this server's key (a
// server before signed requests) or nothing. `signed_part`: the HELLO up to its features byte.
std::vector<uint8_t> HelloTail(const std::vector<uint8_t>& signed_part);

// The header lines for one complete title server request (lsp_request_header.h Lines): the
// signature, and the game's build (X-Forza-Build, #517), which goes without a signature too.
std::string RequestLines(const std::string& head, const std::string& body);

// Whether title server requests are signed (a Live identity and a server name).
bool Ready();

}  // namespace forza::live_auth
