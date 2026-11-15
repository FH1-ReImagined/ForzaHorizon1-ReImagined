"""DLC installed for the game that is not one of its own packages (#66, #76, #115, #771).

The fan-made "Forza Horizon - Unicorn cars unlock (by Madmatoster)" pack is the package with offer 1975
(Media/1975.puboffer), which no official package has: with it the first Single Player load never
finishes (docs/dlc.md). Setup's "Unrecognized DLC packages" warning only reads the folder typed into
the DLC field, so a player who installed the pack earlier was never told. This module reads the DLC the
launcher installed (the data folder's 0000000000000000/4D5309C9/00000002, where the game finds it) with
the same knowledge (tools/install_dlc.py KNOWN_NAMES), leaves out the packages the launcher makes itself
(Import FM4 cars, offer 64800), and moves a package out of the installed DLC into a folder next to it:
nothing is deleted. A Xenia save's copy of the installed DLC (game.share_dlc mirrors it when it cannot
link it) loses its copy of the package the same way, and datafolder.carry_dlc no longer copies a package
that was moved out back into the installed DLC."""
import errno
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import datafolder, paths

paths.ensure_tools_importable()
import install_dlc  # noqa: E402

FAN_OFFER = "1975"
MOVED = datafolder.DLC_MOVED_OUT   # next to 00000002 and Headers, same layout

# The plain sentence of every message about the pack (Setup, Play, the install log; the game's log too,
# forza-recomp/src/debug/dlc_log.cpp).
FAN_EFFECT = ("is the fan-made unicorn cars pack (offer 1975), not one of the game's DLC: "
              "with it the first Single Player load never finishes (the loading screen runs for ever).")
UNICORNS = ("For the unicorn cars without the pack: Library > Saves > Edit profile… > Unicorn cars > "
            "Add missing unicorns (with the game closed).")
MOVE_BUTTON = "Move the pack out"


@dataclass(frozen=True)
class Package:
    name: str          # the package folder
    display: str       # the display name in its header, or the known name, or the folder name
    offers: frozenset  # its Media/<offer>.puboffer files (offer numbers as text)

    @property
    def fan(self) -> bool:
        return FAN_OFFER in self.offers

    def label(self) -> str:
        return f"{self.display} ({self.name})" if self.display != self.name else self.name


def ours() -> set:
    """Package folders the launcher itself makes (not DLC the player brought)."""
    from .fm4cars import PACKAGE as FM4_CARS   # Import FM4 cars (#648)
    return {FM4_CARS.upper()}


def dlc_dirs(home):
    """(the installed packages' folder, their headers' folder) under the user data folder `home`."""
    base = Path(home) / datafolder.DLC_DIR
    return base / "00000002", base / "Headers" / "00000002"


_OFFER_RE = re.compile(r"[ \t\n\v\f\r]*([+-]?[0-9]+)")


def offer_number(stem: str) -> str:
    """The offer a <stem>.puboffer file stands for, read as the game's log reads it (atoi: leading digits,
    so 01975 and 1975x are 1975 too); a name with no number stays as it is."""
    m = _OFFER_RE.match(stem)
    return str(int(m.group(1))) if m else stem


def offers(package: Path) -> frozenset:
    """The offers of an extracted package folder: Media/<offer>.puboffer, names in any case (as the game)."""
    out = set()
    try:
        with os.scandir(package) as it:
            media_dirs = [m.path for m in it if m.name.lower() == "media" and m.is_dir()]
        for media in media_dirs:
            with os.scandir(media) as it:
                for f in it:
                    if f.name.lower().endswith(".puboffer"):
                        out.add(offer_number(f.name[:-len(".puboffer")]))
    except OSError:
        pass
    return frozenset(out)


def is_fan_folder(package) -> bool:
    return FAN_OFFER in offers(Path(package))


def is_fan_entries(paths) -> bool:
    """Whether a package's file list (STFS entry paths, '/' separated) has Media/1975.puboffer."""
    for p in paths:
        parts = p.replace("\\", "/").lower().lstrip("/").split("/")
        if (len(parts) == 2 and parts[0] == "media" and parts[1].endswith(".puboffer")
                and offer_number(parts[1][:-len(".puboffer")]) == FAN_OFFER):
            return True
    return False


def header_file(hdr_dir: Path, name: str):
    """The header of package `name` in `hdr_dir`: <name>.header, or a file of that name in another case
    (a copy made on Windows); None when there is none."""
    exact = hdr_dir / (name + ".header")
    if exact.is_file():
        return exact
    want = (name + ".header").lower()
    try:
        with os.scandir(hdr_dir) as it:
            for e in it:
                if e.name.lower() == want and e.is_file():
                    return Path(e.path)
    except OSError:
        pass
    return None


def unrecognized(home) -> list:
    """[Package] installed under `home` that are not one of the game's known packages, and every package
    with offer 1975 whatever its folder is called; the launcher's own packages are left out. The pack
    first, then by name. A missing or unreadable folder is no packages; an unreadable header is a name."""
    pkg_dir, hdr_dir = dlc_dirs(home)
    known, mine = install_dlc.KNOWN_NAMES, ours()
    out = []
    for name in sorted(datafolder.dlc_packages(home)):
        p = Package(name, "", offers(pkg_dir / name))
        if name.upper() in mine and not p.fan:
            continue
        if name.upper() in known and not p.fan:
            continue
        hdr = header_file(hdr_dir, name)
        display = (hdr is not None and install_dlc.header_display_name(str(hdr))) or known.get(name.upper()) or name
        out.append(Package(name, display, p.offers))
    return sorted(out, key=lambda p: (not p.fan, p.name.upper()))


def fan_packages(home) -> list:
    return [p for p in unrecognized(home) if p.fan]


def fan_text(packages, installed=True) -> str:
    """What happens with the pack and what to do (Setup and Play)."""
    names = ", ".join(p.label() for p in packages)
    if not installed:
        return f"{names} {FAN_EFFECT}"
    return (f"Installed DLC: {names} {FAN_EFFECT} Press \"{MOVE_BUTTON}\" to move it out of the installed "
            "DLC into a folder next to it: nothing is deleted, and moving it back by hand restores it. " + UNICORNS)


def unknown_text(packages) -> str:
    """The installed packages that are not known and not the pack (Setup)."""
    if not packages:
        return ""
    return ("Installed DLC that is not one of the game's known packages:\n" +
            "\n".join(f"- {p.label()}" for p in packages) +
            "\nUnofficial or modified packs may cause endless loading. If the game hangs while loading, "
            f"move these out of {datafolder.DLC_DIR}{os.sep}00000002 in the data folder first.")


class MoveError(Exception):
    pass


def moved_root(home, name, header_name=None) -> Path:
    """The folder a package moves to: DLC moved out, or DLC moved out (2), (3)... when that one already
    holds a package or header of that name (nothing there is overwritten)."""
    base = Path(home) / datafolder.DLC_DIR
    header_name = header_name or name + ".header"
    for i in range(1, 1000):
        root = base / (MOVED if i == 1 else f"{MOVED} ({i})")
        if not (root / "00000002" / name).exists() and not (root / "Headers" / "00000002" / header_name).exists():
            return root
    raise MoveError(f"Every \"{MOVED}\" folder in {base} already holds {name}.")


def _mkdirs(d: Path) -> list:
    """Makes folder `d` and the ones above it; returns the folders it made (the deepest last)."""
    made = []
    for p in reversed([d, *d.parents]):
        if not os.path.lexists(p):
            os.mkdir(p)
            made.append(p)
    return made


def _rmdirs(made):
    """Removes the folders _mkdirs made, if they are still empty (a failed move leaves nothing behind)."""
    for p in reversed(made):
        try:
            os.rmdir(p)
        except OSError:
            break


def _why(e: OSError, src_dir: Path, dst_dir: Path) -> str:
    """The end of a MoveError: what failed and what to do."""
    if e.errno == errno.EXDEV:
        return (f": {src_dir} is on another drive than {dst_dir} ({e}). Nothing was moved: move it out of "
                f"{src_dir} by hand.")
    return f" ({e}). Nothing was moved: close the game if it is running and try again."


def _move(home, name, where="") -> Path:
    """move_out for one user data root."""
    pkg_dir, hdr_dir = dlc_dirs(home)
    src, src_hdr = pkg_dir / name, header_file(hdr_dir, name)
    if not src.is_dir():
        raise MoveError(f"{name} is not in {pkg_dir}.")
    root = moved_root(home, name, src_hdr.name if src_hdr is not None else None)
    dst = root / "00000002" / name
    made = []
    try:
        made = _mkdirs(dst.parent)
        os.rename(src, dst)
    except OSError as e:
        _rmdirs(made)
        raise MoveError(f"Could not move {name}{where} to {dst.parent}" + _why(e, pkg_dir, dst.parent))
    if src_hdr is not None:
        dst_hdr = root / "Headers" / "00000002" / src_hdr.name   # its own name: nothing is renamed
        made_hdr = []
        try:
            made_hdr = _mkdirs(dst_hdr.parent)
            os.rename(src_hdr, dst_hdr)
        except OSError as e:
            _rmdirs(made_hdr)
            try:
                os.rename(dst, src)
            except OSError as e2:
                raise MoveError(f"Could not move {src_hdr.name}{where} ({e}), and the package folder could not "
                                f"be put back ({e2}): it is in {dst.parent}, its header still in {hdr_dir}.")
            _rmdirs(made)
            raise MoveError(f"Could not move {src_hdr.name}{where} to {dst_hdr.parent}"
                            + _why(e, hdr_dir, dst_hdr.parent))
    return root


def mirrors(home) -> list:
    """The Xenia save roots under `home` (xenia-<XUID>, game.prepare_xenia) whose 0000000000000000 is a
    copy of the installed DLC rather than a link to it (game.share_dlc mirrors it when it cannot link)."""
    home = Path(home)
    target = home / "0000000000000000"
    out = []
    try:
        with os.scandir(home) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if not e.name.startswith("xenia-") or not e.is_dir(follow_symlinks=False):
            continue
        dlc = Path(e.path) / "0000000000000000"
        if (os.path.islink(dlc) or getattr(os.path, "isjunction", lambda _p: False)(dlc)
                or not dlc.is_dir()):
            continue
        try:
            if os.path.samefile(dlc, target):
                continue
        except OSError:
            pass
        out.append(Path(e.path))
    return out


def move_out(home, name) -> Path:
    """Move package `name` (its folder, and its header when it has one) out of the installed DLC to
    moved_root()/00000002 and moved_root()/Headers/00000002. Returns that root. Renames only (no copy,
    nothing deleted); if a step fails, what moved is put back and MoveError says what happened.
    The copies the Xenia saves' mirrors hold go first, the same way inside each mirror: when one fails
    the installed package stays and the notice with it (the next Play mirrors it again anyway)."""
    for m in mirrors(home):
        if (dlc_dirs(m)[0] / name).is_dir():
            _move(m, name, f" (the copy of the installed DLC for the Xenia save {m.name})")
    return _move(home, name)


def restorable(name) -> bool:
    """Whether Setup's Install DLC takes the package folder `name` back: a package name (hex) only."""
    return bool(install_dlc.NAME_RE.fullmatch(name))


def moved_text(packages, root, home) -> str:
    """What the launcher says after moving the pack out."""
    pkg_dir, _hdr_dir = dlc_dirs(home)
    names = ", ".join(p.display for p in packages)
    folders = ", ".join(p.name for p in packages)
    by_hand = (f"move {folders} back into {pkg_dir} and its .header file into the Headers{os.sep}00000002 "
               "folder beside it")
    if all(restorable(p.name) for p in packages):
        how = f"pick that folder in Setup's DLC field and press Install DLC, or {by_hand}."
    else:   # the installer names a folder after the file it came from; Install DLC skips other names
        how = f"{by_hand} (Install DLC does not take a folder of that name)."
    return f"Moved {names} out of the installed DLC to {root} (nothing was deleted). To restore it, {how}"
