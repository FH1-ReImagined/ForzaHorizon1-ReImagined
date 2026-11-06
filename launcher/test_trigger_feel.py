"""DualSense trigger effects from the car's telemetry (#692): feel.py.

The effects follow the telemetry, never the trigger's position; ABS pulses, rough ground
vibrates, and the controller is told at most 20 times a second.
"""
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from forza_launcher.haptics import feel
from forza_launcher.haptics.feel import Feel
from forza_launcher.haptics.settings import DEFAULTS
from forza_launcher.haptics.telemetry import Frame
from forza_launcher.haptics.vendor.dualsense.triggers import off

ON = {**DEFAULTS, 'enabled': True}


def frame(seq, ms, *, speed=20., lt=0, rt=0, brake=0., gas=0., slips=(1., 1., 1., 1.), abs_on=0,
          y=100., rpm=3000., redline=7000., active=True, x=None):
    physics = (rpm, redline, speed, brake, gas, 0., *slips, abs_on)
    return Frame(seq, 31, ms, 0x1234, active, (x if x is not None else seq * .5, y, 0.),
                 (0., 0., speed), (0., 0., 0.), (0., 0., 1.), lt, rt, 0, 0, physics)


class Drive:
    """Frames at 30 Hz on the game's clock, through one Feel."""
    def __init__(self, settings=ON):
        self.feel, self.settings, self.seq, self.ms, self.out = Feel(), settings, 0, 1000, []

    def run(self, n, **kw):
        for i in range(n):
            args = {k: (v(i) if callable(v) else v) for k, v in kw.items()}
            self.out.append((self.ms, self.feel.update(frame(self.seq, self.ms, **args),
                                                       self.settings)))
            self.seq += 1
            self.ms += 33
        return [o for _, o in self.out[-n:]]


def mode(effect):
    return effect[0]


def zones(effect):
    p = effect[1]
    active = p[0] | p[1] << 8
    strength = p[2] | p[3] << 8 | p[4] << 16 | p[5] << 24
    return [((strength >> 3 * i) & 7) + 1 if active >> i & 1 else 0 for i in range(10)]


def frequency(effect):
    return effect[1][8]


def scenario(drive, pedal):
    """Braking from speed, ABS, a launch with wheelspin, rough ground. `pedal(i)` gives the
    trigger positions (raw 0-255 and the game's processed 0-1) while a pedal is pressed."""
    speed = [40.]
    def slower(step):
        def f(i):
            speed[0] = max(0., speed[0] - step)
            return speed[0]
        return f
    drive.run(30, speed=40., rt=lambda i: pedal(i)[0], gas=lambda i: pedal(i)[1])
    drive.run(30, speed=slower(.25), lt=lambda i: pedal(i)[0], brake=lambda i: pedal(i)[1])
    drive.run(20, speed=slower(.4), lt=lambda i: pedal(i)[0], brake=lambda i: pedal(i)[1],
              abs_on=lambda i: i % 3 != 2, slips=(.5, .5, .6, .6))
    drive.run(30, speed=lambda i: 1 + i * .3, rt=lambda i: pedal(i)[0], gas=lambda i: pedal(i)[1],
              slips=(1., 1., 2.5, 2.5))
    drive.run(60, speed=12., y=lambda i: 100 + (.06, -.04, .02)[i % 3])
    return [o for _, o in drive.out]


class FeelTests(unittest.TestCase):
    def test_the_effect_does_not_follow_the_trigger_position(self):
        # The same drive with the triggers held at different, jittering positions: every
        # effect sent is the same. Only the car's telemetry moves the effects.
        rng = random.Random(692)
        full = scenario(Drive(), lambda i: (255, 1.))
        for _ in range(5):
            jitter = scenario(Drive(), lambda i: (rng.randint(70, 255), rng.uniform(.1, 1.)))
            self.assertEqual(jitter, full)

    def test_abs_pulses_the_brake_and_braking_resists(self):
        d = Drive()
        d.run(10, speed=30.)
        braking = d.run(20, speed=lambda i: 30 - i * .3, lt=255, brake=1.)
        self.assertEqual(mode(braking[-1][0]), 0x21)  # resistance, from the deceleration
        light = max(zones(d.run(1, speed=24.)[0][0]))
        abs_out = d.run(15, speed=lambda i: 24 - i * .4, lt=255, brake=1., abs_on=1)
        effect = abs_out[-1][0]
        self.assertEqual(mode(effect), 0x26)
        self.assertEqual(frequency(effect), feel.ABS_HZ)
        self.assertGreaterEqual(max(zones(effect)), 3)
        self.assertEqual(zones(effect)[0], 0)  # free at rest: felt only when pressed
        after = d.run(15, speed=lambda i: 18 - i * .3, lt=255, brake=1.)
        self.assertEqual(mode(after[-1][0]), 0x21)
        self.assertGreaterEqual(max(zones(braking[-1][0])), light)

    def test_harder_braking_resists_more(self):
        forces = []
        for step in (.1, .2, .3):  # 3, 6 and 9 m/s2 at 30 Hz
            d = Drive()
            d.run(10, speed=40.)
            forces.append(max(zones(d.run(20, speed=lambda i: 40 - i * step, lt=255,
                                          brake=1.)[-1][0])))
        self.assertEqual(forces, sorted(forces))
        self.assertLess(forces[0], forces[-1])

    def test_rough_ground_vibrates_and_smooth_road_does_not(self):
        d = Drive()
        smooth = d.run(60, speed=15., y=lambda i: 100 + i * .002)  # a gentle climb
        self.assertTrue(all(mode(r) != 0x26 for _, r in smooth))
        rough = d.run(60, speed=15., y=lambda i: 100 + (.06, -.04, .02)[i % 3])
        self.assertEqual(mode(rough[-1][1]), 0x26)
        self.assertEqual(frequency(rough[-1][1]), feel.ROUGH_HZ)
        self.assertEqual(mode(rough[-1][0]), 0x26)

    def test_wheelspin_buzzes_the_throttle_and_rises_with_the_spin(self):
        d = Drive()
        little = d.run(10, speed=5., rt=255, gas=1., slips=(1., 1., 1.3, 1.3))[-1][1]
        lots = d.run(10, speed=5., rt=255, gas=1., slips=(1., 1., 2.5, 2.5))[-1][1]
        self.assertEqual((mode(little), mode(lots)), (0x26, 0x26))
        self.assertGreater(frequency(lots), frequency(little))
        grip = d.run(20, speed=10., rt=255, gas=1.)[-1][1]
        self.assertEqual(grip, off())

    def test_an_impact_jolts_both_triggers(self):
        d = Drive()
        d.run(10, speed=25.)
        hit = d.run(3, speed=lambda i: 25 - (i + 1) * 4)[-1]
        self.assertEqual((mode(hit[0]), mode(hit[1])), (0x26, 0x26))
        self.assertEqual(frequency(hit[0]), feel.IMPACT_HZ)

    def test_at_most_twenty_changes_a_second_and_none_when_nothing_changes(self):
        d = Drive()
        # A target that wants to change every frame: deceleration swinging between light and
        # hard, ABS flickering, spin on and off.
        speeds = [40.]
        def swing(i):
            speeds[0] -= .1 if i % 2 else .45
            return speeds[0]
        d.run(10, speed=40.)
        d.run(120, speed=swing, abs_on=lambda i: i % 5 == 0, slips=lambda i: (1, 1, 1 + i % 3, 1))
        changes = [ms for (ms, a), (_, b) in zip(d.out, d.out[1:]) if a != b]
        gaps = [b - a for a, b in zip(changes, changes[1:])]
        self.assertGreater(len(changes), 3)
        self.assertGreaterEqual(min(gaps), feel.MIN_GAP_MS)
        self.assertLessEqual(len(changes) / (120 * .033), 20)
        steady = d.run(60, speed=20.)
        self.assertEqual(len(set(steady[10:])), 1)

    def test_menus_and_ignored_presses_release_both_triggers(self):
        d = Drive()
        d.run(20, speed=20.)
        self.assertEqual(d.run(1, speed=20., active=False)[0], (off(), off()))
        d.run(20, speed=20.)
        # The online pause menu: the world runs on, the trigger is pressed, the car gets
        # nothing.
        menu = d.run(5, speed=20., lt=255, brake=0., gas=0.)
        self.assertEqual(menu[-1], (off(), off()))
        self.assertEqual(d.run(10, speed=20.)[-1], (off(), off()))  # still in the menu
        self.assertNotEqual(d.run(3, speed=20., rt=255, gas=.5)[-1], (off(), off()))  # driving

    def test_the_sliders_scale_and_zero_turns_a_trigger_off(self):
        def abs_amplitude(settings):
            d = Drive(settings)
            d.run(5, speed=30.)
            return max(zones(d.run(10, speed=lambda i: 30 - i * .4, abs_on=1)[-1][0]))
        self.assertLess(abs_amplitude({**ON, 'intensity': 30}), abs_amplitude({**ON, 'intensity': 100}))
        self.assertLess(abs_amplitude({**ON, 'brake_strength': 3}), abs_amplitude({**ON, 'brake_strength': 8}))
        d = Drive({**ON, 'brake_strength': 0, 'throttle_strength': 0})
        self.assertTrue(all(o == (off(), off()) for o in d.run(30, speed=20., abs_on=1)))
        self.assertTrue(all(o == (off(), off()) for o in Drive({**ON, 'enabled': False}).run(5)))

    def test_without_physics_only_light_pedal_weight(self):
        f = Frame(1, 15, 100, 0x1234, True, (0., 0., 0.), (0., 0., 0.), (0., 0., 0.), (0., 0., 1.),
                  255, 255, 0, 0, None)
        left, right = Feel().update(f, ON)
        self.assertEqual(mode(left), 0x21)
        self.assertLessEqual(max(zones(left)), 2)
        self.assertEqual(right, off())


if __name__ == '__main__':
    unittest.main()
