"""Prebuild shaders (#226, docs/shader_prebuild.md): build every pipeline the game uses before playing,
so that new places, cars and cameras stop compiling shaders mid-game.

The launcher ships a list of pipelines (data/pipelines-4D5309C9.json: shader hashes and decoded render
state, no game data). This step rebuilds the listed shaders from the player's own game files
(tools/pipeline_list.py), writes them and the list for the SDK to <cache root>/shaders/prebuild/
(patch 0113 reads them at every start), then starts the game with --shader_prebuild_only: it builds
every stored and listed pipeline into the driver's cache, reporting the usual SHADER_WARMUP progress,
and exits before the game itself starts. That run also writes the shader pack (ReIGlue 0600,
<cache root>/shaders/translated/*.vk.xspv): the SPIR-V it translated for this GPU and these settings,
which later starts take instead of translating again (the Advanced setting vulkan_shader_pack turns
it off). The game uses a pack only when it was written by its own build for its exact configuration.

It is offered again when the result no longer fits: another graphics driver (the driver cache files
of the last prebuild are gone: the SDK deletes those of an earlier driver), another launcher, list or
GPU plugin, or a graphics setting that selects other pipeline stores (split scene/HUD rendering,
output size, render scale, render target path, API), or the shader pack it wrote is gone."""
import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from . import TITLE_ID, __version__, cleanenv, paths

LIST_NAME = f"pipelines-{TITLE_ID}.json"
STAMP_NAME = "launcher.json"
DONE_LINE = "SHADER_PREBUILD: complete"
PACK_SETTING = "vulkan_shader_pack"
PACK_LINE = "Vulkan shader pack: wrote "
# Arguments that choose which pipeline stores the game uses (and so what a prebuild built).
STORE_ARGS = ("--gpu_backend", "--render_target_path_vulkan", "--fh1_upscaler",
              "--fh1_scene_720p_native_hud", "--fh1_native_scene_scale",
              "--fh1_native_scene_width", "--fh1_native_scene_height", "--fh1_output_width",
              "--fh1_output_height", "--resolution_scale", "--draw_resolution_scale_x",
              "--draw_resolution_scale_y")


class PrebuildError(Exception):
    pass


REBUILD_FIRST = "Rebuild the game first (Setup > Build): Prebuild shaders needs the current build."
STOPPED_NO_LOG = ("The game stopped right away without writing a log; rebuilding the game "
                  "(Setup > Build) usually fixes this.")
UNKNOWN_FLAG = ("The game does not know the prebuild option: it was built by an older launcher. "
                "Rebuild the game (Setup > Build).")


def build_problem(cfg) -> str:
    """Why the game cannot be prebuilt yet ('' when its build is current). Same test as the Setup page's
    Build step: the build is complete and made from the project of this launcher (builder.build_status).
    A build Play starts that is not the launcher's (this checkout's, or the Advanced game_dir) is not
    the launcher's to judge: it is prebuilt as it is (an older one says it lacks the option, UNKNOWN_FLAG)."""
    from . import builder, game
    try:
        outside = game.outside_build(cfg) is not None
    except Exception:
        outside = False
    if outside:
        return ""
    try:
        state, _detail = builder.build_status(cfg.data_home(), cfg["tu_package"])
    except Exception:
        return ""
    return "" if state == "built" else REBUILD_FIRST


def list_path() -> Path | None:
    for base in (paths.LAUNCHER_ROOT / "forza_launcher" / "data", paths.PKG_DIR / "data"):
        if (base / LIST_NAME).is_file():
            return base / LIST_NAME
    return None


def _arg(argv, name):
    """The value of --name=value (or --name value) in argv; the last one wins, as in the game."""
    value = None
    for i, a in enumerate(argv):
        a = str(a)
        if a.startswith(name + "="):
            value = a.split("=", 1)[1]
        elif a == name and i + 1 < len(argv):
            value = str(argv[i + 1])
    return value


def ambiguous_paths(plan) -> str:
    """A path option given twice: the game then ignores its path options and uses its default folders,
    so the prebuild would land where the game does not look (and the game would use another cache)."""
    for name in ("--user_data_root", "--cache_root"):
        if sum(1 for a in plan.argv if str(a) == name or str(a).startswith(name + "=")) > 1:
            return name
    return ""


def cache_root(plan) -> Path:
    """The game's cache root for this launch plan: --cache_root, else <user data root>/cache."""
    root = _arg(plan.argv, "--cache_root")
    if root:
        p = Path(root)
    else:
        user = _arg(plan.argv, "--user_data_root")
        p = (Path(user) if user else paths.data_home()) / "cache"
    return p if p.is_absolute() else plan.cwd / p


def prebuild_dir(plan) -> Path:
    return cache_root(plan) / "shaders" / "prebuild"


def store_key(plan) -> dict:
    return {name[2:]: _arg(plan.argv, name) for name in STORE_ARGS if _arg(plan.argv, name) is not None}


def _toml_setting(plan, name):
    from .config import parse_toml
    try:
        return parse_toml(getattr(plan, "toml_text", "") or "").get(name)
    except ValueError:
        return None


def shader_pack_on(plan) -> bool:
    """The game's vulkan_shader_pack for this plan: on unless the command line or forza.toml turns it off."""
    value = _arg(plan.argv, "--" + PACK_SETTING)
    if value is None:
        value = _toml_setting(plan, PACK_SETTING)
    if value is None or isinstance(value, bool):
        return value is not False
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def pack_files(plan, since=None) -> list:
    """The shader packs under the cache root (the main cache's, and split rendering's two), relative to
    it; since: only those written from that time on (a little slack for coarse file times)."""
    root, out = cache_root(plan), []
    for d in [root / "shaders" / "translated"] + sorted(root.glob("fh1-live-*/*/shaders/translated"))[:16]:
        try:
            for p in sorted(d.glob(f"{TITLE_ID}.*.vk.xspv")):
                if since is None or p.stat().st_mtime >= since - 2.0:
                    out.append(str(p.relative_to(root)))
        except OSError:
            continue
    return out


def uses_vulkan(plan) -> bool:
    """The Direct3D 12 backends (and ReGPU) are chosen in forza.toml, the command line can override it."""
    backend = _arg(plan.argv, "--gpu_backend")
    if backend is None:
        backend = _toml_setting(plan, "gpu_backend")
    return (backend or "vulkan") == "vulkan"


def _file_id(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_size}-{st.st_mtime_ns}"
    except OSError:
        return ""


_SHA1_MEMO = {}


def _sha1(path: Path) -> str:
    """SHA-1 of a file, read again only when its size or time changed (the Setup page asks often)."""
    key = (str(path), _file_id(path))
    if key not in _SHA1_MEMO:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _SHA1_MEMO[key] = h.hexdigest()
    return _SHA1_MEMO[key]


def driver_files(plan) -> list:
    """The SDK's driver pipeline cache files (<title>-<vendor>-<device>-<driver>-<versions>.vkpc) under
    the cache root: their names change with the GPU and its driver."""
    root, out = cache_root(plan), []
    for d in [root / "shaders" / "local"] + sorted(root.glob("fh1-live-*/*/shaders/local"))[:16]:
        try:
            out += sorted(str(p.relative_to(root)) for p in d.glob(f"{TITLE_ID}-*.vkpc"))
        except OSError:
            continue
    return out


def identity(plan, list_file=None) -> dict:
    """What a prebuild depends on, apart from the driver."""
    list_file = list_file or list_path()
    return {"launcher": __version__, "list": _sha1(list_file) if list_file else "",
            "plugin": _file_id(plan.cwd / ("rexgpu-xenos.dll" if paths.IS_WINDOWS else "librexgpu-xenos.so")),
            "stores": store_key(plan)}


def read_stamp(plan) -> dict | None:
    try:
        with open(prebuild_dir(plan) / STAMP_NAME, encoding="utf-8") as f:
            stamp = json.load(f)
        return stamp if isinstance(stamp, dict) else None
    except (OSError, ValueError):
        return None


def status(plan, build_problem_text="") -> tuple:
    """(state, text): 'unavailable', 'none' (never prebuilt here), 'stale' (offer it again: the text
    says why) or 'done'. 'rebuild' when the game's build is missing or out of date (build_problem)."""
    if build_problem_text:
        return "rebuild", build_problem_text
    lst = list_path()
    if lst is None:
        return "unavailable", "This launcher has no pipeline list."
    if not uses_vulkan(plan):
        return "unavailable", "Shader prebuild needs the Vulkan graphics API."
    if ambiguous_paths(plan):
        return "unavailable", (f"{ambiguous_paths(plan)} is given twice (Advanced > extra arguments): the game "
                               "would not use either.")
    stamp = read_stamp(plan)
    if not stamp:
        return "none", ""
    now = identity(plan, lst)
    if stamp.get("stores") != now["stores"]:
        return "stale", "Your graphics settings changed since the last prebuild."
    if stamp.get("list") != now["list"] or stamp.get("launcher") != now["launcher"] \
            or stamp.get("plugin") != now["plugin"]:
        return "stale", "The launcher or the game was updated since the last prebuild."
    files = stamp.get("driver") or []
    root = cache_root(plan)
    if files and not any((root / f).is_file() for f in files):
        return "stale", "Your graphics driver changed since the last prebuild."
    packs = [f for f in stamp.get("packs") or [] if (root / f).is_file()]
    if shader_pack_on(plan) and stamp.get("packs") is not None:
        # A prebuild of this launcher with the pack on lists what it wrote; with the pack off, none.
        if not stamp.get("pack_on", True):
            return "stale", "Prebuild again to keep the translated shaders (Advanced setting turned on)."
        if stamp.get("packs") and not packs:
            return "stale", "The translated shaders of the last prebuild were deleted."
    return "done", (f"{stamp.get('pipelines', 0):,} pipelines ready"
                    + (", their shaders translated" if packs and shader_pack_on(plan) else "")
                    + (f", built in {stamp['seconds']:.0f} s" if stamp.get("seconds") else "") + ".")


def game_sources(cfg, game, plan) -> list:
    """The player's files that hold the listed shaders: media/shaders, the tracks' bin.zip, the title
    update's media.zip and the executable image (decoded from default.xex in memory)."""
    paths.ensure_tools_importable()
    import apply_title_update
    files = cfg.game_files()
    update = _arg(plan.argv, "--update_data_root")
    update = Path(update) if update else None
    out = []
    for p in (files / "media" / "shaders", update / "media.zip" if update else None, files / "media" / "tracks"):
        if p is not None and p.exists():
            out.append(str(p))
    xex = update / "default.xex" if update and (update / "default.xex").is_file() else files / "default.xex"
    if not xex.is_file():
        raise PrebuildError(f"No default.xex in {xex.parent}.")
    try:
        image = bytes(apply_title_update.Xex(xex.read_bytes(), str(xex)).image())
    except SystemExit as e:  # the tool reports a bad file this way
        raise PrebuildError(f"Cannot read {xex}: {e}") from None
    out.append(("default.xex", image))
    return out


def write_files(cfg, game, plan, status_cb=None, cancelled=None, log=print) -> dict:
    """Rebuild the listed shaders from the player's files and write the SDK's prebuild files."""
    paths.ensure_tools_importable()
    import pipeline_list
    lst_file = list_path()
    if lst_file is None:
        raise PrebuildError("This launcher has no pipeline list: reinstall it.")
    lst = pipeline_list.load_list(str(lst_file))
    problems = pipeline_list.check_list(lst)
    if problems:
        raise PrebuildError(f"The pipeline list is damaged ({problems[0]}): reinstall the launcher.")
    from . import shader_share
    lst = shader_share.community_list(cfg, lst, log)  # the server's list too (#276), else the shipped one
    srcs = game_sources(cfg, game, plan)
    try:
        return pipeline_list.write_prebuild(lst, srcs, str(prebuild_dir(plan)), status_cb, cancelled)
    except InterruptedError:
        raise
    except (OSError, ValueError) as e:
        raise PrebuildError(f"Could not write the shader prebuild: {e}") from e


def prebuild_command(plan) -> list:
    """The game in prebuild-only mode. The run is judged by its SHADER_WARMUP progress lines and
    the SHADER_PREBUILD done line, which the game logs at info level: a player's lower log level
    (Advanced > Log level, forza.toml) would hide them and turn a finished prebuild into a
    failure (#295), so this run always logs at info."""
    args = [a for a in plan.argv if not (a == "--log_level" or a.startswith("--log_level="))]
    return args + ["--log_level=info", "--shader_prebuild_only=true"]


def run_game(plan, progress=None, cancelled=None, log=print, poll=0.25) -> dict:
    """Start the game in prebuild-only mode and wait for it. progress(done, total, part) follows the
    SHADER_WARMUP lines; part counts the pipeline caches (two with split scene/HUD rendering)."""
    from .game import clean_shm, log_is_current, log_path_of, prepare_log
    from .shader_warmup import Monitor
    log_file = log_path_of(plan)
    prepare_log(plan, log)
    monitor = Monitor(log_file)
    env = cleanenv.clean_environment()
    for k in plan.unset:
        env.pop(k, None)
    env.update(plan.env)
    start = time.monotonic()
    started_at = time.time()
    out = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(prebuild_command(plan), cwd=plan.cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT)
    except BaseException:
        out.close()
        raise
    part, last, seen_active = 0, None, False
    try:
        while proc.poll() is None:
            if cancelled and cancelled():
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise InterruptedError
            st = monitor.poll()
            if st is not None and st != last:
                if st.active and (last is None or not last.active):
                    part += 1
                last = st
                seen_active = seen_active or st.active
                if progress and st.active:
                    progress(st.done, st.total, part)
            time.sleep(poll)
    finally:
        if not paths.IS_WINDOWS:
            clean_shm()
    seconds = time.monotonic() - start
    console = _console_text(out)
    # Only a log written during this run counts: an older forza.log says nothing about this one.
    # The log must have changed during this process (log_is_current compares the signature taken
    # before the start) and been written since it started: a run that ended moments ago leaves a
    # recent log, which must not count as this one's (#298 review).
    fresh = log_is_current(plan) and _written_since(log_file, started_at)
    tail = _log_tail(log_file) if fresh else []
    if proc.returncode != 0 or not any(DONE_LINE in line for line in tail):
        if not tail:
            low = console.lower()
            if ("unknown" in low or "unrecognized" in low or "unrecognised" in low) and \
                    ("flag" in low or "option" in low or "argument" in low):
                raise PrebuildError(UNKNOWN_FLAG)
            raise PrebuildError(STOPPED_NO_LOG)
        errors = [line.strip() for line in tail if "[error]" in line or "[critical]" in line][-2:]
        raise PrebuildError("The game stopped before the prebuild finished"
                            + (f" (exit code {proc.returncode})" if proc.returncode else "")
                            + (": " + " / ".join(errors) if errors else "."))
    created = translated = 0
    for line in tail:
        try:
            if "graphics pipelines from Vulkan storage" in line:
                created += int(line.split("Created ", 1)[1].split(" ", 1)[0])
            elif PACK_LINE in line:  # "... wrote <file> (1489 translations, ..."
                translated += int(line.split(PACK_LINE, 1)[1].split("(", 1)[1].split(" ", 1)[0])
        except (IndexError, ValueError):
            pass
    return {"seconds": seconds, "pipelines": created, "parts": part, "translated": translated,
            "started": started_at}


def _written_since(log_file, started_at, slack=2.0) -> bool:
    try:
        return log_file is not None and Path(log_file).stat().st_mtime >= started_at - slack
    except OSError:
        return False


def _console_text(f, max_bytes=16 * 1024) -> str:
    try:
        f.seek(0)
        return f.read(max_bytes).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    finally:
        f.close()


def _log_tail(log_file, max_bytes=256 * 1024):
    try:
        with open(log_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except (OSError, TypeError):
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    # Only this run: from its last "begin" of the warm-up.
    starts = [i for i, line in enumerate(lines) if "Initializing shader storage" in line]
    return lines[starts[-1]:] if starts else lines


def write_stamp(plan, result: dict, files: dict):
    pack_on = shader_pack_on(plan)
    stamp = dict(identity(plan), driver=driver_files(plan), pipelines=result.get("pipelines", 0),
                 listed=files.get("pipelines", 0), shaders=files.get("shaders", 0),
                 seconds=round(result.get("seconds", 0), 1), time=int(time.time()), pack_on=pack_on,
                 packs=pack_files(plan, result.get("started")) if pack_on else [],
                 translated=result.get("translated", 0))
    d = prebuild_dir(plan)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STAMP_NAME + ".new")
    tmp.write_text(json.dumps(stamp, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, d / STAMP_NAME)


def prebuild(cfg, game, status_cb=None, progress=None, cancelled=None, log=print) -> str:
    """The whole step. status_cb(text); progress(fraction 0..1, detail)."""
    from . import game as game_mod
    if build_problem(cfg):
        raise PrebuildError(REBUILD_FIRST)
    plan = game_mod.build_plan(cfg, game, write=True, log=log, battery_saver=False)
    if not uses_vulkan(plan):
        raise PrebuildError("Shader prebuild needs the Vulkan graphics API (Settings > Graphics > Graphics API).")
    if ambiguous_paths(plan):
        raise PrebuildError(f"{ambiguous_paths(plan)} is given twice (Advanced > extra arguments): the game "
                            "would ignore both. Keep one.")
    if status_cb:
        status_cb("Reading the shaders in your game files")

    def files_progress(i, n, name):
        if progress:
            progress(0.08 * i / max(n, 1), os.path.basename(str(name)))
    files = write_files(cfg, game, plan, files_progress, cancelled, log)
    log(f"Shader prebuild: {files['shaders']} shaders rebuilt from your game files, "
        f"{files['pipelines']} of {files['pipelines_listed']} listed pipelines")
    if status_cb:
        status_cb("Building the pipelines (the game window stays black meanwhile)")

    def game_progress(done, total, part):
        if progress and total:
            # One cache, or the HUD's and then the scene's: the bar fills once per cache.
            progress(0.1 + 0.9 * done / total, f"{done:,} of {total:,} pipelines"
                     + (f" (part {part})" if part > 1 else ""))
    result = run_game(plan, game_progress, cancelled, log)
    write_stamp(plan, result, files)
    if result.get("translated"):
        log(f"Shader prebuild: {result['translated']:,} translated shaders kept for the next starts")
    return f"{result['pipelines']:,} pipelines built in {result['seconds']:.0f} s."
