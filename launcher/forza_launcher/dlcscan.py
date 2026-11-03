"""Finding DLC packages below a folder and inside .zip archives (issue #410).

scan_tree() walks a folder (bounded depth, links never followed, hidden and system folders left
out). stage_zips() unpacks only the entries of zips that look like STFS packages into a temporary
folder, refusing archives with unsafe entries and capping the size. Nothing here installs; the
caller scans the staged files like any others and removes the folder afterwards."""
import io
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path

from .fetch import Cancelled

MAX_DEPTH = 8                       # folders below the chosen one
MAX_UNPACKED = 8 * 2**30            # all packages taken out of zips together (bytes)
FREE_MARGIN = 256 * 2**20           # kept free on top of what an install needs
MAGICS = (b"CON ", b"LIVE", b"PIRS")
SYSTEM_NAMES = {"$recycle.bin", "system volume information", "__macosx", "lost+found", ".trash"}
_NAME_RE = re.compile(r"[0-9A-Fa-f]{8,42}")
_CHUNK = 1 << 20


class ZipRefused(Exception):
    """A zip that is not unpacked, with the reason in plain words."""


def _hidden(path: str, name: str) -> bool:
    if name.startswith(".") or name.lower() in SYSTEM_NAMES:
        return True
    try:
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x6)   # Windows: hidden, system
    except OSError:
        return False


def looks_like_package(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) in MAGICS
    except OSError:
        return False


def is_zip(path: str) -> bool:
    """A name ending in .zip (a damaged one is reported when it is opened)."""
    return path.lower().endswith(".zip")


def scan_tree(root: str, child, has_files, header_display_name, known_names, name_re=_NAME_RE):
    """(dirs, files, zips, skipped) below `root`. dirs: extracted package folders
    [(path, name, display name, header path or None)]; files: STFS-looking files; zips: .zip files;
    skipped: [(name, reason)]. `child`, `has_files`, `header_display_name`, `known_names` are
    tools/install_dlc.py's helpers (its layout rules stay in one place)."""
    dirs, files, zips, skipped = [], [], [], []

    def found_count():
        return len(dirs) + len(files) + len(zips)

    def walk(d, depth, rel):
        try:
            names = sorted(os.listdir(d))
        except OSError as e:
            skipped.append((rel or os.path.basename(d), f"cannot be read ({e.strerror or e})"))
            return
        hdir = child(os.path.dirname(d), "Headers")
        hdir = child(hdir, os.path.basename(d)) if hdir else None
        for n in names:
            p = os.path.join(d, n)
            shown = rel + n
            if os.path.islink(p):
                skipped.append((shown, "a link, not followed"))
            elif _hidden(p, n):
                if os.path.isdir(p):
                    skipped.append((shown, "hidden or system folder"))
            elif os.path.isfile(p):
                if is_zip(p):
                    zips.append(p)
                elif looks_like_package(p):
                    files.append(p)
            elif not os.path.isdir(p):
                continue
            elif name_re.fullmatch(n):
                if not has_files(p):
                    skipped.append((shown, "no files inside"))
                else:
                    h = os.path.join(hdir, n + ".header") if hdir else None
                    h = h if h and os.path.isfile(h) else None
                    dirs.append((p, n, (h and header_display_name(h)) or known_names.get(n.upper(), "DLC " + n), h))
            elif depth >= MAX_DEPTH:
                skipped.append((shown, f"more than {MAX_DEPTH} folders deep, not searched"))
            else:
                before = found_count()
                walk(p, depth + 1, shown + "/")
                if found_count() == before:
                    skipped.append((shown, "not a package name"))

    walk(root, 0, "")
    return dirs, files, zips, skipped


# --- zips -----------------------------------------------------------------------------------

def check_entry(info: zipfile.ZipInfo) -> None:
    """Raise ZipRefused for an entry that could write outside the folder or is a link."""
    name = info.filename
    bad = (not name or "\0" in name or name.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", name) is not None
           or ".." in name.replace("\\", "/").split("/"))
    if bad:
        raise ZipRefused(f"it has an unsafe path ({name!r})")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ZipRefused(f"it holds a link ({name!r})")


def _peek(z: zipfile.ZipFile, info) -> bytes:
    with z.open(info) as f:
        return f.read(4)


def zip_contents(z: zipfile.ZipFile):
    """([entries that look like packages], [nested zip entries]) after checking every entry."""
    pkgs, nested = [], []
    for info in z.infolist():
        check_entry(info)
        if info.is_dir():
            continue
        base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
        if base.startswith(".") or base.lower() in SYSTEM_NAMES:
            continue
        if base.lower().endswith(".zip"):
            nested.append(info)
        elif _peek(z, info) in MAGICS:
            pkgs.append(info)
    return pkgs, nested


def preview_zip(path: str):
    """([package file names], [(name, reason)] skipped) of a zip, nothing unpacked. A zip nested
    in it is opened when it is small; a big one is only noted."""
    names, skipped = [], []
    label = os.path.basename(path)
    try:
        with zipfile.ZipFile(path) as z:
            pkgs, nested = zip_contents(z)
            names += [i.filename.replace("\\", "/").rsplit("/", 1)[-1] for i in pkgs]
            for n in nested:
                if n.file_size > 64 * 2**20:
                    skipped.append((f"{label}: {n.filename}", "nested zip, checked when installing"))
                    continue
                try:
                    with z.open(n) as f, zipfile.ZipFile(io.BytesIO(f.read())) as inner:
                        ipk, _ = zip_contents(inner)
                        names += [i.filename.replace("\\", "/").rsplit("/", 1)[-1] for i in ipk]
                except (zipfile.BadZipFile, ZipRefused, OSError, RuntimeError, NotImplementedError):
                    skipped.append((f"{label}: {n.filename}", "nested zip that cannot be read"))
    except ZipRefused as e:
        return [], [(label, f"zip refused: {e}")]
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError) as e:
        return [], [(label, f"zip is damaged or unreadable ({e})")]
    if not names and not skipped:
        skipped.append((label, "zip holds no DLC packages"))
    return names, skipped


def _unpack(z, info, dest: str, left: list, cancelled, progress, done: list, total: int) -> None:
    """Stream one entry to `dest`, counting the bytes really written against the budget."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        with z.open(info) as src, open(dest, "wb") as out:
            while True:
                if cancelled():
                    raise Cancelled()
                buf = src.read(_CHUNK)
                if not buf:
                    break
                left[0] -= len(buf)
                if left[0] < 0:
                    raise ZipRefused(f"it unpacks to more than {MAX_UNPACKED // 2**30} GB")
                out.write(buf)
                done[0] += len(buf)
                progress(done[0], total, os.path.basename(dest))
    except BaseException:
        try:
            os.remove(dest)
        except OSError:
            pass
        raise


def stage_zips(zips, stage: Path, cancelled=lambda: False, progress=lambda *a: None, log=print):
    """Unpack the package entries of `zips` (and of zips inside them, one level) under `stage`.
    Returns (files, skipped): the staged package files and [(name, reason)]. A zip with an unsafe
    entry, damaged, or too big is left out whole, with its reason. Needs room for two copies
    (the staged one and the installed one) in the free space of `stage`'s disk."""
    stage.mkdir(parents=True, exist_ok=True)
    files, skipped = [], []
    left = [MAX_UNPACKED]
    counter = [0]
    done = [0]

    def one(zpath, opener, label, level):
        try:
            with zipfile.ZipFile(opener) as z:
                pkgs, nested = zip_contents(z)
                if level:
                    nested = []
                if not pkgs and not nested:
                    skipped.append((label, "zip holds no DLC packages"))
                    return
                need = sum(i.file_size for i in pkgs) + sum(i.file_size for i in nested)
                if need > left[0]:
                    raise ZipRefused(f"it unpacks to more than {MAX_UNPACKED // 2**30} GB")
                free = shutil.disk_usage(stage).free
                if free < need * 2 + FREE_MARGIN:
                    raise ZipRefused(f"not enough free disk space (needs about {(need * 2 + FREE_MARGIN) / 2**30:.1f} GB, "
                                     f"{free / 2**30:.1f} GB free)")
                total = max(need, 1)
                done[0] = 0
                for info in pkgs:
                    counter[0] += 1
                    base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
                    dest = os.path.join(stage, f"z{counter[0]}", base)
                    _unpack(z, info, dest, left, cancelled, progress, done, total)
                    files.append(dest)
                for info in nested:
                    counter[0] += 1
                    dest = os.path.join(stage, f"z{counter[0]}", "nested.zip")
                    _unpack(z, info, dest, left, cancelled, progress, done, total)
                    one(dest, dest, f"{label}: {info.filename}", 1)
                    os.remove(dest)
        except ZipRefused as e:
            skipped.append((label, f"zip not used: {e}"))
        except (zipfile.BadZipFile, EOFError, NotImplementedError, RuntimeError, OSError) as e:
            if isinstance(e, OSError) and not isinstance(e, zipfile.BadZipFile) and getattr(e, "errno", None) == 28:
                raise
            skipped.append((label, f"zip is damaged or unreadable ({e})"))

    for z in zips:
        log(f"unpacking packages from {os.path.basename(z)}")
        one(z, z, os.path.basename(z), 0)
    return files, skipped
