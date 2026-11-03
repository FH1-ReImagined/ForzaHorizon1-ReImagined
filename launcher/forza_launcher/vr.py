""""Play in VR" (docs/vr.md, #333): finding the OpenXR runtime and what a VR start needs besides the cvar.

The game opens the OpenXR loader only when vr_mode is not off. The loader finds the runtime from
the active_runtime.json the runtime installs (WiVRn, Monado and SteamVR write
~/.config/openxr/1/active_runtime.json). Inside the Flatpak, XDG_CONFIG_HOME is the app's own
folder, so the loader would not look there: the launcher names the host's manifest with
XR_RUNTIME_JSON (the sandbox can read it, flatpak/ manifest's finish-args).
"""
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path

from . import paths


WIVRN_URL = "https://github.com/WiVRn/WiVRn"
STEAMVR_URL = "https://store.steampowered.com/app/250820"
WINDOWS_KEY = r"SOFTWARE\Khronos\OpenXR\1"
MODES = {"cockpit": "Cockpit", "screen": "Big screen"}
# "VR picture" presets for the cockpit (docs/vr.md, "Version 3"): the draw resolution scale across and
# up. The cockpit view is 123 x 109 degrees drawn into the game's 16:9 picture, so its pixels per
# degree are 6.1 across and 4.5 up per unit of scale: 2 x 3 gives 12.1 x 13.4, 3 x 4 18.2 x 17.9.
PICTURE_SCALES = {"sharp": (2, 3), "sharpest": (3, 4)}
LOG_HINT = "lines starting with \"VR:\" in forza.log say what happened"


def mode(cfg) -> str:
    """The VR mode the player chose (an older config's "off" means the default, the cockpit)."""
    value = cfg.settings.get("vr_mode", "cockpit")
    return value if value in MODES else "cockpit"


@dataclass
class Runtime:
    name: str
    path: str


def active_runtime_manifest(home: Path, environ=None, in_flatpak=None):
    """The runtime manifest the OpenXR loader picks on the host, or None: XDG_CONFIG_HOME (the
    host's, ~/.config by default), then the XDG_CONFIG_DIRS and /etc."""
    environ = os.environ if environ is None else environ
    in_flatpak = paths.IN_FLATPAK if in_flatpak is None else in_flatpak
    # In a sandbox XDG_CONFIG_* name the app's folders, not the host's runtime install.
    config = environ.get("HOST_XDG_CONFIG_HOME") or (None if in_flatpak else environ.get("XDG_CONFIG_HOME"))
    config_home = Path(config) if config else home / ".config"
    search = environ.get("HOST_XDG_CONFIG_DIRS") or (None if in_flatpak else environ.get("XDG_CONFIG_DIRS"))
    dirs = [config_home] + [Path(d) for d in (search or "/etc/xdg").split(os.pathsep) if d]
    dirs.append(Path("/etc"))
    machine = platform.machine().lower()
    abi = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    for d in dirs:
        # OpenXR-SDK 1.1.62 prefers the architecture-specific name within each directory.
        for name in (f"active_runtime.{abi}.json", "active_runtime.json"):
            manifest = d / "openxr" / "1" / name
            if manifest.exists():
                return manifest
    return None


def _name_from_manifest(manifest: Path) -> str:
    """A friendly name for the runtime: the manifest's own name, else a known one in its path."""
    text = str(manifest)
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        text += " " + str(data.get("runtime", {}).get("name", "")) + " " + str(data.get("runtime", {}).get("library_path", ""))
        name = str(data.get("runtime", {}).get("name", "")).strip()
    except (OSError, ValueError, AttributeError):
        name = ""
    low = text.lower()
    for needle, nice in (("wivrn", "WiVRn"), ("steamvr", "SteamVR"), ("monado", "Monado"),
                         ("oculus", "Meta Quest Link"), ("virtualdesktop", "Virtual Desktop"),
                         ("virtual desktop", "Virtual Desktop")):
        if needle in low:
            return nice
    return name or "OpenXR runtime"


def read_windows_runtime() -> str | None:
    """HKLM\\SOFTWARE\\Khronos\\OpenXR\\1 ActiveRuntime (the manifest path), or None. Read-only."""
    try:
        import winreg
    except ImportError:
        return None
    for view in (winreg.KEY_WOW64_64KEY, 0):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WINDOWS_KEY, 0, winreg.KEY_READ | view) as key:
                value, _kind = winreg.QueryValueEx(key, "ActiveRuntime")
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def detect(home=None, environ=None, windows=None, registry=None):
    """The installed OpenXR runtime (Runtime) or None. Linux (incl. the Flatpak, which reads the
    host's manifest); Windows: the registry's ActiveRuntime. `registry` is a function returning that
    value, for tests."""
    windows = paths.IS_WINDOWS if windows is None else windows
    environ = os.environ if environ is None else environ
    try:
        # The loader's explicit override wins on both platforms, even if it is broken. Never
        # advertise a different registry/default runtime after an invalid explicit selection.
        if environ.get("XR_RUNTIME_JSON"):
            manifest = Path(environ["XR_RUNTIME_JSON"])
            if not manifest.is_file():
                return None
        elif windows:
            value = (registry or read_windows_runtime)()
            if not value:
                return None
            manifest = Path(value)
            if registry is None and not manifest.exists():
                return None
        else:
            manifest = active_runtime_manifest(Path(home or Path.home()), environ)
            if manifest is None:
                return None
        return Runtime(_name_from_manifest(manifest), str(manifest))
    except (OSError, ValueError):
        return None


# What a VR session sets over the flat settings (the launcher.json settings stay as they are).
SESSION_OVERRIDES = [
    ("Frame rate", "72 (the headset's rate)", "frame_rate"),
    ("Scene anti-aliasing (MSAA)", "Off", "scene_msaa"),
    ("Render scale", "2 (or the Sharpness you chose)", "resolution_scale"),
    ("Backend", "Vulkan (Windows)", "gpu_backend"),
]


def _taken(extra_args, key):
    return any(a == key or a.startswith(key + "=") for a in extra_args)


def configure(cfg, env: dict, args: list, notes: list, in_flatpak=None, home=None, environ=None, session=False):
    """Arguments, environment and a note for a "Play in VR" start. Play starts flat: nothing
    changes unless `session` is set. The VR values go as arguments, so the saved settings stay."""
    if not session:
        return
    chosen = mode(cfg)
    environ = os.environ if environ is None else environ
    in_flatpak = paths.IN_FLATPAK if in_flatpak is None else in_flatpak
    from .game import split_args
    extra = split_args(cfg.advanced.get("extra_args", ""))
    if paths.IS_WINDOWS:
        # The OpenXR path is Vulkan only (XR_KHR_vulkan_enable2).
        args[:] = [a for a in args if not a.startswith("--gpu_backend=")]
        args.append("--gpu_backend=vulkan")
    if in_flatpak and not env.get("XR_RUNTIME_JSON") and not environ.get("XR_RUNTIME_JSON"):
        manifest = active_runtime_manifest(Path(home or Path.home()), environ, in_flatpak=True)
        if manifest is not None:
            env["XR_RUNTIME_JSON"] = str(manifest)
    picture = cfg.settings.get("vr_picture", "game")
    own_scale = cfg.settings.get("present_effect") == "fsr3" or cfg.settings.get("fh1_upscaler", "off") != "off"
    wanted = {"frame_rate": "72", "scene_msaa": "off", "vr_mode": chosen, "vr_stereo": "true",
              "vr_trace": "false", "vr_seat_height_cm": f"{float(cfg.settings.get('vr_seat_height_cm', -4.0)):g}"}
    scale = []
    if own_scale:
        notes.append("VR: AMD FSR 3 and NVIDIA DLSS keep their own scale; the Sharpness setting is not applied.")
    elif chosen == "cockpit" and picture in PICTURE_SCALES:
        x, y = PICTURE_SCALES[picture]
        scale = [("draw_resolution_scale_x", str(x)), ("draw_resolution_scale_y", str(y))]
    else:
        scale = [("resolution_scale", "2")]
    drop = tuple(f"--{k}=" for k in ("resolution_scale", "draw_resolution_scale_x", "draw_resolution_scale_y",
                                     *wanted)) if not own_scale else tuple(f"--{k}=" for k in wanted)
    args[:] = [a for a in args if not a.startswith(drop)]
    for key, value in [*wanted.items(), *scale]:
        if not _taken(extra, f"--{key}"):   # the player's own extra flag wins: the game refuses one twice
            args.append(f"--{key}={value}")
    notes.append(f"VR headset: {MODES[chosen]}. Start your VR app and connect the headset "
                 f"before the game; without them it starts on the screen only ({LOG_HINT}).")
