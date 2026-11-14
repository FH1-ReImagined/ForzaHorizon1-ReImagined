"""Import Forza Motorsport 4's circuits into FH1 from the player's own FM4 Disc 1 (#648).

Experimental and off unless chosen: an imported circuit takes the place of Colorado for one
start (free drive and timed laps on the circuit, no events yet). Play with "FH1's Colorado"
(the default) starts the game exactly as without any import.

The import runs the converter (tools/fm4_track_mod.py, then tools/fm4_pvs.py, as
docs/festival_track_spike.md describes; docs/fm4-tracks.md is the players' page) for each
chosen circuit and keeps the result in the data folder:

  <data>/fm4-tracks/<track>/            one imported circuit
      media/tracks/<track>/...          the converted track (bin.zip, ribbons, collision,
                                        fm4_stream_blocks.txt): loose files of the game,
                                        with its road network (<track>.nav, tools/fm4_routes.py)
      media/audio/tracks/<Track>/...    the circuit's ambience sound banks (Colorado's)
      media/zipmanifest.xml             FH1's archive list with the track's bin.zip in it
      stx/<track>_stx_*.bin             FM4's static texture pack numbers (FORZA_FM4_STX)
      spawn.txt                         the start on the grid, from FM4's AI line (FORZA_TRACK_SPAWN)
      lap.txt                           the lap line and sectors for the game's lap timer
      track.json                        what was imported: name, size, start position
  <data>/fm4-tracks/laps/<track>.jsonl  the laps the game timed there (kept when it is removed),
      <track>.ghost.txt                 and the best lap's trace, time attack's ghost
  <data>/mods/.fm4-track-<track>/       its database (free roam loads the circuit), game modes
                                        and race route, a mod the Mods page does not list

A start on a circuit passes --fm4_track_dir=<data>/fm4-tracks/<track>: the game mounts the
folder at game:\\media\\tracks\\<track> (with its sound banks and zip manifest) and gives the
converted track's switches their defaults (forza-recomp/src/hooks/fm4_track_mount.cpp; a
FORZA_* variable set in the extra environment still wins, for development). The track's mod
loads last (with the database of a mod that changes it too, such as the FM4 cars, merged in),
and the start is offline: other players are in Colorado. The game folder is never changed.
Nothing is downloaded and nothing leaves this PC.
"""
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import cleanenv, mods, paths
from . import fm4cars
from .config import _atomic_write
from .fetch import Cancelled
from .fm4cars import Fm4Error

STORE = "fm4-tracks"
LAP_FILE = "lap.txt"              # the lap line and sectors for the game's lap timer
LAPS = "laps"                     # <data>/fm4-tracks/laps/<track>.jsonl: the game's counted laps
SPAWN_FILE = "spawn.txt"         # x,y,z,fx,fz for the game (fm4_track_mount.cpp)
MARKER = "track.json"
MOD_PREFIX = ".fm4-track-"
MERGED_MOD = ".fm4-track-db"      # the track's database rows over another mod's database
FORMAT = 1
FREE_ROAM_TRACK = 317             # TU4's Tracks row free roam loads (fm4_track_mod.py --free-roam)
DEFAULT = ""                      # the choice "FH1's Colorado"
COLORADO = "FH1's Colorado (default)"
SPAWN_BACK = 15.0                 # metres behind the start line (tools/fm4_spawn.py)

# What players see. Folders not named here are shown split at their capitals.
NAMES = {
    "Alps": "Bernese Alps", "AmalfiGP": "Amalfi Coast", "Catalunya": "Circuit de Catalunya",
    "CrownJewel": "Crown Jewel", "FujimiKaido": "Fujimi Kaido", "Hockenheim": "Hockenheim",
    "Indi": "Indianapolis", "Infin": "Infineon", "LagunaSeca": "Laguna Seca", "LeMans": "Le Mans",
    "MapleValley": "Maple Valley", "Nurburgring": "Nürburgring Nordschleife",
    "NurburgringF1": "Nürburgring GP", "RoadAmerica": "Road America", "RoadAtlanta": "Road Atlanta",
    "SunsetInfield": "Sunset Peninsula", "TopGear": "Top Gear Test Track",
}
NOT_CIRCUITS = re.compile(r"(?i)^(homespace\d*|ui.*)$")  # FM4's garage sets and menu scenes

EXPERIMENTAL = ("Experimental. What works: free driving on the circuit, with its buildings, stands "
                "and collision, and timed laps (lap, sectors and best lap on the game's HUD, kept "
                "per circuit), your car's shadow, the circuit's road on the minimap. Not yet: events "
                "and races, a choice of car (you drive the car you last used in Colorado), the "
                "circuit's own shadows, AI cars and traffic, the map screen and GPS. "
                "Suzuka is the most tested circuit. "
                "A start on a circuit plays offline, and your save is backed up first.")
EXPERIMENTAL_SHORT = ("Experimental: free driving and timed laps on the circuit (no events "
                      "yet). You drive the car you last used in Colorado, so choose it there "
                      "first: there is no car choice on a circuit yet. It plays offline, and "
                      "your save is backed up first.")

# Disc 1's files the converter reads besides the circuits' folders.
DISC1_COMMON = ("default.xex", "Media/db", "Media/physics", "Media/AI/Tracks")


class Fm4TrackError(Fm4Error):
    pass


def display_name(folder: str) -> str:
    return NAMES.get(folder) or re.sub(r"(?<=[a-z])(?=[A-Z])", " ", folder)


def key_of(folder: str) -> str:
    """The game's name of a circuit (its track folder): FM4's folder in lower case."""
    return folder.lower()


def store(cfg) -> Path:
    return Path(cfg.data_home()) / STORE


def mod_folder(key: str) -> str:
    return MOD_PREFIX + key


def _gb(n):
    return f"{n / (1 << 30):.1f} GB" if n >= 1 << 30 else f"{n / (1 << 20):.0f} MB"


def size_text(n: int) -> str:
    return _gb(n)


def _tree_size(p: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


# ---- What is installed --------------------------------------------------------------------------

def installed(cfg) -> list:
    """The imported circuits, by name: each its track.json (key, folder, name, size, spawn...)."""
    out = []
    try:
        dirs = sorted(store(cfg).iterdir())
    except OSError:
        return out
    for d in dirs:
        if d.name.startswith(".") or not d.is_dir():
            continue
        try:
            info = json.loads((d / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(info, dict) or info.get("key") != d.name:
            continue
        info["path"] = str(d)
        out.append(info)
    return sorted(out, key=lambda i: i.get("name", i["key"]).lower())


def find(cfg, key: str):
    return next((i for i in installed(cfg) if i["key"] == key), None)


def total_size(cfg) -> int:
    return sum(int(i.get("size", 0)) for i in installed(cfg))


def selected(cfg) -> str:
    """The circuit chosen for Play ("" for Colorado). A choice whose import is gone is Colorado."""
    s = cfg.d.get("fm4_tracks")
    key = s.get("selected", "") if isinstance(s, dict) else ""
    if not isinstance(key, str) or not key:
        return DEFAULT
    return key if find(cfg, key) else DEFAULT


def select(cfg, key: str):
    if key and not find(cfg, key):
        raise Fm4TrackError(f"{key} is not imported.")
    s = cfg.d.get("fm4_tracks")
    if not isinstance(s, dict):
        s = cfg.d["fm4_tracks"] = {}
    s["selected"] = key or DEFAULT
    cfg.save()


def choices(cfg) -> list:
    """[(key, label)] for the picker: Colorado first."""
    return [(DEFAULT, COLORADO)] + [(i["key"], f"{i['name']} ({'FH2 world' if i.get('kind') in ('fh2ff', 'fh2') else 'FM4'}, "
                                               "experimental)") for i in installed(cfg)]


def remove(cfg, key: str):
    """Deletes an imported circuit (its folder and its database mod). Play goes back to Colorado
    when it was the chosen one."""
    if not re.fullmatch(r"[a-z0-9_]+", key or ""):
        raise Fm4TrackError(f"{key!r} is not an imported circuit.")
    d = store(cfg) / key
    m = mods.mods_dir(cfg) / mod_folder(key)
    if d.exists() and not (d / MARKER).is_file():
        raise Fm4TrackError(f"{d} was not made by Import FM4 tracks: it is left alone.")
    if selected(cfg) == key:
        select(cfg, DEFAULT)
    for p in (m, d):
        if p.exists() and not fm4cars._remove(p):
            raise Fm4TrackError(f"{p} could not be deleted: a program has a file of it open.")


# ---- The disc -----------------------------------------------------------------------------------

def _circuit_folders(names) -> list:
    """FM4's circuits among a disc's paths (lower case or as stored): the folders of
    media/tracks that hold a bin.zip and a Ribbon_NN layout, as stored."""
    found, ribbons = {}, set()
    for n in names:
        parts = n.replace("\\", "/").split("/")
        if len(parts) >= 4 and parts[0].lower() == "media" and parts[1].lower() == "tracks":
            folder = parts[2]
            if NOT_CIRCUITS.match(folder):
                continue
            if len(parts) == 4 and parts[3].lower() == "bin.zip":
                found[folder.lower()] = folder
            if re.fullmatch(r"(?i)ribbon_\d\d", parts[3]):
                ribbons.add(folder.lower())
    return sorted((found[k] for k in found if k in ribbons), key=str.lower)


class Disc:
    """FM4's Disc 1 as chosen: a folder with its files, or a disc image or Games on Demand
    package whose files are read as needed (into work). circuits: FM4's track folders."""

    def __init__(self, root: Path | None, volume_kind=None, volume_path=None, circuits=(), sizes=None,
                 work: Path | None = None):
        self.root, self.kind, self.path = root, volume_kind, volume_path
        self.circuits, self.sizes, self.work = list(circuits), dict(sizes or {}), work

    def size_of(self, folder) -> int:
        return self.sizes.get(folder.lower(), 0)

    def folder(self, names, progress, cancelled) -> Path:
        """The disc's folder holding DISC1_COMMON and the circuits `names` (read from the image
        into work when it is one)."""
        if self.root is not None and self.kind is None:
            return self.root
        out = self.work / "disc1"
        volume = fm4cars._open_volume(self.kind, self.path)
        try:
            have = {e.path.replace("\\", "/").lower() for e in volume.entries()}
            wanted = [p for p in DISC1_COMMON if p.lower() in have or
                      any(h.startswith(p.lower() + "/") for h in have)]
            todo = [f"Media/tracks/{n}" for n in names if not (out / "Media" / "tracks" / n).is_dir()]
            fm4cars._extract(volume, out, wanted + todo, "Reading Disc 1: ", progress, cancelled)
        finally:
            fm4cars._close(volume)
        return out


def open_disc(disc1, work: Path, progress=lambda n, t, s: None, cancelled=lambda: False) -> Disc:
    """Reads what Disc 1 holds (an image is not unpacked yet: its circuits are listed from its
    file table). Raises Fm4Error in plain words when the choice is not FM4's Disc 1."""
    paths.ensure_tools_importable()
    found = fm4cars._choice(disc1, 1, work, {}, progress, cancelled)
    errors = []
    for kind, path in found.discs:
        try:
            if kind == "folder":
                fm4cars._check_disc1(path)
                tracks = fm4cars._find_ci(path, "Media", "tracks")
                listed, sizes = [], {}
                if tracks:
                    for d in sorted(tracks.iterdir()):
                        if d.is_dir():
                            for f in d.iterdir():
                                listed.append(f"media/tracks/{d.name}/{f.name}")
                for c in _circuit_folders(listed):
                    sizes[c.lower()] = _tree_size(tracks / c)
                return Disc(path, circuits=_circuit_folders(listed), sizes=sizes, work=work)
            volume = fm4cars._open_volume(kind, path)
            try:
                entries = volume.entries()
                names = {e.path.replace("\\", "/") for e in entries}
                low = {n.lower() for n in names}
                if "default.xex" not in low:
                    if any(x.startswith("content/") for x in low):
                        raise Fm4Error(fm4cars.DISC2_AS_DISC1)
                    raise Fm4Error(f"{path.name} is not Forza Motorsport 4's Disc 1: it has no default.xex. "
                                   + fm4cars.CHOOSE[1])
                xex_dir = work / "disc1"
                fm4cars._extract(volume, xex_dir, ("default.xex",), "Reading Disc 1: ", progress, cancelled)
                fm4cars._check_xex(xex_dir / "default.xex")
                if "media/db/gamedb.slt" not in low:
                    raise Fm4Error(f"{path.name} is not Forza Motorsport 4's Disc 1: it has no Media/db/gamedb.slt.")
                circuits = _circuit_folders(names)
                sizes = {}
                for e in entries:
                    parts = e.path.replace("\\", "/").split("/")
                    if not e.is_dir and len(parts) >= 4 and parts[0].lower() == "media" and parts[1].lower() == "tracks":
                        sizes[parts[2].lower()] = sizes.get(parts[2].lower(), 0) + e.size
            finally:
                fm4cars._close(volume)
            return Disc(None, kind, path, circuits, sizes, work)
        except Fm4Error as e:
            errors.append(str(e))
    if errors:
        raise Fm4Error(fm4cars.DISC2_AS_DISC1 if fm4cars.DISC2_AS_DISC1 in errors else errors[0])
    if found.packs or found.unpacked:
        raise Fm4Error(f"{found.label} holds Forza Motorsport 4's Disc 2 car packs, not Disc 1. The "
                       "circuits are on Disc 1 (the play disc).")
    raise Fm4Error(fm4cars._nothing(found, 1))


# ---- Converting ---------------------------------------------------------------------------------

def tool_command(script: str, *args) -> list:
    """The command that runs a converter tool: this launcher runs it when frozen (--run-script)."""
    d = paths.tools_dir()
    if d is None or not (d / script).is_file():
        raise Fm4TrackError(f"The converter ({script}) is missing from this launcher.")
    return [sys.executable] + (["--run-script"] if paths.FROZEN else []) + [str(d / script)] + [str(a) for a in args]


def _tool_env(extra=None):
    env = cleanenv.clean_environment()
    # the converter's test switches stay out of a player's import: its defaults are the tested ones
    for k in list(env):
        if k.startswith(("FM4_", "FORZA_")):
            del env[k]
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.update(extra or {})
    return env


def run_tool(cmd, env, on_line, cancelled, cwd=None):
    """Runs a converter step; each line it prints goes to on_line. A cancel stops the process.
    Raises Fm4TrackError with its last lines when it fails."""
    kw = {}
    if paths.IS_WINDOWS:
        kw["creationflags"] = 0x08000000  # no console window
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=env, cwd=cwd, text=True, encoding="utf-8", errors="replace", **kw)
    except OSError as e:
        raise Fm4TrackError(f"The converter could not start: {e}") from None
    tail = []

    def read():
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                del tail[:-20]
                on_line(line)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    while proc.poll() is None:
        if cancelled():
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            reader.join(5)
            raise Cancelled()
        time.sleep(0.2)
    reader.join(5)
    if proc.returncode != 0:
        last = "\n".join(tail[-6:]) or f"exit code {proc.returncode}"
        raise Fm4TrackError(f"The converter stopped with an error:\n{last}")


def spawn_of(disc_root: Path, folder: str, ribbon: int):
    """The grid spawn x,y,z,fx,fz from FM4's AI line of the ribbon free roam loads, or None."""
    paths.ensure_tools_importable()
    import fm4_spawn
    ai = fm4cars._find_ci(disc_root, "Media", "AI", "Tracks")
    if not ai:
        return None
    geos = {p.name.lower(): p for p in ai.iterdir() if p.suffix.lower() == ".geo"}
    want = f"{folder.lower()}_{ribbon:02d}.geo"
    geo = geos.get(want) or next((geos[n] for n in sorted(geos)
                                  if re.fullmatch(re.escape(folder.lower()) + r"_\d\d\.geo", n)), None)
    if geo is None:
        return None
    try:
        pts = fm4_spawn.waypoints(str(geo))
        x, y, z, fx, fz = fm4_spawn.place(pts, 0, SPAWN_BACK)
    except (SystemExit, OSError, KeyError, ValueError, IndexError):
        return None
    return f"{x:.1f},{y:.1f},{z:.1f},{fx:.3f},{fz:.3f}"


def _ensure_festival_node(track_dir: Path, spawn):
    """FH1's free-roam startup dereferences this Colorado node on imported circuits too."""
    # The converter maps the free-roam layout to Ribbon_00, even when it started as Ribbon_01.
    ribbon = fm4cars._find_ci(track_dir, "Ribbon_00") or track_dir / "Ribbon_00"
    path = fm4cars._find_ci(ribbon, "gameobjs.xml") or ribbon / "gameobjs.xml"
    try:
        root = ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))).getroot()
    except FileNotFoundError:
        root = ET.Element("GameObjs")
    except ET.ParseError as exc:
        raise Fm4TrackError("Could not read the circuit's GameObjs.xml. Import the circuit again.") from exc
    if root.tag != "GameObjs":
        raise Fm4TrackError("Could not read the circuit's GameObjs.xml. Import the circuit again.")
    node_id = "FESTIVAL_SECOND_NODE"
    if any(obj.get("GameplayID") == node_id for obj in root):
        return
    try:
        x, y, z, fx, fz = map(float, (spawn or "").split(","))
        if not all(map(math.isfinite, (x, y, z, fx, fz))):
            raise ValueError
    except ValueError as exc:
        raise Fm4TrackError("The circuit has no valid grid position. Import it again from FM4 Disc 1.") from exc
    length = math.hypot(fx, fz)
    fx, fz = (fx / length, fz / length) if length else (0, 1)
    last = max((int(obj.tag[3:]) for obj in root
                if isinstance(obj.tag, str) and re.fullmatch(r"Obj[0-9]+", obj.tag)), default=-1)
    node = ET.SubElement(root, f"Obj{last + 1}", GameplayID=node_id)
    ET.SubElement(node, "Pos", x=str(x), y=str(y), z=str(z))
    orientation = ET.SubElement(node, "Orientation")
    for axis, vector in (("XAxis", (fz, 0, -fx)), ("YAxis", (0, 1, 0)), ("ZAxis", (fx, 0, fz))):
        ET.SubElement(orientation, axis, dict(zip("xyz", (f"{v:.6f}" for v in vector))))
    _atomic_write(path, ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n")


def lap_line(disc_root: Path, folder: str, ribbon: int, name: str):
    """lap.txt for the game's lap timer (tools/fm4_timing.py: FM4's AI line, its sectors and
    length), or None when the disc has no AI line for the circuit."""
    paths.ensure_tools_importable()
    import fm4_timing
    try:
        return fm4_timing.build(str(disc_root), folder, ribbon, name)
    except (SystemExit, OSError, ValueError, KeyError, IndexError, sqlite3.Error):
        return None


def laps(cfg, key: str) -> list:
    """The counted laps on circuit `key`, oldest first: the game adds one JSON object a line to
    <data>/fm4-tracks/laps/<track>.jsonl (time and sectors in seconds, date, lap, best)."""
    out = []
    try:
        text = (store(cfg) / LAPS / f"{key}.jsonl").read_text(encoding="utf-8")
    except OSError:
        return out
    for row in text.splitlines():
        try:
            lap = json.loads(row)
        except ValueError:
            continue
        if isinstance(lap, dict) and isinstance(lap.get("time"), (int, float)) and lap["time"] > 0:
            out.append(lap)
    return out


def lap_circuits(cfg) -> list:
    """[(key, name)] of the imported circuits and of those with laps (removed since), by name."""
    names = {i["key"]: i.get("name", i["key"]) for i in installed(cfg) if i.get("kind") not in ("fh2ff", "fh2")}  # no lap timer in a world
    try:
        for p in (store(cfg) / LAPS).glob("*.jsonl"):
            lower = {k.lower(): v for k, v in NAMES.items()}
            names.setdefault(p.stem, lower.get(p.stem, p.stem.capitalize()))
    except OSError:
        pass
    return sorted(names.items(), key=lambda kv: kv[1].lower())


def best_lap(cfg, key: str):
    times = [lap["time"] for lap in laps(cfg, key)]
    return min(times) if times else None


def lap_text(seconds) -> str:
    if seconds is None:
        return ""
    cs = round(seconds * 100)
    return f"{cs // 6000}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def free_roam_ribbon(track_dir: Path) -> int:
    """The layout free roam loads: Ribbon_00, else the first (fm4_track_mod.ribbon_as_zero)."""
    nums = sorted(int(d.name[7:]) for d in track_dir.iterdir()
                  if d.is_dir() and re.fullmatch(r"(?i)ribbon_\d\d", d.name))
    return 0 if not nums or 0 in nums else nums[0]


def _need_space(where: Path, need: int):
    try:
        free = shutil.disk_usage(where).free
    except OSError:
        return
    if need + (512 << 20) > free:
        raise Fm4TrackError(f"Importing these circuits needs about {_gb(need)} of free space on the drive "
                            f"of the data folder, and it has {_gb(free)}. Choose fewer circuits or free "
                            "some space.")


def estimate(disc: Disc, names) -> tuple:
    """(bytes the imported circuits take, bytes needed while importing) for `names`: about 3x
    a circuit's folder on the disc once converted, 7x the largest while one converts."""
    sizes = [disc.size_of(n) or (100 << 20) for n in names]
    kept = sum(int(s * 3.2) for s in sizes)
    return kept, kept + (max(sizes) * 7 if sizes else 0)


def build(cfg, disc: Disc, names, progress=lambda f, text: None, cancelled=lambda: False, runner=run_tool):
    """Converts and installs the circuits `names` (FM4 folder names) of `disc`. A circuit imported
    before is replaced once its new conversion is complete. Returns a message for the player."""
    if not names:
        raise Fm4TrackError("Choose at least one circuit.")
    game = Path(cfg.game_files())
    if not (game / "default.xex").is_file() or not (game / "media" / "zipmanifest.xml").is_file():
        raise Fm4TrackError("FH1's game files are not set up yet: open Setup first.")
    update = fm4cars._update_root(cfg)
    if update is None:
        raise Fm4TrackError("FH1's Title Update 4 (its media.zip) was not found: FM4 circuits need the "
                            "Title Update 4 build. Set it up in Setup.")
    root = store(cfg)
    root.mkdir(parents=True, exist_ok=True)
    _, need = estimate(disc, names)
    _need_space(root, need)
    for old in root.glob(".work-*"):  # an earlier import's, its cleanup refused
        if old.is_dir() and not old.is_symlink():
            fm4cars._remove(old)
    work = Path(tempfile.mkdtemp(prefix=".work-", dir=root))
    steps = len(names) * 3 + 1
    done = [0]

    def step(text, inner=0.0):
        progress(min(1.0, (done[0] + inner) / steps), text)

    imported, failed = [], []
    try:
        src = disc.folder(names, lambda d, t, n: step(n, d / t if t else 0), cancelled)
        done[0] = 1
        for folder in names:
            if cancelled():
                raise Cancelled()
            name = display_name(folder)

            def circuit_step(k, text, name=name):  # k: converter steps finished with this line
                done[0] += k
                step(f"{name}: {text}")
            try:
                _build_one(cfg, src, folder, game, Path(update), work / key_of(folder), name,
                           circuit_step, cancelled, runner)
                imported.append(name)
            except Fm4TrackError as e:
                failed.append(f"{name}: {e}")
            except Cancelled:
                raise
            finally:
                shutil.rmtree(work / key_of(folder), ignore_errors=True)
            done[0] = 1 + 3 * (names.index(folder) + 1)
    finally:
        fm4cars._remove(work)  # when a program still holds a file of it, the next import deletes it
    if not imported:
        raise Fm4TrackError("No circuit could be imported. " + " ".join(failed[:3]))
    msg = (f"Imported {len(imported)} Forza Motorsport 4 circuit(s): {', '.join(imported)}. Choose one "
           "on the Play page to drive it (experimental).")
    if failed:
        msg += f" {len(failed)} could not be converted: " + "; ".join(failed[:3])
    return msg


def _build_one(cfg, disc_root: Path, folder, game: Path, update: Path, work: Path, name, step, cancelled, runner):
    key = key_of(folder)
    track_src = fm4cars._find_ci(disc_root, "Media", "tracks", folder)
    db = fm4cars._find_ci(disc_root, "Media", "db", "gamedb.slt")
    if not track_src or not (track_src / "bin.zip").is_file():
        raise Fm4TrackError(f"Disc 1 has no Media/tracks/{folder}/bin.zip.")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    mod, loose, out = work / "mod", work / "loose", work / "out"
    env = _tool_env({"FM4_DISC": str(disc_root)})
    say = lambda line: step(0, line[:120])
    step(0, "reading the circuit")
    # FM4's AI lines: the circuit's road network and race route (tools/fm4_routes.py)
    ai = fm4cars._find_ci(disc_root, "Media", "AI", "Tracks")
    runner(tool_command("fm4_track_mod.py", track_src, update / "media.zip", mod, "--fm4-db", db or "",
                        "--free-roam", FREE_ROAM_TRACK, "--fh1-disc", game, "--loose", loose,
                        *(("--fm4-ai", ai) if ai else ())),
           env, say, cancelled)
    step(1, "converting the layout, zones and collision")
    runner(tool_command("fm4_pvs.py", "convert", mod, game, out), env, say, cancelled)
    step(1, "installing")
    staged = work / "staged"
    # the track: the converted loose files, then the mod's own track folder over them
    # (reconvert_track.sh: cp -r mod/media/tracks/<t>/. loose/media/tracks/<t>/)
    t_out = out / "media" / "tracks" / key
    t_mod = mod / "media" / "tracks" / key
    if not (t_out / "bin.zip").is_file() or not t_mod.is_dir():
        raise Fm4TrackError("The converter wrote no track archive (bin.zip).")
    dest = staged / "media" / "tracks" / key
    shutil.copytree(t_out, dest)
    shutil.copytree(t_mod, dest, dirs_exist_ok=True)
    manifest = out / "media" / "zipmanifest.xml"
    if not manifest.is_file():
        raise Fm4TrackError("The converter wrote no zipmanifest.xml.")
    shutil.copyfile(manifest, staged / "media" / "zipmanifest.xml")
    audio = loose / "media" / "audio" / "tracks"
    if audio.is_dir():
        shutil.copytree(audio, staged / "media" / "audio" / "tracks")
    (staged / "stx").mkdir()
    for f in out.glob(f"{key}_stx_*.bin"):
        shutil.copyfile(f, staged / "stx" / f.name)
    if not (staged / "stx" / f"{key}_stx_numbers.bin").is_file():
        raise Fm4TrackError("The converter wrote no static texture numbers.")
    # the database, game modes, race route and map profiles: a mod of their own (the cars' kind
    # of files; the game reads a route from its aiopenworld archive, which a mod's files are
    # laid over, and the map profiles are FH1's own with the roads in their driven colour)
    db_mod = work / "dbmod"
    for part in ("db", "gamemodes", "aiopenworld", "ui"):
        if (mod / "media" / part).is_dir():
            shutil.copytree(mod / "media" / part, db_mod / "media" / part)
    if not (db_mod / "media" / "db" / "gamedb.slt").is_file():
        raise Fm4TrackError("The converter wrote no database.")
    (db_mod / "mod.toml").write_text(f'name = "FM4 circuit: {name}"\n'
                                     'description = "Made by Import FM4 tracks from your own FM4 disc. Never share it."\n',
                                     encoding="utf-8")
    ribbon = free_roam_ribbon(track_src)
    spawn = spawn_of(disc_root, folder, ribbon)
    _ensure_festival_node(dest, spawn)
    info = {"format": FORMAT, "key": key, "folder": folder, "name": name, "ribbon": ribbon,
            "spawn": spawn, "imported": time.strftime("%Y-%m-%d %H:%M"),
            "size": _tree_size(staged) + _tree_size(db_mod)}
    (staged / MARKER).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    if info["spawn"]:
        (staged / SPAWN_FILE).write_text(info["spawn"] + "\n", encoding="utf-8")
    lap = lap_line(disc_root, folder, ribbon, name)
    if lap:
        (staged / LAP_FILE).write_text(lap, encoding="utf-8")
    if cancelled():
        raise Cancelled()
    _publish(cfg, key, staged, db_mod)


def _publish(cfg, key, staged: Path, db_mod: Path):
    """Moves the staged circuit and its mod in place of an earlier import of it."""
    target = store(cfg) / key
    mtarget = mods.mods_dir(cfg) / mod_folder(key)
    mtarget.parent.mkdir(parents=True, exist_ok=True)
    for t in (target, mtarget):
        if t.is_symlink():
            raise Fm4TrackError(f"{t} is a link, not made by Import FM4 tracks. Move it first.")
    if target.exists() and not (target / MARKER).is_file():
        raise Fm4TrackError(f"{target} already exists and was not made by Import FM4 tracks. Move it first.")
    old, mold = target.with_name("." + key + ".previous"), mtarget.with_name(mtarget.name + ".previous")
    for p in (old, mold):
        if p.exists():
            fm4cars._remove(p)
    had, mhad = target.exists(), mtarget.exists()
    try:
        if had:
            fm4cars._retry(target.rename, old)
        if mhad:
            fm4cars._retry(mtarget.rename, mold)
        fm4cars._move(str(staged), str(target))
        fm4cars._move(str(db_mod), str(mtarget))
    except BaseException:
        for t in (target, mtarget):
            if t.exists():
                fm4cars._remove(t)
        if had and old.exists():
            old.rename(target)
        if mhad and mold.exists():
            mold.rename(mtarget)
        raise
    fm4cars._remove(old)
    fm4cars._remove(mold)


# ---- Starting the game on a circuit ---------------------------------------------------------------

def _gamedb_of(mod_dir: Path):
    db = mod_dir / "media" / "db"
    if not db.is_dir():
        return None
    return next((p for p in db.iterdir() if p.name.lower() == "gamedb.slt"), None)


def merge_db(base_db: Path, track_db: Path, out_db: Path):
    """out_db: base_db (another mod's database) with the track's rows put in: its Environments row
    and free roam's Tracks row (tools/fm4_track_mod.write_db), and the Tracks rows of its own
    environment that are its alone (an FH2 world's races and arenas, tools/fh2ff_world.py)."""
    out_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base_db, out_db)
    src = sqlite3.connect(f"file:{track_db.as_posix()}?mode=ro", uri=True)
    try:
        row = src.execute("SELECT MediaName, EnvironmentId, RibbonIndex FROM Tracks WHERE id=?",
                          (FREE_ROAM_TRACK,)).fetchone()
        if not row:
            raise Fm4TrackError("The circuit's database has no free roam row: import it again.")
        cols = [r[1] for r in src.execute("PRAGMA table_info(Environments)")]
        env = src.execute("SELECT * FROM Environments WHERE Id=?", (row[1],)).fetchone()
        tcols = [r[1] for r in src.execute("PRAGMA table_info(Tracks)")]
        own = src.execute("SELECT * FROM Tracks WHERE EnvironmentId=? AND MediaName=? AND id<>?",
                          (row[1], row[0], FREE_ROAM_TRACK)).fetchall()
    finally:
        src.close()
    db = sqlite3.connect(out_db)
    try:
        if env:
            have = {r[1] for r in db.execute("PRAGMA table_info(Environments)")}
            pairs = [(c, v) for c, v in zip(cols, env) if c in have]
            db.execute(f"INSERT OR REPLACE INTO Environments ({','.join(c for c, _ in pairs)}) VALUES "
                       f"({','.join('?' * len(pairs))})", [v for _, v in pairs])
        db.execute("UPDATE Tracks SET MediaName=?, EnvironmentId=?, RibbonIndex=? WHERE id=?",
                   (*row, FREE_ROAM_TRACK))
        if own:
            have = {r[1] for r in db.execute("PRAGMA table_info(Tracks)")}
            keep = [i for i, c in enumerate(tcols) if c in have]
            at = tcols.index("id")
            db.execute(f"DELETE FROM Tracks WHERE id IN ({','.join('?' * len(own))})", [r[at] for r in own])
            db.executemany(f"INSERT INTO Tracks ({','.join(tcols[i] for i in keep)}) VALUES "
                           f"({','.join('?' * len(keep))})", [[r[i] for i in keep] for r in own])
        db.commit()
    finally:
        db.close()


def mods_for(cfg, key, write=True) -> list:
    """The mod folders to add after the player's mods for a start on circuit `key`: its own
    database mod, then (when a mod of the player's changes the database too, such as the FM4
    cars) that database with the circuit's rows merged in."""
    out = [mod_folder(key)]
    track_db = _gamedb_of(mods.mods_dir(cfg) / mod_folder(key))
    other = None
    for m in mods.enabled(cfg):
        if "db/gamedb.slt" in m.names:
            other = mods.mods_dir(cfg) / m.folder
    merged = mods.mods_dir(cfg) / MERGED_MOD
    if other is None or track_db is None:
        if write:
            _clear_dir(merged)
        return out
    if write:
        _clear_dir(merged)
        merge_db(_gamedb_of(other), track_db, merged / "media" / "db" / "gamedb.slt")
        (merged / "mod.toml").write_text(f'name = "FM4 circuit database over {other.name}"\n', encoding="utf-8")
    return out + [MERGED_MOD]


def _clear_dir(p: Path):
    if os.path.lexists(p):
        if os.path.islink(p):
            os.unlink(p)
        else:
            shutil.rmtree(p, ignore_errors=True)


def _ensure_map_profiles(cfg, key):
    """A circuit imported before the import wrote them gets FH1's two map profiles with the
    roads in their driven colour (tools/fm4_routes.py) in its mod, at its next start: the
    minimap's grey for a road not driven yet is lost on a circuit's tarmac. Never an error:
    without them the circuit starts as it did."""
    ui = mods.mods_dir(cfg) / mod_folder(key) / "media" / "ui"
    try:
        if ui.is_dir() or not ui.parent.is_dir():
            return
        paths.ensure_tools_importable()
        import fm4_routes
        src = fm4cars._find_ci(Path(cfg.game_files()), "media", "ui")
        files = fm4_routes.map_profiles(str(src)) if src else {}
        if not files:
            return
        # all of them or none: written beside, then moved in as the folder
        stage = Path(tempfile.mkdtemp(prefix=".ui-", dir=ui.parent))
        try:
            for rel, data in files.items():
                (stage / Path(rel).name).write_bytes(data)
            stage.rename(ui)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    except Exception:  # the profiles are a help, never a reason not to start
        pass


def configure(cfg, env: dict, args: list, unset: list, notes: list, write=True):
    """A start on the chosen circuit: the game mounts its folder (--fm4_track_dir, which also gives
    the converted track's switches their defaults; a FORZA_* variable in the extra environment
    still wins), its mod loads last, and it plays offline. Nothing at all for Colorado. Returns
    the circuit's track.json, or None."""
    key = selected(cfg)
    if not key:
        return None
    info = find(cfg, key)
    if info is None:
        return None
    folder = Path(info["path"])
    world = info.get("kind") in ("fh2ff", "fh2")  # an FH2 world (fh2ffworld.py): its own switches
    if write and not world:
        spawn_file = folder / SPAWN_FILE
        try:
            with spawn_file.open(encoding="utf-8") as f:
                saved_spawn = f.readline().strip()
        except FileNotFoundError:
            saved_spawn = ""
        spawn = saved_spawn or info.get("spawn")
        _ensure_festival_node(folder / "media" / "tracks" / key, spawn)
        if not saved_spawn and spawn:
            _atomic_write(spawn_file, spawn + "\n")
        _ensure_map_profiles(cfg, key)
    extra = mods_for(cfg, key, write)
    for i, a in enumerate(args):
        if a.startswith("--forza_mods="):
            have = [] if a == "--forza_mods=" + mods.NONE else a.split("=", 1)[1].split("|")
            args[i] = "--forza_mods=" + "|".join([m for m in have if m not in extra] + extra)
    args.append(f"--fm4_track_dir={folder}")
    # Other players are in Colorado: a circuit plays offline, and nothing of it goes online. No
    # server (so no online play, challenges or co-op results), no Discord activity (it would name
    # an area of Colorado), and the window skips the session's shader-list upload (fm4_track).
    env["FORZA_SERVER"] = ""
    if "FORZA_SERVER" in unset:
        unset.remove("FORZA_SERVER")
    if not any(a.startswith("--discord_presence") for a in args):
        args.append("--discord_presence=false")
    if world:
        from . import fh2ffworld
        fh2ffworld.configure(info, env, notes)
        return info
    notes.append(f"Starting on the Forza Motorsport 4 circuit {info['name']} (experimental, offline). "
                 "Choose FH1's Colorado on the Play page to drive in Colorado again.")
    return info
