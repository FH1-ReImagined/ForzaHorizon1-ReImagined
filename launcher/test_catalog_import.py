"""Import transactions and identity rebinding using entirely synthetic encrypted saves."""
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher import catalog_import as ci, game, paths, ugc
paths.ensure_tools_importable()
import livery_codec as codec


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        log_patch = patch.object(paths, 'config_dir', return_value=self.root/'config')
        log_patch.start();self.addCleanup(log_patch.stop)
        self.image = bytes((i*17+23) % 251 for i in range(0x20000))
        self.owner = bytes.fromhex('E000000000000001')
        self.target = ci.Target(self.root, self.root/'profile', bytes.fromhex(game.LOCAL_PROFILE))
        self.folder = self.target.folder
        career = self.folder/'ForzaProfile/ForzaProfile'
        career.parent.mkdir(parents=True)
        key = bytes(((self.image[0xD634+32+i]^self.target.identity[(i-4)%8])+2*i)%256 for i in range(32))
        seed = bytes(20)+bytes(16)
        career.write_bytes(b'cmss'+struct.pack('>I',52+16)+hmac.digest(key,seed,'sha256')+seed)
        self.career = career.read_bytes()
        self.keys = codec.derive(self.image, 0x82000000, self.owner)
        self.dest_keys = codec.derive(self.image, 0x82000000, self.target.identity)
        self.index_keys = codec.derive(self.image,0x82000000,self.owner,index=True)
        self.dest_index_keys = codec.derive(self.image,0x82000000,self.target.identity,index=True)
        self.body = b'crlv'+struct.pack('>II',3,1)+b'synthetic design'
        files = {'0002_layers_1293.liv':codec.encode_stream(self.body,self.keys[0]),
                 'Original Creator.owner': b'', 'thumb.xdc': b'opaque thumbnail',
                 'StorefrontID':codec.encode_stream(struct.pack('>Q',1234), self.keys[0])}
        self.item = ugc.Item('1293/Fixture/0002_layers_1293.liv','Fixture','livery',1293,2,('Original Creator',),True,files)
        db=self.database()
        self.insert(db,self.row(1,'Fixture',1293))
        index=ci._encoded_database(db,self.index_keys[0]);db.close()
        self.catalog=ugc.Catalog(self.root/'source', '4D5309C9', None, (self.item,),self.owner,index)
        self.executable=self.root/'default.xex';self.executable.write_bytes(b'synthetic')
        fake_image=self.image
        class FakeXex:
            raw=bytes(12)+bytes.fromhex('4D5309C9')
            opts={codec.aes.HDR_EXECUTION_INFO:0}
            load_address=0x82000000
            image_size=len(fake_image)
            def __init__(self,*_): pass
            def image(self): return fake_image
            def pages_size(self): return len(fake_image)
        self.xex_patch=patch('apply_title_update.Xex',FakeXex)
        self.xex_patch.start();self.addCleanup(self.xex_patch.stop)

    def database(self):
        db=sqlite3.connect(':memory:')
        kinds=['INTEGER PRIMARY KEY','TEXT','TEXT','INTEGER','INTEGER','INTEGER','INTEGER',
               'TEXT','INTEGER','INTEGER','TEXT','INTEGER','INTEGER','INTEGER','INTEGER','TEXT']
        db.execute('CREATE TABLE MetaData ('+','.join(name+' '+kind for name,kind in zip(ci.COLUMNS,kinds))+')')
        return db

    def row(self,ident,title,model):
        folder=f'LivCatalog:\\{model}\\{title}\\'
        return [ident,title,folder,123,1234,1,0,folder+'thumb.xdc',0,0,folder+'thumb.xdc',0,0,model,2,'Original Creator']

    def insert(self,db,row):
        db.execute('INSERT INTO MetaData VALUES ('+','.join('?' for _ in ci.COLUMNS)+')',row)

    def run_import(self,**kwargs):
        return ci.import_design(self.catalog,self.item.path,self.target,self.executable,**kwargs)

    def files(self):
        return ci._tree(self.folder/'LivCatalog',lambda:False,self.target.root)

    def existing(self):
        db=self.database();self.insert(db,self.row(4,'Existing',999))
        current=self.folder/'LivCatalog';current.mkdir()
        (current/ci.INDEX).write_bytes(ci._encoded_database(db,self.dest_index_keys[0]));db.close()
        (current/'existing.liv').write_bytes(b'opaque existing')
        return self.files()

    def test_rebind_preserves_plaintext_creator_locks_and_existing_entries(self):
        before=self.existing();backup=self.run_import()
        current=self.files()
        self.assertEqual(current['existing.liv'],before['existing.liv'])
        payload=current[self.item.path]
        self.assertEqual(codec.decode(payload,self.dest_keys).body,self.body)
        with self.assertRaises(ValueError): codec.decode(payload,self.keys)
        self.assertEqual(current['1293/Fixture/Original Creator.owner'],b'')
        self.assertEqual(codec.decode_stream(current['1293/Fixture/StorefrontID'],self.dest_keys).body,struct.pack('>Q',1234))
        db,_=ci._database(current[ci.INDEX],self.dest_index_keys)
        rows=db.execute('SELECT Title,IsLocked,Owner FROM MetaData ORDER BY SaveId').fetchall();db.close()
        self.assertEqual(rows,[('Existing',1,'Original Creator'),('Fixture',1,'Original Creator')])
        for name,raw in before.items():
            self.assertEqual((backup/'LivCatalog'/name).read_bytes(),raw)
        manifest=json.loads((backup/'manifest.json').read_text())
        self.assertEqual(manifest['files'],{n:hashlib.sha256(d).hexdigest() for n,d in before.items()})
        self.assertEqual((self.folder/'ForzaProfile/ForzaProfile').read_bytes(),self.career)
        with self.assertRaisesRegex(ValueError,'already exists'):self.run_import()

    def test_first_catalog_and_cancellation_leave_career_unchanged(self):
        backup=self.run_import()
        self.assertFalse(json.loads((backup/'manifest.json').read_text())['existed'])
        self.assertEqual((self.folder/'ForzaProfile/ForzaProfile').read_bytes(),self.career)
        before=self.files()
        with self.assertRaisesRegex(ValueError,'cancelled'):self.run_import(cancelled=lambda:True)
        self.assertEqual(self.files(),before)

    def test_wrong_identity_tampering_and_non_fh1_refused_before_mutation(self):
        from dataclasses import replace
        before=self.existing()
        for cat in [replace(self.catalog,profile_id=bytes(8)),replace(self.catalog,title_id='4D530910'),
                    replace(self.catalog,index=self.catalog.index[:-1]+bytes([self.catalog.index[-1]^1]))]:
            with self.subTest(),self.assertRaises(ValueError):
                ci.import_design(cat,self.item.path,self.target,self.executable)
            self.assertEqual(self.files(),before)
        wrong=ci.Target(self.root,self.target.root,bytes.fromhex('E000000000000002'))
        with self.assertRaisesRegex(ValueError,'career does not authenticate'):
            ci.import_design(self.catalog,self.item.path,wrong,self.executable)
        self.assertEqual(self.files(),before)

    def test_publication_failure_rolls_back_and_keeps_verified_backup(self):
        before=self.existing()
        rename=Path.rename
        def fail_stage(path,dest):
            if path.name.startswith('.catalog-stage-'):raise OSError('injected publication failure')
            return rename(path,dest)
        with patch.object(Path,'rename',fail_stage),self.assertRaisesRegex(OSError,'injected'):
            self.run_import()
        self.assertEqual(self.files(),before)
        self.assertFalse((self.folder/'.catalog-import.json').exists())
        self.assertTrue(list((self.root/'save-backups/catalog-imports').glob('*/manifest.json')))

    def test_crash_recovery_restores_old_catalog(self):
        before=self.existing();token='a'*32
        current=self.folder/'LivCatalog';current.rename(self.folder/('.catalog-old-'+token))
        current.mkdir();(current/'partial').write_bytes(b'partial')
        (self.folder/'.catalog-import.json').write_text(json.dumps(dict(token=token,existed=True,
            originals={n:hashlib.sha256(d).hexdigest() for n,d in before.items()})))
        with ci.locked(self.target): ci.recover(self.target)
        self.assertEqual(self.files(),before)

    def test_changed_recovery_backup_is_not_restored_over_the_current_catalog(self):
        before=self.existing();token='b'*32
        hold=self.folder/('.catalog-old-'+token);hold.mkdir();(hold/'wrong').write_bytes(b'wrong')
        (self.folder/'.catalog-import.json').write_text(json.dumps(dict(token=token,existed=True,
            originals={n:hashlib.sha256(d).hexdigest() for n,d in before.items()})))
        with ci.locked(self.target),self.assertRaisesRegex(ValueError,'Nothing was removed'):
            ci.recover(self.target)
        self.assertEqual(self.files(),before)

    def test_game_lease_and_linked_paths_block_import(self):
        with ci.locked(self.target),self.assertRaisesRegex(ValueError,'Close the game'):
            self.run_import()
        outside=self.root/'outside';outside.mkdir()
        try:(self.folder/'LivCatalog').symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('symlinks unavailable')
        with self.assertRaisesRegex(ValueError,'Linked'): self.run_import()
        self.assertEqual(list(outside.iterdir()),[])

    def link(self,path,to):
        try:path.symlink_to(to,target_is_directory=to.is_dir())
        except OSError:self.skipTest('symlinks unavailable')

    def test_data_folder_reached_through_a_linked_parent_imports(self):
        # A Steam Deck SD card (/run/media/<card> is a link), /home on Fedora Atomic, ~/Games
        # linked to another drive: links above the data folder are the player's layout (#719).
        card=self.root/'card';self.link(card,self.root)
        before=self.existing()
        target=ci.Target(card,card/'profile',self.target.identity)
        self.assertEqual((target.home,target.root),(self.root,self.target.root))
        backup=ci.import_design(self.catalog,self.item.path,target,card/'default.xex')
        self.assertTrue(backup.is_relative_to(self.root/'save-backups/catalog-imports'))
        self.assertEqual(self.files()['existing.liv'],before['existing.liv'])
        self.assertIn(self.item.path,self.files())
        self.assertIn('Latest verified catalog backup',ci.recovery_warning(target,'test.'))
        # Play recovers the catalog through the same linked path without a warning.
        plan=game.LaunchPlan(game=None,cwd=card,argv=['forza',f'--user_data_root={card/"profile"}'],
            env={'FORZA_XUID':target.identity.hex()},unset=[],toml_path=card/'forza.toml',
            toml_text='',log_dir=card)
        self.assertEqual(ci.target_for_plan(plan),target)
        session=game.Session(plan,log=lambda _:None)
        try:
            with patch('subprocess.Popen') as spawn,patch.object(ci,'recover',wraps=ci.recover) as recover:
                session.start();spawn.assert_called_once();recover.assert_called_once()
            self.assertEqual(plan.notes,[])
        finally:session.cleanup()

    def test_links_inside_the_data_folders_are_refused_wherever_they_point(self):
        before=self.existing()
        outside=Path(self.temp.name+'-outside');outside.mkdir();self.addCleanup(outside.rmdir)
        inside=self.folder/'Elsewhere';inside.mkdir()
        cases=[(self.folder/'LivCatalog',outside),          # out of the data folder
               (self.folder/'LivCatalog',inside),           # inside it: refused like the save manager
               (self.folder/'.catalog-import.json',outside), # a journal written through a link
               (self.root/'save-backups',outside)]          # backups and the lock under the home
        for link,to in cases:
            with self.subTest(link=link.name,to=to.name):
                hold=link.with_name(link.name+'.real')
                if link.exists():link.rename(hold)
                self.link(link,to)
                try:
                    with self.assertRaisesRegex(ValueError,'Linked'):self.run_import()
                finally:
                    link.unlink()
                    if hold.exists():hold.rename(link)
                self.assertEqual(list(outside.iterdir()),[]);self.assertEqual(list(inside.iterdir()),[])
                self.assertEqual(self.files(),before)
        # A folder between the data folder and the save is inside it too.
        profile=self.target.root/game.LOCAL_PROFILE;moved=self.root/'moved'
        profile.rename(moved);self.link(profile,moved)
        with self.assertRaisesRegex(ValueError,'Linked'):self.run_import()
        with self.assertRaisesRegex(ValueError,'Linked'):ci.recover(self.target)
        with self.assertRaisesRegex(ValueError,'outside'):ci._direct(self.root/'x',self.target.root)

    def test_source_and_destination_index_round_trip_and_size_bounds(self):
        db,_=ci._database(self.catalog.index,self.index_keys);db.close()
        with self.assertRaises(ValueError):ci._database(self.catalog.index,self.dest_index_keys)
        raw=codec.encode_stream(struct.pack('>3I',ci.MAX_INDEX+1,1,ci.MAX_INDEX+1)+b'x',self.index_keys[0])
        with self.assertRaisesRegex(ValueError,'compression'):ci._database(raw,self.index_keys)

    def test_plan_resolves_overrides_and_environment_identity(self):
        from types import SimpleNamespace
        plan=SimpleNamespace(log_dir=self.root,cwd=self.root,env={'FORZA_XUID':'E000000000000003'},
            argv=['forza','--user_data_root=relative','--profile_xuid=E000000000000002'])
        result=ci.target_for_plan(plan)
        self.assertEqual(result.root,self.root/'relative')
        self.assertEqual(result.identity.hex().upper(),'E000000000000003')

    def test_session_holds_the_same_lease_until_cleanup(self):
        plan=game.LaunchPlan(game=None,cwd=self.root,
            argv=[sys.executable,'-c','import time;time.sleep(10)',f'--user_data_root={self.target.root}'],
            env={'FORZA_XUID':self.target.identity.hex()},unset=[],toml_path=self.root/'forza.toml',
            toml_text='',log_dir=self.root)
        session=game.Session(plan)
        try:
            session.start()
            with self.assertRaisesRegex(ValueError,'Close the game'):
                with ci.locked(self.target):pass
            session.proc.terminate();session.proc.wait(timeout=5);session.poll();session.cleanup()
            with ci.locked(self.target):pass
        finally:
            if session.proc and session.proc.poll() is None:
                session.proc.kill();session.proc.wait(timeout=5);session.poll();session.cleanup()

    def test_session_reuses_save_manager_lock_and_recovers_before_launch(self):
        plan=game.LaunchPlan(game=None,cwd=self.root,argv=['forza'],env={},unset=[],
            toml_path=self.root/'forza.toml',toml_text='',log_dir=self.root,
            save_profile=self.target)
        session=game.Session(plan)
        try:
            with patch.object(ci,'locked',wraps=ci.locked) as lease, \
                    patch.object(ci,'recover',wraps=ci.recover) as recover, \
                    patch('subprocess.Popen') as spawn:
                session.start()
                lease.assert_not_called()
                recover.assert_called_once()
                spawn.assert_called_once()
                with self.assertRaisesRegex(ValueError,'Close the game'):
                    with ci.locked(self.target):pass
        finally:
            session.cleanup()
        with ci.locked(self.target):pass

    def test_a_busy_save_does_not_start_even_a_local_server(self):
        plan=game.LaunchPlan(game=None,cwd=self.root,argv=['forza'],env={},unset=[],
            toml_path=self.root/'forza.toml',toml_text='',log_dir=self.root,
            server_argv=['server','--data-dir',str(self.root/'server')])
        with ci.locked(self.target),patch('subprocess.Popen') as spawn:
            with self.assertRaisesRegex(ValueError,'Close the game'):
                game.Session(plan).start()
        spawn.assert_not_called()

    def session_plan(self, save_manager=False):
        return game.LaunchPlan(game=None,cwd=self.root,
            argv=['forza',f'--user_data_root={self.target.root}'],
            env={'FORZA_XUID':self.target.identity.hex()},unset=[],
            toml_path=self.root/'forza.toml',toml_text='',log_dir=self.root,
            save_profile=self.target if save_manager else None)

    def test_invalid_journals_warn_once_allow_play_and_keep_lock_through_relaunch(self):
        self.existing()
        backup=self.run_import()
        current=self.files()
        journal=self.folder/'.catalog-import.json'
        for raw in ('{broken', '[]', '{}', json.dumps(dict(token='a'*32,existed=True,originals=[]))):
            for save_manager in (False,True):
                with self.subTest(raw=raw,save_manager=save_manager):
                    journal.write_text(raw)
                    plan=self.session_plan(save_manager)
                    messages=[];session=game.Session(plan,log=messages.append)
                    try:
                        with patch('subprocess.Popen') as spawn, \
                                patch.object(ci,'recover',wraps=ci.recover) as recover, \
                                patch.object(paths,'log_error',wraps=paths.log_error) as log:
                            session.start()
                            self.assertEqual(spawn.call_count,1)
                            self.assertEqual(len(plan.notes),1)
                            self.assertIn(str(backup),plan.notes[0])
                            self.assertIn(str(paths.launcher_log()),plan.notes[0])
                            self.assertIn('Play can continue',plan.notes[0])
                            self.assertIn(plan.notes[0],messages)
                            self.assertTrue(session.relaunch())
                            self.assertEqual(spawn.call_count,2)
                            recover.assert_called_once()
                            log.assert_called_once()
                            self.assertEqual(len(plan.notes),1)
                            with self.assertRaisesRegex(ValueError,'Close the game'):
                                with ci.locked(self.target):pass
                            self.assertEqual(self.files(),current)
                            self.assertEqual(journal.read_text(),raw)
                    finally:session.cleanup()
                    with ci.locked(self.target):pass
        self.assertIn('Invalid import journal',paths.launcher_log().read_text())

    def test_mismatched_recovery_and_unverified_backups_warn_without_deleting(self):
        before=self.existing();backup=self.run_import();token='b'*32
        current=self.files()
        # A tampered archive is never presented as verified restoration advice.
        (backup/'LivCatalog/existing.liv').write_bytes(b'tampered')
        hold=self.folder/('.catalog-old-'+token);hold.mkdir();(hold/'wrong').write_bytes(b'wrong')
        journal=self.folder/'.catalog-import.json'
        journal.write_text(json.dumps(dict(token=token,existed=True,
            originals={n:hashlib.sha256(d).hexdigest() for n,d in before.items()})))
        session=game.Session(self.session_plan(True),log=lambda _:None)
        try:
            with patch('subprocess.Popen') as spawn:
                session.start();spawn.assert_called_once()
            self.assertIn('Nothing was removed',session.plan.notes[0])
            self.assertIn('No verified catalog backup',session.plan.notes[0])
            self.assertEqual(self.files(),current)
            self.assertEqual((hold/'wrong').read_bytes(),b'wrong')
            self.assertTrue(journal.exists())
        finally:session.cleanup()
        # Import remains fail-closed on the unresolved journal.
        with self.assertRaisesRegex(ugc.CatalogError,'Nothing was removed'):self.run_import()

    def test_held_import_lock_blocks_both_session_paths_before_recovery_or_spawn(self):
        for save_manager in (False,True):
            with self.subTest(save_manager=save_manager):
                session=game.Session(self.session_plan(save_manager))
                with ci.locked(self.target),patch.object(ci,'recover') as recover, \
                        patch('subprocess.Popen') as spawn,patch.object(paths,'log_error') as log:
                    with self.assertRaisesRegex((ValueError,RuntimeError),'Close the game'):session.start()
                    recover.assert_not_called();spawn.assert_not_called();log.assert_not_called()
                self.assertEqual(session.plan.notes,[])
                with ci.locked(self.target):pass

    def test_recovery_io_errors_still_abort_and_release_the_session_lock(self):
        for save_manager in (False,True):
            with self.subTest(save_manager=save_manager):
                session=game.Session(self.session_plan(save_manager))
                with patch.object(ci,'recover',side_effect=OSError('disk unavailable')), \
                        patch('subprocess.Popen') as spawn:
                    with self.assertRaisesRegex(OSError,'disk unavailable'):session.start()
                    spawn.assert_not_called()
                with ci.locked(self.target):pass


if __name__=='__main__':unittest.main()
