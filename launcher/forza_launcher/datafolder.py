"""The folder the game is built in and keeps its data in: where the default is, whether it can be
used (writable, free space, not synced by OneDrive), what "access denied" means and how the
player's saves are carried to another folder. Issues #38 and Discussion #31."""
import ctypes
import ntpath
import os
import re
import shutil
import sys
from pathlib import Path

from . import paths

GB = 1 << 30
IS_WINDOWS = sys.platform == "win32"

# Folders that can be built again; a move leaves them behind (and starts them fresh).
REBUILDABLE = ("toolchain", "downloads", "sdk", "build", "xwin")
# What marks a folder as one of ours (a chosen folder with none of these gets a subfolder).
MARKERS = ("game", "runtime", "build", "sdk", "toolchain", "downloads", "xwin")

ACCESS_DENIED_RE = re.compile(r"GetLastError\(\) = 5\b|Access is denied|Permission denied|WinError 5\b|"
                              r"PermissionError|EACCES", re.I)

# Windows file attributes of cloud-synced files and folders (OneDrive and other sync clients).
ATTR_OFFLINE = 0x1000
ATTR_RECALL_ON_OPEN = 0x40000
ATTR_PINNED = 0x80000
ATTR_UNPINNED = 0x100000
ATTR_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ATTRS = ATTR_OFFLINE | ATTR_RECALL_ON_OPEN | ATTR_PINNED | ATTR_UNPINNED | ATTR_RECALL_ON_DATA_ACCESS


# ---- where ----------------------------------------------------------------------------------

def legacy_data_home() -> Path:
    """Where v0.1.x kept everything on Windows."""
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Documents" / "forza"


def game_default_data_home() -> Path:
    """Where the game keeps saves, DLC and its shader cache when it is given no --user_data_root:
    the SDK's user folder, Documents (the known folder, which OneDrive may have moved) + forza."""
    if IS_WINDOWS:
        try:
            buf = ctypes.c_wchar_p()
            # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
            fid = (ctypes.c_byte * 16)(*bytes.fromhex("d09ad3fd8f23af46adb46c85480369c7"))
            if ctypes.windll.shell32.SHGetKnownFolderPath(fid, 0, None, ctypes.byref(buf)) == 0:
                docs = buf.value
                ctypes.windll.ole32.CoTaskMemFree(buf)
                if docs:
                    return Path(docs) / "forza"
        except (OSError, AttributeError, ValueError):
            pass
    return legacy_data_home()


def local_data_home() -> Path:
    """The Windows default: per user, not synced by OneDrive, not one of the folders Controlled
    folder access protects (Documents, Pictures, Desktop ...)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "ForzaReImagined"


def _has_content(p: Path) -> bool:
    try:
        return p.is_dir() and any(p.iterdir())
    except OSError:
        return p.is_dir()      # exists but cannot be listed: it is the player's, leave it alone


def windows_default() -> Path:
    """%LOCALAPPDATA%\\ForzaReImagined, except for an install that already lives in Documents\\forza
    (then that folder stays in use until the player moves, see offer_move)."""
    new, old = local_data_home(), legacy_data_home()
    if not new.exists() and _has_content(old):
        return old
    return new


def is_legacy_default(root: Path) -> bool:
    return IS_WINDOWS and _samepath(root, legacy_data_home())


# ---- cloud sync and access --------------------------------------------------------------------

def _norm(p) -> str:
    return ntpath.normcase(ntpath.normpath(str(p))).rstrip("\\")


def _samepath(a, b) -> bool:
    return _norm(a) == _norm(b)


def onedrive_roots() -> list:
    out = []
    for k, v in os.environ.items():
        if k.upper().startswith("ONEDRIVE") and v:
            out.append(v)
    return out


def _attrs(path) -> int | None:
    if not IS_WINDOWS:
        return None
    fn = ctypes.windll.kernel32.GetFileAttributesW
    fn.restype = ctypes.c_uint32      # the default (int) would make INVALID_FILE_ATTRIBUTES -1
    fn.argtypes = [ctypes.c_wchar_p]
    a = fn(str(path))
    return None if a == 0xFFFFFFFF else a


def in_onedrive(path, env_roots=None, attrs=_attrs) -> str | None:
    """Why `path` looks synced by OneDrive (or another cloud client), else None: it is under a
    OneDrive folder named by the environment, a folder on its way is called OneDrive..., or a
    folder on its way is a cloud placeholder (recall / offline / pinned attributes)."""
    roots = onedrive_roots() if env_roots is None else env_roots
    n = _norm(path)
    for r in roots:
        rn = _norm(r)
        if rn and (n == rn or n.startswith(rn + "\\")):
            return f"it is inside your OneDrive folder ({r})"
    parts = re.split(r"[\\/]+", str(path))
    for part in parts[1:]:
        if part.lower().startswith("onedrive"):
            return f"it is inside a folder named {part}"
    p = str(path)
    while p:
        a = attrs(p)
        if a is not None and a & CLOUD_ATTRS:
            return f"{p} is a cloud-synced folder (Windows marks it as OneDrive-managed)"
        parent = ntpath.dirname(p)
        if parent == p or not parent:
            break
        p = parent
    return None


def probe_writable(path: Path) -> str | None:
    """None when files can be created, written, read back and deleted in `path` (created if
    needed); else the error. This is what Controlled folder access and some antivirus programs
    deny."""
    probe = None
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        probe = Path(path) / ".forza-write-test"
        probe.write_bytes(b"x")
        if probe.read_bytes() != b"x":
            return "a file written there reads back different"
        probe.unlink()
        return None
    except OSError as e:
        if probe is not None:
            try:
                probe.unlink()
            except OSError:
                pass
        return f"{type(e).__name__}: {e}"


def read_problem(root, max_entries=30000, max_reads=300) -> str | None:
    """After a failed command: the first folder or file in what the build uses (sdk, toolchain, xwin,
    build, not the 7 GB of game files) that cannot be listed or read, else None. A denied folder often
    shows up as a program's "not found". Bounded: folders are only listed, a few hundred files are
    opened."""
    root = Path(root)
    bad = []
    seen = reads = 0
    for top in ("sdk", "toolchain", "xwin", "build"):
        if not (root / top).exists():
            continue
        for dirpath, dirs, files in os.walk(root / top, onerror=lambda e: bad.append(f"{e.filename}: {e.strerror}")):
            seen += len(dirs) + len(files)
            if bad:
                return bad[0]
            for f in files:
                if reads >= max_reads:
                    break
                reads += 1
                p = os.path.join(dirpath, f)
                try:
                    with open(p, "rb") as fh:
                        fh.read(1)
                except PermissionError as e:
                    return f"{p}: {e.strerror}"
                except OSError:
                    pass
            if seen > max_entries:
                break
    return bad[0] if bad else None


def suggested_folder() -> Path:
    return local_data_home() if IS_WINDOWS else paths.data_home()


def _other_than(path) -> str:
    """A folder to suggest instead of `path`."""
    s = suggested_folder()
    return str(s) if _norm(s) != _norm(path) else (r"C:\ForzaReImagined" if IS_WINDOWS else str(Path.home() / "forza-data"))


def blocked_message(path, detail: str = "") -> str:
    why = in_onedrive(path)
    msg = (f"Windows would not let the launcher use the folder {path}. It is blocked by something on this "
           "computer: OneDrive (it syncs Documents, Desktop and Pictures), antivirus software, or Windows "
           "Controlled folder access (Windows Security > Virus & threat protection > Ransomware protection). "
           + (f"This folder looks synced because {why}. " if why else "")
           + f"Choose another folder in Setup (\"Where the game is built\"), for example {_other_than(path)}, "
           "then press Build again.")
    if detail:
        msg += f"\n(Windows said: {detail.strip()[:300]})"
    return msg


def is_access_denied_error(e: BaseException) -> bool:
    return isinstance(e, PermissionError) or (isinstance(e, OSError) and
                                              (getattr(e, "winerror", None) == 5 or e.errno == 13))


# ---- is a folder fit to build in -----------------------------------------------------------------

def need_gb(game_files_ready: bool, need_toolchain: bool = True) -> int:
    from . import build_env
    return build_env.NEED_DISK_GB + (build_env.NEED_DISK_TOOLCHAIN_GB if need_toolchain else 0) - (8 if game_files_ready else 0)


def check_folder(path, game_files_ready=False, extra_gb=0.0, need_toolchain=True, writable=True):
    """[(level, text)] ('error' | 'warn' | 'ok') for building in `path`."""
    from . import build_env
    path = Path(path)
    out = []
    if writable:
        err = probe_writable(path)
        if err:
            out.append(("error", f"Cannot write to {path}: {err}. {blocked_message(path)}"))
            return out
    why = in_onedrive(path)
    if why:
        out.append(("warn", f"{path}: {why}. OneDrive would upload the game's files (tens of GB), and it or Windows "
                            "may block the build. A folder outside OneDrive, such as "
                            f"{suggested_folder()}, is safer."))
    need = need_gb(game_files_ready, need_toolchain) + extra_gb
    free = build_env.free_disk(path) / GB
    out.append(("ok" if free >= need else "error",
                f"Disk: {free:.0f} GB free where the game is built, about {need:.0f} GB needed"))
    return out


def normalize_choice(path) -> Path:
    """The folder a player picked, or a subfolder of it: a folder that is not empty and is not one of
    ours gets a ForzaReImagined folder inside, so the build does not spread over the player's files."""
    p = Path(path)
    try:
        entries = os.listdir(p)
    except OSError:
        return p
    if not entries or any(m in entries for m in MARKERS):
        return p
    return p / "ForzaReImagined"


# ---- moving ------------------------------------------------------------------------------------------

def _files_under(top: Path, rel_to: Path, skip_top=()):
    out = []
    if not top.is_dir():
        return out
    for dirpath, dirs, files in os.walk(top):
        if Path(dirpath) == top:
            dirs[:] = [d for d in dirs if d not in skip_top]
        for f in files:
            out.append((os.path.join(dirpath, f), os.path.relpath(os.path.join(dirpath, f), rel_to)))
    return out


def user_data_files(old: Path, with_user: bool = True, with_game: bool = False):
    """[(source, relative)] of what is worth carrying to another folder: saves, profiles, DLC, the
    shader cache, the log and the built game (runtime/) with the build's state file (`with_user`), and
    the extracted game files (`with_game`). The downloaded toolchain, SDK and build tree are made again
    (that is also where a blocked folder fails)."""
    old = Path(old)
    out = []
    if with_user:
        out += _files_under(old, old, set(REBUILDABLE) | {"game"})
        state = old / "build" / "state.json"
        if state.is_file():
            out.append((str(state), os.path.join("build", "state.json")))
    if with_game:
        out += _files_under(old / "game", old)
    return out


def carry_game_written_data(old: Path, new: Path, profile: str, log=None) -> int:
    """v0.1.4-v0.1.5 on Windows started the game without --user_data_root, so it kept its saves,
    DLC and shader cache in Documents\\forza (`old`) while the launcher's folder was `new`. Once,
    when `new` has no profile folder and `old` has one, copy what the game wrote there (not the
    launcher's own folders) into `new`. Never moves, deletes or overwrites the player's files.

    The profile (the saves) is copied into a temporary folder and renamed into place only when every
    file is there, so a copy that fails half way leaves no partial profile behind (which would close
    the gate and start the game on an empty save). Raises OSError then: the caller should start the
    game on `old`, where the saves still are. Other files (DLC, shader cache) are best effort.
    Returns bytes copied."""
    old, new = Path(old), Path(new)
    if _samepath(old, new) or (new / profile).exists() or not (old / profile).is_dir():
        return 0
    files = _files_under(old, old, set(REBUILDABLE) | set(MARKERS))
    in_profile = lambda rel: Path(rel).parts[0] == profile
    done = 0
    for src, rel in files:
        if in_profile(rel):
            continue
        dst = new / rel
        try:
            if not dst.exists():             # never replace a file the launcher or game put there
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                done += os.path.getsize(src)
        except OSError as e:
            if log:
                log(f"could not copy {src}: {e}")
    tmp = new / (profile + ".carrying")       # ours alone: a leftover of a copy that failed
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        n = 0
        for src, rel in files:
            if in_profile(rel):
                dst = tmp / Path(rel).relative_to(profile)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                done += os.path.getsize(src)
                n += 1
        tmp.mkdir(parents=True, exist_ok=True)
        os.rename(tmp, new / profile)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if log:
        log(f"copied the game's saves ({n} files), DLC and shader cache from {old} to {new}; "
            f"the old folder is untouched")
    return done


DLC_DIR = os.path.join("0000000000000000", "4D5309C9")   # under a user data root: 00000002 + Headers


def dlc_packages(root) -> set:
    """Names of the DLC packages the game finds under the user data root `root` (a link or junction
    on the way is followed, as the game follows it)."""
    d = Path(root) / DLC_DIR / "00000002"
    try:
        return {p.name for p in d.iterdir() if p.is_dir() and not p.name.endswith(".part")}
    except OSError:
        return set()


def dlc_offers(root) -> set:
    """Offer ids of the DLC packages under `root` (each package's Media/<offer>.puboffer; 1600 is the
    Rally Expansion Pack, 2700 the 1000 Club)."""
    d = Path(root) / DLC_DIR / "00000002"
    out = set()
    for name in dlc_packages(root):
        try:
            media = [p for p in (d / name).iterdir() if p.name.lower() == "media" and p.is_dir()]
            for m in media:
                out |= {p.name[:-len(".puboffer")] for p in m.iterdir() if p.name.lower().endswith(".puboffer")}
        except OSError:
            pass
    return out


def dlc_without_header(root) -> list:
    """Packages under `root` that have no Headers/00000002/<name>.header (the game may not count them)."""
    h = Path(root) / DLC_DIR / "Headers" / "00000002"
    return sorted(n for n in dlc_packages(root) if not (h / (n + ".header")).is_file())


def carry_dlc(old: Path, new: Path, log=None) -> int:
    """Copies the DLC packages (and their headers) of the user data root `old` that `new` does not have.
    Never overwrites or deletes; a package is copied into a .part folder and renamed into place, so a copy
    that fails half way leaves nothing the game would mount. Returns the number of packages copied."""
    old, new = Path(old), Path(new)
    if _samepath(old, new):
        return 0
    have = dlc_packages(new)
    todo = sorted(dlc_packages(old) - have)
    n = 0
    for name in todo:
        src = paths.long_path(old / DLC_DIR / "00000002" / name)  # deep car packs (#586)
        dst = paths.long_path(new / DLC_DIR / "00000002" / name)
        part = dst.with_name(name + ".part")
        try:
            shutil.rmtree(part, ignore_errors=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, part)
            os.rename(part, dst)
            n += 1
        except OSError as e:
            shutil.rmtree(part, ignore_errors=True)
            if log:
                log(f"could not copy the DLC package {src}: {e}")
            continue
    hsrc, hdst = old / DLC_DIR / "Headers" / "00000002", new / DLC_DIR / "Headers" / "00000002"
    for name in dlc_packages(new):
        s, d = hsrc / (name + ".header"), hdst / (name + ".header")
        try:
            if s.is_file() and not d.exists():
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
        except OSError as e:
            if log:
                log(f"could not copy the DLC header {s}: {e}")
    if n and log:
        log(f"copied {n} DLC package(s) from {old} to {new}; the old folder is untouched")
    return n


def _size(files) -> int:
    total = 0
    for src, _ in files:
        try:
            total += os.path.getsize(src)
        except OSError:
            pass
    return total


def estimate_move(old: Path, with_user: bool = True, with_game: bool = False) -> int:
    return _size(user_data_files(old, with_user, with_game))


def copy_user_data(old: Path, new: Path, with_user=True, with_game=False, progress=None, cancelled=lambda: False,
                   log=None) -> int:
    """Copies (never moves or deletes: the old folder stays as it is) the user data of `old` into
    `new`. Files already there with the same size are skipped, so a repeat resumes. Returns bytes."""
    files = user_data_files(old, with_user, with_game)
    total = sum(os.path.getsize(s) for s, _ in files if os.path.exists(s)) or 1
    done = 0
    for src, rel in files:
        if cancelled():
            raise InterruptedError("cancelled")
        dst = Path(new) / rel
        size = os.path.getsize(src)
        if not (dst.is_file() and dst.stat().st_size == size):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        done += size
        if progress:
            progress(done / total, rel)
    if log:
        log(f"copied {len(files)} files ({done / GB:.1f} GB) from {old} to {new}; the old folder is untouched")
    return done
