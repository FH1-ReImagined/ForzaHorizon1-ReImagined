"""Mods (#149): folders of files the game reads in place of its own, laid over its archives at
start without changing them (docs/modding.md).

Each mod is a folder in <data>/mods: mod.toml (name, version, author, description, all
optional) and its files under media/, at the game's own paths. launcher.json keeps the load
order and the mods turned off ("mods": {"order": [...], "disabled": [...]}); the game gets them
as --forza_mods_dir and --forza_mods (the folders to load in load order, '|'-separated, a later
one winning over an earlier one for the same file)."""
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

MANIFEST = "mod.toml"
NONE = "|"  # --forza_mods value for "no mods" (an empty list)
MAX_FILE = 0x7FFFFFFF           # the game takes no bigger file
MAX_MEMBER = (1 << 26) - 1      # the game's archive index: bigger files only replace loose files
MAX_NAME = 255                  # the game's archive names (mod_archive.h kMaxName)


@dataclass
class Mod:
    folder: str
    name: str = ""
    version: str = ""
    author: str = ""
    description: str = ""
    files: int = 0
    size: int = 0
    problem: str = ""   # why the game would skip it ("" when it loads)
    note: str = ""      # a warning that does not stop it loading
    enabled: bool = True
    names: list = field(default_factory=list)  # its files' game paths, lower case, under media/

    @property
    def title(self) -> str:
        return self.name or self.folder

    def summary(self) -> str:
        bits = [self.title]
        if self.version:
            bits.append(self.version)
        if self.author:
            bits.append(f"by {self.author}")
        return " ".join(bits)


def mods_dir(cfg) -> Path:
    return Path(cfg.data_home()) / "mods"


def game_media(cfg) -> Path:
    """The game's media folder (loose files a big mod file may replace)."""
    return Path(cfg.game_files()) / "media"


def _plain(rel: str) -> bool:
    """The game's paths: printable ASCII without :*?"<>| (mod_archive.h ScanMod)."""
    return all(0x20 <= ord(c) <= 0x7E and c not in ':*?"<>|' for c in rel)


def _loose_file(media: Path | None, rel: str) -> bool:
    """Whether the game has the loose file media/<rel>, matched without case."""
    if media is None:
        return False
    here = media
    for part in rel.split("/"):
        try:
            names = {p.name.lower(): p for p in here.iterdir()}
        except OSError:
            return False
        here = names.get(part.lower())
        if here is None:
            return False
    return here.is_file()


def read_mod(folder: Path, media_of_game: Path | None = None) -> Mod:
    """A mod folder: its manifest and its files under media/, as the game will see them
    (forza-recomp/src/platform/mod_archive.h ScanMod and mod_overlay.cpp InstallMods)."""
    mod = Mod(folder=folder.name)
    if "|" in folder.name:
        mod.problem = "the folder's name has a |, which the game reads as a separator: rename it"
    elif folder.name == "*":
        mod.problem = "a folder named * would make the game load every mod: rename it"
    try:
        _read_manifest(folder, mod)
        if not mod.problem:
            _read_files(folder, mod, media_of_game)
    except OSError as e:
        mod.problem = mod.problem or f"its folder could not be read ({e})"
    return mod


def _read_manifest(folder: Path, mod: Mod):
    manifest = folder / MANIFEST
    if not manifest.is_file():
        return
    try:
        data = manifest.read_text(encoding="utf-8-sig")
        info = tomllib.loads(data) if tomllib else {}
    except (OSError, UnicodeDecodeError, ValueError) as e:
        mod.note = f"{MANIFEST} could not be read ({e}): the folder's name is shown instead"
        info = {}
    for key in ("name", "version", "author", "description"):
        value = info.get(key)
        if isinstance(value, (str, int, float)):
            setattr(mod, key, str(value).strip())


def _read_files(folder: Path, mod: Mod, media_of_game: Path | None):
    media = folder / "media"
    if not media.is_dir():
        mod.problem = "no media folder: put the files under media/, at the game's paths"
        return
    left_out, seen, visited = [], {}, set()

    def onerror(e):
        raise e

    # Links are followed, as the game does; names are where the files are listed.
    for dirpath, dirnames, filenames in os.walk(media, followlinks=True, onerror=onerror):
        st = os.stat(dirpath)
        if (st.st_dev, st.st_ino) in visited:  # a link back up the tree
            dirnames[:] = []
            continue
        visited.add((st.st_dev, st.st_ino))
        for name in filenames:
            p = Path(dirpath) / name
            rel = Path(os.path.relpath(p, media)).as_posix()
            shown = f"media/{rel}"
            if not p.is_file():
                continue
            if not _plain(rel):
                left_out.append(f"{shown}: the game's paths are plain ASCII")
                continue
            game_name = rel.lower()
            if len(game_name.encode()) > MAX_NAME:
                left_out.append(f"{shown}: its path is over 255 characters")
                continue
            if game_name.endswith(".zip"):
                left_out.append(f"{shown}: whole archives are not replaced, put the files from inside it in the mod")
                continue
            size = p.stat().st_size
            if size > MAX_FILE:
                mod.problem = f"{shown} is over 2 GB, too big for the game"
                return
            if size > MAX_MEMBER and not _loose_file(media_of_game, rel):
                mod.problem = (f"{shown} is over 64 MB, more than the game's archives hold, and the game "
                               "has no loose file of that name for it to replace")
                return
            if not os.access(p, os.R_OK):
                mod.problem = f"cannot read {shown}"
                return
            if game_name in seen:
                mod.problem = f"two files differ only in case: {shown} and media/{seen[game_name]}"
                return
            seen[game_name] = rel
            mod.names.append(game_name)
            mod.files += 1
            mod.size += size
    if left_out:
        note = f"{len(left_out)} file(s) left out: " + "; ".join(left_out[:3]) + ("; ..." if len(left_out) > 3 else "")
        mod.note = f"{mod.note}\n{note}" if mod.note else note
    if not mod.files:
        mod.problem = "no files under media/ the game can use"


def scan(directory: Path, media_of_game: Path | None = None) -> list:
    """The mod folders in `directory`, by name."""
    try:
        folders = sorted((p for p in Path(directory).iterdir() if not p.name.startswith(".") and p.is_dir()),
                         key=lambda p: p.name.lower())
    except OSError:
        return []
    return [read_mod(p, media_of_game) for p in folders]


# The load order and the folders turned off, kept in launcher.json under `key`: "mods" here,
# "texture_packs" for the texture packs (texture_packs.py), which list folders the same way.
def state(cfg, key="mods") -> dict:
    s = cfg.d.get(key)
    if not isinstance(s, dict):
        s = {}
    def names(name):
        value = s.get(name)  # a hand-edited launcher.json may hold anything here
        return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []
    return {"order": names("order"), "disabled": names("disabled")}


def ordered(cfg, found: list, key="mods") -> list:
    """`found` in the saved load order, new mods last (by name), each on unless turned off."""
    s = state(cfg, key)
    by_folder = {m.folder: m for m in found}
    out = [by_folder.pop(f) for f in s["order"] if f in by_folder]
    out += [m for m in found if m.folder in by_folder]
    for m in out:
        m.enabled = m.folder not in s["disabled"]
    return out


def remember(cfg, mods: list, key="mods"):
    """Saves the load order and the mods turned off (mods no longer there are kept, so a mod
    moved out and back keeps its place)."""
    s = state(cfg, key)
    shown = {m.folder for m in mods}
    order = [m.folder for m in mods] + [f for f in s["order"] if f not in shown]
    disabled = [m.folder for m in mods if not m.enabled] + [f for f in s["disabled"] if f not in shown]
    cfg.d[key] = {"order": order, "disabled": disabled}
    cfg.save()


def forget(cfg, folder: str, key="mods"):
    """Drops a removed folder from the load order and the folders turned off."""
    s = state(cfg, key)
    cfg.d[key] = {"order": [f for f in s["order"] if f != folder],
                  "disabled": [f for f in s["disabled"] if f != folder]}
    cfg.save()


def enabled(cfg) -> list:
    """The mods the game will load, in load order."""
    return [m for m in ordered(cfg, scan(mods_dir(cfg), game_media(cfg))) if m.enabled and not m.problem]


def launch_args(cfg, named=()) -> list:
    """The game's mod options, leaving out those the player names in the extra flags
    (`named`: option names without dashes)."""
    args = []
    if "forza_mods_dir" not in named:
        args.append(f"--forza_mods_dir={mods_dir(cfg)}")
    if "forza_mods" not in named:
        args.append("--forza_mods=" + ("|".join(m.folder for m in enabled(cfg)) or NONE))
    return args


def conflicts(mods: list) -> dict:
    """For each mod that loads, the files another loading mod has too: {folder: [(file, other
    folder, True when this one wins)]}. Later in the list wins, as in the game."""
    loading = [m for m in mods if m.enabled and not m.problem]
    owners = {}
    for i, m in enumerate(loading):
        for name in m.names:
            owners.setdefault(name, []).append(i)
    out = {}
    for name, idx in sorted(owners.items()):
        if len(idx) < 2:
            continue
        last = idx[-1]
        for i in idx:
            for j in idx:
                if i != j:
                    out.setdefault(loading[i].folder, []).append((name, loading[j].folder, i == last))
    return out


# --- adding and removing mods (the Mods page) -----------------------------------------------

MAX_INSTALL = 8 << 30        # a mod archive or folder at most this big unpacked
MAX_ENTRIES = 200_000


class InstallError(Exception):
    """Why a mod could not be added, in words for the player."""


class ModExists(Exception):
    def __init__(self, folder):
        super().__init__(folder)
        self.folder = folder


def _is_root(p: Path) -> bool:
    return (p / "media").is_dir() or (p / MANIFEST).is_file()


def find_root(folder: Path) -> Path | None:
    """The mod in `folder`: itself when it holds media/ or mod.toml, else its only subfolder
    that does (a zip's contents usually sit in one folder)."""
    if _is_root(folder):
        return folder
    found = [p for p in sorted(folder.iterdir()) if p.is_dir() and _is_root(p)]
    return found[0] if len(found) == 1 else None


def _check_name(name: str):
    if not name or name.startswith(".") or name == "*" or "|" in name:
        raise InstallError(f'"{name}" cannot be a mod\'s folder name (no "|", not "*", not starting '
                           'with a dot): rename it and add it again')


def _safe_members(z: zipfile.ZipFile, what="mod", max_bytes=None, max_entries=None,
                  size_text="8 GB") -> list:
    """The zip's members, refused when one would land outside the folder it is unpacked in
    (an absolute path, "..", a link) or the whole is too big (max_bytes, said as size_text;
    default: a mod's limits). `what` names the thing in the messages (a mod, a texture pack)."""
    max_bytes = MAX_INSTALL if max_bytes is None else max_bytes
    max_entries = MAX_ENTRIES if max_entries is None else max_entries
    infos = z.infolist()
    if len(infos) > max_entries:
        raise InstallError(f"The zip holds {len(infos)} files, more than a {what} can have.")
    total = 0
    for info in infos:
        name = info.filename.replace("\\", "/")
        parts = PurePosixPath(name).parts
        if (name.startswith("/") or (len(name) > 1 and name[1] == ":") or ".." in parts):
            raise InstallError(f'The zip has a file outside its own folder ("{info.filename}"); it was '
                               f'not added. Get the {what} from its author again.')
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise InstallError(f'The zip has a link ("{info.filename}"), which {what}s cannot hold; it was '
                               'not added.')
        total += info.file_size
        if total > max_bytes:
            raise InstallError(f"The zip unpacks to more than {size_text}, more than a {what} can be.")
    return infos


def _unpack(source: Path, into: Path) -> Path:
    """Copies or unpacks `source` into the empty folder `into`; returns the mod's root in it."""
    if source.is_dir():
        root = find_root(source)
        if root is None:
            raise InstallError(f'"{source.name}" is not a mod: a mod folder holds a media folder (and '
                               'usually mod.toml).')
        size = 0
        for dirpath, _, files in os.walk(root, followlinks=True):
            for f in files:
                size += os.path.getsize(os.path.join(dirpath, f))
                if size > MAX_INSTALL:
                    raise InstallError("The folder holds more than 8 GB, more than a mod can be.")
        _check_name(root.name)
        shutil.copytree(root, into / root.name, symlinks=False)
        return into / root.name
    if not zipfile.is_zipfile(source):
        raise InstallError(f'"{source.name}" is not a zip file or a folder.')
    try:
        with zipfile.ZipFile(source) as z:
            infos = _safe_members(z)
            unpacked = into / ".contents"  # no mod's name starts with a dot
            unpacked.mkdir()
            for info in infos:
                z.extract(info, unpacked)
    except NotImplementedError:
        raise InstallError("The zip uses a compression this launcher cannot unpack; repack it as a "
                           "normal zip.") from None
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError) as e:
        raise InstallError(f"The zip could not be read ({e}).") from None
    root = find_root(unpacked)
    if root is None:
        raise InstallError(f'"{source.name}" holds no mod: there is no media folder at its top or in '
                           'its one folder.')
    name = source.stem if root == unpacked else root.name
    _check_name(name)
    final = into / name
    root.rename(final)
    return final


def install(source: Path, directory: Path, replace=False, media_of_game: Path | None = None) -> Mod:
    """Adds the mod in `source` (a folder or a .zip) to the mods folder `directory`. The files
    are checked with the game's rules first and land in place only when all is well. Raises
    InstallError (with words for the player) or ModExists (call again with replace=True)."""
    source = Path(source)
    directory = Path(directory)
    if not source.exists():
        raise InstallError(f'"{source}" is not there any more.')
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if source.is_dir() and directory.resolve() in (source.resolve(), *source.resolve().parents):
            raise InstallError("That folder is already in the mods folder: it is listed on this page "
                               "(press Refresh).")
        staging = Path(tempfile.mkdtemp(prefix=".adding-", dir=directory))
    except OSError as e:
        raise InstallError(f"The mods folder could not be written ({e}).") from None
    try:
        try:
            root = _unpack(source, staging)
        except OSError as e:
            raise InstallError(f"The mod could not be copied ({e}).") from None
        mod = read_mod(root, media_of_game)
        if mod.problem:
            raise InstallError(f"This mod would not load: {mod.problem}.")
        target = directory / root.name
        try:
            if target.exists() or target.is_symlink():
                if not replace:
                    raise ModExists(root.name)
                old = staging / ".old"
                target.rename(old)
                try:
                    root.rename(target)
                except OSError:
                    old.rename(target)  # put the mod that was there back
                    raise
            else:
                root.rename(target)
        except OSError as e:
            raise InstallError(f"The mod could not be put in the mods folder ({e}). Is a file of the "
                               "old one open?") from None
        return read_mod(target, media_of_game)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove(cfg, folder: str):
    """Deletes a mod's folder from the mods folder (a linked folder: only the link) and forgets
    its place in the load order."""
    directory = mods_dir(cfg)
    target = directory / folder
    if target.parent != directory or folder in ("", ".", ".."):
        raise InstallError(f'"{folder}" is not a mod in the mods folder.')
    try:
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
    except OSError as e:
        raise InstallError(f"The mod could not be removed ({e}). Is a file of it open?") from None
    forget(cfg, folder)
