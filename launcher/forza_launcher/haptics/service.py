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


ZONES = 10  # MultiplePositionFeedback: ten zones along the trigger's travel


def profile(strength, intensity):
    """Resistance that grows with the press: each zone has the force the trigger's position in it
    asks for (strength 0-8 at full travel, scaled by intensity %). The first zone stays free."""
    top = strength * intensity / 100
    return [int(top*(i+.5)/ZONES + .5) for i in range(ZONES)]


def effects(frame, settings):
    """Basic resistance for the default driving layout (LT brake, RT throttle).

    The effect is a fixed per-zone profile, so the controller itself makes the trigger stiffer the
    deeper it goes. It does not follow the live LT/RT value: an effect set from the trigger's
    position moves the trigger, the next report moves the effect, and over Bluetooth's latency a
    trigger held half way bounced between two forces (#224, #692). The pair changes only when
    driving starts or stops or a setting changes. Deliberately does not infer ABS, traction,
    gears or RPM from missing signals. Without a frame or outside driving (`frame.active` is false
    in menus) both triggers are released.
    """
    if frame is None or not frame.active or not settings['enabled']:
        return off(), off()
    def pedal(strength):
        zones = profile(strength, settings['intensity'])
        return feedback(zones) if any(zones) else off()
    return pedal(settings['brake_strength']), pedal(settings['throttle_strength'])


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
                    device.set(*effects(frame, settings))
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
