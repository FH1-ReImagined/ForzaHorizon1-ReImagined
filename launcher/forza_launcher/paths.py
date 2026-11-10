"""Where things live: the launcher's config, the game's user data, the game files and binary."""
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
# Running inside the Flatpak sandbox (flatpak/): the runtime is org.freedesktop.Sdk, so the C++
# headers, cmake, ninja and python are in /usr, and the LLVM extension is mounted below.
FLATPAK_APP_ID = "io.github.dyotson.ForzaHorizon1ReImagined"   # flatpak/'s manifest


def in_flatpak(environ=None, info: Path = Path("/.flatpak-info")) -> bool:
    """Inside a Flatpak sandbox: flatpak sets FLATPAK_ID and puts /.flatpak-info in every sandbox."""
    environ = os.environ if environ is None else environ
    return bool(environ.get("FLATPAK_ID")) or info.exists()


FLATPAK_ID = os.environ.get("FLATPAK_ID", "")
IN_FLATPAK = in_flatpak()
FROZEN = bool(getattr(sys, "frozen", False))
PKG_DIR = Path(__file__).resolve().parent
# launcher/ in a source checkout; the PyInstaller bundle directory when frozen.
LAUNCHER_ROOT = Path(getattr(sys, "_MEIPASS", PKG_DIR.parent))
REPO_ROOT = PKG_DIR.parent.parent  # only meaningful in a source checkout


def long_path(p, windows: bool | None = None) -> Path:
    """`p` in Windows' extended-length form (\\\\?\\C:\\..., \\\\?\\UNC\\server\\share\\...), which file
    calls accept past the 260-character MAX_PATH (#586: DLC car packs nest deep enough to pass it).
    Elsewhere, and for a path already in that form, `p` unchanged."""
    windows = IS_WINDOWS if windows is None else windows
    s = str(p)
    if not windows or s.startswith("\\\\?\\"):
        return Path(p)
    s = os.path.abspath(s) if IS_WINDOWS else s
    if s.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + s[2:])
    return Path("\\\\?\\" + s)


def app_dir() -> Path:
    """The directory the launcher executable is in (the game's dist layout)."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return PKG_DIR.parent


def flatpak_llvm_dirs() -> list:
    """bin/ of the org.freedesktop.Sdk.Extension.llvm* extensions mounted in this sandbox (newest
    first): where the app's own extension point or the Sdk's puts them."""
    out = []
    for base in ("/app/lib/sdk", "/usr/lib/sdk"):
        try:
            names = sorted((n for n in os.listdir(base) if n.startswith("llvm")),
                           key=lambda n: int(n[4:]) if n[4:].isdigit() else 0, reverse=True)
        except OSError:
            continue
        out += [f"{base}/{n}/bin" for n in names if os.path.isfile(f"{base}/{n}/bin/clang")]
    return out


def in_checkout() -> bool:
    return not FROZEN and (REPO_ROOT / "tools" / "xcontent_extract.py").is_file()


def config_dir() -> Path:
    """Per-user launcher config (launcher.json)."""
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "ForzaReImagined"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "forza-reimagined"


def launcher_log() -> Path:
    """The launcher's own log (errors of the window and its tasks, with tracebacks)."""
    return config_dir() / "launcher.log"


def log_error(context: str, exc: BaseException | None = None) -> Path:
    """Append an error (and the traceback of `exc`, or of the exception being handled) to the
    launcher log; never raises. Returns the log's path."""
    import time
    import traceback
    path = launcher_log()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix(".log.old"))
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {context}\n")
            if exc is not None:
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            f.write("\n")
    except OSError:
        pass
    return path


def data_home() -> Path:
    """The game's user data root: saves, DLC, online identity, shader cache, forza.log, and the
    build. Linux: ~/.local/share/forza (as play.sh). Windows: %LOCALAPPDATA%\\ForzaReImagined, which
    OneDrive does not sync and Controlled folder access does not guard (issue #38; v0.1.x used
    Documents\\forza, which is kept while an install already lives there: datafolder.windows_default)."""
    if IS_WINDOWS:
        from . import datafolder
        return datafolder.windows_default()
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "forza"


def default_game_files() -> Path:
    """Where the game's files are extracted to."""
    return data_home() / "game"


def asset(name: str) -> Path:
    """A launcher image: bundled in the frozen app, else the checkout's artwork."""
    for base in (LAUNCHER_ROOT / "assets", REPO_ROOT / "docs" / "images",
                 REPO_ROOT / "forza-recomp" / "resources"):
        if (base / name).is_file():
            return base / name
    return LAUNCHER_ROOT / "assets" / name


def tools_dir() -> Path | None:
    """The directory holding xcontent_extract.py and install_dlc.py."""
    for d in (LAUNCHER_ROOT / "tools", app_dir(), REPO_ROOT / "tools"):
        if (d / "xcontent_extract.py").is_file() and (d / "install_dlc.py").is_file():
            return d
    return None


def ensure_tools_importable() -> None:
    d = tools_dir()
    if d is not None and str(d) not in sys.path:
        sys.path.insert(0, str(d))
