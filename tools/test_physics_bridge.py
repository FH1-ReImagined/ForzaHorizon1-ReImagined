"""Validate the real native FH1T v2 producer using telemetry_probe.exe (no game)."""
from pathlib import Path
import os
import socket
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from forza_launcher.haptics.telemetry import Decoder


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as data_receiver:
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(3)
        env = dict(os.environ, FORZA_TELEMETRY_PORT=str(receiver.getsockname()[1]),
                   FORZA_TELEMETRY_TOKEN='0000000000000001', FORZA_TELEMETRY_PHYSICS='1')
        if len(sys.argv) > 2:
            data_receiver.bind(('127.0.0.1', 0))
            env.update(FORZA_DATA_OUT_ENABLE='1', FORZA_DATA_OUT_IP='127.0.0.1',
                       FORZA_DATA_OUT_PORT=str(data_receiver.getsockname()[1]))
        proc = subprocess.Popen([sys.argv[1], *sys.argv[2:]], env=env)
        try:
            decoder = Decoder(1)
            frames = [decoder.decode(receiver.recvfrom(4096)[0]) for _ in range(6)]
            assert all(f is not None for f in frames)
            for frame in frames[:3]:
                assert frame.active and frame.physics is not None
                assert abs(frame.physics[0] - 4774.648) < .1
                assert frame.physics[6:10] == (.25, 1.25, 2.25, 3.25)
                assert frame.physics[10] == 1
            assert not frames[3].active
            assert frames[4].physics is None and frames[5].physics is None
            assert proc.wait(timeout=3) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    print('FH1T v2 native UDP, physics, inactive input, remote car and unreadable memory pass')


if __name__ == '__main__':
    main()
