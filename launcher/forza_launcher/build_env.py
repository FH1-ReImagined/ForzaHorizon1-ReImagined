"""What the machine has: memory, cores, disk, and the tools the build needs."""
import ctypes
import os
import re
import shutil
import subprocess
import threading
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import cleanenv, paths, pins

MB = 1 << 20
GB = 1 << 30


def ram_bytes():
    """(total, available) physical memory in bytes."""
    if sys.platform == "win32":
        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("tpf", ctypes.c_ulonglong),
                        ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong),
                        ("av", ctypes.c_ulonglong), ("ex", ctypes.c_ulonglong)]
        m = MS()
        m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.total, m.avail
    total = avail = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
    except OSError:
        pass
    return total, avail


def free_disk(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


# Rough needs, from a build of this project: the extracted game 7.6 GB, the build tree about
# 4 GB, the SDK 1.5 GB, and a downloaded toolchain 6 GB more.
NEED_DISK_GB = 15
NEED_DISK_TOOLCHAIN_GB = 3
# Peak build memory is a base (cmake, the linker, the SDK step) plus a share per compile job. Measured
# (docs/launcher.md): 1.5 GB at -j3 (Windows), 1.8 to 2.0 GB at -j4, 3.2 GB at -j8 (Linux); a line of
# 0.8 GB + 0.35 GB per job sits just above all three. The game itself needs about 1.1 GB, but only while
# playing, not while building.
RAM_BASE_GB = 0.8
RAM_PER_JOB_GB = 0.35
RAM_MIN_TOTAL_GB = 2.0      # below this even -j1 plus the system does not fit


def ram_need_gb(jobs):
    """Peak memory (GB) of a build with this many jobs."""
    return RAM_BASE_GB + RAM_PER_JOB_GB * max(1, jobs)


def jobs_for_ram(avail_gb):
    """The most jobs whose build fits in this much free memory (at least 1)."""
    return max(1, int((avail_gb - RAM_BASE_GB) / RAM_PER_JOB_GB))


def default_jobs():
    cores = os.cpu_count() or 2
    _total, avail = ram_bytes()
    by_ram = jobs_for_ram(avail / GB) if avail else cores
    return max(1, min(cores, by_ram, 16))


def memory_check(jobs, total=None, avail=None):
    """(level, text, suggested_jobs) for a build with `jobs` jobs; level is 'ok', 'warn' or 'error'.
    Error only when even one job cannot fit in the installed memory; a shortage of free memory is a
    warning with a lower job count to use."""
    if total is None:
        total, avail = ram_bytes()
    if not total:
        return "ok", "", jobs
    text = f"Memory: {total / GB:.0f} GB installed, {avail / GB:.1f} GB free"
    need = ram_need_gb(jobs)
    if total < RAM_MIN_TOTAL_GB * GB:
        return "error", text + f" (a build needs at least {ram_need_gb(1):.1f} GB even with one job)", 1
    fit = min(jobs, jobs_for_ram(avail / GB))
    if avail / GB >= need:
        return "ok", text + f" (about {need:.1f} GB for {jobs} jobs)", jobs
    if avail / GB >= ram_need_gb(1):
        return "warn", text + (f" (about {need:.1f} GB for {jobs} jobs: use {fit} jobs or fewer, "
                               "or close other programs)"), fit
    return "warn", text + (f" (even one job needs about {ram_need_gb(1):.1f} GB: "
                           "close other programs)"), 1


def preflight(root: Path, game_files_ready: bool, need_toolchain: bool, jobs=None, memory=True):
    """[(level, text)] with level 'error', 'warn' or 'ok'."""
    out = []
    if memory:
        lvl, text, _fit = memory_check(jobs or default_jobs())
        if text:
            out.append((lvl, text))
    need = NEED_DISK_GB + (NEED_DISK_TOOLCHAIN_GB if need_toolchain else 0) - (8 if game_files_ready else 0)
    free = free_disk(root) / GB
    out.append(("ok" if free >= need else "error",
                f"Disk: {free:.0f} GB free where the game is built, about {need} GB needed"))
    cores = os.cpu_count() or 1
    out.append(("ok" if cores >= 4 else "warn",
                f"CPU: {cores} cores" + ("" if cores >= 4 else " (the build will take hours)")))
    return out


@dataclass
class Tools:
    cc: str = ""
    cxx: str = ""
    cmake: str = ""
    ninja: str = ""
    path_dirs: list = field(default_factory=list)  # prepended to PATH
    source: dict = field(default_factory=dict)      # tool -> "system" | "downloaded"
    missing: list = field(default_factory=list)     # download names still needed
    problems: list = field(default_factory=list)    # things only the user can fix
    hints: list = field(default_factory=list)       # optional improvements (shown as warnings)
    notes: list = field(default_factory=list)       # log lines: which tool was skipped and why


def _version_of(cmd, pattern):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                             env=cleanenv.clean_environment()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(pattern, out)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


# ---- Windows: tools from MSYS2 / Cygwin must not be used (#100) ---------------------------------
#
# devkitPro, Git for Windows' SDK and others put an MSYS2 `usr\bin` on PATH. Its cmake is a POSIX
# program: it turns `C:\Users\x` into `/home/x/...` and fails ("The source directory
# /home/user1/AppData/... does not exist"). The same goes for ninja, clang and git of those trees.

_MSYS_DIR_NAMES = ("msys", "msys2", "msys32", "msys64", "cygwin", "cygwin64", "cygwin32")
_MSYS_RUNTIME_DLLS = ("msys-2.0.dll", "cygwin1.dll")


def msys_reason(exe: str, windows=None, version_text=None) -> str | None:
    """Why `exe` is an MSYS2/Cygwin program (so the launcher must not use it), else None. Looks at the
    folders of its path, at the POSIX runtime DLL next to it and, when given, at its `--version` text."""
    if not (sys.platform == "win32" if windows is None else windows):
        return None
    parts = [x.lower() for x in re.split(r"[\\/]+", str(exe)) if x]
    for x in parts[:-1]:
        if x in _MSYS_DIR_NAMES or x.startswith("cygwin"):
            return f"inside the {x} tree"
    folder = os.path.dirname(str(exe))
    for dll in _MSYS_RUNTIME_DLLS:
        try:
            if folder and os.path.isfile(os.path.join(folder, dll)):
                return f"next to {dll}"
        except OSError:
            pass
    if version_text and re.search(r"\b(msys|cygwin)", version_text, re.I):
        return "its --version output says MSYS/Cygwin"
    return None


def find_tool(name: str, notes: list | None = None, path: str | None = None, windows=None,
              version_probe=None) -> str | None:
    """The first `name` on PATH that is a native program. On Windows, programs of MSYS2/Cygwin trees are
    skipped, each skip added to `notes`. `version_probe(exe)` may return the --version text."""
    notes = [] if notes is None else notes
    win = sys.platform == "win32" if windows is None else windows
    path = os.environ.get("PATH", "") if path is None else path
    seen = set()
    for d in path.split(os.pathsep):
        if not d or d.lower() in seen:
            continue
        seen.add(d.lower())
        exe = shutil.which(name, path=d)
        if not exe:
            continue
        why = msys_reason(exe, win)
        if not why and win and version_probe:
            why = msys_reason(exe, win, version_probe(exe))
        if why:
            notes.append(f"{name}: skipped {exe} ({why}; it does not understand Windows paths)")
            continue
        return exe
    return None


def native_path(path: str | None = None, windows=None) -> tuple:
    """(PATH without the folders of MSYS2/Cygwin trees, the folders removed). Unchanged off Windows."""
    path = os.environ.get("PATH", "") if path is None else path
    if not (sys.platform == "win32" if windows is None else windows):
        return path, []
    keep, drop = [], []
    for d in path.split(os.pathsep):
        (drop if d and msys_reason(os.path.join(d, "x.exe"), True) else keep).append(d)
    return os.pathsep.join(keep), drop


def vs_bundled_tool(name: str, installs=None) -> str | None:
    """cmake / ninja that Visual Studio ships (the CMake workload), else None. Windows only."""
    if sys.platform != "win32":
        return None
    rel = {"cmake": ("CMake", "bin", "cmake.exe"), "ninja": ("Ninja", "ninja.exe")}[name]
    try:
        installs = list_vs_installs() if installs is None else installs
    except Exception:
        return None
    for v in installs:
        exe = Path(v.path, "Common7", "IDE", "CommonExtensions", "Microsoft", "CMake", *rel)
        if exe.is_file():
            return str(exe)
    return None


def _probe_text(exe: str) -> str:
    try:
        return subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20,
                              env=cleanenv.clean_environment()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _plat():
    return "windows" if sys.platform == "win32" else "linux"


def toolchain_dir(root: Path) -> Path:
    return root / "toolchain"


def downloaded_paths(root: Path):
    """Where the downloaded tools unpack to (whether or not they are there)."""
    p = _plat()
    t = toolchain_dir(root)
    exe = ".exe" if p == "windows" else ""
    llvm = t / f"llvm-{pins.LLVM_VERSION}"
    cm = t / f"cmake-{pins.CMAKE_VERSION}"
    return {
        "llvm": llvm / "bin",
        "clang": llvm / "bin" / ("clang-cl" + exe if p == "windows" else "clang"),
        "cmake": cm / "bin" / ("cmake" + exe),
        "ninja": t / f"ninja-{pins.NINJA_VERSION}" / ("ninja" + exe),
    }


_CXX_CACHE = {}
_TOOLS_CACHE = {}
_TOOLS_LOCK = threading.Lock()


def cxx_works(cxx: str, env=None) -> str | None:
    """None when the compiler builds C++23 with the standard library; else why not. (Cached: it
    runs the compiler.)"""
    if sys.platform == "win32":
        return None
    if cxx not in _CXX_CACHE:
        _CXX_CACHE[cxx] = _cxx_works(cxx, env)
    return _CXX_CACHE[cxx]


def _cxx_works(cxx: str, env=None) -> str | None:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "t.cpp"
        src.write_text('#include <format>\n#include <string>\n#include <thread>\n'
                       'int main(){ return std::format("{}", 1).size() == 1 ? 0 : 1; }\n')
        r = subprocess.run([cxx, "-std=c++23", str(src), "-o", str(Path(d) / "t")],
                           capture_output=True, text=True, env=env or cleanenv.clean_environment(), timeout=120)
        if r.returncode != 0:
            return (r.stderr.strip().splitlines() or ["compile failed"])[-1]
    return None


def detect_tools(root: Path, prefer_downloaded=False, max_age=None) -> Tools:
    """Detection starts a --version program per candidate (hundreds of ms each on Windows), so the
    result is kept for the whole session (#124). `max_age` (seconds) limits that; max_age=0 looks
    again (a build does), as does forget_tools()."""
    import time
    key = (str(root), prefer_downloaded)
    with _TOOLS_LOCK:
        hit = _TOOLS_CACHE.get(key)
    if hit and (max_age is None or time.time() - hit[0] < max_age):
        return hit[1]
    tools = _detect_tools(root, prefer_downloaded)
    with _TOOLS_LOCK:
        _TOOLS_CACHE[key] = (time.time(), tools)
    return tools


def cached_tools(root: Path, prefer_downloaded=False):
    """The detected tools if detection already ran this session, else None (never starts a program)."""
    with _TOOLS_LOCK:
        hit = _TOOLS_CACHE.get((str(root), prefer_downloaded))
    return hit[1] if hit else None


def forget_tools():
    """Detect again at the next detect_tools (after tools were downloaded or installed)."""
    with _TOOLS_LOCK:
        _TOOLS_CACHE.clear()


FLATPAK_LLVM_CMD = "flatpak install flathub org.freedesktop.Sdk.Extension.llvm22//26.08"
FLATPAK_LLVM_HINT = ("The compiler extension of this Flatpak is not installed (a .flatpak bundle does not bring it). "
                     "Run this once, then restart the launcher: " + FLATPAK_LLVM_CMD +
                     " (about 0.5 GB, instead of the 2 GB LLVM download below).")
# SteamOS is read-only and ships no C++ headers, so the .tar.gz launcher points at the Flatpak (#135).
STEAMOS_FLATPAK_HINT = (
    "SteamOS has no C++ development headers, so this launcher can't build the game here. "
    "Use the Flatpak version of the launcher instead (it brings its own compiler): download "
    "ForzaHorizon1-ReImagined-x86_64.flatpak from the releases page and install it in Desktop Mode "
    "(Discover can open it). Your saves and settings can stay where they are.")
OS_RELEASE = "/etc/os-release"


def is_steamos(os_release=None) -> bool:
    """True when os-release names SteamOS (ID=steamos, or ID_LIKE contains steamos). `os_release`
    defaults to OS_RELEASE. A missing or unreadable file is False; this never raises."""
    try:
        text = Path(OS_RELEASE if os_release is None else os_release).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return False
    ident = like = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key.strip() == "ID":
            ident = value
        elif key.strip() == "ID_LIKE":
            like = value
    return ident == "steamos" or "steamos" in like.split()


def _flatpak_path():
    """Inside the Flatpak the compiler comes with the SDK's LLVM extension (not on PATH by itself)."""
    if paths.IN_FLATPAK:
        have = os.environ.get("PATH", "").split(os.pathsep)
        extra = [d for d in paths.flatpak_llvm_dirs() if d not in have]
        if extra:
            os.environ["PATH"] = os.pathsep.join(extra + have)


def _detect_tools(root: Path, prefer_downloaded=False) -> Tools:
    t = Tools()
    _flatpak_path()
    p = _plat()
    dp = downloaded_paths(root)
    exe = ".exe" if p == "windows" else ""

    # compiler
    sys_clang = None if prefer_downloaded else find_tool("clang-cl" if p == "windows" else "clang", t.notes,
                                                         version_probe=_probe_text)
    ver = _version_of([sys_clang, "--version"], r"clang version (\d+\.\d+)") if sys_clang else None
    if sys_clang and ver and ver[0] >= pins.MIN_SYSTEM_CLANG:
        t.cc = sys_clang
        t.cxx = (shutil.which("clang++") or sys_clang) if p != "windows" else sys_clang
        t.source["clang"] = f"system (clang {ver[0]}.{ver[1]})"
    elif dp["clang"].is_file():
        t.cc = str(dp["clang"])
        t.cxx = str(dp["llvm"] / ("clang-cl" + exe if p == "windows" else "clang++"))
        t.path_dirs.append(str(dp["llvm"]))
        t.source["clang"] = f"downloaded (LLVM {pins.LLVM_VERSION})"
    else:
        t.missing.append(f"llvm-{p}")
        if paths.IN_FLATPAK:
            t.hints.append(FLATPAK_LLVM_HINT)
    # cmake
    sys_cmake = None if prefer_downloaded else find_tool("cmake", t.notes, version_probe=_probe_text)
    cv = _version_of([sys_cmake, "--version"], r"version (\d+\.\d+)") if sys_cmake else None
    if sys_cmake and cv and cv >= (3, 25):
        t.cmake = sys_cmake
        t.source["cmake"] = f"system (cmake {cv[0]}.{cv[1]})"
    elif dp["cmake"].is_file():
        t.cmake = str(dp["cmake"])
        t.path_dirs.append(str(dp["cmake"].parent))
        t.source["cmake"] = f"downloaded (cmake {pins.CMAKE_VERSION})"
    elif (vs_cmake := vs_bundled_tool("cmake")) and (vv := _version_of([vs_cmake, "--version"], r"version (\d+\.\d+)")) and vv >= (3, 25):
        t.cmake = vs_cmake
        t.path_dirs.append(str(Path(vs_cmake).parent))
        t.source["cmake"] = f"Visual Studio's (cmake {vv[0]}.{vv[1]})"
    else:
        t.missing.append(f"cmake-{p}")
    # ninja
    # ninja 1.10+ (#123): older ones (e.g. some Visual Studio bundles) fail with "multiple outputs
    # aren't (yet?) supported by depslog" on the generated build.ninja.
    sys_ninja = None if prefer_downloaded else find_tool("ninja", t.notes, version_probe=_probe_text)
    nv = _version_of([sys_ninja, "--version"], r"(\d+\.\d+)") if sys_ninja else None
    if sys_ninja and nv and nv >= (1, 10):
        t.ninja = sys_ninja
        t.source["ninja"] = f"system (ninja {nv[0]}.{nv[1]})"
    elif dp["ninja"].is_file():
        t.ninja = str(dp["ninja"])
        t.path_dirs.append(str(dp["ninja"].parent))
        t.source["ninja"] = f"downloaded (ninja {pins.NINJA_VERSION})"
    elif (vs_ninja := vs_bundled_tool("ninja")) and (vn := _version_of([vs_ninja, "--version"], r"(\d+\.\d+)")) and vn >= (1, 10):
        t.ninja = vs_ninja
        t.path_dirs.append(str(Path(vs_ninja).parent))
        t.source["ninja"] = f"Visual Studio's (ninja {vn[0]}.{vn[1]})"
    else:
        t.missing.append(f"ninja-{p}")
    # the C++ standard library headers (Linux): only the system can supply them
    if t.cxx and p == "linux":
        env = cleanenv.clean_environment()
        env["PATH"] = os.pathsep.join(t.path_dirs + [env.get("PATH", "")])
        why = cxx_works(t.cxx, env)
        if why:
            if is_steamos() and not paths.IN_FLATPAK:
                t.problems.append(STEAMOS_FLATPAK_HINT)
            else:
                t.problems.append(
                    "The compiler cannot build a C++23 program (" + why + "). Install your system's "
                    "C++ development packages: g++ and libstdc++ headers (Debian/Ubuntu: build-essential; "
                    "Fedora: gcc-c++; Arch: gcc), then try again.")
    return t


def tool_env(tools: Tools):
    env = cleanenv.clean_environment()
    if sys.platform == "win32":
        env["PATH"] = native_path(env.get("PATH", ""))[0]   # no MSYS2/Cygwin programs ahead of the right ones (#100)
    if tools.path_dirs:
        env["PATH"] = os.pathsep.join(tools.path_dirs + [env.get("PATH", "")])
    return env


# ---- Windows: the MSVC C++ runtime and Windows SDK ----------------------------------------------
#
# clang-cl compiles with the MSVC standard library, so the library must be new enough for the
# project's C++23: <expected> (std::expected/std::unexpected in the SDK headers, MSVC STL of
# Visual Studio 2022 17.3 = toolset 14.33), std::byteswap and std::unreachable (17.1/17.2),
# <span>, <bit>, std::jthread (2019). Visual Studio 2017 (14.1x) has none of that and fails on the
# first file (issue #37: "struct char8_t;" in <experimental/filesystem>, '<bit>' not found).
MIN_VS = (17, 3)                # Visual Studio 2022 17.3 ...
MIN_TOOLSET = (14, 33)          # ... which ships MSVC 14.33
VS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"
STL_HEADERS = ("expected", "span", "bit")    # present in the installed toolset, whatever its version says


@dataclass
class VSInstall:
    path: str
    version: tuple          # (17, 14, ...) from installationVersion
    name: str = ""


def vswhere_path() -> Path:
    pf = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"


def parse_vswhere(text: str) -> list:
    """The Visual Studio installations in vswhere's `-format json` output, newest first."""
    import json
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for d in data if isinstance(data, list) else []:
        path = d.get("installationPath")
        if not path:
            continue
        ver = tuple(int(x) for x in re.findall(r"\d+", str(d.get("installationVersion", ""))))
        name = d.get("displayName") or d.get("productId") or "Visual Studio"
        shown = (d.get("catalog") or {}).get("productDisplayVersion") or d.get("installationVersion", "")
        out.append(VSInstall(path, ver, f"{name} {shown}".strip()))
    out.sort(key=lambda v: v.version, reverse=True)
    return out


def list_vs_installs(run=subprocess.run) -> list:
    """Every Visual Studio / Build Tools installation with the C++ tools, newest first."""
    vw = vswhere_path()
    if not vw.is_file():
        return []
    for extra in (["-utf8"], []):
        try:
            r = run([str(vw), "-products", "*", "-requires", VS_COMPONENT, "-format", "json"] + extra,
                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return []
        if r.returncode == 0:
            return parse_vswhere(r.stdout)
    return []


def vs_too_old(v: VSInstall) -> str | None:
    """Why the installation cannot be used (by version, from vswhere), else None."""
    if v.version[:2] < MIN_VS:
        return (f"too old: its C++ standard library (MSVC {_msvc_for_vs(v.version)}) lacks <expected> and other C++23 "
                f"parts the game's code needs; Visual Studio {MIN_VS[0]}.{MIN_VS[1]} (2022) or newer is required")
    return None


def _msvc_for_vs(version: tuple) -> str:
    return {15: "14.1x (Visual Studio 2017)", 16: "14.2x (Visual Studio 2019)"}.get(
        version[0] if version else 0, "older than 14.33")


def msvc_env_problem(env: dict) -> str | None:
    """Why an environment from vcvars64.bat cannot compile and link the game, else None. Checks what is
    really installed (the headers), not only a version: a Visual Studio 2022 may hold just an old toolset."""
    inc = [d for d in env.get("INCLUDE", "").split(os.pathsep) if d]
    if not inc or not env.get("LIB"):
        return "vcvars64.bat gave no INCLUDE/LIB (the C++ tools or the Windows SDK are not installed)"
    ver = env.get("VCToolsVersion", "")
    m = re.match(r"(\d+)\.(\d+)", ver)
    if m and (int(m.group(1)), int(m.group(2))) < MIN_TOOLSET:
        return (f"its C++ toolset is MSVC {ver}, older than {MIN_TOOLSET[0]}.{MIN_TOOLSET[1]} "
                "(install the latest MSVC v143 build tools in the Visual Studio Installer)")
    for h in STL_HEADERS:
        if not any(os.path.isfile(os.path.join(d, h)) for d in inc):
            return f"its C++ standard library has no <{h}> (MSVC {ver or 'unknown version'})"
    if not any(os.path.isfile(os.path.join(d, "windows.h")) for d in inc):
        return "no Windows SDK installed (windows.h not found)"
    return None


def load_vcvars(vs_path: str, run=subprocess.run) -> dict:
    """The environment after vcvars64.bat of the installation (empty when it fails)."""
    vcvars = Path(vs_path) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.is_file():
        return {}
    res = run(f'cmd /c ""{vcvars}" >nul && set"', capture_output=True, text=True, shell=True, timeout=120)
    env = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def find_msvc(log=lambda s: None, installs=None, load=load_vcvars):
    """(environment, path) of the newest installed Visual Studio / Build Tools whose C++ toolset can
    build the game, else (None, ""). Says in `log` which were found and why each other was skipped,
    so a player's log shows why the downloaded runtime is used instead."""
    installs = list_vs_installs() if installs is None else installs
    if not installs:
        log("MSVC: no Visual Studio with the C++ tools found")
        return None, ""
    log("MSVC: found " + "; ".join(f"{v.name} at {v.path}" for v in installs))
    for v in installs:
        why = vs_too_old(v)
        if why:
            log(f"MSVC: skipping {v.name} at {v.path}: {why}")
            continue
        env = load(v.path)
        why = msvc_env_problem(env)
        if why:
            log(f"MSVC: skipping {v.name} at {v.path}: {why}")
            continue
        log(f"MSVC: using {v.name} at {v.path} (toolset {env.get('VCToolsVersion', '?')})")
        return env, v.path
    log("MSVC: no installed Visual Studio is new enough; the Microsoft C++ runtime and Windows SDK are downloaded instead")
    return None, ""


def usable_vs_installed() -> bool:
    """Is there a Visual Studio whose toolset can build the game (the same test the build makes; runs
    vcvars64.bat, so it takes a second or two)?"""
    return find_msvc()[0] is not None


def _version_key(name: str):
    return tuple(int(p) if p.isdigit() else -1 for p in name.split("."))


def _newest_version_dir(parent: Path, need) -> Path | None:
    """The highest-versioned folder in `parent` that holds every relative path in `need`."""
    try:
        dirs = [d for d in parent.iterdir() if d.is_dir() and not d.is_symlink()]
    except OSError:
        return None
    dirs = [d for d in dirs if all((d / p).is_dir() for p in need)]
    return max(dirs, key=lambda d: _version_key(d.name)) if dirs else None


# Written by the launcher into the xwin folder once `xwin splat` exits cleanly (#739).
XWIN_DONE = "splat-done.txt"


def xwin_dirs(xdir: Path):
    """(INCLUDE dirs, LIB dirs) of a complete `xwin splat` in `xdir`, else None.

    Two layouts. The launcher splats in xwin's /winsysroot layout (VC/Tools/MSVC/<crt version>,
    Windows Kits/10/Include/<sdk version>) and marks it complete with XWIN_DONE. The plain layout
    (crt/, sdk/) is what launchers up to 0.2.8 made; it always links sdk/include/<sdk version> and
    sdk/lib/<sdk version> to their parent, even with --disable-symlinks, and Windows lets a normal
    user make that link only with Developer Mode on (os error 1314, #739). Such a splat is used only
    with both links: the one that failed on the first link leaves crt/ and most of sdk/, which looks
    complete but is not."""
    crt = _newest_version_dir(xdir / "VC" / "Tools" / "MSVC", ("include", "lib/x86_64"))
    sdk = _newest_version_dir(xdir / "Windows Kits" / "10" / "Include", ("ucrt", "um", "shared"))
    if crt and sdk and (xdir / XWIN_DONE).is_file():
        lib = xdir / "Windows Kits" / "10" / "Lib" / sdk.name
        if all((lib / p).is_dir() for p in ("um/x86_64", "ucrt/x86_64")):
            return ([crt / "include", sdk / "ucrt", sdk / "um", sdk / "shared", sdk / "winrt"],
                    [crt / "lib" / "x86_64", lib / "um" / "x86_64", lib / "ucrt" / "x86_64"])
    inc = xdir / "sdk" / "include"
    lib = xdir / "sdk" / "lib"

    def versioned_link(d):
        try:
            return any(re.fullmatch(r"10\.0\.\d+(\.\d+)?", e.name) for e in d.iterdir())
        except OSError:
            return False

    if (all(p.is_dir() for p in (xdir / "crt/include", xdir / "crt/lib/x86_64", inc / "ucrt", inc / "um",
                                 inc / "shared", lib / "um/x86_64", lib / "ucrt/x86_64"))
            and versioned_link(inc) and versioned_link(lib)):
        return ([xdir / "crt" / "include", inc / "ucrt", inc / "um", inc / "shared", inc / "winrt"],
                [xdir / "crt" / "lib" / "x86_64", lib / "um" / "x86_64", lib / "ucrt" / "x86_64"])
    return None
