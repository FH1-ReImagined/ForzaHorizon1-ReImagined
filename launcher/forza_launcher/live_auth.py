"""Signed requests to the Forza Live server (#515): no secret of the player is sent.

The shader list upload (shader_share.py) and the co-op results (coop_grant.py) prove the player
like the game does: an Ed25519 signature (ed25519.py) over the request, made with the
signing_seed of live_identity.txt, in X-Forza-Auth. The server keeps only the public key, which
the game registered at its sign-in (server/forza_auth.py has the scheme; the game's is
forza-recomp/src/kernel/live_signing.h):

- server name: the host of the server's address, lowercased, without the port;
- canonical string: TAG, method, path and query, server name, XUID, unix time, nonce,
  X-ClassName and X-Method (empty here), SHA-256 of the body, joined by newlines.

A server answers with its clock (X-Forza-Time): a refused request whose clock was off is sent
again once with the server's. A server before signed requests answers without it; for a server
other than the main one the request is sent again once with that server's own key
(X-Friends-Key, a key made from the friends key and the server's name), never the friends key
itself, and the main server gets nothing."""
import hashlib
import hmac
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import ed25519

TAG = "FORZA-ED25519-V1"
OLD_SERVER_LABEL = b"forza-live server key v1|"
MAIN_NAMES = ("forza-reimagined.playit.plus", "forza.dyotson.dev")
WINDOW = 120
_offsets = {}  # server name -> its clock minus ours (seconds)


def server_name(host: str) -> str:
    host = (host or "").strip().lower()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def old_server_key(friends_key: bytes, name: str) -> bytes:
    """What a server before signed requests gets in place of the friends key."""
    return hmac.new(bytes(friends_key), OLD_SERVER_LABEL + name.encode(), hashlib.sha256).digest()[:16]


def canonical(method, target, name, xuid_hex, when, nonce, body, classname="", xmethod="") -> bytes:
    return "\n".join((TAG, method, target, name, xuid_hex.upper(), str(int(when)), nonce, classname,
                      xmethod, hashlib.sha256(body or b"").hexdigest())).encode()


def headers(identity, method, url, body=b"", when=None, nonce=None, old_server=False) -> dict:
    """The headers that prove `identity` ((XUID, friends key, signing seed) in hex) for this
    request."""
    xuid, key, seed = identity
    parts = urllib.parse.urlsplit(url)
    name = server_name(parts.hostname or "")
    target = (parts.path or "/") + ("?" + parts.query if parts.query else "")
    when = int(time.time() + _offsets.get(name, 0)) if when is None else int(when)
    nonce = nonce or os.urandom(16).hex()
    sig = ed25519.sign(bytes.fromhex(seed), canonical(method, target, name, xuid, when, nonce, body))
    out = {"X-Xuid": xuid.upper(),
           "X-Forza-Auth": f"{TAG} Server={name}, Xuid={xuid.upper()}, Time={when}, Nonce={nonce}, "
                           f"Signature={sig.hex()}"}
    if old_server and name not in MAIN_NAMES:
        out["X-Friends-Key"] = old_server_key(bytes.fromhex(key), name).hex().upper()
    return out


def urlopen(url, identity, body=None, timeout=8, content_type="application/json"):
    """urllib.request.urlopen of a signed request (POST with a body, else GET), raising
    HTTPError like it. Sent again once after a refusal (401/403): with the server's clock when
    ours was off, or with this server's own key for a server before signed requests."""
    method = "POST" if body is not None else "GET"
    name = server_name(urllib.parse.urlsplit(url).hostname or "")
    old_server = False
    for attempt in range(2):
        req = urllib.request.Request(url, data=body, method=method, headers={
            "Content-Type": content_type, **headers(identity, method, url, body or b"", old_server=old_server)})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if attempt or e.code not in (401, 403):
                raise
            stamp = e.headers.get("X-Forza-Time") if e.headers else None
            if stamp:
                try:
                    offset = int(stamp) - time.time()
                except ValueError:
                    raise e from None
                if abs(offset - _offsets.get(name, 0)) <= WINDOW / 2:
                    raise  # not the clock
                _offsets[name] = offset
            elif name not in MAIN_NAMES:
                old_server = True
            else:
                raise
            e.close()
    raise AssertionError("unreachable")
