"""Battery saver (#140): the lightest settings while a Steam Deck runs on its battery.

Settings > Display > Frame rate > Battery saver: Off (the default), On, or Auto on battery
(Steam Deck). When it applies, the game starts at 30 fps (20 and 40, chosen for a screen 30
does not divide, stay) and render scale 1 with AMD FSR 1 and the scene in one pass without MSAA
(scene_msaa off): AMD FSR 3 becomes FSR 1, SMAA and the temporal anti-aliasing become the game's
own, and the render thread sleeps while it waits for the GPU (gpu_wait_blocking, already the
default on Linux). The player's own settings stay in launcher.json and come back at the next start on
mains power. The power source is read once, when the game starts, from
/sys/class/power_supply; a controller's or a mouse's battery (scope Device) does not count.
No Qt in here (test_power.py).
"""
import glob
import os
from dataclasses import dataclass, field

from . import gpu
from .paths import IS_WINDOWS

OFF, ON, AUTO = "off", "on", "auto"
MODES = [(OFF, "Off"), (ON, "On"), (AUTO, "Auto on battery (Steam Deck)")]
MAINS, BATTERY = "mains", "battery"
FRAME_RATE = 30
KEEP_FRAME_RATES = (20, 40)   # rates under 60 a player picks for a screen 30 does not divide
# Passed as a flag: the launcher's own switch for it is Windows only (Advanced > GPU).
FLAGS = ["--gpu_wait_blocking=true"]
SYSFS = "/sys"
PROC = "/proc"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def power_source(sysfs: str | None = None) -> str:
    """MAINS when a charger is online, BATTERY when the system's battery runs the machine,
    '' when there is no system battery (a desktop) or nothing says which."""
    sysfs = sysfs or SYSFS
    batteries, chargers = [], []
    for d in sorted(glob.glob(f"{sysfs}/class/power_supply/*")):
        kind = _read(f"{d}/type")
        if kind == "Battery":
            if _read(f"{d}/scope") == "Device" or _read(f"{d}/present") == "0":
                continue   # a controller's, a mouse's or an empty bay
            batteries.append(_read(f"{d}/status"))
        elif kind:
            if _read(f"{d}/scope") == "Device":
                continue
            chargers.append(_read(f"{d}/online"))
    if not batteries:
        return ""
    if "1" in chargers:
        return MAINS
    if "Discharging" in batteries:
        return BATTERY
    if chargers and all(c == "0" for c in chargers):
        return BATTERY
    return ""


def is_deck(sysfs: str | None = None, proc: str | None = None) -> bool:
    """The Steam Deck, as the GPU and specs detection find it (gpu.steam_deck_reason)."""
    return bool(gpu.steam_deck_reason(sysfs or SYSFS, proc or PROC))


def overrides(settings: dict) -> dict:
    """The launcher settings battery saver changes, from the player's (effective) settings. A frame rate
    of 20 or 40 stays: the player chose it for a screen that 30 does not divide (a Deck at 40 Hz)."""
    rate = settings.get("frame_rate")
    out = {"frame_rate": rate if rate in KEEP_FRAME_RATES else FRAME_RATE, "resolution_scale": 1,
           "scene_msaa": "off"}
    if settings.get("present_effect") == "fsr3":
        out["present_effect"] = "fsr"
    if settings.get("forza_antialiasing", "game") != "game":
        out["forza_antialiasing"] = "game"
    if settings.get("fh1_upscaler", "off") != "off":
        out["fh1_upscaler"] = "off"
    return {k: v for k, v in out.items() if settings.get(k) != v}


@dataclass
class Saver:
    active: bool = False
    reason: str = ""                               # why it applies or not, for the launcher log
    settings: dict = field(default_factory=dict)   # launcher settings to start the game with
    flags: list = field(default_factory=list)
    frame_rate: int = FRAME_RATE                   # the frame rate this start runs at

    def note(self) -> str:
        if not self.active:
            return ""
        return (f"Battery saver ({self.reason}): {self.frame_rate} fps, render scale 1, AMD FSR 1, scene "
                "anti-aliasing Off, sleeping GPU waits. Your own settings apply again on mains power.")


def decide(settings: dict, sysfs: str | None = None) -> Saver:
    """Whether battery saver applies to this start, and what it sets. `settings` are the
    player's effective settings (Match display already a number)."""
    mode = settings.get("battery_saver", OFF)
    if IS_WINDOWS or mode not in (ON, AUTO):
        return Saver()
    if mode == AUTO:
        if not is_deck(sysfs):
            return Saver(reason="not a Steam Deck")
        source = power_source(sysfs)
        if source != BATTERY:
            return Saver(reason="on mains power" if source == MAINS else "power source unknown")
        reason = "on battery"
    else:
        reason = "on"
    changes = overrides(settings)
    return Saver(True, reason, changes, list(FLAGS), changes.get("frame_rate", settings.get("frame_rate", FRAME_RATE)))


def setting_warning(mode: str, sysfs: str | None = None) -> str:
    """A line under the setting when Auto can never apply here."""
    if mode == AUTO and not IS_WINDOWS and not is_deck(sysfs):
        return "This PC is not a Steam Deck: Auto never applies here. Choose On to use it."
    return ""


def without_forced(changes: dict, applied: dict) -> dict:
    """The in-game graphics menu saves every value it shows; the ones battery saver forced at the
    last start are not the player's choice and are left out (frame rate 30 stays 60 on mains)."""
    return {k: v for k, v in changes.items() if not (k in applied and applied[k] == v)}
