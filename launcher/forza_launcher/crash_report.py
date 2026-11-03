"""Local, reviewable crash reports. No uploads, game files or environment dumps."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import platform
import re
from urllib.parse import urlencode

from . import GITHUB_URL, __version__, dlccheck, game, paths, specs
from .config import flatten, parse_toml

# Deliberately exclude profile IDs, paths, custom servers and arbitrary extra settings.
SETTING_KEYS = (
    "gpu", "gpu_backend", "gpu_vendor", "vulkan_device", "d3d12_adapter", "frame_rate", "vsync",
    "window_width", "window_height", "resolution_scale", "present_mode",
    "internal_display_resolution", "draw_resolution_scale_x", "draw_resolution_scale_y",
    "present_effect", "swap_post_effect", "native_2x_msaa", "scene_msaa", "scene_resolution",
    "anisotropic_override",
    "world_shadow_detail", "world_texture_detail", "world_shadow_filter", "world_ambient_occlusion",
    "texture_dump", "texture_replace",
    "fh1_upscaler", "fh1_output_resolution", "fsr3_mode", "fsr3_output_resolution",
    "fh1_upscale_quality", "fh1_aa", "resolution", "forza_ultrawide",
    "fullscreen", "audio", "apu", "audio_driver", "time_scalar", "ai_rubber_banding",
    "forza_car_tuning", "skip_startup_movies",
)
MAX_ISSUE_URL = 7500


def redact(text, private=()):
    """Best effort removal of known folders/identifiers; the player still reviews the text."""
    text = str(text)
    for value in sorted({str(v) for v in private if v and len(str(v)) > 3}, key=len, reverse=True):
        # Logs can use forward slashes even on Windows.
        pattern = "".join(r"[\\/]" if c in "\\/" else re.escape(c) for c in value)
        text = re.sub(pattern, "<private>", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s\"']+", "<home>", text)
    text = re.sub(r"/(?:home|Users)/[^/\s\"']+", "<home>", text)
    text = re.sub(r"(?i)\b(?:xuid|profile_xuid)\s*[=:]\s*(?:0x)?[0-9a-f]{16}\b", "XUID=<private>", text)
    text = re.sub(r"(?i)\b[0-9a-f]{16}\b", "<id>", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b", "<address>", text)
    text = re.sub(r"(?i)\b(?:https?|wss?|udp|tcp)://[^\s<>\"']+", "<url>", text)
    # Logs may contain JSON keys or quoted values with spaces/escaped quotes.
    # Remove the complete value, including an unquoted Authorization scheme.
    secret_key = (r"(?:authorization|(?:access[_-]?|refresh[_-]?|session[_-]?)?token|"
                  r"password|(?:client[_-]?)?secret|api[_-]?key)")
    secret_value = (r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|'''
                    r"(?:bearer\s+|basic\s+)?[^\s,;]+)")
    text = re.sub(r'''(?i)(?<![\w-])(["']?''' + secret_key + r'''["']?\s*[:=]\s*)'''
                  + secret_value, r"\1<private>", text)
    # Prevent log contents from escaping the Markdown code blocks.
    return text.replace("```", "''' ").replace("\x00", "")


def pc_summary(s):
    """CPU, RAM and video memory lines for the report's system section."""
    cpu = specs.short_cpu(s.cpu_name) or "unknown"
    if s.cpu_cores:
        cpu += f", {s.cpu_cores} cores" + (f" / {s.cpu_threads} threads" if s.cpu_threads else "")
    if s.avx2 is not None:
        cpu += ", AVX2" if s.avx2 else ", no AVX2"
    gb = lambda v: f"{v:.1f} GB".replace(".0 GB", " GB") if v else "unknown"
    return {"cpu": cpu, "ram": gb(s.ram_gb), "vram": gb(s.vram_gb)}


def launch_context(cfg, plan):
    """Freeze launch-time information; Settings can change while the game is running."""
    private = [Path.home(), cfg.data_home(), cfg.game_files(), cfg.path.parent, plan.cwd,
               cfg.online.get("custom_server"), cfg.profile.get("xenia_content"),
               cfg.profile.get("xenia_xuid")]
    try:
        settings = {**cfg.settings, **flatten(parse_toml(plan.toml_text))}
    except (ValueError, TypeError):
        settings = dict(cfg.settings)
    # Command-line flags override forza.toml. Never include arbitrary flags or their values.
    for i, arg in enumerate(plan.argv):
        key, sep, value = str(arg).lstrip("-").partition("=")
        if key in SETTING_KEYS:
            if not sep and i + 1 < len(plan.argv):
                value = plan.argv[i + 1]
            settings[key] = value
    selected = {k: settings[k] for k in SETTING_KEYS if k in settings}
    return {
        "private": tuple(str(p) for p in private if p),
        "pc": pc_summary(cfg.pc_specs()),
        "gpu": cfg.gpu.get("name") or cfg.gpu_vendor() or "Unknown (add GPU and driver)",
        "settings": selected,
        "online": cfg.online.get("mode", "unknown"),
        "profile": cfg.profile.get("mode", "own"),
        "packaging": "Flatpak" if paths.IN_FLATPAK else "Packaged launcher" if paths.FROZEN else "Source",
    }


def _message(line):
    return re.sub(r"^(?:\[[^\]\n]*\]\s*)+", "", line).strip()


@dataclass(frozen=True)
class CrashReport:
    title: str
    body: str
    log_path: Path | None


def collect(plan, code, context):
    """Run on a worker. Read at most 64 KiB of tail and 32 KiB of startup details."""
    private = context["private"]
    clean = lambda text: redact(text, private)
    log = game.log_path_of(plan)
    current = game.log_is_current(plan)
    try:
        tail = game.read_log_tail(log) if log is not None and current else []
        note = "" if tail else "The log is empty or no log path was configured."
    except OSError:
        tail, note = [], "The game log could not be read."
    startup = ""
    if not current:
        note = "No new log output was written. The previous run's log has been excluded."
    if log is not None and current:
        try:
            with log.open("rb") as f:
                startup = f.read(32768).decode("utf-8", errors="replace")
        except OSError:
            pass
    faults = [ln for ln in tail if any(level in ln for level in ("[critical]", "[fatal]"))]
    if not faults:
        faults = game.last_errors(tail)
    fault = clean(_message(faults[-1]))[:400] if faults else f"Unexpected exit (code {code})"
    chain = [ln for ln in tail if "Guest call chain" in ln or re.search(r"\s#\d+\s", ln)]
    device = [ln for ln in startup.splitlines() if any(marker in ln.lower() for marker in
              ("adapter in use", "vulkan device '", "device name:", "driver version:",
               "driver name:", "physical device:"))]
    # PNP IDs from cached detection add no useful information and can contain hardware identifiers.
    gpu_name = re.split(r"(?i)\s+(?:PCI|USB)\\", str(context["gpu"]))[0]
    system = " ".join((platform.system(), platform.release(), platform.version(), platform.machine()))
    try:
        version = game.game_version(plan.game)
    except OSError:
        version = "unknown build"
    settings = "\n".join(f"{k} = {v}" for k, v in context["settings"].items()) or "Not available"
    heap_failure = game.heap_failure_summary(tail)
    diagnosis = f"## Internal heap allocation failure\n{heap_failure}\n\n" if heap_failure else ""
    short_read = dlccheck.short_read_summary(tail)
    if short_read:
        diagnosis += f"## Damaged game file\n{short_read}\n\n"
    body = (
        "## What happened\nThe game closed unexpectedly.\n\n"
        "## Steps to reproduce\nDescribe what you were doing when it crashed.\n\n"
        "## System\n"
        f"- Launcher: {__version__} ({context['packaging']})\n"
        f"- Game: {clean(version)}\n- OS: {clean(system)}\n"
        f"- GPU (launcher detection): {clean(gpu_name)}\n"
        f"- CPU: {clean(context['pc']['cpu'])}\n- RAM: {clean(context['pc']['ram'])}\n"
        f"- VRAM: {clean(context['pc']['vram'])}\n"
        "- GPU driver: add version if not shown below\n"
        f"- Online: {clean(context['online'])}\n- Profile: {clean(context['profile'])}\n"
        f"- Exit code: {code} (0x{code & 0xFFFFFFFF:08X})\n"
        f"- Report time (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
        f"## Fault\n```text\n{fault}\n```\n\n"
        f"{diagnosis}"
        f"## Call chain\n```text\n{clean(chr(10).join(chain)) or 'Not recorded'}\n```\n\n"
        f"## Graphics device from log\n```text\n{clean(chr(10).join(device)) or 'Not recorded'}\n```\n\n"
        f"## Launch settings\n```text\n{clean(settings)}\n```\n\n"
        f"## Log tail (up to 200 lines / 64 KiB)\n```text\n{clean(chr(10).join(tail)) or note}\n```\n"
    )
    return CrashReport("Crash: " + fault[:110], body, log)


def issue_url(title, body):
    """A blank issue draft; nothing is submitted by opening it. Bound browser URL length."""
    url = GITHUB_URL + "/issues/new?" + urlencode({"title": title, "body": body, "labels": "bug"})
    return url if len(url) <= MAX_ISSUE_URL else None
