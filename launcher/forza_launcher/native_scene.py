"""Optional exact scene size, gated by the renderer that implements it."""
import json
import re
from pathlib import Path

from . import dlss, paths, schema

WIDTH = "fh1_native_scene_width"
HEIGHT = "fh1_native_scene_height"
CAPABILITY = "fh1-native-scene.json"


def pair(settings):
    values = []
    for key, limit in ((WIDTH, schema.NATIVE_SCENE_MAX_WIDTH), (HEIGHT, schema.NATIVE_SCENE_MAX_HEIGHT)):
        value = settings.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= limit:
            raise ValueError("Scene dimensions must be whole numbers within 8960 x 5040.")
        values.append(value)
    if bool(values[0]) != bool(values[1]):
        raise ValueError("Set both scene width and height, or set both to 0 for the default.")
    return tuple(values)


def available(cfg, install):
    if cfg.settings.get("gpu_backend", "vulkan") != "vulkan" or install is None:
        return False
    # An advanced backend/plugin override makes the renderer selection ambiguous here.
    from . import game
    try:
        extra = game.split_args(cfg.advanced.get("extra_args", ""))
    except ValueError:
        return False
    if any(a.split("=", 1)[0] in ("--gpu_backend", "--gpu_plugin") for a in extra):
        return False
    from .config import flatten, parse_toml
    try:
        overrides = flatten(parse_toml(cfg.advanced.get("extras_toml", "") or ""))
    except ValueError:
        return False
    if "gpu_backend" in overrides or "gpu_plugin" in overrides:
        return False
    renderer = "rexgpu-xenos.dll" if paths.IS_WINDOWS else "librexgpu-xenos.so"
    # The SDK's GPU plugin loader opens a path next to the executable, not a
    # library found through LD_LIBRARY_PATH. Linux /proc/self/exe resolves links.
    executable = Path(install.exe)
    folder = executable.parent if paths.IS_WINDOWS else executable.resolve().parent
    try:
        data = json.loads((folder / CAPABILITY).read_text(encoding="utf-8"))
        return (isinstance(data, dict) and type(data.get("api")) is int and data["api"] == 1 and
                data.get("renderer_sha256") == dlss._sha256(folder / renderer))
    except (OSError, ValueError, TypeError):
        return False


def configure(cfg, install, args, notes, extra, *, vr=False, battery_saver=False):
    try:
        width, height = pair(cfg.settings)
    except ValueError as error:
        if available(cfg, install):
            raise RuntimeError(str(error)) from error
        notes.append(f"Custom scene size ignored: {error}")
        return
    if not width:
        return
    named = {a[2:].split("=", 1)[0] for a in extra if a.startswith("--")}
    if WIDTH in named or HEIGHT in named:
        notes.append("Custom scene size is controlled by the extra command-line arguments.")
        return
    if not available(cfg, install):
        notes.append("Custom scene size is unavailable for this renderer; the default scene size is used.")
        return
    values = cfg.settings
    if (vr or battery_saver or str(values.get("scene_resolution", "720")) != "720" or
            values.get("fh1_upscaler", "off") != "off" or values.get("present_effect") == "fsr3" or
            values.get("forza_antialiasing", "game") != "game" or values.get("forza_ultrawide")):
        notes.append("Custom scene size is unavailable with the selected scene, upscaling, AA, VR or battery mode.")
        return
    # The split renderer's HUD-size defaults are not the launcher's output choice.
    # Explicit output overrides are left to the player rather than guessed here.
    if named & {"resolution", "fh1_output_width", "fh1_output_height", "fh1_upscaler",
                "present_effect", "forza_ultrawide", "scene_resolution", "render_target_path_vulkan"}:
        notes.append("Custom scene size is unavailable with explicit output-size arguments.")
        return
    from .config import flatten, parse_toml
    try:
        overrides = flatten(parse_toml(cfg.advanced.get("extras_toml", "") or ""))
    except ValueError:
        overrides = {}
    if set(overrides) & {WIDTH, HEIGHT, "resolution", "fh1_output_width", "fh1_output_height",
                         "fh1_upscaler", "present_effect", "forza_ultrawide", "scene_resolution",
                         "render_target_path_vulkan"}:
        notes.append("Custom scene size is unavailable with explicit output-size overrides.")
        return
    # Match the SDK's preset normalization and aliases (video_mode_util.h).
    resolution = re.sub(r"[\s_-]", "", str(values.get("resolution", ""))).lower()
    sizes = {"": (1280, 720), "480p": (640, 480), "540p": (960, 540), "720p": (1280, 720),
             "900p": (1600, 900), "1080p": (1920, 1080), "1440p": (2560, 1440),
             "1800p": (3200, 1800), "2160p": (3840, 2160), "4k": (3840, 2160)}
    output = sizes.get(resolution)
    if output is None:
        match = re.fullmatch(r"([0-9]+)x([0-9]+)", resolution)
        if not match:
            raise RuntimeError("Choose a valid output resolution before using a custom scene size.")
        output = tuple(map(int, match.groups()))
        if not (0 < output[0] <= schema.NATIVE_SCENE_MAX_WIDTH and
                0 < output[1] <= schema.NATIVE_SCENE_MAX_HEIGHT):
            raise RuntimeError("The selected output resolution is outside the supported launcher range.")
    args += [f"--{WIDTH}={width}", f"--{HEIGHT}={height}"]
    args += [f"--fh1_output_width={output[0]}", f"--fh1_output_height={output[1]}"]
    notes.append(f"Custom 3D scene: {width} x {height}; the renderer checks the device limit.")
