"""Impulse trigger commands to the game's SDL backend; no second HID owner."""
import struct

COMMAND = struct.Struct('<4sHHQIIHHI')
ACK = struct.Struct('<4sHHQIi')


def amplitudes(frame, settings):
    if frame is None or not frame.active or not settings['xbox_enabled']:
        return 0, 0
    if frame.physics is not None:
        rpm, redline, speed, brake, gas, handbrake, *tail = frame.physics
        slips, abs_active = tail[:4], tail[4]
        # ABS is the game's actual per-step state. Lock/spin require tire contact,
        # checked by the native reader, and movement so a stationary launch is quiet.
        moving = abs(speed) > 2
        lock = min(1., max(0., -min(slips) - 1.)) if moving else 0.
        spin = min(1., max(0., max(slips) - 1.)) if moving else 0.
        pulse = 1. if (frame.sample_ms // 42) % 2 else .35  # about 12 Hz
        left = brake * (pulse if abs_active and moving else lock)
        right = gas * min(1., .12 + .25 * min(1., rpm / redline) + .63 * spin)
        scale = 65535 * settings['xbox_intensity'] / 10000
        return (round(scale * settings['xbox_brake'] * left),
                round(scale * settings['xbox_throttle'] * right))
    scale = 65535 * settings['xbox_intensity'] / 100 / 100 / 255
    return (round(scale * settings['xbox_brake'] * frame.brake),
            round(scale * settings['xbox_throttle'] * frame.throttle))


class XboxOutput:
    def __init__(self, token, capable):
        self.token, self.capable = token, capable
        self.sequence = 0
        self.ack_sequence = None
        self.result = None
        self.ack_time = 0
        self.last_send = -1
        self.release_until = 0
        self.was_enabled = False

    def acknowledge(self, data, now):
        if len(data) != ACK.size:
            return False
        magic, version, size, token, sequence, result = ACK.unpack(data)
        if (magic, version, size, token) != (b'FH1A', 1, ACK.size, self.token):
            return False
        if not 0 < ((self.sequence-sequence) & 0xffffffff) < 0x80000000:
            return False  # No acknowledgement of unsent commands.
        if self.ack_sequence is not None and not 0 < ((sequence-self.ack_sequence) & 0xffffffff) < 0x80000000:
            return False
        self.result, self.ack_time, self.ack_sequence = result, now, sequence
        return True

    def send(self, sock, peer, frame, settings, now):
        enabled = settings['xbox_enabled']
        if self.was_enabled and not enabled:
            self.release_until = now+.2
        self.was_enabled = enabled
        if not self.capable or peer is None or frame is None or (not enabled and now >= self.release_until):
            return
        if now-self.last_send < .025:
            return
        left, right = amplitudes(frame, settings)
        data = COMMAND.pack(b'FH1X', 1, COMMAND.size, self.token, self.sequence,
                            frame.sequence, left, right, 0)
        sock.sendto(data, peer)
        self.sequence = (self.sequence+1) & 0xffffffff
        self.last_send = now

    def status(self, enabled, active, now):
        if not enabled:
            return 'Xbox trigger vibration off'
        if not self.capable:
            return 'Rebuild the game to add Xbox trigger vibration support'
        if not active:
            return 'Waiting for driving telemetry · trigger vibration released'
        if self.result is None or now-self.ack_time > 1:
            return 'Waiting for the game’s Xbox trigger output'
        if self.result == -2:
            return 'Update the launcher and rebuild the game to enable Xbox trigger vibration'
        if self.result < 0:
            return 'Xbox trigger output failed · check the controller connection'
        if self.result == 0:
            return 'No Xbox controller with trigger vibration available through the current driver'
        return 'Xbox trigger vibration active · normal game rumble preserved'
