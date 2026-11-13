"""Import Forza Horizon 2's worlds into FH1 free roam from the player's own copies (#821, experimental).

Forza Horizon 2 Presents Fast & Furious (Xbox 360) is FH2's southern Europe cut to an area round
Nice; FH2's own world is the whole of it. tools/fh2ff_world.py makes either a world FH1's free
roam drives (docs/fh2ff-world.md); this runs it from the launcher and keeps the result where the
FM4 circuits are (fm4tracks.py), so the Play page's picker offers it and a start in it works as
one on a circuit:

  <data>/fm4-tracks/<key>/              the world (key "anthem" F&F's, "fh2" FH2's, "fh2docks" FH2's
                                        with F&F's docks, made first by tools/fh2ff_combine.py):
                                        media/tracks/Anthem (its archive, ribbon, collision, road
                                        network), its sound bank stand-ins, media/zipmanifest.xml,
                                        spawn.txt, track.json (kind fh2ff or fh2)
  <data>/mods/.fm4-track-<key>/         its database (free roam loads the world), game modes,
                                        lighting files and, with FH2's files, the map's art

What a start in it sets differs from a circuit's (fm4tracks.configure): the game's plain-file
switch for the world's changed files and the start on its road (FORZA_INSECURE_FILES,
FORZA_TRACK_SPAWN), and a circuit's collision and no-traffic switches off (FORZA_FM4_COL=0,
FORZA_TRACK_NO_TRAFFIC=0: the world's fm4_stream_blocks.txt, which sizes the game's streaming
pool, would turn them on): the world's collision streams as FH1's own does and traffic drives its
roads. The game folder is never changed; nothing is downloaded and nothing leaves this PC.
"""
import json
import tempfile
import time
from pathlib import Path

from . import fm4cars, fm4tracks

FOLDER = "Anthem"                 # both worlds' folder (FH2's codename); the game's track key "anthem"
TOOL_KEY = "anthem"               # the tool's output: fm4-tracks/anthem and mods/fh2ff-anthem
TOOL_MOD = "fh2ff-anthem"
FORMAT = 1
FF_TITLE, FH2_TITLE = 0x4D530AB5, 0x4D530AA4

# key: (track.json kind, name, the game's title id)
WORLDS = {"anthem": ("fh2ff", "Fast & Furious", FF_TITLE), "fh2": ("fh2", "Forza Horizon 2", FH2_TITLE),
          # FH2's world with Fast & Furious's docks in it (tools/fh2ff_combine.py); FH2's files lead
          "fh2docks": ("fh2docks", "Forza Horizon 2 + Fast & Furious docks", FH2_TITLE),
          # and with Fast & Furious's version of its whole area (fh2ff_combine.py --all)
          "fh2ffall": ("fh2ffall", "Forza Horizon 2 + Fast & Furious", FH2_TITLE)}
COMBINED = "fh2docks"
COMBINED_ALL = "fh2ffall"
COMBINED_KEYS = (COMBINED, COMBINED_ALL)
KINDS = {kind for kind, _, _ in WORLDS.values()}
KEY = "anthem"                    # F&F's (the first import)
KIND = WORLDS[KEY][0]
NAME = WORLDS[KEY][1]

EXPERIMENTAL = ("Experimental. What works: free roam in Fast & Furious's world (FH2's Nice and its "
                "coast) or Forza Horizon 2's whole world, with their roads, buildings, lighting and "
                "collision, traffic on their roads, the minimap and, with FH2's files, the full map. Not "
                "yet: their own events and sounds (FH1's Colorado ones stand in), and FH2's far east "
                "(past about 8 km east of its centre FH1 does not stream it). A start in one plays "
                "offline, and your save is backed up first.")


class Fh2ffError(fm4tracks.Fm4TrackError):
    pass


def _world(root: Path):
    return fm4cars._find_ci(root, "media", "tracks", FOLDER)


def _title(root: Path):
    xex = fm4cars._find_ci(root, "default.xex")
    return fm4cars._xex_title(xex) if xex and xex.is_file() else None


def disc_root(folder, key=KEY) -> Path:
    """The folder of the game's files (the one with media/) for world `key`, from the folder the
    player chose: that one or a folder in it. Raises Fh2ffError in plain words when it is not that
    game's (its default.xex names the game when it is there)."""
    _, name, title = WORLDS[key]
    game = "Forza Horizon 2 Presents Fast & Furious" if key == "anthem" else name
    p = Path(folder)
    for root in [p] + ([c for c in sorted(p.iterdir()) if c.is_dir()] if p.is_dir() else []):
        world = _world(root)
        if not (world and (world / "bin.zip").is_file() and fm4cars._find_ci(world, "Ribbon_00")
                and fm4cars._find_ci(root, "media", "db", "gamedb.slt")):
            continue
        found = _title(root)
        if found is not None and found != title:
            other = next((n for _, n, t in WORLDS.values() if t == found), f"another game (title id {found:08X})")
            raise Fh2ffError(f"{p} holds {other}'s files, not {game}'s.")
        return root
    raise Fh2ffError(f"{p} does not hold {game}'s files (media/tracks/Anthem with its bin.zip and "
                     "Ribbon_00, and media/db/gamedb.slt). Choose the folder of the game's files, as "
                     "extracted from your own copy.")


def fh2_root(folder):
    """FH2's own files (for its road types and map art), or None when the player chose none."""
    if not folder:
        return None
    root = disc_root(folder, "fh2")
    if fm4cars._find_ci(root, "media", "ui", "textures", "Horizon.zip") is None:
        raise Fh2ffError(f"{folder} does not look like Forza Horizon 2's files (no media/ui/textures/Horizon.zip). "
                         "Choose FH2's folder, or none.")
    return root


def installed(cfg, key=KEY):
    info = fm4tracks.find(cfg, key)
    return info if info and info.get("kind") == WORLDS[key][0] else None


def build(cfg, ff_folder, fh2_folder=None, progress=lambda f, text: None, cancelled=lambda: False,
          runner=fm4tracks.run_tool, key=KEY):
    """Converts a world (key "anthem": F&F's from ff_folder, with FH2's road types and map art
    from fh2_folder when given; "fh2": FH2's own from ff_folder) and installs it in place of an
    earlier import of it (kept until the new one is complete). Returns a message for the player."""
    kind, name, _ = WORLDS[key]
    added = None
    if key in COMBINED_KEYS:          # FH2's files (ff_folder) lead, F&F's (fh2_folder) give the docks
        src = fh2 = fh2_root(ff_folder)
        added = disc_root(fh2_folder, "anthem")
    elif key == "fh2":
        src = fh2 = fh2_root(ff_folder)
    else:
        src, fh2 = disc_root(ff_folder), fh2_root(fh2_folder)
    game = Path(cfg.game_files())
    if not (game / "default.xex").is_file() or not (game / "media" / "zipmanifest.xml").is_file():
        raise Fh2ffError("FH1's game files are not set up yet: open Setup first.")
    update = fm4cars._update_root(cfg)
    if update is None:
        raise Fh2ffError("FH1's Title Update 4 (its media.zip) was not found: the world needs the Title "
                         "Update 4 build. Set it up in Setup.")
    root = fm4tracks.store(cfg)
    root.mkdir(parents=True, exist_ok=True)
    have = fm4tracks.find(cfg, key)
    if have and have.get("kind") != kind:
        raise Fh2ffError(f"An imported circuit is named {key} already: remove it first.")
    # FH2's archive stores a file once for each zone that streams it: the world takes about a
    # third of it; F&F's about its size
    fm4tracks._need_space(root, fm4tracks._tree_size(_world(src)) * {"fh2": 1, COMBINED: 2, COMBINED_ALL: 2}.get(key, 3))
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=root))
    lines = [0]

    def say(line):
        lines[0] += 1  # the tool prints some 25 lines; the bar moves with them
        progress(min(0.95, lines[0] / (50 if added else 30)), line[:120])
    try:
        out = work / "out"
        if added:
            progress(0.0, "Putting Fast & Furious's " + ("area" if key == COMBINED_ALL else "docks")
                     + " into Forza Horizon 2's world (a few minutes)…")
            env = {k: v for k, v in fm4tracks._tool_env().items() if not k.startswith("FH2FF_")}
            runner(fm4tracks.tool_command("fh2ff_combine.py", src, added, work / "combined",
                                          *(("--all",) if key == COMBINED_ALL else ())), env, say, cancelled)
            src = work / "combined"
        progress(0.4 if added else 0.0, f"Converting {name}'s world (a few minutes)…")
        runner(fm4tracks.tool_command("fh2ff_world.py", "build", src, game, Path(update) / "media.zip", out,
                                      *(("--fh2", fh2) if fh2 else ())),
               fm4tracks._tool_env(), say, cancelled)
        staged = out / "fm4-tracks" / TOOL_KEY
        mod = out / "mods" / TOOL_MOD
        if not fm4cars._find_ci(staged, "media", "tracks", FOLDER) or not (mod / "media" / "db" / "gamedb.slt").is_file():
            raise Fh2ffError("The converter did not write the world and its database.")
        try:
            spawn = (staged / fm4tracks.SPAWN_FILE).read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            raise Fh2ffError("The converter found no start on the world's roads.") from None
        (mod / "mod.toml").write_text(f'name = "{name} world"\ndescription = "Made by Import {name} from '
                                      'your own copies of the games. Never share it."\n', encoding="utf-8")
        info = {"format": FORMAT, "kind": kind, "key": key, "folder": FOLDER, "name": name, "ribbon": 0,
                "spawn": spawn, "fh2": fh2 is not None, "imported": time.strftime("%Y-%m-%d %H:%M"),
                "size": fm4tracks._tree_size(staged) + fm4tracks._tree_size(mod)}
        (staged / fm4tracks.MARKER).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        if cancelled():
            from .fetch import Cancelled
            raise Cancelled()
        fm4tracks._publish(cfg, key, staged, mod)
    finally:
        fm4cars._remove(work)
    progress(1.0, "Imported.")
    if added:
        return (f"Imported {name} ({fm4tracks.size_text(info['size'])}). Choose it on the Play page to drive "
                "it (experimental).")
    return (f"Imported {name}'s world ({fm4tracks.size_text(info['size'])})"
            + ("" if fh2 else ", without FH2's map art and road types (choose FH2's files too to add them)")
            + ". Choose it on the Play page to drive it (experimental).")


def configure(info, env: dict, notes: list):
    """A start in a world (fm4tracks.configure calls this for its kinds): its own switches."""
    env["FORZA_INSECURE_FILES"] = "zipmanifest.xml|\\tracks\\" + FOLDER.lower() + "\\"
    if info.get("spawn"):
        env["FORZA_TRACK_SPAWN"] = info["spawn"]
    env["FORZA_FM4_COL"] = "0"
    env["FORZA_TRACK_NO_TRAFFIC"] = "0"
    notes.append(f"Starting in {info.get('name', NAME)}'s world (experimental, offline). Choose FH1's "
                 "Colorado on the Play page to drive in Colorado again.")
