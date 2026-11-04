#!/usr/bin/env python3
"""Validate diffuse-exp-v1 HDR readbacks. Older policy captures are rejected.

Requires NumPy; reads local files only. GPU sRGB sampling permits half an 8-bit
source step, then half-float output rounding and a small numerical tolerance.
"""
import argparse
import json
from pathlib import Path

import numpy as np

MODEL = "diffuse-exp-v1"


def validate_settings(settings):
    if settings.get("hdr_model") != MODEL:
        raise ValueError("Capture uses an older or unknown HDR policy; use its matching historical checker.")
    if not 0 < settings["width"] <= 16384 or not 0 < settings["height"] <= 16384:
        raise ValueError("Invalid capture dimensions.")
    names = ("paper_white", "peak", "scene_ceiling", "diffuse_anchor", "curve_exponent",
             "curve_denominator", "gamma")
    if not all(np.isfinite(settings[name]) for name in names):
        raise ValueError("Nonfinite HDR metadata.")
    if not (80 <= settings["paper_white"] <= settings["peak"] <= 10000
            and 1 <= settings["scene_ceiling"] <= settings["peak"] / settings["paper_white"] + 1e-5
            and 0 < settings["diffuse_anchor"] < 1
            and settings["curve_exponent"] >= 0 and settings["curve_denominator"] >= 0):
        raise ValueError("HDR metadata violates the output bounds.")


def calibration(settings):
    height, width = settings["height"], settings["width"]
    signal = np.full((height, width), -1.0)
    mode = settings["pattern"]
    if mode not in (1, 2, 3):
        return signal
    yy, xx = np.indices((height, width), dtype=float)
    cell = min(width / 5, height / 4)
    gx = (xx + .5 - width / 2) / cell + 2
    gy = (yy + .5 - height / 2) / cell + 1.5
    inside = (gx >= 0) & (gx < 4) & (gy >= 0) & (gy < 3)
    column, row = np.floor(gx).astype(int), np.floor(gy).astype(int)
    lx, ly = gx - column, gy - row
    inset = (np.abs(lx - .5) < .32) & (np.abs(ly - .5) < .32)
    index = row * 4 + column
    if mode == 1:
        nits = np.where(index == 0, 0, np.exp2(index - 11.0))
        checker = ((lx * 8).astype(int) + (ly * 8).astype(int)) % 2 == 0
        value = np.where(inset & checker, nits / 80, 0)
    elif mode == 2:
        level = np.select([column == 0, column == 1, column == 2], [.18, .5, .75], default=1)
        surround = np.select([row == 0, row == 1], [0, .18], default=1)
        value = settings["paper_white"] / 80 * np.where(inset, level, surround)
    else:
        value = settings["peak"] / 80 * np.where(inset, np.exp2((index - 11) / 6), 1)
    signal[inside] = value[inside]
    return signal


def reference(settings, code, coverage):
    linear = np.where(code <= .04045, code / 12.92, ((code + .055) / 1.055) ** 2.4)
    decoded = code ** settings["gamma"] if settings["gamma"] > 0 else linear
    intensity = decoded.max(axis=2)
    anchor, ceiling = settings["diffuse_anchor"], settings["scene_ceiling"]
    exponent, denominator = settings["curve_exponent"], settings["curve_denominator"]
    target = intensity.copy()
    if ceiling > 1 and exponent > .0001:
        selected = intensity > anchor
        u = (intensity[selected] - anchor) / (1 - anchor)
        target[selected] = np.clip(anchor + (ceiling - anchor) * np.expm1(exponent * u) / denominator,
                                   intensity[selected], ceiling)
    gain = np.divide(target, intensity, out=np.ones_like(target), where=intensity > 0)
    gain[coverage > 0] = 1
    result = decoded * gain[:, :, None] * (settings["paper_white"] / 80)
    if settings["compare"]:
        result[:, :settings["width"] // 2] = linear[:, :settings["width"] // 2] * (settings["paper_white"] / 80)
    pattern = calibration(settings)
    selected = pattern >= 0
    result[selected] = pattern[selected, None]
    return result, selected


def check(path, expect_scene=False):
    path = str(path)
    settings = json.loads(Path(path + ".json").read_text())
    validate_settings(settings)
    height, width = settings["height"], settings["width"]
    output = np.fromfile(path + ".rgba16f", dtype="<f2").reshape(height, width, 4).astype(float)
    stored = np.fromfile(path + ".bgra8", dtype=np.uint8).reshape(height, width, 4)
    code = stored[:, :, [2, 1, 0]].astype(float) / 255
    coverage = stored[:, :, 3].astype(float) / 255
    expected, pattern = reference(settings, code, coverage)
    lower = reference(settings, np.clip(code - .5 / 255, 0, 1), coverage)[0].astype(np.float16).astype(float)
    upper = reference(settings, np.clip(code + .5 / 255, 0, 1), coverage)[0].astype(np.float16).astype(float)
    hdr = ~pattern
    if settings["compare"]:
        hdr[:, :width // 2] = False
    hud, scene = hdr & (coverage > 0), hdr & (coverage == 0)
    nits = output[:, :, :3].max(axis=2) * 80
    outside = (output[:, :, :3] < lower - .002) | (output[:, :, :3] > upper + .002)
    errors = np.abs(output[:, :, :3] - expected) * 80
    hud_max = float(nits[hud].max()) if hud.any() else None
    expanded = int((scene & (nits > settings["paper_white"] + .5)).sum())
    failures = []
    if not np.isfinite(output).all():
        failures.append("non-finite output")
    if outside.any():
        failures.append("output differs from the independent curve or calibration grid")
    if hud_max is not None and hud_max > settings["paper_white"] + .5:
        failures.append("protected overlay exceeds paper white")
    limit = settings["paper_white"] * settings["scene_ceiling"]
    if settings["pattern"] == 3:
        limit = max(limit, settings["peak"])
    if nits.max() > limit + .5:
        failures.append("output exceeds configured luminance bound")
    if expect_scene and (not expanded or not hud.any()):
        failures.append("capture does not demonstrate both overlay protection and scene expansion")
    return {"width": width, "height": height, "settings": settings,
            "finite": bool(np.isfinite(output).all()), "peak_channel_nits": float(nits.max()),
            "hud_pixels": int(hud.sum()), "hud_max_channel_nits": hud_max,
            "scene_pixels": int(scene.sum()), "scene_pixels_above_paper_white": expanded,
            "mean_curve_error_nits": float(errors.mean()), "max_curve_error_nits": float(errors.max()),
            "channels_outside_quantization_bounds": int(outside.sum()), "failures": failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trigger", type=Path)
    parser.add_argument("--expect-scene", action="store_true")
    args = parser.parse_args()
    try:
        report = check(args.trigger, args.expect_scene)
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2))
    return bool(report["failures"])


if __name__ == "__main__":
    raise SystemExit(main())
