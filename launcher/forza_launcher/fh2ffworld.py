"""Import Fast & Furious's world into FH1 free roam from the player's own copies (#821, experimental).

Forza Horizon 2 Presents Fast & Furious (Xbox 360) is FH2's southern Europe cut to an area round
Nice. tools/fh2ff_world.py makes it a world FH1's free roam drives (docs/fh2ff-world.md); this
runs it from the launcher and keeps the result where the FM4 circuits are (fm4tracks.py), so the
Play page's picker offers it and a start on it works as one on a circuit:

  <data>/fm4-tracks/anthem/             the world: media/tracks/Anthem (its archive, ribbon,
                                        collision, road network), its sound bank stand-ins,
                                        media/zipmanifest.xml, spawn.txt, track.json (kind fh2ff)
  <data>/mods/.fm4-track-anthem/        its database (free roam loads the world), game modes,
                                        lighting files and, with FH2's disc, the map's art

What a start on it sets differs from a circuit's (fm4tracks.configure): the game's plain-file
switch for the world's changed files and the start on its road (FORZA_INSECURE_FILES,
FORZA_TRACK_SPAWN), and nothing else: the world's collision streams as FH1's own does and traffic
drives its roads. The game folder is never changed; nothing is downloaded and nothing leaves this PC.
"""
import json
import shutil
import tempfile
import time
from pathlib import Path

from . import fm4cars, fm4tracks

KEY = "anthem"                    # F&F's world folder (FH2's codename), in lower case as the game names it
KIND = "fh2ff"                    # track.json's kind: a world, not an FM4 circuit
NAME = "Fast & Furious"
TOOL_MOD = "fh2ff-anthem"         # the mod's folder in the tool's output (tools/fh2ff_world.py MOD)
FORMAT = 1

EXPERIMENTAL = ("Experimental. What works: free roam in Fast & Furious's world (FH2's Nice and its "
                "coast) with its roads, buildings, lighting and collision, traffic on its roads, the "
                "minimap and, with FH2's disc, the full map. Not yet: its own events and sounds (FH1's "
                "Colorado ones stand in). A start in it plays offline, and your save is backed up first.")


class Fh2ffError(fm4tracks.Fm4TrackError):
    pass


def _world(root: Path):
    return fm4cars._find_ci(root, "media", "tracks", "Anthem")


def disc_root(folder) -> Path:
    """The folder of F&F's disc files (the one with media/), from the folder the player chose:
    that one or a folder in it. Raises Fh2ffError in plain words when it is not F&F's."""
    p = Path(folder)
    for root in [p] + ([c for c in sorted(p.iterdir()) if c.is_dir()] if p.is_dir() else []):
        world = _world(root)
        if world and (world / "bin.zip").is_file() and fm4cars._find_ci(world, "Ribbon_00"):
            if fm4cars._find_ci(root, "media", "db", "gamedb.slt") is None:
                break
            return root
    raise Fh2ffError(f"{p} does not hold Forza Horizon 2 Presents Fast & Furious's files (media/tracks/Anthem "
                     "with its bin.zip and Ribbon_00, and media/db/gamedb.slt). Choose the folder of the "
                     "game's files, as extracted from your own copy.")


def fh2_root(folder):
    """FH2's own disc files (for its road types and map art), or None when the player chose none.
    The same check as disc_root: FH2's world is media/tracks/Anthem too."""
    if not folder:
        return None
    root = disc_root(folder)
    if fm4cars._find_ci(root, "media", "ui", "textures", "Horizon.zip") is None:
        raise Fh2ffError(f"{folder} does not look like Forza Horizon 2's files (no media/ui/textures/Horizon.zip). "
                         "Choose FH2's folder, or none.")
    return root


def installed(cfg):
    info = fm4tracks.find(cfg, KEY)
    return info if info and info.get("kind") == KIND else None


def build(cfg, ff_folder, fh2_folder=None, progress=lambda f, text: None, cancelled=lambda: False,
          runner=fm4tracks.run_tool):
    """Converts F&F's world and installs it in place of an earlier import of it (kept until the
    new one is complete). Returns a message for the player."""
    ff = disc_root(ff_folder)
    fh2 = fh2_root(fh2_folder)
    game = Path(cfg.game_files())
    if not (game / "default.xex").is_file() or not (game / "media" / "zipmanifest.xml").is_file():
        raise Fh2ffError("FH1's game files are not set up yet: open Setup first.")
    update = fm4cars._update_root(cfg)
    if update is None:
        raise Fh2ffError("FH1's Title Update 4 (its media.zip) was not found: the world needs the Title "
                         "Update 4 build. Set it up in Setup.")
    root = fm4tracks.store(cfg)
    root.mkdir(parents=True, exist_ok=True)
    have = fm4tracks.find(cfg, KEY)
    if have and have.get("kind") != KIND:
        raise Fh2ffError(f"An imported circuit is named {KEY} already: remove it first.")
    world = _world(ff)
    fm4tracks._need_space(root, fm4tracks._tree_size(world) * 3)
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=root))
    lines = [0]

    def say(line):
        lines[0] += 1  # the tool prints some 25 lines; the bar moves with them
        progress(min(0.95, lines[0] / 30), line[:120])
    try:
        out = work / "out"
        progress(0.0, "Converting Fast & Furious's world (a few minutes)…")
        runner(fm4tracks.tool_command("fh2ff_world.py", "build", ff, game, Path(update) / "media.zip", out,
                                      *(("--fh2", fh2) if fh2 else ())),
               fm4tracks._tool_env(), say, cancelled)
        staged = out / "fm4-tracks" / KEY
        mod = out / "mods" / TOOL_MOD
        if not fm4cars._find_ci(staged, "media", "tracks", "Anthem") or not (mod / "media" / "db" / "gamedb.slt").is_file():
            raise Fh2ffError("The converter did not write the world and its database.")
        try:
            spawn = (staged / fm4tracks.SPAWN_FILE).read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            raise Fh2ffError("The converter found no start on the world's roads.") from None
        (mod / "mod.toml").write_text(f'name = "{NAME} world"\ndescription = "Made by Import Fast & Furious '
                                      'from your own copies of the games. Never share it."\n', encoding="utf-8")
        info = {"format": FORMAT, "kind": KIND, "key": KEY, "folder": "Anthem", "name": NAME, "ribbon": 0,
                "spawn": spawn, "fh2": fh2 is not None, "imported": time.strftime("%Y-%m-%d %H:%M"),
                "size": fm4tracks._tree_size(staged) + fm4tracks._tree_size(mod)}
        (staged / fm4tracks.MARKER).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        if cancelled():
            from .fetch import Cancelled
            raise Cancelled()
        fm4tracks._publish(cfg, KEY, staged, mod)
    finally:
        fm4cars._remove(work)
    progress(1.0, "Imported.")
    return (f"Imported {NAME}'s world ({fm4tracks.size_text(info['size'])})"
            + ("" if fh2 else ", without FH2's map art and road types (choose FH2's files too to add them)")
            + ". Choose it on the Play page to drive it (experimental).")


def configure(info, env: dict, notes: list):
    """A start in the world (fm4tracks.configure calls this for kind fh2ff): its own switches."""
    env["FORZA_INSECURE_FILES"] = "zipmanifest.xml|\\tracks\\" + KEY + "\\"
    if info.get("spawn"):
        env["FORZA_TRACK_SPAWN"] = info["spawn"]
    notes.append(f"Starting in {NAME}'s world (experimental, offline). Choose FH1's Colorado on the Play "
                 "page to drive in Colorado again.")

