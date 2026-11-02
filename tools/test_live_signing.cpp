// Standalone: c++ -std=c++20 -I forza-recomp/src tools/test_live_signing.cpp -o test_live_signing
// Signed requests to the Forza Live server (forza-recomp/src/kernel/live_signing.h, ed25519.h,
// #515): SHA-256, HMAC, SHA-512 and Ed25519 against their published vectors (FIPS 180, RFC 4231,
// RFC 8032), and the scheme against the vectors server/test_forza_auth.py and
// launcher/test_live_auth.py check too.
#include "kernel/live_signing.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); std::exit(1); } } while (false)

namespace ls = forza::live_signing;
namespace ed = forza::ed25519;

namespace {
std::string Unhex(const std::string& hex) {
  std::string out;
  for (size_t i = 0; i + 1 < hex.size(); i += 2) out += char(std::stoi(hex.substr(i, 2), nullptr, 16));
  return out;
}

template <size_t N>
std::string Str(const std::array<uint8_t, N>& a) {
  return std::string(reinterpret_cast<const char*>(a.data()), a.size());
}

std::string Bytes(int n) {
  std::string k;
  for (int i = 0; i < n; ++i) k += char(i);
  return k;
}

const std::string kHead =
    "POST /Services.Horizon/o.xtsw HTTP/1.1\r\nHost: forza\r\nContent-Type: bin/xtsw\r\n"
    "Content-Length: 3\r\nX-ClassName:  Forza.WebServices.User, Forza.WebServices \r\n"
    "x-method: Logon2\r\nX-UserId: 0x9000000000001\r\n\r\n";
const std::string kBody("\0\1\2", 3);

// RFC 8032 section 7.1: seed, public key, message, signature.
const char* kRfc8032[][4] = {
    {"9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"},
    {"4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"},
    {"c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"},
    {"833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
     "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"},
};
}  // namespace

int main() {
  // FIPS 180-2 and RFC 4231 vectors
  CHECK(ls::Hex(ls::Sha256Of("")) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  CHECK(ls::Hex(ls::Sha256Of("abc")) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  CHECK(ls::Hex(ls::Sha256Of("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq")) ==
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  {
    ls::Sha256 h;  // a million a's, in pieces that straddle the blocks
    const std::string piece(997, 'a');
    size_t left = 1000000;
    while (left) {
      const size_t n = std::min(left, piece.size());
      h.Update(piece.data(), n);
      left -= n;
    }
    CHECK(ls::Hex(Str(h.Final())) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
  }
  CHECK(ls::Hex(ls::HmacSha256(std::string(20, '\x0b'), "Hi There")) ==
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7");
  CHECK(ls::Hex(ls::HmacSha256("Jefe", "what do ya want for nothing?")) ==
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
  CHECK(ls::Hex(ls::HmacSha256(std::string(131, '\xaa'),
                               "Test Using Larger Than Block-Size Key - Hash Key First")) ==
        "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
  {
    ed::Sha512 h;
    h.Update("abc", 3);
    CHECK(ls::Hex(Str(h.Final())) ==
          "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f");
    ed::Sha512 e;
    CHECK(ls::Hex(Str(e.Final())) ==
          "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e");
    ed::Sha512 m;  // 112 bytes: the padding takes a second block
    const std::string two = "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu";
    m.Update(two.data(), two.size());
    CHECK(ls::Hex(Str(m.Final())) ==
          "8e959b75dae313da8cf4f72814fc143f8f7779c6eb9f7fa17299aeadb6889018501d289e4900f7e4331b99dec4b5433ac7d329eeb6dd26545e96e55b874be909");
  }

  // RFC 8032 Ed25519
  for (const auto& v : kRfc8032) {
    const std::string seed = Unhex(v[0]), msg = Unhex(v[2]);
    const auto key = ls::SigningKey::FromSeed(seed);
    CHECK(ls::Hex(key.Public()) == v[1]);
    CHECK(ls::Hex(key.Sign(msg)) == v[3]);
  }

  // server names and the key for servers before signed requests
  CHECK(ls::ServerName(" Forza-Reimagined.Playit.Plus:56677 ") == "forza-reimagined.playit.plus");
  CHECK(ls::ServerName("[::1]:21200") == "::1");
  CHECK(ls::ServerName("host.example.") == "host.example");
  CHECK(ls::ServerName("127.0.0.1") == "127.0.0.1");
  CHECK(ls::IsMain("forza.dyotson.dev") && !ls::IsMain("my.server.example"));
  CHECK(ls::Hex(ls::OldServerKey(Bytes(16), "my.server.example")) == "d809345837c85fbdd0453daf311b4ef6");

  // a title service call (server/test_forza_auth.py VECTOR)
  const auto key = ls::SigningKey::FromSeed(Bytes(32));
  CHECK(ls::Hex(key.Public()) == "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8");
  CHECK(ls::HeaderValue(kHead, "x-classname") == "Forza.WebServices.User, Forza.WebServices");
  CHECK(ls::HeaderValue(kHead, "x-method") == "Logon2");
  CHECK(ls::HeaderValue(kHead, "x-missing").empty());
  const uint64_t xuid = 0x0009000000000001ull;
  const std::string nonce = "00112233445566778899aabbccddeeff";
  for (const auto& [name, sig] : {std::pair<std::string, std::string>{
           "forza-reimagined.playit.plus",
           "f24472663146a0d6964c5ceb1d631bb15cf0cdb9f126aad2ba43ac700c1202e70c60dd512228f2bf4140d904bb755ba331cbf24446622161fbeb9fa84d33c60f"},
       {"my.server.example",
        "a89b4d608e6287a5c3c43d237b06e3c2ac922591cbda795b79f7011eca8f03f1608d170debc4a5a023c099c1746314708b278945b3589a83225ee3f80a9b6e03"}}) {
    CHECK(ls::AuthHeaderLine(key, name, xuid, 1700000000, nonce, kHead, kBody) ==
          "X-Forza-Auth: FORZA-ED25519-V1 Server=" + name +
              ", Xuid=0009000000000001, Time=1700000000, Nonce=" + nonce + ", Signature=" + sig + "\r\n");
  }
  CHECK(ls::AuthHeaderLine(key, "x", xuid, 1, nonce, "garbage\r\n\r\n", "").empty());

  // the HELLO's auth block (forza_auth.hello_block with the same inputs)
  const std::string challenge(16, '\xaa'), signed_part = "hello body\x1f", friends_key = Bytes(16);
  {
    const std::string block = ls::HelloBlock(key, "forza-reimagined.playit.plus", xuid, 1700000000,
                                             challenge, signed_part, &friends_key);
    CHECK(ls::Hex(block) ==
          "a202000000006553f1001c666f727a612d7265696d6167696e65642e706c617969742e706c75737b5e17bd6e91d2"
          "ff66cedd8a22f0c833062a673e7bf8069cb824138bc90814c270314277e55052f9038ab31f9246b0db57e91d4183"
          "561be2e11d559be6a2c20603a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8000102"
          "030405060708090a0b0c0d0e0f");
  }
  {
    const std::string block = ls::HelloBlock(key, "my.server.example", xuid, 1700000000, challenge,
                                             signed_part, nullptr);
    CHECK(ls::Hex(block) ==
          "a200000000006553f100116d792e7365727665722e6578616d706c659d6b77030f3d6f015e926813f8e069bd06b5"
          "6e4057f561e7f5fdcd1e1717d2d35a6d992800b4a4249423acd0bbd7005288fd11f235ef0410986d9affb79d0704"
          "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8");
    // the longest name still fits the slot's u8 length, with an upgrade
    const std::string longest = ls::HelloBlock(key, std::string(ls::kMaxName, 'n'), xuid, 1, challenge,
                                               signed_part, &friends_key);
    CHECK(longest.size() <= 255);
  }

  // the WELCOME's auth part
  {
    ls::WelcomeAuth w;
    const std::string tail = Unhex("a20b000000006553f100") + challenge;
    CHECK(ls::ParseWelcomeAuth(reinterpret_cast<const uint8_t*>(tail.data()), tail.size(), w));
    CHECK(w.flags == (ls::kBound | ls::kProven | ls::kLegacy) && w.server_time == 1700000000 &&
          w.challenge == challenge);
    CHECK(!ls::ParseWelcomeAuth(nullptr, 0, w));
    CHECK(!ls::ParseWelcomeAuth(reinterpret_cast<const uint8_t*>(tail.data()), tail.size() - 1, w));
    const std::string old = Unhex("a10b000000006553f100") + challenge;  // the HMAC draft's tag
    CHECK(!ls::ParseWelcomeAuth(reinterpret_cast<const uint8_t*>(old.data()), old.size(), w));
  }

  // what signing costs (the game signs each title service call and a HELLO every 5 s)
  const auto t0 = std::chrono::steady_clock::now();
  for (int i = 0; i < 20; ++i) key.Sign("x");
  const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() / 20;
  std::printf("live_signing: OK (Ed25519 signature %.2f ms)\n", ms);
  return 0;
}
