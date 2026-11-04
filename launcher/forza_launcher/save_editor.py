"""Authenticated FH1 profile edits; game-specific key bytes come from the player's XEX.

The public format research is linked in docs/launcher.md. Only fixed-width values
in the self-describing prefix are edited; class-serialised state remains opaque, except
the four 1000 CLUB tutorial flags (CAR_CHALLENGE_TUTORIAL), set only after their entry parses.
"""
from dataclasses import dataclass, field
from functools import lru_cache, reduce
import hmac
import operator
import os
from pathlib import Path
import re
import struct
import uuid

from . import TITLE_ID, game, paths, saves

MAX_PROFILE = 16 * 1024**2
# Independent resource budgets: bound Python objects and cumulative UTF-8 paths,
# rather than recursion depth or field-name length. A scalar needs >=15 bytes.
MAX_PREFIX_ENTRIES = 32768
MAX_PREFIX_PATH_BYTES = 4 * 1024**2
FORMATS = {0: '?', 1: 'B', 3: 'I', 4: 'Q', 7: 'i', 9: 'f'}
# CareerWristbandLevels IDs in the supported game's database (not online ranks).
WRISTBANDS = tuple(enumerate(('Yellow', 'Green', 'Blue', 'Pink', 'Orange', 'Purple', 'Gold')))
EDITABLE = {'Main/Credits': (3, 0, 999_999_999), 'Main/XP': (7, 0, 2_147_483_647),
            'Main/WristbandLevel': (7, 0, len(WRISTBANDS)-1)}
# The 1000 CLUB tutorial (#237). The class-serialised entry CCarChallengeState (class of the
# same name) holds the 1000 CLUB's per-car challenge progress and ends with four step flags of
# its tutorial. Signed in to Forza Live, a save past the point where the 1000 CLUB opens starts
# the tutorial at every load until all four are set, and until then the car takes no input but
# START: no throttle, steering, camera or map. Finishing it in the game (START, 1000 CLUB, then
# the steps it shows) or here sets the flags.
CAR_CHALLENGE_TUTORIAL = 'CarChallenges/TutorialDone'
_CAR_CHALLENGES = (struct.pack('>I', 18) + b'CCarChallengeState') * 2


@dataclass(frozen=True)
class Value:
    offset: int
    kind: int
    value: object


def fields(body):
    """Walk the self-describing prefix iteratively, preserving exact scalar offsets."""
    if not 4 <= len(body) <= MAX_PROFILE:
        raise saves.SaveError('Invalid profile field section size.')
    data = memoryview(body)
    position, announced, path_bytes = 0, 0, 0
    found = {}

    def reserve(size):
        nonlocal position
        if size > len(data) - position:
            raise saves.SaveError('The profile has a truncated field section.')
        start = position
        position += size
        return start

    def count_entries():
        nonlocal announced
        count = struct.unpack_from('>I', data, reserve(4))[0]
        # Reject impossible counts before looping or allocating per-entry state.
        if count > (len(data) - position) // 15:
            raise saves.SaveError('The profile has a truncated field section.')
        announced += count
        if announced > MAX_PREFIX_ENTRIES:
            raise saves.SaveError('The profile field section exceeds the entry budget.')
        return count

    # A frame retains a prefix, its byte size, unvisited count and sibling names.
    # Children are visited in stream order; completed frames are discarded.
    stack = [['', 0, count_entries(), set()]]
    while stack:
        frame = stack[-1]
        if frame[2] == 0:
            stack.pop()
            continue
        frame[2] -= 1
        length = struct.unpack_from('>I', data, reserve(4))[0]
        if not length:
            raise saves.SaveError('Invalid profile field name.')
        path_size = frame[1] + length
        path_bytes += path_size
        if path_bytes > MAX_PREFIX_PATH_BYTES:
            raise saves.SaveError('The profile field section exceeds the path budget.')
        start = reserve(length)
        try:
            name = bytes(data[start:position]).decode('utf-8')
        except UnicodeError as exc:
            raise saves.SaveError('Invalid profile field name.') from exc
        if '/' in name or '\0' in name or name in frame[3]:
            raise saves.SaveError('Duplicate or invalid profile field name.')
        frame[3].add(name)
        tag, reserved, kind = struct.unpack_from('>IIB', data, reserve(9))
        if (tag, reserved) != (0x20, 0):
            raise saves.SaveError('Unsupported profile field header.')
        path = frame[0] + name
        if kind == 15:
            stack.append([path+'/', path_size+1, count_entries(), set()])
            continue
        if kind not in FORMATS:
            raise saves.SaveError(f'Unsupported profile field type: {kind}.')
        offset = reserve(struct.calcsize(FORMATS[kind]))
        if kind == 0 and data[offset] not in (0, 1):
            raise saves.SaveError('Invalid profile boolean.')
        found[path] = Value(offset, kind, struct.unpack_from('>'+FORMATS[kind], data, offset)[0])

    for path, (kind, _low, _high) in EDITABLE.items():
        if path not in found or found[path].kind != kind:
            raise saves.SaveError('This is not a supported FH1 career profile.')
    tutorial = car_challenge_tutorial(body, position)
    if tutorial is not None:
        found[CAR_CHALLENGE_TUTORIAL] = tutorial
    return found


def car_challenge_tutorial(body, start=0):
    """The 1000 CLUB tutorial flags as Value(offset, 0, finished), or None.

    The entry's payload is a car count, then per car its ID, a challenge count and per
    challenge an ID, a value and a length-prefixed extra, then the four flags. Anything that
    does not parse to exactly that (or a second entry) is left alone."""
    first = body.find(_CAR_CHALLENGES, start)
    if first < 0 or body.find(_CAR_CHALLENGES, first+1) >= 0:
        return None
    position = first+len(_CAR_CHALLENGES)
    try:
        _version, size, cars = struct.unpack_from('>III', body, position)
        end = position+8+size
        position += 12
        if end > len(body) or cars > 10000:
            return None
        for _ in range(cars):
            _car, count = struct.unpack_from('>II', body, position)
            position += 8
            if count > 10000:
                return None
            for _ in range(count):
                position += 10+struct.unpack_from('>H', body, position+8)[0]
                if position > end:
                    return None
    except struct.error:
        return None
    flags = body[position:end]
    if position+4 != end or any(flag > 1 for flag in flags):
        return None
    return Value(position, 0, all(flags))


def _aes_tools():
    paths.ensure_tools_importable()
    import apply_title_update
    return apply_title_update


def _encrypt_cbc(key, data, iv):
    """AES-128 CBC, no padding. Same stdlib-only fallback as the XEX decoder."""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return cipher.update(data) + cipher.finalize()
    except ImportError:
        pass
    aes = _aes_tools()
    sbox, xt = aes._SBOX, aes._xt
    schedule = list(key)
    rcon = 1
    while len(schedule) < 176:
        word = schedule[-4:]
        if len(schedule) % 16 == 0:
            word = [sbox[b] for b in word[1:]+word[:1]]
            word[0] ^= rcon
            rcon = xt(rcon)
        for byte in word:
            schedule.append(schedule[-16] ^ byte)
    out, previous = bytearray(), iv
    for start in range(0, len(data), 16):
        state = [a ^ b ^ c for a, b, c in zip(data[start:start+16], previous, schedule[:16])]
        for round_number in range(1, 11):
            state = [sbox[state[4*((col+row)%4)+row]] for col in range(4) for row in range(4)]
            if round_number < 10:
                for col in range(0, 16, 4):
                    a, b, c, d = state[col:col+4]
                    xor = a ^ b ^ c ^ d
                    state[col:col+4] = [a ^ xor ^ xt(a ^ b), b ^ xor ^ xt(b ^ c),
                                        c ^ xor ^ xt(c ^ d), d ^ xor ^ xt(d ^ a)]
            state = [b ^ k for b, k in zip(state, schedule[16*round_number:16*(round_number+1)])]
        previous = bytes(state)
        out.extend(previous)
    return bytes(out)


@lru_cache(maxsize=4)
def _tables(path, size, modified):
    """Cache key tables in memory only; no game bytes or decrypted saves are persisted."""
    if size > 128 * 1024**2:
        raise saves.SaveError('The game executable is too large to read safely.')
    raw = Path(path).read_bytes()
    from .xexcheck import header_info
    info = header_info(raw)
    if not info or info[3] != int(TITLE_ID, 16):
        raise saves.SaveError('Choose the FH1 game files in Setup before editing a save.')
    try:
        xex = _aes_tools().Xex(raw)
        if xex.image_size > 128 * 1024**2 or xex.pages_size() > 128 * 1024**2:
            raise ValueError('Unsupported executable image size')
        image = xex.image()
    except (SystemExit, ValueError, struct.error) as exc:
        raise saves.SaveError('Could not read the profile key tables from this game executable.') from exc
    # Locations of the profile's AES/HMAC tables in the supported disc and TU4
    # images. These are addresses, not key material. A matching save MAC is required.
    return tuple(bytes(image[address-xex.load_address:address-xex.load_address+64])
                 for address in (0x8200D634, 0x82013A04)
                 if 0 <= address-xex.load_address <= len(image)-64)


def _owner(cfg):
    owner = saves._xuid(cfg) if cfg.profile['mode'] == 'xenia' else game.LOCAL_PROFILE
    args = game.split_args(cfg.advanced.get('extra_args', ''))
    for i, arg in enumerate(args):
        if arg.startswith('--profile_xuid='):
            owner = arg.split('=', 1)[1]
        elif arg == '--profile_xuid' and i+1 < len(args):
            owner = args[i+1]
    if not re.fullmatch('[0-9a-fA-F]{16}', owner):
        raise saves.SaveError('Choose a valid profile XUID before editing a save.')
    return bytes.fromhex(owner)


def _keys(cfg):
    executable = cfg.game_files()/'default.xex'
    try:
        info = executable.stat()
    except OSError as exc:
        raise saves.SaveError('Choose your FH1 game files in Setup before opening the editor.') from exc
    owner = _owner(cfg)
    material = _tables(str(executable.resolve()), info.st_size, info.st_mtime_ns)
    return tuple((bytes(((table[i] ^ owner[(i-2)%8])+2*i) % 256 for i in range(16)),
                  bytes(((table[32+i] ^ owner[(i-4)%8])+2*i) % 256 for i in range(32)))
                 for table in material)


@dataclass(frozen=True)
class Document:
    profile: saves.Profile
    original: bytes = field(repr=False)
    body: bytes = field(repr=False)
    padding: bytes = field(repr=False)
    aes_key: bytes = field(repr=False)
    mac_key: bytes = field(repr=False)
    values: dict


@dataclass(frozen=True)
class EditResult:
    backup: saves.Snapshot | None
    document: Document


def decode(profile, raw, keys):
    if not 76 <= len(raw) <= MAX_PROFILE or raw[:4] != b'cmss':
        raise saves.SaveError('Unsupported or damaged ForzaProfile file.')
    used = struct.unpack_from('>I', raw, 4)[0]
    if not 68 <= used <= len(raw)-8:
        raise saves.SaveError('Invalid profile stream length.')
    payload = raw[40:8+used]
    length = reduce(operator.xor, struct.unpack_from('>5I', payload))
    if not 4 <= length <= MAX_PROFILE or 52 + ((length+15)//16)*16 != used:
        raise saves.SaveError('Invalid encrypted profile length.')
    for aes_key, mac_key in keys:
        if not hmac.compare_digest(hmac.digest(mac_key, payload, 'sha256'), raw[8:40]):
            continue
        cipher = payload[20:]
        ecb = _aes_tools().aes_decrypt(aes_key, cipher, cbc=False)
        plain = bytes(a ^ b for a, b in zip(ecb, payload[4:20]+cipher[:-16]))
        body = plain[:length]
        return Document(profile, raw, body, plain[length:], aes_key, mac_key, fields(body))
    raise saves.SaveError('The profile integrity check failed. It may be damaged, belong to a different '
                          'XUID, or use an unsupported game version. Nothing was changed.')


def _read(profile):
    path = saves._inside(profile.folder/'ForzaProfile/ForzaProfile', profile.root)
    if path.stat().st_size > MAX_PROFILE:
        raise saves.SaveError('The profile is too large to edit safely.')
    return path.read_bytes()


def load(cfg, profile, cancelled=lambda: False):
    with saves.locked(profile):
        saves._recover(profile)
        saves._check(cancelled)
        raw = _read(profile)
        keys = _keys(cfg)
        saves._check(cancelled)
        return decode(profile, raw, keys)


def encode(document, changes):
    body = bytearray(document.body)
    for path, value in changes.items():
        if path == CAR_CHALLENGE_TUTORIAL:
            saved = document.values.get(path)
            if saved is None or value is not True:
                raise saves.SaveError('This profile has no 1000 CLUB tutorial to finish.')
            body[saved.offset:saved.offset+4] = b'\1\1\1\1'
            continue
        if path not in EDITABLE:
            raise saves.SaveError('This profile value cannot be edited.')
        kind, low, high = EDITABLE[path]
        if type(value) is not int or not low <= value <= high:
            raise saves.SaveError(f'{path.split("/")[-1]} must be between {low:,} and {high:,}.')
        saved = document.values[path]
        if saved.kind != kind:
            raise saves.SaveError('The profile field type changed.')
        struct.pack_into('>'+FORMATS[kind], body, saved.offset, value)
    seed = document.original[40:60]
    cipher = _encrypt_cbc(document.aes_key, bytes(body)+document.padding, seed[4:])
    payload = seed+cipher
    used = len(payload)+32
    raw = document.original[:8]+hmac.digest(document.mac_key, payload, 'sha256')+payload+document.original[8+used:]
    checked = decode(document.profile, raw, ((document.aes_key, document.mac_key),))
    if checked.body != body or len(raw) != len(document.original):
        raise saves.SaveError('The edited profile did not pass verification.')
    return raw


def apply(document, changes, cancelled=lambda: False, progress=lambda *_: None):
    profile = document.profile
    with saves.locked(profile):
        saves._recover(profile)
        saves._check(cancelled)
        raw = _read(profile)
        if raw != document.original:
            raise saves.SaveError('The save changed after the editor was opened. Reload it before editing.')
        edited = encode(document, changes)
        if edited == raw:
            return EditResult(None, document)
        backup = saves._create(profile, 'Before profile edit', 'before-edit', cancelled, progress, 10)
        saves._check(cancelled)
        if _read(profile) != raw:
            raise saves.SaveError('The save changed during backup. Reload it before editing.')
        path = saves._inside(profile.folder/'ForzaProfile/ForzaProfile', profile.root)
        temporary = path.with_name('.profile-edit-'+uuid.uuid4().hex)
        try:
            with temporary.open('xb') as writer:
                writer.write(edited)
                writer.flush()
                os.fsync(writer.fileno())
            saves._check(cancelled)
            if _read(profile) != raw:
                raise saves.SaveError('The save changed before applying the edit. Reload it first.')
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return EditResult(backup, decode(profile, edited, ((document.aes_key, document.mac_key),)))
