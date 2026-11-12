"""Synthetic authenticated profiles; no real game keys or save data in fixtures."""
import sys
from pathlib import Path
import hmac
import os
import struct
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import save_editor as editor, saves


KEYS = (bytes(range(16)), bytes(range(32)))


def field(name, kind, value):
    name = name.encode()
    data = value if kind == 15 else struct.pack('>'+editor.FORMATS[kind], value)
    return struct.pack('>I', len(name))+name+struct.pack('>IIB', 0x20, 0, kind)+data


def body(tail=b'opaque class state\0\xff\xbb'):
    main = struct.pack('>I', 6) + field('Credits', 3, 500) + field('XP', 7, 100) + field('Level', 3, 2)
    main += field('WristbandLevel', 7, 1)+field('UnrelatedFlag', 0, True)+field('UnknownCounter', 4, 2**40)
    return struct.pack('>I', 1)+field('Main', 15, main)+tail


def car_challenges(flags=b'\0\0\0\0', cars=((309, ((0x1001, 0, b''), (0x1002, 0, b'\0'*8))),
                                               (404, ((0x1003, 3, b''),)))):
    """The 1000 CLUB entry of the class state: per car its challenges, then the tutorial flags."""
    payload = struct.pack('>I', len(cars))
    for car, challenges in cars:
        payload += struct.pack('>II', car, len(challenges))
        for challenge, value, extra in challenges:
            payload += struct.pack('>IIH', challenge, value, len(extra))+extra
    name = struct.pack('>I', 18)+b'CCarChallengeState'
    return b'class state '+name*2+struct.pack('>II', 4, len(payload)+len(flags))+payload+flags+b' more state'


def profile_bytes(data=None):
    data = body() if data is None else data
    padding = b'\xbb'*(-len(data)%16)
    iv = bytes(range(16))
    seed = struct.pack('>I', len(data) ^ editor.reduce(editor.operator.xor, struct.unpack('>4I', iv)))+iv
    payload = seed+editor._encrypt_cbc(KEYS[0], data+padding, iv)
    return b'cmss'+struct.pack('>I', 32+len(payload))+hmac.digest(KEYS[1], payload, 'sha256')+payload+b'untouched file capacity'


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.profile = saves.Profile(root, root/'data', 'local', 'Fixture')
        self.file = self.profile.folder/'ForzaProfile/ForzaProfile'
        self.file.parent.mkdir(parents=True)
        self.original = profile_bytes()
        self.file.write_bytes(self.original)
        (self.file.parent/'PlayerDatabase').write_bytes(b'untouched companion')
        self.doc = editor.decode(self.profile, self.original, (KEYS,))

    def test_nist_aes_cbc_vector_with_standard_library_fallback(self):
        key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
        iv = bytes.fromhex('000102030405060708090a0b0c0d0e0f')
        data = bytes.fromhex('6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51')
        expected = bytes.fromhex('7649abac8119b246cee98e9b12e9197d5086cb9b507219ee95db113a917678b2')
        with patch.dict('sys.modules', {'cryptography.hazmat.primitives.ciphers': None}):
            self.assertEqual(editor._encrypt_cbc(key, data, iv), expected)

    def test_no_edit_round_trip_is_byte_identical(self):
        self.assertEqual(editor.encode(self.doc, {}), self.original)
        result = editor.apply(self.doc, {'Main/Credits': 500})
        self.assertIsNone(result.backup)
        self.assertEqual(saves.snapshots(self.profile), ([], 0))

    def test_key_derivation_uses_separate_aes_and_hmac_table_blocks(self):
        from forza_launcher.config import Config
        cfg = Config(self.profile.home/'launcher.json')
        cfg['game_root'] = str(self.profile.home/'game')
        cfg.game_files().mkdir()
        (cfg.game_files()/'default.xex').write_bytes(b'fake')
        window = bytes(range(200, 232)) + bytes(range(64))  # TABLE_LEAD bytes, then the table
        owner = bytes.fromhex('B13EBABEBABEBABE')
        with patch.object(editor, '_tables', return_value=(window,)):
            (aes, mac), (base_aes, base_mac) = editor._keys(cfg)
        self.assertEqual(aes, bytes(((i ^ owner[(i-2)%8])+2*i)%256 for i in range(16)))
        self.assertEqual(mac, bytes((((i+32) ^ owner[(i-4)%8])+2*i)%256 for i in range(32)))
        # a profile the base game wrote (before TU4 converts it): the same AES key, the HMAC
        # key from the 32 bytes before the table
        self.assertEqual(base_aes, aes)
        self.assertEqual(base_mac, bytes((((200+i) ^ owner[(i-4)%8])+2*i)%256 for i in range(32)))
        # the garage's keys come from the same table, both builds alike
        from forza_launcher import garage
        with patch.object(editor, '_tables', return_value=(window,)):
            ((garage_aes, garage_mac),) = garage._keys(cfg)
        self.assertEqual(garage_aes, bytes((((48+i) ^ owner[(i-5)%8])+2*i)%256 for i in range(16)))
        self.assertEqual(garage_mac, bytes((((16+i) ^ owner[(i-1)%8])+2*i)%256 for i in range(32)))

    def test_edit_changes_only_allowed_body_bytes_and_preserves_capacity(self):
        changed = editor.encode(self.doc, {'Main/Credits': 1_000_000, 'Main/XP': 450_000})
        after = editor.decode(self.profile, changed, (KEYS,))
        self.assertEqual(after.values['Main/Credits'].value, 1_000_000)
        self.assertEqual(after.values['Main/XP'].value, 450_000)
        allowed = {offset+i for path in ('Main/Credits', 'Main/XP')
                   for offset in (self.doc.values[path].offset,) for i in range(4)}
        self.assertTrue(all(a == b or i in allowed for i, (a, b) in enumerate(zip(self.doc.body, after.body))))
        self.assertEqual(after.padding, self.doc.padding)
        self.assertTrue(changed.endswith(b'untouched file capacity'))
        self.assertEqual(len(changed), len(self.original))

    def test_apply_keeps_full_before_edit_backup_and_can_restore_it(self):
        result = editor.apply(self.doc, {'Main/Credits': 1000})
        self.assertEqual(result.backup.kind, 'before-edit')
        self.assertEqual(result.document.values['Main/Credits'].value, 1000)
        self.assertEqual((result.backup.path/'save/ForzaProfile/ForzaProfile').read_bytes(), self.original)
        self.assertEqual((self.file.parent/'PlayerDatabase').read_bytes(), b'untouched companion')
        saves.restore(self.profile, result.backup)
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_all_career_wristbands_round_trip_without_changing_xp_or_other_state(self):
        self.assertEqual(editor.WRISTBANDS, ((0, 'Yellow'), (1, 'Green'), (2, 'Blue'), (3, 'Pink'),
                                            (4, 'Orange'), (5, 'Purple'), (6, 'Gold')))
        for value, name in editor.WRISTBANDS:
            with self.subTest(wristband=name):
                changed = editor.encode(self.doc, {'Main/WristbandLevel': value})
                after = editor.decode(self.profile, changed, (KEYS,))
                self.assertEqual(after.values['Main/WristbandLevel'].value, value)
                offset = self.doc.values['Main/WristbandLevel'].offset
                self.assertTrue(all(a == b or offset <= i < offset+4
                                    for i, (a, b) in enumerate(zip(self.doc.body, after.body))))
                self.assertEqual(after.values['Main/XP'].value, 100)

    def test_unknown_existing_wristband_is_preserved_when_editing_other_fields(self):
        data = bytearray(self.doc.body)
        struct.pack_into('>i', data, self.doc.values['Main/WristbandLevel'].offset, 8)
        document = editor.decode(self.profile, profile_bytes(bytes(data)), (KEYS,))
        changed = editor.decode(self.profile, editor.encode(document, {'Main/Credits': 1000}), (KEYS,))
        self.assertEqual(changed.values['Main/WristbandLevel'].value, 8)

    def test_unfinished_car_challenge_tutorial_is_found_and_only_its_flags_change(self):
        document = editor.decode(self.profile, profile_bytes(body(car_challenges())), (KEYS,))
        tutorial = document.values[editor.CAR_CHALLENGE_TUTORIAL]
        self.assertFalse(tutorial.value)
        self.assertEqual(document.body[tutorial.offset:tutorial.offset+4], b'\0\0\0\0')
        changed = editor.encode(document, {editor.CAR_CHALLENGE_TUTORIAL: True})
        after = editor.decode(self.profile, changed, (KEYS,))
        self.assertTrue(after.values[editor.CAR_CHALLENGE_TUTORIAL].value)
        self.assertEqual([i for i, (a, b) in enumerate(zip(document.body, after.body)) if a != b],
                         list(range(tutorial.offset, tutorial.offset+4)))
        self.assertEqual(len(changed), len(document.original))
        partly = editor.decode(self.profile, profile_bytes(body(car_challenges(b'\1\0\0\0'))), (KEYS,))
        self.assertFalse(partly.values[editor.CAR_CHALLENGE_TUTORIAL].value)

    def test_finished_missing_or_unfamiliar_car_challenge_state_is_left_alone(self):
        finished = editor.decode(self.profile, profile_bytes(body(car_challenges(b'\1\1\1\1'))), (KEYS,))
        self.assertTrue(finished.values[editor.CAR_CHALLENGE_TUTORIAL].value)
        self.assertNotIn(editor.CAR_CHALLENGE_TUTORIAL, self.doc.values)
        with self.assertRaises(saves.SaveError): editor.encode(self.doc, {editor.CAR_CHALLENGE_TUTORIAL: True})
        name = struct.pack('>I', 18)+b'CCarChallengeState'
        for tail in (car_challenges(b'\2\0\0\0'), car_challenges(b'\0\0\0'), car_challenges(b'\0'*5),
                     car_challenges()+car_challenges(), car_challenges()[:-20],
                     car_challenges().replace(struct.pack('>IIH', 0x1002, 0, 8), struct.pack('>IIH', 0x1002, 0, 9)),
                     name*2+b'\0\0'):
            with self.subTest(tail=tail[-24:]):
                document = editor.decode(self.profile, profile_bytes(body(tail)), (KEYS,))
                self.assertNotIn(editor.CAR_CHALLENGE_TUTORIAL, document.values)

    def test_stale_document_cannot_overwrite_newer_save(self):
        self.file.write_bytes(b'newer save')
        with self.assertRaisesRegex(saves.SaveError, 'changed'): editor.apply(self.doc, {'Main/Credits': 999})
        self.assertEqual(self.file.read_bytes(), b'newer save')

    def test_game_lock_blocks_load_and_edit(self):
        with saves.locked(self.profile):
            with self.assertRaises(saves.SaveError): editor.apply(self.doc, {'Main/Credits': 999})
            with self.assertRaises(saves.SaveError): editor.load(None, self.profile)

    def test_tampered_ciphertext_and_wrong_owner_keys_fail_authentication(self):
        bad = bytearray(self.original)
        bad[65] ^= 1
        with self.assertRaisesRegex(saves.SaveError, 'integrity'): editor.decode(self.profile, bytes(bad), (KEYS,))
        with self.assertRaises(saves.SaveError): editor.decode(self.profile, self.original, ((bytes(16), bytes(32)),))

    def test_bad_stream_lengths_and_field_layouts_are_rejected(self):
        for length in (0, 67, len(self.original), 0xffffffff):
            bad = bytearray(self.original)
            struct.pack_into('>I', bad, 4, length)
            with self.assertRaises(saves.SaveError): editor.decode(self.profile, bytes(bad), (KEYS,))
        for data in (b'\xff'*4, struct.pack('>I',1)+field('Bad',1,0), body().replace(b'Credits', b'Creditx')):
            with self.assertRaises(saves.SaveError): editor.decode(self.profile, profile_bytes(data), (KEYS,))

    def test_edit_limits_and_unknown_fields_are_rejected(self):
        for path, value in (('Main/Credits', -1), ('Main/Credits', 1_000_000_000), ('Main/XP', 2**31),
                            ('Main/XP', True), ('Main/Level', 50), ('Main/Credits', 1.5),
                            ('Main/WristbandLevel', -1), ('Main/WristbandLevel', 7)):
            with self.subTest(path=path, value=value):
                with self.assertRaises(saves.SaveError): editor.apply(self.doc, {path:value})
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_cancel_and_backup_failure_do_not_replace_the_profile(self):
        with self.assertRaises(saves.Cancelled):
            editor.apply(self.doc, {'Main/Credits': 1000}, cancelled=lambda: True)
        with patch.object(saves, '_create', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): editor.apply(self.doc, {'Main/Credits': 1000})
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_failed_atomic_replace_leaves_current_save_and_safety_backup(self):
        real_replace = os.replace
        def fail(source, target):
            if Path(source).name.startswith('.profile-edit-'):
                raise PermissionError('fixture failure')
            return real_replace(source, target)
        with patch.object(editor.os, 'replace', side_effect=fail):
            with self.assertRaises(PermissionError): editor.apply(self.doc, {'Main/Credits': 1000})
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(len(saves.snapshots(self.profile)[0]), 1)
        self.assertFalse(list(self.file.parent.glob('.profile-edit-*')))

    def test_game_save_changes_during_backup_are_not_overwritten(self):
        create = saves._create
        def changed(*args):
            result = create(*args)
            self.file.write_bytes(b'written by another process')
            return result
        with patch.object(saves, '_create', side_effect=changed):
            with self.assertRaisesRegex(saves.SaveError, 'changed'): editor.apply(self.doc, {'Main/Credits': 1000})
        self.assertEqual(self.file.read_bytes(), b'written by another process')


def entry(name, cls, data, version=1):
    return b''.join(struct.pack('>I', len(t))+t.encode() for t in (name, cls))+struct.pack('>II', version, len(data))+data


LOCATION = editor.LOCATION_STATE


def class_state(*entries):
    """The top-level class state after the fields: a lead word, the entries by name, a trailer."""
    entries = sorted(entries, key=lambda e: e[0])
    return struct.pack('>II', 2, len(entries))+b''.join(entry(*e) for e in entries)+editor.STATE_TRAILER


FH1_STATE = (('Colorado', LOCATION, b'C'*900, 2), ('Player0', 'CPlayerState', b'P'*51, 13),
             ('TradingPaintChallenge', 'CSkillsFreeRoamChallengeState', b'\1'*5),
             ('WindFarmToNWCableCarStationBeaumontDaytime', 'CBasicChallengeState', b'\1'))


def norm(*entries):
    return sorted((e[0], e[1], e[2], e[3] if len(e) > 3 else 1) for e in entries)


def circuit(name, size=800):
    return (name, LOCATION, bytes([len(name)])*size, 2)


def container(data, capacity):
    """What the game writes: the stream signed and encrypted, cut at a fixed container like the
    game's 51,200-byte ForzaProfile when it is too long (#850)."""
    whole = profile_bytes(data)[:-len(b'untouched file capacity')]
    if len(whole) <= capacity+8:
        return whole+b'\0'*(capacity+8-len(whole))
    cut = bytearray(whole[:capacity+8])
    struct.pack_into('>I', cut, 4, capacity)
    return bytes(cut)


class CircuitStateTests(unittest.TestCase):
    """FM4 circuits' location states fill the fixed ForzaProfile container (#850)."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.profile = saves.Profile(root, root/'data', 'local', 'Fixture')
        self.file = self.profile.folder/'ForzaProfile/ForzaProfile'
        self.file.parent.mkdir(parents=True)
        patcher = patch.object(editor, '_keys', return_value=((bytes(16), bytes(32)), KEYS))
        patcher.start()
        self.addCleanup(patcher.stop)

    def trim(self, raw, keep=''):
        self.file.write_bytes(raw)
        return editor.trim_circuit_states(None, self.profile, keep)

    def states(self):
        document = editor.decode(self.profile, self.file.read_bytes(), (KEYS,))
        return [(n, c, d, v) for n, c, v, d in editor._states(document.body)[2]]

    def test_circuit_states_leave_the_profile_after_a_backup_but_the_one_being_started(self):
        raw = container(body(class_state(*FH1_STATE, circuit('Suzuka'), circuit('Alps'))), 8000)
        result = self.trim(raw)
        self.assertEqual(result.removed, ('Alps', 'Suzuka'))
        self.assertFalse(result.repaired)
        self.assertEqual(result.note(), '')
        self.assertEqual((result.backup.path/'save/ForzaProfile/ForzaProfile').read_bytes(), raw)
        self.assertEqual(result.backup.kind, 'automatic')  # rotated: a trim can come with every start
        self.assertEqual(self.states(), norm(*FH1_STATE))
        after = self.file.read_bytes()
        self.assertEqual(len(after), len(raw))
        self.assertEqual(editor.decode(self.profile, after, (KEYS,)).body,
                         body(class_state(*FH1_STATE)))
        self.assertIsNone(self.trim(after))  # nothing left to take out: no backup, no write
        self.assertEqual(len(saves.snapshots(self.profile)[0]), 1)
        result = self.trim(raw, keep='suzuka')
        self.assertEqual(result.removed, ('Alps',))
        self.assertEqual(self.states(), norm(*FH1_STATE, circuit('Suzuka')))

    def test_the_rally_expansion_stays_and_a_circuit_matches_its_key_in_any_case(self):
        rally = ('ColoradoDirt', LOCATION, b'D'*600, 2)
        raw = container(body(class_state(*FH1_STATE, rally, circuit('TopGear'), circuit('Suzuka'))), 9000)
        result = self.trim(raw, keep='topgear')
        self.assertEqual(result.removed, ('Suzuka',))
        self.assertEqual(self.states(), norm(*FH1_STATE, rally, circuit('TopGear')))

    def test_a_profile_the_game_cut_in_a_circuit_state_is_made_whole_again(self):
        full = body(class_state(*FH1_STATE, circuit('Suzuka'), circuit('TopGear', 3000)))
        raw = container(full, 3500)  # ends inside TopGear's state
        with self.assertRaises(saves.SaveError): editor.decode(self.profile, raw, (KEYS,))
        result = self.trim(raw)
        self.assertTrue(result.repaired)
        self.assertEqual(result.removed, ('Suzuka', 'TopGear'))
        self.assertIn('Repaired your save', result.note())
        self.assertEqual(result.backup.label, 'Before repairing the profile')
        self.assertEqual(result.backup.kind, 'before-edit')  # the damaged copy is never rotated away
        self.assertEqual((result.backup.path/'save/ForzaProfile/ForzaProfile').read_bytes(), raw)
        # what came after the cut comes back as a profile that has not played it
        defaults = [(n, c, d, v) for n, (c, v, d) in editor.LOST_DEFAULTS.items()]
        self.assertEqual(self.states(), norm(*FH1_STATE[:2], *defaults))
        self.assertEqual(editor.decode(self.profile, self.file.read_bytes(), (KEYS,)).values['Main/Credits'].value, 500)

    def test_the_start_backup_is_not_copied_again_for_the_trim(self):
        raw = container(body(class_state(*FH1_STATE, circuit('Suzuka'))), 8000)
        self.file.write_bytes(raw)
        started = saves.create(self.profile, kind='automatic', keep=3)
        result = editor.trim_circuit_states(None, self.profile, keep_automatic=3)
        self.assertEqual(result.removed, ('Suzuka',))
        self.assertEqual(result.backup.path, started.path)
        self.assertEqual(len(saves.snapshots(self.profile)[0]), 1)

    def test_cuts_it_cannot_rebuild_and_other_damage_only_warn(self):
        big = (('Player0', 'CPlayerState', b'P'*3000, 13),) + FH1_STATE[2:]
        sponsor = ('SponsorshipState', 'CSponsorshipState', b'S'*871)
        before = body(class_state(*FH1_STATE, circuit('Silverstone', 3000), sponsor))
        cases = {'cut in an FH1 entry': container(body(class_state(FH1_STATE[0], circuit('Alps'), *big)), 2500),
                 # a circuit that sorts before FH1 entries the cut lost (SponsorshipState)
                 'cut in a circuit before FH1 entries': container(before, 3000),
                 'wrong signature': bytes(bytearray(profile_bytes(body(class_state(*FH1_STATE))))[:-200]
                                          + b'\0'*200)}
        tampered = bytearray(container(body(class_state(*FH1_STATE, circuit('Alps'))), 8000))
        short = bytearray(tampered)
        struct.pack_into('>I', short, 4, struct.unpack_from('>I', short, 4)[0]-16)
        cases['short of its length, not at its container'] = bytes(short)
        tampered[100] ^= 1
        cases['tampered'] = bytes(tampered)
        for name, raw in cases.items():
            with self.subTest(name):
                result = self.trim(raw)
                self.assertEqual(result.note(), editor.DAMAGED)
                self.assertIsNone(result.backup)
                self.assertEqual(self.file.read_bytes(), raw)
        self.assertEqual(saves.snapshots(self.profile), ([], 0))

    def test_unfamiliar_layouts_missing_files_and_missing_keys_are_left_alone(self):
        raw = profile_bytes()  # readable, but its class state is not the game's
        self.assertIsNone(self.trim(raw))
        self.assertEqual(self.file.read_bytes(), raw)
        self.file.unlink()
        self.assertIsNone(editor.trim_circuit_states(None, self.profile))
        self.file.write_bytes(raw)
        with patch.object(editor, '_keys', side_effect=saves.SaveError('Choose your FH1 game files')):
            self.assertIsNone(self.trim(container(body(class_state(*FH1_STATE, circuit('Alps'))), 8000)))
        self.assertEqual(saves.snapshots(self.profile), ([], 0))

    def test_game_lock_blocks_the_trim(self):
        self.file.write_bytes(container(body(class_state(*FH1_STATE, circuit('Alps'))), 8000))
        with saves.locked(self.profile):
            with self.assertRaises(saves.SaveError):
                editor.trim_circuit_states(None, self.profile)


if __name__ == '__main__': unittest.main()
