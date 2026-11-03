"""Authenticated offline PlayerDatabase edits. No game keys or car rows are bundled."""
from dataclasses import dataclass, field
from contextlib import closing
import hmac
import os
import sqlite3
import struct
import time
import uuid
import zlib

from . import save_editor as profile_editor, saves, unicorns

MAX_DATABASE = 32 * 1024**2
# Matches forza-recomp/src/platform/garage_capacity.h; container-size checks are separate.
GARAGE_CAPACITY = 1000
CHUNK = 32768
GARAGE_COLUMNS = set('''Id CarId PerformanceIndex ClassID PartsValue SpeedRating AccelerationRating
LaunchRating BrakingRating HandlingRating CurbWeight WeightDistribution AspirationTypeId SimPeakPower
SimPeakAngVel SimPeakTorque SimPeakTorqueAngVel SimRedlineAngVel PeakIntakePSI DistanceDriven TimeDriven
TotalWinnings TotalRepairs NumVictories NumPodiums NumRaces NumOwners NumTimesSold CurOwnerPurchaseDate
CurOwnerNumRaces CurOwnerWinnings OriginalOwner CarGroup Flags Engine Drivetrain CarBody Motor Brakes
SpringDamper AntiSwayFront AntiSwayRear TireCompound RearWing RimSizeFront RimSizeRear Camshaft Valves
Displacement PistonsCompression FuelSystem Ignition Exhaust Intake Flywheel Manifold RestrictorPlate
OilCooling SingleTurbo TwinTurbo QuadTurbo SuperchargerCSC SuperchargerDSC Intercooler Clutch Transmission
Driveline Differential FrontBumper RearBumper Hood SideSkirts TireWidthFront TireWidthRear WeightReduction
ChassisStiffness MotorParts TireBrand WheelStyle DefaultManufacturerColorIndex LiveryFileName Guid Thumbnail
Tuning_frontTirePressure Tuning_rearTirePressure Tuning_finalDriveRatio Tuning_firstGear Tuning_secondGear
Tuning_thirdGear Tuning_fourthGear Tuning_fifthGear Tuning_sixthGear Tuning_seventhGear Tuning_eighthGear
Tuning_frontCamber Tuning_rearCamber Tuning_frontToe Tuning_rearToe Tuning_frontCaster Tuning_frontSwaybar
Tuning_rearSwaybar Tuning_frontSpring Tuning_rearSpring Tuning_frontRideHeight Tuning_rearRideHeight
Tuning_frontDampingStiffness Tuning_rearDampingStiffness Tuning_frontBumpRatio Tuning_rearBumpRatio
Tuning_frontDownforce Tuning_rearDownforce Tuning_brakeBalance Tuning_brakePressure Tuning_frontAccel
Tuning_rearAccel Tuning_frontDecel Tuning_rearDecel Tuning_centerTorque SharedID MirroredPerformanceIndex'''.split())


@dataclass(frozen=True)
class Document:
    profile: saves.Profile
    original: bytes = field(repr=False)
    database: bytes = field(repr=False)
    aes_key: bytes = field(repr=False)
    mac_key: bytes = field(repr=False)
    chunk_size: int = CHUNK


@dataclass(frozen=True)
class Result:
    added: tuple
    already_owned: tuple
    backup: object = None


@dataclass(frozen=True)
class PaintRepairResult:
    repaired: tuple
    backup: object = None


def _keys(cfg):
    executable = cfg.game_files() / 'default.xex'
    info = executable.stat()
    owner = profile_editor._owner(cfg)
    tables = profile_editor._tables(str(executable.resolve()), info.st_size, info.st_mtime_ns)
    return tuple((bytes(((t[48+i] ^ owner[(i-5) % 8])+2*i) % 256 for i in range(16)),
                  bytes(((t[16+i] ^ owner[(i-1) % 8])+2*i) % 256 for i in range(32))) for t in tables)


def _inflate(body, *, with_chunk_size=False):
    if len(body) < 12:
        raise saves.SaveError('Truncated garage compression header.')
    size = struct.unpack_from('>I', body)[0]
    if not 512 <= size <= MAX_DATABASE:
        raise saves.SaveError('Unsupported garage database size.')
    out, pos, chunk_size = bytearray(), 4, 0
    while len(out) < size:
        if pos+8 > len(body):
            raise saves.SaveError('Truncated garage compression block.')
        compressed, unpacked = struct.unpack_from('>II', body, pos)
        pos += 8
        if not 0 < unpacked <= MAX_DATABASE or len(out)+unpacked > size or not 0 < compressed <= len(body)-pos:
            raise saves.SaveError('Invalid garage compression block.')
        decoder = zlib.decompressobj(-15)
        try:
            part = decoder.decompress(body[pos:pos+compressed], unpacked+1)
        except zlib.error as exc:
            raise saves.SaveError('Damaged compressed garage database.') from exc
        if len(part) != unpacked or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise saves.SaveError('Invalid compressed garage data length.')
        out.extend(part)
        chunk_size = max(chunk_size, unpacked)
        pos += compressed
    if pos != len(body) or not out.startswith(b'SQLite format 3\0'):
        raise saves.SaveError('Unsupported garage database format.')
    return (bytes(out), chunk_size) if with_chunk_size else bytes(out)


def _deflate(database, chunk_size=CHUNK):
    if not 0 < chunk_size <= MAX_DATABASE:
        raise saves.SaveError('Unsupported garage compression block size.')
    result = bytearray(struct.pack('>I', len(database)))
    for start in range(0, len(database), chunk_size):
        part = database[start:start+chunk_size]
        encoder = zlib.compressobj(6, zlib.DEFLATED, -15)
        compressed = encoder.compress(part)+encoder.flush()
        result.extend(struct.pack('>II', len(compressed), len(part)))
        result.extend(compressed)
    return bytes(result)


def decode(profile, raw, keys):
    if not 76 <= len(raw) <= MAX_DATABASE or raw[:4] != b'cmss':
        raise saves.SaveError('Unsupported PlayerDatabase file.')
    used = struct.unpack_from('>I', raw, 4)[0]
    if not 68 <= used <= len(raw)-8:
        raise saves.SaveError('Invalid garage stream length.')
    length = profile_editor.reduce(profile_editor.operator.xor, struct.unpack_from('>5I', raw, 40))
    encrypted = ((length+15)//16)*16
    # Older saves overstate the outer stream length. The secure stream reads
    # exactly the padded length encoded by its seed, not that spare capacity.
    if not 12 <= length <= MAX_DATABASE or 52+encrypted > used:
        raise saves.SaveError('Invalid encrypted garage length.')
    payload = raw[40:60+encrypted]
    for aes, mac in keys:
        if not hmac.compare_digest(hmac.digest(mac, payload, 'sha256'), raw[8:40]):
            continue
        cipher = payload[20:]
        ecb = profile_editor._aes_tools().aes_decrypt(aes, cipher, cbc=False)
        plain = bytes(a ^ b for a, b in zip(ecb, payload[4:20]+cipher[:-16]))
        database, chunk_size = _inflate(plain[:length], with_chunk_size=True)
        _open(database).close()
        return Document(profile, raw, database, aes, mac, chunk_size)
    raise saves.SaveError('The garage integrity check failed. Nothing was changed.')


def _open(database):
    db = sqlite3.connect(':memory:')
    try:
        # Some Python builds (including macOS) omit loadable-extension support.
        # Where the API exists, explicitly keep it disabled before reading the save.
        disable_extensions = getattr(db, 'enable_load_extension', None)
        if disable_extensions is not None:
            disable_extensions(False)
        db.execute('PRAGMA trusted_schema=OFF')
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_DATABASE)
        db.deserialize(database)
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise saves.SaveError('The garage database failed its integrity check.')
        if db.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
            raise saves.SaveError('Unsupported garage database schema.')
        db.row_factory = sqlite3.Row
        return db
    except (sqlite3.Error, saves.SaveError) as exc:
        db.close()
        if isinstance(exc, sqlite3.Error):
            raise saves.SaveError('Unsupported or damaged garage database.') from exc
        raise


def encode(document, database):
    if database == document.database:
        return document.original
    body = _deflate(database, document.chunk_size)
    iv = os.urandom(16)
    seed = struct.pack('>I', len(body) ^ profile_editor.reduce(profile_editor.operator.xor,
                       struct.unpack('>4I', iv)))+iv
    cipher = profile_editor._encrypt_cbc(document.aes_key, body+b'\xbb'*(-len(body) % 16), iv)
    payload = seed+cipher
    used = 32+len(payload)
    if 8+used > len(document.original):
        raise saves.SaveError('The edited garage exceeds this save file’s capacity.')
    raw = b'cmss'+struct.pack('>I', used)+hmac.digest(document.mac_key, payload, 'sha256')+payload
    raw += document.original[len(raw):]
    checked = decode(document.profile, raw, ((document.aes_key, document.mac_key),))
    if checked.database != database:
        raise saves.SaveError('The edited garage did not pass verification.')
    return raw


def _path(profile):
    return saves._inside(profile.folder/'ForzaProfile/PlayerDatabase', profile.root)


def _read(profile):
    path = _path(profile)
    if path.stat().st_size > MAX_DATABASE:
        raise saves.SaveError('The garage file is too large.')
    return path.read_bytes()


def _stock_part(db, table, group, value, required=False):
    rows = db.execute(f'SELECT * FROM "{table}" WHERE "{group}"=? AND IsStock=1', (value,)).fetchall()
    if len(rows) > 1 or (required and len(rows) != 1):
        raise saves.SaveError('Unsupported or ambiguous stock car parts: '+table)
    return dict(rows[0]) if rows else {'Id': -1}


def _factory_colour(db, cid):
    # This is Combo_Colors.Sequence, not a zero-based position in the list.
    colour = db.execute('SELECT Sequence FROM Combo_Colors WHERE Ordinal=? '
                        'ORDER BY Sequence LIMIT 1', (cid,)).fetchone()
    if colour is None or type(colour[0]) is not int or not 1 <= colour[0] <= 255:
        raise saves.SaveError('The unicorn car has no supported factory colour.')
    return colour[0]


def stock_car(db, cid, ordinal, columns):
    """Build stock parts from the player's game database, never from another owned car."""
    if cid not in {model for model, _, _ in unicorns.CARS}:
        raise saves.SaveError('Unsupported unicorn car model.')
    car = db.execute('SELECT * FROM Data_Car WHERE Id=?', (cid,)).fetchone()
    # The disc database has these models but leaves IsUnicorn unset. Only the
    # explicit seven-model allowlist identifies this action's supported cars.
    if car is None:
        raise saves.SaveError('The unicorn car is missing from the game database.')
    row = {c['name']: ('' if c['type'] == 'TEXT' else 0) for c in columns}
    for name in row.keys() & car.keys():
        row[name] = car[name]
    guid = uuid.uuid4().hex
    row.update(Id=ordinal, CarId=cid, PartsValue=0, NumOwners=1, OriginalOwner='ReImagined',
               CurOwnerPurchaseDate=int(time.time()), CarGroup=None, TireBrand=None,
               Flags=73, WheelStyle=-1, DefaultManufacturerColorIndex=_factory_colour(db, cid), LiveryFileName='',
               Guid=f'{guid[:8]}-{guid[8:12]}-{guid[12:16]}-{guid[16:]}', Thumbnail=car['Thumbnail'], SharedID=0,
               MirroredPerformanceIndex=car['PerformanceIndex'])
    parents = {}
    for name in ('Engine', 'Drivetrain', 'CarBody', 'Motor'):
        part = _stock_part(db, 'List_Upgrade'+name, 'Ordinal', cid, name != 'Motor')
        row[name] = part['Id']
        parents[name] = part.get(name+'ID', -1)
    direct = ('Brakes','SpringDamper','AntiSwayFront','AntiSwayRear','TireCompound','RearWing','RimSizeFront','RimSizeRear')
    for name in direct:
        row[name] = _stock_part(db, 'List_Upgrade'+name, 'Ordinal', cid, True)['Id']
    engine = {'Camshaft':'Camshaft','Valves':'Valves','Displacement':'Displacement',
              'PistonsCompression':'PistonsCompression','FuelSystem':'FuelSystem','Ignition':'Ignition',
              'Exhaust':'Exhaust','Intake':'Intake','Flywheel':'Flywheel','Manifold':'Manifold',
              'RestrictorPlate':'RestrictorPlate','OilCooling':'OilCooling','SingleTurbo':'TurboSingle',
              'TwinTurbo':'TurboTwin','QuadTurbo':'TurboQuad','SuperchargerCSC':'CSC',
              'SuperchargerDSC':'DSC','Intercooler':'Intercooler'}
    for name, suffix in engine.items():
        row[name] = _stock_part(db, 'List_UpgradeEngine'+suffix, 'EngineID', parents['Engine'])['Id']
    for name in ('Clutch','Transmission','Driveline','Differential'):
        row[name] = _stock_part(db, 'List_UpgradeDrivetrain'+name, 'DrivetrainID', parents['Drivetrain'], True)['Id']
    body = {'FrontBumper':'FrontBumper','RearBumper':'RearBumper','Hood':'Hood','SideSkirts':'SideSkirt',
            'TireWidthFront':'TireWidthFront','TireWidthRear':'TireWidthRear','WeightReduction':'Weight',
            'ChassisStiffness':'ChassisStiffness'}
    for name, suffix in body.items():
        row[name] = _stock_part(db, 'List_UpgradeCarBody'+suffix, 'CarBodyID', parents['CarBody'], True)['Id']
    row['MotorParts'] = _stock_part(db, 'List_UpgradeMotorParts', 'MotorID', parents['Motor'])['Id']
    boost = db.execute('SELECT "StockBoost-bar" FROM Data_Engine WHERE EngineID=?', (parents['Engine'],)).fetchone()
    row['PeakIntakePSI'] = float(boost[0])*14.7 if boost else 0.0
    for name in row:
        if name.startswith('Tuning_'):
            row[name] = -1.0
    return row


def add_missing(cfg, profile, cancelled=lambda: False, progress=lambda *_: None):
    with saves.locked(profile):
        saves._recover(profile)
        saves._check(cancelled)
        original = _read(profile)
        document = decode(profile, original, _keys(cfg))
        db = _open(document.database)
        try:
            columns = [dict(c) for c in db.execute('PRAGMA table_info(Career_Garage)')]
            if ({c['name'] for c in columns} != GARAGE_COLUMNS
                    or any(c['type'] not in ('INT','INTEGER','REAL','TEXT') for c in columns)
                    or [c['name'] for c in columns if c['pk']] != ['Id']):
                raise saves.SaveError('Unsupported garage schema.')
            owned = {r[0] for r in db.execute('SELECT CarId FROM Career_Garage')}
            missing = tuple(cid for cid, _, _ in unicorns.CARS if cid not in owned)
            existing = tuple(cid for cid, _, _ in unicorns.CARS if cid in owned)
            if not missing:
                return Result((), existing)
            count = db.execute('SELECT count(*) FROM Career_Garage').fetchone()[0]
            if count + len(missing) > GARAGE_CAPACITY:
                raise saves.SaveError(f'The garage holds at most {GARAGE_CAPACITY:,} cars. '
                                      f'Adding these {len(missing)} cars would exceed that limit.')
            unicorns.check_files(cfg.game_files())
            ordinal = max(db.execute('SELECT coalesce(max(Id),0) FROM Career_Garage').fetchone()[0],
                          db.execute('SELECT coalesce(max(LastUsedGarageId),0) FROM LastUsedGarageId').fetchone()[0])
            if (db.execute('SELECT count(*) FROM LastUsedGarageId').fetchone()[0] > 1
                    or not isinstance(ordinal, int) or not 0 <= ordinal <= 2_147_483_647-len(missing)):
                raise saves.SaveError('Unsupported garage ID counter.')
            with closing(sqlite3.connect((cfg.game_files()/'media/db/gamedb.slt').resolve().as_uri()+'?mode=ro', uri=True)) as source:
                source.row_factory = sqlite3.Row
                for cid in missing:
                    saves._check(cancelled)
                    ordinal += 1
                    row = stock_car(source, cid, ordinal, columns)
                    names = list(row)
                    db.execute('INSERT INTO Career_Garage ('+','.join('"'+n+'"' for n in names)+') VALUES ('+
                               ','.join('?' for _ in names)+')', [row[n] for n in names])
            db.execute('DELETE FROM LastUsedGarageId')
            db.execute('INSERT INTO LastUsedGarageId VALUES (?)', (ordinal,))
            db.commit()
            edited = encode(document, db.serialize())
        except sqlite3.Error as exc:
            raise saves.SaveError('Unsupported garage or game database: '+str(exc)) from exc
        finally:
            db.close()
        backup = _replace_garage(profile, original, edited, 'Before adding unicorn cars', cancelled, progress)
        return Result(missing, existing, backup)


def _replace_garage(profile, original, edited, label, cancelled, progress):
    saves._check(cancelled)
    backup = saves._create(profile, label, 'before-edit', cancelled, progress, 10)
    if backup is None:
        raise saves.SaveError('Could not back up the selected save.')
    saves._check(cancelled)
    if _read(profile) != original:
        raise saves.SaveError('The garage changed during backup. Reload before editing.')
    path = _path(profile)
    temporary = path.with_name('.garage-edit-'+uuid.uuid4().hex)
    try:
        with temporary.open('xb') as writer:
            writer.write(edited)
            writer.flush()
            os.fsync(writer.fileno())
        saves._check(cancelled)
        if _read(profile) != original:
            raise saves.SaveError('The garage changed before applying the edit.')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


def repair_factory_paint(cfg, profile, cancelled=lambda: False, progress=lambda *_: None):
    """Opt-in repair of the old editor's invalid factory colour; never replace a livery."""
    with saves.locked(profile):
        saves._recover(profile)
        saves._check(cancelled)
        original = _read(profile)
        document = decode(profile, original, _keys(cfg))
        db = _open(document.database)
        try:
            columns = [dict(c) for c in db.execute('PRAGMA table_info(Career_Garage)')]
            if ({c['name'] for c in columns} != GARAGE_COLUMNS
                    or any(c['type'] not in ('INT', 'INTEGER', 'REAL', 'TEXT') for c in columns)
                    or [c['name'] for c in columns if c['pk']] != ['Id']):
                raise saves.SaveError('Unsupported garage schema.')
            supported = {cid for cid, _, _ in unicorns.CARS}
            candidates = [row for row in db.execute(
                "SELECT Id, CarId FROM Career_Garage WHERE DefaultManufacturerColorIndex=0 "
                "AND LiveryFileName='' AND OriginalOwner='ReImagined' AND NumOwners=1 AND Flags=73")
                if row['CarId'] in supported]
            if not candidates:
                return PaintRepairResult(())
            with closing(sqlite3.connect((cfg.game_files()/'media/db/gamedb.slt').resolve().as_uri()+'?mode=ro', uri=True)) as source:
                for row in candidates:
                    saves._check(cancelled)
                    db.execute('UPDATE Career_Garage SET DefaultManufacturerColorIndex=? WHERE Id=?',
                               (_factory_colour(source, row['CarId']), row['Id']))
            db.commit()
            edited = encode(document, db.serialize())
        except sqlite3.Error as exc:
            raise saves.SaveError('Unsupported garage or game database: '+str(exc)) from exc
        finally:
            db.close()
        backup = _replace_garage(profile, original, edited, 'Before repairing unicorn paint', cancelled, progress)
        return PaintRepairResult(tuple(row['Id'] for row in candidates), backup)
