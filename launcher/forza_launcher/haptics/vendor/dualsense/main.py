"""DualSense HID writer - adaptive triggers only.

Writes L2/R2 trigger effect reports directly to the DualSense controller
over USB or Bluetooth. Motor rumble bytes are left untouched when
allow_steam_rumble is True, so Steam and the game keep control of body
vibration independently alongside the trigger effects.

HID output report format: https://github.com/nondebug/dualsense
"""

import logging
import struct
import sys
import threading
import time
import zlib

if sys.platform.startswith("linux"):
    from . import _hidraw as hid
else:
    import hid

from .triggers import off, rigid

log = logging.getLogger("hh.dualsense")

_SONY_VID = 0x054C
_PIDS = (0x0CE6, 0x0DF2)  # DualSense, DualSense Edge
_NAMES = {0x0CE6: "DualSense", 0x0DF2: "DualSense Edge"}

# HID output report layout (byte offsets) per transport
_USB = {"id": 0x02, "flags": 1, "vf1": 2, "motor_r": 3, "motor_l": 4, "psav": 10, "r": 11, "l": 22, "vf2": 39, "lb_setup": 42, "player_leds": 44, "lb_r": 45, "lb_g": 46, "lb_b": 47, "size": 64, "bt": False}
_BT  = {"id": 0x31, "flags": 2, "vf1": 3, "motor_r": 4, "motor_l": 5, "psav": 11, "r": 12, "l": 23, "vf2": 40, "lb_setup": 43, "player_leds": 45, "lb_r": 46, "lb_g": 47, "lb_b": 48, "size": 78, "bt": True}

# valid_flag0 bit masks
_FL_MOTORS   = 0x03  # right motor (bit 0) + left motor (bit 1)
_FL_TRIGGERS = 0x0C  # right trigger (bit 2) + left trigger (bit 3)


def _find_controller():
    """Return the gamepad HID interface dict or None."""
    devs = hid.enumerate(_SONY_VID, 0)
    pids = set(_PIDS)
    for d in devs:
        if d.get("product_id") in pids and d.get("usage_page") == 1 and d.get("usage") == 5:
            return d
    for d in devs:
        if d.get("product_id") in pids:
            return d
    return None


def _detect_bluetooth(info):
    """Infer transport from bus_type field or device path."""
    bus = info.get("bus_type")
    if bus in (2, 5):
        return True
    if bus in (1, 3):
        return False
    path = info.get("path", b"")
    if isinstance(path, str):
        path = path.encode()
    p = path.upper()
    return b"BTHENUM" in p or b"BLUETOOTH" in p


class DualSense:
    """Writes adaptive trigger effects to a DualSense over USB or Bluetooth.

    Starts without a controller and retries every `retry_interval` seconds.
    Drops writes silently while disconnected.

    Set allow_steam_rumble = True (default) so the motor bytes in each HID
    report are left unclaimed, letting Steam deliver body rumble alongside
    the trigger effects written here.
    """

    def __init__(self, retry_interval: float = 10.0):
        self._interval = retry_interval
        self._dev = None
        self._layout = _USB
        self._lock = threading.Lock()
        self._pending = (off(), off())
        self._pending_lb = (0, 0, 0)
        self._pending_motors = (0, 0)
        self._pending_player_leds = 0
        self._dirty = False
        self._running = False
        self._thread = None
        self._connected = False
        self._open_warned = False
        self.allow_steam_rumble = True
        self.last_error = None

    @property
    def connected(self):
        return self._connected

    def open(self):
        """Start the background I/O thread."""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hh-ds")
        self._thread.start()

    def close(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        self._release()

    def set(self, left, right, lb_r=0, lb_g=0, lb_b=0, motor_l=0, motor_r=0, player_leds=0):
        """Queue a new (L2, R2) effect pair, lightbar color, motor rumble, and player LEDs.

        An unchanged request writes nothing: the controller keeps its effect, and a report every
        frame (USB or Bluetooth) only re-applied it and crowded the link (#692)."""
        request = ((left, right), (lb_r, lb_g, lb_b), (motor_l, motor_r), player_leds)
        with self._lock:
            if request == (self._pending, self._pending_lb, self._pending_motors,
                           self._pending_player_leds):
                return  # still pending, or written; a reconnect writes it again
            self._pending = (left, right)
            self._pending_lb = (lb_r, lb_g, lb_b)
            self._pending_motors = (motor_l, motor_r)
            self._pending_player_leds = player_leds
            self._dirty = True

    def _connect(self):
        info = _find_controller()
        if not info:
            return False
        name = _NAMES.get(info.get("product_id"), "DualSense")
        dev = None
        try:
            dev = hid.device()
            dev.open_path(info["path"])
            dev.set_nonblocking(True)
        except (OSError, IOError) as exc:
            self.last_error = str(exc)
            if dev is not None:
                try:
                    dev.close()
                except OSError:
                    pass
            if not self._open_warned:
                self._open_warned = True
                if sys.platform.startswith("linux"):
                    log.error(
                        "%s: cannot open device (%s); check access to the Sony /dev/hidraw device",
                        name, exc,
                    )
                else:
                    log.warning(
                        "%s: cannot open device (%s)\n"
                        "  Close other controller remappers and replug.",
                        name, exc,
                    )
            return False

        self._dev = dev
        self._layout = _BT if _detect_bluetooth(info) else _USB
        self._connected = True
        self.last_error = None
        self._open_warned = False
        log.info("%s connected (%s)", name, "Bluetooth" if self._layout["bt"] else "USB")
        self._dirty = True
        return True

    def _release(self):
        if self._dev:
            try:
                self._dev.write(self._build(off(), off(), 0, 0, 0, 0, 0, 0))
            except Exception:
                pass
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
        if self._connected:
            log.warning("DualSense disconnected - retrying every %.0fs", self._interval)
        self._connected = False

    def _loop(self):
        last_try = -1e9
        waiting_logged = False
        while self._running:
            time.sleep(0.004)
            if not self._connected:
                now = time.monotonic()
                if now - last_try < self._interval:
                    time.sleep(0.1)
                    continue
                last_try = now
                try:
                    connected = self._connect()
                except Exception as exc:
                    self.last_error = str(exc)
                    log.warning("Controller discovery failed: %s", exc)
                    connected = False
                if connected:
                    waiting_logged = False
                    continue
                if not waiting_logged:
                    log.info("Waiting for DualSense - retrying every %.0fs", self._interval)
                    waiting_logged = True
                continue

            try:
                try:
                    self._dev.read(self._layout["size"])
                except OSError:
                    pass
                with self._lock:
                    if not self._dirty:
                        continue
                    left, right = self._pending
                    lb_r, lb_g, lb_b = self._pending_lb
                    motor_l, motor_r = self._pending_motors
                    player_leds = self._pending_player_leds
                    self._dirty = False
                self._dev.write(self._build(left, right, lb_r, lb_g, lb_b, motor_l, motor_r, player_leds))
            except Exception as exc:
                self.last_error = str(exc)
                log.warning("HID write failed (%s) - reconnecting", exc)
                self._release()

    def _build(self, left, right, lb_r, lb_g, lb_b, motor_l, motor_r, player_leds):
        """Assemble a HID output report for the given L2/R2 effect pair and lightbar/motors."""
        L = self._layout
        buf = bytearray(L["size"])
        buf[0] = L["id"]
        if L["bt"]:
            buf[1] = 0x02
        # Claim trigger bytes; only claim motor bytes when zeroing them out or controlling them
        flags = _FL_TRIGGERS
        if not self.allow_steam_rumble:
            flags |= 0x20  # HAPTICS_SELECT (enable audio haptics instead of legacy motors)
            buf[L["motor_r"]] = 0
            buf[L["motor_l"]] = 0
        buf[L["flags"]] = flags

        # FH1 owns neither the lightbar, player LEDs nor body rumble.
        for offset, (mode, params) in ((L["r"], right), (L["l"], left)):
            buf[offset] = mode
            for i, b in enumerate(params[:10]):
                buf[offset + 1 + i] = b & 0xFF
        if L["bt"]:
            struct.pack_into("<I", buf, 74, zlib.crc32(b"\xA2" + bytes(buf[:74])))
        return bytes(buf)
