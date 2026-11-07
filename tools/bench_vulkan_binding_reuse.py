#!/usr/bin/env python3
"""Compare Vulkan unchanged-binding modes in complete runtimes on copied saves (requires psutil).

Windows/Linux: one excluded warm-up per variant, then forward/reverse measurements.
Keep all compilation, capture tools and other game instances stopped during timing.
"""
import argparse
import csv
import ctypes
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "launcher"))
from forza_launcher.config import Config
from forza_launcher import game
import psutil


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def executable(runtime):
    return runtime / ("forza.exe" if sys.platform == "win32" else "forza")


def conflicts(own_pid=None):
    found = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        name = (proc.info["name"] or "").lower()
        if proc.pid != own_pid and name in {"forza.exe", "forza",
                                             "clang-cl.exe", "clang++.exe", "clang++", "cl.exe",
                                             "rexglue.exe", "rexglue", "ninja.exe", "ninja", "lld-link.exe"}:
            found.append(proc.info)
    return found


def cpu_snapshot(proc):
    total = proc.cpu_times()
    return {"process": total.user + total.system,
            "threads": {str(t.id): t.user_time + t.system_time for t in proc.threads()}}


def audit_affinity(proc, expected, locked):
    if not expected:
        return None
    observed = sorted(proc.cpu_affinity())
    if observed != sorted(expected):
        if locked:
            raise RuntimeError("CPU affinity changed after startup: " + str(observed))
        # SDK initialization restores the system mask on Windows. Reapply the
        # requested mask during startup, then reject changes before scoring.
        proc.cpu_affinity(expected)
        observed = sorted(proc.cpu_affinity())
        if observed != sorted(expected):
            raise RuntimeError("Could not apply CPU affinity: " + str(observed))
    return observed


def focused(pid):
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    owner = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(ctypes.c_void_p(user32.GetForegroundWindow()), ctypes.byref(owner))
    return owner.value == pid


def diagnostic_summary(label, audit, ai_result, require_foreground=False):
    if not ai_result or not ai_result.get("valid") or ai_result.get("reason") != "completed":
        raise RuntimeError("Correctness diagnostic requires a completed valid AI race")
    observed = sorted({tuple(row["cpu_affinity"]) for row in audit
                       if row.get("cpu_affinity") is not None})
    focus = [row["focused"] for row in audit if row.get("focused") is not None]
    focused_fraction = sum(focus) / len(focus) if focus else None
    if require_foreground and focused_fraction != 1:
        raise RuntimeError("Correctness diagnostic was not entirely in the foreground")
    # Current main has a relative frame CSV clock but no wall-clock epoch.
    # Correctness evidence needs neither a guessed clock offset nor FPS/CPU data.
    return {"label": label, "warmup": True, "run_kind": "correctness_diagnostic",
            "qualified_for_performance": False,
            "ai_race_completed": True, "ai_source": ai_result.get("source"),
            "audited_cpu_affinities": [list(mask) for mask in observed],
            "focused_fraction": focused_fraction}


def frame_stats(rows):
    values = sorted(float(row["ms"]) for row in rows)
    if not values:
        raise RuntimeError("No frame samples in the measurement window")
    tail = values[-max(1, math.ceil(len(values) / 100)):]
    return {"frames": len(values), "fps": 1000 / statistics.mean(values),
            "low1_fps": 1000 / statistics.mean(tail),
            "p99_ms": values[min(len(values) - 1, math.ceil(len(values) * .99) - 1)],
            "worst_ms": max(values), "over50ms": sum(v > 50 for v in values)}


def select_clock_samples(samples, epoch_text, begin, end):
    epoch = float(epoch_text)
    if not math.isfinite(epoch):
        raise RuntimeError("Frame-clock epoch must be finite")
    duration = end[2] - begin[2]
    if duration <= 0 or abs(end[0] - begin[0] - duration) > .1:
        raise RuntimeError("Wall clock changed during measurement")
    return [row for row in samples
            if begin[0] - epoch <= float(row["t"]) <= end[0] - epoch], duration


def run_one(args, label, runtime, warmup):
    if conflicts():
        raise RuntimeError("Conflicting game/build processes: " + json.dumps(conflicts()))
    if psutil.virtual_memory().available < 10 * 1024 ** 3:
        raise RuntimeError("Game runs require at least 10 GiB available memory")
    folder = args.output / label
    folder.mkdir(parents=True, exist_ok=False)
    # Only this disposable copy is passed to the runtime.
    shutil.copytree(args.seed / game.LOCAL_PROFILE, folder / game.LOCAL_PROFILE)
    cfg = Config(folder / "launcher.json")
    cfg.advanced.update(data_root=str(folder), game_dir=str(runtime), extra_env="",
                        extra_args="--readback_resolve=some")
    cfg.d["game_root"] = str(args.game_root)
    cfg.online["mode"] = "offline"
    cfg.profile["mode"] = "own"
    output_resolution = getattr(args, "output_resolution", "720p")
    cfg.settings.update(fh1_upscaler="off", fh1_output_resolution=output_resolution, resolution=output_resolution,
                        resolution_scale=getattr(args, "resolution_scale", 1), frame_rate=args.frame_rate, fullscreen=False,
                        gpu_backend="vulkan", present_effect="bilinear", store_shaders=True,
                        mnk_mode=True, vsync=False, forza_ultrawide=False)
    cfg.save()
    plan = game.build_plan(cfg, game.GameInstall(runtime, executable(runtime)), write=True)
    if (runtime / "forza.toml").is_file():
        shutil.copy2(runtime / "forza.toml", folder / "effective-forza.toml")
    plan.argv = [a for a in plan.argv if not a.startswith("--log_file=")]
    plan.argv += [f"--log_file={folder / 'game.log'}", f"--update_data_root={args.update_root}",
                  f"--cache_root={args.cache}"]
    present_mode = getattr(args, "present_mode", "default")
    if present_mode == "immediate":
        plan.argv.append("--vulkan_allow_present_mode_immediate=true")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("FORZA_", "RECOMP_PERF", "VK_", "REVIEW_"))}
    for key in plan.unset:
        env.pop(key, None)
    env.update(plan.env)
    env.update(FORZA_AUTOPRESS=args.schedule, RECOMP_PERF="1", RECOMP_PERF_SCENE=label,
               RECOMP_PERF_CSV=str(folder / "frames.csv"), RECOMP_PERF_EPOCH=str(folder / "epoch.txt"),
               FORZA_SCREENSHOT_TRIGGER=str(folder / "screenshot.trigger"),
               FORZA_FRAMEDUMP_DIR=str(folder))
    ai_race = getattr(args, "ai_race", False)
    ai_folder = folder / "ai"
    if ai_race:
        env.update(FORZA_BENCHMARK_AI="1", FORZA_BENCHMARK_OUTPUT=str(ai_folder))
    profile = warmup and getattr(args, "stack_sampler", None) is not None
    gpu_telemetry = warmup and getattr(args, "gpu_telemetry", False)
    gpu_wait = getattr(args, "gpu_wait", "default")
    env.pop("FORZA_GPU_WAIT", None)
    env.pop("FORZA_GPU_WAIT_LOG", None)
    if gpu_wait != "default":
        env["FORZA_GPU_WAIT"] = gpu_wait
    env["FORZA_GPU_WAIT_LOG"] = "0"
    if profile or getattr(args, "gpu_wait_log", False):
        env["FORZA_GPU_WAIT_LOG"] = "1"
    binding_reuse = getattr(args, "binding_reuse", False)
    binding_stats = warmup and getattr(args, "binding_stats", False)
    validate = warmup and binding_reuse and getattr(args, "validate_warmup", False)
    env["FORZA_VK_BINDING_REUSE"] = "1" if binding_reuse else "0"
    env["FORZA_VK_BINDING_STATS"] = "1" if binding_stats else "0"
    binding_verify = warmup and binding_reuse and getattr(args, "verify_warmup", True)
    env["FORZA_VK_BINDING_VERIFY"] = "1" if binding_verify else "0"
    env["FORZA_VK_DYNAMIC_CONSTANTS"] = "0"
    env["FORZA_VK_SAMPLER_CACHE"] = "0"
    if validate:
        plan.argv.append("--vulkan_validation_enabled=true")
        if getattr(args, "validation_layer_path", None):
            env["VK_LAYER_PATH"] = str(args.validation_layer_path)
    hashes = {p.name: digest(p) for p in runtime.iterdir()
              if p == executable(runtime) or p.suffix in {".exe", ".dll", ".so", ".dylib"}}
    manifest = {"label": label, "variant": label.rsplit("-", 1)[0], "warmup": warmup,
                "argv": plan.argv, "binary_sha256": hashes,
                "settle_s": args.settle, "measure_s": args.seconds, "settings": cfg.settings,
                "cpu_affinity": getattr(args, "cpu_affinity", None),
                "affinity_audited": bool(getattr(args, "cpu_affinity", None)),
                "schedule": args.schedule, "present_mode": present_mode,
                "require_foreground": getattr(args, "require_foreground", False),
                "ai_race": ai_race, "profiled_warmup": profile,
                "gpu_telemetry_warmup": gpu_telemetry,
                "gpu_wait": gpu_wait,
                "gpu_wait_log": env.get("FORZA_GPU_WAIT_LOG") == "1",
                "binding_reuse": binding_reuse, "binding_stats": binding_stats,
                "diagnostic_only": getattr(args, "diagnostic_only", False),
                "vulkan_validation": validate, "binding_verify": binding_verify}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    with (folder / "stdout.log").open("w") as log:
        child = subprocess.Popen(plan.argv, cwd=plan.cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        proc = psutil.Process(child.pid)
        started = time.monotonic()
        audit = []
        begin = end = None
        ai_ready = ai_complete = None
        measure_at = None if ai_race else args.settle
        auxiliaries = []
        auxiliary_logs = []
        (folder / "process.json").write_text(json.dumps({"pid": child.pid, "label": label}))
        print(f"Launched {label}: PID {child.pid}", flush=True)
        try:
            if getattr(args, "cpu_affinity", None):
                proc.cpu_affinity(args.cpu_affinity)
            while begin is None or time.monotonic() - started < measure_at + args.seconds:
                if child.poll() is not None:
                    raise RuntimeError(f"Game exited early: {child.returncode}")
                other = conflicts(child.pid)
                if other:
                    raise RuntimeError("Run contaminated by another game/build: " + json.dumps(other))
                elapsed = time.monotonic() - started
                affinity = audit_affinity(proc, getattr(args, "cpu_affinity", None),
                                          elapsed >= args.settle or begin is not None or ai_ready is not None)
                audit.append({"t": elapsed, "conflicts": other, "focused": focused(child.pid),
                              "cpu_affinity": affinity})
                if ai_race and ai_ready is None:
                    text = (folder / "game.log").read_text(errors="replace") if (folder / "game.log").exists() else ""
                    if "BENCHMARK: measuring 60 seconds" in text:
                        ai_ready = elapsed
                        measure_at = elapsed + args.settle
                        print(f"{label}: native AI is measuring; CPU window starts in {args.settle:g}s", flush=True)
                    elif elapsed > 240:
                        raise RuntimeError("AI benchmark did not reach its measured driving segment")
                    if (ai_folder / "result.json").exists():
                        raise RuntimeError("AI benchmark ended before the CPU window: " + (ai_folder / "result.json").read_text())
                if begin is None and measure_at is not None and elapsed >= measure_at:
                    begin = (time.time(), cpu_snapshot(proc), time.monotonic())
                    if profile:
                        handle = (folder / "sampler.log").open("w")
                        auxiliary_logs.append(handle)
                        auxiliaries.append(subprocess.Popen(
                            [str(args.stack_sampler), str(child.pid), str(args.profile_seconds),
                             str(folder / "cpu-stacks.csv")], stdout=handle, stderr=subprocess.STDOUT))
                    if gpu_telemetry:
                        handle = (folder / "gpu-telemetry.csv").open("w")
                        auxiliary_logs.append(handle)
                        auxiliaries.append(subprocess.Popen(
                            ["nvidia-smi", "--query-gpu=timestamp,utilization.gpu,utilization.memory,clocks.gr,power.draw",
                             "--format=csv,noheader,nounits", "-lms", "1000"], stdout=handle, stderr=subprocess.STDOUT))
                time.sleep(.5)
            end = (time.time(), cpu_snapshot(proc), time.monotonic())
            # Capture only after CPU/frame measurement has ended.
            if not ai_race:
                (folder / "screenshot.trigger").touch()
            # Let the 10-second CSV flush publish the entire measured window.
            # This post-window time is excluded from both CPU and frame results.
            deadline = time.monotonic() + (75 if ai_race else 12)
            flushed_at = time.monotonic() + 12
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("Game exited before publishing timing samples")
                other = conflicts(child.pid)
                if other:
                    raise RuntimeError("Run contaminated during flush: " + json.dumps(other))
                if ai_race:
                    elapsed = time.monotonic() - started
                    affinity = audit_affinity(proc, getattr(args, "cpu_affinity", None), True)
                    audit.append({"t": elapsed, "conflicts": other, "focused": focused(child.pid),
                                  "cpu_affinity": affinity})
                    report = ai_folder / "result.json"
                    if report.exists():
                        ai_complete = elapsed
                        ai_result = json.loads(report.read_text())
                        if not ai_result["valid"] or ai_result["reason"] != "completed":
                            raise RuntimeError("AI driver rejected the race: " + json.dumps(ai_result))
                        if time.monotonic() >= flushed_at:
                            break
                time.sleep(.5)
            if ai_race:
                if ai_complete is None:
                    raise RuntimeError("AI benchmark did not complete a valid driven segment")
                (folder / "screenshot.trigger").touch()
                # AI scoring and CPU timing have both ended before capture.
                shot_deadline = time.monotonic() + 5
                while not list(folder.glob("forza_shot*.ppm")) and time.monotonic() < shot_deadline:
                    time.sleep(.1)
        finally:
            for auxiliary in auxiliaries:
                if auxiliary.poll() is None:
                    auxiliary.terminate()
                auxiliary.wait(timeout=20)
            for handle in auxiliary_logs:
                handle.close()
            # Terminate only the child created by this run; its saves are disposable.
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=20)
            (folder / "process_audit.json").write_text(json.dumps(audit, indent=2))
            (folder / "cpu_samples.json").write_text(json.dumps({"begin": begin, "end": end}, indent=2))
    (folder / "cpu_samples.json").write_text(json.dumps({"begin": begin, "end": end}, indent=2))
    if getattr(args, "diagnostic_only", False):
        result = diagnostic_summary(label, audit, ai_result,
                                    getattr(args, "require_foreground", False))
        (folder / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)
        return result
    # Forced shutdown of the disposable child can leave the final stdio buffer
    # ending in a partial record. Keep complete records only, never a partial row.
    raw = (folder / "frames.csv").read_text()
    complete = raw[:raw.rfind("\n") + 1]
    samples = list(csv.DictReader(io.StringIO(complete)))
    epoch_path = folder / "epoch.txt"
    if not epoch_path.is_file():
        raise RuntimeError("Runtime omitted RECOMP_PERF_EPOCH; cannot align CPU and frame clocks. "
                           "Use --diagnostic-only for excluded correctness evidence.")
    selected, duration = select_clock_samples(samples, epoch_path.read_text(), begin, end)
    result = {"label": label, "variant": label.rsplit("-", 1)[0], "warmup": warmup,
              "audited_cpu_affinity": getattr(args, "cpu_affinity", None),
              "guest": frame_stats([r for r in selected if r["src"] == "guest"]),
              "present": frame_stats([r for r in selected if r["src"] == "present"])}
    result["cpu_cores"] = (end[1]["process"] - begin[1]["process"]) / duration
    result["thread_cpu_s"] = {tid: value - begin[1]["threads"].get(tid, value)
                              for tid, value in end[1]["threads"].items()}
    result["cpu_window_s"] = duration
    result["cpu_ms_per_guest_frame"] = result["cpu_cores"] * duration * 1000 / result["guest"]["frames"]
    focus = [a["focused"] for a in audit if measure_at <= a["t"] <= end[2] - started and a["focused"] is not None]
    result["focused_fraction"] = sum(focus) / len(focus) if focus else None
    result["focus_changed"] = len(set(focus)) > 1
    if result["focus_changed"]:
        raise RuntimeError("Run changed between foreground and background during measurement")
    if getattr(args, "require_foreground", False) and result["focused_fraction"] != 1:
        raise RuntimeError("Measurement was not entirely in the foreground")
    if ai_race:
        ai_focus = [a["focused"] for a in audit if ai_ready <= a["t"] <= ai_complete]
        result["ai_race"] = ai_result
        result["ai_focused_fraction"] = sum(ai_focus) / len(ai_focus)
        result["ai_measure_ready_s"] = ai_ready
        result["cpu_measure_ready_s"] = measure_at
        result["profiled_warmup"] = bool(profile)
        if getattr(args, "require_foreground", False) and result["ai_focused_fraction"] != 1:
            raise RuntimeError("AI scored interval was not entirely in the foreground")
        if profile and (auxiliaries[0].returncode != 0 or not (folder / "cpu-stacks.csv").is_file()):
            raise RuntimeError("Excluded warm-up did not produce a successful stack profile")
    if present_mode == "immediate":
        log_text = (folder / "game.log").read_text(errors="replace")
        if "present mode IMMEDIATE " not in log_text:
            raise RuntimeError("Vulkan presenter did not confirm IMMEDIATE mode")
        result["present_mode"] = "IMMEDIATE"
    for source in ("guest", "present"):
        sampled = [float(r["t"]) for r in selected if r["src"] == source]
        if max(sampled) - min(sampled) < .95 * duration:
            raise RuntimeError("Frame CSV does not cover the CPU measurement window")
    text = json.dumps(result, indent=2)
    (folder / "result.json").write_text(text)
    print(text, flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ("game-root", "update-root", "seed", "cache", "output"):
        p.add_argument("--" + flag, type=lambda s: Path(s).resolve(), required=True)
    for flag in ("baseline", "candidate"):
        p.add_argument("--" + flag, type=lambda s: Path(s).resolve())
    p.add_argument("--settle", type=float, default=60)
    p.add_argument("--seconds", type=float, default=45)
    p.add_argument("--frame-rate", type=int, default=120)
    p.add_argument("--cpu-affinity", type=lambda s: [int(x) for x in s.split(",")],
                   help="Optional logical-processor list for a CPU-constrained comparison")
    p.add_argument("--resume", action="store_true", help="Keep validated results and retry incomplete runs")
    p.add_argument("--warmup", choices=["all", "first", "none"], default="all",
                   help="Excluded warm-ups; first shares a warmed shader cache across variants")
    p.add_argument("--present-mode", choices=["default", "immediate"], default="default")
    p.add_argument("--require-foreground", action="store_true")
    p.add_argument("--ai-race", action="store_true", help="Require existing native-AI benchmark hooks; settle starts at its measured phase")
    p.add_argument("--diagnostic-only", action="store_true",
                   help="Run excluded AI warm-ups only; report audits without performance metrics or a frame-clock epoch")
    p.add_argument("--rounds", type=int, default=2, help="Runs per variant, alternating variant order each round")
    p.add_argument("--stack-sampler", type=lambda s: Path(s).resolve(), help="Windows stack sampler, run only in excluded warm-ups")
    p.add_argument("--profile-seconds", type=float, default=20)
    p.add_argument("--gpu-telemetry", action="store_true", help="NVIDIA GPU telemetry in excluded warm-ups only; requires nvidia-smi")
    p.add_argument("--gpu-wait", choices=["default", "spin", "yield", "backoff"], default="default")
    p.add_argument("--baseline-gpu-wait", choices=["default", "spin", "yield", "backoff"])
    p.add_argument("--candidate-gpu-wait", choices=["default", "spin", "yield", "backoff"])
    p.add_argument("--gpu-wait-log", action="store_true", help="Log fence wait counts every ten seconds")
    p.add_argument("--resolution-scale", type=int, default=1, choices=[1, 2, 3])
    p.add_argument("--output-resolution", default="720p", choices=["720p", "1080p", "1440p", "2160p"])
    p.add_argument("--schedule", default="8:A:0.08,18:START:0.08,28:A:0.08")
    p.add_argument("--baseline-cache", choices=["off", "on"], default="off")
    p.add_argument("--candidate-cache", choices=["off", "on"], default="on")
    p.add_argument("--binding-stats", action="store_true", help="Counters in excluded warm-ups only")
    p.add_argument("--validate-warmup", action="store_true", help="Validate dynamic-mode excluded warm-up")
    p.add_argument("--verify-warmup", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--validation-layer-path", type=Path)
    args = p.parse_args()
    if args.require_foreground and sys.platform != "win32":
        p.error("Foreground verification currently requires Windows")
    if args.rounds < 1:
        p.error("Use at least one round")
    if args.diagnostic_only and (not args.ai_race or args.warmup == "none"):
        p.error("--diagnostic-only requires --ai-race and an excluded warm-up")
    if args.ai_race and args.settle + args.seconds > 55:
        p.error("Keep the CPU window inside the AI benchmark's 60-second interval")
    if args.stack_sampler and (sys.platform != "win32" or not args.ai_race or not args.stack_sampler.is_file()):
        p.error("A Windows AI race and an existing sampler executable are required")
    if args.stack_sampler and not 0 < args.profile_seconds <= args.seconds:
        p.error("Keep stack sampling within the excluded warm-up's CPU window")
    if args.gpu_telemetry and not shutil.which("nvidia-smi"):
        p.error("NVIDIA telemetry requires nvidia-smi on PATH")
    if not args.baseline or not args.candidate:
        p.error("Specify --baseline and --candidate runtimes")
    variants = [("baseline", args.baseline), ("candidate", args.candidate)]
    if any(not executable(runtime).is_file() for _, runtime in variants):
        p.error("Each runtime must contain the game executable")
    if args.seconds < 10 or args.settle < 10:
        p.error("Use at least 10 seconds of settling and measurement")
    if not (args.seed / game.LOCAL_PROFILE).is_dir():
        p.error("Seed must contain the runtime profile folder " + game.LOCAL_PROFILE)
    # Prevent a run directory from writing into the immutable seed or vice versa.
    if args.output == args.seed or args.seed in args.output.parents or args.output in args.seed.parents:
        p.error("Output and seed directories must be separate")
    args.output.mkdir(parents=True, exist_ok=args.resume)
    results = []
    warmups = variants if args.warmup == "all" else variants[:1] if args.warmup == "first" else []
    sequence = [(name + "-warmup", runtime, True) for name, runtime in warmups]
    for number in range(1, (0 if args.diagnostic_only else args.rounds) + 1):
        order = variants if number % 2 else reversed(variants)
        sequence += [(f"{name}-{number}", runtime, False) for name, runtime in order]
    default_gpu_wait = args.gpu_wait
    for label, runtime, warmup in sequence:
        variant = label.rsplit("-", 1)[0]
        args.binding_reuse = (args.candidate_cache if variant == "candidate" else args.baseline_cache) == "on"
        override = {"baseline": args.baseline_gpu_wait, "candidate": args.candidate_gpu_wait}.get(variant)
        args.gpu_wait = override if override is not None else default_gpu_wait
        folder = args.output / label
        if folder.exists() and args.resume:
            manifest = json.loads((folder / "manifest.json").read_text())
            hashes = {q.name: digest(q) for q in runtime.iterdir()
                      if q == executable(runtime) or q.suffix in {".exe", ".dll", ".so", ".dylib"}}
            if (manifest["binary_sha256"] != hashes or manifest["settle_s"] != args.settle or
                manifest["measure_s"] != args.seconds or manifest["settings"]["frame_rate"] != args.frame_rate or
                manifest["settings"]["resolution_scale"] != args.resolution_scale or
                manifest["settings"]["fh1_output_resolution"] != args.output_resolution or
                manifest.get("cpu_affinity") != args.cpu_affinity or
                (args.cpu_affinity and not manifest.get("affinity_audited", False)) or
                manifest.get("schedule", "8:A:0.08,18:START:0.08,28:A:0.08") != args.schedule or
                manifest.get("present_mode", "default") != args.present_mode or
                manifest.get("require_foreground", False) != args.require_foreground or
                manifest.get("ai_race", False) != args.ai_race or
                manifest.get("gpu_wait", "default") != args.gpu_wait or
                manifest.get("binding_reuse", False) != args.binding_reuse or
                manifest.get("diagnostic_only", False) != args.diagnostic_only or
                manifest.get("binding_stats", False) != (warmup and args.binding_stats) or
                manifest.get("binding_verify", False) != (warmup and args.binding_reuse and args.verify_warmup) or
                manifest.get("vulkan_validation", False) !=
                    (warmup and args.binding_reuse and args.validate_warmup) or
                manifest.get("gpu_wait_log", manifest.get("profiled_warmup", False)) !=
                    (args.gpu_wait_log or (warmup and args.stack_sampler is not None))):
                raise RuntimeError("Cannot resume with changed binaries or measurement settings: " + label)
            result = folder / "result.json"
            if result.exists():
                data = json.loads(result.read_text())
                if data["label"] != label or data["warmup"] != warmup:
                    raise RuntimeError("Saved run identity differs: " + label)
                results.append(data)
                print("Retained completed run:", label, flush=True)
                continue
            # Keep the rejected attempt for inspection; only rename within the
            # explicitly selected output directory, with no recursive deletion.
            archive = args.output / (label + "-rejected-" + str(time.time_ns()))
            if folder.resolve().parent != args.output.resolve() or archive.resolve().parent != args.output.resolve():
                raise RuntimeError("Rejected-run archive leaves output directory")
            folder.rename(archive)
        results.append(run_one(args, label, runtime, warmup))
    (args.output / "results.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
