"""FH1 packet validation, fail-open trigger lifecycle and persisted preferences."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher.haptics.settings import normalize, DEFAULTS
from forza_launcher.haptics.telemetry import WIRE, Decoder
from forza_launcher.haptics.service import HapticsService, effects, profile, supported
from forza_launcher.haptics.vendor.dualsense.triggers import off


def packet(token=1, seq=0, flags=15, active=1, stamp=100, **changes):
    values = dict(position=(1., 2., 3.), velocity=(0., 0., 10.), acceleration=(0., 0., 2.),
                  forward=(0., 0., 1.), brake=255, throttle=255, handbrake=0, steer=0, buttons=0, reserved=0)
    values.update(changes)
    return WIRE.pack(b'FH1T', 1, 96, token, seq, flags, stamp, 0x12340000, active,
                     *values['position'], *values['velocity'], *values['acceleration'], *values['forward'],
                     values['brake'], values['throttle'], values['handbrake'], values['steer'], values['buttons'], values['reserved'])


class FakeDevice:
    connected = True
    def __init__(self):
        self.pairs = []
        self.closed = False
    def open(self): pass
    def set(self, left, right): self.pairs.append((left, right))
    def close(self):
        self.set(off(), off())
        self.closed = True


def wait_for(predicate):
    deadline = time.monotonic()+2
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Worker did not reach expected state')


class PacketTests(unittest.TestCase):
    def test_decoder_rejects_wrong_session_invalid_fields_and_duplicate_order(self):
        decoder = Decoder(1)
        for invalid in (b'', packet()[:-1], packet(token=2), packet(flags=16),
                        packet(flags=1), packet(handbrake=1), packet(position=(float('nan'), 0, 0))):
            self.assertIsNone(decoder.decode(invalid))
        self.assertTrue(decoder.decode(packet()).active)
        self.assertIsNone(decoder.decode(packet()))
        self.assertIsNone(decoder.decode(packet(seq=1, stamp=99)))
        self.assertIsNotNone(decoder.decode(packet(seq=1, stamp=101)))

    def test_sequence_wrap_and_raw_input_mapping(self):
        decoder = Decoder(1)
        self.assertIsNotNone(decoder.decode(packet(seq=0xffffffff)))
        frame = decoder.decode(packet(seq=0))
        prefs = {**DEFAULTS, 'enabled': True, 'intensity': 100}
        left, right = effects(frame, prefs)
        self.assertEqual(left[0], 0x21)
        self.assertNotEqual(left, right)
        self.assertEqual(effects(frame, {**prefs, 'intensity': 0}), (off(), off()))
        self.assertEqual(effects(None, prefs), (off(), off()))

    def test_held_half_press_does_not_flicker(self):
        # #224, #692: the effect must not follow the trigger. A trigger held half way, jittering
        # or pushed back by the resistance, keeps one steady effect: the controller applies the
        # per-zone profile itself, so nothing is re-sent while the trigger moves.
        for prefs in ({**DEFAULTS, 'enabled': True}, {**DEFAULTS, 'enabled': True, 'intensity': 100}):
            decoder = Decoder(1)
            out = set()
            for seq, v in enumerate([0, 20, 61, 59, 62, 60, 127, 129, 126, 130, 200, 255, 4, 12, 0]):
                out.add(effects(decoder.decode(packet(seq=seq, stamp=seq+1, throttle=v, brake=v)), prefs))
            self.assertEqual(len(out), 1)
            left, right = out.pop()
            self.assertEqual((left[0], right[0]), (0x21, 0x21))

    def test_resistance_grows_with_the_press(self):
        for strength in range(9):
            for intensity in (0, 35, 70, 100):
                zones = profile(strength, intensity)
                self.assertEqual(len(zones), 10)
                self.assertEqual(zones, sorted(zones))
                self.assertEqual(zones[0], 0)  # the first tenth of travel stays free
                self.assertLessEqual(zones[-1], 8)
        self.assertEqual(profile(8, 100), [0, 1, 2, 3, 4, 4, 5, 6, 7, 8])
        self.assertEqual(profile(3, 70), [0, 0, 1, 1, 1, 1, 1, 2, 2, 2])  # default throttle
        prefs = {**DEFAULTS, 'enabled': True, 'throttle_strength': 0}
        frame = Decoder(1).decode(packet())
        self.assertEqual(effects(frame, prefs)[1], off())
        self.assertEqual(effects(frame, {**prefs, 'throttle_strength': 1, 'intensity': 10})[1], off())

    def test_menus_release_the_triggers_and_driving_brings_them_back(self):
        decoder = Decoder(1)
        prefs = {**DEFAULTS, 'enabled': True, 'intensity': 100}
        driving = decoder.decode(packet(seq=0, stamp=1, active=1))
        menu = decoder.decode(packet(seq=1, stamp=2, active=0, flags=0))
        again = decoder.decode(packet(seq=2, stamp=3, active=1))
        on = effects(driving, prefs)
        self.assertEqual(on[0][0], 0x21)
        self.assertEqual(effects(menu, prefs), (off(), off()))
        self.assertEqual(effects(again, prefs), on)

    def test_settings_normalize_and_capability_detection(self):
        self.assertEqual(normalize(None), DEFAULTS)
        self.assertEqual(normalize({'enabled': 'true', 'intensity': float('nan'), 'brake_strength': 100}),
                         {**DEFAULTS, 'brake_strength': 8})
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertFalse(supported(root))
            (root/'forza-telemetry.json').write_text('{"protocol":"FH1T","version":1}')
            self.assertTrue(supported(root))


class ServiceTests(unittest.TestCase):
    def test_udp_watchdog_disable_relaunch_and_shutdown_release(self):
        device = FakeDevice()
        service = HapticsService({**DEFAULTS, 'enabled': True}, lambda: device)
        env = service.new_stream()
        service.start()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target = ('127.0.0.1', int(env['FORZA_TELEMETRY_PORT']))
        token = int(env['FORZA_TELEMETRY_TOKEN'], 16)
        try:
            sender.sendto(packet(token), target)
            wait_for(lambda: device.pairs and device.pairs[-1] != (off(), off()))
            wait_for(lambda: device.pairs[-1] == (off(), off()))  # telemetry timeout
            sender.sendto(packet(token, seq=1), target)
            wait_for(lambda: device.pairs[-1] != (off(), off()))
            service.new_stream()
            wait_for(lambda: device.pairs[-1] == (off(), off()))
            sender.sendto(packet(token, seq=2), target)  # stale game cannot reactivate
            time.sleep(.06)
            self.assertEqual(device.pairs[-1], (off(), off()))
            service.update(DEFAULTS)
            wait_for(lambda: device.closed)
        finally:
            service.stop()
            service.join()
            sender.close()
        self.assertFalse(service.running)
        self.assertEqual(device.pairs[-1], (off(), off()))

    def test_disabled_never_opens_hid_and_game_start_failure_cleans_up(self):
        factory = unittest.mock.Mock()
        service = HapticsService(DEFAULTS, factory)
        service.start()
        wait_for(lambda: service.status == 'Adaptive triggers off')
        service.stop()
        service.join()
        factory.assert_not_called()
        from forza_launcher.game import Session, LaunchPlan
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root/'forza-telemetry.json').write_text('{"protocol":"FH1T","version":1}')
            plan = LaunchPlan(None, root, ['missing-game'], {}, [], root/'forza.toml', '')
            plan.haptics_settings = {**DEFAULTS, 'enabled': True}
            session = Session(plan)
            with patch('forza_launcher.game.subprocess.Popen', side_effect=OSError('missing')):
                with self.assertRaises(OSError): session.start()
            session.haptics.join()
            self.assertFalse(session.haptics.running)

    def test_hid_reports_claim_only_triggers_and_have_valid_bluetooth_crc(self):
        import types, zlib, struct
        with patch.dict(sys.modules, {'hid': types.ModuleType('hid')}):
            from forza_launcher.haptics.vendor.dualsense.main import DualSense, _BT, _USB
        device = DualSense()
        for layout in (_USB, _BT):
            device._layout = layout
            report = device._build(off(), off(), 255, 255, 255, 255, 255, 31)
            self.assertEqual(report[layout['flags']], 0x0c)
            self.assertEqual(report[layout['vf1']], 0)
            self.assertEqual(report[layout['vf2']], 0)
            if layout['bt']:
                self.assertEqual(struct.unpack_from('<I', report, 74)[0], zlib.crc32(b'\xa2'+report[:74]))

    def test_unchanged_effects_write_once_over_usb_and_bluetooth(self):
        # #692: the service asks for the effect pair on every telemetry frame. Only a change, or a
        # reconnect, may reach the controller.
        import types
        from forza_launcher.haptics.vendor.dualsense.triggers import feedback
        with patch.dict(sys.modules, {'hid': types.ModuleType('hid')}):
            from forza_launcher.haptics.vendor.dualsense import main
        for bus, report_id in ((3, 0x02), (5, 0x31)):
            writes = []
            class Hid:
                def open_path(self, path): pass
                def set_nonblocking(self, flag): pass
                def read(self, size): return b''
                def write(self, data): writes.append(bytes(data))
                def close(self): pass
            info = {'product_id': 0x0CE6, 'usage_page': 1, 'usage': 5, 'path': b'p', 'bus_type': bus}
            fake = types.SimpleNamespace(enumerate=lambda vid, pid: [info], device=Hid)
            with patch.object(main, 'hid', fake):
                device = main.DualSense(retry_interval=.01)
                device.open()
                try:
                    wait_for(lambda: device.connected and writes)
                    on = feedback([0, 1, 2, 3, 4, 4, 5, 6, 7, 8])
                    for _ in range(50):
                        device.set(on, on)
                        time.sleep(.002)
                    wait_for(lambda: len(writes) == 2)
                    time.sleep(.05)
                    self.assertEqual(len(writes), 2)  # the connect write, then the effect once
                    self.assertEqual(writes[-1][0], report_id)
                    device.set(off(), off())
                    wait_for(lambda: len(writes) == 3)
                finally:
                    device.close()


class SettingsUiTests(unittest.TestCase):
    def test_enabling_during_a_session_without_telemetry_requests_restart(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QApplication
        from forza_launcher.config import Config
        from forza_launcher.ui.controls_page import ControlsPage
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            metadata = root/'forza-telemetry.json'
            metadata.write_text('{"protocol":"FH1T","version":1,"xbox_trigger_rumble":1}')
            session = SimpleNamespace(haptics=None, plan=SimpleNamespace(cwd=root))
            page = ControlsPage(Config(root/'launcher.json'), SimpleNamespace(session=session))
            try:
                page.haptics_enabled.setChecked(True)
                page.xbox_enabled.setChecked(True)
                self.assertIn('restart the game', page.haptics_status.text())
                self.assertIn('restart the game', page.xbox_status.text())
                metadata.unlink()
                page.refresh_haptics()
                self.assertIn('Rebuild', page.haptics_status.text())
                self.assertIn('Rebuild', page.xbox_status.text())
            finally:
                page.close()
                page.deleteLater()
                app.processEvents()

    def test_controls_preferences_persist_without_changing_game_settings(self):
        from PySide6.QtWidgets import QApplication
        from forza_launcher.config import Config
        from forza_launcher.ui.controls_page import ControlsPage
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(Path(d)/'launcher.json')
            original = cfg.settings.copy()
            page = ControlsPage(cfg)
            page.haptics_enabled.setChecked(True)
            page.haptics_fields['throttle_strength'].setValue(2)
            self.assertEqual(Config(cfg.path).d['haptics']['throttle_strength'], 2)
            self.assertTrue(Config(cfg.path).d['haptics']['enabled'])
            self.assertEqual(cfg.settings, original)
            page.close()
            page.deleteLater()
            app.processEvents()


if __name__ == '__main__': unittest.main()
