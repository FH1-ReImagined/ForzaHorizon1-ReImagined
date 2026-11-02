"""Co-op campaign results applied to a save (#309, coop_grant.py): synthetic saves and a small
game database (no game rows, assets or key material)."""
from contextlib import closing
from pathlib import Path
import sqlite3
import os
import subprocess
import struct
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import coop_grant, garage, game, save_editor, saves
from forza_launcher.config import Config
from test_garage import KEYS, envelope
from test_save_editor import field, profile_bytes


def profile_body(xp=1000, credits=5000, wristband=2, online=None):
    fields = field('Credits', 3, credits) + field('XP', 7, xp) + field('WristbandLevel', 7, wristband)
    if online is not None:
        fields += field('OnlineWinnings', 3, online)
    main = struct.pack('>I', 3 if online is None else 4) + fields
    return struct.pack('>I', 1) + field('Main', 15, main) + b'opaque class state'


def player_database(results=(), unlocks=(4, 31), hubs=()):
    db = sqlite3.connect(':memory:')
    db.execute('PRAGMA page_size=512')
    db.executescript('''
        CREATE TABLE Career_EventResults (EventId INTEGER PRIMARY KEY, Place INT, RivalsBeaten INT);
        CREATE TABLE Career_RaceResults (RaceId INTEGER PRIMARY KEY, Place INT);
        CREATE TABLE CareerEventUnlocks (EventId INTEGER PRIMARY KEY);
        CREATE TABLE CareerHubUnlocks (HubId INTEGER PRIMARY KEY);
        CREATE TABLE OtherProgress (value TEXT);
        INSERT INTO OtherProgress VALUES ('preserved');''')
    for event, race, place in results:
        db.execute('INSERT INTO Career_EventResults VALUES (?,?,0)', (event, place))
        db.execute('INSERT INTO Career_RaceResults VALUES (?,?)', (race, place))
    db.executemany('INSERT INTO CareerEventUnlocks VALUES (?)', [(e,) for e in unlocks])
    db.executemany('INSERT INTO CareerHubUnlocks VALUES (?)', [(h,) for h in hubs])
    db.commit()
    raw = db.serialize()
    db.close()
    return raw


def game_database(path):
    """Events like the game's: FR05 (4, level 0, circuit, 3000 CR), FR50 (231, level 5), a
    street race (38, hub 2, UnlockPointsReq 1699), a showcase (8), a Nemesis race (31: level 1,
    XP 220), the Headline race (167, unlocks 169), a level-3 festival race needing XP (90)."""
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
            CREATE TABLE Events (Id INT, HorizonEventID TEXT, Level INT, CareerEventStyle INT,
                CashPrize INT, NumberOfDrivers INT, UnlockPointsReq INT, HubId INT);
            CREATE TABLE Races (Id INT, EventId INT, RaceNumber INT);
            CREATE TABLE WristbandScoring (Id INT, WristbandLevel INT, Place INT, Points INT);
            CREATE TABLE Rewards_EventUnlock (Id INT, EventId INT, UnlockEventId INT);
            CREATE TABLE EventHubs (Id INT, UnlockPointsReq INT);
            CREATE TABLE EventHubInitialEvents (HubId INT, EventId INT);
            INSERT INTO Events VALUES (4, 'FR05', 0, 1, 3000, 7, 0, 0), (231, 'FR50', 5, 1, 15000, 7, 0, 0),
                (38, 'STREET_PLNS_005', 0, 2, 35000, 7, 1699, 2), (8, 'SHOWCASE', 0, 3, 9000, 1, 0, 0),
                (31, 'NEM_01', 1, 5, 0, 1, 220, 0), (167, 'HEADLINE', 6, 6, 50000, 7, 10000, 0),
                (90, 'FR30', 3, 1, 9000, 7, 1200, 0), (39, 'STREET_PLNS_006', 0, 2, 35000, 7, 1500, 3);
            INSERT INTO Races VALUES (11, 4, 1), (12, 231, 1), (13, 38, 1), (14, 8, 1), (15, 167, 1);
            INSERT INTO Rewards_EventUnlock VALUES (1, 4, 167);
            INSERT INTO EventHubs VALUES (1, 310), (2, 1100), (3, 4500);
            INSERT INTO EventHubInitialEvents VALUES (2, 38), (3, 39);''')
        for level in range(7):
            for place in range(1, 9):
                db.execute('INSERT INTO WristbandScoring VALUES (?,?,?,?)',
                           (level * 8 + place, level, place, (100 + 25 * level) - 10 * (place - 1)))
        db.commit()


PAID0 = [0, 0]  # the online race paid nothing: the whole prize


class GrantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root/'launcher.json')
        self.cfg.advanced['data_root'] = str(self.root/'data')
        self.cfg.d['game_root'] = str(self.root/'game')
        self.profile = saves.profile_for(self.cfg)
        self.folder = self.profile.folder/'ForzaProfile'
        self.folder.mkdir(parents=True)
        self.write()
        source = self.cfg.game_files()/'media/db/gamedb.slt'
        source.parent.mkdir(parents=True)
        game_database(source)
        for p in (patch.object(garage, '_keys', return_value=(KEYS,)),
                  patch.object(save_editor, '_keys', return_value=(KEYS,))):
            p.start()
            self.addCleanup(p.stop)

    def write(self, xp=1000, credits=5000, wristband=2, online=None, **database):
        (self.folder/'ForzaProfile').write_bytes(profile_bytes(profile_body(xp, credits, wristband, online)))
        (self.folder/'PlayerDatabase').write_bytes(envelope(player_database(**database)))

    def state(self):
        doc = save_editor.decode(self.profile, (self.folder/'ForzaProfile').read_bytes(), (KEYS,))
        db = garage._open(garage.decode(self.profile, (self.folder/'PlayerDatabase').read_bytes(), (KEYS,)).database)
        with closing(db):
            return {'xp': doc.values['Main/XP'].value, 'credits': doc.values['Main/Credits'].value,
                    'wristband': doc.values['Main/WristbandLevel'].value,
                    'events': dict(db.execute('SELECT EventId, Place FROM Career_EventResults')),
                    'races': dict(db.execute('SELECT RaceId, Place FROM Career_RaceResults')),
                    'unlocks': sorted(r[0] for r in db.execute('SELECT EventId FROM CareerEventUnlocks')),
                    'hubs': sorted(r[0] for r in db.execute('SELECT HubId FROM CareerHubUnlocks')),
                    'other': [tuple(r) for r in db.execute('SELECT value FROM OtherProgress')]}

    def test_payout_follows_the_game(self):
        self.assertEqual(coop_grant.round_credits(2250), 2300)
        self.assertEqual(coop_grant.round_credits(9), 0)
        self.assertEqual(coop_grant.round_credits(13500), 14000)
        self.assertEqual(coop_grant.payout(3000, 1, 8, 1), 3000)
        self.assertEqual(coop_grant.payout(3000, 3, 8, 1), 2300)    # 6/8 of the prize
        self.assertEqual(coop_grant.payout(18000, 3, 8, 1), 14000)
        self.assertEqual(coop_grant.payout(3000, 8, 8, 1), 380)     # 1/8: 375
        self.assertEqual(coop_grant.payout(35000, 2, 8, 2), 18000)  # street: half, 17500 rounded
        self.assertEqual(coop_grant.payout(35000, 4, 8, 2), 0)

    def test_a_new_result_is_applied_like_the_career(self):
        result = coop_grant.apply(self.cfg, self.profile,
                                  [{'id': 'r1', 'event': 4, 'code': 'FR05', 'place': 3, 'places': 8, 'winnings': PAID0}])
        self.assertEqual(len(result.applied), 1)
        change = result.applied[0]
        self.assertEqual((change.event, change.place, change.old, change.xp, change.credits),
                         (4, 3, None, 80, 2300))
        after = self.state()
        self.assertEqual((after['xp'], after['credits'], after['wristband']), (1080, 7300, 2))
        self.assertEqual((after['events'], after['races']), ({4: 3}, {11: 3}))
        # FR05's Rewards_EventUnlock (167); nothing by XP (hubs, Nemesis): the game's next race
        self.assertEqual(after['hubs'], [])
        self.assertEqual(after['unlocks'], [4, 31, 167])
        self.assertEqual(change.unlocked, (167,))
        self.assertEqual(after['other'], [('preserved',)])
        backup = result.backup.path/'save/ForzaProfile'
        self.assertEqual((backup/'PlayerDatabase').read_bytes(), envelope(player_database()))
        self.assertEqual(result.backup.label, 'Before co-op results')

    def test_unlocks_by_xp_are_left_to_the_game(self):
        """The game runs them after every race with the save's XP; its Nemesis races have
        conditions of their own (a grant by XP alone unlocked five the game had not)."""
        self.write(xp=1150, wristband=3, unlocks=(4, 31, 231))
        coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 231, 'place': 1, 'places': 8, 'winnings': PAID0}])
        after = self.state()
        self.assertEqual(after['xp'], 1150 + 225)                       # level 5, 1st
        self.assertEqual((after['hubs'], after['unlocks']), ([], [4, 31, 231]))

    def test_an_event_the_save_has_not_unlocked_waits(self):
        before = self.originals()
        result = coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 231, 'place': 1, 'places': 8, 'winnings': PAID0}])
        self.assertEqual((result.applied, result.unchanged, result.locked, result.backup), ((), (), ('r1',), None))
        self.assertEqual(self.originals(), before)

    def test_the_wristband_level_is_left_to_the_game(self):
        self.write(xp=740, wristband=1)
        coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 4, 'place': 1, 'places': 8, 'winnings': PAID0}])
        after = self.state()
        self.assertEqual((after['xp'], after['wristband']), (840, 1))   # past 750, still Green

    def test_a_worse_or_equal_place_changes_nothing(self):
        self.write(results=[(4, 11, 2)])
        before = [(self.folder/n).read_bytes() for n in ('ForzaProfile', 'PlayerDatabase')]
        result = coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 4, 'place': 5, 'places': 8, 'winnings': PAID0},
                                                           {'id': 'r2', 'event': 4, 'place': 2, 'places': 8, 'winnings': PAID0}])
        self.assertEqual((result.applied, result.unchanged, result.backup), ((), ('r1', 'r2'), None))
        self.assertEqual([(self.folder/n).read_bytes() for n in ('ForzaProfile', 'PlayerDatabase')], before)
        self.assertEqual(saves.snapshots(self.profile)[0], [])

    def test_a_better_place_pays_the_difference_once(self):
        self.write(results=[(4, 11, 5)])
        result = coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 4, 'place': 2, 'places': 8, 'winnings': PAID0},
                                                           {'id': 'r2', 'event': 4, 'place': 3, 'places': 8, 'winnings': PAID0}])
        self.assertEqual([(c.place, c.old, c.xp, c.credits) for c in result.applied], [(2, 5, 30, 2600)])
        self.assertEqual(result.unchanged, ('r2',))
        after = self.state()
        self.assertEqual((after['events'], after['races'], after['xp'], after['credits']),
                         ({4: 2}, {11: 2}, 1030, 7600))
        again = coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 4, 'place': 2, 'places': 8, 'winnings': PAID0}])
        self.assertEqual(again.applied, ())

    def test_events_the_career_does_not_take_this_way(self):
        result = coop_grant.apply(self.cfg, self.profile, [
            {'id': 'a', 'event': 8, 'place': 1, 'places': 2},     # a showcase
            {'id': 'b', 'event': 999, 'place': 1, 'places': 8, 'winnings': PAID0},   # not in the database
            {'id': 'c', 'event': 4, 'place': 9, 'places': 8, 'winnings': PAID0},     # off the grid
            {'id': 'd', 'event': '4', 'place': 1, 'places': 8, 'winnings': PAID0}])  # not a number
        self.assertEqual((result.applied, result.unchanged), ((), ('a', 'b', 'c', 'd')))

    def test_a_save_that_changes_during_the_backup_is_not_replaced(self):
        create = saves._create

        def changing(profile, *args):
            backup = create(profile, *args)
            self.write(xp=1)
            return backup
        with patch.object(saves, '_create', side_effect=changing):
            with self.assertRaises(saves.SaveError):
                coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 4, 'place': 1, 'places': 8, 'winnings': PAID0}])
        changed = self.state()
        self.assertEqual((changed['xp'], changed['events']), (1, {}))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['ForzaProfile', 'PlayerDatabase'])

    def serve(self, results):
        """after_session against a server with these pending results; (output, requests sent,
        lines logged)."""
        sent, lines = [], []

        def request(base, identity, path, body=None, timeout=0):
            sent.append((base, identity, path, body))
            return {'results': results} if path == '/coop/results' else {'applied': 2}
        with patch.object(coop_grant, '_request', side_effect=request):
            out = coop_grant.after_session(self.cfg, self.profile, lines.append,
                                           base='http://server:21201', identity=('0009000000001241', 'AB' * 16, 'CD' * 32))
        return out, sent, lines

    def test_results_go_only_to_the_save_of_the_session_they_were_earned_in(self):
        other = saves.Profile(self.profile.home, self.root/'other', 'xenia-E030000004B490FA', 'Xenia copy')
        coop_grant.record_session(self.cfg, other, 1000, 2000, now=2100)
        coop_grant.record_session(self.cfg, self.profile, 5000, 6000, now=6100)
        out, sent, lines = self.serve([
            {'id': 'other', 'event': 4, 'code': 'FR05', 'place': 1, 'places': 8, 'winnings': PAID0, 'at': 1500},
            {'id': 'outside', 'event': 4, 'code': 'FR05', 'place': 1, 'places': 8, 'winnings': PAID0, 'at': 3500},
            {'id': 'locked', 'event': 231, 'code': 'FR50', 'place': 3, 'places': 8, 'winnings': PAID0, 'at': 5500,
             'host': 'DriverA', 'until': 5500 + 30 * 86400},
            {'id': 'mine', 'event': 4, 'code': 'FR05', 'place': 2, 'places': 8, 'winnings': PAID0, 'at': 5600}])
        self.assertEqual(out, 'applied 1, unchanged 0, locked 1, waiting 1, dropped 1')
        # the result of a game the launcher did not start can't go to any save: told, dropped
        self.assertEqual([s[2:] for s in sent[1:]], [('/coop/results/applied', b'{"ids": ["outside"]}'),
                                                     ('/coop/results/applied', b'{"ids": ["mine"]}')])
        self.assertEqual(self.state()['events'], {4: 2})
        self.assertIn("Co-op results: 1 result(s) earned with another save wait for that save's next "
                      'session', lines)
        self.assertIn('Co-op results: 1 result(s) from a game the launcher did not start (or while two '
                      'saves were in use) cannot go to a career and were dropped', lines)
        until = time.strftime('%d %b %Y', time.localtime(5500 + 30 * 86400))
        self.assertEqual(lines[-1], "Co-op results: FR50 (3rd in DriverA's world) waits: this career "
                                    'has not unlocked FR50 yet. It is added at the first session after '
                                    f'you unlock it; the server keeps it until {until}.')
        # nothing of this save's: nothing confirmed, no edit
        out, sent, _ = self.serve([{'id': 'other', 'event': 4, 'place': 1, 'places': 8, 'winnings': PAID0, 'at': 1500}])
        self.assertEqual((out, [s[2] for s in sent]), ('applied 0, waiting 1', ['/coop/results']))

    def test_a_guest_s_locked_result_applies_once_its_career_unlocks_the_event(self):
        """Milestone 2: a guest's result for an event its career has not reached waits on the
        server (not confirmed) and goes in at a later session, once the event is unlocked."""
        coop_grant.record_session(self.cfg, self.profile, 5000, 6000, now=6100)
        guest = [{'id': 'g1', 'event': 231, 'code': 'FR50', 'place': 2, 'places': 8, 'winnings': PAID0, 'at': 5500,
                  'host': 'DriverA', 'until': 5500 + 30 * 86400}]
        out, sent, lines = self.serve(guest)
        self.assertEqual((out, [s[2] for s in sent]), ('applied 0, unchanged 0, locked 1, waiting 0',
                                                       ['/coop/results']))
        self.assertIn('waits: this career has not unlocked FR50 yet', lines[-1])
        self.assertEqual(self.state()['events'], {})
        # the career unlocks FR50 (its wristband), and the next session's grant applies it
        self.write(unlocks=(4, 31, 231))
        coop_grant.record_session(self.cfg, self.profile, 7000, 8000, now=8100)
        # the server's bracket closed in another save's session: what the race paid is not
        # known, so the result and XP go in without a prize
        other = saves.Profile(self.profile.home, self.root/'other', 'xenia-E030000004B490FA', 'Xenia copy')
        coop_grant.record_session(self.cfg, other, 6500, 6800, now=8100)
        out, sent, lines = self.serve([dict(guest[0], winnings=[1000, 1000], winnings_at=6600)])
        self.assertEqual(lines, ['Co-op results: FR50 at place 2 (was not raced): +215 XP, +0 CR '
                                 '(no prize: what the online race paid is not known)'])
        self.write(unlocks=(4, 31, 231))
        # this save's next race closed it (an online race in its later session): the prize
        guest[0]['winnings_at'] = 7500
        out, sent, lines = self.serve(guest)
        self.assertEqual(out, 'applied 1, unchanged 0, locked 0, waiting 0')
        self.assertEqual(sent[-1][2:], ('/coop/results/applied', b'{"ids": ["g1"]}'))
        self.assertEqual(lines, ['Co-op results: FR50 at place 2 (was not raced): +215 XP, +13,000 CR'])
        self.assertEqual((self.state()['events'], self.state()['xp'], self.state()['credits']),
                         ({231: 2}, 1215, 18000))
        out, sent, lines = self.serve([dict(guest[0], id='g2', place=1, winnings=[1000, 6000])])  # 7500
        self.assertEqual(lines, ['Co-op results: FR50 at place 1 (was 2): +10 XP, +10,000 CR '
                                 '(the prize less the 5,000 CR the online race paid)'])

    def test_the_prize_is_less_what_the_online_race_paid(self):
        """The co-op round's online race paid online winnings of its own: the grant pays the
        rest of the event's prize (the larger of the two, once), never both."""
        self.write(online=20000, unlocks=(4, 31, 231))
        result = coop_grant.apply(self.cfg, self.profile, [
            # FR50 at 2nd: prize 13,000 (15,000 x 7/8); the race paid 2,500 (its next race said)
            {'id': 'r1', 'event': 231, 'place': 2, 'places': 8, 'winnings': [13750, 16250]},
            # FR05 at 2nd: prize 2,600; the last race of the session: the save says 20,000 - 18,000
            {'id': 'r2', 'event': 4, 'place': 2, 'places': 8, 'winnings': [18000, None]}])
        self.assertEqual([(c.code, c.credits, c.online) for c in result.applied],
                         [('FR50', 10500, 2500), ('FR05', 600, 2000)])
        self.assertEqual(self.state()['credits'], 5000 + 10500 + 600)
        # an online race that paid more than the prize: no credits, the result and XP still
        self.write(online=30000)
        result = coop_grant.apply(self.cfg, self.profile, [
            {'id': 'r3', 'event': 4, 'place': 8, 'places': 8, 'winnings': [25000, 30000]}])
        self.assertEqual([(c.place, c.xp, c.credits, c.online) for c in result.applied], [(8, 30, 0, 5000)])
        paid = coop_grant.online_paid
        self.assertIsNone(paid({}))                                  # an older server, or M1's rounds
        self.assertIsNone(paid({'winnings': 'x'}))
        self.assertIsNone(paid({'winnings': [100, None]}))           # nothing to compare
        self.assertIsNone(paid({'winnings': [100, 50]}, 0))          # below the value before: another save
        self.assertEqual(paid({'winnings': [100, 400]}, 900), 300)    # the next race, in this save
        self.assertEqual(paid({'winnings': [100, 400]}, 900, after_ok=False), 800)  # the save, right after
        self.assertIsNone(paid({'winnings': [100, 400]}, 900, after_ok=False, now_ok=False))

    def test_the_notes_come_even_when_the_confirmation_fails(self):
        """The save is changed before the server hears of it: a failed confirmation must not hide
        what was added (it comes back "unchanged" next time and settles then)."""
        import urllib.error
        self.write(online=0)
        coop_grant.record_session(self.cfg, self.profile, 1000, 2000, now=2000)
        lines = []

        def request(base, identity, path, body=None, timeout=0):
            if path == '/coop/results':
                return {'results': [{'id': 'r1', 'event': 4, 'code': 'FR05', 'place': 1, 'places': 8,
                                     'winnings': [0, None], 'at': 1500}]}
            raise urllib.error.URLError('server gone')
        with patch.object(coop_grant, '_request', side_effect=request), \
                patch.object(coop_grant.paths, 'log_error') as logged:
            out = coop_grant.after_session(self.cfg, self.profile, lines.append,
                                           base='http://server:21201', identity=('0009000000001241', 'AB' * 16, 'CD' * 32))
        self.assertEqual(out, 'applied 1, unchanged 0, locked 0, waiting 0')
        self.assertEqual(lines, ['Co-op results: FR05 at place 1 (was not raced): +100 XP, +3,000 CR, '
                                 '1 event(s) unlocked'])
        self.assertEqual(logged.call_args[0][0], 'confirm co-op results')
        self.assertEqual(self.state()['events'], {4: 1})

    def test_a_result_without_winnings_pays_no_prize(self):
        """A round of a milestone 1 ledger (or an older server) says nothing of the online race's
        winnings: the result and the XP go in, the prize does not (it could be paid twice)."""
        result = coop_grant.apply(self.cfg, self.profile, [{'id': 'r1', 'event': 4, 'place': 1, 'places': 8}])
        self.assertEqual([(c.place, c.xp, c.credits, c.online) for c in result.applied], [(1, 100, 0, None)])
        self.assertEqual((self.state()['events'], self.state()['credits']), ({4: 1}, 5000))

    def test_notes_name_the_place_and_cope_with_missing_fields(self):
        self.assertEqual([coop_grant.ordinal(n) for n in (1, 2, 3, 4, 8, 11, 12, 13, 21)],
                         ['1st', '2nd', '3rd', '4th', '8th', '11th', '12th', '13th', '21st'])
        self.assertEqual(coop_grant.locked_note({'event': 231}),
                         'Co-op results: 231 waits: this career has not unlocked 231 yet. It is added '
                         'at the first session after you unlock it; the server keeps it.')
        self.assertTrue(coop_grant.locked_note({'event': 4, 'code': 'FR05', 'place': 7}).startswith(
            'Co-op results: FR05 (7th) waits:'))
        self.assertTrue(coop_grant.locked_note({'event': 4, 'code': 'FR05', 'host': 'DriverA'}).startswith(
            "Co-op results: FR05 (in DriverA's world) waits:"))

    def test_sessions_are_kept_for_a_month(self):
        coop_grant.record_session(self.cfg, self.profile, 1000, 2000, now=2000)
        coop_grant.record_session(self.cfg, self.profile, 3000, 4000, now=2000 + coop_grant.KEEP_SESSIONS + 10)
        self.assertEqual([s['start'] for s in coop_grant.load_sessions(self.cfg)], [3000])

    def test_after_session_fetches_applies_and_confirms(self):
        self.write(online=0)  # right after the session: the save's online winnings close the bracket
        coop_grant.record_session(self.cfg, self.profile, 1000, 2000, now=2000)
        out, sent, lines = self.serve([{'id': 'r1', 'event': 4, 'code': 'FR05', 'place': 1, 'places': 8, 'winnings': PAID0, 'at': 1500},
                                       {'id': 'r2', 'event': 4, 'code': 'FR05', 'place': 4, 'places': 8, 'winnings': PAID0, 'at': 1600}])
        self.assertEqual(out, 'applied 1, unchanged 1, locked 0, waiting 0')
        self.assertEqual(sent[-1][2:], ('/coop/results/applied', b'{"ids": ["r1", "r2"]}'))
        self.assertEqual(lines, ['Co-op results: FR05 at place 1 (was not raced): +100 XP, +3,000 CR, '
                                 '1 event(s) unlocked'])
        self.assertEqual(self.state()['events'], {4: 1})
        self.assertEqual(coop_grant.after_session(self.cfg, self.profile, base=''), 'no server')

    def grant(self):
        return coop_grant.apply(self.cfg, self.profile,
                                [{'id': 'r1', 'event': 4, 'place': 1, 'places': 8, 'winnings': PAID0}])

    def originals(self):
        return {p.name: p.read_bytes() for p in self.folder.iterdir()}

    def assert_once(self):
        self.assertEqual((self.state()['events'], self.state()['xp'], self.state()['credits']),
                         ({4: 1}, 1100, 8000))
        self.assertEqual(self.grant().applied, ())
        self.assertEqual((self.state()['xp'], self.state()['credits']), (1100, 8000))

    def test_second_staged_file_failure_never_changes_originals(self):
        before = self.originals()
        real = os.replace

        def fail_profile(source, target):
            if Path(source).name.startswith('.coop-grant-') and Path(target).name == 'ForzaProfile':
                raise PermissionError('second file unavailable')
            return real(source, target)

        with patch.object(coop_grant.os, 'replace', side_effect=fail_profile):
            with self.assertRaises(PermissionError):
                self.grant()
        self.assertEqual(self.originals(), before)
        saves.recover(self.profile)
        self.grant()
        self.assert_once()

    def test_directory_publish_failure_rolls_back_both_files(self):
        before = self.originals()
        real = os.replace

        def fail_publish(source, target):
            if Path(source).name.startswith('.save-restore-') and Path(target) == self.profile.folder:
                raise PermissionError('directory unavailable')
            return real(source, target)

        with patch.object(saves.os, 'replace', side_effect=fail_publish):
            with self.assertRaises(PermissionError):
                self.grant()
        self.assertEqual(self.originals(), before)
        self.assertFalse((self.profile.folder.parent / '.save-manager-restore.json').exists())
        self.grant()
        self.assert_once()

    def test_interruption_between_directory_renames_recovers_before_session_start(self):
        before = self.originals()
        real = os.replace

        def interrupted(source, target):
            if Path(source).name.startswith('.save-restore-') and Path(target) == self.profile.folder:
                raise SystemExit('process stopped before publish')
            return real(source, target)

        with patch.object(saves.os, 'replace', side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.grant()
        self.assertFalse(self.profile.folder.exists())
        # The first session after a process restart must recover before _start
        # can launch anything, even if no pending co-op result was fetched.
        session = game.Session(SimpleNamespace(save_profile=self.profile))
        try:
            with patch.object(session, '_start', side_effect=lambda: self.assertEqual(self.originals(), before)) as start:
                session.start()
                start.assert_called_once()
        finally:
            session.cleanup()
        self.grant()
        self.assert_once()

    def test_interruption_after_publish_keeps_both_edits_and_retry_does_not_pay_twice(self):
        real = os.replace

        def interrupted(source, target):
            real(source, target)
            if Path(source).name.startswith('.save-restore-') and Path(target) == self.profile.folder:
                raise SystemExit('process stopped after publish')

        with patch.object(saves.os, 'replace', side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.grant()
        saves.recover(self.profile)
        self.assert_once()

    def test_failed_journal_write_leaves_originals_and_backup_intact(self):
        before = self.originals()
        atomic_write = saves._atomic_write

        def fail_journal(path, text):
            if path.name == '.save-manager-restore.json':
                raise OSError('journal unavailable')
            return atomic_write(path, text)

        with patch.object(saves, '_atomic_write', side_effect=fail_journal):
            with self.assertRaises(OSError):
                self.grant()
        self.assertEqual(self.originals(), before)
        self.assertEqual(len(saves.snapshots(self.profile)[0]), 1)
        self.grant()
        self.assert_once()

    def test_real_process_exit_at_either_rename_recovers_and_retries_once(self):
        self.cfg.save()
        child = r'''
from pathlib import Path
import os, sys
from forza_launcher import coop_grant, garage, save_editor, saves
from forza_launcher.config import Config
from test_garage import KEYS
cfg = Config(Path(sys.argv[1]))
profile = saves.profile_for(cfg)
garage._keys = save_editor._keys = lambda _: (KEYS,)
real = os.replace
def replace(source, target):
    real(source, target)
    if ((sys.argv[2] == 'old' and Path(source) == profile.folder) or
        (sys.argv[2] == 'new' and Path(source).name.startswith('.save-restore-') and Path(target) == profile.folder)):
        os._exit(77)
os.replace = replace
coop_grant.apply(cfg, profile, [{'id': 'r1', 'event': 4, 'place': 1, 'places': 8, 'winnings': [0, 0]}])
'''
        for boundary in ('old', 'new'):
            with self.subTest(boundary=boundary):
                self.write()
                before = self.originals()
                result = subprocess.run([sys.executable, '-c', child, str(self.cfg.path), boundary],
                                        cwd=Path(__file__).parent, capture_output=True, text=True)
                self.assertEqual(result.returncode, 77, result.stderr)
                saves.recover(self.profile)
                if boundary == 'old':
                    self.assertEqual(self.originals(), before)
                    self.grant()
                self.assert_once()

    def test_recovery_failure_blocks_session_and_releases_lock(self):
        journal = self.profile.folder.parent / '.save-manager-restore.json'
        journal.write_text('{"old": "invalid", "stage": "invalid"}')
        session = game.Session(SimpleNamespace(save_profile=self.profile))
        with patch.object(session, '_start') as start:
            with self.assertRaises(saves.SaveError):
                session.start()
            start.assert_not_called()
        with saves.locked(self.profile):
            pass

    def test_failed_rollback_preserves_journal_for_later_recovery(self):
        before = self.originals()
        real = os.replace

        def unavailable(source, target):
            if Path(target) == self.profile.folder:
                raise PermissionError('publish and rollback unavailable')
            return real(source, target)

        with patch.object(saves.os, 'replace', side_effect=unavailable):
            with self.assertRaises(PermissionError):
                self.grant()
        self.assertTrue((self.profile.folder.parent / '.save-manager-restore.json').is_file())
        saves.recover(self.profile)
        self.assertEqual(self.originals(), before)
        self.grant()
        self.assert_once()

    def test_corrupt_staging_backup_is_rejected_without_changing_originals(self):
        before = self.originals()
        create = saves._create

        def corrupted(*args):
            snapshot = create(*args)
            (snapshot.path / 'save/ForzaProfile/PlayerDatabase').write_bytes(b'corrupt backup')
            return snapshot

        with patch.object(saves, '_create', side_effect=corrupted):
            with self.assertRaisesRegex(saves.SaveError, 'integrity'):
                self.grant()
        self.assertEqual(self.originals(), before)


if __name__ == '__main__':
    unittest.main()
