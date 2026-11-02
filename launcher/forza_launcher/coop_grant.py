"""Co-op campaign results (#309, docs/coop_campaign.md "Milestone 1 progress" and "Milestone 2").

A festival event raced in a Co-op Festival world is an online race: the game writes no career
state for it. The Forza Live server keeps every player's result (server/forza_coop_ledger.py:
the host's, and each guest's once the host's game agrees with it), and after the session each
player's launcher applies that player's own result to its save the way the career's own race end
would have:

- `Career_RaceResults` and `Career_EventResults` at the new place, only when it is better than
  the save's (the career writer, sub_826ADDD0, keeps the best place);
- the wristband XP of the improvement: the points of the event's level at the new place less
  those of the old one (WristbandScoring, as sub_8253B230 counts what is left to earn);
- the event's prize at that place: CashPrize split by the race mode's distribution over the
  grid (the festival modes: (cars - place + 1) / cars, sub_8269BA50; the street mode: 1st all,
  2nd half, 3rd a quarter) and rounded to two significant figures (sub_826984E8);
- the event's own unlock rewards (Rewards_EventUnlock, which CTriggerEventUnlocks gives when
  that event is completed, sub_8253B988).

Left to the game: the unlocks by XP and the wristband level. CTriggerEventUnlocks runs the XP
unlocks (hubs, street races, Nemesis races, the Headline race; sub_82546080) with the save's XP
after every race, so the player's next race catches up, and the Nemesis races there have
conditions of their own (a test grant that wrote them by XP alone unlocked five the game had
not). When the XP passes the next wristband threshold the free roam says so and Race Central
awards it with its own unlocks and reward. A result that does not
improve the save changes nothing, so applying one twice is harmless. The online race paid
online winnings of its own (Main/OnlineWinnings, into Main/Credits; the playlist's
payoutModifier does not change them, measured), so the prize here is the event's prize less
what that race paid online (the server brackets it with the player's onlineWinnings in that
race's stats and the next race's; before a next race, the save's Main/OnlineWinnings): the
player gets the larger of the two, once. The online race's XP is the online track's
(Main/OnlineXP); the wristband XP here is the career's, once per improvement.

Which save: a result is applied only to the save of the launcher session it was earned in (the
launcher keeps its sessions, `coop_sessions.json`: the save profile, start and end), never to
another career sharing the PC's Forza Live identity; results of another save's session wait for
that save's next session, results of a game the launcher did not start wait unapplied. Only an
event the save has unlocked (CareerEventUnlocks) takes a result: /event can start any festival
event, and a guest's career may not have reached the host's. A locked one waits on the server
until this career unlocks it (for 30 days, and at most 20 waiting results a player), and the
Play page says so after the session. Results that wait are not confirmed.

The edit is the save editor's: the save lock, both files read, decoded and verified with the
player's own keys, one backup of the save ("Before co-op results"), both files checked unchanged
before a complete staged save replaces the directory through the save manager's recovery
journal. Recovery runs before another grant or game session. The server learns which results were
applied; those not applied wait there until the next session. Nothing happens offline, on the
server on this PC, or without a Forza Live identity (shader_share's rules).
"""
from contextlib import closing
from dataclasses import dataclass
import json
import math
import os
import sqlite3
import threading
import time
import urllib.error
import uuid

from . import garage, live_auth, paths, save_editor, saves, shader_share

FETCH_TIMEOUT = 6
SESSIONS_NAME = 'coop_sessions.json'
SESSION_SLACK = 600      # seconds around a session: the server's clock against this PC's
MAX_SESSIONS = 200
KEEP_SESSIONS = 30 * 86400  # the server keeps results as long
MAX_RESULTS = 50
MAX_CREDITS = save_editor.EDITABLE['Main/Credits'][2]
MAX_XP = save_editor.EDITABLE['Main/XP'][2]
STREET_STYLE = 2
_run_lock = threading.Lock()


@dataclass(frozen=True)
class Change:
    event: int
    code: str
    place: int
    old: object       # the save's place before (None: not raced)
    xp: int
    credits: int
    unlocked: tuple   # CareerEventUnlocks rows added
    online: object = 0  # what the online race itself paid (onlineWinnings), less from the
                        # prize; None: not known, so no prize


@dataclass(frozen=True)
class GrantResult:
    applied: tuple    # Change of each result that improved the save
    unchanged: tuple  # ids of results that did not (already as good, or unknown events)
    backup: object = None
    xp: int = 0
    credits: int = 0
    locked: tuple = ()  # ids of results for events this save has not unlocked (they wait)


# --- the career's rules -------------------------------------------------------------------------

def round_credits(value):
    """sub_826984E8: below 10 nothing, else rounded to two significant figures."""
    if value < 10:
        return 0
    step = 10 ** (math.floor(math.log10(value)) - 1)
    return int(math.floor(value / step + 0.5) * step)


def payout(cash, place, cars, style):
    """An event's prize at a place (sub_8269BA50): the street mode's TopThreeFinish, else the
    festival modes' share of the grid."""
    if cars <= 1 or not 1 <= place <= cars:
        return 0
    if style == STREET_STYLE:
        share = {1: 1.0, 2: 0.5, 3: 0.25}.get(place, 0.0)
    else:
        share = (cars - place + 1) / cars
    return round_credits(cash * share)


def points(gamedb, level, place):
    if place is None:
        return 0
    row = gamedb.execute('SELECT Points FROM WristbandScoring WHERE WristbandLevel=? AND Place=?',
                         (level, place)).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def unlock(db, event_id, added):
    if db.execute('INSERT OR IGNORE INTO CareerEventUnlocks (EventId) VALUES (?)', (event_id,)).rowcount:
        added.append(event_id)


def event_unlocks(gamedb, db, event_id):
    """The event's own unlock rewards (Rewards_EventUnlock: what completing it unlocks)."""
    added = []
    for (other,) in gamedb.execute('SELECT UnlockEventId FROM Rewards_EventUnlock WHERE EventId=?', (event_id,)):
        unlock(db, other, added)
    return tuple(added)


def online_paid(result, online_now=None, after_ok=True, now_ok=True):
    """What the co-op round's online race paid the player itself (onlineWinnings), or None
    when that is not known. Before the race paid: the round's report (the server's
    `winnings`[0]). After it: the player's next race (`winnings`[1]), only when that race was
    in this save (after_ok: every save of the PC shares the Forza Live identity), or the save's
    Main/OnlineWinnings when the grant runs right after the session the result was earned in
    (now_ok). A value below the one before is another save's: not known either."""
    w = result.get('winnings')
    if not (isinstance(w, list) and len(w) == 2 and type(w[0]) is int):
        return None
    before, after = w
    if type(after) is int and after_ok and after >= before:
        return after - before
    if type(online_now) is int and now_ok and online_now >= before:
        return online_now - before
    return None


def career(gamedb, db, xp, credits, results, locked=None, online_now=None, trusted=None):
    """Applies results ([{"id", "event", "place", "places", "code", "winnings"}]) to a
    PlayerDatabase connection. Returns (changes, unchanged ids, xp, credits) after them; the
    ids of results for events the save has not unlocked go to `locked` (a list) and change
    nothing. The prize is less what the round's online race already paid (online_paid, with
    the save's Main/OnlineWinnings as online_now and trusted(result) -> (after_ok, now_ok)):
    the player gets the larger of the two, once. When that is not known, no prize: the
    online race paid something, and paying the prize too could pay twice."""
    trusted = trusted or (lambda result: (True, True))
    changes, unchanged = [], []
    locked = [] if locked is None else locked
    for result in results[:MAX_RESULTS]:
        event_id, place = result.get('event'), result.get('place')
        row = gamedb.execute('SELECT Level, CareerEventStyle, CashPrize, NumberOfDrivers, HorizonEventID '
                             'FROM Events WHERE Id=?', (event_id,)).fetchone() if type(event_id) is int else None
        cars = result.get('places') or 8
        if (row is None or type(place) is not int or type(cars) is not int or not 1 <= place <= cars <= 8
                or row[1] not in (1, STREET_STYLE)):
            unchanged.append(result.get('id'))
            continue
        level, style, cash, _, code = row
        if db.execute('SELECT 1 FROM CareerEventUnlocks WHERE EventId=?', (event_id,)).fetchone() is None:
            locked.append(result.get('id'))
            continue
        old = db.execute('SELECT Place FROM Career_EventResults WHERE EventId=?', (event_id,)).fetchone()
        old = old[0] if old else None
        if old is not None and 1 <= old <= place:
            unchanged.append(result.get('id'))
            continue
        for (race,) in gamedb.execute('SELECT Id FROM Races WHERE EventId=? ORDER BY RaceNumber', (event_id,)):
            stored = db.execute('SELECT Place FROM Career_RaceResults WHERE RaceId=?', (race,)).fetchone()
            if stored is None:
                db.execute('INSERT INTO Career_RaceResults (RaceId, Place) VALUES (?,?)', (race, place))
            elif not 1 <= stored[0] <= place:
                db.execute('UPDATE Career_RaceResults SET Place=? WHERE RaceId=?', (place, race))
        if old is None:
            db.execute('INSERT INTO Career_EventResults (EventId, Place, RivalsBeaten) VALUES (?,?,0)', (event_id, place))
        else:
            db.execute('UPDATE Career_EventResults SET Place=? WHERE EventId=?', (place, event_id))
        gained = max(0, points(gamedb, level, place) - points(gamedb, level, old))
        online = online_paid(result, online_now, *trusted(result))
        prize = 0 if online is None else max(0, payout(cash or 0, place, cars, style) - online)
        xp = min(MAX_XP, xp + gained)
        credits = min(MAX_CREDITS, credits + prize)
        added = event_unlocks(gamedb, db, event_id)
        changes.append(Change(event_id, result.get('code') or code or str(event_id), place, old, gained, prize,
                              added, online))
    return changes, unchanged, xp, credits


# --- the save ------------------------------------------------------------------------------------

def _written(path, edited):
    """The edited file next to the original, flushed to disk (replaced by the caller)."""
    temporary = path.with_name('.coop-grant-'+uuid.uuid4().hex)
    with temporary.open('xb') as writer:
        writer.write(edited)
        writer.flush()
        os.fsync(writer.fileno())
    return temporary


def apply(cfg, profile, results, cancelled=lambda: False, progress=lambda *_: None, trusted=None):
    """Applies the results to the profile's save (closed game). GrantResult."""
    with saves.locked(profile):
        saves._recover(profile)
        saves._check(cancelled)
        profile_raw = save_editor._read(profile)
        garage_raw = garage._read(profile)
        document = save_editor.decode(profile, profile_raw, save_editor._keys(cfg))
        garage_doc = garage.decode(profile, garage_raw, garage._keys(cfg))
        values = document.values
        try:
            xp, credits = values['Main/XP'].value, values['Main/Credits'].value
        except KeyError as exc:
            raise saves.SaveError('This profile has no career values to update.') from exc
        online_now = values['Main/OnlineWinnings'].value if 'Main/OnlineWinnings' in values else None
        db = garage._open(garage_doc.database)
        try:
            source = (cfg.game_files()/'media/db/gamedb.slt').resolve()
            with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as gamedb:
                locked = []
                changes, unchanged, new_xp, new_credits = career(gamedb, db, xp, credits, results, locked,
                                                                 online_now, trusted)
            if not changes:
                return GrantResult((), tuple(unchanged), locked=tuple(locked))
            db.commit()
            edited_garage = garage.encode(garage_doc, db.serialize())
        except sqlite3.Error as exc:
            raise saves.SaveError('Unsupported save or game database: '+str(exc)) from exc
        finally:
            db.close()
        edited_profile = save_editor.encode(document, {'Main/XP': new_xp, 'Main/Credits': new_credits})
        saves._check(cancelled)
        backup = saves._create(profile, 'Before co-op results', 'before-edit', cancelled, progress, 10)
        if backup is None:
            raise saves.SaveError('Could not back up the selected save.')
        profile_path = saves._inside(profile.folder/'ForzaProfile/ForzaProfile', profile.root)
        garage_path = garage._path(profile)
        # Stage the entire verified backup, then publish both edits together via
        # the save manager's recoverable directory swap. Originals stay intact
        # while either staged file is being written.
        parent = saves._inside(profile.folder.parent, profile.root)
        stage = parent / ('.save-restore-' + uuid.uuid4().hex)
        journal = parent / '.save-manager-restore.json'
        stage.mkdir()
        try:
            metadata, _ = saves._manifest(backup.path, profile)
            source = saves._inside(backup.path / 'save', profile.backups)
            copied = saves._copy(source, stage, saves._files(source, cancelled), cancelled, progress)
            if copied != metadata['files']:
                raise saves.SaveError('The co-op backup failed its integrity check.')
            for path, edited in ((garage_path, edited_garage), (profile_path, edited_profile)):
                staged_path = stage / path.relative_to(profile.folder)
                temporary = _written(staged_path, edited)
                os.replace(temporary, staged_path)
            saves._check(cancelled)
            if save_editor._read(profile) != profile_raw or garage._read(profile) != garage_raw:
                raise saves.SaveError('The save changed before the co-op results were applied.')
            saves._replace_directory(profile, stage, cancelled)
        finally:
            if not journal.exists():
                saves._remove(stage, parent)
        # what the game will read back
        save_editor.decode(profile, save_editor._read(profile), ((document.aes_key, document.mac_key),))
        garage.decode(profile, garage._read(profile), ((garage_doc.aes_key, garage_doc.mac_key),))
        return GrantResult(tuple(changes), tuple(unchanged), backup, new_xp - xp, new_credits - credits,
                           tuple(locked))


# --- the server ----------------------------------------------------------------------------------

def _request(base, identity, path, body=None, timeout=FETCH_TIMEOUT):
    with live_auth.urlopen(base+path, identity, body, timeout=timeout) as r:
        return json.loads(r.read(1 << 20) or b'{}')


def fetch(base, identity):
    results = _request(base, identity, '/coop/results').get('results', [])
    return [r for r in results if isinstance(r, dict)][:MAX_RESULTS] if isinstance(results, list) else []


def confirm(base, identity, ids):
    return _request(base, identity, '/coop/results/applied', json.dumps({'ids': list(ids)}).encode()).get('applied', 0)


# --- the launcher's sessions: which save a result belongs to -----------------------------------

def _sessions_path(cfg):
    return cfg.data_home() / SESSIONS_NAME


def load_sessions(cfg):
    try:
        data = json.loads(_sessions_path(cfg).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return [s for s in data.get('sessions', []) if isinstance(s, dict)
            and isinstance(s.get('profile'), str) and all(isinstance(s.get(k), (int, float)) for k in ('start', 'end'))]


def record_session(cfg, profile, started, ended, now=None):
    """A game session of this launcher ended: kept so that results earned in it go to its save."""
    if profile is None or not started:
        return
    now = time.time() if now is None else now
    sessions = [s for s in load_sessions(cfg) if now - s['end'] <= KEEP_SESSIONS]
    sessions.append({'profile': profile.key, 'start': float(started), 'end': float(ended)})
    path = _sessions_path(cfg)
    tmp = path.with_name(path.name + '.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({'version': 1, 'sessions': sessions[-MAX_SESSIONS:]}), encoding='utf-8')
        os.replace(tmp, path)
    except OSError as e:
        paths.log_error('record the session for co-op results', e)


def owners(sessions, at):
    """The save profiles of the sessions a result's time falls in."""
    return {s['profile'] for s in sessions
            if isinstance(at, (int, float)) and s['start'] - SESSION_SLACK <= at <= s['end'] + SESSION_SLACK}


def ordinal(n):
    return '%d%s' % (n, 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th'))


def locked_note(result):
    """What the player reads about a result waiting for an event this career has not unlocked."""
    code = str(result.get('code') or result.get('event'))
    place = result.get('place')
    host = result.get('host')
    until = result.get('until')
    where = ' '.join(([ordinal(place)] if type(place) is int else [])
                     + ([f"in {host}'s world"] if isinstance(host, str) and host else []))
    keep = (time.strftime(' until %d %b %Y', time.localtime(until))
            if isinstance(until, (int, float)) and until > 0 else '')
    return (f'Co-op results: {code}{f" ({where})" if where else ""} waits: this career has not '
            f'unlocked {code} yet. It is added at the first session after you unlock it; the '
            f'server keeps it{keep}.')


def enabled(cfg) -> bool:
    """Settings > Online > "Co-op campaign results" (the "Online features" choice turns it off)."""
    return cfg.settings.get('coop_results', True) is True


def after_session(cfg, profile, log=print, base=None, identity=None, request=None):
    """Fetches the player's pending co-op results and applies those earned in this save's
    sessions to it. Returns a short result (for the log); `log` gets the lines the player
    reads (the Play page shows them)."""
    if not enabled(cfg):
        return 'off'
    base = shader_share.server_base(cfg) if base is None else base
    if not base:
        return 'no server'
    identity = identity or shader_share.read_identity(shader_share.identity_dirs(cfg))
    if identity is None:
        return 'no Forza Live identity yet'
    if profile is None:
        return 'no save'
    fetched = fetch(base, identity)
    if not fetched:
        return 'nothing'
    sessions = load_sessions(cfg)
    results, waiting, stray = [], 0, []
    for r in fetched:
        found = owners(sessions, r.get('at'))
        if found == {profile.key}:
            results.append(r)
        elif len(found) == 1:  # another save's session: its next session applies it
            waiting += 1
        else:  # a game the launcher did not start, or two saves' sessions at that time
            stray.append(r)
    if waiting:
        log(f'Co-op results: {waiting} result(s) earned with another save wait for that save\'s '
            'next session')
    if stray:  # no save of this launcher can take them: told once, and the server drops them
        log(f'Co-op results: {len(stray)} result(s) from a game the launcher did not start (or '
            'while two saves were in use) cannot go to a career and were dropped')
        _confirm_quietly(base, identity, [r.get('id') for r in stray if isinstance(r.get('id'), str)])
    dropped = f', dropped {len(stray)}' if stray else ''
    if not results:
        return f'applied 0, waiting {waiting}{dropped}'
    mine = [s for s in sessions if s['profile'] == profile.key]
    latest = max(mine, key=lambda s: s['end'], default=None)

    def trusted(r):
        """(the server's next race was in this save, the grant runs right after the result's
        session)"""
        at, next_at = r.get('at'), r.get('winnings_at')
        after_ok = isinstance(next_at, (int, float)) and owners(sessions, next_at) == {profile.key}
        now_ok = (latest is not None and isinstance(at, (int, float))
                  and latest['start'] - SESSION_SLACK <= at <= latest['end'] + SESSION_SLACK)
        return after_ok, now_ok
    result = apply(cfg, profile, results, trusted=trusted)
    for change in result.applied:  # the save has changed: told before anything else can fail
        was = f'{change.old}' if change.old else 'not raced'
        note = ('no prize: what the online race paid is not known' if change.online is None else
                f'the prize less the {change.online:,} CR the online race paid' if change.online else '')
        log(f'Co-op results: {change.code} at place {change.place} (was {was}): +{change.xp} XP, '
            f'+{change.credits:,} CR' + (f' ({note})' if note else '')
            + (f', {len(change.unlocked)} event(s) unlocked' if change.unlocked else ''))
    for r in results:
        if r.get('id') in result.locked:
            log(locked_note(r))
    settled = {c.event for c in result.applied}
    done = [r.get('id') for r in results if isinstance(r.get('id'), str)
            and (r.get('id') in result.unchanged or r.get('event') in settled)]
    if done:  # not confirmed: they come back "unchanged" at the next session and settle then
        _confirm_quietly(base, identity, done)
    return (f'applied {len(result.applied)}, unchanged {len(result.unchanged)}, '
            f'locked {len(result.locked)}, waiting {waiting}{dropped}')


def _confirm_quietly(base, identity, ids):
    if not ids:
        return 0
    try:
        return confirm(base, identity, ids)
    except (OSError, ValueError, urllib.error.URLError) as e:
        paths.log_error('confirm co-op results', e)
        return 0


def after_session_quietly(cfg, profile, log=print):
    if not _run_lock.acquire(blocking=False):
        return 'busy'
    try:
        return after_session(cfg, profile, log)
    except (OSError, ValueError, urllib.error.URLError, saves.SaveError) as e:
        paths.log_error('apply co-op results', e)
        return 'failed'
    finally:
        _run_lock.release()


def after_session_in_background(cfg, profile, log=print):
    """After a session: the fetch and the save edit in a daemon thread (the game is closed)."""
    if profile is None or not enabled(cfg) or not shader_share.server_base(cfg):
        return None
    t = threading.Thread(target=after_session_quietly, args=(cfg, profile, log), name='coop-results', daemon=True)
    t.start()
    return t
