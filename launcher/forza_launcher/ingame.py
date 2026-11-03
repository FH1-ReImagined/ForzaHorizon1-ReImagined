"""Import graphics choices saved by the game's native pause menu."""
import json

from . import display, power, schema

FILENAME = "graphics-settings.json"
KEYS = {"output", "upscaler", "resolution_scale", "frame_rate", "vsync",
        "anisotropic_override", "forza_hud_edges", "forza_performance_overlay", "anti_aliasing",
        "world_shadow_filter", "world_ambient_occlusion", "forza_minimap_camera", *schema.CAMERA_FOV_KEYS}
NVIDIA = {"quality", "balanced", "dlss", "dlaa"}
FSR3 = {"fsr3_quality", "fsr3_balanced", "fsr3_performance", "fsr3_native"}


from .antialiasing import configure_runtime


def translate(document, current, pc=None):
    """Validate the complete request before changing any launcher setting."""
    if (not isinstance(document, dict) or
            type(document.get("version")) is not int or document["version"] != 1):
        raise ValueError("unsupported graphics settings version")
    values = document.get("settings")
    if not isinstance(values, dict) or not values or values.keys() - KEYS:
        raise ValueError("unknown graphics settings")
    changes = {}
    for key, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"invalid {key}")
        if key == "output":
            if value not in {"720p", "1080p", "1440p", "4k"}:
                raise ValueError("unsupported output resolution")
            if value == "720p":
                changes["resolution"] = value
                continue  # Reconstruction output selectors start at 1080p.
            changes.update(resolution=value, fsr3_output_resolution=value)
            if "fh1_output_resolution" in schema.BY_KEY:
                changes["fh1_output_resolution"] = value
        elif key == "upscaler":
            if value not in {"off"} | NVIDIA | FSR3:
                raise ValueError("unsupported upscaler")
            if value in NVIDIA and "fh1_upscaler" not in schema.BY_KEY:
                raise ValueError("NVIDIA reconstruction is unavailable on this platform")
            if "fh1_upscaler" in schema.BY_KEY:
                changes["fh1_upscaler"] = value if value in NVIDIA else "off"
            if value in FSR3:
                changes.update(present_effect="fsr3", fsr3_mode=value[5:])
            elif current.get("present_effect") == "fsr3":
                changes["present_effect"] = "bilinear"
        elif key == "anti_aliasing":
            if value not in {"game", "integrated", "smaa", "dlaa", "fsr3_native"}:
                raise ValueError("unsupported anti-aliasing")
            if value == "dlaa" and "fh1_upscaler" not in schema.BY_KEY:
                raise ValueError("NVIDIA anti-aliasing is unavailable on this platform")
            changes["forza_antialiasing"] = "game" if value == "integrated" else value
        elif key == "forza_performance_overlay":
            if value not in {"off", "fps", "graph"}:
                raise ValueError("unsupported performance overlay")
            changes[key] = value
        elif key == "world_shadow_filter":
            if value not in {"console", "soft", "softer"}:
                raise ValueError("unsupported shadow softness")
            changes[key] = value
        elif key == "world_ambient_occlusion":
            if value not in {"off", "low", "high"}:
                raise ValueError("unsupported ambient occlusion")
            changes[key] = value
        elif key in {"vsync", "forza_hud_edges", "forza_minimap_camera"}:
            if value not in ("true", "false"):
                raise ValueError(f"invalid {key}")
            changes[key] = value == "true"
        else:
            if not isinstance(value, str) or not value.lstrip("-").isdigit():
                raise ValueError(f"invalid {key}")
            number = int(value)
            valid = ((key == "frame_rate" and (number in schema.LOW_FRAME_RATES or 60 <= number <= 240)) or
                     (key == "resolution_scale" and 1 <= number <= 8) or
                     (key == "anisotropic_override" and -1 <= number <= 5) or
                     (key in schema.CAMERA_FOV_KEYS and (number == 0 or 40 <= number <= 90)))
            if not valid:
                raise ValueError(f"unsupported {key}")
            if key == "frame_rate" and current.get("frame_rate") == 0 and number == display.effective(
                    current, pc)["frame_rate"]:
                continue    # the menu saves the rate it was started with: Match display stays
            changes[key] = number
    if "anti_aliasing" in values:
        mode = values.get("upscaler")
        if mode is None:
            mode = current.get("fh1_upscaler", "off")
            if mode == "off" and current.get("present_effect") == "fsr3":
                mode = "fsr3_" + current.get("fsr3_mode", "quality")
        aa = values["anti_aliasing"]
        if (mode != "off" and aa != "integrated") or (mode == "off" and aa == "integrated"):
            raise ValueError("incompatible upscaling and anti-aliasing")
    elif "upscaler" in values:
        changes["forza_antialiasing"] = "game"
    # Accept old version-1 requests from builds with the combined selector.
    if "anti_aliasing" not in values and values.get("upscaler") == "dlaa":
        changes.update(fh1_upscaler="off", forza_antialiasing="dlaa")
    elif "anti_aliasing" not in values and values.get("upscaler") == "fsr3_native":
        changes.update(present_effect="bilinear", forza_antialiasing="fsr3_native")
    return changes


def import_pending(cfg, log=print):
    pending = cfg.data_home() / FILENAME
    try:
        if pending.stat().st_size > 16384:
            raise ValueError("graphics settings file is too large")
        changes = translate(json.loads(pending.read_text(encoding="utf-8")), cfg.settings,
                            cfg.pc_specs() if cfg.d["specs"].get("values") else None)
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError) as error:
        log(f"Could not load in-game graphics settings: {error}")
        return False
    changes = power.without_forced(changes, cfg.d["power"].get("applied") or {})
    previous = dict(cfg.settings)
    cfg.settings.update(changes)
    try:
        cfg.save()
    except OSError as error:
        cfg.settings.clear()
        cfg.settings.update(previous)
        log(f"Could not save in-game graphics settings: {error}")
        return False
    try:
        pending.unlink()
    except OSError as error:
        log(f"Graphics settings saved, but the pending file could not be removed: {error}")
    log("Applied the graphics settings saved in the game's pause menu.")
    return True
