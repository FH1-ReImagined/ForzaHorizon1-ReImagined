"""tools/career_state.py (#309, docs/coop_campaign.md "The two-game acceptance test"): a save's
career state read with the launcher's codecs, on synthetic saves (no game rows or key material)."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import saves
from test_coop_grant import game_database, player_database, profile_body
from test_garage import KEYS, envelope
from test_save_editor import profile_bytes

_spec = importlib.util.spec_from_file_location(
    'career_state', Path(__file__).resolve().parent.parent / 'tools' / 'career_state.py')
career_state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(career_state)


class CareerStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.save = self.root / '00000001'
        (self.save / 'ForzaProfile').mkdir(parents=True)
        (self.save / 'ForzaProfile/ForzaProfile').write_bytes(
            profile_bytes(profile_body(xp=1230, credits=6400, wristband=2, online=2500)))
        (self.save / 'ForzaProfile/PlayerDatabase').write_bytes(
            envelope(player_database(results=[(4, 11, 7)], unlocks=(4, 31))))
        game_database(self.root / 'gamedb.slt')

    def state(self, *events):
        with closing(sqlite3.connect(self.root / 'gamedb.slt')) as gamedb:
            return career_state.state(self.save, (KEYS,), (KEYS,), gamedb, events)

    def test_profile_values_and_tables(self):
        out = self.state()
        self.assertEqual(out['profile'], {'Main/XP': 1230, 'Main/Credits': 6400, 'Main/WristbandLevel': 2,
                                          'Main/OnlineWinnings': 2500})
        self.assertEqual(out['tables'], {'Career_EventResults': 1, 'Career_RaceResults': 1,
                                         'CareerEventUnlocks': 2, 'CareerHubUnlocks': 0})
        self.assertNotIn('prize_cars', out)  # no Career_Garage in this synthetic save

    def test_events_by_code_or_id(self):
        out = self.state('fr05', '231', 'NOPE')
        self.assertEqual(out['events']['FR05'], {'id': 4, 'place': 7, 'races': {'11': 7}, 'unlocked': True})
        self.assertEqual(out['events']['FR50'], {'id': 231, 'place': None, 'races': {'12': None},
                                                 'unlocked': False})
        self.assertEqual(out['events']['NOPE'], 'not an event of this game')

    def test_wrong_keys_are_refused(self):
        with self.assertRaises(saves.SaveError):
            career_state.state(self.save, ((bytes(16), bytes(32)),), (KEYS,))

    def test_nothing_is_written(self):
        before = {p.name: p.read_bytes() for p in (self.save / 'ForzaProfile').iterdir()}
        self.state('FR05')
        self.assertEqual({p.name: p.read_bytes() for p in (self.save / 'ForzaProfile').iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
