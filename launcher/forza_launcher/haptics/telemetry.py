"""Strict decoder for the native FH1T v1 bridge, not Forza's later Data Out format."""
from dataclasses import dataclass
import math
import struct

WIRE = struct.Struct('<4sHHQIIQII12fBBBbHH')
PHYSICS = struct.Struct('<10fI')
assert WIRE.size == 96


@dataclass(frozen=True)
class Frame:
    sequence: int
    flags: int
    sample_ms: int
    car: int
    active: bool
    position: tuple
    velocity: tuple
    acceleration: tuple
    forward: tuple
    brake: int
    throttle: int
    steer: int
    buttons: int
    physics: tuple | None = None  # RPM, redline, m/s, processed brake/gas/handbrake, 4 slips, ABS


class Decoder:
    def __init__(self, token):
        self.token = token
        self.sequence = None
        self.sample_ms = None

    def decode(self, data):
        if len(data) not in (WIRE.size, WIRE.size + PHYSICS.size):
            return None
        magic, version, size, token, seq, flags, stamp, car, active, *v = WIRE.unpack(data[:WIRE.size])
        if (magic, token) != (b'FH1T', self.token) or size != len(data):
            return None
        if (version, size) not in ((1, WIRE.size), (2, WIRE.size + PHYSICS.size)):
            return None
        if flags & ~(31 if version == 2 else 15) or active not in (0, 1) or v[14] != 0 or v[17] != 0:
            return None  # Handbrake is intentionally unavailable in v1.
        physics = None
        if version == 2:
            values = PHYSICS.unpack(data[WIRE.size:])
            if any(not math.isfinite(f) or abs(f) > 1e6 for f in values[:10]) or values[10] not in (0, 1):
                return None
            if flags & 16:
                rpm, redline, speed, brake, gas, handbrake = values[:6]
                if not car or rpm < 0 or redline <= 0 or abs(speed) > 300 or any(
                        not 0 <= f <= 1 for f in (brake, gas, handbrake)):
                    return None
                physics = values
        if active and (not car or flags & 3 != 3):
            return None
        if any(not math.isfinite(f) or abs(f) > 1e6 for f in v[:12]):
            return None
        if self.sequence is not None and not 0 < ((seq-self.sequence) & 0xffffffff) < 0x80000000:
            return None
        if self.sample_ms is not None and stamp < self.sample_ms:
            return None
        self.sequence, self.sample_ms = seq, stamp
        return Frame(seq, flags, stamp, car, bool(active), tuple(v[:3]), tuple(v[3:6]),
                     tuple(v[6:9]), tuple(v[9:12]), v[12], v[13], v[15], v[16], physics)
