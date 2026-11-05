"""Import Forza Motorsport 4's cars into FH1 from the player's own FM4 discs (#648).

The launcher reads FM4's Disc 1 (and optionally the Disc 2 car packs), converts every FM4 car
FH1 lacks with tools/fm4_cars.py and publishes the result: the cars' files as a DLC-style
package in the user data folder (the mod overlay holds 4 GB at most, a full import is more),
their database rows and names as one generated mod ("FM4 cars" on the Mods page). Unticking
the mod hides the cars; remove() deletes both.
Nothing is downloaded and nothing leaves this PC. docs/modding/fm4-cars.md has the details.
"""
import copy
import json
import re
import shutil
import struct
import tempfile
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


# ---- What the player chose (#681) --------------------------------------------------------------
# Disc 1 can be a disc image, a Games on Demand package (its data parts hold the same XDVDFS
# volume as the disc) or a folder with the disc's files; Disc 2 also its car packs themselves.
# Anything else is named in plain words: another game's package, a title update, a lone XEX.

TITLES = {0x4D5309C9: "Forza Horizon", FM4_TITLE: "Forza Motorsport 4", 0x4D53084D: "Forza Motorsport 3",
          0x4D530AA4: "Forza Horizon 2"}
# Xbox 360 content package types (the header's content type)
CONTENT_TYPES = {0x1: "saved game", 0x2: "content package (DLC)", 0x4000: "hard drive install",
                 0x7000: "Games on Demand package", 0x9000: "avatar item", 0x10000: "profile",
                 0x20000: "gamer picture", 0x30000: "theme", 0x80000: "demo",
                 0xB0000: "title update", 0xD0000: "Xbox Live Arcade game"}
CHOOSE = {1: "Choose your FM4 Disc 1 (the play disc): its disc image (.iso), its Games on Demand "
             "package or a folder with its files.",
          2: "Choose your FM4 Disc 2 (the content install disc): its disc image (.iso), a folder with "
             "its files or its car packs, or Skip."}
DISC2_AS_DISC1 = ("This is Forza Motorsport 4's Disc 2 (the content install disc), not Disc 1. Choose "
                  "Disc 1 (the play disc) first: Disc 2 comes at the next step.")


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
    return (f"{path.name} is not an Xbox 360 disc image, Games on Demand package or content package."
            f"{hint} " + CHOOSE[disc])


def _god_packages(folder: Path):
    """Forza Motorsport 4 Games on Demand packages (the file next to its <name>.data folder) in a
    folder, its 00007000 folder or the title folders' (up to Content/0000000000000000)."""
    bases = [folder, folder / "00007000", *folder.glob("*/00007000"), *folder.glob("*/*/00007000")]
    found = []
    for base in bases:
        try:
            files = sorted(f for f in base.iterdir() if f.is_file())
        except OSError:
            continue
        for f in files:
            hdr = package_header(f)
            if (f.with_name(f.name + ".data").is_dir() and hdr and hdr[1] == FM4_TITLE
                    and hdr[2] == 1 and f not in found):
                found.append(f)
    return found


def _source(path, disc):
    """What was chosen for Disc `disc`: ("folder", folder), ("image", disc image),
    ("god", [Games on Demand packages]) or ("pack", a Disc 2 car pack). Anything else raises
    Fm4Error saying what it is."""
    p = Path(path)
    if p.is_file() and p.parent.name.lower().endswith(".data") and re.fullmatch(r"Data\d+", p.name, re.I):
        p = p.parent  # one of a Games on Demand package's parts: the package is next to the folder
    if p.is_dir() and p.name.lower().endswith(".data") and p.with_name(p.name[:-5]).is_file():
        p = p.with_name(p.name[:-5])
    if p.is_dir():
        if (p / "default.xex").is_file():
            return "folder", p
        gods = _god_packages(p)
        return ("god", gods) if gods else ("folder", p)
    if not p.is_file():
        raise Fm4Error(f"{p} does not exist.")
    import xiso_extract
    if xiso_extract.is_iso(p):
        return "image", p
    hdr = package_header(p)
    if hdr and hdr[1] == FM4_TITLE:
        if hdr[2] == 1:
            if not p.with_name(p.name + ".data").is_dir():
                raise Fm4Error(f"{p.name} is Forza Motorsport 4's Games on Demand package, but the "
                               f"{p.name}.data folder with its data is not next to it.")
            return "god", [p]
        if hdr[0] == 0x2 and disc == 2:
            return "pack", p
    if _xex_title(p) == FM4_TITLE:
        return "folder", p.parent  # FM4's default.xex: the disc's files are around it
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


def _names(volume):
    import xiso_extract
    try:
        return {e.path.replace("\\", "/").lower() for e in volume.entries()}
    except (SystemExit, xiso_extract.IsoError) as e:
        raise Fm4Error(f"The disc's files could not be read: {e}") from None


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
    if not (root / "default.xex").is_file():
        if _find_ci(root, "content", "0000000000000000"):
            raise Fm4Error(DISC2_AS_DISC1)
        raise Fm4Error("This folder is not Forza Motorsport 4's Disc 1: it has no default.xex. " + CHOOSE[1])
    _check_xex(root / "default.xex")
    if not _find_ci(root, "Media", "db", "gamedb.slt"):
        if _find_ci(root, "content", "0000000000000000"):
            raise Fm4Error(DISC2_AS_DISC1)
        raise Fm4Error("This is not Forza Motorsport 4's Disc 1: it has no Media/db/gamedb.slt.")


def _unpack_disc1(kind, found, out, progress, cancelled):
    """Unpacks what the cars need from a disc image or a Games on Demand package into out:
    default.xex first, so another game or Disc 2 is refused before the long copy."""
    for n, path in enumerate(found):
        volume = _open_volume(kind, path)
        try:
            names = _names(volume)
            if "media/db/gamedb.slt" not in names and n + 1 < len(found):
                continue  # Disc 2 of a Games on Demand copy: try the next package
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


def _car_packs(folder: Path):
    """FM4's car packs in a folder: its STFS packages of content type 2 (Disc 2 has four)."""
    out = []
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.stat().st_size > 1 << 20:
            hdr = package_header(p)
            if hdr and hdr[1] == FM4_TITLE and hdr[0] == 0x2 and hdr[2] != 1:
                out.append(p)
    return out


def prepare(disc1, disc2, work: Path, progress, cancelled):
    """Folders the converter reads: (disc 1 folder, disc 2 packages folder or None). A disc
    image or Games on Demand package is unpacked into work (only what the cars need); a folder
    is used as it is."""
    paths.ensure_tools_importable()
    import xcontent_extract
    kind, found = _source(disc1, 1)
    if kind == "folder":
        disc1 = found
    else:
        disc1 = _unpack_disc1(kind, found if kind == "god" else [found], work / "disc1", progress, cancelled)
    _check_disc1(disc1)
    if not disc2:
        return disc1, None
    kind, found = _source(disc2, 2)
    if kind == "pack":
        packages = [found]
    else:
        if kind != "folder":
            for path in (found if kind == "god" else [found]):
                volume = _open_volume(kind, path)
                try:
                    names = _names(volume)
                    if not any(x.startswith("content/") for x in names):
                        if kind == "god" and path is not found[-1]:
                            continue
                        raise Fm4Error(f"{path.name} has no Forza Motorsport 4 car packs: is it Disc 1? "
                                       + CHOOSE[2])
                    _extract(volume, work / "disc2", ("content",), "Reading Disc 2: ", progress, cancelled)
                    break
                finally:
                    _close(volume)
            found = work / "disc2"
        disc2 = found
        packages = _car_packs(disc2)
        if not packages:
            if _find_ci(disc2, "Media", "db", "gamedb.slt"):
                raise Fm4Error("This folder is Forza Motorsport 4's Disc 1, chosen again for Disc 2. " + CHOOSE[2])
            return disc1, disc2  # already unpacked packages (each with Media/DLCZips)
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


def _update_root(cfg):
    from . import game as game_module
    from .config import render_toml
    install, _ = game_module.find_game(cfg)
    update = game_module.update_data_root(cfg, install, render_toml(cfg)) if install else None
    extra = game_module.split_args(cfg.advanced.get("extra_args", ""))
    for i, arg in enumerate(extra):
        if arg.startswith("--update_data_root="):
            update = Path(arg.split("=", 1)[1])
        elif arg == "--update_data_root" and i + 1 < len(extra):
            update = Path(extra[i + 1])
    return update if update and (Path(update) / "media.zip").is_file() else None


def build(cfg, disc1, disc2=None, progress=lambda n, total, text: None, cancelled=lambda: False,
          converter=None):
    """Convert every FM4 car FH1 lacks and publish them as the FM4 cars mod. The previous
    import stays until the new one is complete. Returns a message for the player."""
    tools = _tools()
    target = mods.mods_dir(cfg) / MOD
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or (target.exists() and not (target / MARKER).is_file()):
        raise Fm4Error(f"{target} already exists and was not made by Import FM4 cars. Move it first.")
    backup = target.with_name("." + MOD + ".previous")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    dlc_root = Path(cfg.data_home()) / datafolder.DLC_DIR / "00000002"
    with tempfile.TemporaryDirectory(prefix=".fm4-import-", dir=target.parent) as temp:
        work = Path(temp)
        src1, src2 = prepare(disc1, disc2, work / "src", progress, cancelled)
        staged = work / MOD
        staged_pack = work / "package" / tools.PACK_NAME
        fm4 = tools.FM4(str(src1), str(src2) if src2 else None)
        update = _update_root(cfg)
        fh1 = tools.FH1(str(cfg.game_files()), str(update) if update else None, str(dlc_root))
        try:
            conv = (converter or tools.Converter)(fm4, fh1, str(staged), str(staged_pack))
            ids = [cid for cid, _, reason in conv.candidates() if not reason]
            if not ids:
                raise Fm4Error("Your FM4 files have no car that FH1 lacks.")
            try:
                report = conv.run(ids, progress, cancelled)
            except tools.Cancelled:
                raise Cancelled() from None
            except tools.ConvertError as e:
                raise Fm4Error(str(e)) from None
        finally:
            fh1.close()
        info = {"format": 1, "cars": [c[1] for c in report["cars"]],
                "skipped": [s[1] for s in report["skipped"]],
                "dlc": _dlc(cfg)}
        (staged / MARKER).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        if cancelled():
            raise Cancelled()
        old_state = copy.deepcopy(cfg.d.get("mods"))
        package, header = _package(cfg)
        new_header = work / "Headers" / "00000002" / header.name  # where the converter puts it
        existed = target.exists()
        if existed:
            target.rename(backup)
        old_package = package.with_name(package.name + ".previous")
        had_package = package.exists()
        moved = False
        try:
            if had_package:
                if old_package.exists():
                    shutil.rmtree(old_package)
                package.rename(old_package)
            if staged_pack.exists():
                package.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(staged_pack), str(package))
                moved = True
                header.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(new_header, header)
            staged.rename(target)
            state = mods.state(cfg)
            cfg.d["mods"] = {"order": [f for f in state["order"] if f != MOD] + [MOD],
                             "disabled": [f for f in state["disabled"] if f != MOD]}
            cfg.save()
        except BaseException:
            if target.exists():
                shutil.rmtree(target)
            if existed:
                backup.rename(target)
            if moved and package.exists():
                shutil.rmtree(package)
            if old_package.exists():
                old_package.rename(package)
            if old_state is None:
                cfg.d.pop("mods", None)
            else:
                cfg.d["mods"] = old_state
            raise
        if existed:
            shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(old_package, ignore_errors=True)
    n, skipped = len(info["cars"]), len(info["skipped"])
    more = f" {skipped} could not be converted (see the mod's {MARKER})." if skipped else ""
    return f"Added {n} Forza Motorsport 4 car(s). They are in the Autoshow the next time the game starts.{more}"


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
