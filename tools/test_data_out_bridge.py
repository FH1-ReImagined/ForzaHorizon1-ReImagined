"""Real native sampler -> UDP -> independent Horizon/FM7 receiver; synthetic memory only."""
import os
import socket
import struct
import subprocess
import sys
import time


def main():
    for form, length, offset, bad_temperature in (
            ('horizon', 324, 244, False), ('fm7', 311, 232, False),
            ('horizon', 324, 244, True), ('fm7', 311, 232, True)):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
            receiver.bind(('127.0.0.1', 0))
            receiver.settimeout(.1)
            env = dict(os.environ, FORZA_DATA_OUT_ENABLE='1', FORZA_DATA_OUT_IP='127.0.0.1',
                       FORZA_DATA_OUT_PORT=str(receiver.getsockname()[1]), FORZA_DATA_OUT_RATE='60',
                       FORZA_DATA_OUT_FORMAT=form)
            for key in ('FORZA_TELEMETRY_PORT', 'FORZA_TELEMETRY_TOKEN', 'FORZA_TELEMETRY_PHYSICS'):
                env.pop(key, None)  # Export must work with the haptics bridge disabled.
            proc = subprocess.Popen([sys.argv[1], '--bad-temperature' if bad_temperature else '--xbox'], env=env)
            frames = []
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    try:
                        frames.append(receiver.recvfrom(4096)[0])
                    except socket.timeout:
                        if proc.poll() is not None: break
                assert proc.wait(timeout=1) == 0
            finally:
                if proc.poll() is None: proc.kill(); proc.wait()
            assert frames and all(len(p) == length for p in frames)
            active = [p for p in frames if struct.unpack_from('<i', p)[0] == 1]
            assert active
            assert any(struct.unpack_from('<i', p)[0] == 0 for p in frames)
            for p in active:
                assert abs(struct.unpack_from('<f', p, 16)[0] - 4774.648) < .1
                assert struct.unpack_from('<4f', p, 84) == (.25, 1.25, 2.25, 3.25)
                assert p[offset + 71:offset + 73] == bytes([128, 128])
                assert p[offset + 75] == 3
                temperatures = struct.unpack_from('<4f', p, offset + 24)
                assert temperatures == ((0, 0, 0, 0) if bad_temperature else (79, 92, 105, 118))
                if not bad_temperature:
                    celsius = tuple((f - 32) * 5 / 9 for f in temperatures)
                    assert all(abs(c - expected) < .0001 for c, expected in
                               zip(celsius, (26.111111, 33.333333, 40.555556, 47.777778)))
            print(form, 'invalid thermals' if bad_temperature else 'Fahrenheit thermals',
                  'native UDP samples', len(frames), 'active', len(active))
    # Off means no socket traffic, even though the native sampler probe runs.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(.5)
        env = dict(os.environ, FORZA_DATA_OUT_ENABLE='0', FORZA_DATA_OUT_IP='127.0.0.1',
                   FORZA_DATA_OUT_PORT=str(receiver.getsockname()[1]))
        subprocess.run([sys.argv[1]], env=env, check=True, timeout=3)
        try:
            receiver.recvfrom(4096)
        except socket.timeout:
            pass
        else:
            raise AssertionError('disabled Data Out sent traffic')
    print('Data Out disabled: no UDP traffic')
    # Enabling external output must not add v2 flags to an old launcher's v1 IPC.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as haptics, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        haptics.bind(('127.0.0.1', 0)); receiver.bind(('127.0.0.1', 0))
        haptics.settimeout(3)
        env = dict(os.environ, FORZA_DATA_OUT_ENABLE='1', FORZA_DATA_OUT_IP='127.0.0.1',
                   FORZA_DATA_OUT_PORT=str(receiver.getsockname()[1]), FORZA_TELEMETRY_TOKEN='0000000000000001',
                   FORZA_TELEMETRY_PORT=str(haptics.getsockname()[1]))
        env.pop('FORZA_TELEMETRY_PHYSICS', None)
        proc = subprocess.Popen([sys.argv[1]], env=env)
        try:
            packet = haptics.recvfrom(4096)[0]
            assert len(packet) == 96 and struct.unpack_from('<H', packet, 4)[0] == 1
            assert struct.unpack_from('<I', packet, 20)[0] & ~15 == 0
            assert proc.wait(timeout=3) == 0
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()
    print('Data Out and legacy FH1T v1 coexist without format/flag leakage')


if __name__ == '__main__': main()
