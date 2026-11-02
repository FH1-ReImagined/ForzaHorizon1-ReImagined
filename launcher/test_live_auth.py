"""Signed requests to the Forza Live server (live_auth.py, ed25519.py, #515): the launcher's shader
list upload and co-op results are signed with the identity's Ed25519 key and never carry the
friends key or the seed. RFC 8032's vectors, and the vector the server (server/test_forza_auth.py)
and the game (tools/test_live_signing.cpp) check too. No network: urlopen is a stand-in."""
import email.message
import hashlib
import io
import sys
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import ed25519, live_auth  # noqa: E402

KEY = bytes(range(16)).hex().upper()
SEED = bytes(range(32)).hex().upper()
XUID = "0009000000000001"
IDENTITY = (XUID, KEY, SEED)
NONCE = "00112233445566778899aabbccddeeff"
MAIN = "forza-reimagined.playit.plus"
PUB = "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"

RFC8032 = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
    ("833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42",
     "ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf",
     "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f",
     "dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704"),
]


class Reply:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return b"{}"


def refusal(url, stamp=None):
    hdrs = email.message.Message()
    if stamp is not None:
        hdrs["X-Forza-Time"] = str(stamp)
    return urllib.error.HTTPError(url, 403, "Forbidden", hdrs, io.BytesIO(b'{"error": "unknown player"}'))


class SchemeTest(unittest.TestCase):
    def setUp(self):
        live_auth._offsets.clear()

    def test_rfc8032(self):
        for seed, pub, msg, sig in RFC8032:
            seed, pub, msg, sig = map(bytes.fromhex, (seed, pub, msg, sig))
            self.assertEqual(ed25519.public_key(seed), pub)
            self.assertEqual(ed25519.sign(seed, msg), sig)
            self.assertTrue(ed25519.verify(pub, msg, sig))

    def test_the_shared_vector(self):
        """The server's and the game's vector: the same canonical string and signatures."""
        self.assertEqual(ed25519.public_key(bytes.fromhex(SEED)).hex(), PUB)
        body = b"\x00\x01\x02"
        for name, want in ((MAIN, "f24472663146a0d6964c5ceb1d631bb15cf0cdb9f126aad2ba43ac700c1202e70c60dd512228f2bf4140d904bb755ba331cbf24446622161fbeb9fa84d33c60f"),
                           ("my.server.example", "a89b4d608e6287a5c3c43d237b06e3c2ac922591cbda795b79f7011eca8f03f1608d170debc4a5a023c099c1746314708b278945b3589a83225ee3f80a9b6e03")):
            text = live_auth.canonical("POST", "/Services.Horizon/o.xtsw", name, XUID, 1700000000, NONCE, body,
                                       "Forza.WebServices.User, Forza.WebServices", "Logon2")
            self.assertEqual(ed25519.sign(bytes.fromhex(SEED), text).hex(), want)
        self.assertEqual(live_auth.old_server_key(bytes.fromhex(KEY), "my.server.example").hex(),
                         "d809345837c85fbdd0453daf311b4ef6")

    def test_headers(self):
        h = live_auth.headers(IDENTITY, "POST", f"http://{MAIN.upper()}:56678/shader-lists/upload", b"{}",
                              when=1700000000, nonce=NONCE)
        self.assertEqual(h["X-Xuid"], XUID)
        self.assertTrue(h["X-Forza-Auth"].startswith(
            f"FORZA-ED25519-V1 Server={MAIN}, Xuid={XUID}, Time=1700000000, Nonce={NONCE}, Signature="))
        text = live_auth.canonical("POST", "/shader-lists/upload", MAIN, XUID, 1700000000, NONCE, b"{}")
        sig = bytes.fromhex(h["X-Forza-Auth"].split("Signature=")[1])
        self.assertTrue(ed25519.verify(bytes.fromhex(PUB), text, sig))
        self.assertNotIn("X-Friends-Key", h)
        # the query is signed with the path
        q = live_auth.headers(IDENTITY, "GET", "http://[::1]:21201/coop/results?x=1", b"", when=1, nonce=NONCE)
        self.assertIn("Server=::1,", q["X-Forza-Auth"])

    def test_names(self):
        self.assertEqual(live_auth.server_name("Forza.Dyotson.Dev:21200"), "forza.dyotson.dev")


class RequestTest(unittest.TestCase):
    def setUp(self):
        live_auth._offsets.clear()
        self.sent = []

    def run_with(self, answers, url):
        def fake(req, timeout=None):
            self.sent.append(dict(req.header_items()))
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with mock.patch.object(live_auth.urllib.request, "urlopen", fake):
            with live_auth.urlopen(url, IDENTITY, b"{}", timeout=5) as r:
                return r.read()

    def secrets_in(self, headers):
        text = " ".join(headers.values()).upper()
        return [s for s in (KEY, SEED, hashlib.sha256(bytes.fromhex(KEY)).hexdigest().upper()) if s in text]

    def test_no_secret_is_sent(self):
        url = f"http://{MAIN}:56678/shader-lists/upload"
        self.assertEqual(self.run_with([Reply()], url), b"{}")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.secrets_in(self.sent[0]), [])

    def test_a_wrong_clock_is_corrected_once(self):
        url = f"http://{MAIN}:56678/coop/results/applied"
        server_time = int(time.time()) + 3600
        self.run_with([refusal(url, server_time), Reply()], url)
        times = [int(h["X-forza-auth"].split("Time=")[1].split(",")[0]) for h in self.sent]
        self.assertLess(abs(times[0] - time.time()), 5)
        self.assertLess(abs(times[1] - server_time), 5)
        # a refusal with the right clock is not sent again
        self.sent.clear()
        with self.assertRaises(urllib.error.HTTPError):
            self.run_with([refusal(url, int(time.time()) + 3600)], url)
        self.assertEqual(len(self.sent), 1)

    def test_an_old_server_gets_its_own_key_never_the_friends_key(self):
        url = "http://my.server.example:21201/shader-lists/upload"
        self.run_with([refusal(url), Reply()], url)
        derived = live_auth.old_server_key(bytes.fromhex(KEY), "my.server.example").hex().upper()
        self.assertEqual([h.get("X-friends-key") for h in self.sent], [None, derived])
        self.assertEqual([self.secrets_in(h) for h in self.sent], [[], []])
        # the main server: never
        self.sent.clear()
        url = f"http://{MAIN}:56678/shader-lists/upload"
        with self.assertRaises(urllib.error.HTTPError):
            self.run_with([refusal(url)], url)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("X-friends-key", self.sent[0])


if __name__ == "__main__":
    unittest.main()
