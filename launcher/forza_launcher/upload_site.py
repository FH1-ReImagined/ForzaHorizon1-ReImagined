"""Photo Mode's Big Shot upload names forza.dyotson.dev (#875).

A Big Shot (Photo Mode, BIG SHOT : 8.3 MEGAPIXEL) is uploaded to the server the game is signed in
to, and the main Forza Live server publishes it on forza.dyotson.dev. The game's texts for it
still name ForzaMotorsport.net, which is gone. They are five entries of the string table
carflow.str of the game's language (the title update's copy in update:\\media.zip,
stringtables/<lang>/carflow.str, 20 languages; else the disc's stringtables/<LANG>.zip,
CarFlow.str): the question before the photo is made, the text of its loading screen, the question
about replacing the one uploaded before, and the result and error dialogs.

A start that goes online gets a hidden mod, mods/.reimagined-upload-site, whose
media/stringtables/<lang>/carflow.str is the game's own table with the site's name replaced in
those entries and nothing else: the rest of each text stays, so every language keeps its own
wording, and so do the ids, keys and order. The mod loads first, so a player's own mod with a
carflow.str wins. It is rebuilt only when the game's tables or the entries below change.

There is no setting for it: the text says where the photo goes, so it follows the server Play
uses (Online > Servers). With This PC or Offline nothing reaches the site, and the game's own text
stays.
"""
import hashlib
import json
import re
import shutil
import struct
import tempfile
from pathlib import Path

from . import mods, paths

MOD = ".reimagined-upload-site"
MARKER = "site.json"
SITE = "forza.dyotson.dev"
# The old site's name as the tables write it: ForzaMotorsport.net, and Forzamotorsport.net in one
# entry.
OLD = re.compile(r"forzamotorsport\.net", re.IGNORECASE)
# {table: its entries the Big Shot shows}. The game reads them in the photo camera's own code
# (Title Update 4: sub_827DB540 the question, sub_828006B8 the loading screen, sub_827E9960 the
# rest). The table's other entries with the old name are the movie upload's and stay.
ENTRIES = {
    "carflow": (
        "IDS_Photo_ConfirmExport_Msg",          # "You are about to create a large photo and upload it to ..."
        "IDS_Photo_BigShot_Msg",                # "This photo will be automatically uploaded to ..."
        "IDS_Photo_Upload_Confirm_Overwrite",   # "You have already uploaded a big shot photo to ..."
        "IDS_BigShot_Upload_Success",           # "... was successfully uploaded to the ... server."
        "IDS_BigShot_Upload_Failure",           # "... was NOT successfully uploaded to the ... server."
    ),
}


class SiteError(ValueError):
    pass


def texts(data: bytes) -> dict:
    """{id name: text} of an LSB2 string table (radio.patch_strings's layout). A table without
    ids (the game's LOC placeholder) has none."""
    if data[:4] != b"LSB2" or len(data) < 28:
        raise SiteError("not an LSB2 string table")
    try:
        n = struct.unpack_from(">I", data, 16)[0]
        a, b = struct.unpack_from(f">{n}I", data, 20)[:2]
        if not b:
            return {}
        count = struct.unpack_from(">I", data, a + 4)[0]
        pool = a + 8 + 6 * (count + 1)
        idcount = struct.unpack_from(">I", data, b + 4)[0]
        idpool = b + 8 + idcount * 8
        names = {}
        for i in range(idcount):
            key, _, offset = struct.unpack_from(">HHI", data, b + 8 + i * 8)
            start = idpool + offset
            names[key] = data[start:data.index(b"\0", start)].decode("ascii")
        out = {}
        for i in range(count):
            key, offset = struct.unpack_from(">HI", data, a + 8 + i * 6)
            pos = end = pool + offset * 2
            while data[end:end + 2] != b"\0\0":
                if end + 1 >= b:
                    raise SiteError("a string table's text is truncated")
                end += 2
            if key in names:
                out[names[key]] = data[pos:end].decode("utf-16-be")
        return out
    except (struct.error, ValueError) as e:
        if isinstance(e, SiteError):
            raise
        raise SiteError(f"a string table could not be read: {e}") from None


def replacements(data: bytes, ids) -> dict:
    """{id: text} of the entries of `ids` that name the old site, with the new name in its place."""
    have = texts(data)
    return {i: OLD.sub(SITE, have[i]) for i in ids if i in have and OLD.search(have[i])}


def rewrite(data: bytes, ids):
    """The table with the site's name replaced in the entries of `ids`; None when none of them
    names the old site (nothing to change)."""
    from .radio import RadioError, patch_strings
    changed = replacements(data, ids)
    if not changed:
        return None
    try:
        return patch_strings(data, changed)
    except RadioError as e:
        raise SiteError(str(e)) from None


def sources(cfg) -> dict:
    """{(language folder (lower case), table): the game's .str bytes}: the title update's, else
    the disc's (stringtables/<LANG>.zip)."""
    from .radio import _updated
    paths.ensure_tools_importable()
    from extract_map_points import read_member
    updated = _updated(cfg)
    out = {}
    tables = Path(cfg.game_files()) / "media" / "stringtables"
    try:
        archives = sorted(p for p in tables.glob("*.zip") if p.is_file())
    except OSError:
        archives = []
    for archive in archives:
        lang = archive.stem.lower()
        for table in ENTRIES:
            data = updated(f"stringtables/{lang}/{table}.str",
                           lambda a=archive, t=table: read_member(a, t + ".str"))
            if data and data[:4] == b"LSB2":
                out[(lang, table)] = data
    return out


def _fingerprint(tables: dict) -> str:
    h = hashlib.sha256(json.dumps([SITE, OLD.pattern, ENTRIES], sort_keys=True).encode())
    for lang, table in sorted(tables):
        h.update(f"{lang}/{table}".encode() + hashlib.sha256(tables[(lang, table)]).digest())
    return h.hexdigest()


def enabled(cfg) -> bool:
    """Play goes online to a server other than this PC's own (the main server, a listed one or an
    address): the only case in which a Big Shot can reach the site."""
    return cfg.online.get("mode") in ("public", "custom") and bool(cfg.server_spec())


def ensure_mod(cfg, log=print):
    """The hidden mod's folder name when Play goes online and the mod is ready, else None. Builds
    or rebuilds it as needed; any failure leaves the game's own text (and says why in the log)."""
    target = mods.mods_dir(cfg) / MOD
    if not enabled(cfg):
        return None
    try:
        tables = sources(cfg)
        if not tables:
            return None
        stamp = _fingerprint(tables)
        try:
            if json.loads((target / MARKER).read_text(encoding="utf-8")).get("stamp") == stamp:
                return MOD
        except (OSError, ValueError):
            pass
        changed = {}
        for (lang, table), data in tables.items():
            new = rewrite(data, ENTRIES[table])
            if new is not None:
                changed[(lang, table)] = new
        if not changed:  # tables that do not name the old site (a changed game): nothing to do
            return None
        if target.is_symlink() or (target.exists() and not (target / MARKER).is_file()):
            raise SiteError(f"{target} was not made by the launcher; move it away")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".site-", dir=target.parent) as temp:
            staged = Path(temp) / MOD
            for (lang, table), data in changed.items():
                out = staged / "media" / "stringtables" / lang / f"{table}.str"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
            (staged / "mod.toml").write_text('name = "Photo upload site name"\n', encoding="utf-8")
            (staged / MARKER).write_text(json.dumps({
                "stamp": stamp, "site": SITE, "tables": len(changed),
                "languages": sorted({lang for lang, _ in changed})}), encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            staged.rename(target)
        return MOD
    except Exception as e:  # noqa: BLE001 - a failure here must never stop the game from starting (#758)
        log(f"Photo upload site name: {e}; the game's own text is shown")
        return None


def launch_args(cfg, args: list, write=True, log=print):
    """Puts the mod first in --forza_mods (the player's mods after it win). A plan that writes
    nothing (a preview) names the mod only when it is already built."""
    if write:
        folder = ensure_mod(cfg, log)
    else:
        folder = MOD if enabled(cfg) and (mods.mods_dir(cfg) / MOD / MARKER).is_file() else None
    if not folder:
        return
    for i, a in enumerate(args):
        if a.startswith("--forza_mods="):
            have = [] if a == "--forza_mods=" + mods.NONE else [m for m in a.split("=", 1)[1].split("|") if m != MOD]
            args[i] = "--forza_mods=" + "|".join([folder] + have)
