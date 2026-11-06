"""Authenticated, backed-up FH1 design import. Never edit a career or creator locks."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import struct
import uuid
import zlib

from . import TITLE_ID, game, paths, ugc

INDEX = 'DatabaseReservedName4002'
COLUMNS = ('SaveId', 'Title', 'Folder', 'Date', 'StorefrontID', 'IsLocked', 'IsNewFile',
           'Thumbnail', 'ThumbEncrypted', 'ThumbCompressed', 'LargeThumbnail',
           'LargeEncrypted', 'LargeCompressed', 'Ordinal', 'NumLayers', 'Owner')
MAX_INDEX = 4 * 1024**2


@dataclass(frozen=True)
class Target:
    home: Path
    root: Path
    identity: bytes

    @property
    def folder(self):
        return self.root / game.LOCAL_PROFILE / TITLE_ID / '00000001'


def target_for_plan(plan):
    import tomllib
    settings = tomllib.loads(getattr(plan, 'toml_text', ''))
    root = settings.get('user_data_root') or plan.log_dir
    identity = settings.get('profile_xuid') or game.LOCAL_PROFILE
    for i, arg in enumerate(plan.argv):
        for key in ('user_data_root', 'profile_xuid'):
            value = None
            if arg.startswith('--'+key+'='):
                value = arg.split('=', 1)[1]
            elif arg == '--'+key and i+1 < len(plan.argv):
                value = plan.argv[i+1]
            if value is not None:
                if key == 'user_data_root':
                    root = Path(value)
                else:
                    identity = value
    identity = plan.env.get('FORZA_XUID', os.environ.get('FORZA_XUID', identity)) or identity
    if not re.fullmatch('[0-9a-fA-F]{16}', identity) or int(identity, 16) == 0:
        raise ugc.CatalogError('Choose a valid offline profile identity in Settings.')
    root = Path(root)
    if not root.is_absolute():
        root = plan.cwd/root
    return Target(Path(plan.log_dir).absolute(), root.absolute(), bytes.fromhex(identity))


def target_for(cfg):
    install, _ = game.find_game(cfg)
    if install is None:
        raise ugc.CatalogError('Choose a game installation in Setup first.')
    return target_for_plan(game.build_plan(cfg, install, write=False))


def _direct(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ugc.CatalogError('Linked save paths cannot be imported into.')
    return path


@contextmanager
def locked(target):
    """Same home-wide OS lease as the pending save manager; also held by Session."""
    storage = _direct(target.home/'save-backups')
    storage.mkdir(parents=True, exist_ok=True)
    lock_path = _direct(storage/'.manager.lock')
    with lock_path.open('a+b') as lock:
        lock.seek(0, os.SEEK_END)
        if not lock.tell():
            lock.write(b'1'); lock.flush()
        lock.seek(0)
        acquired = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise ugc.CatalogError('Close the game and other save operations before importing.') from exc
            yield
        finally:
            if acquired:
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read(path, limit=ugc.MAX_MEMBER):
    path = _direct(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise ugc.CatalogError('Invalid or oversized save file: '+path.name)
    with path.open('rb') as reader:
        raw = reader.read(limit+1)
    if len(raw) != info.st_size:
        raise ugc.CatalogError('The save changed while reading. Close the game and retry.')
    return raw


def _tree(folder, cancelled):
    result, total, seen = {}, 0, set()
    _direct(folder)
    if not folder.exists():
        return result
    if not folder.is_dir():
        raise ugc.CatalogError('The destination catalog must be an extracted directory.')
    def failed(error):
        raise error
    for directory, dirs, files in os.walk(folder, onerror=failed, followlinks=False):
        ugc._check(cancelled)
        for name in dirs+files:
            _direct(Path(directory)/name)
        for name in files:
            path = Path(directory)/name
            relative = path.relative_to(folder).as_posix()
            ugc._safe_name(relative)
            if relative.casefold() in seen:
                raise ugc.CatalogError('Duplicate destination catalog path.')
            seen.add(relative.casefold())
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > ugc.MAX_MEMBER:
                raise ugc.CatalogError('Unsupported file in the destination catalog.')
            total += info.st_size
            if total > ugc.MAX_BYTES or len(result) >= ugc.MAX_FILES:
                raise ugc.CatalogError('Destination catalog exceeds the import size limit.')
            with path.open('rb') as reader:
                data = reader.read(ugc.MAX_MEMBER+1)
                after = os.fstat(reader.fileno())
            if len(data) != info.st_size or (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise ugc.CatalogError('The catalog changed while reading. Close the game and retry.')
            result[relative] = data
    return result


def _database(raw, candidates):
    import livery_codec as codec
    doc = codec.decode_stream(raw, candidates)
    if codec.round_trip(doc) != raw or len(doc.body) < 12:
        raise ugc.CatalogError('Catalog index did not round-trip correctly.')
    size, packed, repeated = struct.unpack_from('>3I', doc.body)
    if size != repeated or not 0 < size <= MAX_INDEX or packed != len(doc.body)-12:
        raise ugc.CatalogError('Unsupported catalog index compression header.')
    dec = zlib.decompressobj(-15)
    data = dec.decompress(doc.body[12:], size+1)
    if len(data) != size or not dec.eof or dec.unused_data or dec.unconsumed_tail:
        raise ugc.CatalogError('Damaged or oversized catalog database.')
    db = sqlite3.connect(':memory:')
    try:
        db.deserialize(data)
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA quick_check').fetchone() != ('ok',):
            raise ugc.CatalogError('Catalog database integrity check failed.')
        objects = db.execute('SELECT type,name FROM sqlite_master').fetchall()
        if objects != [('table', 'MetaData')]:
            raise ugc.CatalogError('Unsupported catalog database schema.')
        if tuple(row[1] for row in db.execute('PRAGMA table_info(MetaData)')) != COLUMNS:
            raise ugc.CatalogError('Unsupported catalog metadata columns.')
        if db.execute('SELECT COUNT(*) FROM MetaData').fetchone()[0] > ugc.MAX_FILES:
            raise ugc.CatalogError('Too many catalog database entries.')
        return db, candidates.index((doc.aes_key, doc.mac_key))
    except BaseException:
        db.close()
        raise


def _encoded_database(db, keys):
    import livery_codec as codec
    db.commit()
    data = db.serialize()
    if len(data) > MAX_INDEX:
        raise ugc.CatalogError('Catalog database exceeds its size limit.')
    compressor = zlib.compressobj(wbits=-15)
    packed = compressor.compress(data)+compressor.flush()
    return codec.encode_stream(struct.pack('>3I', len(data), len(packed), len(data))+packed, keys)


def _journal(path):
    """Validate the complete journal before recovery can remove or rename anything."""
    try:
        record = json.loads(_read(path, MAX_INDEX))
        if (not isinstance(record, dict) or
                not isinstance(record.get('token'), str) or
                not re.fullmatch('[a-f0-9]{32}', record['token']) or
                type(record.get('existed')) is not bool):
            raise ValueError('invalid journal fields')
        for key in ('originals', 'published'):
            # Older journals only needed published hashes for first-catalog rollback.
            hashes = record.get(key, {} if key == 'published' and record['existed'] else None)
            if not isinstance(hashes, dict):
                raise ValueError('invalid catalog hashes')
            for name, digest in hashes.items():
                ugc._safe_name(name)
                if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
                    raise ValueError('invalid catalog hash')
        return record
    except (ValueError, TypeError, KeyError) as exc:
        raise ugc.CatalogError('Invalid import journal; restore the catalog backup manually. Nothing was removed.') from exc


def recovery_warning(target, error):
    """Read-only advice; never trust a journal-supplied path or an unverified backup."""
    storage = target.home/'save-backups/catalog-imports'
    backup = None
    try:
        _direct(storage)
        for candidate in sorted(storage.iterdir(), reverse=True):
            try:
                manifest = json.loads(_read(candidate/'manifest.json', MAX_INDEX))
                if (manifest['target'] != str(target.folder/'LivCatalog') or
                        manifest['identity'] != target.identity.hex().upper() or
                        type(manifest['existed']) is not bool or
                        not isinstance(manifest['files'], dict)):
                    continue
                actual = {name: hashlib.sha256(raw).hexdigest() for name, raw in
                          _tree(candidate/'LivCatalog', lambda: False).items()}
                if actual == manifest['files']:
                    backup = candidate
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    except (OSError, ugc.CatalogError):
        pass
    detail = (f'Latest verified catalog backup: {backup}. Check its manifest before restoring with the game closed.'
              if backup else f'No verified catalog backup was found. Catalog backup folder: {storage}.')
    return f'Design catalog recovery could not complete: {error} Play can continue; further imports require recovery. {detail}'


def recover(target):
    """Caller holds the lease. An interrupted publication rolls back before Play/import."""
    folder = _direct(target.folder)
    journal = _direct(folder/'.catalog-import.json')
    if not journal.exists():
        return
    record = _journal(journal)
    token = record['token']
    catalog = _direct(folder/'LivCatalog')
    stage = _direct(folder/('.catalog-stage-'+token))
    hold = _direct(folder/('.catalog-old-'+token))
    def matches(path, expected):
        return {name: hashlib.sha256(raw).hexdigest() for name,raw in _tree(path,lambda:False).items()} == expected
    if hold.exists():
        if not matches(hold, record['originals']):
            raise ugc.CatalogError('Import recovery backup does not match its journal. Nothing was removed.')
        if catalog.exists():
            shutil.rmtree(catalog)
        hold.rename(catalog)
    elif not record['existed'] and not stage.exists() and catalog.exists():
        if not matches(catalog, record['published']):
            raise ugc.CatalogError('Interrupted import changed after publication. Restore its backup manually.')
        shutil.rmtree(catalog)
    if stage.exists():
        shutil.rmtree(stage)
    journal.unlink()


def import_design(catalog, item_path, target, executable, cancelled=lambda: False, progress=lambda text: None):
    """Merge one authenticated FH1 design; preserve creator/lock flags and every other item."""
    paths.ensure_tools_importable()
    import livery_codec as codec
    from apply_title_update import Xex
    if catalog.title_id != TITLE_ID or catalog.profile_id is None or not catalog.index:
        raise ugc.CatalogError('Import requires the original FH1 LivCatalog save package with its index and profile identity.')
    item = next((i for i in catalog.items if i.path == item_path), None)
    if item is None or item.kind != 'livery' or item.model_id is None:
        raise ugc.CatalogError('Select an FH1 car design. Vinyl-group import is not supported yet.')
    ugc._safe_name(item.path)
    for name in item.files:
        ugc._safe_name(name)
        if PurePosixPath(name).name != name:
            raise ugc.CatalogError('Invalid design member filename.')
    _direct(target.folder)
    if not (target.folder/'ForzaProfile/ForzaProfile').is_file():
        raise ugc.CatalogError('Start and save this profile in-game before importing a design.')
    ugc._check(cancelled)
    progress('Authenticating source and destination…')
    executable = Path(executable)
    try:
        xex = Xex(_read(executable, codec.MAX_XEX))
    except (SystemExit, struct.error, IndexError) as exc:
        raise ugc.CatalogError('Could not read the FH1 executable. Choose your game files in Setup.') from exc
    info = xex.opts.get(codec.aes.HDR_EXECUTION_INFO)
    if info is None or struct.unpack_from('>I', xex.raw, info+12)[0] != int(TITLE_ID, 16):
        raise ugc.CatalogError('Choose your FH1 executable in Setup.')
    if xex.image_size > codec.MAX_XEX or xex.pages_size() > codec.MAX_XEX:
        raise ugc.CatalogError('Executable image exceeds the size limit.')
    try:
        image = xex.image()
    except (SystemExit, struct.error, IndexError) as exc:
        raise ugc.CatalogError('Could not decode the FH1 executable.') from exc
    source_keys = codec.derive(image, xex.load_address, catalog.profile_id)
    destination_keys = codec.derive(image, xex.load_address, target.identity)
    source_index_keys = codec.derive(image, xex.load_address, catalog.profile_id, index=True)
    destination_index_keys = codec.derive(image, xex.load_address, target.identity, index=True)
    with locked(target):
        recover(target)
        # Authenticate career identity without parsing or changing any career data.
        career = _read(target.folder/'ForzaProfile/ForzaProfile')
        if career[:4] != b'cmss' or len(career) < 76:
            raise ugc.CatalogError('Unsupported destination career save.')
        used = struct.unpack_from('>I', career, 4)[0]
        valid = False
        # the HMAC key of a TU4 profile follows the table's first half; a base-game profile's
        # is the 32 bytes before the table (save_editor._keys)
        for off in (address-xex.load_address+delta for address in (0x8200D634, 0x82013A04)
                    for delta in (32, -32)):
            if 0 <= off <= len(image)-32:
                key = bytes(((image[off+i]^target.identity[(i-4)%8])+2*i)%256 for i in range(32))
                valid |= 68 <= used <= len(career)-8 and hmac.compare_digest(hmac.digest(key, career[40:8+used], 'sha256'), career[8:40])
        if not valid:
            raise ugc.CatalogError('The destination career does not authenticate for the active profile identity. Nothing was changed.')
        source_db, source_variant = _database(catalog.index, source_index_keys)
        destination_db = None
        try:
            folder = str(PurePosixPath(item.path).parent)
            expected = 'LivCatalog:\\'+folder.replace('/', '\\')+'\\'
            source_db.text_factory = lambda b: b.decode('utf-8')
            rows = source_db.execute('SELECT * FROM MetaData WHERE Folder=?', (expected,)).fetchall()
            if len(rows) != 1 or rows[0][6] or rows[0][13] != item.model_id:
                raise ugc.CatalogError('The design and authenticated catalog metadata disagree.')
            row = list(rows[0])
            # Only known local thumbnail paths are installed; no external references.
            if row[7] != expected+'thumb.xdc' or row[10] != expected+'thumb.xdc' or any(row[i] for i in (8,9,11,12)):
                raise ugc.CatalogError('Unsupported thumbnail metadata for this design.')
            current = _direct(target.folder/'LivCatalog')
            originals = _tree(current, cancelled)
            if INDEX in originals:
                destination_db, variant = _database(originals[INDEX], destination_index_keys)
            else:
                if originals:
                    raise ugc.CatalogError('The destination catalog has no readable index. Nothing was changed.')
                destination_db = sqlite3.connect(':memory:')
                destination_db.deserialize(source_db.serialize())
                destination_db.execute('DELETE FROM MetaData WHERE SaveId<>0')
                variant = source_variant
            if destination_db.execute('SELECT 1 FROM MetaData WHERE lower(Folder)=lower(?) OR (Ordinal=? AND Title=?)', (expected,item.model_id,row[1])).fetchone():
                raise ugc.CatalogError('This design already exists in the destination catalog.')
            if any(name.casefold().startswith(folder.casefold()+'/') for name in originals):
                raise ugc.CatalogError('The destination design folder already exists.')
            row[0] = destination_db.execute('SELECT COALESCE(MAX(SaveId),0)+1 FROM MetaData').fetchone()[0]
            destination_db.execute('INSERT INTO MetaData VALUES ('+','.join('?' for _ in COLUMNS)+')', row)
            updated = dict(originals)
            for name, raw in item.files.items():
                ugc._check(cancelled)
                if name.lower().endswith('.liv') or name == 'StorefrontID':
                    doc = codec.decode(raw, source_keys) if name.lower().endswith('.liv') else codec.decode_stream(raw, source_keys)
                    if codec.round_trip(doc) != raw:
                        raise ugc.CatalogError('The source payload did not round-trip correctly.')
                    key = destination_keys[source_keys.index((doc.aes_key, doc.mac_key))]
                    raw = codec.encode_stream(doc.body, key)
                    if codec.decode_stream(raw, (key,)).body != doc.body:
                        raise ugc.CatalogError('The imported payload failed verification.')
                updated[folder+'/'+name] = raw
            updated[INDEX] = _encoded_database(destination_db, destination_index_keys[variant])
            if len(updated) > ugc.MAX_FILES or sum(map(len, updated.values())) > ugc.MAX_BYTES:
                raise ugc.CatalogError('The merged catalog exceeds its size limit.')
            verified, _ = _database(updated[INDEX], destination_index_keys)
            verified.close()
            ugc._check(cancelled)
            progress('Backing up the existing catalog…')
            backup = _direct(target.home/'save-backups/catalog-imports'/
                (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex))
            backup.mkdir(parents=True)
            for name, data in originals.items():
                ugc._check(cancelled)
                path = backup/'LivCatalog'/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                if path.read_bytes() != data:
                    raise ugc.CatalogError('Catalog backup verification failed.')
            manifest = dict(target=str(current), identity=target.identity.hex().upper(), existed=current.exists(),
                files={name: hashlib.sha256(data).hexdigest() for name,data in originals.items()})
            (backup/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
            progress('Publishing the verified design…')
            token = uuid.uuid4().hex
            stage = _direct(target.folder/('.catalog-stage-'+token))
            hold = _direct(target.folder/('.catalog-old-'+token))
            journal = _direct(target.folder/'.catalog-import.json')
            stage.mkdir()
            try:
                for name,data in updated.items():
                    ugc._check(cancelled)
                    path = stage/name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                # Recheck the destination after staging, before changing any original file.
                if _tree(current, cancelled) != originals:
                    raise ugc.CatalogError('The destination changed during import. Close the game and retry.')
                if _read(target.folder/'ForzaProfile/ForzaProfile') != career:
                    raise ugc.CatalogError('The career changed during import. Close the game and retry.')
                ugc._check(cancelled)
                record = json.dumps(dict(token=token,existed=current.exists(),
                    originals={n:hashlib.sha256(d).hexdigest() for n,d in originals.items()},
                    published={n:hashlib.sha256(d).hexdigest() for n,d in updated.items()}))
                if len(record.encode('utf-8')) > MAX_INDEX:
                    raise ugc.CatalogError('Catalog paths exceed the recovery journal limit.')
                with journal.open('x', encoding='utf-8') as stream:
                    stream.write(record)
                    stream.flush(); os.fsync(stream.fileno())
                # Publication is non-cancellable; a crash journal restores the previous catalog.
                if current.exists():
                    current.rename(hold)
                stage.rename(current)
                journal.unlink()
                if hold.exists():
                    shutil.rmtree(hold)
            except BaseException:
                if journal.exists():
                    recover(target)
                raise
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            return backup
        finally:
            source_db.close()
            if destination_db is not None:
                destination_db.close()
