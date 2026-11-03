"""Per-game loopback receiver. Socket and HID I/O never run on the Qt thread."""
import json
import secrets
import socket
import threading
import time
from dataclasses import replace

from .settings import normalize
from .telemetry import Decoder
from .vendor.dualsense.triggers import feedback, off
from .xbox import XboxOutput

STALE_SECONDS = .35


def supported(directory):
    try:
        metadata = json.loads((directory / 'forza-telemetry.json').read_text(encoding='utf-8'))
        return metadata.get('protocol') == 'FH1T' and metadata.get('version') == 1
    except (OSError, ValueError, AttributeError):
        return False


def supports_xbox(directory):
    try:
        metadata = json.loads((directory / 'forza-telemetry.json').read_text(encoding='utf-8'))
        return supported(directory) and metadata.get('xbox_trigger_rumble') == 1
    except (OSError, ValueError, AttributeError):
        return False


PRESS_ON, PRESS_OFF, PRESS_BAND = 12, 4, 8  # raw 0-255: engage above ON, release below OFF


class Pedal:
    """Hysteresis for one trigger (#224). The raw value jitters by a few counts, so a trigger held
    half way crossed the rounding step of the force again and again and the effect flickered. The
    effect engages above PRESS_ON, releases only below PRESS_OFF, and while engaged follows the
    trigger only after it moved PRESS_BAND from where the current force was set."""
    def __init__(self):
        self.held = None

    def update(self, value):
        if self.held is None:
            if value >= PRESS_ON:
                self.held = value
        elif value < PRESS_OFF:
            self.held = None
        elif abs(value-self.held) >= PRESS_BAND:
            self.held = value
        return self.held or 0

    def reset(self):
        self.held = None


def effects(frame, settings, pedals=None):
    """Basic resistance from raw LT/RT, valid for the default driving layout.

    Deliberately does not infer ABS, traction, gears or RPM from missing signals. `pedals` is the
    (brake, throttle) Pedal pair that keeps the effect steady across frames. Without a frame or
    outside driving (`frame.active` is false in menus) both triggers are released.
    """
    pedals = pedals or (Pedal(), Pedal())
    if frame is None or not frame.active or not settings['enabled']:
        for pedal in pedals:
            pedal.reset()
        return off(), off()
    def pedal(state, value, strength):
        force = round(strength * settings['intensity']/100 * state.update(value)/255)
        return feedback([0] + [force]*9) if force else off()
    return pedal(pedals[0], frame.brake, settings['brake_strength']), pedal(pedals[1], frame.throttle, settings['throttle_strength'])


class HapticsService:
    def __init__(self, settings, device_factory=None, xbox_capable=False):
        self._settings = normalize(settings)
        self._device_factory = device_factory
        self._xbox_capable = xbox_capable
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._status = 'Waiting for game telemetry'
        self._xbox_status = 'Xbox trigger vibration off'
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.bind(('127.0.0.1', 0))
            self._socket.settimeout(.025)
        except BaseException:
            self._socket.close()
            raise
        self._thread = None
        self.new_stream()

    def new_stream(self):
        with self._lock:
            self._token = secrets.randbits(64) or 1
            self._decoder = Decoder(self._token)
            self._frame, self._received = None, 0
            self._peer = None
            self._xbox = XboxOutput(self._token, self._xbox_capable)
        return {'FORZA_TELEMETRY_PORT': str(self._socket.getsockname()[1]),
                'FORZA_TELEMETRY_PHYSICS': '1',
                'FORZA_TELEMETRY_TOKEN': f'{self._token:016x}'}

    def start(self):
        self._thread = threading.Thread(target=self._run, name='fh1-haptics', daemon=True)
        self._thread.start()

    def update(self, settings):
        with self._lock:
            self._settings = normalize(settings)

    @property
    def status(self):
        with self._lock:
            return self._status

    @property
    def xbox_status(self):
        with self._lock:
            return self._xbox_status

    def stop(self):
        self._stop.set()  # Worker releases triggers and closes the device/socket.

    def join(self, timeout=3):
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        device = None
        failed = False
        pedals = (Pedal(), Pedal())
        try:
            while not self._stop.is_set():
                data = None
                try:
                    data, peer = self._socket.recvfrom(4096)
                except socket.timeout:
                    pass
                now = time.monotonic()
                with self._lock:
                    if data and peer[0] == '127.0.0.1':
                        if peer == self._peer and self._xbox.acknowledge(data, now):
                            pass
                        else:
                            decoded = self._decoder.decode(data)
                            if decoded is not None:
                                self._frame, self._received, self._peer = decoded, now, peer
                    frame = self._frame if now-self._received < STALE_SECONDS else None
                    settings = self._settings.copy()
                    output_frame = frame or (replace(self._frame, active=False) if self._frame else None)
                    self._xbox.send(self._socket, self._peer, output_frame, settings, now)
                    self._xbox_status = self._xbox.status(settings['xbox_enabled'], bool(frame and frame.active), now)
                if settings['enabled'] and not device and not failed:
                    try:
                        if self._device_factory:
                            device = self._device_factory()
                        else:
                            from .vendor.dualsense.main import DualSense
                            device = DualSense(retry_interval=2)
                        device.open()
                    except Exception as exc:
                        from .. import paths
                        paths.log_error('start DualSense transport', exc)
                        if device:
                            device.close()
                        device, failed = None, True
                if device:
                    device.set(*effects(frame, settings, pedals))
                    if not settings['enabled']:
                        device.close()
                        device = None
                if not settings['enabled']:
                    status, failed = 'Adaptive triggers off', False
                elif failed:
                    status = 'Controller access unavailable; check the HID driver and launcher log'
                elif not device or not device.connected:
                    error = getattr(device, 'last_error', None)
                    status = f'Controller unavailable: {error}' if error else 'Waiting for DualSense / DualSense Edge'
                elif frame is None or not frame.active:
                    status = 'Controller ready · waiting for driving telemetry (triggers released)'
                else:
                    status = 'Driving telemetry connected · adaptive triggers active'
                with self._lock:
                    self._status = status
        except Exception as exc:
            from .. import paths
            paths.log_error('haptics service stopped', exc)
            with self._lock:
                self._status = 'Haptics stopped; see launcher log'
        finally:
            try:
                with self._lock:
                    self._xbox.last_send = -1
                    disabled = {**self._settings, 'xbox_enabled': False}
                    try:
                        self._xbox.send(self._socket, self._peer, self._frame, disabled, time.monotonic())
                    except OSError:
                        pass  # The game may already have exited. Still release HID below.
                if device:
                    try:
                        device.set(off(), off())
                    finally:
                        device.close()
            finally:
                self._socket.close()
