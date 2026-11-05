"""ReGPU, the experimental Direct3D 12 renderer (Windows only): rexgpu-regpu.dll beside forza.exe,
built from forza-recomp/regpu-plugin and loaded by the game with gpu_plugin = "regpu" and
gpu_backend = "d3d12" (docs/rendering.md, "ReGPU"). In the launcher it is the Graphics API
setting's third value, "regpu"; forza.toml gets the two SDK keys instead.
"""
from pathlib import Path

CHOICE = "regpu"
PLUGIN = "rexgpu-regpu.dll"


def selected(settings: dict) -> bool:
    return settings.get("gpu_backend") == CHOICE


def toml_entries(backend: str) -> list:
    """The forza.toml (key, value) pairs for the Graphics API setting's value."""
    if backend == CHOICE:
        return [("gpu_backend", "d3d12"), ("gpu_plugin", "regpu")]
    return [("gpu_backend", backend)]


def backend_from_toml(flat: dict):
    """The Graphics API value an existing forza.toml means, or None to read gpu_backend as it is."""
    if flat.get("gpu_plugin") == "regpu":
        return CHOICE
    return None


def available(game_dir: Path) -> bool:
    return (Path(game_dir) / PLUGIN).is_file()


def fall_back(settings: dict, game_dir: Path) -> str:
    """ReGPU chosen but this build has no plugin (a build made before it, or one whose plugin did not
    compile): this start uses Vulkan. Returns the note to show, or "" when nothing changed."""
    if not selected(settings) or available(game_dir):
        return ""
    settings["gpu_backend"] = "vulkan"
    return ("ReGPU (experimental) is not in this build: playing with Vulkan. Build the game again "
            "(Setup > Build) to add it.")
