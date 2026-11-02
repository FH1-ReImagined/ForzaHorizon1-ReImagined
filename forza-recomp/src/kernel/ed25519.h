// forza - Ed25519 signatures (RFC 8032) for signed requests to the Forza Live server (#515).
//
// The game signs only: its public key from the 32-byte seed in live_identity.txt, and
// signatures of title service calls and of the server's sign-in challenge (live_auth.cpp). The
// server verifies (server/forza_ed25519.py); nothing secret is kept on it.
//
// The arithmetic is TweetNaCl's (Bernstein, van Gastel, Janssen, Lange, Schwabe, Smetsers,
// public domain): field elements as 16 limbs of 16 bits, extended coordinates, a constant-time
// ladder over the 256 scalar bits with conditional swaps (no secret-dependent branches or
// memory accesses), and the reduction modulo the group order. SHA-512 is FIPS 180-4. Written
// for this project in C++; checked against the RFC 8032 test vectors and the server's code
// (tools/test_live_signing.cpp). Header-only so that test needs no runtime.

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace forza::ed25519 {

// --- SHA-512 -------------------------------------------------------------------------------------

class Sha512 {
 public:
  Sha512() {
    static constexpr uint64_t kInit[8] = {
        0x6a09e667f3bcc908ull, 0xbb67ae8584caa73bull, 0x3c6ef372fe94f82bull, 0xa54ff53a5f1d36f1ull,
        0x510e527fade682d1ull, 0x9b05688c2b3e6c1full, 0x1f83d9abfb41bd6bull, 0x5be0cd19137e2179ull};
    std::memcpy(h_, kInit, sizeof(h_));
  }

  void Update(const void* data, size_t size) {
    const auto* p = static_cast<const uint8_t*>(data);
    total_ += size;
    while (size > 0) {
      const size_t take = size < sizeof(buf_) - used_ ? size : sizeof(buf_) - used_;
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

  std::array<uint8_t, 64> Final() {
    const uint64_t bits = total_ * 8;
    const uint8_t one = 0x80, zero = 0;
    Update(&one, 1);
    while (used_ != 112) Update(&zero, 1);
    uint8_t len[16] = {};
    for (int i = 0; i < 8; ++i) len[8 + i] = uint8_t(bits >> (56 - 8 * i));
    Update(len, 16);
    std::array<uint8_t, 64> out{};
    for (int i = 0; i < 8; ++i) {
      for (int b = 0; b < 8; ++b) out[size_t(8 * i + b)] = uint8_t(h_[i] >> (56 - 8 * b));
    }
    return out;
  }

 private:
  static uint64_t Rotr(uint64_t x, int n) { return (x >> n) | (x << (64 - n)); }

  void Block(const uint8_t* p) {
    static constexpr uint64_t k[80] = {
        0x428a2f98d728ae22ull, 0x7137449123ef65cdull, 0xb5c0fbcfec4d3b2full, 0xe9b5dba58189dbbcull,
        0x3956c25bf348b538ull, 0x59f111f1b605d019ull, 0x923f82a4af194f9bull, 0xab1c5ed5da6d8118ull,
        0xd807aa98a3030242ull, 0x12835b0145706fbeull, 0x243185be4ee4b28cull, 0x550c7dc3d5ffb4e2ull,
        0x72be5d74f27b896full, 0x80deb1fe3b1696b1ull, 0x9bdc06a725c71235ull, 0xc19bf174cf692694ull,
        0xe49b69c19ef14ad2ull, 0xefbe4786384f25e3ull, 0x0fc19dc68b8cd5b5ull, 0x240ca1cc77ac9c65ull,
        0x2de92c6f592b0275ull, 0x4a7484aa6ea6e483ull, 0x5cb0a9dcbd41fbd4ull, 0x76f988da831153b5ull,
        0x983e5152ee66dfabull, 0xa831c66d2db43210ull, 0xb00327c898fb213full, 0xbf597fc7beef0ee4ull,
        0xc6e00bf33da88fc2ull, 0xd5a79147930aa725ull, 0x06ca6351e003826full, 0x142929670a0e6e70ull,
        0x27b70a8546d22ffcull, 0x2e1b21385c26c926ull, 0x4d2c6dfc5ac42aedull, 0x53380d139d95b3dfull,
        0x650a73548baf63deull, 0x766a0abb3c77b2a8ull, 0x81c2c92e47edaee6ull, 0x92722c851482353bull,
        0xa2bfe8a14cf10364ull, 0xa81a664bbc423001ull, 0xc24b8b70d0f89791ull, 0xc76c51a30654be30ull,
        0xd192e819d6ef5218ull, 0xd69906245565a910ull, 0xf40e35855771202aull, 0x106aa07032bbd1b8ull,
        0x19a4c116b8d2d0c8ull, 0x1e376c085141ab53ull, 0x2748774cdf8eeb99ull, 0x34b0bcb5e19b48a8ull,
        0x391c0cb3c5c95a63ull, 0x4ed8aa4ae3418acbull, 0x5b9cca4f7763e373ull, 0x682e6ff3d6b2b8a3ull,
        0x748f82ee5defb2fcull, 0x78a5636f43172f60ull, 0x84c87814a1f0ab72ull, 0x8cc702081a6439ecull,
        0x90befffa23631e28ull, 0xa4506cebde82bde9ull, 0xbef9a3f7b2c67915ull, 0xc67178f2e372532bull,
        0xca273eceea26619cull, 0xd186b8c721c0c207ull, 0xeada7dd6cde0eb1eull, 0xf57d4f7fee6ed178ull,
        0x06f067aa72176fbaull, 0x0a637dc5a2c898a6ull, 0x113f9804bef90daeull, 0x1b710b35131c471bull,
        0x28db77f523047d84ull, 0x32caab7b40c72493ull, 0x3c9ebe0a15c9bebcull, 0x431d67c49c100d4cull,
        0x4cc5d4becb3e42b6ull, 0x597f299cfc657e2aull, 0x5fcb6fab3ad6faecull, 0x6c44198c4a475817ull};
    uint64_t w[80];
    for (int i = 0; i < 16; ++i) {
      w[i] = 0;
      for (int b = 0; b < 8; ++b) w[i] = w[i] << 8 | p[8 * i + b];
    }
    for (int i = 16; i < 80; ++i) {
      const uint64_t s0 = Rotr(w[i - 15], 1) ^ Rotr(w[i - 15], 8) ^ (w[i - 15] >> 7);
      const uint64_t s1 = Rotr(w[i - 2], 19) ^ Rotr(w[i - 2], 61) ^ (w[i - 2] >> 6);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint64_t a = h_[0], b = h_[1], c = h_[2], d = h_[3], e = h_[4], f = h_[5], g = h_[6], h = h_[7];
    for (int i = 0; i < 80; ++i) {
      const uint64_t t1 = h + (Rotr(e, 14) ^ Rotr(e, 18) ^ Rotr(e, 41)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
      const uint64_t t2 = (Rotr(a, 28) ^ Rotr(a, 34) ^ Rotr(a, 39)) + ((a & b) ^ (a & c) ^ (b & c));
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

  uint64_t h_[8];
  uint8_t buf_[128] = {};
  size_t used_ = 0;
  uint64_t total_ = 0;
};

namespace detail {

using Gf = int64_t[16];  // a field element mod 2^255 - 19: 16 limbs of 16 bits

inline constexpr int64_t kD2[16] = {0xf159, 0x26b2, 0x9b94, 0xebd6, 0xb156, 0x8283, 0x149a, 0x00e0,
                                    0xd130, 0xeef3, 0x80f2, 0x198e, 0xfce7, 0x56df, 0xd9dc, 0x2406};
inline constexpr int64_t kBx[16] = {0xd51a, 0x8f25, 0x2d60, 0xc956, 0xa7b2, 0x9525, 0xc760, 0x692c,
                                    0xdc5c, 0xfdd6, 0xe231, 0xc0a4, 0x53fe, 0xcd6e, 0x36d3, 0x2169};
inline constexpr int64_t kBy[16] = {0x6658, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666,
                                    0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666, 0x6666};
inline constexpr int64_t kL[32] = {0xed, 0xd3, 0xf5, 0x5c, 0x1a, 0x63, 0x12, 0x58, 0xd6, 0x9c, 0xf7,
                                   0xa2, 0xde, 0xf9, 0xde, 0x14, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                   0, 0, 0, 0x10};

inline void Set(Gf r, const int64_t* a) {
  for (int i = 0; i < 16; ++i) r[i] = a[i];
}

inline void Carry(Gf o) {
  for (int i = 0; i < 16; ++i) {
    o[i] += int64_t(1) << 16;
    const int64_t c = o[i] >> 16;
    o[(i + 1) * (i < 15)] += c - 1 + 37 * (c - 1) * (i == 15);
    o[i] -= c * 65536;
  }
}

// Swaps p and q when b is 1, in constant time.
inline void Select(Gf p, Gf q, int64_t b) {
  const int64_t c = ~(b - 1);
  for (int i = 0; i < 16; ++i) {
    const int64_t t = c & (p[i] ^ q[i]);
    p[i] ^= t;
    q[i] ^= t;
  }
}

inline void Pack(uint8_t* o, const Gf n) {
  Gf m, t;
  Set(t, n);
  Carry(t);
  Carry(t);
  Carry(t);
  for (int j = 0; j < 2; ++j) {
    m[0] = t[0] - 0xffed;
    for (int i = 1; i < 15; ++i) {
      m[i] = t[i] - 0xffff - ((m[i - 1] >> 16) & 1);
      m[i - 1] &= 0xffff;
    }
    m[15] = t[15] - 0x7fff - ((m[14] >> 16) & 1);
    const int64_t b = (m[15] >> 16) & 1;
    m[14] &= 0xffff;
    Select(t, m, 1 - b);
  }
  for (int i = 0; i < 16; ++i) {
    o[2 * i] = uint8_t(t[i] & 0xff);
    o[2 * i + 1] = uint8_t(t[i] >> 8);
  }
}

inline void Add(Gf o, const Gf a, const Gf b) {
  for (int i = 0; i < 16; ++i) o[i] = a[i] + b[i];
}

inline void Sub(Gf o, const Gf a, const Gf b) {
  for (int i = 0; i < 16; ++i) o[i] = a[i] - b[i];
}

inline void Mul(Gf o, const Gf a, const Gf b) {
  int64_t t[31] = {};
  for (int i = 0; i < 16; ++i) {
    for (int j = 0; j < 16; ++j) t[i + j] += a[i] * b[j];
  }
  for (int i = 0; i < 15; ++i) t[i] += 38 * t[i + 16];
  for (int i = 0; i < 16; ++i) o[i] = t[i];
  Carry(o);
  Carry(o);
}

inline void Invert(Gf o, const Gf in) {
  Gf c;
  Set(c, in);
  for (int a = 253; a >= 0; --a) {
    Mul(c, c, c);
    if (a != 2 && a != 4) Mul(c, c, in);
  }
  Set(o, c);
}

inline int Parity(const Gf a) {
  uint8_t d[32];
  Pack(d, a);
  return d[0] & 1;
}

// p += q, both in extended coordinates (X, Y, Z, T).
inline void PointAdd(Gf p[4], Gf q[4]) {
  Gf a, b, c, d, t, e, f, g, h;
  Sub(a, p[1], p[0]);
  Sub(t, q[1], q[0]);
  Mul(a, a, t);
  Add(b, p[0], p[1]);
  Add(t, q[0], q[1]);
  Mul(b, b, t);
  Mul(c, p[3], q[3]);
  Mul(c, c, kD2);
  Mul(d, p[2], q[2]);
  Add(d, d, d);
  Sub(e, b, a);
  Sub(f, d, c);
  Add(g, d, c);
  Add(h, b, a);
  Mul(p[0], e, f);
  Mul(p[1], h, g);
  Mul(p[2], g, f);
  Mul(p[3], e, h);
}

inline void PointPack(uint8_t* r, Gf p[4]) {
  Gf tx, ty, zi;
  Invert(zi, p[2]);
  Mul(tx, p[0], zi);
  Mul(ty, p[1], zi);
  Pack(r, ty);
  r[31] ^= uint8_t(Parity(tx) << 7);
}

// p = [s]B, a ladder over all 256 bits that only swaps.
inline void ScalarBase(Gf p[4], const uint8_t* s) {
  static constexpr int64_t kZero[16] = {}, kOne[16] = {1};
  Gf q[4];
  Set(q[0], kBx);
  Set(q[1], kBy);
  Set(q[2], kOne);
  Mul(q[3], kBx, kBy);
  Set(p[0], kZero);
  Set(p[1], kOne);
  Set(p[2], kOne);
  Set(p[3], kZero);
  for (int i = 255; i >= 0; --i) {
    const int64_t b = (s[i / 8] >> (i & 7)) & 1;
    for (int j = 0; j < 4; ++j) Select(p[j], q[j], b);
    PointAdd(q, p);
    PointAdd(p, p);
    for (int j = 0; j < 4; ++j) Select(p[j], q[j], b);
  }
}

// r = x mod L, x: 64 little-endian limbs of 8 bits (their sum may exceed a byte each).
inline void ModL(uint8_t* r, int64_t x[64]) {
  for (int i = 63; i >= 32; --i) {
    int64_t carry = 0;
    int j;
    for (j = i - 32; j < i - 12; ++j) {
      x[j] += carry - 16 * x[i] * kL[j - (i - 32)];
      carry = (x[j] + 128) >> 8;
      x[j] -= carry * 256;
    }
    x[j] += carry;
    x[i] = 0;
  }
  int64_t carry = 0;
  for (int j = 0; j < 32; ++j) {
    x[j] += carry - (x[31] >> 4) * kL[j];
    carry = x[j] >> 8;
    x[j] &= 255;
  }
  for (int j = 0; j < 32; ++j) x[j] -= carry * kL[j];
  for (int i = 0; i < 32; ++i) {
    x[i + 1] += x[i] >> 8;
    r[i] = uint8_t(x[i] & 255);
  }
}

inline void Reduce(uint8_t* r /* 64 bytes in, 32 out */) {
  int64_t x[64];
  for (int i = 0; i < 64; ++i) x[i] = r[i];
  for (int i = 0; i < 64; ++i) r[i] = 0;
  ModL(r, x);
}

inline std::array<uint8_t, 64> Expand(const uint8_t* seed) {
  Sha512 h;
  h.Update(seed, 32);
  auto d = h.Final();
  d[0] &= 248;
  d[31] &= 127;
  d[31] |= 64;
  return d;
}

}  // namespace detail

using PublicKey = std::array<uint8_t, 32>;
using Signature = std::array<uint8_t, 64>;

// The public key of a 32-byte seed (RFC 8032's private key).
inline PublicKey KeyOf(const uint8_t* seed) {
  auto d = detail::Expand(seed);
  detail::Gf p[4];
  detail::ScalarBase(p, d.data());
  PublicKey pk{};
  detail::PointPack(pk.data(), p);
  std::memset(d.data(), 0, d.size());
  return pk;
}

// The signature of `message` by the seed's key (`public_key`: KeyOf(seed)).
inline Signature Sign(const uint8_t* seed, const PublicKey& public_key, const void* message, size_t size) {
  auto d = detail::Expand(seed);
  Sha512 hr;
  hr.Update(d.data() + 32, 32);
  hr.Update(message, size);
  auto r = hr.Final();
  detail::Reduce(r.data());
  detail::Gf p[4];
  detail::ScalarBase(p, r.data());
  Signature sig{};
  detail::PointPack(sig.data(), p);
  Sha512 hk;
  hk.Update(sig.data(), 32);
  hk.Update(public_key.data(), 32);
  hk.Update(message, size);
  auto k = hk.Final();
  detail::Reduce(k.data());
  int64_t x[64] = {};
  for (int i = 0; i < 32; ++i) x[i] = r[size_t(i)];
  for (int i = 0; i < 32; ++i) {
    for (int j = 0; j < 32; ++j) x[i + j] += int64_t(k[size_t(i)]) * d[size_t(j)];
  }
  detail::ModL(sig.data() + 32, x);
  std::memset(d.data(), 0, d.size());
  std::memset(r.data(), 0, r.size());
  return sig;
}

inline Signature Sign(const uint8_t* seed, const PublicKey& public_key, const std::string& message) {
  return Sign(seed, public_key, message.data(), message.size());
}

}  // namespace forza::ed25519
