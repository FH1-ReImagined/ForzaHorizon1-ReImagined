"""ReImagined loading-screen tips (Settings > Gameplay > ReImagined loading tips, on by default).

FH1's loading screens show a title and a tip from the string table tips.str of the game's
language: the title update's copy in update:\\media.zip (stringtables/<lang>/tips.str, 20
languages, 204 entries in English: IDS_<Name>Tip... with IDS_<Name>Title...), else the disc's
stringtables/<LANG>.zip (Tips.str). The game picks the pair itself from ui/loadingdefs.xml
(the title update's: 25 loading groups for free roam, races, Playground games, rally and the
rest, 192 tips, each group random or in order, 1 to 5 tips per screen, 8 s each); the text is
all that changes.

With the setting on, a start gets a hidden mod, mods/.reimagined-tips, whose
media/stringtables/<lang>/tips.str is the game's own table with each tip slot (a title entry and
its tip entry, paired as loadingdefs.xml pairs them) set to one of the data file's tips in turn
(the ids, keys and order stay). The mod loads first, so a player's own mod with a tips.str
wins. The mod is rebuilt only when the data file or the game's files change. Off: no mod, the
game's own tips.

The tips are in one data file, data/loading_tips.json ("tips": [{"title", "tip"}, ...]).
Every language shows them in English for now.
"""
import hashlib
import json
import re
import shutil
import struct
import tempfile
from pathlib import Path

from . import mods, paths

MOD = ".reimagined-tips"
MARKER = "tips.json"
DATA = Path(__file__).resolve().parent / "data" / "loading_tips.json"
SETTING = "reimagined_loading_tips"
# The longest text the game's own tables hold in those entries (English, Title Update 4): the
# loading screen shows them whole, so the data file's stay within them.
MAX_TIP = 194
MAX_TITLE = 19


class TipsError(ValueError):
    pass


def load(path: Path = None):
    """[(title, tip)] of the data file, checked against the limits."""
    path = Path(path or DATA)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        entries = [(str(e.get("title", "")).strip(), str(e.get("tip", "")).strip()) for e in d.get("tips", [])]
    except (OSError, ValueError, AttributeError) as e:
        raise TipsError(f"{path} could not be read: {e}") from None
    if not entries:
        raise TipsError(f"{path} needs at least one tip.")
    for title, tip in entries:
        if not title or not tip:
            raise TipsError(f"Every tip needs a title and a text: {title or tip}")
        if len(title) > MAX_TITLE:
            raise TipsError(f"A tip's title is {len(title)} characters; the loading screen fits {MAX_TITLE}: {title}")
        if len(tip) > MAX_TIP:
            raise TipsError(f"A tip is {len(tip)} characters; the loading screen fits {MAX_TIP}: {tip[:60]}...")
    return entries


def string_ids(data: bytes) -> list:
    """The id names of an LSB2 string table, in its entry order (radio.patch_strings's layout)."""
    if data[:4] != b"LSB2" or len(data) < 28:
        raise TipsError("not an LSB2 string table")
    n = struct.unpack_from(">I", data, 16)[0]
    offsets = struct.unpack_from(f">{n}I", data, 20)
    a, b = offsets[:2]
    if not b:
        return []
    count = struct.unpack_from(">I", data, a + 4)[0]
    keys = [struct.unpack_from(">H", data, a + 8 + i * 6)[0] for i in range(count)]
    idcount = struct.unpack_from(">I", data, b + 4)[0]
    idpool = b + 8 + idcount * 8
    names = {}
    for i in range(idcount):
        key, _, offset = struct.unpack_from(">HHI", data, b + 8 + i * 8)
        start = idpool + offset
        end = data.find(b"\0", start)
        names[key] = data[start:end].decode("ascii")
    return [names[k] for k in keys if k in names]


def is_title(name: str) -> bool:
    return "title" in name.lower()


def slot_pairs(xml: bytes) -> list:
    """[(title id, tip id)] of loadingdefs.xml's <Tip> entries, each pair once, in file order."""
    text = xml.decode("utf-16") if xml[:2] in (b"\xff\xfe", b"\xfe\xff") else xml.decode("utf-8", "replace")
    out, seen = [], set()
    for tag in re.findall(r"<Tip\b[^>]*>", text):
        title = re.search(r'\btitle="Tips:(\w+)"', tag)
        tip = re.search(r'\bmessage="Tips:(\w+)"', tag)
        if title and tip and (title.group(1), tip.group(1)) not in seen:
            seen.add((title.group(1), tip.group(1)))
            out.append((title.group(1), tip.group(1)))
    return out


def _stem(name: str) -> str:
    """IDS_PulseTip and IDS_PulseTitle -> IDS_Pulse: pairs the entries no loading screen names."""
    name = re.sub(r"_?Title", "", name, count=1)
    return re.sub(r"_?(Tips?|Desc)(\d*)$", r"\2", name)


def replacements(ids, entries, pairs=()) -> dict:
    """{id: text}: each tip slot gets the next entry, its title in the title entry and its tip in
    the tip entry. The slots are loadingdefs.xml's pairs, then the entries no loading screen
    names, paired by name."""
    slot, n = {}, 0
    for title, tip in pairs:
        slot[title] = slot[tip] = n
        n += 1
    stems = {}
    for name in ids:
        if name not in slot:
            if _stem(name) not in stems:
                stems[_stem(name)] = n
                n += 1
            slot[name] = stems[_stem(name)]
    titles, tips = {t for t, _ in pairs}, {m for _, m in pairs}
    out = {}
    for name in ids:
        title, tip = entries[slot[name] % len(entries)]
        out[name] = title if name in titles or (name not in tips and is_title(name)) else tip
    return out


def rewrite(data: bytes, entries, pairs=()) -> bytes:
    from .radio import RadioError, patch_strings
    try:
        return patch_strings(data, replacements(string_ids(data), entries, pairs))
    except RadioError as e:
        raise TipsError(str(e)) from None


def sources(cfg) -> dict:
    """{language folder (lower case): tips.str bytes} of the game: the title update's, else the
    disc's (stringtables/<LANG>.zip)."""
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
        data = updated(f"stringtables/{lang}/tips.str", lambda a=archive: read_member(a, "Tips.str"))
        if data and data[:4] == b"LSB2":
            out[lang] = data
    return out


def slot_source(cfg) -> bytes:
    """The game's loadingdefs.xml (the title update's, else the disc's media/ui/LoadingDefs.xml),
    or b"" when there is none (the entries are then paired by name)."""
    from .radio import _updated

    def disc():
        ui = Path(cfg.game_files()) / "media" / "ui"
        try:
            found = [p for p in ui.iterdir() if p.name.lower() == "loadingdefs.xml"]
            return found[0].read_bytes() if found else b""
        except OSError:
            return b""
    return _updated(cfg)("ui/loadingdefs.xml", disc) or b""


def _fingerprint(cfg, tables: dict, defs: bytes = b"") -> str:
    h = hashlib.sha256(DATA.read_bytes())
    h.update(hashlib.sha256(defs).digest())
    for lang in sorted(tables):
        h.update(lang.encode() + hashlib.sha256(tables[lang]).digest())
    return h.hexdigest()


def enabled(cfg) -> bool:
    return bool(cfg.effective_settings().get(SETTING, True))


def ensure_mod(cfg, log=print):
    """The hidden mod's folder name when the setting is on and the mod is ready, else None. Builds
    or rebuilds it as needed; any failure leaves the game's own tips (and says why in the log)."""
    target = mods.mods_dir(cfg) / MOD
    if not enabled(cfg):
        return None
    try:
        entries = load()
        tables = sources(cfg)
        if not tables:
            return None
        defs = slot_source(cfg)
        pairs = slot_pairs(defs)
        stamp = _fingerprint(cfg, tables, defs)
        try:
            if json.loads((target / MARKER).read_text(encoding="utf-8")).get("stamp") == stamp:
                return MOD
        except (OSError, ValueError):
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".tips-", dir=target.parent) as temp:
            staged = Path(temp) / MOD
            for lang, data in tables.items():
                out = staged / "media" / "stringtables" / lang / "tips.str"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(rewrite(data, entries, pairs))
            (staged / "mod.toml").write_text('name = "ReImagined loading tips"\n', encoding="utf-8")
            (staged / MARKER).write_text(json.dumps({"stamp": stamp, "tips": len(entries), "slots": len(pairs),
                                                     "languages": sorted(tables)}), encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            staged.rename(target)
        return MOD
    except (OSError, TipsError) as e:
        log(f"ReImagined loading tips: {e}; the game's own tips are shown")
        return None


def launch_args(cfg, args: list, write=True, log=print):
    """Puts the tips' mod first in --forza_mods (the player's mods after it win). A plan that
    writes nothing (a preview) names the mod only when it is already built."""
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
