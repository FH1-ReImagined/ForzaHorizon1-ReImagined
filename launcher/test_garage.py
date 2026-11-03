"""Synthetic garage saves: no game-generated rows, assets, or key material."""
import hmac
from contextlib import closing
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from forza_launcher import garage, saves, save_editor, unicorns
from forza_launcher.config import Config

KEYS = (bytes(range(16)), bytes(range(32)))
STOCK_CAR = garage.stock_car


def database(owned=(309,), extra=False):
    db = sqlite3.connect(':memory:')
    db.execute('PRAGMA page_size=512')
    columns = ['"Id" INTEGER PRIMARY KEY']+[f'"{name}" '+('TEXT' if name in ('Guid','Thumbnail','OriginalOwner','LiveryFileName') else 'INT')
               for name in sorted(garage.GARAGE_COLUMNS-{'Id'})]
    db.execute('CREATE TABLE Career_Garage ('+','.join(columns)+')')
    db.execute('CREATE TABLE LastUsedGarageId (LastUsedGarageId INTEGER PRIMARY KEY)')
    db.execute('INSERT INTO LastUsedGarageId VALUES (50)')
    db.execute('CREATE TABLE OtherProgress (value TEXT)')
    db.execute("INSERT INTO OtherProgress VALUES ('preserved')")
    for i, cid in enumerate(owned, 1):
        db.execute('INSERT INTO Career_Garage(Id,CarId,Engine,Guid) VALUES (?,?,?,?)', (i,cid,987654,'existing'))
    if extra:
        db.execute('ALTER TABLE Career_Garage ADD COLUMN Unsupported INT')
    db.commit()
    raw = db.serialize()
    db.close()
    return raw


def envelope(data, spare=16384, overstate=0, chunk_size=garage.CHUNK):
    body = garage._deflate(data, chunk_size)
    iv = bytes(range(16))
    seed = struct.pack('>I',len(body)^save_editor.reduce(save_editor.operator.xor,struct.unpack('>4I',iv)))+iv
    payload = seed+save_editor._encrypt_cbc(KEYS[0],body+b'\xbb'*(-len(body)%16),iv)
    raw = b'cmss'+struct.pack('>I',32+len(payload)+overstate)+hmac.digest(KEYS[1],payload,'sha256')+payload
    return raw+bytes(max(spare,overstate))


class GarageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root/'launcher.json')
        self.cfg.advanced['data_root'] = str(self.root/'data')
        self.cfg.d['game_root'] = str(self.root/'game')
        self.profile = saves.profile_for(self.cfg)
        self.path = self.profile.folder/'ForzaProfile/PlayerDatabase'
        self.path.parent.mkdir(parents=True)
        self.original = envelope(database())
        self.path.write_bytes(self.original)
        self.companion = self.path.with_name('ForzaProfile')
        self.companion.write_bytes(b'unchanged profile and active-car choice')
        source = self.cfg.game_files()/'media/db/gamedb.slt'
        source.parent.mkdir(parents=True)
        sqlite3.connect(source).close()
        self.patches = [patch.object(garage,'_keys',return_value=(KEYS,)),
                        patch.object(unicorns,'check_files'),patch.object(garage,'stock_car',side_effect=self.stock)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def stock(_source,cid,ordinal,columns):
        return {c['name']:(ordinal if c['name']=='Id' else cid if c['name']=='CarId' else
                          'fixture' if c['type']=='TEXT' else 0) for c in columns}

    def test_roundtrip_noop_and_overstated_outer_stream(self):
        for extra in (0,240):
            raw = envelope(database(),overstate=extra)
            doc = garage.decode(self.profile,raw,(KEYS,))
            self.assertEqual(garage.encode(doc,doc.database),raw)

    def test_editor_without_optional_sqlite_extension_api(self):
        class NoExtensionAPI(sqlite3.Connection):
            @property
            def enable_load_extension(self):
                raise AttributeError('SQLite extension API was not compiled in')

        connect = sqlite3.connect
        def without_extension_api(*args, **kwargs):
            return connect(*args, **kwargs, factory=NoExtensionAPI)

        with patch.object(garage.sqlite3, 'connect', side_effect=without_extension_api):
            document = garage.decode(self.profile, self.original, (KEYS,))
            self.assertEqual(garage.encode(document, document.database), self.original)
            with closing(garage._open(document.database)) as db:
                with self.assertRaisesRegex(sqlite3.OperationalError, 'not authorized|no such function'):
                    db.execute("SELECT load_extension('garage-test-must-not-load')")
            result = garage.add_missing(self.cfg, self.profile)
            self.assertEqual(len(result.added), 7)
            with closing(garage._open(garage.decode(self.profile, self.path.read_bytes(), (KEYS,)).database)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM Career_Garage').fetchone()[0], 8)
            saves.restore(self.profile, result.backup)
            self.assertEqual(self.path.read_bytes(), self.original)

    def test_multiple_compression_blocks_and_bounded_bad_data(self):
        data = b'SQLite format 3\0'+bytes(100000)
        self.assertEqual(garage._inflate(garage._deflate(data)),data)
        with self.assertRaises(saves.SaveError): garage._inflate(struct.pack('>III',2**31,1,1)+b'x')
        with self.assertRaises(saves.SaveError): garage._inflate(garage._deflate(data)[:-1])

    def test_single_64k_block_and_larger_blocks_remain_bounded(self):
        for chunk_size in (65536, 131072):
            data = b'SQLite format 3\0'+bytes(chunk_size-16)
            body = garage._deflate(data, chunk_size)
            self.assertEqual(struct.unpack_from('>I', body, 8)[0], chunk_size)
            self.assertEqual(garage._inflate(body, with_chunk_size=True), (data, chunk_size))
        with self.assertRaises(saves.SaveError):
            garage._inflate(struct.pack('>III', 65536, 1, garage.MAX_DATABASE+1)+b'x')
        with self.assertRaises(saves.SaveError):
            garage._deflate(data, garage.MAX_DATABASE+1)

    def test_add_preserves_64k_garage_blocks_backup_and_noop(self):
        with garage._open(database()) as db:
            db.execute('CREATE TABLE Padding (payload BLOB)')
            db.execute('INSERT INTO Padding VALUES (zeroblob(131072))')
            db.commit()
            data = db.serialize()
        original = envelope(data, chunk_size=65536)
        self.path.write_bytes(original)
        before = garage.decode(self.profile, original, (KEYS,))
        self.assertEqual(before.chunk_size, 65536)
        self.assertEqual(garage.encode(before, before.database), original)
        result = garage.add_missing(self.cfg, self.profile)
        self.assertEqual(len(result.added), 7)
        self.assertEqual((result.backup.path/'save/ForzaProfile/PlayerDatabase').read_bytes(), original)
        updated = self.path.read_bytes()
        after = garage.decode(self.profile, updated, (KEYS,))
        self.assertEqual(after.chunk_size, 65536)
        with garage._open(after.database) as db:
            self.assertEqual(db.execute('SELECT length(payload) FROM Padding').fetchone()[0], 131072)
            self.assertEqual(db.execute('SELECT value FROM OtherProgress').fetchone()[0], 'preserved')
        self.assertFalse(garage.add_missing(self.cfg, self.profile).added)
        self.assertEqual(self.path.read_bytes(), updated)
        saves.restore(self.profile, result.backup)
        self.assertEqual(self.path.read_bytes(), original)

    def test_add_preserves_rows_progress_companions_and_uses_unique_ids(self):
        result = garage.add_missing(self.cfg,self.profile)
        self.assertEqual(set(result.added),{c[0] for c in unicorns.CARS})
        self.assertEqual((result.backup.path/'save/ForzaProfile/PlayerDatabase').read_bytes(),self.original)
        self.assertEqual(self.companion.read_bytes(),b'unchanged profile and active-car choice')
        doc = garage.decode(self.profile,self.path.read_bytes(),(KEYS,))
        with garage._open(doc.database) as db:
            self.assertEqual(tuple(db.execute('SELECT Id,CarId,Engine,Guid FROM Career_Garage WHERE Id=1').fetchone()),(1,309,987654,'existing'))
            self.assertEqual(db.execute('SELECT value FROM OtherProgress').fetchone()[0],'preserved')
            self.assertEqual(db.execute('SELECT min(Id),max(Id) FROM Career_Garage WHERE Id>1').fetchone()[:],(51,57))
            self.assertEqual(db.execute('SELECT LastUsedGarageId FROM LastUsedGarageId').fetchone()[0],57)
        after = self.path.read_bytes()
        again = garage.add_missing(self.cfg,self.profile)
        self.assertFalse(again.added)
        self.assertIsNone(again.backup)
        self.assertEqual(self.path.read_bytes(),after)
        self.assertEqual(len(saves.snapshots(self.profile)[0]),1)
        saves.restore(self.profile,result.backup)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_partial_and_duplicate_owned_models_are_skipped(self):
        self.path.write_bytes(envelope(database((320,320,326))))
        result = garage.add_missing(self.cfg,self.profile)
        self.assertEqual(result.already_owned,(320,326))
        self.assertEqual(len(result.added),5)

    def test_capacity_counts_rows_and_rejects_entire_batch_without_writing(self):
        for count in (549, 550, 551, 993, 994, 999, 1000, 1001):
            with self.subTest(count=count):
                # Duplicated models still occupy separate garage slots.
                original = envelope(database((309,) * count))
                self.path.write_bytes(original)
                backups = len(saves.snapshots(self.profile)[0])
                if count + 7 > 1000:
                    with self.assertRaisesRegex(saves.SaveError, '1,000 cars'):
                        garage.add_missing(self.cfg, self.profile)
                    self.assertEqual(self.path.read_bytes(), original)
                    self.assertEqual(len(saves.snapshots(self.profile)[0]), backups)
                else:
                    result = garage.add_missing(self.cfg, self.profile)
                    with garage._open(garage.decode(self.profile, self.path.read_bytes(), (KEYS,)).database) as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM Career_Garage').fetchone()[0], count + 7)
                        self.assertEqual(db.execute('SELECT count(*) FROM Career_Garage WHERE CarId=309').fetchone()[0], count)
                    self.assertEqual(len(self.path.read_bytes()), len(original))
                    saves.restore(self.profile, result.backup)
                    self.assertEqual(self.path.read_bytes(), original)

    def test_capacity_allows_last_slot_and_preserves_full_garage_noop(self):
        owned = tuple(cid for cid, _, _ in unicorns.CARS)
        self.path.write_bytes(envelope(database((309,) * 993 + owned[:-1])))
        result = garage.add_missing(self.cfg, self.profile)
        self.assertEqual(result.added, owned[-1:])
        original = self.path.read_bytes()
        with garage._open(garage.decode(self.profile, original, (KEYS,)).database) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM Career_Garage').fetchone()[0], 1000)
        noop = garage.add_missing(self.cfg, self.profile)
        self.assertEqual(noop.added, ())
        self.assertIsNone(noop.backup)
        self.assertEqual(self.path.read_bytes(), original)

    def test_native_and_launcher_capacity_agree(self):
        header = Path(__file__).resolve().parents[1]/'forza-recomp/src/platform/garage_capacity.h'
        self.assertIn(f'kCapacity = {garage.GARAGE_CAPACITY};', header.read_text())

    def test_bad_mac_unknown_schema_and_backup_failure_never_replace(self):
        broken = bytearray(self.original);broken[8]^=1;self.path.write_bytes(broken)
        with self.assertRaises(saves.SaveError):garage.add_missing(self.cfg,self.profile)
        self.assertEqual(self.path.read_bytes(),broken)
        unknown = envelope(database(extra=True));self.path.write_bytes(unknown)
        with self.assertRaisesRegex(saves.SaveError,'schema'):garage.add_missing(self.cfg,self.profile)
        self.assertEqual(self.path.read_bytes(),unknown)
        self.path.write_bytes(self.original)
        with patch.object(saves,'_create',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):garage.add_missing(self.cfg,self.profile)
        self.assertEqual(self.path.read_bytes(),self.original)

    def test_failed_replace_keeps_current_save_and_full_backup(self):
        replace = garage.os.replace
        def fail(source,target):
            if Path(source).name.startswith('.garage-edit-'):raise PermissionError('fixture')
            return replace(source,target)
        with patch.object(garage.os,'replace',side_effect=fail):
            with self.assertRaises(PermissionError):garage.add_missing(self.cfg,self.profile)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(len(saves.snapshots(self.profile)[0]),1)
        self.assertFalse(list(self.path.parent.glob('.garage-edit-*')))

    def test_cancel_and_save_change_during_backup_block_replacement(self):
        with self.assertRaises(saves.Cancelled):garage.add_missing(self.cfg,self.profile,cancelled=lambda:True)
        create = saves._create
        def changed(*args):
            backup=create(*args)
            self.path.write_bytes(self.original+b'changed externally')
            return backup
        with patch.object(saves,'_create',side_effect=changed):
            with self.assertRaisesRegex(saves.SaveError,'changed'):garage.add_missing(self.cfg,self.profile)
        self.assertEqual(self.path.read_bytes(),self.original+b'changed externally')

    def stock_database(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE Data_Car (Id INT, IsUnicorn INT, PerformanceIndex REAL, Thumbnail TEXT)')
        db.execute("INSERT INTO Data_Car VALUES (320,1,0.5,'native-car-card')")
        db.execute('CREATE TABLE Combo_Colors (Ordinal INT, Sequence INT)')
        db.executemany('INSERT INTO Combo_Colors VALUES (?,1)', [(cid,) for cid, _, _ in unicorns.CARS])
        db.execute('CREATE TABLE Data_Engine (EngineID INT, "StockBoost-bar" REAL)')
        db.execute('INSERT INTO Data_Engine VALUES (74,1)')
        def table(name,group,value,identifier,extra=''):
            db.execute(f'CREATE TABLE {name} (Id INT, {group} INT, IsStock INT{extra})')
            db.execute(f'INSERT INTO {name}(Id,{group},IsStock) VALUES (?,?,1)',(identifier,value))
        for name,parent in [('Engine',74),('Drivetrain',73),('CarBody',320000)]:
            table('List_Upgrade'+name,'Ordinal',320,320000,','+name+'ID INT')
            db.execute('UPDATE List_Upgrade'+name+' SET '+name+'ID=?',(parent,))
        table('List_UpgradeMotor','Ordinal',999,-1,',MotorID INT')
        # Nonzero stock IDs ensure this isn't inferred as carId*1000 or Level=0.
        for name in ('Brakes','SpringDamper','AntiSwayFront','AntiSwayRear','TireCompound','RearWing','RimSizeFront','RimSizeRear'):
            table('List_Upgrade'+name,'Ordinal',320,320007)
        for suffix in ('Camshaft','Valves','Displacement','PistonsCompression','FuelSystem','Ignition','Exhaust','Intake','Flywheel',
                       'Manifold','RestrictorPlate','OilCooling','TurboSingle','TurboTwin','TurboQuad','CSC','DSC','Intercooler'):
            table('List_UpgradeEngine'+suffix,'EngineID',74,74007)
        for name in ('Clutch','Transmission','Driveline','Differential'):
            table('List_UpgradeDrivetrain'+name,'DrivetrainID',73,73007)
        for name in ('FrontBumper','RearBumper','Hood','SideSkirt','TireWidthFront','TireWidthRear','Weight','ChassisStiffness'):
            table('List_UpgradeCarBody'+name,'CarBodyID',320000,320008)
        table('List_UpgradeMotorParts','MotorID',999,1)
        return db

    def test_stock_lookup_uses_stock_markers_and_parent_ids(self):
        db = self.stock_database()
        columns=[{'name':n,'type':'TEXT' if n in ('Guid','Thumbnail','OriginalOwner','LiveryFileName') else 'INT'} for n in garage.GARAGE_COLUMNS]
        row=STOCK_CAR(db,320,10,columns)
        self.assertEqual((row['Id'],row['CarId'],row['RimSizeFront'],row['Camshaft'],row['Transmission'],row['FrontBumper']),
                         (10,320,320007,74007,73007,320008))
        self.assertEqual((row['Motor'],row['MotorParts']),(-1,-1))
        self.assertEqual(row['PeakIntakePSI'],14.7)
        self.assertEqual(row['Tuning_firstGear'],-1.0)
        self.assertEqual(row['DefaultManufacturerColorIndex'], 1)
        self.assertEqual(len(row['Guid']),35)
        db.execute('INSERT INTO List_UpgradeBrakes VALUES (320008,320,1)')
        with self.assertRaisesRegex(saves.SaveError,'ambiguous'):STOCK_CAR(db,320,11,columns)

    def test_factory_colour_is_a_database_sequence_not_a_list_offset(self):
        db = self.stock_database()
        db.execute('DELETE FROM Combo_Colors WHERE Ordinal=320')
        db.executemany('INSERT INTO Combo_Colors VALUES (320,?)', [(9,), (4,)])
        self.assertEqual(garage._factory_colour(db, 320), 4)
        for value in (None, 0, -1, 256, 'invalid'):
            db.execute('DELETE FROM Combo_Colors WHERE Ordinal=320')
            if value is not None:
                db.execute('INSERT INTO Combo_Colors VALUES (320,?)', (value,))
            with self.assertRaisesRegex(saves.SaveError, 'factory colour'):
                garage._factory_colour(db, 320)

    def paint_fixture(self):
        with garage._open(database(owned=(320,326,330,1082,1197,1233,1488,309))) as db:
            db.execute("UPDATE Career_Garage SET DefaultManufacturerColorIndex=0, LiveryFileName='', "
                       "OriginalOwner='ReImagined', NumOwners=1, Flags=73, PartsValue=987, Tuning_firstGear=3")
            # These are deliberately not repairable: valid paint, custom livery, another owner,
            # native flags, second-hand car and a non-unicorn. Only CR-X (1) and MINI (7) match.
            db.execute('UPDATE Career_Garage SET DefaultManufacturerColorIndex=2 WHERE Id=2')
            db.execute("UPDATE Career_Garage SET LiveryFileName='Liveries/custom.liv' WHERE Id=3")
            db.execute("UPDATE Career_Garage SET OriginalOwner='Someone else' WHERE Id=4")
            db.execute('UPDATE Career_Garage SET Flags=8 WHERE Id=5')
            db.execute('UPDATE Career_Garage SET NumOwners=2 WHERE Id=6')
            db.commit()
            self.path.write_bytes(envelope(db.serialize()))
        source = self.cfg.game_files()/'media/db/gamedb.slt'
        with closing(sqlite3.connect(source)) as db:
            db.execute('CREATE TABLE Combo_Colors (Ordinal INT, Sequence INT)')
            db.executemany('INSERT INTO Combo_Colors VALUES (?,1)', [(320,), (1488,)])
            db.commit()
        return self.path.read_bytes()

    def test_paint_repair_preserves_other_fields_backups_restore_and_noop(self):
        original = self.paint_fixture()
        with garage._open(garage.decode(self.profile, original, (KEYS,)).database) as db:
            before = [dict(row) for row in db.execute('SELECT * FROM Career_Garage ORDER BY Id')]
        result = garage.repair_factory_paint(self.cfg, self.profile)
        self.assertEqual(result.repaired, (1, 7))
        self.assertEqual((result.backup.path/'save/ForzaProfile/PlayerDatabase').read_bytes(), original)
        with garage._open(garage.decode(self.profile, self.path.read_bytes(), (KEYS,)).database) as db:
            after = [dict(row) for row in db.execute('SELECT * FROM Career_Garage ORDER BY Id')]
            self.assertEqual(db.execute('SELECT LastUsedGarageId FROM LastUsedGarageId').fetchone()[0], 50)
            self.assertEqual(db.execute('SELECT value FROM OtherProgress').fetchone()[0], 'preserved')
        for row in before:
            if row['Id'] in (1, 7):
                row['DefaultManufacturerColorIndex'] = 1
        self.assertEqual(after, before)
        self.assertEqual(self.companion.read_bytes(), b'unchanged profile and active-car choice')
        updated = self.path.read_bytes()
        noop = garage.repair_factory_paint(self.cfg, self.profile)
        self.assertEqual(noop.repaired, ())
        self.assertIsNone(noop.backup)
        self.assertEqual(updated, self.path.read_bytes())
        saves.restore(self.profile, result.backup)
        self.assertEqual(original, self.path.read_bytes())

    def test_paint_repair_refuses_missing_colour_failed_backup_and_concurrent_edit(self):
        original = self.paint_fixture()
        with closing(sqlite3.connect(self.cfg.game_files()/'media/db/gamedb.slt')) as db:
            db.execute('DELETE FROM Combo_Colors WHERE Ordinal=1488')
            db.commit()
        with self.assertRaisesRegex(saves.SaveError, 'factory colour'):
            garage.repair_factory_paint(self.cfg, self.profile)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(saves.snapshots(self.profile)[0])
        with closing(sqlite3.connect(self.cfg.game_files()/'media/db/gamedb.slt')) as db:
            db.execute('INSERT INTO Combo_Colors VALUES (1488,1)')
            db.commit()
        with patch.object(saves, '_create', return_value=None):
            with self.assertRaisesRegex(saves.SaveError, 'back up'):
                garage.repair_factory_paint(self.cfg, self.profile)
        self.assertEqual(self.path.read_bytes(), original)
        def changed(*args):
            self.path.write_bytes(original+b'concurrent change')
            return object()
        with patch.object(saves, '_create', side_effect=changed):
            with self.assertRaisesRegex(saves.SaveError, 'changed during backup'):
                garage.repair_factory_paint(self.cfg, self.profile)
        self.assertEqual(self.path.read_bytes(), original+b'concurrent change')

    def test_base_disc_database_adds_all_seven_without_unicorn_flags(self):
        db = self.stock_database()
        db.execute('UPDATE Data_Car SET IsUnicorn=0')
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'List_Upgrade%'")]
        for cid, _, _ in unicorns.CARS:
            if cid == 320:
                continue
            db.execute('INSERT INTO Data_Car SELECT ?,0,PerformanceIndex,Thumbnail FROM Data_Car WHERE Id=320', (cid,))
            for table in tables:
                names = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
                if 'Ordinal' in names:
                    values = ','.join('?' if name == 'Ordinal' else '"'+name+'"' for name in names)
                    db.execute(f'INSERT INTO "{table}" SELECT {values} FROM "{table}" WHERE Ordinal=320', (cid,))
        db.commit()
        source = self.cfg.game_files()/'media/db/gamedb.slt'
        with closing(sqlite3.connect(source)) as target:
            db.backup(target)
        source_before = source.read_bytes()
        with patch.object(garage, 'stock_car', STOCK_CAR):
            result = garage.add_missing(self.cfg, self.profile)
        self.assertEqual(set(result.added), {cid for cid, _, _ in unicorns.CARS})
        with garage._open(garage.decode(self.profile, self.path.read_bytes(), (KEYS,)).database) as edited:
            self.assertEqual(edited.execute('SELECT count(*) FROM Career_Garage WHERE Id>50').fetchone()[0], 7)
            self.assertEqual(edited.execute('SELECT count(*) FROM Career_Garage WHERE Id>50 AND Brakes=320007').fetchone()[0], 7)
        self.assertEqual(source.read_bytes(), source_before)
        saves.restore(self.profile, result.backup)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_missing_model_and_non_allowlisted_model_are_refused(self):
        db = self.stock_database()
        with self.assertRaisesRegex(saves.SaveError, 'Unsupported unicorn'):
            STOCK_CAR(db, 309, 1, [])
        db.execute('DELETE FROM Data_Car')
        db.commit()
        with closing(sqlite3.connect(self.cfg.game_files()/'media/db/gamedb.slt')) as target:
            db.backup(target)
        with patch.object(garage, 'stock_car', STOCK_CAR):
            with self.assertRaisesRegex(saves.SaveError, 'missing from the game database'):
                garage.add_missing(self.cfg, self.profile)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(saves.snapshots(self.profile)[0])
