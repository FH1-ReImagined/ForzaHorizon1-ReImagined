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
import os
import shutil
import tempfile
from pathlib import Path

from . import datafolder, mods, paths
from .fetch import Cancelled

MOD = "reimagined-fm4-cars"
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
    name = _tools().PACK_NAME
    return root / "00000002" / name, root / "Headers" / "00000002" / (name + ".header")


def _dlc(cfg):
    name = _tools().PACK_NAME
    return sorted(p for p in datafolder.dlc_packages(cfg.data_home()) if p != name)


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


def _check_disc1(root: Path):
    xex = root / "default.xex"
    if not xex.is_file():
        raise Fm4Error("This is not Forza Motorsport 4's Disc 1: it has no default.xex.")
    from .xexcheck import header_info
    with open(xex, "rb") as f:
        info = header_info(f.read(0x10000))
    if not info or info[3] != FM4_TITLE:
        raise Fm4Error("This is not Forza Motorsport 4's Disc 1 (the title id is not 4D530910).")


def prepare(disc1, disc2, work: Path, progress, cancelled):
    """Folders the converter reads: (disc 1 folder, disc 2 packages folder or None). A disc
    image is unpacked into work (only what the cars need); a folder is used as it is."""
    paths.ensure_tools_importable()
    import xcontent_extract
    import xiso_extract
    disc1 = Path(disc1)
    if disc1.is_file():
        if not xiso_extract.is_iso(disc1):
            raise Fm4Error(f"{disc1.name} is not an Xbox 360 disc image.")
        out = work / "disc1"
        iso = xiso_extract.open_iso(disc1)
        try:
            xiso_extract.extract(iso, out, lambda d, t, n: progress(d, t, "Reading Disc 1: " + n),
                                 cancelled, wanted=DISC1_PATHS)
        finally:
            iso.close()
        disc1 = out
    _check_disc1(disc1)
    if not disc2:
        return disc1, None
    disc2 = Path(disc2)
    packages = []
    if disc2.is_file():
        if not xiso_extract.is_iso(disc2):
            raise Fm4Error(f"{disc2.name} is not an Xbox 360 disc image.")
        iso = xiso_extract.open_iso(disc2)
        try:
            xiso_extract.extract(iso, work / "disc2", lambda d, t, n: progress(d, t, "Reading Disc 2: " + n),
                                 cancelled, wanted=("content",))
        finally:
            iso.close()
        disc2 = work / "disc2"
    for p in sorted(disc2.rglob("*")):
        # the car packs: STFS packages of FM4 (title 4D530910, content type 2)
        if p.is_file() and p.stat().st_size > 1 << 20 and p.name.lower().startswith("4d530910"):
            packages.append(p)
    if not packages:
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
