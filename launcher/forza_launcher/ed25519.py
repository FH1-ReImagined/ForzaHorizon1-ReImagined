"""Ed25519 signatures (RFC 8032), pure Python with the standard library only (#515).

The launcher signs its requests to the Forza Live server with it (live_auth.py). A copy of the
server's forza_ed25519.py (FH1-ReImagined/forza-live), which verifies them: keep the two the
same. The arithmetic is that of the reference implementation in RFC 8032, section 6, rearranged
for speed:

- points in extended coordinates (X, Y, Z, T) with the dedicated doubling dbl-2008-hwcd and the
  addition add-2008-hwcd-3 (Hisil, Wong, Carter, Dawson 2008);
- a table of j * 16^i * B (j < 16, i < 64) in affine "precomputed" form, built at the first use,
  so a multiple of the base point is at most 64 mixed additions;
- [k]A by 4-bit windows (252 doublings, at most 63 additions), and the check compares the
  encoding of [S]B - [k]A with R, which needs no decoding of R.

This code is not constant time: fine for checking signatures (all inputs are public) and for the
launcher's few signatures a session; the game signs with forza-recomp's own code (ed25519.h).

Derived from RFC 8032 (https://www.rfc-editor.org/rfc/rfc8032), whose code components are under
the Simplified BSD License:

    Copyright (c) 2017 IETF Trust and the persons identified as authors of the code. All rights
    reserved.

    Redistribution and use in source and binary forms, with or without modification, are
    permitted provided that the following conditions are met:
    - Redistributions of source code must retain the above copyright notice, this list of
      conditions and the following disclaimer.
    - Redistributions in binary form must reproduce the above copyright notice, this list of
      conditions and the following disclaimer in the documentation and/or other materials
      provided with the distribution.
    - Neither the name of Internet Society, IETF or IETF Trust, nor the names of specific
      contributors, may be used to endorse or promote products derived from this software
      without specific prior written permission.

    THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS
    OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
    MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
    EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
    GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
    ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
    NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
    OF THE POSSIBILITY OF SUCH DAMAGE.
"""

import functools
import hashlib

P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493  # the group order
D = -121665 * pow(121666, P - 2, P) % P
D2 = 2 * D % P
SQRT_M1 = pow(2, (P - 1) // 4, P)
IDENTITY = (0, 1, 1, 0)


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    return P - x if (x & 1) != sign else x


_BY = 4 * pow(5, P - 2, P) % P
_BX = _recover_x(_BY, 0)
BASE = (_BX, _BY, 1, _BX * _BY % P)


def _add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = t1 * D2 % P * t2 % P
    d = 2 * z1 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % P, g * h % P, f * g % P, e * h % P


def _double(p):
    x1, y1, z1, _ = p
    a = x1 * x1 % P
    b = y1 * y1 % P
    c = 2 * z1 * z1 % P
    s = x1 + y1
    e = (s * s - a - b) % P
    g = b - a
    f = g - c
    h = -a - b
    return e * f % P, g * h % P, f * g % P, e * h % P


def _madd(p, q):
    """p + q, q in precomputed form (y + x, y - x, 2 d x y) with Z = 1."""
    x1, y1, z1, t1 = p
    ypx, ymx, t2d = q
    a = (y1 - x1) * ymx % P
    b = (y1 + x1) * ypx % P
    c = t1 * t2d % P
    d = 2 * z1
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % P, g * h % P, f * g % P, e * h % P


def _encode(p):
    x, y, z, _ = p
    zi = pow(z, P - 2, P)
    x, y = x * zi % P, y * zi % P
    return (y | (x & 1) << 255).to_bytes(32, "little")


@functools.lru_cache(maxsize=4096)
def _decode(s):
    """The point of a 32-byte encoding, or None (not on the curve, or not canonical)."""
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return x, y, 1, x * y % P


_table = []


def _base_table():
    if not _table:
        point = BASE
        for _ in range(64):
            row, multiple = [None], IDENTITY
            for _ in range(15):
                multiple = _add(multiple, point)
                x, y, z, _t = multiple
                zi = pow(z, P - 2, P)
                x, y = x * zi % P, y * zi % P
                row.append(((y + x) % P, (y - x) % P, 2 * D * x * y % P))
            _table.append(row)
            for _ in range(4):
                point = _double(point)
    return _table


def _base_mult(k):
    """[k]B for 0 <= k < 2^256."""
    table, q = _base_table(), IDENTITY
    for i in range(64):
        j = k >> 4 * i & 15
        if j:
            q = _madd(q, table[i][j])
    return q


def _mult(p, k):
    """[k]p for 0 <= k < 2^256."""
    multiples = [IDENTITY, p]
    for _ in range(14):
        multiples.append(_add(multiples[-1], p))
    q = IDENTITY
    for i in range(63, -1, -1):
        q = _double(_double(_double(_double(q))))
        j = k >> 4 * i & 15
        if j:
            q = _add(q, multiples[j])
    return q


def _expand(seed):
    if len(seed) != 32:
        raise ValueError("an Ed25519 seed is 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little") & ((1 << 254) - 8) | (1 << 254)
    return a, h[32:]


def public_key(seed):
    """The 32-byte public key of a 32-byte seed (RFC 8032's secret key)."""
    return _encode(_base_mult(_expand(seed)[0]))


def sign(seed, message):
    """The 64-byte signature of `message` (bytes) by the key of `seed`."""
    a, prefix = _expand(seed)
    public = _encode(_base_mult(a))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % L
    big_r = _encode(_base_mult(r))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % L
    return big_r + ((r + k * a) % L).to_bytes(32, "little")


def verify(public, message, signature):
    """True when `signature` is the signature of `message` by `public` (cofactorless check:
    [S]B = R + [k]A, with a canonical R and S < L)."""
    if len(signature) != 64:
        return False
    a = _decode(bytes(public))
    s = int.from_bytes(signature[32:], "little")
    if a is None or s >= L:
        return False
    k = int.from_bytes(hashlib.sha512(signature[:32] + bytes(public) + message).digest(), "little") % L
    x, y, z, t = _mult(a, k)
    return _encode(_add(_base_mult(s), (P - x, y, z, P - t))) == signature[:32]
