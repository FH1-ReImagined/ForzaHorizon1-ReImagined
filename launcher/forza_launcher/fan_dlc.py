"""DLC installed for the game that is not one of its own packages (#66, #76, #115, #771).

The fan-made "Forza Horizon - Unicorn cars unlock (by Madmatoster)" pack is the package with offer 1975
(Media/1975.puboffer), which no official package has: with it the first Single Player load never
finishes (docs/dlc.md). Setup's "Unrecognized DLC packages" warning only reads the folder typed into
the DLC field, so a player who installed the pack earlier was never told. This module reads the DLC the
launcher installed (the data folder's 0000000000000000/4D5309C9/00000002, where the game finds it) with
the same knowledge (tools/install_dlc.py KNOWN_NAMES), leaves out the packages the launcher makes itself
(Import FM4 cars, offer 64800), and moves a package out of the installed DLC into a folder next to it:
nothing is deleted, and that folder can be installed again from Setup."""
import os
from dataclasses import dataclass
from pathlib import Path

from . import datafolder, paths

paths.ensure_tools_importable()
import install_dlc  # noqa: E402

FAN_OFFER = "1975"
MOVED = "DLC moved out"   # next to 00000002 and Headers, same layout (Setup > Install DLC takes it back)

# The plain sentence of every message about the pack (Setup, Play, the install log; the game's log too,
# forza-recomp/src/debug/dlc_log.cpp).
FAN_EFFECT = ("is the fan-made unicorn cars pack (offer 1975), not one of the game's DLC: "
              "with it the first Single Player load never finishes (the loading screen runs for ever).")
UNICORNS = ("For the unicorn cars without the pack: Saves > Edit profile… > Unicorn cars > "
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


def offers(package: Path) -> frozenset:
    """The offers of an extracted package folder: Media/<offer>.puboffer, names in any case (as the game)."""
    out = set()
    try:
        for media in os.scandir(package):
            if media.name.lower() != "media" or not media.is_dir():
                continue
            for f in os.scandir(media.path):
                if f.name.lower().endswith(".puboffer"):
                    out.add(f.name[:-len(".puboffer")])
    except OSError:
        pass
    return frozenset(out)


def is_fan_folder(package) -> bool:
    return FAN_OFFER in offers(Path(package))


def is_fan_entries(paths) -> bool:
    """Whether a package's file list (STFS entry paths, '/' separated) has Media/1975.puboffer."""
    want = f"media/{FAN_OFFER}.puboffer"
    return any(p.replace("\\", "/").lower().lstrip("/") == want for p in paths)


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
        display = (install_dlc.header_display_name(str(hdr_dir / (name + ".header")))
                   or known.get(name.upper()) or name)
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
            "DLC into a folder next to it: nothing is deleted, and moving it back restores it. " + UNICORNS)


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


def moved_root(home, name) -> Path:
    """The folder a package moves to: DLC moved out, or DLC moved out (2), (3)... when that one already
    holds a package or header of that name (nothing there is overwritten)."""
    base = Path(home) / datafolder.DLC_DIR
    for i in range(1, 1000):
        root = base / (MOVED if i == 1 else f"{MOVED} ({i})")
        if not (root / "00000002" / name).exists() and not (root / "Headers" / "00000002" / (name + ".header")).exists():
            return root
    raise MoveError(f"Every \"{MOVED}\" folder in {base} already holds {name}.")


def move_out(home, name) -> Path:
    """Move package `name` (its folder, and its header when it has one) out of the installed DLC to
    moved_root()/00000002 and moved_root()/Headers/00000002. Returns that root. Renames only (no copy,
    nothing deleted); if a step fails, what moved is put back and MoveError says what happened."""
    pkg_dir, hdr_dir = dlc_dirs(home)
    src, src_hdr = pkg_dir / name, hdr_dir / (name + ".header")
    if not src.is_dir():
        raise MoveError(f"{name} is not in {pkg_dir}.")
    root = moved_root(home, name)
    dst, dst_hdr = root / "00000002" / name, root / "Headers" / "00000002" / (name + ".header")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dst)
    except OSError as e:
        raise MoveError(f"Could not move {name} to {dst.parent} ({e}). Nothing was moved: close the game "
                        "if it is running and try again.")
    if src_hdr.exists():
        try:
            dst_hdr.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src_hdr, dst_hdr)
        except OSError as e:
            try:
                os.rename(dst, src)
            except OSError as e2:
                raise MoveError(f"Could not move {src_hdr.name} ({e}), and the package folder could not be "
                                f"put back ({e2}): it is in {dst.parent}, its header still in {hdr_dir}.")
            raise MoveError(f"Could not move {src_hdr.name} to {dst_hdr.parent} ({e}). Nothing was moved: "
                            "close the game if it is running and try again.")
    return root


def moved_text(packages, root, home) -> str:
    """What the launcher says after moving the pack out."""
    pkg_dir, _hdr_dir = dlc_dirs(home)
    names = ", ".join(p.display for p in packages)
    folders = ", ".join(p.name for p in packages)
    return (f"Moved {names} out of the installed DLC to {root} (nothing was deleted). To restore it, pick "
            f"that folder in Setup's DLC field and press Install DLC, or move {folders} back into {pkg_dir} "
            f"and its .header file into the Headers{os.sep}00000002 folder beside it.")
