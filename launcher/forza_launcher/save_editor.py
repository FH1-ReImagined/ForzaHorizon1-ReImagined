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
TABLE_LEAD = 32  # key table windows (_tables) start this far before each table
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
    """Parse the bounded prefix completely; retain the unknown trailing bytes unchanged."""
    return _fields(body)[0]


def _fields(body):
    """fields(body) and the offset where the field section ends."""
    position, total, found = 0, 0, {}

    def take(count):
        nonlocal position
        if count < 0 or position + count > len(body):
            raise saves.SaveError('The profile has a truncated field section.')
        data = body[position:position+count]
        position += count
        return data

    def u32():
        return struct.unpack('>I', take(4))[0]

    def section(prefix='', depth=0):
        nonlocal total
        count = u32()
        total += count
        if depth > 8 or total > 10000:
            raise saves.SaveError('The profile field section is too complex.')
        for _ in range(count):
            length = u32()
            if not 0 < length <= 256:
                raise saves.SaveError('Invalid profile field name.')
            try:
                name = take(length).decode('utf-8')
            except UnicodeError as exc:
                raise saves.SaveError('Invalid profile field name.') from exc
            if '/' in name or '\0' in name:
                raise saves.SaveError('Invalid profile field name.')
            path = prefix + name
            if path in found or take(8) != struct.pack('>II', 0x20, 0):
                raise saves.SaveError('Duplicate or unsupported profile field.')
            kind = take(1)[0]
            found[path] = None
            if kind == 15:
                section(path+'/', depth+1)
            elif kind in FORMATS:
                offset = position
                raw = take(struct.calcsize(FORMATS[kind]))
                if kind == 0 and raw not in (b'\0', b'\1'):
                    raise saves.SaveError('Invalid profile boolean.')
                found[path] = Value(offset, kind, struct.unpack('>'+FORMATS[kind], raw)[0])
            else:
                raise saves.SaveError(f'Unsupported profile field type: {kind}.')

    section()
    for path, (kind, _low, _high) in EDITABLE.items():
        if path not in found or found[path] is None or found[path].kind != kind:
            raise saves.SaveError('This is not a supported FH1 career profile.')
    values = {path: value for path, value in found.items() if value is not None}
    tutorial = car_challenge_tutorial(body, position)
    if tutorial is not None:
        values[CAR_CHALLENGE_TUTORIAL] = tutorial
    return values, position


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
    # Each window starts TABLE_LEAD bytes before its table: a profile the base game wrote
    # (VersionFlags 0, before TU4 converts it) is signed with the 32 bytes before the table
    # (the same in both images), a TU4 profile with the table's second half.
    return tuple(bytes(image[address-xex.load_address-TABLE_LEAD:address-xex.load_address+64])
                 for address in (0x8200D634, 0x82013A04)
                 if TABLE_LEAD <= address-xex.load_address <= len(image)-64)


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
    keys = []
    for window in material:
        table = window[TABLE_LEAD:]
        aes = bytes(((table[i] ^ owner[(i-2)%8])+2*i) % 256 for i in range(16))
        # (aes, mac) of a TU4 profile, then of a base-game one: the same AES key, the HMAC
        # key from the table's second half or from the 32 bytes before it
        for mac_table in (table[32:64], window[:TABLE_LEAD]):
            keys.append((aes, bytes(((mac_table[i] ^ owner[(i-4)%8])+2*i) % 256 for i in range(32))))
    return tuple(keys)


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
        backup = _replace(profile, raw, edited, 'Before profile edit', cancelled, progress)
        return EditResult(backup, decode(profile, edited, ((document.aes_key, document.mac_key),)))


def _replace(profile, raw, edited, label, cancelled, progress):
    """Back the save up, then put `edited` in place of ForzaProfile if it still holds `raw`."""
    backup = saves._create(profile, label, 'before-edit', cancelled, progress, 10)
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
    return backup


# FM4 circuits in the profile (#850). A start on an imported FM4 circuit (#648) makes the game
# keep a CHorizonLocationState for that circuit in ForzaProfile (its gas stations, flyers, barn
# finds and road discovery, about 8 KB), next to Colorado's. ForzaProfile is a fixed container
# (51,200 bytes of stream in a 51,208-byte file): the game's writer cuts what does not fit without
# an error, the cut file fails its integrity check and the next start stops right after the title
# screen. A played Colorado and three circuits fit; the fourth circuit does not. So before each
# start the launcher takes the circuits' states out again (after a backup), keeping only the one
# of the circuit being started, and gives a file the game already cut its readable form back.
FH1_LOCATIONS = frozenset(('Colorado', 'ColoradoDirt'))
LOCATION_STATE = 'CHorizonLocationState'
STATE_TRAILER = struct.pack('>II', 2, 0)
# Top-level entries whose names sort after the circuits' (the game writes them by name), with
# their data in a profile that has not played them: what a cut through a circuit's state loses.
LOST_DEFAULTS = {'TradingPaintChallenge': ('CSkillsFreeRoamChallengeState', 1, b'\0'*5),
                 'WindFarmToNWCableCarStationBeaumontDaytime': ('CBasicChallengeState', 1, b'\0')}
DAMAGED = ('Your save\'s profile (ForzaProfile) does not pass the game\'s integrity check, so the game '
           'may close right after the title screen. In Library > Saves, restore a backup from before '
           'it happened (Your backups, then Restore selected).')


@dataclass(frozen=True)
class CircuitTrim:
    removed: tuple          # names of the circuits' states taken out
    repaired: bool          # the file was cut at its container and is readable again
    backup: saves.Snapshot | None
    warning: str = ''       # nothing was changed; the player should know this

    def note(self):
        if self.warning:
            return self.warning
        if self.repaired:
            return ('Repaired your save: the FM4 circuits had filled its profile, which stopped the game '
                    'after the title screen. A backup of the damaged save is in Library > Saves.')
        return ''


def _states(body, salvaged=False):
    """The top-level class-serialised entries after the field section.

    Returns (start, lead, entries, rest, cut): entries are (name, class, version, data); rest is
    what follows the last entry. In a `salvaged` body (the readable start of a profile the game
    cut) cut is the (name, class) of the entry the end cuts through, as far as it is there, or
    ('', '') after the last entry; otherwise it is None and a short body is an error."""
    _values, start = _fields(body)
    lead, count = struct.unpack_from('>II', body, start) if start + 8 <= len(body) else (0, 0)
    if start + 8 > len(body) or count > 10000:
        raise saves.SaveError('Unsupported profile class state.')
    position, entries = start + 8, []

    def text(at):
        size = struct.unpack_from('>I', body, at)[0]
        if not 0 < size <= 256:
            raise saves.SaveError('Unsupported profile class state.')
        if at + 4 + size > len(body):
            raise struct.error('cut')
        try:
            return body[at+4:at+4+size].decode('ascii'), at + 4 + size
        except UnicodeError as exc:
            raise saves.SaveError('Unsupported profile class state.') from exc

    for _ in range(count):
        name = cls = ''
        try:
            name, at = text(position)
            cls, at = text(at)
            version, size = struct.unpack_from('>II', body, at)
            if at + 8 + size > len(body):
                raise struct.error('cut')
        except struct.error as exc:
            if not salvaged:
                raise saves.SaveError('The profile has a truncated class state.') from exc
            return start, lead, entries, b'', (name, cls)
        entries.append((name, cls, version, body[at+8:at+8+size]))
        position = at + 8 + size
    rest = body[position:]
    if salvaged:
        return start, lead, entries, b'', ('', '')
    return start, lead, entries, rest, None


def _salvage(raw, keys):
    """A profile the game cut at its container: (body, aes, mac) of what is left, or None."""
    used = struct.unpack_from('>I', raw, 4)[0] if len(raw) >= 76 and raw[:4] == b'cmss' else 0
    if used != len(raw) - 8:
        return None  # not at its container: damaged some other way
    payload = raw[40:8+used]
    length = reduce(operator.xor, struct.unpack_from('>5I', payload))
    cipher = payload[20:20+(len(payload)-20)//16*16]
    if not len(cipher) < length <= MAX_PROFILE:
        return None
    for aes_key, mac_key in keys:
        ecb = _aes_tools().aes_decrypt(aes_key, cipher, cbc=False)
        body = bytes(a ^ b for a, b in zip(ecb, payload[4:20]+cipher[:-16]))
        try:
            _fields(body)
        except saves.SaveError:
            continue
        return body, aes_key, mac_key
    return None


def _seal(raw, body, aes_key, mac_key):
    """`body` encrypted and signed into a container the size of `raw`, with raw's IV."""
    iv = raw[44:60]
    seed = struct.pack('>I', len(body) ^ reduce(operator.xor, struct.unpack('>4I', iv))) + iv
    payload = seed + _encrypt_cbc(aes_key, body + b'\xbb'*(-len(body) % 16), iv)
    sealed = b'cmss' + struct.pack('>I', len(payload)+32) + hmac.digest(mac_key, payload, 'sha256') + payload
    if len(sealed) > len(raw):
        raise saves.SaveError('The profile does not fit its file.')
    sealed += b'\0' * (len(raw) - len(sealed))
    if decode(None, sealed, ((aes_key, mac_key),)).body != body:
        raise saves.SaveError('The rebuilt profile did not pass verification.')
    return sealed


def trimmed_states(body, keep='', salvaged=False):
    """`body` without the FM4 circuits' location states (but `keep`'s); a `salvaged` (cut) one
    made whole again. Returns (new body, removed names, repaired) or None when nothing changes."""
    start, lead, entries, rest, cut = _states(body, salvaged)
    removed = tuple(n for n, c, _v, _d in entries
                    if c == LOCATION_STATE and n not in FH1_LOCATIONS and n.lower() != keep.lower())
    if cut is not None:
        name, cls = cut
        # the cut must fall in a circuit's state (dropped whole) or after the last entry
        if (name or cls) and (cls != LOCATION_STATE or name in FH1_LOCATIONS):
            raise saves.SaveError('The profile was cut inside a part the launcher cannot rebuild.')
        if name:
            removed += (name,)
        rest = STATE_TRAILER
    elif not removed:
        return None
    kept = [e for e in entries if e[0] not in removed]
    if cut is not None:
        names = {e[0] for e in kept}
        kept += [(n, c, v, d) for n, (c, v, d) in LOST_DEFAULTS.items() if n not in names]
    kept.sort(key=lambda e: e[0].encode())
    out = bytearray(body[:start] + struct.pack('>II', lead, len(kept)))
    for name, cls, version, data in kept:
        for text in (name, cls):
            out += struct.pack('>I', len(text)) + text.encode('ascii')
        out += struct.pack('>II', version, len(data)) + data
    return bytes(out + rest), removed, cut is not None


def trim_circuit_states(cfg, profile, keep='', cancelled=lambda: False, progress=lambda *_: None):
    """Before a start: take the FM4 circuits' location states out of ForzaProfile (#850).

    `keep` is the circuit being started (its state stays). Returns a CircuitTrim, or None when
    the save needs nothing (or there is no profile, or no game files to read its keys from)."""
    with saves.locked(profile):
        saves._recover(profile)
        try:
            raw = _read(profile)
        except FileNotFoundError:
            return None
        try:
            keys = _keys(cfg)
        except saves.SaveError:
            return None
        saves._check(cancelled)
        salvaged = None
        try:
            document = decode(profile, raw, keys)
            body, aes_key, mac_key = document.body, document.aes_key, document.mac_key
        except saves.SaveError:
            salvaged = _salvage(raw, keys)
            if salvaged is None:
                return CircuitTrim((), False, None, DAMAGED)
            body, aes_key, mac_key = salvaged
        try:
            change = trimmed_states(body, keep, salvaged is not None)
        except (saves.SaveError, struct.error):
            # a readable profile in a layout this does not know is left alone
            return None if salvaged is None else CircuitTrim((), False, None, DAMAGED)
        if change is None:
            return None
        new_body, removed, repaired = change
        edited = _seal(raw, new_body, aes_key, mac_key)
        label = 'Before repairing the profile' if repaired else 'Before removing FM4 circuit data'
        backup = _replace(profile, raw, edited, label, cancelled, progress)
        return CircuitTrim(removed, repaired, backup)
