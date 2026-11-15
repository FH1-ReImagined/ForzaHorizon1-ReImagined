"""Drag tires for every car (Mods > Drag tires, off by default; #924).

FH1's own cars have no drag tire, and the FM4 cars import leaves FM4's out. With this on, a start
gets a hidden mod, mods/.reimagined-drag-tires. tools/drag_tires.py builds it from the game's own
files: a database patch that gives every car a drag tire upgrade (tire compound 10, the sixth tire
of Custom Upgrade, from warshack4874's drag tire mod), and upgrades.str with its name and
description in every language. Every car means FH1's own, every installed DLC or fan pack's, and
those of the FM4 cars import.

The mod loads first, so a player's own mod with an upgrades.str wins. It is rebuilt only when the
data file, the builder, the game's title update, the installed DLC or the FM4 cars import change.
Off: no mod, no drag tire. A car with the drag tire fitted then does not load (the game gives
another car), so the Mods page asks first (OFF_WARNING). A failure never stops the game from
starting: it starts without them, and the log says why.
"""
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from . import datafolder, mods, paths

MOD = ".reimagined-drag-tires"
MARKER = "drag_tires.json"
# What turning it off does to a car with the drag tire fitted (seen in the game: the save's car did
# not load, and the game spawned another car); the Mods page asks with this first.
OFF_WARNING = ("A car with the drag tire fitted does not load without it: the game gives you another car "
               "instead. Fit another tire compound to those cars first (Custom Upgrade), or turn drag tires "
               "back on to get them back.\n\nTurn drag tires off?")


def enabled(cfg) -> bool:
    return bool((cfg.d.get("drag_tires") or {}).get("enabled"))


def set_enabled(cfg, on: bool):
    cfg.d.setdefault("drag_tires", {})["enabled"] = bool(on)
    cfg.save()


def _tools():
    paths.ensure_tools_importable()
    import drag_tires
    return drag_tires


def _fm4_gamedb(cfg):
    """The FM4 cars import's database (its cars get the tire too), or None."""
    from . import fm4cars
    p = mods.mods_dir(cfg) / fm4cars.MOD / "media" / "db" / "gamedb.slt"
    return p if p.is_file() else None


def sources(cfg):
    """(game folder, title update folder or None, DLC folder or None, FM4 import database or None)."""
    from . import fm4cars
    update = fm4cars._update_root(cfg)
    dlc = Path(cfg.data_home()) / datafolder.DLC_DIR / "00000002"
    return Path(cfg.game_files()), update, (dlc if dlc.is_dir() else None), _fm4_gamedb(cfg)


def _stat(p) -> str:
    try:
        s = Path(p).stat()
        return f"{p}|{s.st_size}|{s.st_mtime_ns}"
    except OSError:
        return f"{p}|-"


def fingerprint(tools, cfg, game, update, dlc, fm4db) -> str:
    h = hashlib.sha256()
    for f in (tools.DATA, tools.__file__):
        h.update(Path(f).read_bytes())
    h.update(str(game).encode())
    for p in ((Path(update) / "media.zip") if update else None, fm4db):
        h.update(_stat(p).encode() if p else b"-")
    for name in sorted(datafolder.dlc_packages(cfg.data_home())):
        h.update(name.encode() + b"\n")
    return h.hexdigest()


def ensure_mod(cfg, log=print):
    """The hidden mod's folder name when it is on and ready, else None. Builds or rebuilds it as
    needed; any failure leaves the game without drag tires (and says why in the log)."""
    target = mods.mods_dir(cfg) / MOD
    if not enabled(cfg):
        return None
    try:
        tools = _tools()
        game, update, dlc, fm4db = sources(cfg)
        stamp = fingerprint(tools, cfg, game, update, dlc, fm4db)
        try:
            if json.loads((target / MARKER).read_text(encoding="utf-8")).get("stamp") == stamp:
                return MOD
        except (OSError, ValueError):
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".drag-", dir=target.parent) as temp:
            staged = Path(temp) / MOD
            fh1 = tools.fm4_cars.FH1(str(game), str(update) if update else None, str(dlc) if dlc else None)
            try:
                report = tools.build(fh1, str(staged), [str(fm4db)] if fm4db else [])
            finally:
                fh1.close()
            (staged / "mod.toml").write_text('name = "Drag tires"\n', encoding="utf-8")
            (staged / MARKER).write_text(json.dumps({"stamp": stamp, "cars": report["cars"],
                                                     "languages": report["languages"]}), encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            staged.rename(target)
        log(f"Drag tires: every car has one ({report['cars']} cars)")
        return MOD
    except Exception as e:  # noqa: BLE001 - a drag tire failure must never stop the game from starting
        log(f"Drag tires: {e}; the game starts without them")
        return None


def status(cfg):
    """What the built mod holds ({"cars", "languages"}), or None."""
    try:
        return json.loads((mods.mods_dir(cfg) / MOD / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def launch_args(cfg, args: list, write=True, log=print):
    """Puts the mod first in --forza_mods (the player's mods after it win). A plan that writes
    nothing (a preview) names the mod only when it is already built."""
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
