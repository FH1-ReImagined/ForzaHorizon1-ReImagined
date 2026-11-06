"""The trigger measuring tools (#692): the report decoder always; the virtual DualSense and a
replay through the launcher's real haptics service where /dev/uhid is writable.

    python -m unittest discover -s tools -p 'test_trigger_harness.py' -v
"""
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / 'launcher'))
import trigger_harness  # noqa: E402
import virtual_dualsense as vds  # noqa: E402
from forza_launcher.haptics.vendor.dualsense.triggers import feedback, off, zone_vibration  # noqa: E402

UHID = os.access('/dev/uhid', os.R_OK | os.W_OK)


def launcher_writer():
    with patch.dict(sys.modules, {'hid': types.ModuleType('hid')}):
        from forza_launcher.haptics.vendor.dualsense import main
    return main


class DecodeTests(unittest.TestCase):
    def test_reports_the_launcher_writes_decode_to_their_effects(self):
        main = launcher_writer()
        device = main.DualSense()
        for layout, transport in ((main._USB, 'usb'), (main._BT, 'bt')):
            device._layout = layout
            report = device._build(feedback([0, 1, 2, 3, 4, 4, 5, 6, 7, 8]),
                                   zone_vibration([0] + [3] * 9, 14), 0, 0, 0, 0, 0, 0)
            out = vds.decode_output(report)
            self.assertEqual(out['transport'], transport)
            self.assertEqual(out['left'], {'mode': 'feedback', 'zones': [0, 1, 2, 3, 4, 4, 5, 6, 7, 8],
                                           'force': 8, 'start': 1, 'end': 9})
            self.assertEqual(out['right']['mode'], 'vibration')
            self.assertEqual((out['right']['amplitude'], out['right']['frequency'],
                              out['right']['start']), (3, 14, 1))
            self.assertNotIn('motors', out)
            if transport == 'bt':
                self.assertTrue(out['crc_ok'])
            self.assertEqual(vds.decode_output(device._build(off(), off(), 0, 0, 0, 0, 0, 0))['left'],
                             {'mode': 'off'})

    def test_the_synthetic_drive_is_valid_telemetry(self):
        sys.path.insert(0, str(HERE.parent / 'launcher'))
        from forza_launcher.haptics.telemetry import Decoder
        decoder = Decoder(1)
        frames = [decoder.decode(p) for _, p in trigger_harness.synthetic_drive(1)]
        self.assertTrue(all(frames))
        self.assertTrue(any(f.physics[10] for f in frames))
        self.assertFalse(frames[-1].active)


@unittest.skipUnless(UHID, '/dev/uhid is not writable here')
class VirtualPadTests(unittest.TestCase):
    def test_a_virtual_pad_is_its_own_hidraw_node(self):
        for bus in ('usb', 'bt'):
            with vds.VirtualDualSense(bus) as pad:
                path = pad.hidraw_path()
                real = os.path.realpath('/sys/class/hidraw/%s/device' % os.path.basename(path))
                self.assertIn('/virtual/misc/uhid/', real)
                fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                try:
                    pad.set_triggers(40, 200)
                    report = b''
                    for _ in range(100):
                        try:
                            report = os.read(fd, 128)
                            break
                        except BlockingIOError:
                            import time
                            time.sleep(.01)
                    offset = 2 if bus == 'bt' else 1  # the L2/R2 bytes of the input report
                    self.assertEqual((report[offset + 4], report[offset + 5]), (40, 200))
                    main = launcher_writer()
                    writer = main.DualSense()
                    writer._layout = main._BT if bus == 'bt' else main._USB
                    os.write(fd, writer._build(feedback([0] + [2] * 9), off(), 0, 0, 0, 0, 0, 0))
                    import time
                    time.sleep(.2)
                    left = [r for _, r in pad.reports if r and 'left' in r][-1]['left']
                    self.assertEqual((left['mode'], left['force']), ('feedback', 2))
                finally:
                    os.close(fd)

    def test_replays_through_the_service_prove_the_effects_ignore_the_trigger(self):
        with tempfile.TemporaryDirectory() as d:
            rec = os.path.join(d, 'drive.jsonl')
            trigger_harness.run_synthetic(types.SimpleNamespace(out=rec, token='0000000000000001'))
            logs = []
            for scale in (1., .5):
                log = os.path.join(d, 'replay-%s.jsonl' % scale)
                trigger_harness.run_replay(types.SimpleNamespace(
                    recording=rec, launcher=['new=%s' % (HERE.parent / 'launcher')], bus='bt',
                    log=log, settings='{}', pedal_scale=scale, speed=1., start=0, end=0))
                logs.append(trigger_harness.load(log)[1]['new'])
            same = [(r['out'].get('left'), r['out'].get('right')) for r in logs[0]]
            half = [(r['out'].get('left'), r['out'].get('right')) for r in logs[1]]
            self.assertEqual(same, half)  # half the trigger travel: the same effects
            sides = [r['out'][k] for r in logs[0] for k in ('left', 'right')]
            self.assertIn(14, [s.get('frequency') for s in sides])   # ABS pulses
            self.assertIn(45, [s.get('frequency') for s in sides])   # rough ground
            self.assertTrue(all(r['out']['crc_ok'] for r in logs[0]))
            times = [r['t'] for r in logs[0]][1:]  # after the write on connecting
            self.assertGreaterEqual(min(b - a for a, b in zip(times, times[1:])), .04)
            self.assertEqual(sides[-2:], [{'mode': 'off'}, {'mode': 'off'}])  # the menu


if __name__ == '__main__':
    unittest.main()
