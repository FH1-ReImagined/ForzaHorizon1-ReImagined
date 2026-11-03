"""Configure scene anti-aliasing independently of output and HUD resolution."""

def configure_runtime(cfg, env, args, write=True):
    """Keep native scene scale independent of the output/HUD, as the live menu does."""
    from . import dlss, fsr3, paths
    if write and paths.IS_WINDOWS and "FORZA_DLSS_RUNTIME" not in env and dlss.ready(cfg):
        env.setdefault("FORZA_DLSS_RUNTIME", str(dlss.runtime_dir(cfg)))
    values = cfg.settings
    aa = values.get("forza_antialiasing", "game")
    scaling = values.get("fh1_upscaler", "off") != "off" or values.get("present_effect") == "fsr3"
    if scaling or aa != "game":
        args.append("--swap_post_effect=none")
    if scaling and aa != "game":
        raise RuntimeError("Upscaling includes anti-aliasing. Turn it off before choosing a separate AA method.")
    if aa == "game":
        return  # Preserve the existing render path unless separate AA was chosen.
    if aa != "game" and (values.get("gpu_backend", "vulkan") != "vulkan" or values.get("forza_ultrawide")):
        raise RuntimeError("Separate scene anti-aliasing currently requires Vulkan and standard 16:9 output.")
    if aa == "dlaa" and not dlss.enabled(cfg):
        raise RuntimeError("DLAA requires an NVIDIA RTX GPU and its runtime.")
    if (values.get("gpu_backend", "vulkan") != "vulkan" or
            values.get("forza_ultrawide") or dlss.enabled(cfg) or
            fsr3.enabled(cfg)):
        return
    sizes = {"720p": (1280, 720, 1), "1080p": (1920, 1080, 2),
             "1440p": (2560, 1440, 2), "4k": (3840, 2160, 3)}
    size = sizes.get(values.get("resolution"))
    scene_scale = int(values.get("resolution_scale", 1))
    if size is None or scene_scale not in (1, 2, 3):
        return
    width, height, output_scale = size
    if aa == "smaa":
        args += ["--fh1_upscaler=smaa"]
        scene_scale = output_scale
    args += ["--fh1_scene_720p_native_hud=true", f"--fh1_native_scene_scale={scene_scale}",
             f"--resolution_scale={output_scale}", f"--fh1_output_width={width}",
             f"--fh1_output_height={height}"]
