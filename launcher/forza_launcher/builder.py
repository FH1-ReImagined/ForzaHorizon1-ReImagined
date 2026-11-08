"""Building the game on the player's machine: toolchain, SDK, codegen from their own
default.xex, CMake configure and ninja. Nothing built here is ever uploaded or shipped.

Layout under the data root (default ~/.local/share/forza, %LOCALAPPDATA%\\ForzaReImagined on Windows):
  game/        the extracted game files (default.xex ...)
  toolchain/   downloaded LLVM, CMake, ninja (only when the system has none)
  downloads/   the pinned archives (kept, so a retry needs no download)
  sdk/         the patched ReXGlue SDK
  build/       the project sources, generated code and the build tree
  runtime/     what Play runs: the program, its libraries, forza.toml
"""
import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__, paths, pins, tuning_status
from . import build_env, cleanenv, datafolder, fetch, specs
from .fetch import Cancelled

IS_WIN = sys.platform == "win32"
PLAT = "windows" if IS_WIN else "linux"
EXE = ".exe" if IS_WIN else ""


class BuildError(Exception):
    pass


class FolderBlocked(BuildError):
    """The data folder cannot be written: Windows denied access (OneDrive, antivirus, Controlled folder
    access, #38), or on Linux it is read-only (in the Flatpak: a folder the sandbox may only read,
    #717). The player should allow the access or choose another folder."""


class Reporter:
    """Receives what the build does. The window and the command line each implement it."""

    def log(self, text): ...
    def status(self, text): ...
    def progress(self, fraction, detail=""): ...   # 0..1 over the whole build
    def cancelled(self): return False


@dataclass
class Options:
    root: Path
    jobs: int = 0
    low_priority: bool = True
    accept_ms_license: bool = False
    prefer_downloaded: bool = False
    clean: bool = False
    title_update: str = ""   # the player's TU4 package (recommended: Rally, 1000 Club; optional)

    def __post_init__(self):
        self.root = Path(self.root)
        if not self.jobs:
            self.jobs = build_env.default_jobs()


# ---- locations ----------------------------------------------------------------------------

def project_dir() -> Path | None:
    """The shipped project sources: a folder holding forza-recomp/ (and tools/cmake)."""
    for d in (paths.app_dir() / "project", paths.LAUNCHER_ROOT / "project", paths.REPO_ROOT):
        if (d / "forza-recomp" / "CMakeLists.txt").is_file():
            return d
    return None


def sdk_overlay_archive() -> Path | None:
    """The patched SDK pieces CI builds (Linux: plugin, runtime, codegen tool; Windows: the whole
    SDK prefix), shipped next to the launcher."""
    name = f"rexglue-overlay-{PLAT}-amd64.tar.gz"
    for d in (paths.app_dir() / "sdk-overlay", paths.LAUNCHER_ROOT / "sdk-overlay", paths.app_dir()):
        if (d / name).is_file():
            return d / name
    return None


def dev_sdk_overlay() -> Path | None:
    """A source checkout's own patched overlay (tools/build_rexglue_sdk.sh), for development."""
    if IS_WIN or not paths.in_checkout():
        return None
    c = Path(os.environ.get("REXSDK_PATCHED", Path.home() / "rexglue-sdk-patched-forza/0.10.0.24/linux-amd64"))
    return c if (c / "lib" / "librexgpu-xenos.so").is_file() else None


def sdk_dir(root: Path) -> Path:
    return root / "sdk" / f"{pins.SDK_VERSION}-{PLAT}"


def build_dir(root: Path) -> Path:
    return root / "build"


def runtime_dir(root: Path) -> Path:
    return root / "runtime"


# CMake cannot build in a folder whose path has one of these (#691). [ and ] (and * and ?, which
# Windows does not allow) are patterns to file(GLOB), with which CMake finds its own compiler
# identification files (the downloaded CMake lives in toolchain/) and the SDK's per-configuration
# target files ("IMPORTED_LOCATION or IMPORTED_IMPLIB not set for imported target rex::runtime");
# ; separates the items of CMake lists (and of INCLUDE and LIB); and on Windows %NAME% expands in
# the commands ninja runs through cmd.exe.
CMAKE_UNSAFE = "[]*?;" + ("%" if IS_WIN else "")


def cmake_unsafe_chars(path) -> str:
    """The characters of `path` CMake cannot build under (CMAKE_UNSAFE), each once."""
    return "".join(dict.fromkeys(c for c in str(path) if c in CMAKE_UNSAFE))


def link_home() -> Path:
    """Where build_root puts its links: a folder of the launcher's own, outside every data folder."""
    if IS_WIN:
        d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ForzaReImagined-links"
    else:
        d = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "forza-reimagined" / "links"
    return d if not cmake_unsafe_chars(d) else Path(tempfile.gettempdir()) / "forza-reimagined-links"


def _is_link(p: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)   # Python 3.12
    return os.path.islink(p) or bool(IS_WIN and isjunction and isjunction(p))


def _make_link(target: Path, link: Path):
    if IS_WIN:
        import _winapi   # a junction: no administrator rights or Developer Mode needed, unlike a symlink
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _remove_link(link: Path):
    """Removes the link `link`, never what it points to."""
    if IS_WIN:
        os.rmdir(link)   # a junction
    else:
        os.unlink(link)


def build_root(root: Path, rep: Reporter) -> Path:
    """`root`, or when its path has characters CMake cannot build under (CMAKE_UNSAFE), a link to it
    whose path has none: the build then runs through the link, on the folder's own files. The link
    is kept for the next build (CMake's cache names it), and remade when it points elsewhere."""
    bad = cmake_unsafe_chars(root)
    if not bad:
        return root
    real = os.path.normcase(os.path.realpath(root))
    home = link_home()
    link = home / hashlib.sha1(real.encode("utf-8", "surrogateescape")).hexdigest()[:12]
    try:
        if not _is_link(link) and os.path.lexists(link):
            raise OSError(f"{link} is in the way and is not a link")
        if _is_link(link) and os.path.normcase(os.path.realpath(link)) != real:
            _remove_link(link)
        if not _is_link(link):
            home.mkdir(parents=True, exist_ok=True)
            _make_link(root, link)
    except OSError as e:
        raise BuildError(
            f"The path of the folder the game is built in, {root}, has {' '.join(bad)} in it, which CMake "
            f"cannot build under, and the launcher could not make a link to it without them ({e}). Choose "
            f"a folder without these characters in {datafolder.CHANGE_FOLDER_UI}, then press Build "
            "again.") from e
    rep.log(f"The folder {root} has {' '.join(bad)} in its path, which CMake cannot build under: "
            f"building through {link}, a link to it.")
    return link


EXCLUDE_DIRS = {"out", "__pycache__", ".git"}
# Original preparation source only; no generated tuning assets are distributed.
NATIVE_TUNING_FILES = ("native_tuning_assets.py",)

# Original resource preparation shipped with the project; no generated game assets.
NATIVE_MENU_FILES = ("native_menu_scene.py", "offline_cars_flow.py", "lzx_delta.py",
                     "native_menu/fh1_donors.json", "native_menu/icons/ATTRIBUTION.md",
                     "native_menu/icons/FONT-AWESOME-LICENSE.txt",
                     *(f"native_menu/icons/{name}.xds" for name in
                       ("change_car", "tune_car", "garage", "autoshow", "social_friends", "social_world", "social_game", "social_requests", "social_chat", "social_connection")))


# ReGPU (the thirdparty/ReGPU submodule, FH1-ReImagined/ReGPU) is built only into the Windows GPU plugin
# (FORZA_REGPU_PLUGIN): other platforms get none of it, and Windows only what its CMakeLists.txt reads
# (its two EXCLUDE_FROM_ALL check programs' sources included), not its notes or developer tools.
REGPU_DIR = Path("thirdparty") / "ReGPU"
REGPU_FILES = ("CMakeLists.txt", "LICENSE", "README.md", "tests/linkcheck.cpp", "tools/embed_hlsl.py",
               "tools/xenos_dis.cpp")
REGPU_DIRS = ("include", "src")


def _regpu_file(rel: Path) -> bool:
    inner = rel.relative_to(REGPU_DIR)
    return inner.as_posix() in REGPU_FILES or inner.parts[0] in REGPU_DIRS


def source_files(proj: Path, windows: bool = IS_WIN):
    """The project files the build uses: forza-recomp minus generated code and output."""
    base = proj / "forza-recomp"
    for dirpath, dirs, files in os.walk(base):
        rel = Path(dirpath).relative_to(base)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS and
                         not (rel == Path(".") and d.startswith("generated")) and
                         not (not windows and rel / d == REGPU_DIR))
        in_regpu = rel == REGPU_DIR or REGPU_DIR in rel.parents
        for f in sorted(files):
            if f.endswith((".pyc", ".log")) or f == ".git":  # (.git: a submodule's link to its repository)
                continue
            if in_regpu and not _regpu_file(rel / f):
                continue
            yield rel / f
    if (base / "generated" / "rexglue.cmake").is_file():
        yield Path("generated") / "rexglue.cmake"


def server_files(server: Path):
    """The Forza Live server files a release ships, relative to server/ (the submodule): the
    entry point and the forza_live package with its data, without tests or caches."""
    yield Path("forza_live_server.py")
    for f in sorted((server / "forza_live").rglob("*")):
        if f.is_file() and f.suffix in (".py", ".xml") and "__pycache__" not in f.parts:
            yield f.relative_to(server)


_SOURCES_MEMO = {}


def sources_hash(proj: Path) -> str:
    """SHA-1 of the build sources. The window asks at every page change, so the files are read again
    only when one changed (size or modification time), not every time (#124)."""
    base = proj / "forza-recomp"
    extras = [proj / "tools" / "cmake" / "windows-native-clang-cl.cmake"]
    extras += [proj / "tools" / name for name in (*NATIVE_TUNING_FILES, *NATIVE_MENU_FILES)]
    rels, sig = sorted(source_files(proj)), []
    for rel in rels:
        try:
            st = (base / rel).stat()
            sig.append((str(rel), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(rel), 0, 0))
    for extra in extras:
        try:
            st = extra.stat()
            sig.append((str(extra), st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append((str(extra), 0, 0))
    key = str(proj)
    hit = _SOURCES_MEMO.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    h = hashlib.sha1()
    for rel in rels:
        h.update(str(rel).replace("\\", "/").encode())
        h.update((base / rel).read_bytes())
    for extra in extras:
        if extra.is_file():
            h.update(extra.relative_to(proj).as_posix().encode())
            h.update(extra.read_bytes())
    _SOURCES_MEMO[key] = (sig, h.hexdigest())
    return h.hexdigest()


def tu_id(title_update: str = "") -> str:
    """'' for the base game, else an id of the chosen Title Update 4 package (its size or name)."""
    if not title_update:
        return ""
    p = Path(title_update).expanduser()
    try:
        return "tu4:" + (str(p.stat().st_size) if p.is_file() else p.name)
    except OSError:
        return "tu4:" + p.name


CPU_BASELINE, CPU_V3 = "baseline", "x86-64-v3"
CPU_TOO_OLD = ("The game was built for a newer CPU (x86-64-v3: AVX2, FMA, BMI2, MOVBE) than this PC's and cannot "
               "start here. Rebuild it on this PC: Setup > Build.")


def build_id_arg() -> str:
    """The CMake option that names this build to the Forza Live server (FORZA_BUILD_ID, sent as
    X-Forza-Build on every title service call, and kept with each leaderboard row: #517): the
    launcher's version, which is what a player's game was built by."""
    return "-DFORZA_BUILD_ID=" + re.sub(r"[^A-Za-z0-9._+-]", "_", __version__)[:40]


def cpu_target() -> str:
    """The CPU the game's generated code is compiled for (FORZA_CPU_TARGET, #220, docs/perf.md): x86-64-v3 when
    this PC's CPU and OS have all of it (the game's code then needs 8-11% less CPU time), else baseline (SSE4.1,
    any x86-64 CPU). FORZA_CPU_TARGET=baseline|x86-64-v3 in the environment overrides."""
    forced = os.environ.get("FORZA_CPU_TARGET", "").strip().lower()
    if forced in (CPU_BASELINE, CPU_V3):
        return forced
    return CPU_V3 if specs.x86_64_v3() is True else CPU_BASELINE


def current_build_id(root: Path, title_update: str = "") -> str | None:
    """Changes with the launcher version, the project sources, the SDK pin, whether (and
    which) Title Update 4 is used and the CPU target, so choosing or dropping the update, or
    moving the data folder to a PC with another CPU, asks for a rebuild."""
    proj = project_dir()
    if proj is None:
        return None
    return hashlib.sha1(f"{__version__}|{sources_hash(proj)}|{pins.SDK_TAG}|{PLAT}|{tu_id(title_update)}|"
                        f"{cpu_target()}".encode()).hexdigest()[:16]


def cpu_blocker(root: Path, install_dir: Path | None = None) -> str:
    """Why the game the launcher built cannot start on this CPU ('' = it can): it was built for x86-64-v3
    and this CPU lacks part of it. Unknown CPUs are let through: the game checks again at start."""
    if install_dir is not None and Path(install_dir) != runtime_dir(root):
        return ""
    if read_state(root).get("cpu_target") == CPU_V3 and specs.x86_64_v3() is False:
        return CPU_TOO_OLD
    return ""


def read_state(root: Path) -> dict:
    try:
        return json.loads((build_dir(root) / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def write_state(root: Path, state: dict):
    build_dir(root).mkdir(parents=True, exist_ok=True)
    (build_dir(root) / "state.json").write_text(json.dumps(state, indent=2))


def build_status(root: Path, title_update: str = ""):
    """('built' | 'stale' | 'none' | 'nosource', detail)"""
    cid = current_build_id(root, title_update)
    if cid is None:
        return "nosource", "The project sources are not installed next to the launcher."
    st = read_state(root)
    rt = runtime_dir(root) / ("forza" + EXE)
    if st.get("complete") and rt.is_file():
        if st.get("id") == cid:
            return "built", f"Built {st.get('when', '')}" + (" with Title Update 4" if st.get("tu4") else
                                                              " without a title update (no Horizon Rally, no 1000 Club)")
        if bool(st.get("tu4")) != bool(title_update):
            return "stale", ("A Title Update 4 package is chosen: rebuild to play Horizon Rally and the 1000 Club."
                             if title_update else "The title update was removed: rebuild for the base game.")
        built_for = st.get("cpu_target", CPU_BASELINE)
        if built_for != cpu_target():
            if cpu_blocker(root):
                return "stale", CPU_TOO_OLD
            if built_for == CPU_BASELINE:
                return "stale", "This CPU can run a faster build of the game (x86-64-v3): rebuild it."
            return "stale", "The game was built for x86-64-v3 CPUs; this PC gets the build for any CPU: rebuild it."
        return "stale", "A new version of the project needs a rebuild."
    if st.get("started"):
        return "partial", "A build was started and not finished; it resumes where it stopped."
    return "none", "The game has not been built yet."


# ---- running programs ------------------------------------------------------------------------

def kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def _windows_tree_memory(root_pid):
    """Summed working set (bytes) of a process and all its descendants (Win32 through ctypes:
    a Toolhelp snapshot for the parent links, GetProcessMemoryInfo for the memory)."""
    import ctypes
    from ctypes import wintypes

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    class COUNTERS(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ENTRY)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(COUNTERS), wintypes.DWORD]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)   # TH32CS_SNAPPROCESS
    if snap in (None, wintypes.HANDLE(-1).value):
        return 0
    parents = {}
    try:
        e = ENTRY()
        e.dwSize = ctypes.sizeof(ENTRY)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            parents[e.th32ProcessID] = e.th32ParentProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    tree, grew = {root_pid}, True
    while grew:
        grew = False
        for pid, ppid in parents.items():
            if ppid in tree and pid not in tree:
                tree.add(pid)
                grew = True
    total = 0
    for pid in tree:
        h = k32.OpenProcess(0x1000 | 0x0400, False, pid)   # QUERY_LIMITED_INFORMATION | QUERY_INFORMATION
        if not h:
            h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            continue
        try:
            c = COUNTERS()
            c.cb = ctypes.sizeof(COUNTERS)
            if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
                total += c.WorkingSetSize
        finally:
            k32.CloseHandle(h)
    return total


class ResourceMonitor(threading.Thread):
    """Samples the summed resident memory of the build's process tree: the session on Linux
    (/proc), the process and its descendants on Windows (Win32 through ctypes)."""

    def __init__(self, sid):
        super().__init__(daemon=True)
        self.sid, self.peak, self.stop_flag = sid, 0, threading.Event()

    def run(self):
        if IS_WIN:
            while not self.stop_flag.wait(2.0):
                try:
                    self.peak = max(self.peak, _windows_tree_memory(self.sid))
                except Exception:   # a monitor must never break the build
                    return
            return
        page = os.sysconf("SC_PAGE_SIZE")
        while not self.stop_flag.wait(2.0):
            total = 0
            for d in os.listdir("/proc"):
                if not d.isdigit():
                    continue
                try:
                    with open(f"/proc/{d}/stat") as f:
                        rest = f.read().rsplit(")", 1)[1].split()
                    if int(rest[3]) != self.sid:  # session id
                        continue
                    with open(f"/proc/{d}/statm") as f:
                        total += int(f.read().split()[1]) * page
                except (OSError, IndexError, ValueError):
                    continue
            self.peak = max(self.peak, total)


CURRENT_ROOT = None     # the data root of the build being run (for messages that name the folder)


def root_hint(cwd):
    return CURRENT_ROOT or cwd


def run(cmd, cwd, env, rep: Reporter, on_line=None, low_priority=False, monitor=None):
    """Run a command, streaming its output to rep.log; raises BuildError on failure and
    Cancelled when the player cancels."""
    rep.log("$ " + " ".join(str(c) for c in cmd))
    output_tail = ""
    error_lines = []

    def failure(message, code=None, kind=BuildError):
        error = kind(message)
        error.command = str(cmd[0])
        error.exit_code = code
        error.cwd = str(cwd)
        # Output is evidence, not a guessed diagnosis. The complete stream is
        # already in the build log; keep both early diagnostics and a bounded tail.
        context = (f"Command: {str(cmd[0])[:512]}\nWorking directory: {str(cwd)[:512]}"
                   + (f"\nExit code: {code}" if code is not None else ""))
        first = "\nFirst error lines:\n" + "\n".join(error_lines) if error_lines else ""
        tail = "\nLast command output:\n" + output_tail if output_tail else ""
        error.detail = (context + first + tail)[:8192]
        return error
    kw = {}
    if IS_WIN:
        kw["creationflags"] = 0x08000000 | 0x00000200 | (0x00004000 if low_priority else 0)
    else:
        kw["start_new_session"] = True
        if low_priority:
            kw["preexec_fn"] = lambda: os.nice(10)
    try:
        proc = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace", bufsize=1, **kw)
    except OSError as e:
        raise failure(f"Could not start {cmd[0]}: {e}") from e
    denied = []     # lines saying access was denied (Windows: OneDrive, antivirus, Controlled folder access)
    mon = None
    if monitor is not None:
        mon = ResourceMonitor(proc.pid)
        mon.start()
    stop = threading.Event()

    def watch():
        while not stop.wait(0.5):
            if rep.cancelled():
                kill_tree(proc)
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            output_tail = (output_tail + "\n" + line[-2000:])[-5500:]
            if len(error_lines) < 3 and re.search(
                    r"(?:^|\s)(?:fatal error|error(?:\s+[A-Z]+\d+)?):|^CMake Error|^FAILED:",
                    line, re.IGNORECASE):
                error_lines.append(line[:400])
            if IS_WIN and len(denied) < 3 and datafolder.ACCESS_DENIED_RE.search(line):
                denied.append(line)
            if on_line:
                on_line(line)
            rep.log(line)
        code = proc.wait()
    finally:
        stop.set()
        proc.stdout.close()
        if mon:
            mon.stop_flag.set()
            monitor["peak"] = max(monitor.get("peak", 0), mon.peak)
    if rep.cancelled():
        raise Cancelled()
    if code != 0:
        if denied:
            raise failure(datafolder.blocked_message(root_hint(cwd), denied[0]), code, FolderBlocked)
        if IS_WIN and CURRENT_ROOT:     # a denied folder can also look like "file not found" to a program
            why = datafolder.read_problem(CURRENT_ROOT)
            if why:
                raise failure(datafolder.blocked_message(CURRENT_ROOT, why), code, FolderBlocked)
        raise failure(f"{Path(str(cmd[0])).name} failed (exit code {code}); see the build log for full output.", code)


# ---- the build tree's file times -------------------------------------------------------------

def _rmtree(path: Path):
    """shutil.rmtree that also removes read-only files (Windows)."""
    def fix(func, p, *exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)


def clamp_future_times(roots, now: float | None = None, slack: float = 2.0) -> int:
    """Sets every file or folder under `roots` whose modification time is in the future to now;
    returns how many. CMake's regeneration rule (build.ninja depends on every CMake file it read)
    never becomes clean while one of them is newer than the clock, and ninja gives up after 100
    tries ("manifest 'build.ninja' still dirty after 100 tries", issue #35). Files get such times
    from installers (Inno Setup stamped the installed files with the build machine's local time,
    read as the player's: hours ahead west of UTC), from archives, and from a clock that was
    wrong while they were written."""
    now = time.time() if now is None else now
    n = 0
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for dirpath, dirs, files in os.walk(root):
            for name in dirs + files:
                p = os.path.join(dirpath, name)
                try:
                    st = os.lstat(p)
                    if st.st_mtime > now + slack and not os.path.islink(p):
                        os.utime(p, (min(st.st_atime, now), now))
                        n += 1
                except OSError:
                    continue
        try:
            if root.stat().st_mtime > now + slack:
                os.utime(root, (now, now))
                n += 1
        except OSError:
            pass
    return n


STAMP_NAME = ".forza-configure-stamp"


def configure_stamp(tools, sdk_ident: str, tu: bool, root: Path | None = None) -> str:
    """What the CMake build tree was configured with: the launcher version (its project sources,
    toolchain file and options), the SDK, the compilers, whether a title update is used, and the
    path CMake saw the folder by when that was a link to it (build_root). A tree configured with
    anything else is deleted and configured again, not resumed."""
    return hashlib.sha1("|".join([__version__, PLAT, pins.SDK_TAG, sdk_ident, str(bool(tu)),
                                  tools.cc, tools.cxx, tools.cmake, tools.ninja,
                                  json.dumps(tools.source, sort_keys=True)] +
                                 ([str(root)] if root is not None and root.parent == link_home() else [])
                                 ).encode()).hexdigest()


def reset_stale_build_tree(bdir: Path, stamp: str, rep: Reporter):
    """Deletes the CMake build tree `bdir` when it was configured by something else than `stamp`
    (or by a launcher too old to write one), which is what a resumed build after an update is."""
    if not bdir.exists():
        return
    try:
        old = (bdir / STAMP_NAME).read_text().strip()
    except OSError:
        old = ""
    if old != stamp:
        rep.log("The earlier build was made by another version of the launcher or toolchain: "
                "configuring from scratch.")
        _rmtree(bdir)


def write_configure_stamp(bdir: Path, stamp: str):
    try:
        (bdir / STAMP_NAME).write_text(stamp)
    except OSError:
        pass


DIRTY_MANIFEST = "still dirty after"


# ---- the steps ----------------------------------------------------------------------------

def _dl_progress(rep, lo, hi, what):
    def cb(done, total):
        if total:
            rep.progress(lo + (hi - lo) * done / total, f"{what}: {done / 1e6:.0f} of {total / 1e6:.0f} MB")
    return cb


LLVM_BIN_KEEP = {
    "clang", "clang++", "clang-cl", "clang-cpp", "clang-scan-deps", "lld", "ld.lld", "ld64.lld", "lld-link",
    "llvm-ar", "llvm-ranlib", "llvm-nm", "llvm-objcopy", "llvm-strip", "llvm-objdump", "llvm-readelf",
    "llvm-readobj", "llvm-lib", "llvm-rc", "llvm-cvtres", "llvm-mt", "llvm-dlltool", "llvm-symbolizer",
}


def llvm_member_wanted(name: str) -> bool:
    """The full LLVM release is 12 GB unpacked; the build needs the compiler driver, the linker,
    the archiver tools and the compiler's own headers (lib/clang)."""
    parts = Path(name).parts
    if parts[0] == "bin" and len(parts) == 2:
        base = parts[1][:-4] if parts[1].endswith(".exe") else parts[1]
        return base in LLVM_BIN_KEEP or re.fullmatch(r"clang-\d+", base) is not None
    return parts[0] in ("lib",) and len(parts) > 1 and parts[1] == "clang" or parts[0].upper().startswith("LICENSE")


def ensure_toolchain(opts: Options, rep: Reporter, lo, hi):
    tools = build_env.detect_tools(opts.root, opts.prefer_downloaded, max_age=0)
    for p in tools.problems:
        raise BuildError(p)
    for n in tools.notes:
        rep.log(n)
    if not tools.missing:
        for k, v in tools.source.items():
            rep.log(f"{k}: {v}")
        return tools
    for h in tools.hints:
        rep.log(h)
    cache = opts.root / "downloads"
    tdir = build_env.toolchain_dir(opts.root)
    n = len(tools.missing)
    for i, name in enumerate(tools.missing):
        a, b = lo + (hi - lo) * i / n, lo + (hi - lo) * (i + 1) / n
        mb = pins.SIZES_MB.get(name, 0)
        rep.status(f"Downloading {name} ({mb} MB)")
        rep.log(f"download {pins.DOWNLOADS[name][0]}")
        arc = fetch.download(name, cache, _dl_progress(rep, a, a + (b - a) * 0.6, name), rep.cancelled)
        rep.status(f"Unpacking {name}")
        if name.startswith("llvm"):
            dest = tdir / f"llvm-{pins.LLVM_VERSION}"
            fetch.unpack(name, arc, dest, strip=1, only=llvm_member_wanted,
                         progress=_dl_progress(rep, a + (b - a) * 0.6, b, "unpack"), cancelled=rep.cancelled)
            arc.unlink(missing_ok=True)  # 2 GB: not needed any more
        elif name.startswith("cmake"):
            fetch.unpack(name, arc, tdir / f"cmake-{pins.CMAKE_VERSION}", strip=1, cancelled=rep.cancelled)
        elif name.startswith("ninja"):
            fetch.unpack(name, arc, tdir / f"ninja-{pins.NINJA_VERSION}", cancelled=rep.cancelled)
            if not IS_WIN:
                os.chmod(tdir / f"ninja-{pins.NINJA_VERSION}" / "ninja", 0o755)
        rep.progress(b)
    tools = build_env.detect_tools(opts.root, opts.prefer_downloaded, max_age=0)
    for p in tools.problems:
        raise BuildError(p)
    if tools.missing:
        raise BuildError("The toolchain is incomplete after the download: " + ", ".join(tools.missing))
    for k, v in tools.source.items():
        rep.log(f"{k}: {v}")
    return tools


def sdk_identity(overlay: Path | None, dev: Path | None) -> str:
    ident = f"{pins.SDK_TAG}|{overlay.stat().st_size if overlay else (str(dev) if dev else 'none')}"
    if overlay is None and dev is not None:
        helper = dev / "lib/cmake/rexglue/rexglue_helpers.cmake"
        if helper.is_file():
            ident += "|cmake=" + hashlib.sha256(helper.read_bytes()).hexdigest()
    return ident


def ensure_sdk(opts: Options, rep: Reporter, lo, hi) -> Path:
    """The patched SDK prefix: the pinned nightly plus this project's patched plugin, runtime and
    codegen tool (Linux), or the project's own SDK build (Windows). Returns the prefix."""
    prefix = sdk_dir(opts.root)
    marker = prefix / ".forza-sdk-ready"
    overlay = sdk_overlay_archive()
    dev = dev_sdk_overlay()
    ident = sdk_identity(overlay, dev)
    if marker.is_file() and marker.read_text() == ident:
        rep.log(f"SDK ready: {prefix}")
        return prefix
    if overlay is None and dev is None:
        raise BuildError("The patched SDK files (sdk-overlay/rexglue-overlay-%s-amd64.tar.gz) are "
                         "not installed next to the launcher. Reinstall the launcher." % PLAT)
    shutil.rmtree(prefix, ignore_errors=True)
    stage = prefix.with_name(prefix.name + ".part")
    shutil.rmtree(stage, ignore_errors=True)
    if IS_WIN:
        rep.status("Unpacking the SDK")
        fetch.extract_tar(overlay, stage, cancelled=rep.cancelled)
    else:
        rep.status("Downloading the ReXGlue SDK (122 MB)")
        arc = fetch.download("sdk-linux", opts.root / "downloads", _dl_progress(rep, lo, lo + (hi - lo) * 0.6, "SDK"),
                             rep.cancelled)
        rep.status("Unpacking the SDK")
        fetch.extract_zip(arc, stage, strip=1, cancelled=rep.cancelled)
        if overlay is not None:
            fetch.extract_tar(overlay, stage, cancelled=rep.cancelled)
        else:  # development: the checkout's own overlay
            for rel in ("lib/librexgpu-xenos.so", "lib/librexruntime.so", "bin/rexglue",
                        "lib/cmake/rexglue/rexglue_helpers.cmake"):
                if rel.endswith(".cmake") and not (dev / rel).is_file():
                    continue  # an overlay made before it carried ReIGlue's CMake helpers
                dst = stage / rel
                dst.unlink(missing_ok=True)
                shutil.copy2((dev / rel).resolve(), dst)
        for rel in ("bin/rexglue", "lib/librexgpu-xenos.so", "lib/librexruntime.so"):
            if not (stage / rel).is_file():
                raise BuildError(f"The patched SDK is missing {rel}")
    os.replace(stage, prefix)
    marker.write_text(ident)
    rep.progress(hi)
    return prefix


def title_update_script() -> Path | None:
    for d in (paths.LAUNCHER_ROOT / "tools", paths.app_dir() / "project" / "tools", paths.REPO_ROOT / "tools"):
        if (d / "apply_title_update.py").is_file():
            return d / "apply_title_update.py"
    return None


TU_EXECUTABLES = ("default.xex", "XMediaFacade_default.xex", "SpeechFacade_default.xex")


def tu_dir(root: Path) -> Path:
    """Where the update is applied: the patched executables and the update's other files
    (media.zip). This folder is the game's update: (--update_data_root)."""
    return build_dir(root) / "tu"


def apply_title_update(opts: Options, rep: Reporter, game_files: Path) -> Path | None:
    """tools/apply_title_update.py BASE_XEX TU_PACKAGE OUT_XEX (docs/dlc.md): writes the patched
    default.xex and both facade DLLs, and the update's media.zip, into OUT_XEX's folder.
    Returns that folder, or None when no title update is used."""
    if not opts.title_update:
        return None
    pkg = Path(opts.title_update).expanduser()
    if not pkg.exists():
        raise BuildError(f"The title update package {pkg} does not exist.")
    script = title_update_script()
    if script is None:
        raise BuildError("This launcher has no tools/apply_title_update.py: reinstall the launcher, or "
                         "clear the title update to build the base game.")
    out = tu_dir(opts.root) / "default.xex"
    shutil.rmtree(out.parent, ignore_errors=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    rep.status("Applying your title update")
    cmd = [sys.executable] + (["--run-script"] if paths.FROZEN else []) + [
        script, game_files / "default.xex", pkg, out]
    run(cmd, opts.root, cleanenv.clean_environment(), rep, low_priority=opts.low_priority)
    missing = [n for n in TU_EXECUTABLES if not (out.parent / n).is_file()]
    if missing:
        raise BuildError("The title update step did not produce " + ", ".join(missing) + ".")
    return out.parent


def native_tuning_script() -> Path | None:
    """Prefer the tool belonging to the shipped project (also used in its source hash)."""
    proj = project_dir()
    locations = ([proj / "tools"] if proj else []) + [paths.LAUNCHER_ROOT / "tools"]
    for folder in locations:
        if all((folder / name).is_file() for name in NATIVE_TUNING_FILES):
            return folder / "native_tuning_assets.py"
    return None


def native_menu_script() -> Path | None:
    """Prefer the tool belonging to the shipped project (also used in its source hash)."""
    proj = project_dir()
    locations = ([proj / "tools"] if proj else []) + [paths.LAUNCHER_ROOT / "tools"]
    for folder in locations:
        if all((folder / name).is_file() for name in NATIVE_MENU_FILES):
            return folder / "native_menu_scene.py"
    return None


def _scoped_path(parent: Path, child: Path) -> Path:
    """Check resolved targets before replacing or removing launcher-owned directories."""
    parent, child = parent.resolve(), child.resolve()
    if child == parent or not child.is_relative_to(parent):
        raise BuildError(f"Build output {child} is outside {parent}.")
    return child


def _publish_runtime_update(root: Path, source: Path | None, rep: Reporter, name="update"):
    """Copy completely before replacing the old update; retain it if publishing fails."""
    if name not in ("update", "update-native", "update-menu"):
        raise BuildError("Unsupported runtime update destination.")
    rt = runtime_dir(root).resolve()  # build/runtime may be linked to another drive.
    rt.mkdir(parents=True, exist_ok=True)
    target = _scoped_path(rt, rt / name)
    if target != rt / name:
        raise BuildError(f"runtime/{name} must be a regular directory, not a directory link.")
    if source is not None:
        source = source.resolve()
        if source.is_relative_to(rt) or rt.is_relative_to(source):
            raise BuildError("The source title update must be outside the runtime directory.")
    temporary = _scoped_path(rt, Path(tempfile.mkdtemp(prefix=".update-install-", dir=rt)))
    candidate, previous = temporary / "new", temporary / "previous"
    retain_previous = False
    try:
        if source is not None:
            shutil.copytree(source, candidate)
        if rep.cancelled():
            raise Cancelled()
        if target.exists():
            os.replace(target, previous)
        try:
            if source is not None:
                os.replace(candidate, target)
        except OSError:
            if previous.exists():
                try:
                    os.replace(previous, target)
                except OSError as error:
                    retain_previous = True
                    raise BuildError(f"Could not restore the previous update; it is preserved at {previous}: "
                                     f"{error}") from error
            raise
    finally:
        if not retain_previous:
            _rmtree(_scoped_path(rt, temporary))


def stage_runtime_update(opts: Options, rep: Reporter, tu: Path | None) -> bool:
    """Prepare original native tuning resources from FH1 only; fall back to the stock TU.

    The preparation tool only sees a new private output directory. Its source TU is also
    the codegen input and is never modified. Return whether native resources were installed.
    """
    work = build_dir(opts.root).resolve()
    rt = runtime_dir(opts.root)

    def record(value):
        try:
            tuning_status.write(rt, value)
        except OSError as error:
            rep.log(f"Could not save tuning diagnostic: {error}")

    def unavailable(value):
        record(value)
        rep.log(tuning_status.summary(value) + f" Diagnostic: {value['code']}. Installing the stock FH1 update.")
        if value["detail"]:
            rep.log("Tuning preparation details: " + value["detail"])
        _publish_runtime_update(opts.root, None, rep, "update-native")
        return False

    if tu is not None and work.is_relative_to(tu.resolve()):
        raise BuildError("The source title update must not contain the build workspace.")
    # Always install the unmodified update. Restart-only options select the
    # independent prepared overlay at launch, without requiring a rebuild.
    _publish_runtime_update(opts.root, tu, rep)
    if tu is None:
        return unavailable(tuning_status.failure("no_title_update", "This build has no Title Update 4 selected.",
                           "Select your FH1 Title Update 4 package in Setup, then Build."))
    work.mkdir(parents=True, exist_ok=True)
    script = native_tuning_script()
    if script is None:
        return unavailable(tuning_status.failure("missing_tools", "This launcher is missing the tuning preparation tool.",
                           "Reinstall the current launcher release, then Build with Title Update 4 selected."))
    with tempfile.TemporaryDirectory(prefix="native-tuning-", dir=work) as directory:
        prepared = Path(directory) / "overlay"
        rep.status("Preparing native tuning resources from your FH1 update")
        output_lines = []
        diagnostic = None

        def preparation_line(line):
            nonlocal diagnostic
            output_lines.append(line[-1000:])
            del output_lines[:-8]
            diagnostic = tuning_status.tool_failure(line) or diagnostic

        try:
            cmd = [sys.executable] + (["--run-script"] if paths.FROZEN else []) + [
                script, tu, "--output", prepared]
            run(cmd, opts.root, cleanenv.clean_environment(), rep, low_priority=opts.low_priority,
                on_line=preparation_line)
            required = (*TU_EXECUTABLES, "media.zip", "native-tuning-provenance.json")
            missing = [name for name in required if not (prepared / name).is_file()]
            if missing:
                raise BuildError("Preparation did not produce " + ", ".join(missing))
            report = json.loads((prepared / "native-tuning-provenance.json").read_text(encoding="utf-8"))
            if not isinstance(report, dict) or report.get("schema") != 1 or \
                    not isinstance(report.get("source_archive_sha256"), str) or \
                    len(report["source_archive_sha256"]) != 64 or not isinstance(report.get("entries"), list):
                raise BuildError("Preparation produced an unrecognized resource manifest")
        except (BuildError, OSError, ValueError) as error:
            if diagnostic is None:
                denied = isinstance(error, (PermissionError, FolderBlocked))
                diagnostic = tuning_status.failure(
                    "access_denied" if denied else "preparation_failed",
                    "Tuning preparation could not access a required file." if denied else "Tuning resource preparation failed.",
                    "Check the file access error and your selected data folder." if denied else
                    "Send the build log, including the preparation error; another rebuild alone may not fix it.",
                    str(error) + ("\n" + "\n".join(output_lines) if output_lines else ""))
            return unavailable(diagnostic)
        _publish_runtime_update(opts.root, prepared, rep, "update-native")
    rep.log("Installed native tuning resources prepared from FH1; no other game files are needed.")
    record(tuning_status.failure("ready", "Tuning resources were prepared successfully.", ""))
    return True


def stage_native_menu_update(opts: Options, rep: Reporter, tu: Path | None) -> bool:
    """Prepare original native menu resources from FH1 only; fall back to the stock TU.

    The preparation tool only sees a new private output directory. Its source TU is also
    the codegen input and is never modified. Return whether native resources were installed.
    """
    work = build_dir(opts.root).resolve()
    if tu is not None and work.is_relative_to(tu.resolve()):
        raise BuildError("The source title update must not contain the build workspace.")
    if tu is None:
        _publish_runtime_update(opts.root, None, rep, "update-menu")
        return False
    work.mkdir(parents=True, exist_ok=True)
    script = native_menu_script()
    if script is None:
        rep.log("Native menu unavailable: preparation tools are missing; installing the stock FH1 update.")
        _publish_runtime_update(opts.root, None, rep, "update-menu")
        return False
    with tempfile.TemporaryDirectory(prefix="native-menu-", dir=work) as directory:
        prepared = Path(directory) / "overlay"
        rep.status("Preparing native menu resources from your FH1 update")
        try:
            cmd = [sys.executable] + (["--run-script"] if paths.FROZEN else []) + [
                script, tu, "--output", prepared]
            run(cmd, opts.root, cleanenv.clean_environment(), rep, low_priority=opts.low_priority)
            required = (*TU_EXECUTABLES, "media.zip", "native-menu-provenance.json")
            missing = [name for name in required if not (prepared / name).is_file()]
            if missing:
                raise BuildError("Preparation did not produce " + ", ".join(missing))
            report = json.loads((prepared / "native-menu-provenance.json").read_text(encoding="utf-8"))
            if not isinstance(report, dict) or report.get("schema") != 1 or \
                    report.get("backend") != "fh1-native-menu-resources":
                raise BuildError("Preparation produced an unrecognized resource manifest")
        except (BuildError, OSError, ValueError) as error:
            rep.log(f"Native menu unavailable: {error}; installing the stock FH1 update.")
            _publish_runtime_update(opts.root, None, rep, "update-menu")
            return False
        _publish_runtime_update(opts.root, prepared, rep, "update-menu")
    rep.log("Installed native menu resources prepared from FH1; no other game files are needed.")
    return True


def sync_sources(opts: Options, rep: Reporter, game_files: Path, tu: Path | None = None) -> Path:
    """Copies the project sources into the build folder, pointing the manifests at the player's
    files: forza_manifest.toml at the extracted game, forza_manifest_tu4.toml (used with a title
    update) at the folder apply_title_update.py wrote."""
    proj = project_dir()
    if proj is None:
        raise BuildError("The project sources (project/forza-recomp) are not installed next to the launcher.")
    dest = build_dir(opts.root) / "forza-recomp"
    dest.mkdir(parents=True, exist_ok=True)
    changed = 0
    wanted = set()
    for rel in source_files(proj):
        src, dst = proj / "forza-recomp" / rel, dest / rel
        wanted.add(rel)
        data = src.read_bytes()
        if rel.as_posix().startswith("forza_manifest") and rel.suffix == ".toml":
            if tu is not None:   # file_path x3 and game_root of the TU4 manifest
                data = data.replace(b"../extracted/tu4", tu.resolve().as_posix().encode())
            data = data.replace(b"../extracted/game", game_files.resolve().as_posix().encode())
        if not dst.is_file() or dst.read_bytes() != data:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)  # unchanged files keep their time, so ninja rebuilds only changes
            changed += 1
    rep.log(f"project sources: {len(wanted)} files, {changed} updated")
    return dest


def sync_toolchain_file(root: Path) -> Path:
    """The Windows toolchain file, copied into the build folder: CMake reads it again on every
    run, and the copy in the installation has the installer's file time (in the future west of
    UTC, issue #35), which keeps build.ninja dirty."""
    src = project_dir() / "tools" / "cmake" / "windows-native-clang-cl.cmake"
    dst = build_dir(root) / "toolchain" / "windows-native-clang-cl.cmake"
    data = src.read_bytes()
    if not dst.is_file() or dst.read_bytes() != data:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
    return dst


# The MSVC version clang-cl assumes when it finds no Visual Studio at all (LLVM 23: _MSC_VER
# 1933), which is what xwin builds were tested with; pinned so an old Visual Studio on the
# machine cannot lower it (#173).
XWIN_MSVC_COMPAT = "19.33"


def xwin_splat_command(exe, root: Path, xdir: Path) -> list:
    """The xwin command that unpacks the CRT and SDK into `xdir` without making any link, so a
    normal user can run it with Developer Mode off (#634, #739). --use-winsysroot-style: the plain
    layout always links sdk/include/<sdk version> and sdk/lib/<sdk version> to their parent
    (`symlink_on_windows_too` in xwin's splat.rs, 0.10.0), --disable-symlinks or not, which fails
    with os error 1314; the /winsysroot layout puts the files in versioned folders and links nothing.
    --disable-symlinks: the case aliases, which a file system that ignores case does not need."""
    return [exe, "--accept-license", "--cache-dir", root / "downloads" / "xwin-cache", "splat",
            "--disable-symlinks", "--use-winsysroot-style", "--output", xdir]


def msvc_environment(opts: Options, rep: Reporter, tools, lo, hi):
    """Windows: the environment (INCLUDE, LIB, PATH) of the MSVC CRT and Windows SDK, from an
    installed Visual Studio, else from xwin, which needs the player to accept Microsoft's license."""
    env, _path = build_env.find_msvc(rep.log)
    if env:
        return env
    xdir = opts.root / "xwin"
    dirs = build_env.xwin_dirs(xdir)
    if dirs is None:
        if not opts.accept_ms_license:
            raise BuildError("The Microsoft C++ runtime and Windows SDK are needed and are not installed. "
                             "Accept Microsoft's license in the setup page to download them, or install "
                             "Visual Studio 2022 (or its Build Tools) with the C++ and Windows SDK workloads.")
        arc = fetch.download("xwin-windows", opts.root / "downloads", cancelled=rep.cancelled)
        tooldir = build_env.toolchain_dir(opts.root) / f"xwin-{pins.XWIN_VERSION}"
        fetch.unpack("xwin-windows", arc, tooldir, strip=1)
        exe = next(tooldir.rglob("xwin.exe"))
        rep.status("Downloading the Microsoft C++ runtime and Windows SDK")
        # A plain-layout splat that stopped half way (the 0.2.8 error of #739) leaves crt/ and most
        # of sdk/; xwin itself only clears the folders of the layout it makes.
        (xdir / build_env.XWIN_DONE).unlink(missing_ok=True)
        for old in ("crt", "sdk", "vcrd"):
            shutil.rmtree(xdir / old, ignore_errors=True)
        run(xwin_splat_command(exe, opts.root, xdir), opts.root, cleanenv.clean_environment(), rep)
        xdir.mkdir(parents=True, exist_ok=True)
        (xdir / build_env.XWIN_DONE).write_text(f"xwin {pins.XWIN_VERSION} splat --use-winsysroot-style\n")
        dirs = build_env.xwin_dirs(xdir)
        if dirs is None:
            raise BuildError(f"xwin finished but the Microsoft C++ runtime or Windows SDK is missing from {xdir}. "
                             "Delete that folder and build again.")
    inc, lib = dirs
    env = cleanenv.clean_environment()
    env["INCLUDE"] = os.pathsep.join(map(str, inc))
    env["LIB"] = os.pathsep.join(map(str, lib))
    # clang-cl takes the MSVC version from a Visual Studio it finds, which here is none or one
    # too old for this CRT (#173); the toolchain file passes this on as -fms-compatibility-version.
    env["FORZA_MSVC_COMPAT"] = XWIN_MSVC_COMPAT
    return env


NINJA_RE = re.compile(r"^\[(\d+)/(\d+)\]")


def compile_passes(opts: Options, rep: Reporter, tools, src: Path, bdir: Path, env, monitor):
    """Compile. Codegen may rewrite the DLL modules' sources during the first pass, which ninja only
    sees on its next run, so it runs twice (the second pass is a no-op when nothing changed).
    A BuildError with dirty_manifest set: ninja gave up regenerating build.ninja."""
    for attempt in (1, 2):
        rep.status("Compiling" + (" (second pass)" if attempt == 2 else ": this takes a long time"))
        dirty = {"seen": False}
        failed = []

        def on_line(line):
            if DIRTY_MANIFEST in line:
                dirty["seen"] = True
            if line.startswith("FAILED: "):
                failed.append(line)
            m = NINJA_RE.match(line)
            if m:
                n, total = int(m.group(1)), int(m.group(2))
                if total:
                    rep.progress(0.24 + 0.70 * n / total, f"compiling {n} of {total}")
        try:
            run([tools.ninja, "-C", bdir, f"-j{opts.jobs}"], src, env, rep, on_line=on_line,
                low_priority=opts.low_priority, monitor=monitor)
        except BuildError as e:
            e.dirty_manifest = dirty["seen"]
            e.failed = list(failed)
            raise


def regpu_only_failure(failed) -> bool:
    """Whether every ninja "FAILED: <output>" line is the ReGPU plugin's (regpu-plugin/, the DLL)."""
    outputs = []
    for line in failed:
        rest = re.sub(r"^FAILED:\s*(?:\[code=\d+\]\s*)?", "", line).strip().replace("\\", "/")
        outputs.append(rest.split(" ")[0])
    return bool(outputs) and all(o.startswith("regpu-plugin/") or "rexgpu-regpu" in o for o in outputs)


def run_build(opts: Options, rep: Reporter, game_files: Path) -> Path:
    """The whole pipeline; returns the runtime directory. Safe to cancel and run again.
    A folder that cannot be written (Windows: OneDrive, antivirus, Controlled folder access; Linux: a
    read-only mount, in the Flatpak a folder it may only read, #717) ends in FolderBlocked with a
    message that says so, before the build starts (a file is written there first) or during it."""
    global CURRENT_ROOT
    CURRENT_ROOT = opts.root
    for lvl, text in datafolder.check_folder(opts.root, need_toolchain=False):
        if lvl == "error" and text.startswith("Cannot write"):
            raise FolderBlocked(text)
        if lvl == "warn":
            rep.log("Warning: " + text)
    try:
        # A folder CMake cannot build under (#691) is built through a link to it; messages that name
        # the folder (CURRENT_ROOT) keep its own path.
        root = build_root(opts.root, rep)
        if root == opts.root:
            return _run_build(opts, rep, game_files)
        return opts.root / _run_build(dataclasses.replace(opts, root=root), rep, game_files).relative_to(root)
    except OSError as e:
        if IS_WIN and datafolder.is_access_denied_error(e) or \
                not IS_WIN and datafolder.is_read_only_error(e) and _inside(e.filename, opts.root):
            paths.log_error("build: access denied", e)
            raise FolderBlocked(datafolder.blocked_message(opts.root, str(e))) from e
        raise


def _inside(name, root) -> bool:
    """The file an OSError names is in the data folder, also through build_root's link (or there is no
    name to tell)."""
    if not name:
        return True
    try:
        return Path(os.path.realpath(name)).is_relative_to(Path(os.path.realpath(root)))
    except (TypeError, ValueError):
        return False


def _run_build(opts: Options, rep: Reporter, game_files: Path) -> Path:
    t0 = time.time()
    root = opts.root
    root.mkdir(parents=True, exist_ok=True)
    if not (game_files / "default.xex").is_file():
        raise BuildError(f"No default.xex in {game_files}: extract your game files first.")
    from . import installer   # the executables must be the supported code (another printing is fine)
    ok, why = installer.check_game_folder(game_files)
    if not ok:
        raise BuildError(why)
    rep.log(why)
    cid = current_build_id(root, opts.title_update)
    state = read_state(root)
    if opts.clean:
        shutil.rmtree(build_dir(root), ignore_errors=True)
        state = {}
    write_state(root, {"started": time.time(), "id": cid, "complete": False})
    monitor = {}

    rep.status("Checking the toolchain")
    tools = ensure_toolchain(opts, rep, 0.0, 0.14)
    rep.status("Preparing the SDK")
    sdk = ensure_sdk(opts, rep, 0.14, 0.17)
    tu = apply_title_update(opts, rep, game_files)
    rep.status("Preparing the project sources")
    src = sync_sources(opts, rep, game_files, tu)
    env = build_env.tool_env(tools)
    cmake_extra = []
    if IS_WIN:
        env.update(msvc_environment(opts, rep, tools, 0.17, 0.18))
        llvm_bin = str(Path(tools.cc).parent)
        env["PATH"] = os.pathsep.join([llvm_bin] + [p for p in tools.path_dirs if p != llvm_bin] +
                                      [env.get("PATH", "")])
        env["FORZA_LLVM_BIN"] = llvm_bin
        env["PATH"], dropped = build_env.native_path(env["PATH"])   # vcvars re-adds the player's PATH (#100)
        if dropped:
            rep.log("PATH: left out MSYS2/Cygwin folders: " + "; ".join(dropped))
        cmake_extra += ["-DCMAKE_TOOLCHAIN_FILE=" + sync_toolchain_file(root).as_posix()]
    else:
        cmake_extra += ["-DCMAKE_CXX_COMPILER=" + tools.cxx]
    rexglue = sdk / "bin" / ("rexglue" + EXE)
    # With a title update the project builds from its TU4 manifest (the patched executables, own
    # hints_tu4/ and generated_tu4/), not the base manifest: the base hints are at base addresses.
    # Always passed, so a build folder used for the other version is reconfigured.
    manifest = "forza_manifest.toml"
    if tu is not None:
        manifest = "forza_manifest_tu4.toml"
        if not (src / manifest).is_file():
            raise BuildError("These project sources have no Title Update 4 support (forza_manifest_tu4.toml).")
    cmake_extra.append("-DFORZA_TITLE_UPDATE=" + ("4" if tu is not None else "0"))
    cmake_extra.append(build_id_arg())
    rep.log("building " + ("with Title Update 4 (" + manifest + ")" if tu is not None else "the base game"))
    target = cpu_target()
    cmake_extra.append("-DFORZA_CPU_TARGET=" + target)
    rep.log("CPU target: " + ("x86-64-v3 (this CPU has AVX2, FMA, BMI2 and MOVBE)" if target == CPU_V3 else
                              "baseline (SSE4.1, any x86-64 CPU)"))

    # Codegen: recompile the player's default.xex (and its two DLLs) to C++.
    rep.status("Generating code from your default.xex (a minute or two)")
    rep.progress(0.18, "codegen")
    run([rexglue, "codegen", manifest], src, env, rep, low_priority=opts.low_priority)
    rep.progress(0.22)

    bdir = src / "out" / "build" / PLAT
    overlay = sdk_overlay_archive()
    dev = dev_sdk_overlay()
    sdk_ident = sdk_identity(overlay, dev)
    stamp = configure_stamp(tools, sdk_ident, tu is not None, root)

    # ReGPU, the experimental Direct3D 12 renderer (Settings > Graphics API, regpu.py), is built with
    # every Windows build, so choosing it needs no new build. It is optional: when only its plugin
    # fails to compile, the game is built without it.
    regpu_plugin = IS_WIN
    # A tree CMake configured for another version of the launcher (a build that stopped, then an
    # update) is deleted, not resumed; so is one ninja cannot get clean (see compile_pass).
    attempt = 0
    while attempt < 2:
        attempt += 1
        reset_stale_build_tree(bdir, stamp, rep)
        fixed = clamp_future_times([src, sdk, build_dir(root) / "toolchain", build_env.toolchain_dir(root),
                                    root / "xwin"])
        if fixed:
            rep.log(f"{fixed} files had a modification time in the future (the clock of this computer is "
                    "behind, or an installer stamped them): set to now.")
        rep.status("Configuring")
        run([tools.cmake, "-S", src, "-B", bdir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
             f"-DCMAKE_PREFIX_PATH={sdk.as_posix()}", f"-Drexglue_DIR={(sdk / 'lib/cmake/rexglue').as_posix()}",
             f"-DFORZA_REXGLUE={rexglue.as_posix()}",
             f"-DCMAKE_MAKE_PROGRAM={Path(tools.ninja).as_posix()}"] +
            ([f"-DFORZA_XENOS_PLUGIN={(sdk / 'lib/librexgpu-xenos.so').as_posix()}"] if not IS_WIN else
             [f"-DFORZA_REGPU_PLUGIN={'ON' if regpu_plugin else 'OFF'}"]) +
            cmake_extra, src, env, rep, low_priority=opts.low_priority)
        write_configure_stamp(bdir, stamp)
        rep.progress(0.24)
        try:
            compile_passes(opts, rep, tools, src, bdir, env, monitor)
            break
        except BuildError as e:
            if regpu_plugin and regpu_only_failure(getattr(e, "failed", ())):
                rep.log("Warning: the experimental ReGPU renderer did not compile (the lines above); building "
                        "the game without it. Settings > Graphics > Graphics API > ReGPU then plays with Vulkan.")
                regpu_plugin = False
                (bdir / "rexgpu-regpu.dll").unlink(missing_ok=True)
                attempt -= 1
                continue
            if not getattr(e, "dirty_manifest", False):
                raise
            if attempt == 2:
                raise BuildError(
                    "ninja keeps finding build.ninja out of date after configuring again from scratch "
                    "(\"still dirty after 100 tries\"): a file CMake reads has a modification time newer than "
                    "this computer's clock. Check the date, time and time zone in Windows settings, then press "
                    f"Resume build; if it comes back, delete the folder {build_dir(root)} and build again, and "
                    "attach launcher.log to the issue.") from None
            rep.log("ninja says build.ninja is still dirty: deleting the CMake build folder and configuring "
                    "again (once).")
            _rmtree(bdir)

    rep.progress(0.95)

    rep.status("Installing")
    rt = stage_runtime(opts, rep, bdir, sdk)
    # runtime/update is the game's update: (--update_data_root): the patched executables and the
    # update's media.zip. A base build has none.
    native_tuning = stage_runtime_update(opts, rep, tu)
    native_menu = stage_native_menu_update(opts, rep, tu)
    wall = time.time() - t0
    peak = monitor.get("peak", 0)
    write_state(root, {"started": t0, "id": cid, "complete": True,
                       "when": time.strftime("%Y-%m-%d %H:%M"), "tu4": tu is not None, "cpu_target": target,
                       "native_tuning": native_tuning, "native_menu": native_menu,
                       "seconds": round(wall), "peak_rss_gb": round(peak / 2 ** 30, 1), "jobs": opts.jobs})
    (rt / "version.txt").write_text(f"built {time.strftime('%Y-%m-%d')} (sources {sources_hash(project_dir())[:8]})\n")
    rep.progress(1.0, "done")
    rep.log(f"Build finished in {wall / 60:.1f} min, jobs {opts.jobs}, "
            f"peak build memory {peak / 2 ** 30:.1f} GB")
    return rt


def stage_runtime(opts: Options, rep: Reporter, bdir: Path, sdk: Path) -> Path:
    """Copy what the game needs to run into runtime/ so the build tree can be deleted."""
    rt = runtime_dir(opts.root)
    rt.mkdir(parents=True, exist_ok=True)
    if (bdir / "licenses").is_dir():
        shutil.copytree(bdir / "licenses", rt / "licenses", dirs_exist_ok=True)

    telemetry = bdir / "forza-telemetry.json"
    if telemetry.is_file():
        shutil.copy2(telemetry, rt / telemetry.name)
    else:
        (rt / telemetry.name).unlink(missing_ok=True)
    notice = bdir / "LICENSE-Pinyon-Shift"
    if notice.is_file():
        shutil.copy2(notice, rt / notice.name)
    # The texture compressor (#413, forza-recomp/texcompress) the texture packs page runs.
    tool = bdir / ("forza-texcompress" + EXE)
    if tool.is_file():
        tmp = rt / ("." + tool.name + ".new")
        shutil.copy2(tool, tmp)
        os.replace(tmp, rt / tool.name)
    if IS_WIN:
        # rexgpu-regpu.dll and its d3dcompiler_47.dll: the optional ReGPU plugin (FORZA_REGPU_PLUGIN);
        # its licenses come with licenses/ above.
        # A plugin from an earlier build is removed when this one has none (it would not match forza.exe).
        plugin = [bdir / n for n in ("rexgpu-regpu.dll", "d3dcompiler_47.dll") if (bdir / n).is_file()]
        if not (bdir / "rexgpu-regpu.dll").is_file():
            (rt / "rexgpu-regpu.dll").unlink(missing_ok=True)
        for f in [bdir / "forza.exe"] + sorted(bdir.glob("forza_*.dll")) + plugin:
            shutil.copy2(f, rt / f.name)
        for dll in sorted((sdk / "bin").glob("*.dll")):
            if not dll.name.endswith(("d.dll", "rd.dll")):
                shutil.copy2(dll, rt / dll.name)
        ngx_license = sdk / "share" / "licenses" / "nvidia-dlss" / "LICENSE.txt"
        if ngx_license.is_file():
            shutil.copy2(ngx_license, rt / "NVIDIA-DLSS-LICENSE.txt")
        fsr3_license = sdk / "share" / "licenses" / "amd-fidelityfx-fsr3" / "LICENSE.txt"
        if fsr3_license.is_file():
            shutil.copy2(fsr3_license, rt / "AMD-FidelityFX-LICENSE.txt")
        capabilities = sdk / "bin" / "fh1-native-dlss.json"
        if capabilities.is_file():
            shutil.copy2(capabilities, rt / capabilities.name)
        else:
            (rt / "fh1-native-dlss.json").unlink(missing_ok=True)
        return rt
    modules = ["forza_XMediaFacade_default", "forza_SpeechFacade_default"]
    for name in ["forza"] + modules:
        tmp = rt / ("." + name + ".new")
        shutil.copy2(bdir / name, tmp)
        os.replace(tmp, rt / name)
    for name in ("librexruntime.so", "librexgpu-xenos.so"):
        tmp = rt / ("." + name + ".new")
        shutil.copy2((sdk / "lib" / name).resolve(), tmp)
        os.replace(tmp, rt / name)
    # The OpenXR loader for the VR setting, when the overlay carries one (docs/vr.md, Packaging);
    # without it the game uses the system's libopenxr_loader.so.1.
    loader = sdk / "lib" / "libopenxr_loader.so.1"
    if loader.exists():
        tmp = rt / ".libopenxr_loader.so.1.new"
        shutil.copy2(loader.resolve(), tmp)
        os.replace(tmp, rt / loader.name)
    # the plugin comes out of the SDK's build tree, whose runpath points into that tree
    script = rt / ".rpath.cmake"
    script.write_text(f'file(RPATH_SET FILE "{(rt / "librexgpu-xenos.so").as_posix()}" NEW_RPATH "$ORIGIN")\n')
    cmake = shutil.which("cmake") or str(build_env.downloaded_paths(opts.root)["cmake"])
    subprocess.run([cmake, "-P", str(script)], capture_output=True, env=cleanenv.clean_environment())
    script.unlink(missing_ok=True)
    return rt


def clean_build_tree(root: Path) -> int:
    """Delete the build tree (generated code, objects): the runtime keeps working. Returns bytes freed."""
    b = build_dir(root)
    size = sum(f.stat().st_size for f in b.rglob("*") if f.is_file()) if b.exists() else 0
    shutil.rmtree(b / "forza-recomp" / "out", ignore_errors=True)
    shutil.rmtree(b / "forza-recomp" / "generated", ignore_errors=True)
    shutil.rmtree(b / "forza-recomp" / "generated_tu4", ignore_errors=True)
    return size
