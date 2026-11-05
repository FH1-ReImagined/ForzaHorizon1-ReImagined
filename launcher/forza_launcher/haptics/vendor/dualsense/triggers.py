"""DualSense adaptive trigger HID effect encoding.

Implements the trigger effect modes documented in the community reverse-
engineering of the DualSense HID output report:
https://github.com/nondebug/dualsense
"""

# Trigger mode bytes
_OFF       = 0x05
_RIGID     = 0x01
_VIBRATION = 0x06
_FEEDBACK  = 0x21  # MultiplePositionFeedback: 10-zone per-zone strength
_PULSE_AB  = 0x26  # Pulse_AB: zone strength + rhythmic pulse


def _clamp(v):
    return max(0, min(255, round(v)))


def _pack_zones(zone_values):
    """Pack zone strengths (0-8 each, up to 10 zones) into active-mask + strength bytes.

    Each zone contributes 1 bit to the active mask and 3 bits to the strength
    field, as specified in the DualSense HID output report format.
    """
    active = 0
    strength = 0
    for i, s in enumerate(zone_values[:10]):
        s = max(0, min(8, int(s)))
        if s:
            active |= 1 << i
            strength |= (s - 1) << (3 * i)
    return (
        active & 0xFF, (active >> 8) & 0xFF,
        strength & 0xFF, (strength >> 8) & 0xFF,
        (strength >> 16) & 0xFF, (strength >> 24) & 0xFF,
    )


def off():
    return (_OFF, ())


def rigid(force):
    """Constant resistance."""
    return (_RIGID, (0, _clamp(force)))


def vibration(freq, amp):
    """Rhythmic pulse vibration."""
    return (_VIBRATION, (_clamp(freq), _clamp(amp)))


def feedback(zones):
    """Static per-zone resistance: list of up to 10 strengths (0-8)."""
    return (_FEEDBACK, _pack_zones(zones) + (0, 0, 0, 0))


def vibration_wall(amp, freq, wall_zones):
    """Lower zones vibrate; top wall_zones zones hold at max resistance."""
    wall = max(1, min(9, int(wall_zones)))
    amp = max(1, min(8, int(amp)))
    zones = [amp] * (10 - wall) + [8] * wall
    return (_PULSE_AB, _pack_zones(zones) + (_clamp(freq), 0, 0, 0))


def zone_vibration(zones, freq):
    """Vibration (mode 0x26) with a per-zone amplitude (0-8 each, 10 zones) at `freq` Hz: the
    controller runs it by itself where the trigger is in an active zone. Frequency byte at
    parameter 8, as in Nielk1's trigger effect factory."""
    return (_PULSE_AB, _pack_zones(zones) + (0, 0, _clamp(freq), 0))
