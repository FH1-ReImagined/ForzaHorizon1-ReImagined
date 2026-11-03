"""Texture packs (#384): folders of replacement textures in <data>/textures, each file named by
the fingerprint of the game texture it replaces (docs/texture_packs.md).

Each pack is a folder in the textures folder: <fingerprint>.dds or .png files (in folders inside
it too) and an optional pack.toml (name, version, author, description). The game's own dump
(textures/dump) is not a pack. launcher.json keeps the order and the packs turned off
("texture_packs": {"order": [...], "disabled": [...]}, the Mods page's format); with Use texture
packs on, the game gets the packs to use as --texture_packs (folders '|'-separated, a later one
winning for the same texture: SDK patch 0310). "hd-pack" is the pack Make HD pack writes
(hd_pack.py); it starts at the top of the list, so the packs below it win.

Texture files directly in the textures folder (milestone 1's layout) are the implicit pack
"Loose files (textures folder)" (folder "."; the game reads "." as the files in the folder
itself, not its folders): always first, so every pack wins over it; it can be turned off, not
moved or removed."""
import os
import re
import shutil
import struct
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import mods

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None

STATE_KEY = "texture_packs"
MANIFEST = "pack.toml"
DUMP = "dump"
HD_PACK = "hd-pack"
LOOSE = "."   # the loose files directly in the textures folder (--texture_packs name)
LOOSE_TITLE = "Loose files (textures folder)"
NONE = "|"  # --texture_packs value for "no packs"
FINGERPRINT = re.compile(r"^[0-9a-fA-F]{16}$")
EXTENSIONS = (".dds", ".png")
# Copied with a pack besides its textures: its manifest, read-me and licence files.
DOC_EXTENSIONS = (".txt", ".md", ".toml")
MAX_SIZE = 16384                # the game's largest replacement (pack_format.h kMaxFileSize)
MAX_FILE = 2 << 30              # one texture file at most this big
MAX_INSTALL = 32 << 30          # a pack (zip or folder) at most this big unpacked
MAX_ENTRIES = 200_000

InstallError = mods.InstallError
PackExists = mods.ModExists


@dataclass
class Pack:
    folder: str
    name: str = ""
    version: str = ""
    author: str = ""
    description: str = ""
    files: int = 0           # texture files the game will use
    size: int = 0            # their bytes on disk
    gpu_bytes: int = 0       # about what they take in graphics memory, with mips
    png_files: int = 0       # of files, PNGs (RGBA8 in graphics memory: texture_compress.py makes DDS)
    png_gpu_bytes: int = 0   # their share of gpu_bytes
    problem: str = ""        # why the game would use nothing of it ("" when it is used)
    note: str = ""           # a warning that does not stop it being used
    enabled: bool = True
    names: list = field(default_factory=list)  # its fingerprints, lower case

    @property
    def title(self) -> str:
        if self.folder == LOOSE:
            return LOOSE_TITLE
        if self.name:
            return self.name
        return "HD pack (made on this PC)" if self.folder == HD_PACK else self.folder

    @property
    def loose(self) -> bool:
        """The loose files: always first, cannot be moved or removed."""
        return self.folder == LOOSE

    @property
    def made_here(self) -> bool:
        return self.folder == HD_PACK

    def summary(self) -> str:
        bits = [self.title]
        if self.version:
            bits.append(self.version)
        if self.author:
            bits.append(f"by {self.author}")
        return " ".join(bits)


def textures_dir(cfg) -> Path:
    from . import game
    return game.texture_pack_folder(cfg)


def fingerprint_of(name: str) -> str | None:
    """The fingerprint a file named <16 hex digits>.dds/.png replaces (lower case), else None."""
    stem, ext = os.path.splitext(name)
    if ext.lower() in EXTENSIONS and FINGERPRINT.match(stem):
        return stem.lower()
    return None


# --- reading texture files (the checks of the game's reader, SDK pack_format.cpp) --------------

_DDS_FOURCC = {b"DXT1": "BC1", b"DXT2": "BC2", b"DXT3": "BC2", b"DXT4": "BC3", b"DXT5": "BC3",
               b"ATI1": "BC4", b"BC4U": "BC4", b"ATI2": "BC5", b"BC5U": "BC5"}
_DXGI = {71: "BC1", 72: "BC1", 74: "BC2", 75: "BC2", 77: "BC3", 78: "BC3", 80: "BC4", 83: "BC5",
         98: "BC7", 99: "BC7", 28: "RGBA8", 29: "RGBA8", 87: "BGRA8", 91: "BGRA8", 61: "R8", 49: "RG8"}
_BLOCK_BYTES = {"BC1": 8, "BC4": 8, "BC2": 16, "BC3": 16, "BC5": 16, "BC7": 16}
_TEXEL_BYTES = {"RGBA8": 4, "BGRA8": 4, "R8": 1, "RG8": 2}


@dataclass
class TextureFile:
    format: str = ""        # BC1-5/7, RGBA8, BGRA8, R8, RG8, or PNG
    width: int = 0
    height: int = 0
    problem: str = ""       # why the game cannot use it ("" when it can)

    def gpu_bytes(self) -> int:
        """Graphics memory with a full mip chain: block-compressed files as they are, the rest
        as RGBA8 (as the game uploads them)."""
        if self.problem:
            return 0
        blocks = _BLOCK_BYTES.get(self.format)
        base = (((self.width + 3) // 4) * ((self.height + 3) // 4) * blocks if blocks
                else self.width * self.height * 4)
        return base * 4 // 3


def read_texture_header(path: Path) -> TextureFile:
    """The format and size of a .dds or .png file, and why the game would not use it."""
    try:
        with open(path, "rb") as f:
            head = f.read(148)
            size = os.fstat(f.fileno()).st_size
    except OSError as e:
        return TextureFile(problem=f"cannot be read ({e.strerror or e})")
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return _png(head)
    if head[:4] == b"DDS ":
        return _dds(head, size)
    return TextureFile(problem="neither a DDS nor a PNG file")


def _png(head: bytes) -> TextureFile:
    if len(head) < 33 or head[12:16] != b"IHDR":
        return TextureFile(format="PNG", problem="a damaged PNG file")
    width, height, depth, colour = struct.unpack(">IIBB", head[16:26])
    t = TextureFile(format="PNG", width=width, height=height)
    if depth not in (1, 2, 4, 8, 16) or colour not in (0, 2, 3, 4, 6):
        t.problem = "a damaged PNG file"
    elif not width or not height or width > MAX_SIZE or height > MAX_SIZE:
        t.problem = f"its size {width}x{height} is over {MAX_SIZE}"
    return t


def _dds(head: bytes, size: int) -> TextureFile:
    if len(head) < 128 or struct.unpack_from("<I", head, 4)[0] != 124:
        return TextureFile(format="DDS", problem="a damaged DDS file")
    flags, height, width = struct.unpack_from("<III", head, 8)
    depth = struct.unpack_from("<I", head, 24)[0]
    pixel_flags, four_cc, bits = struct.unpack_from("<I4sI", head, 80)
    masks = struct.unpack_from("<III", head, 92)
    caps2 = struct.unpack_from("<I", head, 112)[0]
    t = TextureFile(format="DDS", width=width, height=height)
    offset = 128
    if caps2 & 0x200:
        t.problem = "a cube map (not supported yet)"
    elif caps2 & 0x200000 or (flags & 0x800000 and depth > 1):
        t.problem = "a volume texture (not supported yet)"
    elif pixel_flags & 0x4:
        if four_cc in _DDS_FOURCC:
            t.format = _DDS_FOURCC[four_cc]
        elif four_cc == b"DX10":
            offset = 148
            if len(head) < 148:
                t.problem = "a damaged DDS file"
            else:
                dxgi, dimension, misc, array = struct.unpack_from("<IIII", head, 128)
                if dimension != 3 or array > 1 or misc & 0x4:
                    t.problem = "not a single 2D texture (arrays and cube maps are not supported)"
                elif dxgi not in _DXGI:
                    t.problem = f"DXGI format {dxgi}, which the game does not read"
                else:
                    t.format = _DXGI[dxgi]
        else:
            t.problem = f"DDS format {four_cc.decode('latin-1')!r}, which the game does not read"
    elif pixel_flags & 0x40 and bits == 32:
        if masks == (0xFF, 0xFF00, 0xFF0000):
            t.format = "RGBA8"
        elif masks == (0xFF0000, 0xFF00, 0xFF):
            t.format = "BGRA8"
        else:
            t.problem = "a 32-bit channel order the game does not read"
    elif pixel_flags & 0x20000 and bits == 8:
        t.format = "R8"
    elif pixel_flags & 0x20000 and pixel_flags & 0x1 and bits == 16:
        t.format = "RG8"
    else:
        t.problem = "a pixel format the game does not read (use BC1-5/7, RGBA8, BGRA8, R8 or RG8)"
    if not t.problem:
        if not width or not height or width > MAX_SIZE or height > MAX_SIZE:
            t.problem = f"its size {width}x{height} is over {MAX_SIZE}"
        else:
            blocks = _BLOCK_BYTES.get(t.format)
            base = (((width + 3) // 4) * ((height + 3) // 4) * blocks if blocks
                    else width * height * _TEXEL_BYTES[t.format])
            if size < offset + base:
                t.problem = "shorter than its header says"
    return t


# --- the packs -----------------------------------------------------------------------------------

def _valid_folder_name(name: str) -> str:
    """Why a folder cannot be a pack's name ("" when it can)."""
    if "|" in name:
        return "the folder's name has a |, which the game reads as a separator: rename it"
    if name.lower() == DUMP:
        return "the dump"
    if any(c in name for c in '\\/:*?"<>') or any(ord(c) < 0x20 for c in name):
        return "the folder's name has characters the game cannot use: rename it"
    return ""


def read_pack(folder: Path, check=True) -> Pack:
    """A pack folder: its manifest and its texture files, checked as the game will read them
    (check=False: only named, for the game's options, which every start works out)."""
    pack = Pack(folder=folder.name)
    pack.problem = _valid_folder_name(folder.name)
    try:
        _read_manifest(folder, pack)
        if not pack.problem:
            _read_files(folder, pack, check)
    except OSError as e:
        pack.problem = pack.problem or f"its folder could not be read ({e})"
    return pack


def _read_manifest(folder: Path, pack: Pack):
    manifest = folder / MANIFEST
    if not manifest.is_file():
        return
    try:
        info = tomllib.loads(manifest.read_text(encoding="utf-8-sig")) if tomllib else {}
    except (OSError, UnicodeDecodeError, ValueError) as e:
        pack.note = f"{MANIFEST} could not be read ({e}): the folder's name is shown instead"
        info = {}
    for key in ("name", "version", "author", "description"):
        value = info.get(key)
        if isinstance(value, (str, int, float)):
            setattr(pack, key, str(value).strip())


def _read_files(folder: Path, pack: Pack, check=True, top_only=False):
    """The files the game takes (SDK pack.cpp: <fingerprint>.dds/.png in the folder and the
    folders inside it, links to folders not followed; a .dds before a .png, then the first path).
    top_only: only the folder's own files (the loose files)."""
    chosen = {}       # fingerprint -> (is not dds, path, TextureFile)
    unusable, others, duplicates = [], 0, 0

    def onerror(e):
        raise e

    for dirpath, dirnames, filenames in os.walk(folder, onerror=onerror):
        dirnames[:] = [] if top_only else sorted(dirnames)
        for name in sorted(filenames):
            fp = fingerprint_of(name)
            if fp is None:
                if not top_only and not name.lower().endswith(DOC_EXTENSIONS):
                    others += 1
                continue
            p = Path(dirpath) / name
            t = read_texture_header(p) if check else TextureFile()
            rel = Path(os.path.relpath(p, folder)).as_posix()
            if t.problem:
                unusable.append(f"{rel}: {t.problem}")
                continue
            key = (not name.lower().endswith(".dds"), rel)
            if fp in chosen:
                duplicates += 1
                if key >= chosen[fp][0]:
                    continue
            chosen[fp] = (key, p, t)
    for fp, (key, p, t) in sorted(chosen.items()):
        pack.names.append(fp)
        pack.files += 1
        png = key[0] and p.suffix.lower() == ".png"
        pack.png_files += png
        if check:
            pack.size += p.stat().st_size
            pack.gpu_bytes += t.gpu_bytes()
            pack.png_gpu_bytes += t.gpu_bytes() if png else 0
    notes = []
    if unusable:
        notes.append(f"{len(unusable)} file(s) the game cannot use: " + "; ".join(unusable[:3]) +
                     ("; ..." if len(unusable) > 3 else ""))
    if duplicates:
        notes.append(f"{duplicates} texture(s) are in it twice: the .dds (or the first path) is used")
    if others:
        notes.append(f"{others} file(s) are not named by a fingerprint (16 hexadecimal digits, .dds or "
                     ".png) and are not used")
    if notes:
        pack.note = "\n".join(([pack.note] if pack.note else []) + notes)
    if not pack.files:
        pack.problem = ("no texture files the game can use: name each one after the texture it "
                        "replaces (<fingerprint>.dds or .png, as in the dump)")


def scan(directory: Path, check=True) -> list:
    """The pack folders in the textures folder, by name (not the dump, nor hidden folders), after
    the loose files when there are any."""
    try:
        folders = sorted((p for p in Path(directory).iterdir()
                          if not p.name.startswith(".") and p.name.lower() != DUMP and p.is_dir()),
                         key=lambda p: p.name.lower())
    except OSError:
        return []
    packs = [read_pack(p, check) for p in folders]
    if loose_files(directory):
        loose = Pack(folder=LOOSE, description="Texture files directly in the textures folder.")
        try:
            _read_files(Path(directory), loose, check, top_only=True)
        except OSError as e:
            loose.problem = f"the textures folder could not be read ({e})"
        packs.insert(0, loose)
    return packs


def loose_files(directory: Path) -> int:
    """Texture files directly in the textures folder (the loose files)."""
    try:
        return sum(1 for p in Path(directory).iterdir() if p.is_file() and fingerprint_of(p.name))
    except OSError:
        return 0


def ordered(cfg, found: list) -> list:
    """`found` in the saved order, each on unless turned off. New packs go last (by name),
    except the one made on this PC, which goes first: hand-made packs below it win over it."""
    saved = mods.state(cfg, STATE_KEY)["order"]
    out = mods.ordered(cfg, found, STATE_KEY)
    if HD_PACK not in saved:
        made = [p for p in out if p.folder == HD_PACK]
        out = made + [p for p in out if p.folder != HD_PACK]
    return [p for p in out if p.loose] + [p for p in out if not p.loose]


def remember(cfg, packs: list):
    mods.remember(cfg, packs, STATE_KEY)


def conflicts(packs: list) -> dict:
    """For each pack in use, the textures another pack in use has too: {folder: [(fingerprint,
    other folder, True when this one wins)]}. Later in the list wins, as in the game."""
    return mods.conflicts(packs)


def listed(cfg) -> list:
    return ordered(cfg, scan(textures_dir(cfg)))


def enabled(cfg, check=True) -> list:
    """The packs the game will use, in order."""
    return [p for p in ordered(cfg, scan(textures_dir(cfg), check)) if p.enabled and not p.problem]


def launch_args(cfg, named=()) -> list:
    """--texture_packs with the packs turned on, when Use texture packs is on and the folder is
    the launcher's (the player names neither option in the extra flags)."""
    if not cfg.effective_settings().get("texture_replace"):
        return []
    if "texture_packs" in named or "texture_pack_root" in named:
        return []
    return ["--texture_packs=" + ("|".join(p.folder for p in enabled(cfg, check=False)) or NONE)]


# --- adding and removing packs (the Texture packs page) ----------------------------------------

def _wanted(name: str) -> bool:
    """The files a pack is installed with: textures named by a fingerprint, its manifest and
    its read-me and licence files. Nothing else is copied."""
    return fingerprint_of(name) is not None or name.lower().endswith(DOC_EXTENSIONS)


def _has_textures(folder: Path) -> bool:
    for _dirpath, _dirnames, filenames in os.walk(folder):
        if any(fingerprint_of(n) for n in filenames):
            return True
    return False


def find_root(folder: Path) -> Path | None:
    """The pack in `folder`: itself when textures or pack.toml are at its top, else the one folder
    it holds (a zip's contents usually sit in one folder), as deep as that goes."""
    here = folder
    for _ in range(8):
        try:
            entries = list(here.iterdir())
        except OSError:
            return None
        if (here / MANIFEST).is_file() or any(e.is_file() and fingerprint_of(e.name) for e in entries):
            return here
        folders = [e for e in entries if e.is_dir()]
        if len(folders) != 1:
            break
        here = folders[0]
    return here if _has_textures(here) else None


DUMP_REFUSED = ("This is a texture dump (it has index.tsv), the game's own textures, not a pack: a "
                "pack holds textures someone made to replace them.")


def _install_name(name: str) -> str:
    if not name or name.startswith("."):
        raise InstallError(f'"{name}" cannot be a texture pack\'s folder name (not starting with a dot): '
                           'rename it and add it again')
    why = _valid_folder_name(name)
    if why == "the dump" or name == HD_PACK:
        return f"{name} (added)"  # the dump's and Make HD pack's folders stay theirs
    if why:
        raise InstallError(f'"{name}" cannot be a texture pack\'s folder name: {why}.')
    return name


def _copy_folder(root: Path, into: Path):
    """Copies the pack's wanted files from the folder `root` into `into` (links to folders not
    followed), within the size limits."""
    total, count = 0, 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not _wanted(name):
                continue
            src = Path(dirpath) / name
            size = src.stat().st_size
            total += size
            count += 1
            if size > MAX_FILE:
                raise InstallError(f'"{name}" is over 2 GB, more than a texture can be.')
            if total > MAX_INSTALL or count > MAX_ENTRIES:
                raise InstallError("The folder holds more than 32 GB or 200,000 textures, more than a "
                                   "texture pack can be.")
            dest = into / os.path.relpath(src, root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)


def _unpack(source: Path, into: Path) -> tuple:
    """Copies or unpacks the pack in `source` into the empty folder `into`; returns its root there
    and the folder name it gets (the folder's, or the zip's one folder's, else the zip's)."""
    if source.is_dir():
        root = find_root(source)
        if root is None:
            raise InstallError(f'"{source.name}" holds no texture pack: no .dds or .png files named '
                               'by a fingerprint.')
        if any("index.tsv" in files for _d, _s, files in os.walk(source)):
            raise InstallError(DUMP_REFUSED)
        name = _install_name(source.name)
        target = into / ".contents"
        target.mkdir()
        _copy_folder(root, target)
        return target, name
    if not zipfile.is_zipfile(source):
        raise InstallError(f'"{source.name}" is not a zip file or a folder.')
    unpacked = into / ".contents"
    unpacked.mkdir()
    try:
        with zipfile.ZipFile(source) as z:
            infos = mods._safe_members(z, "texture pack", MAX_INSTALL, MAX_ENTRIES, "32 GB")
            names = [i.filename.replace("\\", "/").rstrip("/") for i in infos]
            if any(n.rsplit("/", 1)[-1] == "index.tsv" for n in names):
                raise InstallError(DUMP_REFUSED)
            for info, member in zip(infos, names):
                if info.is_dir() or not _wanted(member.rsplit("/", 1)[-1]):
                    continue
                if info.file_size > MAX_FILE:
                    raise InstallError(f'"{info.filename}" is over 2 GB, more than a texture can be.')
                z.extract(info, unpacked)
    except NotImplementedError:
        raise InstallError("The zip uses a compression this launcher cannot unpack; repack it as a "
                           "normal zip.") from None
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, EOFError) as e:
        raise InstallError(f"The zip could not be read ({e}).") from None
    root = find_root(unpacked)
    if root is None:
        raise InstallError(f'"{source.name}" holds no texture pack: no .dds or .png files named by a '
                           'fingerprint.')
    top = [e for e in unpacked.iterdir()]
    name = top[0].name if len(top) == 1 and top[0].is_dir() else source.stem
    return root, _install_name(name)


def _same_as_dump(pack_root: Path, dump: Path) -> int:
    """Files of the pack that are byte for byte the game's own dumped textures."""
    same = 0
    for dirpath, _dirnames, filenames in os.walk(pack_root):
        for name in filenames:
            if fingerprint_of(name) is None:
                continue
            mine, theirs = Path(dirpath) / name, dump / name.lower()
            try:
                if theirs.is_file() and mine.stat().st_size == theirs.stat().st_size and \
                        mine.read_bytes() == theirs.read_bytes():
                    same += 1
            except OSError:
                pass
    return same


def install(source: Path, directory: Path, replace=False) -> Pack:
    """Adds the texture pack in `source` (a folder or a .zip) to the textures folder `directory`.
    Only textures named by a fingerprint and the pack's text files are copied, checked as the
    game reads them, and land in place only when all is well. Raises InstallError (with words
    for the player) or PackExists (call again with replace=True)."""
    source, directory = Path(source), Path(directory)
    if not source.exists():
        raise InstallError(f'"{source}" is not there any more.')
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if source.is_dir() and directory.resolve() in (source.resolve(), *source.resolve().parents):
            raise InstallError("That folder is already in the textures folder: it is listed on this "
                               "page (press Refresh).")
        staging = Path(tempfile.mkdtemp(prefix=".adding-", dir=directory))
    except OSError as e:
        raise InstallError(f"The textures folder could not be written ({e}).") from None
    try:
        try:
            root, name = _unpack(source, staging)
        except OSError as e:
            raise InstallError(f"The texture pack could not be copied ({e}).") from None
        pack = read_pack(root)
        if pack.problem:
            raise InstallError(f"This texture pack would not be used: {pack.problem}.")
        same = _same_as_dump(root, directory / DUMP)
        target = directory / name
        try:
            if target.exists() or target.is_symlink():
                if not replace:
                    raise PackExists(name)
                old = staging / ".old"
                target.rename(old)
                try:
                    root.rename(target)
                except OSError:
                    old.rename(target)  # put the pack that was there back
                    raise
            else:
                root.rename(target)
        except OSError as e:
            raise InstallError(f"The texture pack could not be put in the textures folder ({e}). Is "
                               "the game running?") from None
        pack = read_pack(target)
        if same:
            pack.note = "\n".join(filter(None, [pack.note, (
                f"{same} file(s) are the game's own textures unchanged (the same as in your dump): "
                "they change nothing, and a pack must never contain them")]))
        return pack
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove(cfg, folder: str):
    """Deletes a pack's folder from the textures folder (a linked folder: only the link) and
    forgets its place in the order."""
    directory = textures_dir(cfg)
    target = directory / folder
    if folder == LOOSE:
        raise InstallError("The loose files are not a pack folder: turn them off here, or move or delete "
                           "them in the textures folder (Open textures folder).")
    if target.parent != directory or folder in ("", ".", "..") or folder.lower() == DUMP:
        raise InstallError(f'"{folder}" is not a texture pack in the textures folder.')
    try:
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
    except OSError as e:
        raise InstallError(f"The texture pack could not be removed ({e}). Is the game running?") from None
    mods.forget(cfg, folder, STATE_KEY)
