"""XXH3-64 (seed 0, default secret) in pure Python.

The SDK keys stored shaders by XXH3_64bits() of the guest (big-endian) microcode
and stored pipelines by XXH3_64bits() of the packed description. The shader
prebuild tools need the same hashes without a native dependency, so this is a
direct port of the scalar code path of xxHash 0.8 (thirdparty/xxHash/xxhash.h).
Speed is a few MB/s, enough for the game's 22 MB of effect files.
"""

# xxHash: Copyright (c) 2012-2021 Yann Collet. All rights reserved.
# BSD 2-Clause License (https://www.opensource.org/licenses/bsd-license.php):
#
# Redistribution and use in source and binary forms, with or without modification, are
# permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this list of
#   conditions and the following disclaimer.
# * Redistributions in binary form must reproduce the above copyright notice, this list of
#   conditions and the following disclaimer in the documentation and/or other materials
#   provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
# GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
# OF THE POSSIBILITY OF SUCH DAMAGE.

import struct

M64 = (1 << 64) - 1
P32_1, P32_2, P32_3 = 0x9E3779B1, 0x85EBCA77, 0xC2B2AE3D
P64_1, P64_2, P64_3 = 0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9
P64_4, P64_5 = 0x85EBCA77C2B2AE63, 0x27D4EB2F165667C5
PRIME_MX1, PRIME_MX2 = 0x165667919E3779F9, 0x9FB21C651E98DF25

SECRET = bytes.fromhex(
    "b8fe6c3923a44bbe7c01812cf721ad1cded46de9839097db7240a4a4b7b3671f"
    "cb79e64eccc0e578825ad07dccff7221b8084674f743248ee03590e6813a264c"
    "3c2852bb91c300cb88d0658b1b532ea371644897a20df94e3819ef46a9deacd8"
    "a8fa763fe39c343ff9dcbbc7c70b4f1d8a51e04bcdb45931c89f7ec9d9787364"
    "eac5ac8334d3ebc3c581a0fffa1363eb170ddd51b7f0da49d316552629d4689e"
    "2b16be587d47a1fc8ff8b8d17ad031ce45cb3a8f95160428afd7fbcabb4b407e")


def _r64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def _r32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _mul_fold(a, b):
    p = a * b
    return (p ^ (p >> 64)) & M64


def _avalanche(h):
    h ^= h >> 37
    h = (h * PRIME_MX1) & M64
    return h ^ (h >> 32)


def _xxh64_avalanche(h):
    h ^= h >> 33
    h = (h * P64_2) & M64
    h ^= h >> 29
    h = (h * P64_3) & M64
    return h ^ (h >> 32)


def _rotl(x, r):
    return ((x << r) | (x >> (64 - r))) & M64


def _mix16(data, o, so):
    return _mul_fold(_r64(data, o) ^ _r64(SECRET, so), _r64(data, o + 8) ^ _r64(SECRET, so + 8))


def xxh3_64(data):
    data = bytes(data)
    n = len(data)
    s = SECRET
    if n <= 16:
        if n > 8:
            lo = _r64(data, 0) ^ (_r64(s, 24) ^ _r64(s, 32))
            hi = _r64(data, n - 8) ^ (_r64(s, 40) ^ _r64(s, 48))
            swapped = int.from_bytes(lo.to_bytes(8, "little"), "big")
            return _avalanche((n + swapped + hi + _mul_fold(lo, hi)) & M64)
        if n >= 4:
            i64 = (_r32(data, n - 4) + (_r32(data, 0) << 32)) & M64
            h = i64 ^ (_r64(s, 8) ^ _r64(s, 16))
            h ^= _rotl(h, 49) ^ _rotl(h, 24)
            h = (h * PRIME_MX2) & M64
            h ^= (h >> 35) + n
            h = (h * PRIME_MX2) & M64
            return h ^ (h >> 28)
        if n:
            comb = (data[0] << 16) | (data[n >> 1] << 24) | data[n - 1] | (n << 8)
            return _xxh64_avalanche(comb ^ (_r32(s, 0) ^ _r32(s, 4)))
        return _xxh64_avalanche(_r64(s, 56) ^ _r64(s, 64))
    if n <= 128:
        acc = (n * P64_1) & M64
        if n > 32:
            if n > 64:
                if n > 96:
                    acc += _mix16(data, 48, 96) + _mix16(data, n - 64, 112)
                acc += _mix16(data, 32, 64) + _mix16(data, n - 48, 80)
            acc += _mix16(data, 16, 32) + _mix16(data, n - 32, 48)
        acc += _mix16(data, 0, 0) + _mix16(data, n - 16, 16)
        return _avalanche(acc & M64)
    if n <= 240:
        acc = (n * P64_1) & M64
        for i in range(8):
            acc += _mix16(data, 16 * i, 16 * i)
        acc_end = _mix16(data, n - 16, 136 - 17)
        acc = _avalanche(acc & M64)
        for i in range(8, n // 16):
            acc_end += _mix16(data, 16 * i, 16 * (i - 8) + 3)
        return _avalanche((acc + acc_end) & M64)
    return _hash_long(data, n)


def _hash_long(data, n):
    s = SECRET
    acc = [P32_3, P64_1, P64_2, P64_3, P64_4, P32_2, P64_5, P32_1]
    stripes_per_block = (len(s) - 64) // 8
    block_len = 64 * stripes_per_block
    nb_blocks = (n - 1) // block_len
    skeys = [struct.unpack_from("<8Q", s, 8 * i) for i in range(stripes_per_block)]
    last_keys = struct.unpack_from("<8Q", s, len(s) - 64 - 7)
    scramble = struct.unpack_from("<8Q", s, len(s) - 64)

    def accumulate(off, keys):
        vals = struct.unpack_from("<8Q", data, off)
        for lane in range(8):
            v = vals[lane]
            k = v ^ keys[lane]
            acc[lane ^ 1] = (acc[lane ^ 1] + v) & M64
            acc[lane] = ((k & 0xFFFFFFFF) * (k >> 32) + acc[lane]) & M64

    for b in range(nb_blocks):
        for i in range(stripes_per_block):
            accumulate(b * block_len + 64 * i, skeys[i])
        for lane in range(8):
            a = acc[lane]
            a ^= a >> 47
            a ^= scramble[lane]
            acc[lane] = (a * P32_1) & M64
    stripes = ((n - 1) - block_len * nb_blocks) // 64
    for i in range(stripes):
        accumulate(nb_blocks * block_len + 64 * i, skeys[i])
    accumulate(n - 64, last_keys)
    result = (n * P64_1) & M64
    for i in range(4):
        so = 11 + 16 * i
        result += _mul_fold(acc[2 * i] ^ _r64(s, so), acc[2 * i + 1] ^ _r64(s, so + 8))
    return _avalanche(result & M64)
