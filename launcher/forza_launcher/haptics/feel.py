"""DualSense trigger effects from what the car does (#692).

The effects follow the game's telemetry, never the trigger's own position: an effect set from
where the trigger is pushes the trigger, the next report moves the effect, and over Bluetooth's
latency a trigger held half way bounced (#224). #696 stopped that with one fixed profile, which
also stopped every reaction to driving. Here each trigger gets an effect picked from the car's
state (FH1T v2 physics: speed, ABS, the four tire slips, RPM; the car's height) and the
controller runs it by itself: resistance as a per-zone profile (mode 0x21), pulses and buzz as
its vibration mode (0x26), so the fast part never goes through the PC.

  Brake (L2)    ABS working: pulses at ABS_HZ. A locked wheel without ABS: a coarser judder.
                Braking (the car slows by more than DECEL_FROM m/s2): resistance that grows
                with the deceleration. Otherwise light pedal weight (BASE).
  Throttle (R2) Wheelspin: a buzz that rises in frequency and strength with the spin. Near the
                rev limiter: a light wall at the end of the travel. Otherwise free.
  Both          Rough ground (off-road, gravel, rumble strips; the car's vertical shaking): a
                low buzz. An impact (a sudden loss of speed): a short strong jolt. Off in menus,
                and when a trigger is pressed and the car gets nothing (a menu over a world that
                keeps running, which the game's driving flag only notices after seconds).

Changes reach the controller only when the effect's target moves past a threshold (a different
effect, one zone step of force, a step of amplitude or FREQ_STEP Hz), at most every MIN_GAP_MS
(20 Hz, on the game's sample clock and on the receiver's). Signals use the game's sample clock
(frame.sample_ms), so a replay of a recorded drive gives the same effects. Measured with
tools/trigger_harness.py (docs/telemetry.md "Measuring the trigger effects"). The sliders: intensity scales everything, brake and throttle strength
(0-8, Off at 0) are the strongest force or amplitude of that trigger.
"""
from collections import deque

from .vendor.dualsense.triggers import feedback, off, zone_vibration

ZONES = 10
MIN_GAP_MS = 50          # at most 20 effect changes a second
FREQ_STEP = 5            # Hz: a smaller change of a vibration's frequency is not sent
FORCE_STEP = .75         # zone units past the current force before it changes
HOLD_MS = 150            # ABS, lock and spin stay on this long after their signal stops
MOVING = 2.0             # m/s: below this, slips and ABS are noise of a standing car
DECEL_WINDOW_MS = 200    # deceleration from the game's speed over this window
DECEL_FROM, DECEL_FULL = 2.0, 10.0   # m/s2: braking resistance starts / is at full
BASE = .3                # brake pedal weight while not braking, share of the strength
SLIP_FROM = .1           # normalized slip beyond peak grip before lock or spin counts
ABS_HZ, LOCK_HZ = 14, 24
SPIN_HZ = (30, 80)
ROUGH_FROM, ROUGH_PER_MS, ROUGH_SPAN = 3.0, .06, 5.0
# m/s2 RMS of vertical shaking where rough ground starts: ROUGH_FROM + ROUGH_PER_MS * speed (m/s),
# full ROUGH_SPAN above it. Measured on TU4 at 30 fps: tarmac 0.2-4.2 (the most at 30-40 m/s),
# grass 2-9 at 4-11 m/s (mean 3.7).
ROUGH_HZ = 45
ROUGH_HOLD_MS = 300
IMPACT_DECEL = 30.0      # m/s2 of speed lost over the window: a crash, not brakes (ABS stops
IMPACT_LOSS = 4.0        # measured 11-14 m/s2) ... and at least this many m/s of it
IMPACT_MS, IMPACT_HZ = 250, 35
GHOST_FRAMES = 3
LIMIT = .93              # share of the redline where the throttle wall starts


def _ramp(top, first=1):
    """Resistance growing along the travel up to `top` (0-8) in the last zone."""
    return [0 if i < first else max(1, int(top * (i + 1) / ZONES + .5)) for i in range(ZONES)]


def _buzz(amplitude, first=1):
    return [0 if i < first else amplitude for i in range(ZONES)]


class Target:
    """One trigger's wanted effect: kind and its numbers, before encoding."""
    __slots__ = ('kind', 'level', 'freq', 'first')

    def __init__(self, kind='off', level=0., freq=0, first=1):
        self.kind, self.level, self.freq, self.first = kind, level, freq, first

    def encode(self):
        level = int(self.level + .5)
        if self.kind == 'resist' and level > 0:
            return feedback(_ramp(self.level, self.first))
        if self.kind == 'vibrate' and level > 0 and self.freq > 0:
            return zone_vibration(_buzz(min(8, level), self.first), self.freq)
        return off()

    def differs(self, other):
        """Whether `other` (the wanted target) is far enough from this (the sent one)."""
        if other.kind != self.kind or other.first != self.first:
            return True
        if self.kind == 'resist':
            return abs(other.level - self.level) >= FORCE_STEP
        if self.kind == 'vibrate':
            return (int(other.level + .5) != int(self.level + .5) or
                    abs(other.freq - self.freq) >= FREQ_STEP)
        return False


class Feel:
    """Telemetry in, (left, right) effects out. Feed it every new frame with update()."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.speeds = deque()          # (sample_ms, speed)
        self.heights = deque()         # (sample_ms, height)
        self.last_ms = self.gap = self.climb = self.last_position = None
        self.up_avg = self.shake2 = 0.
        self.rough = 0.                # vertical shaking, m/s2 RMS
        self.rough_from = ROUGH_FROM
        self.ghost = 0                 # frames with a trigger pressed that the car ignores
        self.rough_until = -1
        self.impact_until = -1
        self.abs_until = self.lock_until = self.spin_until = -1
        self.spin = self.lock = 0.
        self.decel = 0.
        self.sequence = None
        self.sent = [Target(), Target()]
        self.sent_ms = self.sent_wall = None
        self.output = (off(), off())

    # -- signals --
    def _signals(self, frame):
        now = frame.sample_ms
        physics = frame.physics
        if physics is not None:
            speed = abs(physics[2])
            self.rough_from = ROUGH_FROM + ROUGH_PER_MS * speed
            self.speeds.append((now, speed))
            while len(self.speeds) > 2 and now - self.speeds[1][0] >= DECEL_WINDOW_MS:
                self.speeds.popleft()
            then, before = self.speeds[0]
            self.decel = (before - speed) * 1000 / (now - then) if now - then >= 50 else 0.
            slips = physics[6:10]
            moving = speed > MOVING
            if moving and physics[10]:
                self.abs_until = now + HOLD_MS
            self.lock = max(0., -min(slips) - 1.) if moving else 0.
            if self.lock > SLIP_FROM:
                self.lock_until = now + HOLD_MS
            self.spin = max(0., max(slips) - 1.) if speed > .5 else 0.
            if self.spin > SLIP_FROM:
                self.spin_until = now + HOLD_MS
            if self.decel > IMPACT_DECEL and before - speed > IMPACT_LOSS:
                self.impact_until = now + IMPACT_MS
        self._shake(frame, now)
        self._ghost(frame)
        return now

    def _shake(self, frame, now):
        """Vertical shaking from the car's height over the game's clock: rough ground. The
        derived acceleration in the packet is not used: a frame that comes late or early (a
        hitch, the frames that catch up after it) gives it spikes of 40 m/s2 on a smooth road.
        Here a frame whose gap is far from the usual one is skipped, and the shaking is the
        RMS of the high-passed vertical acceleration over about 300 ms. The game samples at its
        physics substeps, so at times every other packet repeats the last position: only the
        packets where the car moved count."""
        if not frame.flags & 1 or frame.position == self.last_position:
            return  # a sample between two physics steps repeats the position: skip it
        self.last_position = frame.position
        gap = now - self.last_ms if self.last_ms is not None else None
        self.last_ms = now
        y = frame.position[1]
        if gap is None or gap <= 0 or (self.heights and abs(y - self.heights[-1][1]) > 2):
            self.heights.clear()  # the first sample, or a jump (a reset to the road)
            self.climb = None
            self.shake2 = self.rough = 0.
        else:
            self.gap = gap if self.gap is None else self.gap * .9 + gap * .1
            if not .6 * self.gap <= gap <= 1.6 * self.gap:
                self.heights.clear()   # start again after a hitch or a burst
                self.climb = None
        if not self.heights:
            self.heights.append((now, y))
            return
        then, low = self.heights[-1]
        self.heights.clear()
        self.heights.append((now, y))
        climb = (y - low) * 1000 / (now - then)
        if self.climb is not None:
            up = (climb - self.climb) * 1000 / gap
            self.up_avg = self.up_avg * .85 + up * .15
            self.shake2 = self.shake2 * .9 + (up - self.up_avg) ** 2 * .1
            self.rough = self.shake2 ** .5
            if self.rough > self.rough_from:
                self.rough_until = now + ROUGH_HOLD_MS
        self.climb = climb

    def _ghost(self, frame):
        """A trigger pressed that the car does not get: a menu over a world that keeps running
        (the online pause menu) or any screen that takes the pad. Both triggers go off at once
        instead of waiting for the game's rumble to stop (#692), and stay off until the car
        gets a pedal again (the game's processed brake or throttle above zero)."""
        p = frame.physics
        if p is None:
            return
        pressed = max(frame.brake, frame.throttle) > 64
        idle = p[3] < .05 and p[4] < .05
        if not idle:
            self.ghost = 0
        elif pressed or self.ghost >= GHOST_FRAMES:
            self.ghost += 1

    # -- targets --
    def _targets(self, frame, settings, now):
        scale = settings['intensity'] / 100
        brake, throttle = settings['brake_strength'] * scale, settings['throttle_strength'] * scale
        physics = frame.physics is not None
        rough = min(1., max(.01, (self.rough - self.rough_from) / ROUGH_SPAN)) \
            if now < self.rough_until else 0.

        def vib(strength, share, freq, first=1):
            return Target('vibrate', max(1., strength * share), freq, first) if strength else Target()

        def left():
            if not brake:
                return Target()
            if now < self.impact_until:
                return vib(brake, 1., IMPACT_HZ)
            if now < self.abs_until:
                return vib(brake, .8, ABS_HZ)
            if now < self.lock_until:
                return vib(brake, .5 + .5 * min(1., self.lock), LOCK_HZ)
            if physics and self.decel > DECEL_FROM:
                share = min(1., (self.decel - DECEL_FROM) / (DECEL_FULL - DECEL_FROM))
                return Target('resist', brake * (BASE + (1 - BASE) * share))
            if rough:
                return vib(brake, .25 + .25 * rough, ROUGH_HZ)
            return Target('resist', brake * BASE)

        def right():
            if not throttle:
                return Target()
            if now < self.impact_until:
                return vib(throttle, 1., IMPACT_HZ)
            if now < self.spin_until:
                spin = min(1., self.spin)
                return vib(throttle, .4 + .6 * spin,
                           SPIN_HZ[0] + (SPIN_HZ[1] - SPIN_HZ[0]) * spin)
            if rough:
                return vib(throttle, .25 + .25 * rough, ROUGH_HZ)
            if physics and frame.physics[1] > 0 and frame.physics[0] / frame.physics[1] > LIMIT:
                return Target('resist', throttle * .6, first=7)
            return Target()

        return left(), right()

    def update(self, frame, settings, wall=None):
        """The (left, right) effects for this frame. Off at once outside driving. `wall` (the
        receiver's monotonic seconds) also holds the 20 Hz limit when frames arrive bunched."""
        if frame is None or not frame.active or not settings['enabled']:
            if self.sequence is not None:
                self.reset()
            return off(), off()
        if frame.sequence == self.sequence:
            return self.output
        self.sequence = frame.sequence
        now = self._signals(frame)
        if self.ghost >= GHOST_FRAMES:
            self.sent, self.sent_ms, self.sent_wall = [Target(), Target()], now, wall
            self.output = (off(), off())
            return self.output
        wanted = self._targets(frame, settings, now)
        if self.sent_ms is not None and (now - self.sent_ms < MIN_GAP_MS or (
                wall is not None and self.sent_wall is not None and
                wall - self.sent_wall < MIN_GAP_MS / 1000)):
            return self.output
        changed = False
        for i in (0, 1):
            if self.sent_ms is None or self.sent[i].differs(wanted[i]):
                self.sent[i] = wanted[i]
                changed = True
        if changed:
            self.sent_ms, self.sent_wall = now, wall
            self.output = (self.sent[0].encode(), self.sent[1].encode())
        return self.output
