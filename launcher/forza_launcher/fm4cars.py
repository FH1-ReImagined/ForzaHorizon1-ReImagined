"""Import Forza Motorsport 4's cars into FH1 from the player's own FM4 discs (#648).

The launcher reads FM4's Disc 1 (and optionally the Disc 2 car packs), converts every FM4 car
FH1 lacks with tools/fm4_cars.py and publishes the result: the cars' files as a DLC-style
package in the user data folder (the mod overlay holds 4 GB at most, a full import is more),
their database rows and names as one generated mod ("FM4 cars" on the Mods page). Unticking
the mod hides the cars; remove() deletes both.
Nothing is downloaded and nothing leaves this PC. docs/modding/fm4-cars.md has the details.
"""
import copy
import errno
import json
import os
import re
import shutil
import struct
import tempfile
import time
from pathlib import Path

from . import datafolder, mods, paths
from .fetch import Cancelled

MOD = "reimagined-fm4-cars"
PACKAGE = "464D3443415253000000000000000000000000004D"  # tools/fm4_cars.py PACK_NAME
MARKER = ".reimagined-fm4-cars.json"
FM4_TITLE = 0x4D530910
DISC1_PATHS = ("default.xex", "Media/cars", "Media/wheels", "Media/db", "Media/StringTables",
               "Media/UI/Textures.zip", "Media/audio/cars")


class Fm4Error(ValueError):
    pass


def _tools():
    paths.ensure_tools_importable()
    import fm4_cars
    return fm4_cars


def _package(cfg):
    """(the package folder, its header) in the user data folder the game reads DLC from."""
    root = Path(cfg.data_home()) / datafolder.DLC_DIR
    return root / "00000002" / PACKAGE, root / "Headers" / "00000002" / (PACKAGE + ".header")


def _dlc(cfg):
    return sorted(p for p in datafolder.dlc_packages(cfg.data_home()) if p != PACKAGE)


def status(cfg):
    """What the installed import holds ({"cars": [...], "dlc": [...]}), or None."""
    marker = mods.mods_dir(cfg) / MOD / MARKER
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def stale(cfg):
    """True when DLC packages were added or removed since the import: the import's car names
    table holds the DLC's car names of that time, so it should be made again."""
    info = status(cfg)
    if not info:
        return False
    return _dlc(cfg) != sorted(info.get("dlc", []))


SOUND_NOTICE = ("Some imported FM4 DLC cars (such as the 1986 Audi Sport quattro S1) had no engine sound bank, "
                "and the game stopped with a disc read error when one was bought or was your current car. "
                "They now use the sound of a similar FH1 engine, and your save starts again. To give them "
                "their own engine sound, import FM4 cars again (Mods > Forza Motorsport 4 cars).")


def repair_sound(cfg, log=print):
    """Once for an import made before #776 (its marker has no "banks"): that import left out the
    engine sound banks FH1 lacks, and a save whose current car was one of those cars stopped at
    every start with the game's disc read error. Its harmonic tuning files that name a missing
    bank get FH1's most similar one in the mod (which the game reads before the package), so the
    cars drive with a similar engine's sound until the next import, which brings FM4's banks.
    Returns a notice for the player when it changed something, else None."""
    info = status(cfg)
    if not info or info.get("banks"):
        return None
    folder = mods.mods_dir(cfg) / MOD
    media = cfg.game_files() / "media"
    if not media.is_dir():
        return None  # no game files to check against: next time
    try:
        tools = _tools()
        package, _ = _package(cfg)
        fixes = tools.sound_fallbacks(str(media), str(package) if package.is_dir() else None, str(folder))
        if fixes is None:
            return None
        ht_dir = folder / "media" / "audio" / "cars" / "engines" / "harmonictuning"
        for name, (_, data) in fixes.items():
            ht_dir.mkdir(parents=True, exist_ok=True)
            (ht_dir / name).write_bytes(data)
        info["banks"] = True
        if fixes:
            info["sound_fallback"] = {name: fh1 for name, (fh1, _) in sorted(fixes.items())}
        (folder / MARKER).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # never in the way of starting the game
        log(f"FM4 cars: could not check the engine sound banks ({e})")
        return None
    if not fixes:
        return None
    for name, (fh1, _) in sorted(fixes.items()):
        log(f"FM4 cars: {name} names an engine sound bank no file has; FH1's {fh1} in its place (#776)")
    return SOUND_NOTICE


# The engine tunings an import before #840 took from FH1, a cut traffic car's (the Traffic_/NA_
# placeholder slots tools/fm4_cars.py traffic_tuning finds): FM4's own are full ones. The Volt's
# is NA in FM4 too (electric) and stays.
TRAFFIC_TUNINGS = ("AUD_2_A4_11_ET.xml", "CHE_Spark_11_ET.xml", "FOR_FocusST_06_ET.xml", "SCI_xD_09_ET.xml")
TUNING_NOTICE = ("Some imported FM4 cars (such as the 2006 Ford Focus ST) have a cut-down traffic car's "
                 "engine sound, the same with every exhaust. Import FM4 cars again (Mods > Forza Motorsport "
                 "4 cars) to give them FM4's own.")


def traffic_tuned(cfg):
    """The engine tunings (TRAFFIC_TUNINGS) an import made before #840 holds: its marker has no
    "engine_tuning". Only a new import replaces them (they come from FM4's discs)."""
    info = status(cfg)
    if not info or info.get("engine_tuning", 1) >= 2:
        return []
    folder = mods.mods_dir(cfg) / MOD / "media" / "audio" / "cars" / "engines" / "enginetuning"
    try:
        have = {p.name.lower() for p in folder.iterdir()}
    except OSError:
        return []
    return [n for n in TRAFFIC_TUNINGS if n.lower() in have]


def tuning_notice(cfg, log=print):
    """Once for an import made before #840 that has a traffic car's engine tuning (traffic_tuned):
    a notice for the player, else None. The Mods page says it until the next import."""
    info = status(cfg)
    if not info or info.get("tuning_notice") or not traffic_tuned(cfg):
        return None
    try:
        info["tuning_notice"] = True
        (mods.mods_dir(cfg) / MOD / MARKER).write_text(json.dumps(info, ensure_ascii=False, indent=1),
                                                     encoding="utf-8")
    except OSError as e:
        log(f"FM4 cars: could not note the engine tuning notice ({e})")
        return None
    return TUNING_NOTICE


# ---- What the player chose (#681) --------------------------------------------------------------
# Disc 1 can be a disc image (whole, trimmed or split into parts), a Games on Demand or hard drive
# install package (its data parts hold the same XDVDFS volume as the disc), a folder with the
# disc's files, or an archive of any of these; Disc 2 also its car packs themselves. A folder is
# searched (the parent of the disc's folder, an Xbox 360 or Xenia content folder). Anything else
# is named in plain words: another game's package, a title update, a lone XEX.

TITLES = {0x4D5309C9: "Forza Horizon", FM4_TITLE: "Forza Motorsport 4", 0x4D53084D: "Forza Motorsport 3",
          0x4D530AA4: "Forza Horizon 2"}
# Xbox 360 content package types (the header's content type)
CONTENT_TYPES = {0x1: "saved game", 0x2: "content package (DLC)", 0x4000: "hard drive install",
                 0x7000: "Games on Demand package", 0x9000: "avatar item", 0x10000: "profile",
                 0x20000: "gamer picture", 0x30000: "theme", 0x80000: "demo",
                 0xB0000: "title update", 0xD0000: "Xbox Live Arcade game"}
CHOOSE = {1: "Choose your FM4 Disc 1 (the play disc): its disc image (.iso), its Games on Demand "
             "package, a folder with its files, or an archive (.zip, .7z, .rar) holding one of them.",
          2: "Choose your FM4 Disc 2 (the content install disc): its disc image (.iso), its Games on "
             "Demand package, a folder with its files or its car packs, an archive holding one of "
             "them, or Skip."}
DISC2_AS_DISC1 = ("This is Forza Motorsport 4's Disc 2 (the content install disc), not Disc 1. Choose "
                  "Disc 1 (the play disc) first: Disc 2 comes at the next step.")
PACK_MIN = 1 << 20  # Disc 2's car packs are hundreds of MB; smaller FM4 packages are offers and such
SCAN_DEPTH, SCAN_DIRS = 8, 5000  # how far a chosen folder is searched
IMAGE_NAME = re.compile(r"(?i)\.(iso|xiso|img|bin)(\.(part)?\d{1,3})?$|\.\d{3}$|\.part\d{1,3}(\.(iso|xiso|img|bin))?$")
SKIP_DIRS = {"$systemupdate"}  # a disc's console update: nothing to find there


def _game_thing(title, thing):
    """'a Forza Horizon content package (DLC)', or the same of another game with its title id."""
    name = TITLES.get(title)
    if name:
        return f"a {name} {thing}"
    article = "an" if thing[:1].lower() in "aeiox" else "a"
    return f"{article} {thing} of another game (title id {title:08X})"


def package_header(path):
    """(content type, title id, volume type, display name) of an Xbox 360 content package
    (STFS or Games on Demand: CON, LIVE or PIRS), or None when the file is not one."""
    try:
        with open(path, "rb") as f:
            raw = f.read(0x511)
    except OSError:
        return None
    if len(raw) < 0x511 or raw[:4] not in (b"CON ", b"LIVE", b"PIRS"):
        return None
    content_type, = struct.unpack_from(">I", raw, 0x344)
    title, = struct.unpack_from(">I", raw, 0x360)
    volume, = struct.unpack_from(">I", raw, 0x3A9)
    name = raw[0x411:0x511].decode("utf-16-be", "replace").split("\0")[0].strip()
    return content_type, title, volume, name


def _xex_title(path):
    from .xexcheck import header_info
    try:
        with open(path, "rb") as f:
            info = header_info(f.read(0x10000))
    except OSError:
        return None
    return info[3] if info else None


def describe(path: Path, disc=1):
    """Why a file is not FM4's Disc `disc`, in plain words: what it is and what to choose."""
    hdr = package_header(path)
    if hdr:
        content_type, title, _, name = hdr
        named = f' ("{name}")' if name else ""
        if title == FM4_TITLE and content_type == 0x2 and disc == 1:
            return (f"{path.name} is one of Forza Motorsport 4's Disc 2 car packs{named}, not Disc 1. "
                    "Choose Disc 1 first: the car packs come at the next step (Disc 2).")
        if title == FM4_TITLE and content_type == 0xB0000:
            return (f"{path.name} is a Forza Motorsport 4 title update{named}, not a disc: the import "
                    "does not need it (the cars are on the discs). " + CHOOSE[disc])
        if title == FM4_TITLE and content_type == 0x80000:
            return (f"{path.name} is the Forza Motorsport 4 demo{named}, which has only a few of the "
                    "game's cars and not their database. " + CHOOSE[disc])
        thing = CONTENT_TYPES.get(content_type, f"content package (type {content_type:X})")
        return f"{path.name} is {_game_thing(title, thing)}{named}, not a Forza Motorsport 4 disc. " + CHOOSE[disc]
    title = _xex_title(path)
    if title is not None:
        what = _game_thing(title, "program file (XEX)")
        return f"{path.name} is {what}, not a Forza Motorsport 4 disc. " + CHOOSE[disc]
    hint = ""
    m = re.fullmatch(r"([0-9A-Fa-f]{8})[0-9A-Fa-f]{8}", path.stem)
    if m and int(m.group(1), 16) in TITLES:
        hint = (f" Its name is the kind a {TITLES[int(m.group(1), 16)]} content package has "
                f"({m.group(1).upper()} is that game's title id): renaming a package to .iso does not "
                "make it a disc image.")
    return (f"{path.name} is not an Xbox 360 disc image, Games on Demand package, content package or "
            f"archive.{hint} " + CHOOSE[disc])


# ---- Archives -----------------------------------------------------------------------------------
# .zip and .tar are read with the standard library; .7z and .rar (and split or unusual zips) with
# 7-Zip, unrar, unar or bsdtar when one is installed. The archive is unpacked into the import's
# temporary folder and what it holds is then taken like a chosen folder.

ARCHIVE_MAGIC = ((b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"), (b"PK\x07\x08", "zip"),
                 (b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!\x1a\x07", "rar"))
ARCHIVE_NAMES = {"zip": "zip", "7z": "7z", "rar": "RAR", "tar": "tar"}
ARCHIVE_TOOLS = {"7z": ("7zz", "7z", "7za", "7zr", "bsdtar"),
                 "rar": ("7zz", "7z", "unrar", "unar", "bsdtar"),
                 "zip": ("7zz", "7z", "7za", "bsdtar", "unar"),
                 "tar": ("7zz", "7z", "7za", "bsdtar")}


def _first_volume(p: Path):
    """(the first volume of a split archive p is part of, True), or (p, False)."""
    name = p.name
    m = re.fullmatch(r"(?i)(.+\.(?:7z|zip|rar))\.(\d{3})", name)
    if m:
        return p.with_name(m.group(1) + ".001"), True
    m = re.fullmatch(r"(?i)(.+\.part)(\d+)(\.rar)", name)
    if m:
        return p.with_name(m.group(1) + "1".zfill(len(m.group(2))) + m.group(3)), True
    m = re.fullmatch(r"(?i)(.+)\.z\d\d", name)
    if m:
        return p.with_name(m.group(1) + ".zip"), True
    if p.suffix.lower() == ".zip" and any(p.with_suffix(f".z{k:02d}").exists() for k in (1,)):
        return p, True
    return p, False


def archive_kind(p: Path):
    """'zip', '7z', 'rar' or 'tar' when the file is an archive (by its first bytes), else None."""
    try:
        with open(p, "rb") as f:
            head = f.read(265)
    except OSError:
        return None
    for magic, kind in ARCHIVE_MAGIC:
        if head.startswith(magic):
            return kind
    if head[257:262] == b"ustar":
        return "tar"
    if re.search(r"(?i)\.(tar\.(gz|bz2|xz)|tgz|tbz2?|txz)$", p.name):
        return "tar"
    return None


def _gb(n):
    return f"{n / (1 << 30):.1f} GB"


def _need_space(out: Path, size, name):
    try:
        free = shutil.disk_usage(out).free
    except OSError:
        return
    if size + (256 << 20) > free:
        raise Fm4Error(f"Unpacking {name} needs {_gb(size)} of free space on the drive of the mods "
                       f"folder, and it has {_gb(free)}. Free some space, or extract the archive "
                       "yourself and choose what it holds.")


def _copy_stream(src, dest, label, progress, cancelled, done, total):
    with open(dest, "wb") as out:
        while True:
            if cancelled():
                raise Cancelled()
            chunk = src.read(1 << 22)
            if not chunk:
                return done
            out.write(chunk)
            done += len(chunk)
            progress(done, total, label)


def _safe_dest(out: Path, name):
    import xcontent_extract
    try:
        return Path(xcontent_extract.extraction_path(str(out), name.replace("\\", "/").strip("/")))
    except SystemExit:
        raise Fm4Error(f"The archive holds a file with an unsafe name ({name!r}): extract it yourself "
                       "and choose what it holds.") from None


def _unpack_zip(p: Path, out: Path, progress, cancelled):
    import zipfile
    with zipfile.ZipFile(p) as z:
        members = [i for i in z.infolist() if not i.is_dir()]
        total = sum(i.file_size for i in members)
        _need_space(out, total, p.name)
        done = 0
        for i in members:
            dest = _safe_dest(out, i.filename)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(i) as src:
                done = _copy_stream(src, dest, f"Unpacking {p.name}: {i.filename}", progress,
                                    cancelled, done, total)


def _unpack_tar(p: Path, out: Path, progress, cancelled):
    import tarfile
    with tarfile.open(p) as t:
        members = [m for m in t.getmembers() if m.isfile()]  # no links or devices
        total = sum(m.size for m in members)
        _need_space(out, total, p.name)
        done = 0
        for m in members:
            dest = _safe_dest(out, m.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with t.extractfile(m) as src:
                done = _copy_stream(src, dest, f"Unpacking {p.name}: {m.name}", progress,
                                    cancelled, done, total)


def _tool_paths(kind):
    import os
    import sys
    found = []
    for name in ARCHIVE_TOOLS[kind]:
        exe = shutil.which(name)
        if exe and exe not in found:
            found.append(exe)
    if sys.platform == "win32":
        for env in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if os.environ.get(env):
                exe = Path(os.environ[env]) / "7-Zip" / "7z.exe"
                if exe.is_file() and str(exe) not in found:
                    found.insert(0, str(exe))
        tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
        if tar.is_file():  # Windows' own tar is bsdtar: it reads zip, 7z and rar
            found.append(str(tar))
    return found


def _tool_command(exe, p: Path, out: Path):
    stem = Path(exe).name.lower().removesuffix(".exe")
    if stem in ("7zz", "7z", "7za", "7zr"):
        return [exe, "x", "-y", "-bso0", "-bsp1", f"-o{out}", str(p)]
    if stem == "unrar":  # -ol-: links are not made (unsafe_entry refuses them anyway)
        return [exe, "x", "-y", "-o+", "-idq", "-ol-", str(p), str(out) + "/"]
    if stem == "unar":
        return [exe, "-q", "-f", "-o", str(out), str(p)]
    # bsdtar, Windows' tar.exe: refuses absolute and .. paths by default (no -P)
    return [exe, "-x", "--no-same-owner", "--no-same-permissions", "-f", str(p), "-C", str(out)]


def unsafe_entry(out: Path):
    """(path, why) of the first thing an extractor made that is not a plain file or folder
    inside out (a link, a hard link, a device or pipe, or a path leading outside), or None.
    The extractors are trusted to refuse absolute and .. names; this checks what they did."""
    import os
    import stat
    root = os.path.realpath(out)
    for folder, dirs, files in os.walk(out, followlinks=False):
        for name in dirs + files:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, out)
            try:
                st = os.lstat(path)
            except OSError as e:
                return rel, str(e)
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0):
                return rel, "a link"
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                return rel, "a device or pipe"
            if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                return rel, "a hard link"
            real = os.path.realpath(path)
            try:
                inside = os.path.normcase(os.path.commonpath((root, real))) == os.path.normcase(root)
            except ValueError:
                inside = False
            if not inside:
                return rel, "outside the folder it was unpacked to"
    return None


def _tool_size(exe, p: Path):
    """The unpacked size 7-Zip lists for the archive, or None."""
    import subprocess
    if Path(exe).name.lower().removesuffix(".exe") not in ("7zz", "7z", "7za", "7zr"):
        return None
    try:
        r = subprocess.run([exe, "l", "-slt", "-ba", str(p)], capture_output=True, text=True, timeout=120,
                           creationflags=0x08000000 if paths.IS_WINDOWS else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    sizes = [int(m) for m in re.findall(r"(?m)^Size = (\d+)$", r.stdout)]
    return sum(sizes) if r.returncode == 0 and sizes else None


def _run_tool(exe, p: Path, out: Path, progress, cancelled):
    """Runs one extractor; returns None when it worked, else its last error line."""
    import subprocess
    import threading
    proc = subprocess.Popen(_tool_command(exe, p, out), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=0x08000000 if paths.IS_WINDOWS else 0)
    state = {"pct": 0, "err": b""}

    def read_out():
        for chunk in iter(lambda: proc.stdout.read1(4096), b""):
            m = re.findall(rb"(\d{1,3})%", chunk)
            if m:
                state["pct"] = min(100, int(m[-1]))

    def read_err():
        state["err"] = proc.stderr.read()
    threads = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    for t in threads:
        t.start()
    try:
        while True:
            try:
                proc.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancelled():
                proc.kill()
                proc.wait()
                raise Cancelled()
            progress(state["pct"], 100, f"Unpacking {p.name}")
    finally:
        for t in threads:
            t.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode == 0:
        return None if any(out.iterdir()) else "it unpacked nothing"
    lines = [ln.strip() for ln in state["err"].decode("utf-8", "replace").splitlines() if ln.strip()]
    return lines[-1] if lines else f"exit code {proc.returncode}"


def unpack_archive(p: Path, kind, split, out: Path, progress, cancelled):
    """Unpacks an archive into out (made here)."""
    out.mkdir(parents=True, exist_ok=True)
    label = ARCHIVE_NAMES[kind]
    if not split and kind in ("zip", "tar"):
        import tarfile
        import zipfile
        import zlib
        try:
            (_unpack_zip if kind == "zip" else _unpack_tar)(p, out, progress, cancelled)
            return out
        except NotImplementedError:  # a zip compression the standard library does not read
            shutil.rmtree(out, ignore_errors=True)
            out.mkdir(parents=True)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise Fm4Error(f"The drive of the mods folder is full: {p.name} could not be unpacked. "
                               "Free some space, or extract it yourself and choose what it holds.") from None
            raise Fm4Error(f"{p.name} could not be read ({e}). Extract it yourself and choose what it "
                           "holds.") from None
        except (zipfile.BadZipFile, tarfile.TarError, zlib.error, EOFError) as e:
            raise Fm4Error(f"{p.name} could not be read as a {label} archive ({e}): is it complete? "
                           "Extract it yourself and choose what it holds.") from None
    tools = _tool_paths(kind)
    if not tools:
        need = "7-Zip or unrar" if kind == "rar" else "7-Zip"
        raise Fm4Error(f"{p.name} is a {label} archive, and no program that unpacks it was found on this "
                       f"PC ({need}). Extract it first (for example with 7-Zip), then choose the disc "
                       "image, Games on Demand package or folder it holds.")
    size = _tool_size(tools[0], p)
    if size:
        _need_space(out, size, p.name)
    errors = []
    for exe in tools:
        err = _run_tool(exe, p, out, progress, cancelled)
        if err is None:
            bad = unsafe_entry(out)
            if bad:
                shutil.rmtree(out, ignore_errors=True)
                raise Fm4Error(f"{p.name} holds an unsafe entry ({bad[0]}: {bad[1]}), which the import "
                               "does not unpack. Extract it yourself and choose what it holds.")
            return out
        errors.append(f"{Path(exe).name}: {err}")
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True)
    raise Fm4Error(f"{p.name} could not be unpacked ({'; '.join(errors)}). Is it complete (all its parts "
                   "next to it)? Extract it yourself, then choose the disc image, Games on Demand "
                   "package or folder it holds.")


# ---- Finding the discs in what was chosen ------------------------------------------------------

class Found:
    """What a choice holds. discs: [(kind, path)] tried in order, kind "folder" (a disc's files
    around its default.xex), "image" (a disc image, a split one by its first part) or "god" (a
    Games on Demand or hard drive install package next to its .data folder); packs: Forza
    Motorsport 4 content packages (Disc 2's car packs); unpacked: unpacked car packs (their
    Media/DLCZips folders); others: content packages of other games or kinds; archives; broken:
    messages about a package without its data or the other way round."""

    def __init__(self, root, label=None):
        self.root, self.label = root, label or root.name or str(root)
        self.discs, self.packs, self.unpacked, self.others, self.archives, self.broken = [], [], [], [], [], []


def _scan(folder: Path, found=None):
    """Searches a folder (shallowest first, SCAN_DEPTH levels, SCAN_DIRS folders at most)."""
    import xiso_extract
    found = found or Found(folder)
    queue, seen, images = [(folder, 0)], 0, set()
    while queue and seen < SCAN_DIRS:
        d, depth = queue.pop(0)
        seen += 1
        try:
            children = sorted(d.iterdir(), key=lambda c: c.name.lower())
        except OSError:
            continue
        names = {c.name.lower() for c in children}
        if "default.xex" in names and _find_ci(d, "default.xex").is_file():
            found.discs.append(("folder", d))
            # a disc's own folder: only its content folder (Disc 2's car packs) is searched further
            children = [c for c in children if c.name.lower() == "content"]
        if d.name.lower() == "dlczips" and any(re.fullmatch(r"[0-9a-f]+_pri_65\.zip", n) for n in names):
            found.unpacked.append(d.parent.parent)  # <pack>/Media/DLCZips
            continue
        for c in children:
            low = c.name.lower()
            try:
                is_dir = c.is_dir()
            except OSError:
                continue
            if is_dir:
                if low.endswith(".data"):
                    if low[:-5] not in names:
                        found.broken.append(f"{c.name} is the data folder of a Games on Demand package, "
                                            f"but the package file ({c.name[:-5]}) is not next to it: "
                                            "copy it too.")
                    continue
                if depth < SCAN_DEPTH and low not in SKIP_DIRS:
                    queue.append((c, depth + 1))
                continue
            try:
                if not c.is_file():
                    continue
                size = c.stat().st_size
            except OSError:
                continue
            hdr = package_header(c)
            if hdr:
                if hdr[2] == 1:  # Games on Demand or a hard drive install: data next to it
                    if hdr[1] != FM4_TITLE:
                        found.others.append(c)
                    elif (d / (c.name + ".data")).is_dir():
                        found.discs.append(("god", c))
                    else:
                        found.broken.append(f"{c.name} is Forza Motorsport 4's Games on Demand package, "
                                            f"but the {c.name}.data folder with its data is not next to it.")
                elif hdr[1] == FM4_TITLE and hdr[0] == 0x2 and size > PACK_MIN:
                    found.packs.append(c)
                else:
                    found.others.append(c)
                continue
            if archive_kind(c):
                first, _ = _first_volume(c)
                if first == c or not first.exists():
                    found.archives.append(c)
                continue
            if IMAGE_NAME.search(c.name) or size >= 64 << 20:
                try:
                    first = Path(xiso_extract.split_parts(c)[0])
                except xiso_extract.IsoError as e:
                    found.broken.append(str(e))
                    continue
                if first not in images and xiso_extract.is_iso(c):
                    images.add(first)
                    found.discs.append(("image", c))
    return found


def _choice(path, disc, work: Path, archives, progress, cancelled):
    """What was chosen for Disc `disc` (a Found). An archive is unpacked into work (once: archives
    maps what was unpacked to where). A file that is no disc raises Fm4Error saying what it is."""
    import xiso_extract
    p = Path(path)
    if p.is_file() and p.parent.name.lower().endswith(".data") and re.fullmatch(r"Data[0-9A-F]{4}", p.name, re.I):
        p = p.parent  # one of a Games on Demand package's parts: the package is next to the folder
    if p.is_dir() and p.name.lower().endswith(".data"):
        if p.with_name(p.name[:-5]).is_file():
            p = p.with_name(p.name[:-5])
        elif any(re.fullmatch(r"(?i)data[0-9a-f]{4}", c.name) for c in p.iterdir()):
            raise Fm4Error(f"{p.name} is the data folder of a Games on Demand package, but the package "
                           f"file ({p.name[:-5]}) is not next to it: copy it too, then choose it.")
    if p.is_file() and p.suffix.lower() == ".dvd":  # the layer break file next to a disc image
        try:
            lines = [ln.strip() for ln in p.read_text("latin-1").splitlines() if ln.strip()]
        except OSError:
            lines = []
        image = next((p.with_name(ln) for ln in reversed(lines) if "=" not in ln), None)
        if image and image.is_file():
            p = image
    if p.is_dir():
        return _scan(p)
    if not p.is_file():
        raise Fm4Error(f"{p} does not exist.")
    found = Found(p.parent)
    first, split = _first_volume(p)
    if split and not first.is_file():
        raise Fm4Error(f"{p.name} is one part of a split archive, but its first part ({first.name}) "
                       "is not next to it: put all the parts in one folder.")
    kind = archive_kind(first)
    if kind:
        key = first.resolve()
        if key not in archives:
            archives[key] = unpack_archive(first, kind, split, work / f"archive{len(archives) + 1}",
                                           progress, cancelled)
        inside = _scan(archives[key], Found(archives[key], p.name))
        if not (inside.discs or inside.packs or inside.unpacked):
            if inside.archives:
                raise Fm4Error(f"{p.name} holds another archive ({inside.archives[0].name}): extract it "
                               "yourself and choose what it holds.")
        return inside
    try:
        xiso_extract.split_parts(p)
    except xiso_extract.IsoError as e:
        raise Fm4Error(str(e)) from None
    if xiso_extract.is_iso(p):
        found.discs.append(("image", p))
        return found
    part = xiso_extract.part_number(p)
    if part and part > 1 and not package_header(p):
        raise Fm4Error(f"{p.name} looks like part {part} of a split disc image, but its first part is "
                       "not next to it: put all the parts in one folder.")
    hdr = package_header(p)
    if hdr and hdr[1] == FM4_TITLE:
        if hdr[2] == 1:
            if not p.with_name(p.name + ".data").is_dir():
                raise Fm4Error(f"{p.name} is Forza Motorsport 4's Games on Demand package, but the "
                               f"{p.name}.data folder with its data is not next to it.")
            found.discs.append(("god", p))
            return found
        if hdr[0] == 0x2 and disc == 2:
            found.packs.append(p)
            return found
    if _xex_title(p) == FM4_TITLE:
        found.discs.append(("folder", p.parent))  # FM4's default.xex: the disc's files are around it
        return found
    raise Fm4Error(describe(p, disc))


def _open_volume(kind, path):
    import xcontent_extract
    import xiso_extract
    try:
        return xiso_extract.open_iso(path) if kind == "image" else xcontent_extract.open_package(str(path))
    except xiso_extract.IsoError as e:
        raise Fm4Error(f"{path.name}: {e}") from None
    except SystemExit as e:  # the extractor's way of refusing a damaged package
        raise Fm4Error(f"{path.name} could not be read: {e}") from None


def _close(volume):
    for f in getattr(volume, "files", []):
        f.close()


def _names(volume, path):
    import xiso_extract
    try:
        return {e.path.replace("\\", "/").lower() for e in volume.entries()}
    except (SystemExit, xiso_extract.IsoError) as e:
        raise Fm4Error(f"The files of {path.name} could not be read: {e}") from None


def _extract(volume, out, wanted, label, progress, cancelled):
    import xiso_extract
    try:
        xiso_extract.extract(volume, out, lambda d, t, n: progress(d, t, label + n), cancelled, wanted=wanted)
    except InterruptedError:
        raise Cancelled() from None
    except (SystemExit, xiso_extract.IsoError) as e:
        raise Fm4Error(f"The disc's files could not be read: {e}") from None


def _check_xex(xex: Path):
    title = _xex_title(xex)
    if title is None:
        raise Fm4Error("This is not Forza Motorsport 4's Disc 1: its default.xex is not an Xbox 360 program.")
    if title != FM4_TITLE:
        name = TITLES.get(title)
        what = f"a {name} disc" if name else f"another game's disc (title id {title:08X})"
        raise Fm4Error(f"This is {what}, not Forza Motorsport 4's Disc 1 (title id {FM4_TITLE:08X}). "
                       + CHOOSE[1])


def _find_ci(root: Path, *parts):
    p = root
    for part in parts:
        try:
            p = next((c for c in p.iterdir() if c.name.lower() == part.lower()), None)
        except OSError:
            return None
        if p is None:
            return None
    return p


def _check_disc1(root: Path):
    xex = _find_ci(root, "default.xex")
    if not xex or not xex.is_file():
        if _find_ci(root, "content", "0000000000000000"):
            raise Fm4Error(DISC2_AS_DISC1)
        raise Fm4Error("This folder is not Forza Motorsport 4's Disc 1: it has no default.xex. " + CHOOSE[1])
    _check_xex(xex)
    if not _find_ci(root, "Media", "db", "gamedb.slt"):
        if _find_ci(root, "content", "0000000000000000"):
            raise Fm4Error(DISC2_AS_DISC1)
        raise Fm4Error("This is not Forza Motorsport 4's Disc 1: it has no Media/db/gamedb.slt.")


def _unpack_disc1(kind, path, out, progress, cancelled):
    """Unpacks what the cars need from a disc image or a Games on Demand package into out:
    default.xex first, so another game or Disc 2 is refused before the long copy."""
    shutil.rmtree(out, ignore_errors=True)
    volume = _open_volume(kind, path)
    try:
        names = _names(volume, path)
        if "default.xex" not in names:
            if any(x.startswith("content/") for x in names):
                raise Fm4Error(DISC2_AS_DISC1)
            raise Fm4Error(f"{path.name} is not Forza Motorsport 4's Disc 1: it has no default.xex. "
                           + CHOOSE[1])
        _extract(volume, out, ("default.xex",), "Reading Disc 1: ", progress, cancelled)
        _check_xex(out / "default.xex")
        if "media/db/gamedb.slt" not in names:
            if any(x.startswith("content/") for x in names):
                raise Fm4Error(DISC2_AS_DISC1)
            raise Fm4Error(f"{path.name} is not Forza Motorsport 4's Disc 1: it has no Media/db/gamedb.slt.")
        _extract(volume, out, DISC1_PATHS[1:], "Reading Disc 1: ", progress, cancelled)
        return out
    finally:
        _close(volume)


def _nothing(found: Found, disc):
    """The message for a choice that holds no disc `disc`."""
    if found.broken:
        return found.broken[0]
    if found.others:
        return describe(found.others[0], disc)
    if found.archives:
        return (f"{found.archives[0].name} is an archive: choose that file itself (the import unpacks "
                "it), or extract it first.")
    return (f"{found.label} holds no Forza Motorsport 4 Disc {disc}: no default.xex, disc "
            "image or Games on Demand package was found in it or its subfolders. " + CHOOSE[disc])


def _disc1(found: Found, out, progress, cancelled):
    """Disc 1's folder: the first of the found discs that is FM4's Disc 1 (unpacked into out)."""
    errors = []
    for kind, path in found.discs:
        try:
            if kind == "folder":
                _check_disc1(path)
                return path
            disc1 = _unpack_disc1(kind, path, out, progress, cancelled)
            _check_disc1(disc1)
            return disc1
        except Fm4Error as e:
            errors.append(str(e))
    if errors:
        raise Fm4Error(DISC2_AS_DISC1 if DISC2_AS_DISC1 in errors else errors[0])
    if found.packs or found.unpacked:
        raise Fm4Error(f"{found.label} holds Forza Motorsport 4's Disc 2 car packs, not Disc 1. "
                       "Choose Disc 1 (the play disc) first: the car packs come at the next step (Disc 2).")
    raise Fm4Error(_nothing(found, 1))


def _car_packs(folder: Path):
    """FM4's car packs in a folder: its STFS packages of content type 2 (Disc 2 has four)."""
    out = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.stat().st_size > PACK_MIN:
            hdr = package_header(p)
            if hdr and hdr[1] == FM4_TITLE and hdr[0] == 0x2 and hdr[2] != 1:
                out.append(p)
    return out


def _disc2(found: Found, work, progress, cancelled):
    """(car packages to unpack, or None, and a folder of unpacked car packs or None)."""
    import os
    if found.packs:
        packs, seen = [], set()
        for p in found.packs:  # the same pack found twice (a disc's copy and an installed one)
            key = (p.name.lower(), p.stat().st_size)
            if key not in seen:
                seen.add(key)
                packs.append(p)
        return packs, None
    if found.unpacked:
        return None, Path(os.path.commonpath([str(p) for p in found.unpacked]))
    disc1 = None
    for kind, path in found.discs:
        if kind == "folder":
            if _find_ci(path, "Media", "db", "gamedb.slt"):
                disc1 = disc1 or f"{path.name} is Forza Motorsport 4's Disc 1, chosen again for Disc 2. "
            continue
        volume = _open_volume(kind, path)
        try:
            names = _names(volume, path)
            if not any(x.startswith("content/") for x in names):
                if "media/db/gamedb.slt" in names:
                    disc1 = disc1 or f"{path.name} has no Forza Motorsport 4 car packs: is it Disc 1? "
                continue
            out = work / "disc2"
            shutil.rmtree(out, ignore_errors=True)
            _extract(volume, out, ("content",), "Reading Disc 2: ", progress, cancelled)
        finally:
            _close(volume)
        packs = _car_packs(out)
        if packs:
            return packs, None
    if disc1:
        raise Fm4Error(disc1 + CHOOSE[2])
    raise Fm4Error(_nothing(found, 2))


def prepare(disc1, disc2, work: Path, progress, cancelled):
    """Folders the converter reads: (disc 1 folder, disc 2 packages folder or None). A disc
    image, Games on Demand package or archive is unpacked into work (only what the cars need); a
    folder is used as it is."""
    paths.ensure_tools_importable()
    import xcontent_extract
    archives = {}
    disc1 = _disc1(_choice(disc1, 1, work, archives, progress, cancelled), work / "disc1", progress, cancelled)
    if not disc2:
        return disc1, None
    packages, unpacked = _disc2(_choice(disc2, 2, work, archives, progress, cancelled), work, progress, cancelled)
    if unpacked:
        return disc1, unpacked  # already unpacked packages (each with Media/DLCZips)
    out = work / "content"
    for k, pkg in enumerate(packages):
        if cancelled():
            raise Cancelled()
        progress(k, len(packages), "Unpacking the Disc 2 car packs")
        p = xcontent_extract.open_package(str(pkg))
        try:
            for e in p.entries():
                if not e.is_dir:
                    xcontent_extract.extract(p, e, str(out / pkg.name))
        except SystemExit as e:  # the extractor's way of refusing a damaged or unsafe package
            raise Fm4Error(f"{pkg.name}: {e}") from None
        finally:
            for f in getattr(p, "files", []):
                f.close()
    return disc1, out


# ---- FM4's DLC packs ----------------------------------------------------------------------------
# After the discs the player may add their FM4 DLC: the packages (STFS LIVE/PIRS, any name, as the
# console's Content/0000000000000000/4D530910/00000002 holds them), a folder of them, unpacked ones
# (each with Media/DLCZips) or an archive of any of these. A car pack has its cars' database rows
# in a patch (media/db/patch/<n>_merge.slt); Disc 2's car packs, also of content type 2, have none
# (their rows are on Disc 1) and are taken as Disc 2's when Disc 2 was skipped. Anything else
# (the track pack, an offer, another game's package) is named and left out.

CHOOSE_DLC = ("Choose your Forza Motorsport 4 DLC: its packages (any name, as in the console's "
              "Content/0000000000000000/4D530910/00000002 folder), a folder holding them, or an archive "
              "(.zip, .7z, .rar) of them.")


def _quoted(path, hdr=None):
    hdr = hdr or package_header(path)
    return f'{path.name} ("{hdr[3]}")' if hdr and hdr[3] else path.name


def _dlc_other(p: Path):
    """Why a file found among the DLC is left out, in a few words."""
    hdr = package_header(p)
    if not hdr:
        return f"{p.name} is not an Xbox 360 content package"
    content_type, title, _, _ = hdr
    if title == FM4_TITLE and content_type == 0x2:
        return f"{_quoted(p, hdr)} is too small to hold a car (an offer or a token)"
    if title == FM4_TITLE and content_type == 0xB0000:
        return f"{_quoted(p, hdr)} is a Forza Motorsport 4 title update, which the import does not need"
    thing = CONTENT_TYPES.get(content_type, f"content package (type {content_type:X})")
    return f"{_quoted(p, hdr)} is {_game_thing(title, thing)}, not Forza Motorsport 4 DLC"


def dlc_choice(chosen, work: Path, archives, progress, cancelled):
    """(FM4 DLC packages, unpacked package folders, notes) in what was chosen for the DLC: files,
    folders or archives (an archive is unpacked into work once). Raises Fm4Error when it holds
    no FM4 content package at all."""
    packs, unpacked, notes, seen = [], [], [], set()
    for path in chosen:
        p = Path(path)
        if p.is_dir():
            found = _scan(p)
        elif not p.is_file():
            raise Fm4Error(f"{p} does not exist.")
        else:
            first, split = _first_volume(p)
            if split and not first.is_file():
                raise Fm4Error(f"{p.name} is one part of a split archive, but its first part ({first.name}) "
                               "is not next to it: put all the parts in one folder.")
            kind = archive_kind(first)
            if kind:
                key = first.resolve()
                if key not in archives:
                    archives[key] = unpack_archive(first, kind, split, work / f"archive{len(archives) + 1}",
                                                   progress, cancelled)
                found = _scan(archives[key], Found(archives[key], p.name))
            else:
                found = Found(p.parent)
                hdr = package_header(p)
                if hdr and hdr[1] == FM4_TITLE and hdr[0] == 0x2 and hdr[2] != 1 and p.stat().st_size > PACK_MIN:
                    found.packs.append(p)
                else:
                    found.others.append(p)
        for c in found.packs:
            key = (c.name.lower(), c.stat().st_size)  # the same package chosen twice
            if key not in seen:
                seen.add(key)
                packs.append(c)
        unpacked += [u for u in found.unpacked if u not in unpacked]
        notes += [_dlc_other(o) for o in found.others]
        notes += [f"{a.name} is an archive inside what was chosen: choose it itself, or extract it"
                  for a in found.archives]
    if not (packs or unpacked):
        if notes:
            raise Fm4Error(f"No Forza Motorsport 4 DLC was found: {notes[0]}. " + CHOOSE_DLC)
        raise Fm4Error("No Forza Motorsport 4 DLC package was found in what was chosen. " + CHOOSE_DLC)
    return packs, unpacked, notes


def _unpack_package(pkg: Path, out: Path):
    import xcontent_extract
    p = xcontent_extract.open_package(str(pkg))
    try:
        for e in p.entries():
            if not e.is_dir:
                xcontent_extract.extract(p, e, str(out))
    except SystemExit as e:  # the extractor's way of refusing a damaged or unsafe package
        raise Fm4Error(f"{_quoted(pkg)} could not be read: {e}. Is the copy complete?") from None
    finally:
        _close(p)


def prepare_dlc(found, work: Path, have_disc2, progress, cancelled):
    """Unpacks the chosen DLC packages into work and sorts them. Returns (car pack folders [(folder,
    name)], the folders of Disc 2 car packs found among them when Disc 2 was skipped, notes)."""
    tools = _tools()
    packs, unpacked, notes = found
    try:
        total = sum(p.stat().st_size for p in packs)
    except OSError:
        total = 0
    if packs:
        work.mkdir(parents=True, exist_ok=True)
        _need_space(work, total, "your FM4 DLC")
    folders = []
    for k, pkg in enumerate(packs):
        if cancelled():
            raise Cancelled()
        progress(k, len(packs), f"Unpacking your FM4 DLC: {package_header(pkg)[3] or pkg.name}")
        out = work / "packages" / pkg.name
        shutil.rmtree(out, ignore_errors=True)
        _unpack_package(pkg, out)
        folders.append((out, package_header(pkg)[3] or pkg.name))
    folders += [(u, u.name) for u in unpacked]
    cars, disc2, other, offers = [], [], [], set()
    for folder, name in folders:
        for pack in tools.pack_folders(str(folder)) or [str(folder)]:
            pack = Path(pack)
            try:
                offer = next((f.name.lower() for f in (_find_ci(pack, "Media") or pack).iterdir()
                              if f.name.lower().endswith(".puboffer")), None)
            except OSError:
                offer = None
            if offer and offer in offers:
                continue  # the same pack twice (a copy on another drive)
            if offer:
                offers.add(offer)
            try:
                models, rows = tools.pack_cars(str(pack))
            except (OSError, ValueError, tools.ConvertError) as e:
                notes.append(f"{name} could not be read ({e})")
                continue
            finally:
                tools.ZIPS.close(str(pack))  # Windows: the folder is moved or deleted later (#707)
            if rows:
                cars.append((pack, name))
            elif models:
                disc2.append((pack, name))
            else:
                other.append(name)
    if other:
        notes.append(f"{', '.join(other)}: no cars (tracks or other content)")
    content = []
    if disc2 and have_disc2:
        notes.append(f"{', '.join(n for _, n in disc2)}: Disc 2's car packs, read from Disc 2 already")
    else:
        content = [pack for pack, _ in disc2]  # Disc 2 was skipped: its packs are here
    if not cars and not content:
        raise Fm4Error("None of the chosen Forza Motorsport 4 packages has cars"
                       + (f" ({'; '.join(notes)})" if notes else "") + ". " + CHOOSE_DLC)
    return cars, content, notes


def _update_root(cfg):
    from . import game as game_module
    from .config import render_toml
    install, _ = game_module.find_game(cfg)
    update = game_module.update_data_root(cfg, install, render_toml(cfg)) if install else None
    if not update or not update.is_dir():
        # With no managed update directory, Play leaves the root unset and the TU4
        # game defaults to a sibling of its game-data directory.
        update = Path(os.path.abspath(cfg.game_files())).parent / "tu4"
    extra = game_module.split_args(cfg.advanced.get("extra_args", ""))
    for i, arg in enumerate(extra):
        if arg.startswith("--update_data_root="):
            update = Path(arg.split("=", 1)[1])
        elif arg == "--update_data_root" and i + 1 < len(extra):
            update = Path(extra[i + 1])
    return update if update and (Path(update) / "media.zip").is_file() else None


# ---- Moving and deleting on Windows (#551) ------------------------------------------------------
# Windows refuses to rename or delete a file while a program holds it open, and a folder while it
# holds a file in it: our own handles (FM4's zips, the databases), which are closed before the
# publish, but also an antivirus or the search indexer reading the gigabytes just written, for a
# moment. Such an error is retried for a few seconds before it is named to the player.
_BUSY = (5, 32, 33)  # access denied (a folder being scanned), sharing violation, lock violation
_WAITS = (0.1, 0.2, 0.4, 0.8, 1.5, 2.5)


def _busy(e):
    return getattr(e, "winerror", None) in _BUSY


def _retry(fn, *args):
    """fn(*args), retried while Windows says a file is in use."""
    for wait in _WAITS:
        try:
            return fn(*args)
        except OSError as e:
            if not _busy(e):
                raise
            time.sleep(wait)
    return fn(*args)


def _move(src, dst):
    """Moves a folder of ours: a rename, retried while a file of it is in use (shutil.move would
    copy the gigabytes instead, then fail to delete the source); a copy only to another drive."""
    try:
        _retry(os.rename, src, dst)
    except OSError as e:
        if _busy(e):
            raise
        shutil.move(src, dst)


def _remove(path):
    """Deletes a folder of ours, retried while a file of it is in use. False when it stays."""
    try:
        _retry(shutil.rmtree, path)
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def _locked(e):
    return getattr(e, "winerror", None) in (32, 33)


def _in_use(e):
    return Fm4Error(f"{e.filename or 'A file'} is in use by another program, so the import could not "
                    "finish; the cars imported before are kept. This is usually an antivirus scan of "
                    "the new files or a File Explorer window on that folder: wait a minute or close it, "
                    "then import again.")


def build(cfg, disc1, disc2=None, progress=lambda n, total, text: None, cancelled=lambda: False,
          converter=None, dlc=None):
    """Convert every FM4 car FH1 lacks and publish them as the FM4 cars mod: those of the discs
    and, with dlc (a list of files and folders the player chose), those of their FM4 DLC packs.
    The previous import stays until the new one is complete. Returns a message for the player."""
    tools = _tools()
    target = mods.mods_dir(cfg) / MOD
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or (target.exists() and not (target / MARKER).is_file()):
        raise Fm4Error(f"{target} already exists and was not made by Import FM4 cars. Move it first.")
    backup = target.with_name("." + MOD + ".previous")
    if backup.exists():
        _remove(backup)
    for old in target.parent.glob(".fm4-import-*"):  # an earlier import's, its cleanup refused
        if old.is_dir() and not old.is_symlink():
            _remove(old)
    work = Path(tempfile.mkdtemp(prefix=".fm4-import-", dir=target.parent))
    try:
        message = _build(cfg, tools, target, backup, work, disc1, disc2, progress, cancelled, converter, dlc)
    except BaseException:
        _remove(work)
        raise
    if not _remove(work):
        # the cars are in: a folder Windows won't let go of yet is no reason to say it failed
        message += (f" Its temporary folder {work} could not be deleted yet (a program has a file "
                    "of it open); the next import deletes it, or delete it yourself.")
    return message


def _build(cfg, tools, target, backup, work, disc1, disc2, progress, cancelled, converter, dlc=None):
    dlc_root = Path(cfg.data_home()) / datafolder.DLC_DIR / "00000002"
    archives = {}  # the DLC's: the discs' are prepare()'s
    # what was chosen for the DLC is looked at first: a wrong choice is named before the discs' copy
    found = dlc_choice(dlc, work / "dlc", archives, progress, cancelled) if dlc else None
    src1, src2 = prepare(disc1, disc2, work / "src", progress, cancelled)
    dlc_packs, dlc_content, notes = (prepare_dlc(found, work / "dlc", bool(src2), progress, cancelled)
                                     if found else ([], [], []))
    staged = work / MOD
    staged_pack = work / "package" / tools.PACK_NAME
    fm4 = fh1 = conv = None
    try:
        content = ([str(src2)] if src2 else []) + [str(p) for p in dlc_content]
        fm4 = tools.FM4(str(src1), content or None, [str(p) for p, _ in dlc_packs] or None)
        update = _update_root(cfg)
        fh1 = tools.FH1(str(cfg.game_files()), str(update) if update else None, str(dlc_root))
        conv = (converter or tools.Converter)(fm4, fh1, str(staged), str(staged_pack))
        candidates = conv.candidates()
        ids = [cid for cid, _, reason in candidates if not reason]
        dlc_ids = getattr(fm4, "dlc_car_ids", set())
        if dlc_packs:
            have = len(dlc_ids - {cid for cid, _, reason in candidates if reason != "an FH1 DLC car"})
            if have:
                notes.append(f"{have} of the DLC's cars are in FH1 or its DLC already")
            notes += [f"{name}: {reason}" for cid, name, reason in candidates
                      if reason and cid in dlc_ids and reason != "an FH1 DLC car"]
        if not ids:
            raise Fm4Error("Your FM4 files have no car that FH1 lacks.")
        report = conv.run(ids, progress, cancelled)
    except tools.Cancelled:
        raise Cancelled() from None
    except tools.ConvertError as e:
        raise Fm4Error(str(e)) from None
    finally:
        # every handle on the unpacked discs and the new pack closed before they move or are
        # deleted: Windows refuses both while a file is open (#551)
        if conv is not None and hasattr(conv, "close"):
            conv.close()
        if fh1 is not None:
            fh1.close()
        if fm4 is not None:
            fm4.close()
    # banks: made with the engine sound banks FH1 lacks (#776), nothing for repair_sound to do
    # engine_tuning 2: FM4's own engine tuning where FH1's is a cut traffic car's (#840)
    info = {"format": 1, "banks": True, "engine_tuning": 2, "cars": [c[1] for c in report["cars"]],
            "skipped": [s[1] for s in report["skipped"]],
            "dlc": _dlc(cfg)}
    if dlc_packs or dlc:
        info["fm4_dlc"] = {"packs": [name for _, name in dlc_packs],
                           "cars": [c[1] for c in report.get("dlc_cars", [])], "notes": notes}
    (staged / MARKER).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    if cancelled():
        raise Cancelled()
    old_state = copy.deepcopy(cfg.d.get("mods"))
    package, header = _package(cfg)
    new_header = work / "Headers" / "00000002" / header.name  # where the converter puts it
    existed = target.exists()
    if existed:
        try:
            _retry(target.rename, backup)
        except OSError as e:
            if _locked(e):
                raise _in_use(e) from e
            raise
    old_package = package.with_name(package.name + ".previous")
    had_package = package.exists()
    moved = False
    try:
        if had_package:
            if old_package.exists():
                _retry(shutil.rmtree, old_package)
            _retry(package.rename, old_package)
        if staged_pack.exists():
            package.parent.mkdir(parents=True, exist_ok=True)
            moved = True
            _move(str(staged_pack), str(package))
            header.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(new_header, header)
        _retry(staged.rename, target)
        state = mods.state(cfg)
        cfg.d["mods"] = {"order": [f for f in state["order"] if f != MOD] + [MOD],
                         "disabled": [f for f in state["disabled"] if f != MOD]}
        cfg.save()
    except BaseException as e:
        if target.exists():
            _retry(shutil.rmtree, target)
        if existed:
            _retry(backup.rename, target)
        if moved and package.exists():
            _retry(shutil.rmtree, package)
        if old_package.exists():
            _retry(old_package.rename, package)
        if old_state is None:
            cfg.d.pop("mods", None)
        else:
            cfg.d["mods"] = old_state
        if isinstance(e, OSError) and _locked(e):
            raise _in_use(e) from e
        raise
    if existed:
        _remove(backup)
    _remove(old_package)
    n, skipped = len(info["cars"]), len(info["skipped"])
    more = f" {skipped} could not be converted (see the mod's {MARKER})." if skipped else ""
    from_dlc = ""
    if "fm4_dlc" in info:
        from_dlc = f", {len(info['fm4_dlc']['cars'])} of them from your FM4 DLC"
        if notes:
            more += " Not added from the DLC: " + "; ".join(notes) + "."
    return (f"Added {n} Forza Motorsport 4 car(s){from_dlc}. They are in the Autoshow the next time the "
            f"game starts.{more}")


def remove(cfg):
    """Removes the imported cars (the mod and its package). Cars of them in a garage then show
    as missing content until they are imported again."""
    if (mods.mods_dir(cfg) / MOD).exists():
        mods.remove(cfg, MOD)
    package, header = _package(cfg)
    if package.exists():
        shutil.rmtree(package)
    if header.exists():
        header.unlink()
