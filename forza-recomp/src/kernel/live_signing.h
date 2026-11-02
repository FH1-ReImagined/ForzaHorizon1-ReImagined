// forza - signed requests to the Forza Live server (#515): no secret of ours is sent.
//
// Games before this proved our XUID with the friends key of live_identity.txt itself, sent in the
// clear in every HELLO and on every title service call, and the server kept its SHA-256. Now our
// Live identity has an Ed25519 key pair too (the seed is in live_identity.txt, ed25519.h signs):
// each title service call carries an Ed25519 signature (X-Forza-Auth) and the HELLO signs the
// server's challenge the same way. The server keeps only the public key. server/forza_auth.py
// has the scheme and the server's side, live_auth.cpp our state.
//
// - Server name: the host the player configured, lowercased, without its port.
// - Canonical string: "FORZA-ED25519-V1", method, target, server name, XUID (16 upper hex), unix
//   time, nonce (32 lower hex), X-ClassName, X-Method, SHA-256 of the body (lower hex), joined
//   by "\n".
// - A server bound to our raw friends key moves to the public key with one upgrade (the raw key
//   in a signed HELLO), sent only to the main server's names (kMainNames).
// - A server that does not know signed requests gets, in place of the friends key,
//   HMAC-SHA256(friends key, "forza-live server key v1|" + name)[:16] (OldServerKey), useless on
//   any other server; the main server's names get nothing.
//
// Header-only (SHA-256, HMAC and, through ed25519.h, SHA-512 and Ed25519 included) so
// tools/test_live_signing.cpp can test it without the runtime.

#pragma once

#include <algorithm>
#include <array>
#include <cctype>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "ed25519.h"

namespace forza::live_signing {

// --- SHA-256 (FIPS 180-4) and HMAC-SHA256 (RFC 2104) -------------------------------------------

class Sha256 {
 public:
  Sha256() {
    static constexpr uint32_t kInit[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                          0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    std::memcpy(h_, kInit, sizeof(h_));
  }

  void Update(const void* data, size_t size) {
    const auto* p = static_cast<const uint8_t*>(data);
    total_ += size;
    while (size > 0) {
      const size_t take = std::min(size, sizeof(buf_) - used_);
      std::memcpy(buf_ + used_, p, take);
      used_ += take;
      p += take;
      size -= take;
      if (used_ == sizeof(buf_)) {
        Block(buf_);
        used_ = 0;
      }
    }
  }
  void Update(const std::string& s) { Update(s.data(), s.size()); }

  std::array<uint8_t, 32> Final() {
    const uint64_t bits = total_ * 8;
    const uint8_t one = 0x80, zero = 0;
    Update(&one, 1);
    while (used_ != 56) Update(&zero, 1);
    uint8_t len[8];
    for (int i = 0; i < 8; ++i) len[i] = uint8_t(bits >> (56 - 8 * i));
    Update(len, 8);
    std::array<uint8_t, 32> out{};
    for (int i = 0; i < 8; ++i) {
      for (int b = 0; b < 4; ++b) out[size_t(4 * i + b)] = uint8_t(h_[i] >> (24 - 8 * b));
    }
    return out;
  }

 private:
  static uint32_t Rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

  void Block(const uint8_t* p) {
    static constexpr uint32_t k[64] = {
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2};
    uint32_t w[64];
    for (int i = 0; i < 16; ++i) {
      w[i] = uint32_t(p[4 * i]) << 24 | uint32_t(p[4 * i + 1]) << 16 | uint32_t(p[4 * i + 2]) << 8 |
             uint32_t(p[4 * i + 3]);
    }
    for (int i = 16; i < 64; ++i) {
      const uint32_t s0 = Rotr(w[i - 15], 7) ^ Rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
      const uint32_t s1 = Rotr(w[i - 2], 17) ^ Rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = h_[0], b = h_[1], c = h_[2], d = h_[3], e = h_[4], f = h_[5], g = h_[6], h = h_[7];
    for (int i = 0; i < 64; ++i) {
      const uint32_t t1 = h + (Rotr(e, 6) ^ Rotr(e, 11) ^ Rotr(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
      const uint32_t t2 = (Rotr(a, 2) ^ Rotr(a, 13) ^ Rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
      h = g;
      g = f;
      f = e;
      e = d + t1;
      d = c;
      c = b;
      b = a;
      a = t1 + t2;
    }
    h_[0] += a;
    h_[1] += b;
    h_[2] += c;
    h_[3] += d;
    h_[4] += e;
    h_[5] += f;
    h_[6] += g;
    h_[7] += h;
  }

  uint32_t h_[8];
  uint8_t buf_[64] = {};
  size_t used_ = 0;
  uint64_t total_ = 0;
};

// Byte strings are std::string here (keys, digests, datagram parts).
inline std::string Sha256Of(const std::string& data) {
  Sha256 h;
  h.Update(data);
  const auto d = h.Final();
  return std::string(reinterpret_cast<const char*>(d.data()), d.size());
}

inline std::string HmacSha256(std::string key, const std::string& message) {
  if (key.size() > 64) key = Sha256Of(key);
  key.resize(64, '\0');
  std::string ipad(64, '\0'), opad(64, '\0');
  for (size_t i = 0; i < 64; ++i) {
    ipad[i] = char(uint8_t(key[i]) ^ 0x36);
    opad[i] = char(uint8_t(key[i]) ^ 0x5c);
  }
  return Sha256Of(opad + Sha256Of(ipad + message));
}

inline std::string Hex(const std::string& bytes, bool upper = false) {
  static const char* digits[2] = {"0123456789abcdef", "0123456789ABCDEF"};
  std::string out;
  out.reserve(bytes.size() * 2);
  for (char c : bytes) {
    out += digits[upper][uint8_t(c) >> 4];
    out += digits[upper][uint8_t(c) & 15];
  }
  return out;
}

// --- The scheme --------------------------------------------------------------------------------

inline constexpr const char* kTag = "FORZA-ED25519-V1";
inline constexpr const char* kOldServerLabel = "forza-live server key v1|";
// The main server's names: the only ones that get the upgrade (our raw friends key, once), and
// none of our keys when they do not know signed requests.
inline constexpr const char* kMainNames[] = {"forza-reimagined.playit.plus", "forza.dyotson.dev"};
inline constexpr size_t kMaxName = 128;   // a HELLO's auth block fits its u8 length
inline constexpr uint8_t kBlock = 0xA2;   // HELLO auth block, WELCOME auth part
inline constexpr uint8_t kUpgrade = 0x02;  // HELLO block flag: the raw friends key follows
inline constexpr uint8_t kBound = 0x01, kProven = 0x02, kChecked = 0x04, kLegacy = 0x08;  // WELCOME

// The host of "host[:port]" as signatures name it: lowercased, without port, brackets or a
// trailing dot.
inline std::string ServerName(std::string host) {
  while (!host.empty() && std::isspace(static_cast<unsigned char>(host.front()))) host.erase(0, 1);
  while (!host.empty() && std::isspace(static_cast<unsigned char>(host.back()))) host.pop_back();
  for (char& c : host) c = char(std::tolower(static_cast<unsigned char>(c)));
  if (!host.empty() && host.front() == '[') {
    const size_t close = host.find(']');
    host = host.substr(1, close == std::string::npos ? std::string::npos : close - 1);
  } else if (std::count(host.begin(), host.end(), ':') == 1) {
    host.resize(host.find(':'));
  }
  while (!host.empty() && host.back() == '.') host.pop_back();
  return host;
}

inline bool IsMain(const std::string& name) {
  for (const char* m : kMainNames) {
    if (name == m) return true;
  }
  return false;
}

// What a server that does not know signed requests gets in place of the friends key.
inline std::string OldServerKey(const std::string& friends_key, const std::string& name) {
  return HmacSha256(friends_key, kOldServerLabel + name).substr(0, 16);
}

inline std::string Canonical(const std::string& method, const std::string& target,
                             const std::string& name, uint64_t xuid, int64_t when,
                             const std::string& nonce_hex, const std::string& classname,
                             const std::string& xmethod, const std::string& body_hash_hex) {
  char x[17];
  std::snprintf(x, sizeof(x), "%016llX", static_cast<unsigned long long>(xuid));
  return std::string(kTag) + "\n" + method + "\n" + target + "\n" + name + "\n" + x + "\n" +
         std::to_string(when) + "\n" + nonce_hex + "\n" + classname + "\n" + xmethod + "\n" +
         body_hash_hex;
}

// Our Ed25519 key: the seed and its public key.
struct SigningKey {
  std::array<uint8_t, 32> seed{};
  ed25519::PublicKey pub{};

  static SigningKey FromSeed(const std::string& seed) {
    SigningKey k;
    std::memcpy(k.seed.data(), seed.data(), std::min<size_t>(seed.size(), 32));
    k.pub = ed25519::KeyOf(k.seed.data());
    return k;
  }

  std::string Sign(const std::string& text) const {
    const auto sig = ed25519::Sign(seed.data(), pub, text);
    return std::string(reinterpret_cast<const char*>(sig.data()), sig.size());
  }

  std::string Public() const { return std::string(reinterpret_cast<const char*>(pub.data()), pub.size()); }
};

// The value of a header in a request head (the last one of that name, trimmed), or "".
inline std::string HeaderValue(const std::string& head, const std::string& lower_name) {
  std::string value;
  size_t at = head.find("\r\n");
  while (at != std::string::npos && at + 2 < head.size()) {
    const size_t start = at + 2, end = head.find("\r\n", start);
    const std::string line = head.substr(start, end == std::string::npos ? std::string::npos : end - start);
    const size_t colon = line.find(':');
    if (colon != std::string::npos) {
      std::string name = line.substr(0, colon);
      for (char& c : name) c = char(std::tolower(static_cast<unsigned char>(c)));
      while (!name.empty() && (name.back() == ' ' || name.back() == '\t')) name.pop_back();
      while (!name.empty() && (name.front() == ' ' || name.front() == '\t')) name.erase(0, 1);
      if (name == lower_name) {
        size_t b = colon + 1, e = line.size();
        while (b < e && (line[b] == ' ' || line[b] == '\t')) ++b;
        while (e > b && (line[e - 1] == ' ' || line[e - 1] == '\t')) --e;
        value = line.substr(b, e - b);
      }
    }
    at = end;
  }
  return value;
}

// The X-Forza-Auth line ("...\r\n") for a complete request (its head and body), or "" when the
// head has no request line.
inline std::string AuthHeaderLine(const SigningKey& key, const std::string& name, uint64_t xuid,
                                  int64_t when, const std::string& nonce_hex,
                                  const std::string& head, const std::string& body) {
  const size_t eol = head.find("\r\n");
  const std::string request = head.substr(0, eol);
  const size_t sp1 = request.find(' ');
  if (sp1 == std::string::npos || sp1 == 0) return {};
  const size_t sp2 = request.find(' ', sp1 + 1);
  const std::string method = request.substr(0, sp1);
  const std::string target =
      request.substr(sp1 + 1, sp2 == std::string::npos ? std::string::npos : sp2 - sp1 - 1);
  const std::string text =
      Canonical(method, target, name, xuid, when, nonce_hex, HeaderValue(head, "x-classname"),
                HeaderValue(head, "x-method"), Hex(Sha256Of(body)));
  char x[17];
  std::snprintf(x, sizeof(x), "%016llX", static_cast<unsigned long long>(xuid));
  return std::string("X-Forza-Auth: ") + kTag + " Server=" + name + ", Xuid=" + x +
         ", Time=" + std::to_string(when) + ", Nonce=" + nonce_hex +
         ", Signature=" + Hex(key.Sign(text)) + "\r\n";
}

// A HELLO's auth block (it goes in the friends key slot after the features byte): flags, time,
// name, the signature of the HELLO canonical string (`signed_part`: the HELLO up to and
// including its features byte; `nonce`: the WELCOME's challenge), our public key and, for an
// upgrade, the raw friends key.
inline std::string HelloBlock(const SigningKey& key, const std::string& name, uint64_t xuid,
                              int64_t when, const std::string& nonce,
                              const std::string& signed_part, const std::string* upgrade) {
  std::string out;
  out += char(kBlock);
  out += char(upgrade ? kUpgrade : 0);
  for (int i = 0; i < 8; ++i) out += char(uint8_t(uint64_t(when) >> (56 - 8 * i)));
  out += char(uint8_t(name.size()));
  out += name;
  out += key.Sign(Canonical("HELLO", "-", name, xuid, when, Hex(nonce), "", "", Hex(Sha256Of(signed_part))));
  out += key.Public();
  if (upgrade) out += *upgrade;
  return out;
}

// The auth part at the end of a WELCOME (after the title server port): flags, the server's
// clock and our challenge. False for a server before signed requests (nothing there).
struct WelcomeAuth {
  uint8_t flags = 0;
  int64_t server_time = 0;
  std::string challenge;  // 16 bytes
};

inline bool ParseWelcomeAuth(const uint8_t* p, size_t n, WelcomeAuth& out) {
  if (!p || n < 26 || p[0] != kBlock) return false;
  out.flags = p[1];
  uint64_t t = 0;
  for (int i = 0; i < 8; ++i) t = t << 8 | p[2 + i];
  out.server_time = int64_t(t);
  out.challenge.assign(reinterpret_cast<const char*>(p + 10), 16);
  return true;
}

}  // namespace forza::live_signing
