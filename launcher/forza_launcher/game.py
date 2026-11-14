"""Finding the game binary and starting it the way tools/play.sh and play.bat do."""
import glob
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import TITLE_ID, cleanenv, gpu, paths, dlss, fsr3, power, tuning_status
from .config import Config, parse_toml, render_toml, write_toml

EXE = "forza.exe" if paths.IS_WINDOWS else "forza"
LOCAL_PROFILE = "B13EBABEBABEBABE"  # the runtime's own profile folder
LOCAL_SERVER = "127.0.0.1:21200"
LOCAL_HTTP_PORT = 21201


# --- locating the game ----------------------------------------------------------------

@dataclass
class GameInstall:
    dir: Path
    exe: Path
    lib_dirs: list = field(default_factory=list)  # added to LD_LIBRARY_PATH (Linux)
    source: str = ""


def _sdk_lib_dirs():
    """The SDK libraries of a source build, like play.sh (REXSDK, patched overlay, nightly)."""
    home = Path.home()
    for c in (os.environ.get("REXSDK"),
              home / "rexglue-sdk-patched-forza/0.10.0.24/linux-amd64",
              home / "rexglue-sdk-patched/0.10.0.24/linux-amd64",
              home / "rexglue-sdk-nightly/0.10.0.24/linux-amd64"):
        if c and (Path(c) / "lib").is_dir():
            return [Path(c) / "lib"]
    return []


SET_IN_ADVANCED = "set in Advanced settings"
CHECKOUT_BUILD = "the build in this checkout"


def checkout_build_name() -> str:
    """The checkout's build folder under forza-recomp/out/build: FORZA_BUILD, as in play.sh; set
    but empty counts as unset (it named out/build itself)."""
    return os.environ.get("FORZA_BUILD", "").strip() or "linux"


def candidate_dirs(cfg: Config):
    override = cfg.advanced.get("game_dir", "").strip() or os.environ.get("FORZA_GAME_DIR", "")
    if override:
        yield Path(override), SET_IN_ADVANCED, False
    from . import builder
    yield builder.runtime_dir(cfg.data_home()), "built by the launcher", False
    if paths.in_checkout() and not paths.IS_WINDOWS:  # development: this checkout's own build
        yield paths.REPO_ROOT / "forza-recomp/out/build" / checkout_build_name(), CHECKOUT_BUILD, True


def find_game(cfg: Config):
    """Returns (GameInstall | None, [places looked in])."""
    looked = []
    for d, why, needs_sdk in candidate_dirs(cfg):
        looked.append(f"{d} ({why})")
        exe = d / EXE
        if exe.is_file():
            return GameInstall(d, exe, _sdk_lib_dirs() if needs_sdk else [], why), looked
    return None, looked


def outside_build(cfg: Config) -> GameInstall | None:
    """The game Play starts when it is not the launcher's own build: the Advanced game_dir (or
    FORZA_GAME_DIR) override, or this source checkout's build (never seen by a frozen or installed
    launcher). The launcher's build state (builder.build_status) does not describe it."""
    install, _looked = find_game(cfg)
    if install is not None and getattr(install, "source", "") in (SET_IN_ADVANCED, CHECKOUT_BUILD):
        return install
    return None


def outside_build_text(install: GameInstall) -> str:
    """The Setup page's Build step for outside_build()."""
    if install.source == CHECKOUT_BUILD:
        return (f"Using the build in this checkout ({install.dir}). Build makes the launcher's own build "
                "in the data folder, which Play uses instead from then on.")
    return (f"Using the game set in Advanced settings ({install.dir}). Play starts it, not the "
            "launcher's own build, while that is set.")


def missing_message(looked):
    return ("The game has not been built on this computer yet. Open Setup and press Build.\n"
            "Looked in:\n  " + "\n  ".join(looked))


def game_version(game: GameInstall | None) -> str:
    """The build the package was made from: version.txt, else 'Built from commit X' in README.txt."""
    if game is None:
        return "not installed"
    for name in ("version.txt", "README.txt"):
        try:
            text = (game.dir / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if name == "version.txt" and text.strip():
            return text.strip().splitlines()[0]
        m = re.search(r"Built from commit (\w+)", text)
        if m:
            return "build " + m.group(1)
    commit = source_commit()
    if commit:
        return "source build " + commit
    return "unknown build"


def source_commit() -> str:
    """The checkout's short commit when the launcher runs from source, else ""."""
    if not paths.in_checkout():
        return ""
    try:
        out = subprocess.run(["git", "-C", str(paths.REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             env=cleanenv.clean_environment())
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def installed_dlc(cfg: Config) -> int:
    d = cfg.data_home() / "0000000000000000" / TITLE_ID / "00000002"
    try:
        return sum(1 for p in d.iterdir() if p.is_dir())
    except OSError:
        return 0


# --- the Forza Live server --------------------------------------------------------------

def server_script(game: GameInstall | None) -> Path | None:
    for d in ((game.dir,) if game else ()) + (paths.app_dir(), paths.REPO_ROOT):
        p = d / "server" / "forza_live_server.py"
        if p.is_file():
            return p
    return None


def server_listening(port=LOCAL_HTTP_PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def server_command(script: Path, data_dir: Path, game_files: Path | None = None):
    args = ["--host", "127.0.0.1", "--port", LOCAL_SERVER.split(":")[1],
            "--http-port", str(LOCAL_HTTP_PORT), "--data-dir", str(data_dir)]
    # The festival events (the Co-op Festival's /event, the Horizon World's events) come from
    # the game's database. The server only looks for it in a source checkout (extracted/game
    # beside server/); without it the Co-op Festival is not offered at all (#67).
    db = game_files / "media" / "db" / "gamedb.slt" if game_files else None
    if db and db.is_file():
        args += ["--game-db", str(db)]
    if paths.FROZEN:  # run the server inside this executable (no Python needed)
        return [sys.executable, "--live-server", str(script)] + args
    return [sys.executable, str(script)] + args


# --- the Xenia save -----------------------------------------------------------------------

def default_xenia_content() -> Path:
    return Path(os.environ.get("FORZA_XENIA_CONTENT") or Path.home() / "xenia_canary_windows" / "content")


def xenia_candidates(content: Path):
    """XUIDs of Xenia profiles with a Forza Horizon save."""
    out = []
    for d in sorted(glob.glob(str(content / "*" / TITLE_ID / "00000001"))):
        xuid = Path(d).parent.parent.name
        if xuid != "0000000000000000":
            out.append(xuid)
    return out


def _link_dir(link: Path, target: Path):
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    if paths.IS_WINDOWS:  # a junction needs no privilege
        try:
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           capture_output=True, creationflags=0x08000000)
        except (OSError, subprocess.SubprocessError):
            pass


def prepare_xenia(cfg: Config, refresh=False, log=print):
    """Copies the Xenia save (once; again with refresh) and returns (root, xuid)."""
    content = Path(cfg.profile.get("xenia_content") or default_xenia_content())
    xuid = cfg.profile.get("xenia_xuid", "").strip().upper()
    if not xuid:
        cands = xenia_candidates(content)
        if len(cands) != 1:
            raise RuntimeError(f"Found {len(cands)} Xenia profiles with a Forza Horizon save in "
                               f"{content}; choose one under Settings > Profile.")
        xuid = cands[0]
    if not re.fullmatch(r"[0-9A-F]{16}", xuid):
        raise RuntimeError("The Xenia XUID must be 16 hex digits.")
    src = content / xuid / TITLE_ID
    if not src.is_dir():
        raise RuntimeError(f"No Forza Horizon save at {src}")
    home = cfg.data_home()
    root = home / f"xenia-{xuid}"
    dest = root / LOCAL_PROFILE / TITLE_ID
    if refresh or not dest.is_dir():
        shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        log(f"Copied the Xenia save of {xuid} to {dest}")
    # Installed DLC lives with the normal profile; share it.
    share_dlc(home, root, log)
    return root, xuid


def _is_link(p: Path) -> bool:
    return os.path.islink(p) or bool(getattr(os.path, "isjunction", lambda _p: False)(p))


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _remove_link(p: Path):
    try:
        os.unlink(p)
    except OSError:
        os.rmdir(p)      # a Windows junction or directory symlink


def _mirror(src: Path, dst: Path) -> int:
    """Makes `dst` hold every file of `src` (hard links where the disk allows, else copies); files
    already there with the same size are kept. Returns the number of files added. On Windows the
    paths take the long form: a DLC car pack's files nest past MAX_PATH (#586)."""
    src, dst = paths.long_path(src), paths.long_path(dst)
    n = 0
    for dirpath, _dirs, files in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        for f in files:
            s, d = Path(dirpath) / f, dst / rel / f
            if d.is_file() and d.stat().st_size == s.stat().st_size:
                continue
            d.parent.mkdir(parents=True, exist_ok=True)
            if d.exists():
                d.unlink()
            try:
                os.link(s, d)
            except OSError:
                shutil.copy2(s, d)
            n += 1
    return n


def share_dlc(home: Path, root: Path, log=print) -> str:
    """Makes the DLC installed in `home` (home/0000000000000000) visible to a game started on the user
    data root `root` (a Xenia save's copy): a link (Linux), a symbolic link or junction (Windows) at
    root/0000000000000000. Repairs a link that points elsewhere (a folder that moved) or a real folder
    left in its place (its packages are first copied to `home`, then it is renamed, never deleted).
    When no link can be made, mirrors the packages with hard links. Returns "linked" or "mirrored"."""
    from . import datafolder
    target = home / "0000000000000000"
    target.mkdir(parents=True, exist_ok=True)
    link = root / "0000000000000000"
    if _same_dir(link, target):
        return "linked"
    how = Path("..") / "0000000000000000" if not paths.IS_WINDOWS else target
    if os.path.lexists(link) and _is_link(link):
        log(f"{link} pointed to another folder; linking it to {target}")
        _remove_link(link)
    backup = None
    if os.path.lexists(link):
        # A real folder: keep its packages, then move it aside.
        datafolder.carry_dlc(root, home, log)
        backup = link.with_name("0000000000000000.old")
        i = 1
        while os.path.lexists(backup):
            backup, i = link.with_name(f"0000000000000000.old{i}"), i + 1
        os.rename(link, backup)
    _link_dir(link, how)
    if _same_dir(link, target):
        if backup is not None:
            log(f"moved the folder {link} aside to {backup} and linked {link} to {target}")
        return "linked"
    if os.path.lexists(link) and _is_link(link):
        _remove_link(link)
    if backup is not None and not os.path.lexists(link):
        os.rename(backup, link)
    n = _mirror(target, link)
    log(f"could not link {link} to {target}: mirrored the DLC there instead ({n} files)")
    return "mirrored"


# --- the launch plan -------------------------------------------------------------------------

@dataclass
class LaunchPlan:
    game: GameInstall
    cwd: Path
    argv: list
    env: dict            # variables to set
    unset: list          # variables to remove
    toml_path: Path
    toml_text: str
    server_argv: list | None = None   # a local Forza Live server to start (when not running)
    notes: list = field(default_factory=list)
    log_dir: Path | None = None
    dlss_status_file: Path | None = None
    haptics_settings: dict = field(default_factory=dict)
    save_profile: object = None
    log_before_start: tuple | None = None  # identify a leftover log if this process never writes one
    roll_before_start: tuple | None = None  # the same for its roll (forza.1.log, session_roll)
    fm4_track: str = ""  # the FM4 circuit this start drives on (#648), "" for Colorado

    def command_line(self) -> str:
        return " ".join(shlex.quote(a) for a in self.argv)


def split_args(text: str):
    text = text.strip()
    if not text:
        return []
    if paths.IS_WINDOWS:
        return [a.strip('"') for a in shlex.split(text, posix=False)]
    return shlex.split(text)


def parse_env(text: str):
    env = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def content_warnings(cfg: Config, game: GameInstall, home: Path, root: Path, extra_update=False,
                     check_dlc=True) -> list:
    """What the player set up but the game will not find: Title Update 4 chosen but this build has
    no update (the base game starts: no Horizon Rally, no 1000 Club), or DLC installed or chosen but
    none in the user data folder the game is started on (Horizon Rally then says "Xbox LIVE
    Marketplace error"; issues #65, #70), or DLC there without the Rally Expansion Pack (the same
    error, #79), or a known DLC package there that is damaged or incomplete (dlccheck: the Rally pack's
    bad copy crashes Kettle Hills stage 1, #422)."""
    from . import datafolder, dlccheck
    from .fm4cars import PACKAGE as FM4_CARS  # Import FM4 cars' own package is not DLC (#648)
    out = []
    if cfg["tu_package"].strip() and not extra_update and not (game.dir / "update").is_dir():
        out.append(f"Title Update 4 is chosen in Setup, but this build has none ({game.dir / 'update'} is "
                   "missing): the game starts without Horizon Rally and the 1000 Club. Open Setup and press "
                   "Rebuild.")
    if not check_dlc:
        return out
    seen = datafolder.dlc_packages(root) - {FM4_CARS}
    installed = datafolder.dlc_packages(home) - {FM4_CARS}
    if not seen and (installed or cfg["dlc_dir"].strip()):
        where = f"{root / datafolder.DLC_DIR / '00000002'}"
        out.append(f"No DLC in the game's folder ({where})" +
                   (f", though {len(installed)} package(s) are installed in {home}" if installed else "") +
                   ": the game starts without its DLC cars, and Horizon Rally says \"Xbox LIVE Marketplace "
                   "error\". Open Setup and install the DLC again (step 2).")
    elif seen:
        bare = datafolder.dlc_without_header(root)
        if bare:
            out.append(f"{len(bare)} DLC package(s) have no header in {root / datafolder.DLC_DIR / 'Headers'} "
                       f"({', '.join(bare[:3])}{' ...' if len(bare) > 3 else ''}): install the DLC again in Setup.")
        has_update = extra_update or (game.dir / "update").is_dir()
        if has_update and "1600" not in datafolder.dlc_offers(root):
            out.append(f"The Rally Expansion Pack is not among the {len(seen)} DLC package(s) in "
                       f"{root / datafolder.DLC_DIR / '00000002'}: Horizon Rally says \"Xbox LIVE Marketplace "
                       "error\" (issue #79). Open Setup and install the DLC again (step 2) with the Rally "
                       "Expansion Pack.")
        for name, problem, effect in dlccheck.installed_problems(root):
            out.append(f"Your {dlccheck.display_name(name)} is damaged: {problem}"
                       + (f" ({effect})" if effect else "") +
                       ". Install it again from an intact copy of its package file (Setup, step 2), "
                       "which checks every block.")
    return out


def gpu_vendor_arg(cfg: Config, extra_args=()) -> str:
    """--gpu_vendor=nvidia|amd|intel for the graphics card chosen in Settings (#89): a laptop with an
    Intel iGPU and a GeForce otherwise started on the Intel one (Vulkan 1.2, old driver, device lost).
    Nothing when the choice is "Other" or the Steam Deck (one GPU) or the player set it themselves
    (extra flags, other settings, or a device index, which wins in the game anyway); the game then
    prefers a discrete GPU over an integrated one."""
    vendor = cfg.gpu_vendor()
    if vendor not in gpu.SDK_VENDORS:
        return ""
    mine = ("gpu_vendor", "vulkan_device", "d3d12_adapter")
    if int(cfg.settings.get("vulkan_device", -1)) >= 0:
        return ""
    for a in extra_args:
        if a.lstrip("-").split("=", 1)[0] in mine:
            return ""
    for line in cfg.advanced.get("extras_toml", "").splitlines():
        if line.split("=", 1)[0].strip() in mine:
            return ""
    return f"--gpu_vendor={gpu.SDK_VENDORS[vendor]}"


def build_plan(cfg: Config, game: GameInstall, write=True, refresh_xenia=False, log=print,
               battery_saver=True, vr=False) -> LaunchPlan:
    """battery_saver=False plans with the player's settings even when battery saver would apply
    (the shader prebuild, which builds for them). vr=True is "Play in VR": the VR values as arguments."""
    from . import ingame
    if write:
        ingame.import_pending(cfg, log)
    saver = power.decide(cfg.effective_settings()) if battery_saver else power.Saver()
    if write and battery_saver:
        if cfg.settings.get("battery_saver", power.OFF) != power.OFF:
            log(f"Battery saver: {'applies' if saver.active else 'off'} ({saver.reason})")
        if (cfg.d["power"].get("applied") or {}) != saver.settings:
            cfg.d["power"]["applied"] = dict(saver.settings) or None
            try:
                cfg.save()
            except OSError as e:
                log(f"could not save launcher.json: {e}")
    real = cfg.d["settings"]
    cfg.d["settings"] = {**real, **saver.settings}  # this start only; launcher.json keeps the player's
    try:
        return _build_plan(cfg, game, write, refresh_xenia, log, saver, vr)
    finally:
        cfg.d["settings"] = real


def texture_pack_folder(cfg: Config) -> Path:
    """Texture packs and dumps (#384): <data folder>/textures, also with a Xenia save (whose user
    data root is a copy elsewhere); Settings > Graphics > Open textures folder opens it."""
    return cfg.data_home() / "textures"


def texture_packs_used(cfg: Config) -> bool:
    settings = cfg.effective_settings()
    return bool(settings.get("texture_dump")) or bool(settings.get("texture_replace"))


def native_update_requested(cfg: Config, toml_text: str, keys=("forza_car_tuning",)) -> bool:
    """Use the same config/environment/CLI precedence as the runtime flags."""
    values = parse_toml(toml_text)
    environment = {**os.environ, **parse_env(cfg.advanced.get("extra_env", ""))}
    for key in keys:
        name = "REX_" + key.upper()
        if name in environment:
            # rex::string::from_string<bool> is case-sensitive (numeric.h).
            values[key] = environment[name] in ("true", "1", "yes")
    # CLI11 add_flag_function uses case-insensitive counts and sums repeated
    # registered flags. The environment conversion is intentionally separate.
    def flag_count(value):
        value = value.lower()
        if value in ("true", "yes", "on", "enable", "t", "y", "+"):
            return 1
        if value in ("false", "no", "off", "disable", "f", "n", "-", "0"):
            return -1
        if re.fullmatch(r"[+-]?(?:0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*)", value):
            digits = value.lstrip("+-")
            base = 16 if digits.startswith("0x") else 8 if digits.startswith("0") else 10
            return int(value, base)
        return None

    def summed_count(value):
        # CLI11's Sum policy tries numeric conversion before boolean words.
        # In particular, a positive flag's explicit 0 contributes zero.
        numeric = value.replace("_", "").replace("'", "")
        try:
            return float(numeric)
        except ValueError:
            try:
                return float.fromhex(numeric) if "0x" in numeric.lower() else float(int(numeric, 0))
            except ValueError:
                return flag_count(value)

    cli_counts = {}
    invalid_counts = set()
    extra = split_args(cfg.advanced.get("extra_args", ""))
    for arg in extra:
        if arg == "--":
            break
        if not arg.startswith("--"):
            continue
        key, equal, value = arg[2:].partition("=")
        negated = key.startswith("no-")
        if negated:
            key = key[3:]
        if key not in keys:
            continue
        if not equal or value in ("", "{}"):
            value = "false" if negated else "true"
        elif negated:
            # get_flag_value inverts recognized integer/boolean overrides;
            # other numeric spellings (such as 2.5) pass through unchanged.
            count = flag_count(value)
            if count is not None:
                value = str(-count)
        count = summed_count(value)
        if count is None:
            invalid_counts.add(key)
        cli_counts[key] = cli_counts.get(key, 0) + (count or 0)
    for key, count in cli_counts.items():
        # CLI11 formats its floating-point sum before the int64 callback.
        integer = re.match(r"[+-]?\d+", format(count, ".16g"))
        values[key] = key not in invalid_counts and integer is not None and int(integer[0]) > 0
    return any(values.get(key) is True for key in keys)


def update_data_root(cfg: Config, game: GameInstall, toml_text: str) -> Path:
    menu = game.dir / "update-menu"
    if native_update_requested(cfg, toml_text, ("forza_native_pause",)):
        return menu if menu.is_dir() else game.dir / "update"
    prepared = game.dir / "update-native"
    if native_update_requested(cfg, toml_text) and prepared.is_dir():
        return prepared
    return game.dir / "update"


def native_tuning_needs_build(cfg: Config, install: GameInstall) -> bool:
    """Warn about an enabled feature whose automatic resources have not been built."""
    # The launch plan leaves explicit roots authoritative. Rebuilding the managed
    # overlay cannot repair or replace the user's explicitly selected directory.
    if any(a.startswith("--update_data_root") for a in split_args(cfg.advanced.get("extra_args", ""))):
        return False
    text = render_toml(cfg)
    return (native_update_requested(cfg, text) and
            update_data_root(cfg, install, text) == install.dir / "update")


def native_tuning_warning(cfg: Config, install: GameInstall) -> str:
    status = tuning_status.read(install.dir)
    if status and status["code"] != "ready":
        return tuning_status.summary(status)
    if not cfg["tu_package"]:
        return tuning_status.summary(tuning_status.failure(
            "no_title_update", "This build needs FH1 Title Update 4 for tuning.",
            "Select your Title Update 4 package in Setup, then Build."))
    if status and status["code"] == "ready":
        return ("Native car tuning resources were prepared, but are now missing or incomplete. "
                "Open Setup and Build to restore them. Normal play is available.")
    return ("Native car tuning is enabled, but its resources are missing. "
            "Open Setup and Build with Title Update 4 selected. If you already rebuilt, "
            "send the build log's 'Native car tuning unavailable' error. Normal play is available.")


def _build_plan(cfg: Config, game: GameInstall, write, refresh_xenia, log, saver, vr=False) -> LaunchPlan:
    from . import ingame
    if cfg.gamertag_cleaned:
        log("The saved Gamertag %r is not plain ASCII (letters, digits, spaces, at most 15): "
            "using %r" % cfg.gamertag_cleaned)
        cfg.gamertag_cleaned = None
    home = cfg.data_home()
    data = cfg.game_files()
    toml_path = game.dir / "forza.toml"
    # (cfg.d["settings"] is this start's copy, build_plan: launcher.json keeps the player's choice)
    from . import regpu
    regpu_note = regpu.fall_back(cfg.d["settings"], game.dir)
    toml_text = write_toml(cfg, toml_path) if write else render_toml(cfg)

    args = [f"--game_data_root={data}"]
    notes = [regpu_note] if regpu_note else []
    env, unset = {}, []
    env["FORZA_GRAPHICS_SETTINGS"] = str(home / ingame.FILENAME)

    # The profile: your own, or a copy of your Xenia Canary save (play.sh --xenia).
    log_file = home / "forza.log"
    if write and paths.IS_WINDOWS:
        # DLC an older launcher (or the game, started without --user_data_root) installed in
        # Documents\forza while the launcher's folder has none (issues #65, #70).
        from . import datafolder
        if not datafolder.dlc_packages(home):
            try:
                datafolder.carry_dlc(datafolder.game_default_data_home(), home, log)
            except OSError as e:
                log(f"could not copy the DLC from {datafolder.game_default_data_home()}: {e}")
    root = home
    if cfg.profile["mode"] == "xenia":
        if write:
            root, xuid = prepare_xenia(cfg, refresh_xenia, log)
        else:  # a preview: no copy
            xuid = cfg.profile.get("xenia_xuid", "").strip().upper()
            if not xuid:
                found = xenia_candidates(Path(cfg.profile.get("xenia_content") or default_xenia_content()))
                xuid = found[0] if len(found) == 1 else "<XUID>"
            root = home / f"xenia-{xuid}"
        args += [f"--user_data_root={root}", f"--cache_root={home / 'cache'}",
                 f"--profile_xuid={xuid}"]
    elif cfg.advanced.get("data_root", "").strip() or paths.IS_WINDOWS:
        # Windows: the game's own default is Documents\forza, not the launcher's folder
        # (%LOCALAPPDATA%\ForzaReImagined): name it, or saves, DLC and the shader cache go there.
        root = home
        if write and paths.IS_WINDOWS:
            from . import datafolder
            old = datafolder.game_default_data_home()
            try:
                datafolder.carry_game_written_data(old, home, LOCAL_PROFILE, log)
            except OSError as e:
                # The saves could not be copied: play on them where they are, try again next time.
                log(f"could not copy the saves from {old} to {home} ({e}); starting the game on {old}")
                notes.append(f"Your saves are still in {old}: they could not be copied to {home} ({e}).")
                root = old
        args.append(f"--user_data_root={root}")

    # Xbox LIVE: public/custom through the server named in forza.toml (and FORZA_SERVER, which
    # outranks it); local starts a Forza Live server on this PC; offline uses none.
    server_argv = None
    mode = cfg.online["mode"]
    if mode in ("public", "custom") and cfg.server_spec():
        env["FORZA_SERVER"] = cfg.server_spec()
    elif mode == "local":
        script = server_script(game)
        if script is None:
            notes.append("The Forza Live server files were not found: starting offline.")
            unset.append("FORZA_SERVER")
        else:
            env["FORZA_SERVER"] = LOCAL_SERVER
            if not server_listening():
                server_argv = server_command(script, home / "live_server", data)
    else:
        if mode == "custom":
            notes.append("No custom server address set: starting offline.")
        unset.append("FORZA_SERVER")

    extra_update = any(a.startswith("--update_data_root") for a in split_args(cfg.advanced.get("extra_args", "")))
    update = update_data_root(cfg, game, toml_text)
    if update.is_dir() and not extra_update:
        args.append(f"--update_data_root={update}")
    notes += content_warnings(cfg, game, home, root, extra_update, check_dlc=write or root.exists())
    if cfg.advanced.get("pad_per_user"):
        env["FORZA_PAD_PER_USER"] = "1"

    if write and dlss.enabled(cfg):
        dlss.check_renderer(game.dir)
    dlss.configure(cfg, env, args, notes, write)
    dlss_status_file = Path(env["FORZA_DLSS_STATUS_FILE"]) if "FORZA_DLSS_STATUS_FILE" in env else None
    fsr3.configure(cfg, args, notes)
    extra_textures = any(a.startswith("--texture_pack_root")
                         for a in split_args(cfg.advanced.get("extra_args", "")))
    if texture_packs_used(cfg) and not extra_textures:  # a flag given twice confuses the game
        args.append(f"--texture_pack_root={texture_pack_folder(cfg)}")
    from . import vr as vr_module
    vr_module.configure(cfg, env, args, notes, session=vr)
    from . import antialiasing
    antialiasing.configure_runtime(cfg, env, args, write=write)

    extra = split_args(cfg.advanced.get("extra_args", ""))
    vendor_arg = gpu_vendor_arg(cfg, extra)
    if vendor_arg:
        args.append(vendor_arg)
    if saver.active:
        args += saver.flags
        notes.append(saver.note())
    if not any(a == "--log_file" or a.startswith("--log_file=") for a in extra):
        args.append(f"--log_file={log_file}")
    # The mods turned on in the Mods page, in their load order (#149). An option the player
    # names in the extra flags ("--name=value" or "--name value") is left to them: the game
    # refuses one given twice.
    from . import mods, texture_packs
    named = {a[2:].split("=", 1)[0] for a in extra if a.startswith("--")}
    if write:
        # an FM4 cars import made before #776: its cars without their sound bank, once
        from . import fm4cars
        note = fm4cars.repair_sound(cfg, log)
        if note:
            notes.append(note)
        # one made before #840: some cars with a traffic car's engine tuning, once
        note = fm4cars.tuning_notice(cfg, log)
        if note:
            notes.append(note)
    args += mods.launch_args(cfg, named)
    from . import loading_tips  # ReImagined loading-screen tips (Settings > Gameplay), first in the order
    loading_tips.launch_args(cfg, args, write, log)
    from . import upload_site  # the photo and movie upload texts name forza.dyotson.dev when Play goes online (#875)
    upload_site.launch_args(cfg, args, write, log)
    args += texture_packs.launch_args(cfg, named)  # the packs turned on, in order (#384)
    # An imported FM4 circuit chosen on the Play page (#648, experimental): its game folder, mods
    # and switches, offline. Colorado (the default) changes nothing here.
    from . import fm4tracks
    track = fm4tracks.configure(cfg, env, args, unset, notes, write)
    if track is not None and not env.get("FORZA_SERVER"):
        server_argv = None     # a circuit is offline; an FH2 world keeps the Online settings' server
    args += extra
    env.update(parse_env(cfg.advanced.get("extra_env", "")))

    if not paths.IS_WINDOWS:
        # play.sh: LD_LIBRARY_PATH="$SDK/lib:$BUILD" for a source build, "$DIR" for a package.
        libs = [str(p) for p in game.lib_dirs] + [str(game.dir)]
        # the player's own path, not the frozen launcher's _internal (#87)
        user_libs = cleanenv.clean_environment().get("LD_LIBRARY_PATH")
        if user_libs:
            libs.append(user_libs)
        env["LD_LIBRARY_PATH"] = ":".join(libs)
    save_profile = None
    if write:
        from . import saves
        root = saves.data_root_arg(args, root)
        if not root.is_absolute():
            root = game.dir / root
        save_profile = saves.profile_for(cfg, root=root)
    return LaunchPlan(game, game.dir, [str(game.exe)] + args, env, unset, toml_path,
                      toml_text, server_argv, notes, home, dlss_status_file, dict(cfg.d['haptics']),
                      save_profile=save_profile, fm4_track=track["key"] if track else "")


def log_path_of(plan: "LaunchPlan"):
    args = plan.argv if plan is not None else ()
    for i, a in enumerate(args):
        if str(a).startswith("--log_file="):
            log = Path(str(a).split("=", 1)[1])
            return log if log.is_absolute() else plan.cwd / log
        if a == "--log_file" and i + 1 < len(args):
            log = Path(args[i + 1])
            return log if log.is_absolute() else plan.cwd / log
    return None


def rolled_log_path(log: Path) -> Path:
    """Where the game moves its log when it reaches its size limit (the SDK's log_file_max_mb,
    50 MB): forza.log rolls over to forza.1.log and a new forza.log starts; one roll is kept."""
    return log.with_name(f"{log.stem}.1{log.suffix}")


def previous_log_path(log: Path) -> Path:
    """The last session's log, kept while the next session writes forza.log (#609)."""
    return log.with_name(f"{log.stem}.previous{log.suffix}")


def rotate_log(log, note=print) -> bool:
    """Before the game starts: one session per file (#609). forza.log becomes forza.previous.log
    and its roll forza.1.log forza.previous.1.log, replacing the copies of the session before, so
    the new session starts an empty forza.log. On Windows a log that an older game still has open
    cannot be moved: it stays, the new session appends to it as before, and `note` says so.
    True when there is no old log in the way."""
    if log is None:
        return True
    log = Path(log)
    previous, roll = previous_log_path(log), rolled_log_path(log)
    previous_roll = rolled_log_path(previous)
    if not log.exists():
        return True
    try:
        os.replace(log, previous)
    except OSError as exc:
        note(f"Kept {log.name} ({exc.strerror or exc}): another game may still be writing it; "
             f"this session is added to its end.")
        return False
    try:
        if roll.exists():
            os.replace(roll, previous_roll)
        elif previous_roll.exists():
            previous_roll.unlink()  # from an older session: it does not belong to the new copy
    except OSError as exc:
        note(f"Could not move {roll.name} aside: {exc.strerror or exc}")
    return True


def _file_signature(path):
    try:
        st = path.stat() if path is not None else None
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) if st else None
    except OSError:
        return None


def _roll_signature(plan):
    log = log_path_of(plan)
    return _file_signature(rolled_log_path(log)) if log is not None else None


def session_roll(plan):
    """This session's forza.1.log (it reached the size limit) or None: one left by an older session
    is not this one's."""
    log = log_path_of(plan)
    if log is None:
        return None
    now = _roll_signature(plan)
    return rolled_log_path(log) if now is not None and now != getattr(plan, "roll_before_start", None) else None


def prepare_log(plan, note=print):
    """Before a game process starts: move the last session's log aside, then remember what is left
    (log_is_current, session_roll)."""
    rotate_log(log_path_of(plan), note)
    plan.log_before_start = log_signature(plan)
    plan.roll_before_start = _roll_signature(plan)


# Log lines at error level that the game writes in normal play (missing sound banks, audio frames the
# decoder does not like, XAM/XLIVEBASE calls the runtime has no answer for): never the reason a game
# stopped, so "Last errors" skips them (#98).
HARMLESS_ERRORS = (
    "ffmpeg:",
    "Unimplemented XLIVEBASE message",
    "Unimplemented XAM message",
    "ResolvePath(",
    "device not found",
    "[NtCreateFile] FAILED",
    "STUB",
)

# Log lines the game writes when it closes by itself, after the player closed the window or chose to quit.
CLEAN_CLOSE_MARKERS = ("Title terminated; hard-exiting process", "Execution complete")


def _log_tail(plan: "LaunchPlan", lines=200):
    log = log_path_of(plan)
    try:
        if log is not None and log_is_current(plan):
            return read_log_tail(log, lines)
    except OSError:
        pass
    return []


def log_signature(plan):
    return _file_signature(log_path_of(plan))


def log_is_current(plan):
    """An early startup failure must not inherit a previous run's clean close or fault."""
    before = getattr(plan, "log_before_start", None)
    return before is None or log_signature(plan) != before


def read_log_tail(log: Path, lines=200, max_bytes=65536):
    """Bound both I/O and memory, even when a fault loop produced a multi-GB log."""
    with log.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        start = max(0, size - max_bytes)
        f.seek(start)
        raw = f.read(max_bytes)
    if start:
        raw = raw.partition(b"\n")[2]  # discard an incomplete first line
    return raw.decode("utf-8", errors="replace").splitlines()[-lines:]


def last_errors(tail, count=3):
    """The last `count` error lines of a log tail that are not among the harmless ones."""
    out = [ln.strip() for ln in tail
           if ("[error]" in ln or "[critical]" in ln or "[fatal]" in ln)
           and not any(h in ln for h in HARMLESS_ERRORS)]
    return out[-count:]


def closed_cleanly(plan: "LaunchPlan") -> bool:
    """Did the game end its log the way a normal close does (the last lines say it shut down)?"""
    tail = [ln for ln in _log_tail(plan, 12) if ln.strip()]
    return any(m in ln for ln in tail[-4:] for m in CLEAN_CLOSE_MARKERS)


def exit_is_crash(plan: "LaunchPlan", code, stopped=False) -> bool:
    """Should the launcher show the "game stopped" box for this exit? Not for 0, not when the player
    stopped the game from the launcher (Windows then reports 1, Linux -15), not for the player
    ending it from outside (Ctrl+C, kill: -2, -15), and not when the log ends with the game's own
    shutdown lines. 75 is the relaunch request and is handled before."""
    if code in (0, None):
        return False
    if stopped:
        return False
    if not paths.IS_WINDOWS and code in (-2, -15):
        return False
    return not closed_cleanly(plan)


# The game exits with this code when it was built for a newer CPU than this one (x86-64-v3,
# forza-recomp/src/platform/cpu_target.cpp): it starts nothing, so there is no crash to report.
CPU_EXIT_CODE = 76


def heap_failure_summary(tail):
    """Recognize the memory manager's deliberate stops, not arbitrary low-address faults.

    Base sub_823F3048 / TU4 sub_82423000 write to 4 (capacity) or 5 (a block
    could not be allocated despite aggregate capacity). Their preserved registers
    identify the request and final heap; see docs/PROJECT.md, issue #299.
    """
    text = "\n".join(tail)
    faults = list(re.finditer(
        r"Unhandled guest access violation: (read|write) of guest 0x([0-9A-Fa-f]+)\b[^\n]*"
        r"on thread 0x([0-9A-Fa-f]+)\b", text))
    if not faults:
        return None
    fault = faults[-1]
    if fault[1] != "write" or int(fault[2], 16) not in (4, 5):
        return None
    thread = int(fault[3], 16)
    for match in re.finditer(
            r"Guest thread ([0-9A-Fa-f]+) registers \(lr=([0-9A-Fa-f]+) ctr=[^\n]*\):"
            r"([^\[]*)", text[fault.end():]):
        if int(match[1], 16) != thread:
            continue
        arenas = {0x823F30B4: 0x832AAC24, 0x8242306C: 0x8339664C}.get(int(match[2], 16))
        registers = {int(n): int(v, 16) for n, v in re.findall(
            r"\br(\d+)=([0-9A-Fa-f]{8})\b", match[3])}
        if arenas is None or any(registers.get(n) != 0 for n in (3, 6, 31)):
            return None
        if not all(n in registers for n in (27, 29, 30)):
            return None
        size, alignment, heap = (registers.get(n, 0) for n in (29, 27, 30))
        offset = heap - arenas
        arena, kind = divmod(offset, 0x314)
        if not size or not 0 <= arena < 8 or kind not in (0x10, 0x190):
            return None
        category = (0x10 if kind == 0x10 else 0x20) + arena
        detail = ("The heap had aggregate capacity, but could not provide a suitable block "
                  "(fragmentation or allocation constraints)." if int(fault[2], 16) == 5 else
                  "The heap had insufficient remaining capacity for this request.")
        return (f"The game's internal heap 0x{category:02X} could not allocate {size:,} bytes "
                f"(alignment {alignment} bytes). {detail} This does not establish that system "
                "RAM or video memory is exhausted. Include what you were doing and how long "
                "the game had been running in the crash report.")
    return None


def crash_reason(plan: "LaunchPlan", code) -> str:
    """A readable text for a game that stopped early: its exit code, the last error lines of its
    log and, for the usual causes, what to do."""
    if code == CPU_EXIT_CODE:
        from . import builder
        return builder.CPU_TOO_OLD
    log = log_path_of(plan)
    tail = _log_tail(plan)
    errors = last_errors(tail)
    text = f"The game stopped (exit code {code})."
    joined = " ".join(errors)
    heap_failure = heap_failure_summary(tail)
    if heap_failure:
        text += " " + heap_failure
    elif "Vulkan" in joined:
        text += (" It needs a graphics card with Vulkan support and an up-to-date graphics driver; none was "
                 "found (a virtual machine or a remote desktop session usually has none).")
    elif "memory" in joined.lower() or "alloc" in joined.lower():
        text += " It may have run out of memory: close other programs and try again."
    if errors:
        text += "\nLast errors in the log:\n" + "\n".join(errors)
    text += f"\nLog: {log}" if log is not None else "\nOpen the log folder: forza.log has the details."
    return text


# The game exits with this code when it restarts itself (accepting an invite in free roam,
# src/kernel/xam_loader.cpp) and FORZA_LAUNCHER_RELAUNCH is set: start it again.
RELAUNCH_EXIT_CODE = 75
# A game that keeps exiting with that code (a bug, not an invite each time) is not started
# again more than this many times within this many seconds: the exit then counts as a stop.
RELAUNCH_LIMIT = 4
RELAUNCH_WINDOW = 60.0


def request_window_close(pid: int) -> int:
    """Windows: ask the game's windows to close, as the close button does (WM_CLOSE), so it shuts down
    and writes its log. Returns how many windows were asked. (Python's terminate() is TerminateProcess:
    exit code 1, nothing flushed.)"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    asked = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
            asked.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    return len(asked)


# How long the game gets to close after the window was asked to, before it is ended.
STOP_GRACE_SECONDS = 10.0


class Session:
    """A running game, and the local Forza Live server started for it."""

    def __init__(self, plan: LaunchPlan, log=print):
        self.plan = plan
        self.server = None
        self.proc = None
        self.log = log
        self.relaunches = []   # monotonic times of the restarts (RELAUNCH_LIMIT within RELAUNCH_WINDOW)
        self.stop_requested = False   # the player stopped it from the launcher: its exit code is no crash
        self.stop_deadline = None
        self.haptics = None
        self._save_lock = None
        self._catalog_recovered = False
        self._save_lease = None

    def _release_save_lease(self):
        if self._save_lease is not None:
            lease, self._save_lease = self._save_lease, None
            lease.__exit__(None, None, None)

    def start(self):
        from . import saves
        if self.plan.save_profile is not None:
            guard = saves.locked(self.plan.save_profile)
            guard.__enter__()
            self._save_lock = guard
        try:
            if self.plan.save_profile is not None:
                saves._recover(self.plan.save_profile)
            self._start()
        except BaseException:
            self.cleanup()
            raise

    def _start(self):
        plan = self.plan
        self._acquire_save_lease()
        try:
            if plan.log_dir:
                plan.log_dir.mkdir(parents=True, exist_ok=True)
            flags = 0x08000000 if paths.IS_WINDOWS else 0  # CREATE_NO_WINDOW for the server
            if plan.server_argv:
                data_dir = Path(plan.server_argv[plan.server_argv.index("--data-dir") + 1])
                data_dir.mkdir(parents=True, exist_ok=True)
                # Popen gives the child its own handle. Close our copy immediately, including
                # when starting the server fails; otherwise Windows keeps server.log locked.
                # Its name for signed requests, and, as only this PC uses it, an XUID bound there
                # by a game before them goes to this game's public key (#515).
                server_env = {**cleanenv.clean_environment(), "FORZA_REBIND_LEGACY": "1",
                              "FORZA_SERVER_NAMES": LOCAL_SERVER.split(":")[0]}
                with open(data_dir / "server.log", "ab") as server_log:
                    self.server = subprocess.Popen(plan.server_argv, stdout=server_log,
                                                   stderr=subprocess.STDOUT, creationflags=flags,
                                                   stdin=subprocess.DEVNULL, env=server_env)
                self.log("Started a local Forza Live server")
            self._start_game({})
        except BaseException:
            if self.server and self.server.poll() is None:
                self.server.terminate()
            self._release_save_lease()
            raise

    def _acquire_save_lease(self):
        plan = self.plan
        if not self._catalog_recovered and plan.log_dir is not None:
            from . import catalog_import, ugc
            target = catalog_import.target_for_plan(plan)
            # The save manager and catalog importer use the same home-wide OS lock.
            # Reuse the session lock instead of trying to acquire it a second time.
            lease = None if self._save_lock is not None else catalog_import.locked(target)
            if lease is not None:
                lease.__enter__()
            try:
                try:
                    catalog_import.recover(target)
                except ugc.CatalogError as exc:
                    warning = catalog_import.recovery_warning(target, exc)
                    log = paths.log_error('recover design catalog: ' + warning, exc)
                    warning += f' Log: {log}'
                    if warning not in plan.notes:
                        plan.notes.append(warning)
                        self.log(warning)
            except BaseException:
                if lease is not None:
                    lease.__exit__(None, None, None)
                raise
            self._save_lease = lease
            self._catalog_recovered = True

    def _start_game(self, extra):
        plan = self.plan
        self._acquire_save_lease()
        prepare_log(plan, lambda text: (self.log(text), paths.log_error(text)))
        from .shader_warmup import Monitor
        self.shader_warmup = Monitor(log_path_of(plan))
        env = cleanenv.clean_environment()
        for k in plan.unset:
            env.pop(k, None)
        env.update(plan.env)
        env["FORZA_LAUNCHER_RELAUNCH"] = "1"
        env.update(extra)
        # Never inherit another launcher's telemetry destination or session token.
        env.pop('FORZA_TELEMETRY_PORT', None)
        env.pop('FORZA_TELEMETRY_TOKEN', None)
        env.pop('FORZA_TELEMETRY_PHYSICS', None)
        from .haptics.service import HapticsService, supported, supports_xbox
        wanted = plan.haptics_settings.get('enabled') or plan.haptics_settings.get('xbox_enabled')
        if not wanted and self.haptics:
            self.haptics.stop()
            self.haptics = None
        if wanted and self.haptics is None and supported(plan.cwd):
            try:
                self.haptics = HapticsService(plan.haptics_settings, xbox_capable=supports_xbox(plan.cwd))
                self.haptics.start()
            except OSError as exc:
                self.log(f'Adaptive triggers unavailable: {exc}')
        if self.haptics:
            env.update(self.haptics.new_stream())
        elif wanted:
            self.log('Controller haptics need a game build with the FH1 telemetry bridge.')
        try:
            self.proc = subprocess.Popen(plan.argv, cwd=plan.cwd, env=env, stdin=subprocess.DEVNULL)
        except BaseException:
            self.cleanup()
            raise

    def update_haptics(self, settings):
        from .haptics.settings import normalize
        self.plan.haptics_settings = normalize(settings)
        if self.haptics:
            self.haptics.update(self.plan.haptics_settings)

    def relaunch(self):
        """The game asked to be started again (RELAUNCH_EXIT_CODE); the server keeps running.
        Returns False, starting nothing, when it asked too often."""
        now = time.monotonic()
        self.relaunches = [t for t in self.relaunches if now - t < RELAUNCH_WINDOW]
        if len(self.relaunches) >= RELAUNCH_LIMIT:
            self.log("The game keeps restarting itself; not starting it again")
            return False
        self.relaunches.append(now)
        self.log("The game restarts itself")
        self._start_game({"FORZA_RELAUNCHED": "1"})
        return True

    def poll(self):
        if self.proc is None:
            return None
        code = self.proc.poll()
        if code is None and self.stop_deadline is not None and time.monotonic() > self.stop_deadline:
            self.stop_deadline = None
            self.log("The game did not close by itself; ending it")
            self.proc.terminate()
        return code

    def stop(self):
        if self.haptics:
            self.haptics.stop()
        if self.proc and self.proc.poll() is None:
            self.stop_requested = True
            if paths.IS_WINDOWS:
                try:
                    if request_window_close(self.proc.pid):
                        self.stop_deadline = time.monotonic() + STOP_GRACE_SECONDS
                        return
                except (OSError, AttributeError, ValueError) as e:
                    paths.log_error("ask the game to close", e)
            self.proc.terminate()

    def cleanup(self):
        """After the game ends: stop our server and free guest memory left in /dev/shm."""
        if self.haptics:
            self.haptics.stop()
        if self.server and self.server.poll() is None:
            self.server.terminate()
        dlss.cleanup_status(self.plan)
        self._release_save_lease()
        self._catalog_recovered = False
        if self._save_lock is not None:
            guard, self._save_lock = self._save_lock, None
            guard.__exit__(None, None, None)
        if not paths.IS_WINDOWS:
            clean_shm()


def clean_shm():
    """Guest memory (/dev/shm/xenia_memory_*, 5 GB) is not unlinked when the game is killed."""
    for shm in glob.glob("/dev/shm/xenia_memory_*"):
        try:
            if os.stat(shm).st_uid != os.getuid():
                continue
            name = os.path.basename(shm)
            in_use = False
            for maps in glob.glob("/proc/[0-9]*/maps"):
                try:
                    with open(maps, "r", errors="replace") as f:
                        if name in f.read():
                            in_use = True
                            break
                except OSError:
                    continue
            if not in_use:
                os.unlink(shm)
        except OSError:
            continue
