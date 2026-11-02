"""Real physics effects, v1 compatibility and untrusted v2 bridge samples."""
from dataclasses import replace
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_haptics import packet
from forza_launcher.haptics.telemetry import Decoder, PHYSICS
from forza_launcher.haptics.settings import DEFAULTS
from forza_launcher.haptics.xbox import amplitudes


def physics_packet(*, slip=0., abs_active=0, stamp=42, rpm=3000., redline=6000., gas=1., brake=1.):
    data = bytearray(packet())
    struct.pack_into('<HH', data, 4, 2, 140)
    struct.pack_into('<I', data, 20, 31)
    struct.pack_into('<Q', data, 24, stamp)
    return data + PHYSICS.pack(rpm, redline, 20., brake, gas, 0., slip, slip, slip, slip, abs_active)


class PhysicsTriggerTests(unittest.TestCase):
    prefs = {**DEFAULTS, 'xbox_enabled': True, 'xbox_intensity': 100,
             'xbox_brake': 100, 'xbox_throttle': 100}

    def sample(self, **kw):
        frame = Decoder(1).decode(physics_packet(**kw))
        self.assertIsNotNone(frame)
        return frame

    def test_physics_changes_output_at_fixed_trigger_pressure(self):
        grip = amplitudes(self.sample(), self.prefs)
        spin = amplitudes(self.sample(slip=2.), self.prefs)
        lock = amplitudes(self.sample(slip=-2.), self.prefs)
        self.assertEqual(grip[0], 0)
        self.assertGreater(spin[1], grip[1])
        self.assertGreater(lock[0], grip[0])
        self.assertGreater(amplitudes(self.sample(rpm=5000.), self.prefs)[1], grip[1])

    def test_abs_pulses_and_release(self):
        a = amplitudes(self.sample(abs_active=1, stamp=42), self.prefs)
        b = amplitudes(self.sample(abs_active=1, stamp=84), self.prefs)
        self.assertGreater(a[0], b[0])
        self.assertEqual(amplitudes(self.sample(abs_active=1, brake=0., gas=0.), self.prefs), (0, 0))
        self.assertEqual(amplitudes(replace(self.sample(abs_active=1), active=False), self.prefs), (0, 0))
        self.assertEqual(amplitudes(self.sample(), {**self.prefs, 'xbox_intensity': 0}), (0, 0))

    def test_invalid_physics_does_not_advance_sequence(self):
        for kw in ({'rpm': float('nan')}, {'redline': 0.}, {'gas': 2.}, {'abs_active': 2}):
            decoder = Decoder(1)
            self.assertIsNone(decoder.decode(physics_packet(**kw)))
            self.assertIsNotNone(decoder.decode(physics_packet()))
        self.assertIsNone(Decoder(2).decode(physics_packet()))
        self.assertIsNone(Decoder(1).decode(physics_packet()[:-1]))

    def test_v1_remains_pressure_based(self):
        frame = Decoder(1).decode(packet())
        self.assertIsNone(frame.physics)
        self.assertEqual(amplitudes(frame, self.prefs), (65535, 65535))


if __name__ == '__main__':
    unittest.main()
