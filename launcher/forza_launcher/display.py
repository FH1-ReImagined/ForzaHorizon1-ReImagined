"""The "Match display" frame rate (#215): the refresh rate of the monitor the game will use.

The setting is `frame_rate = 0`; the launcher turns it into a number whenever it writes forza.toml or
checks the PC's specs, so the game only ever sees a plain rate. Nothing here needs a running game.

Monitor numbering: the game's `monitor` setting is 0 or 1 for the primary display, 2 for the second, and
so on. SDL counts displays in its own order, Qt in the system's. We list the primary screen first and the
others in Qt's order, which agrees with SDL on Windows and on a typical Linux desktop; with three or more
monitors the order may differ, and then the label shows the rate of the monitor Qt calls that number.
"""
from . import specs

MIN_RATE, MAX_RATE = 60, 240
FALLBACK = 60


def screen_rates() -> list:
    """Refresh rates (Hz) of the screens, primary first; [] when Qt has no screens (no GUI)."""
    try:
        from PySide6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return []
        primary = QGuiApplication.primaryScreen()
        screens = [primary] + [s for s in QGuiApplication.screens() if s is not primary]
        return [float(s.refreshRate()) for s in screens if s is not None]
    except Exception:
        return []


def monitor_rate(monitor, rates=None):
    """The refresh rate (Hz, float) of the game's monitor number, or None when it cannot be read."""
    rates = screen_rates() if rates is None else rates
    try:
        index = max(int(monitor), 1) - 1     # 0 default and 1 primary are the same display
    except (TypeError, ValueError):
        index = 0
    if not rates:
        return None
    rate = rates[index] if index < len(rates) else rates[0]   # a monitor that is not connected: the game uses the primary
    return rate if rate and rate >= 1 else None


def clamp(rate) -> int:
    """A valid frame rate: 60 to 240 (the game's range), 40 for a 40 Hz screen (a Steam Deck's lowest), 30 for
    any other under 60."""
    rate = int(round(rate))
    if rate < MIN_RATE:
        return 40 if rate == 40 else 30
    return min(rate, MAX_RATE)


def safe_cap(pc):
    """The highest rate the specs warnings accept on this PC (None = no limit known)."""
    if specs.is_deck(pc):
        return specs.DECK_FPS
    caps = []
    if specs.cpu_tier(pc) in (specs.LOW, specs.MID):
        caps.append({specs.LOW: 30, specs.MID: 60}[specs.cpu_tier(pc)])
    if specs.gpu_tier(pc) in (specs.LOW, specs.MID):
        caps.append(60)
    return min(caps) if caps else None


def under_cap(hz, cap) -> int:
    """The rate for a screen of `hz` on a PC capped below 60: one the refresh rate divides, so every frame
    stays on screen equally long (a Steam Deck: 40 at 80 Hz, 30 at 60 or 90 Hz), else the cap."""
    for rate in (cap, 40, 30):
        if rate <= cap and abs(hz / rate - round(hz / rate)) < 0.02:
            return rate
    return cap


def resolve(monitor, pc=None, rates=None, log=None) -> int:
    """The integer rate "Match display" means now; 60 (logged) when the refresh rate is unknown."""
    hz = monitor_rate(monitor, rates)
    if hz is None:
        if log:
            log(f"Match display: could not read the refresh rate of monitor {monitor}; using {FALLBACK} fps.")
        rate = FALLBACK
    else:
        rate = clamp(hz)
    cap = safe_cap(pc) if pc is not None else None
    if cap and rate > cap:
        capped = under_cap(hz or FALLBACK, cap) if cap < MIN_RATE else cap
        if log:
            log(f"Match display: {rate} fps is more than this PC is likely to hold; using {capped} fps.")
        rate = capped
    return rate


def label(monitor, rates=None) -> str:
    hz = monitor_rate(monitor, rates)
    return f"Match display ({round(hz)} Hz)" if hz else "Match display"


def effective(settings, pc=None, rates=None, log=None) -> dict:
    """A copy of the settings with "Match display" (frame_rate 0) turned into a number."""
    out = dict(settings)
    if out.get("frame_rate") == 0:
        out["frame_rate"] = resolve(out.get("monitor", 0), pc, rates, log)
    return out
