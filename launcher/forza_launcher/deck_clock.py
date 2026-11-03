"""Read-only Deck GPU clock check (#508). No sysfs writes or subprocesses.

Inspired by Pinyon Shift's launcher check; see NOTICE.md. A sampled clock is
not a configured cap, and manual mode need not have been selected by Steam.
"""
from dataclasses import dataclass
from pathlib import Path
import re

from . import gpu


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


@dataclass(frozen=True)
class Clock:
    mode: str = ""
    observed_mhz: int | None = None

    def warning(self) -> str:
        if self.mode != "manual":
            return ""
        return ("The Deck GPU is using manual clock settings. If performance is low, check Manual GPU "
                "Clock in Steam's Performance menu. A low limit can reduce performance. "
                "The launcher does not change this setting.")


def check(sysfs: str = "/sys", proc: str = "/proc") -> Clock:
    if not gpu.steam_deck_reason(sysfs, proc):
        return Clock()
    for device in sorted(Path(sysfs).glob("class/drm/card[0-9]*/device")):
        # Only the Deck APU: an external AMD card can have independent settings.
        if _read(device / "vendor").lower() != "0x1002" or _read(device / "device").lower() not in (
                "0x163f", "0x1435"):
            continue
        mode = _read(device / "power_dpm_force_performance_level")
        current = re.findall(r"(?mi)^\s*\d+:\s*(\d+)\s*Mhz\s*\*\s*$",
                             _read(device / "pp_dpm_sclk"))
        return Clock(mode, int(current[0]) if len(current) == 1 else None)
    return Clock()
