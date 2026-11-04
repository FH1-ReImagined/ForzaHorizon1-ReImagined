#!/usr/bin/env python3
"""A save's career state as JSON, read only (#309, docs/coop_campaign.md "The two-game
acceptance test"): what a co-op grant changes or must leave alone, for checking a save before
and after a session without the launcher's UI.

Usage: career_state.py SAVE --game GAME [--xuid XUID] [--event CODE_OR_ID ...]

  SAVE   the profile's save folder, the one holding ForzaProfile/ForzaProfile and
         ForzaProfile/PlayerDatabase (e.g. .../B13EBABEBABEBABE/4D5309C9/00000001)
  GAME   the game folder: its default.xex gives the key tables, media/db/gamedb.slt the events
  XUID   the profile's XUID, which the keys are bound to (default: the runtime's own profile,
         B13EBABEBABEBABE; a Xenia save converted for the runtime keeps its Xenia XUID)

Prints the profile values (Main/XP, Credits, WristbandLevel, OnlineWinnings, OnlineXP), the
career tables' sizes, the garage's size and its cars flagged as prizes (Flags 2, what the
game writes for a Rewards_EventPrizes car), and for each --event (a HorizonEventID such as FR05,
or an Events.Id) its place, its races' places and whether it is unlocked. Both files are
decoded and verified with the launcher's own codecs (launcher/forza_launcher); nothing is
written. Run it with the game closed, or on a copy of the save.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'launcher'))

from forza_launcher import game, garage, save_editor  # noqa: E402

PROFILE_VALUES = ('Main/XP', 'Main/Credits', 'Main/WristbandLevel', 'Main/OnlineWinnings',
                  'Main/OnlineXP', 'Main/NumRaces', 'Main/NumOnlineRaces')
TABLES = ('Career_EventResults', 'Career_RaceResults', 'CareerEventUnlocks', 'CareerHubUnlocks',
          'Career_Garage')
PRIZE_FLAGS = 2


class _Settings:
    """What the launcher's key derivation reads from its settings: the game folder and the
    profile's XUID."""

    def __init__(self, game_dir, xuid):
        self._game = Path(game_dir)
        self.profile = {'mode': 'local'}
        self.advanced = {'extra_args': '--profile_xuid=' + xuid}

    def game_files(self):
        return self._game


def keys(game_dir, xuid):
    settings = _Settings(game_dir, xuid)
    return save_editor._keys(settings), garage._keys(settings)


def _count(db, table):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return None
    return db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]


def _event(gamedb, query):
    if gamedb is None:
        return (int(query), None) if str(query).isdigit() else None
    if str(query).isdigit():
        row = gamedb.execute('SELECT Id, HorizonEventID FROM Events WHERE Id=?', (int(query),)).fetchone()
    else:
        row = gamedb.execute('SELECT Id, HorizonEventID FROM Events WHERE lower(HorizonEventID)=lower(?)',
                             (query,)).fetchone()
    return tuple(row) if row else None


def state(save, profile_keys, garage_keys, gamedb=None, events=()):
    """The state of the save folder `save` (Path) as a dict."""
    folder = Path(save) / 'ForzaProfile'
    document = save_editor.decode(None, (folder / 'ForzaProfile').read_bytes(), profile_keys)
    values = document.values
    out = {'profile': {name: values[name].value for name in PROFILE_VALUES if name in values}}
    database = garage.decode(None, (folder / 'PlayerDatabase').read_bytes(), garage_keys).database
    db = garage._open(database)
    with closing(db):
        out['tables'] = {t: _count(db, t) for t in TABLES if _count(db, t) is not None}
        if 'Career_Garage' in out['tables']:
            out['prize_cars'] = sorted(r[0] for r in db.execute(
                'SELECT CarId FROM Career_Garage WHERE Flags=?', (PRIZE_FLAGS,)))
        out['events'] = {}
        for query in events:
            found = _event(gamedb, query)
            if found is None:
                out['events'][str(query)] = 'not an event of this game'
                continue
            event_id, code = found
            place = db.execute('SELECT Place FROM Career_EventResults WHERE EventId=?', (event_id,)).fetchone()
            races = {}
            if gamedb is not None:
                for (race,) in gamedb.execute('SELECT Id FROM Races WHERE EventId=? ORDER BY RaceNumber', (event_id,)):
                    stored = db.execute('SELECT Place FROM Career_RaceResults WHERE RaceId=?', (race,)).fetchone()
                    races[str(race)] = stored[0] if stored else None
            out['events'][code or str(event_id)] = {
                'id': event_id, 'place': place[0] if place else None, 'races': races,
                'unlocked': db.execute('SELECT 1 FROM CareerEventUnlocks WHERE EventId=?',
                                       (event_id,)).fetchone() is not None}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('save', help='the save folder holding ForzaProfile/')
    ap.add_argument('--game', required=True, help='the game folder (default.xex, media/db/gamedb.slt)')
    ap.add_argument('--xuid', default=game.LOCAL_PROFILE, help="the profile's XUID (16 hex digits)")
    ap.add_argument('--event', action='append', default=[], help='a HorizonEventID or Events.Id (repeatable)')
    a = ap.parse_args(argv)
    profile_keys, garage_keys = keys(a.game, a.xuid)
    source = Path(a.game) / 'media/db/gamedb.slt'
    gamedb = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) if source.exists() else None
    try:
        print(json.dumps(state(a.save, profile_keys, garage_keys, gamedb, a.event), indent=1, sort_keys=True))
    finally:
        if gamedb is not None:
            gamedb.close()


if __name__ == '__main__':
    main()
