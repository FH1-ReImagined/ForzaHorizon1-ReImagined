#!/usr/bin/env python3
"""Measure what the DualSense triggers are told: the launcher's real haptics service, a virtual
DualSense per launcher version, and the game's telemetry (or a recording of it) on one timeline.

  live    Listen for a game's FH1T telemetry, record it, and forward every packet to one haptics
          service per --launcher, each writing to its own virtual DualSense
          (tools/virtual_dualsense.py). Prints the environment to start the game with.
            tools/trigger_harness.py live --launcher main=/path/to/launcher \\
                --launcher new=launcher --bus bt --log run.jsonl --seconds 900
  replay  Send a recorded drive's packets again, at their pace, to the services. --pedal-scale
          multiplies the trigger positions in every packet (raw LT/RT and the game's processed
          brake/throttle), so two replays show whether the effects follow the trigger.
  report  Numbers from a log: writes and changes a second, force against brake input, the
          effect in each situation (ABS, wheelspin, lock, rough ground, impact, braking,
          menus), how long the effects stay on after a menu opens; --svg draws the timeline.
  compare Whether two logs (two replays) told the triggers the same thing.

The services are the launcher's own HapticsService and DualSense HID writer, unchanged, run from
the given launcher folder (any version), in their own process. Their writer is pointed at the
virtual pad's /dev/hidrawN and nothing else (the user's own controllers are never opened).
Docs: docs/telemetry.md "Measuring the trigger effects".
"""
import argparse
import importlib.util
import json
import os
import secrets
import select
import socket
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import virtual_dualsense as vds  # noqa: E402

DEFAULT_SETTINGS = {'enabled': True}  # the launcher's defaults otherwise


def load_telemetry(launcher):
    spec = importlib.util.spec_from_file_location(
        'fh1_telemetry', os.path.join(launcher, 'forza_launcher', 'haptics', 'telemetry.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summary(frame):
    out = {'seq': frame.sequence, 'ms': frame.sample_ms, 'active': frame.active,
           'flags': frame.flags, 'lt': frame.brake, 'rt': frame.throttle,
           'acc': [round(a, 2) for a in frame.acceleration]}
    if frame.physics is not None:
        p = frame.physics
        out.update(rpm=round(p[0]), redline=round(p[1]), speed=round(p[2], 2),
                   brake=round(p[3], 3), gas=round(p[4], 3),
                   slips=[round(x, 3) for x in p[6:10]], abs=int(p[10]))
    return out


# -- the service process --------------------------------------------------------------------
def run_service(args):
    """One HapticsService from args.launcher, writing to args.hidraw only."""
    sys.path.insert(0, args.launcher)
    from forza_launcher.haptics import service as svc_module
    from forza_launcher.haptics.telemetry import Decoder
    from forza_launcher.haptics.vendor.dualsense import main as ds_main
    info = {'path': args.hidraw.encode(), 'product_id': 0x0CE6, 'usage_page': 1, 'usage': 5,
            'bus_type': 5 if args.bus == 'bt' else 3}
    real = os.path.realpath('/sys/class/hidraw/%s/device' % os.path.basename(args.hidraw))
    if '/virtual/misc/uhid/' not in real:
        sys.exit('refusing %s: not a virtual (uhid) device' % args.hidraw)
    ds_main._find_controller = lambda: info  # this node, never a real pad
    settings = json.loads(args.settings)
    service = svc_module.HapticsService(settings)
    env = service.new_stream()
    token = int(args.token, 16)
    with service._lock:  # the game's (or the recording's) token, not a fresh one
        service._token = token
        service._decoder = Decoder(token)
    service.start()
    print(env['FORZA_TELEMETRY_PORT'], flush=True)
    sys.stdin.read()  # until the harness closes the pipe
    service.stop()
    service.join()


# -- the harness ----------------------------------------------------------------------------
class Timeline:
    def __init__(self, path):
        self.file = open(path, 'w')
        self.lock = threading.Lock()

    def write(self, record):
        with self.lock:
            self.file.write(json.dumps(record, separators=(',', ':')) + '\n')
            self.file.flush()


class Rig:
    """Virtual pads and their services."""

    def __init__(self, launchers, bus, token, timeline, settings):
        self.pads, self.services, self.ports = {}, {}, {}
        self.timeline = timeline
        for label, path in launchers:
            pad = vds.VirtualDualSense(bus, log=lambda r, label=label: timeline.write(
                {'t': r['t'], 'kind': 'out', 'pad': label, 'out': r['out']})).start()
            hidraw = pad.hidraw_path()
            proc = subprocess.Popen(
                [sys.executable, '-I', os.path.abspath(__file__), 'service', '--launcher',
                 os.path.abspath(path), '--hidraw', hidraw, '--bus', bus, '--token',
                 '%016x' % token, '--settings', json.dumps(settings)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.ports[label] = int(proc.stdout.readline())
            self.pads[label], self.services[label] = pad, proc
            timeline.write({'t': time.monotonic(), 'kind': 'pad', 'pad': label, 'hidraw': hidraw,
                            'launcher': os.path.abspath(path), 'bus': bus})
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def forward(self, data, lt=None, rt=None):
        for label, port in self.ports.items():
            self.sock.sendto(data, ('127.0.0.1', port))
            if lt is not None:
                self.pads[label].set_triggers(lt, rt)

    def close(self):
        time.sleep(.5)  # the services release the triggers on their stale timeout
        for proc in self.services.values():
            proc.stdin.close()
        for proc in self.services.values():
            proc.wait(timeout=10)
        time.sleep(.2)
        for pad in self.pads.values():
            pad.close()


def parse_launchers(values):
    out = []
    for v in values:
        label, _, path = v.partition('=')
        out.append((label, path) if path else (os.path.basename(v.rstrip('/')), v))
    return out


def run_live(args):
    launchers = parse_launchers(args.launcher)
    telemetry = load_telemetry(launchers[-1][1])
    token = secrets.randbits(63) | 1
    timeline = Timeline(args.log)
    settings = {**DEFAULT_SETTINGS, **json.loads(args.settings)}
    rig = Rig(launchers, args.bus, token, timeline, settings)
    listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen.bind(('127.0.0.1', args.port))
    env = {'FORZA_TELEMETRY_PORT': str(listen.getsockname()[1]),
           'FORZA_TELEMETRY_TOKEN': '%016x' % token, 'FORZA_TELEMETRY_PHYSICS': '1'}
    print(' '.join('%s=%s' % kv for kv in env.items()), flush=True)
    if args.env_file:
        with open(args.env_file, 'w') as f:
            f.write(''.join('export %s=%s\n' % kv for kv in env.items()))
    decoder = telemetry.Decoder(token)
    end = time.monotonic() + args.seconds if args.seconds else None
    try:
        while end is None or time.monotonic() < end:
            if args.stop_file and os.path.exists(args.stop_file):
                break
            ready, _, _ = select.select([listen], [], [], .2)
            if not ready:
                continue
            data, peer = listen.recvfrom(4096)
            now = time.monotonic()
            frame = decoder.decode(data)
            rig.forward(data, frame.brake if frame else None, frame.throttle if frame else None)
            record = {'t': round(now, 4), 'kind': 'tele', 'hex': data.hex()}
            if frame is not None:
                record['f'] = summary(frame)
            timeline.write(record)
    except KeyboardInterrupt:
        pass
    finally:
        rig.close()


def scaled(data, scale):
    """The packet with every trigger position times `scale` (raw LT/RT, processed brake/gas)."""
    data = bytearray(data)
    for i in (88, 89):
        data[i] = max(0, min(255, int(data[i] * scale + .5)))
    if len(data) >= 140:
        for off in (96 + 12, 96 + 16):
            v = struct.unpack_from('<f', data, off)[0]
            struct.pack_into('<f', data, off, max(0., min(1., v * scale)))
    return bytes(data)


def run_replay(args):
    records = [json.loads(line) for line in open(args.recording)]
    teles = [r for r in records if r['kind'] == 'tele']
    if not teles:
        sys.exit('no telemetry in %s' % args.recording)
    token = struct.unpack_from('<Q', bytes.fromhex(teles[0]['hex']), 8)[0]
    launchers = parse_launchers(args.launcher)
    telemetry = load_telemetry(launchers[-1][1])
    timeline = Timeline(args.log)
    settings = {**DEFAULT_SETTINGS, **json.loads(args.settings)}
    rig = Rig(launchers, args.bus, token, timeline, settings)
    decoder = telemetry.Decoder(token)
    if args.start or args.end:
        teles = [r for r in teles if (not args.start or r['t'] >= args.start) and
                 (not args.end or r['t'] <= args.end)]
    t0, r0 = teles[0]['t'], time.monotonic()
    try:
        for r in teles:
            wait = (r['t'] - t0) / args.speed - (time.monotonic() - r0)
            if wait > 0:
                time.sleep(wait)
            data = bytes.fromhex(r['hex'])
            if args.pedal_scale != 1:
                data = scaled(data, args.pedal_scale)
            frame = decoder.decode(data)
            rig.forward(data, frame.brake if frame else None, frame.throttle if frame else None)
            record = {'t': round(time.monotonic(), 4), 'kind': 'tele', 'hex': data.hex(),
                      'orig_t': r['t']}
            if frame is not None:
                record['f'] = summary(frame)
            timeline.write(record)
    finally:
        rig.close()


# -- a synthetic drive --------------------------------------------------------------------
def packet(token, seq, ms, active=True, lt=0, rt=0, speed=0., brake=0., gas=0., slips=(1, 1, 1, 1),
           abs_on=0, up=0., rpm=3000., redline=7000., accel=0., x=0., y=0.):
    """One FH1T v2 packet (docs/telemetry.md), as the game sends it."""
    wire = struct.Struct('<4sHHQIIQII12fBBBbHH')
    flags = 31 if active else 0
    head = wire.pack(b'FH1T', 2, 140, token, seq, flags, ms, 0x12340000 if active else 0,
                     int(active), x, y, 0, 0, 0, speed, 0, up, accel, 0, 0, 1,
                     lt, rt, 0, 0, 0, 0)
    return head + struct.pack('<10fI', rpm, redline, speed, brake, gas, 0, *slips, abs_on)


def synthetic_drive(token=1):
    """(seconds, packet) of a scripted drive at 60 Hz: a launch with wheelspin, a run up to speed,
    hard braking into ABS with the brake held part way and moving, a rough patch, an impact,
    and a pause menu."""
    out, seq, speed, t, x = [], 0, 0., 0., 0.
    def emit(n, **kw):
        nonlocal seq, t, speed, x
        for i in range(n):
            k = dict(kw)
            for key, v in list(k.items()):
                if callable(v):
                    k[key] = v(i)
            speed = max(0., speed + k.pop('dv', 0.))
            x += speed / 60
            out.append((t, packet(token, seq, int(t * 1000) + 1, speed=speed, x=x, **k)))
            seq += 1
            t += 1 / 60
    emit(60)                                                       # standing, 1 s
    emit(60, rt=255, gas=1., dv=.15, slips=(1, 1, 1.9, 1.9), rpm=6000.)   # launch, spin
    emit(240, rt=200, gas=.8, dv=.12, rpm=lambda i: 3000 + 15 * i)        # up to speed
    emit(30, rt=255, gas=1., rpm=6700.)                            # at the limiter
    emit(150, lt=lambda i: int(255 * i / 149), brake=lambda i: i / 149,
         dv=lambda i: -10 * (i / 149) / 60)                        # a brake sweep, no ABS
    emit(120, rt=200, gas=.8, dv=.15, rpm=5000.)                   # back up to speed
    emit(60, lt=lambda i: 90 + (i % 7) * 4, brake=lambda i: .35 + (i % 7) * .015, dv=-.08)
    emit(60, lt=255, brake=1., dv=-.17, abs_on=lambda i: int(i % 4 < 2),
         slips=(.6, .6, .7, .7))                                   # ABS
    emit(120, rt=120, gas=.5, y=lambda i: (.05, -.03, .01)[i % 3])   # rough ground
    emit(3, dv=-3.)                                                # impact
    emit(60, rt=120, gas=.5)
    emit(60, active=False)                                         # pause menu
    return out


def run_synthetic(args):
    timeline = Timeline(args.out)
    for t, data in synthetic_drive(int(args.token, 16)):
        timeline.write({'t': round(t, 4), 'kind': 'tele', 'hex': data.hex()})


# -- reports --------------------------------------------------------------------------------
def effect_of(side):
    """A decoded trigger effect as (kind, force or amplitude, frequency)."""
    if not side or side['mode'] == 'off':
        return ('off', 0, 0)
    if side['mode'] == 'feedback':
        return ('resist', side['force'], 0)
    if side['mode'] == 'vibration':
        return ('vibrate', side['amplitude'], side.get('frequency', 0))
    return (side['mode'], side.get('force', side.get('amplitude', 0)), side.get('frequency', 0))


def situation(f, prev_speed):
    if not f.get('active'):
        return 'menu or not driving'
    if 'speed' not in f:
        return 'driving (no physics)'
    slips, speed = f['slips'], abs(f['speed'])
    decel = f.get('_decel', 0)
    if decel > 25 and prev_speed > 5:
        return 'impact'
    if f['abs'] and speed > 2:
        return 'ABS'
    if speed > 2 and -min(slips) - 1 > .1:
        return 'wheel lock'
    if speed > .5 and max(slips) - 1 > .1:
        return 'wheelspin'
    if f.get('_shake', 0) > 3 + .06 * speed:
        return 'rough ground'
    if f.get('brake', 0) > .05 and speed > 1:
        return 'braking'
    return 'driving'


def load(path):
    records = [json.loads(line) for line in open(path)]
    teles = [r for r in records if r['kind'] == 'tele' and 'f' in r]
    outs = {}
    for r in records:
        if r['kind'] == 'out' and r['out'] and ('left' in r['out'] or 'right' in r['out']):
            outs.setdefault(r['pad'], []).append(r)
    # Deceleration over 200 ms of the game's clock and the vertical shaking (from the car's
    # height, packets that moved the car only, as feel.py does), for the situations.
    hist, last, climb, avg, shake2 = [], None, None, 0., 0.
    for r in teles:
        f = r['f']
        if 'speed' in f:
            hist.append((f['ms'], abs(f['speed'])))
            while len(hist) > 2 and f['ms'] - hist[1][0] >= 200:
                hist.pop(0)
            dt = f['ms'] - hist[0][0]
            f['_decel'] = (hist[0][1] - abs(f['speed'])) * 1000 / dt if dt >= 50 else 0
        pos = struct.unpack_from('<3f', bytes.fromhex(r['hex']), 40)
        if last is not None and pos != last[1] and 0 < f['ms'] - last[0] < 100 and \
                abs(pos[1] - last[1][1]) < 2:
            c = (pos[1] - last[1][1]) * 1000 / (f['ms'] - last[0])
            if climb is not None:
                up = (c - climb) * 1000 / (f['ms'] - last[0])
                avg = avg * .85 + up * .15
                shake2 = shake2 * .9 + (up - avg) ** 2 * .1
            climb = c
        elif last is not None and pos != last[1]:
            climb, shake2 = None, 0.
        if last is None or pos != last[1]:
            last = (f['ms'], pos)
        f['_shake'] = shake2 ** .5
    return teles, outs


def state_at(outs, t):
    """The effects in force at time t: the last report that set each trigger."""
    left = right = ('off', 0, 0)
    for r in outs:
        if r['t'] > t:
            break
        if 'left' in r['out']:
            left = effect_of(r['out']['left'])
        if 'right' in r['out']:
            right = effect_of(r['out']['right'])
    return left, right


class States:
    """state_at for increasing times, in one pass."""
    def __init__(self, outs):
        self.outs, self.i, self.left, self.right = outs, 0, ('off', 0, 0), ('off', 0, 0)

    def at(self, t):
        while self.i < len(self.outs) and self.outs[self.i]['t'] <= t:
            o = self.outs[self.i]['out']
            if 'left' in o:
                self.left = effect_of(o['left'])
            if 'right' in o:
                self.right = effect_of(o['right'])
            self.i += 1
        return self.left, self.right


def fmt_effect(counts):
    total = sum(counts.values()) or 1
    parts = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
    return ', '.join('%s %d%%' % (('%s %d' % (k[0], k[1]) + (' @%d Hz' % k[2] if k[2] else ''))
                                  if k[0] != 'off' else 'off', round(100 * n / total))
                     for k, n in parts)


def frange(a, b, step):
    while a < b:
        yield a
        a += step


def marker_spans(path, word, teles=()):
    """(time, name, next marker's time) of the markers whose name contains `word`. In a replay
    the markers' times (of the recording) move to the replay's clock."""
    if not path:
        return []
    marks = [(float(t), n) for t, n in (line.split(None, 1) for line in open(path) if line.strip())]
    if teles and 'orig_t' in teles[0]:
        def moved(t):
            for r in teles:
                if r['orig_t'] >= t:
                    return r['t'] - (r['orig_t'] - t)
            return teles[-1]['t']
        marks = [(moved(t), n) for t, n in marks]
    return [(t, n.strip(), marks[i + 1][0] if i + 1 < len(marks) else t + 10)
            for i, (t, n) in enumerate(marks) if word in n]


def report(args):
    teles, outs = load(args.log)
    if not teles:
        sys.exit('no telemetry')
    span = teles[-1]['t'] - teles[0]['t']
    active = [r for r in teles if r['f']['active']]
    lines = ['Log %s: %.0f s, %d telemetry frames (%.0f a second), %d of them driving' % (
        args.log, span, len(teles), len(teles) / max(span, 1e-9), len(active))]
    for pad, rows in sorted(outs.items()):
        lines.append('')
        lines.append('== %s ==' % pad)
        writes = len(rows)
        per_s = {}
        for r in rows:
            per_s[int(r['t'])] = per_s.get(int(r['t']), 0) + 1
        changes, last, gaps = 0, None, []
        for r in rows:
            key = (r['out'].get('left'), r['out'].get('right'))
            key = json.dumps(key, sort_keys=True)
            if key != last:
                changes += 1
                last = key
        times = [r['t'] for r in rows]
        gaps = [b - a for a, b in zip(times, times[1:])]
        lines.append('trigger reports: %d (%.1f a second on average, at most %d in one second); '
                     'shortest gap %.0f ms' % (writes, writes / max(span, 1e-9),
                                               max(per_s.values()), 1000 * min(gaps) if gaps else 0))
        lines.append('reports that changed the effect: %d (%.1f a second)' % (
            changes, changes / max(span, 1e-9)))
        # situations
        states = States(rows)
        by_sit, prev_speed = {}, 0
        brake_rows = []
        for r in teles:
            f = r['f']
            sit = situation(f, prev_speed)
            prev_speed = abs(f.get('speed', 0))
            left, right = states.at(r['t'] + args.lag)
            d = by_sit.setdefault(sit, [{}, {}, 0])
            d[0][left] = d[0].get(left, 0) + 1
            d[1][right] = d[1].get(right, 0) + 1
            d[2] += 1
            if f['active'] and 'brake' in f and abs(f.get('speed', 0)) > 1:
                brake_rows.append((f['brake'], f.get('abs', 0), f.get('_decel', 0), left))
        lines.append('%-22s %6s  %-40s %s' % ('situation', 'frames', 'brake L2 (share of time)',
                                             'throttle R2'))
        for sit, (lc, rc, n) in sorted(by_sit.items(), key=lambda kv: -kv[1][2]):
            lines.append('%-22s %6d  %-40s %s' % (sit, n, fmt_effect(lc), fmt_effect(rc)))
        # force against brake input, without and with ABS
        lines.append('brake input -> L2 (no ABS | ABS): mean force or amplitude, mean decel')
        for lo in (0, .05, .25, .5, .75, .95):
            hi = {0: .05, .05: .25, .25: .5, .5: .75, .75: .95, .95: 1.01}[lo]
            cells = []
            for abs_on in (0, 1):
                sel = [x for x in brake_rows if lo <= x[0] < hi and bool(x[1]) == bool(abs_on)]
                if sel:
                    kinds = {}
                    for x in sel:
                        kinds[x[3][0]] = kinds.get(x[3][0], 0) + 1
                    kind = max(kinds, key=kinds.get)
                    cells.append('%4d fr  %-8s %.1f  decel %4.1f' % (
                        len(sel), kind, sum(x[3][1] for x in sel) / len(sel),
                        sum(x[2] for x in sel) / len(sel)))
                else:
                    cells.append('%-34s' % '      -')
            lines.append('  brake %.2f-%.2f  %s | %s' % (lo, min(hi, 1), cells[0], cells[1]))
        lines.append('deceleration -> L2 while braking without ABS (brake input over 0.05, moving)')
        for lo, hi in ((0, 2), (2, 4), (4, 6), (6, 8), (8, 10), (10, 99)):
            sel = [x for x in brake_rows if x[0] > .05 and not x[1] and lo <= x[2] < hi]
            if sel:
                kinds = {}
                for x in sel:
                    kinds[x[3][0]] = kinds.get(x[3][0], 0) + 1
                lines.append('  %2d-%-2s m/s2  %4d frames  %-8s mean %.1f' % (
                    lo, hi if hi < 99 else '', len(sel), max(kinds, key=kinds.get),
                    sum(x[3][1] for x in sel) / len(sel)))
        # menus: from the frame that turned inactive to both triggers off
        delays, prev = [], None
        for r in teles:
            a = r['f']['active']
            if prev and not a:
                t0 = r['t']
                st = state_at(rows, t0)
                if st == (('off', 0, 0), ('off', 0, 0)):
                    delays.append(0.)
                else:
                    later = [o['t'] for o in rows if o['t'] >= t0 and
                             effect_of(o['out'].get('left')) == ('off', 0, 0) and
                             effect_of(o['out'].get('right')) == ('off', 0, 0)]
                    delays.append(later[0] - t0 if later else float('inf'))
            prev = a
        if delays:
            lines.append('telemetry active -> inactive %d times: both triggers off within %s s' % (
                len(delays), ', '.join('%.2f' % d for d in delays[:12])))
        # menus from the markers: the moment START was pressed to both triggers off, and what
        # the triggers did while the menu was open
        for t_mark, name, t_end in marker_spans(args.markers, 'pause', teles):
            st = state_at(rows, t_mark)
            if st == (('off', 0, 0), ('off', 0, 0)):
                off_at = 0.
            else:
                later = [o['t'] for o in rows if t_mark <= o['t'] <= t_end and
                         effect_of(o['out'].get('left')) == ('off', 0, 0) and
                         effect_of(o['out'].get('right')) == ('off', 0, 0)]
                off_at = later[0] - t_mark if later else None
            seen = {state_at(rows, t) for t in frange(t_mark + (off_at or 0) + .05, t_end, .25)}
            lines.append('%s: both triggers off %s after START; then %s' % (
                name, '%.2f s' % off_at if off_at is not None else 'NOT within %.1f s' % (
                    t_end - t_mark), ', '.join(sorted('%s/%s' % (l[0], r[0]) for l, r in seen))))
    text = '\n'.join(lines)
    print(text)
    if args.out:
        open(args.out, 'w').write(text + '\n')
    if args.svg:
        svg(args.svg, teles, outs, args.lag)
    if args.svg_force:
        svg_force(args.svg_force, teles, outs, args.lag)


def svg_force(path, teles, outs, lag):
    """Each pad's L2 against the game's processed brake input: resistance (blue) or vibration
    amplitude (magenta, ABS frames red), one panel per pad."""
    pads = sorted(outs)
    W, H, P = 360, 300, 50
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="sans-serif"'
           ' font-size="11"><rect width="100%%" height="100%%" fill="white"/>' % (W * len(pads), H)]
    for j, pad in enumerate(pads):
        ox = j * W
        out.append('<text x="%d" y="16" font-weight="bold">%s: L2 against brake input</text>' % (ox + P, pad))
        out.append('<rect x="%d" y="30" width="%d" height="%d" fill="none" stroke="#999"/>' % (
            ox + P, W - P - 10, H - 80))
        for v in range(0, 9, 2):
            y = 30 + (H - 80) * (1 - v / 8)
            out.append('<text x="%d" y="%.0f" text-anchor="end">%d</text>' % (ox + P - 4, y + 4, v))
        for b in (0, .5, 1):
            x = ox + P + (W - P - 10) * b
            out.append('<text x="%.0f" y="%d" text-anchor="middle">%.1f</text>' % (x, H - 36, b))
        out.append('<text x="%d" y="%d">brake input (game, 0-1); force / amplitude 0-8</text>' % (
            ox + P, H - 18))
        states = States(outs[pad])
        seen = set()
        for r in teles:
            f = r['f']
            if not f['active'] or 'brake' not in f or abs(f.get('speed', 0)) < 1:
                continue
            left = states.at(r['t'] + lag)[0]
            key = (round(f['brake'], 2), left, f.get('abs', 0))
            if key in seen:
                continue
            seen.add(key)
            x = ox + P + (W - P - 10) * f['brake']
            y = 30 + (H - 80) * (1 - left[1] / 8)
            color = '#c00' if f.get('abs') else ('#00c' if left[0] == 'resist' else
                                                  '#c0c' if left[0] == 'vibrate' else '#888')
            out.append('<circle cx="%.1f" cy="%.1f" r="2.5" fill="%s" opacity=".6"/>' % (x, y, color))
    out.append('</svg>')
    open(path, 'w').write('\n'.join(out))


def svg(path, teles, outs, lag):
    """Timeline: speed, brake and throttle input, ABS and slips, and each pad's L2/R2 effect."""
    t0, t1 = teles[0]['t'], teles[-1]['t']
    W, row = 1600, 90
    pads = sorted(outs)
    rows = ['speed (m/s, 0-60)', 'brake / throttle input', 'ABS, lock, spin']
    rows += ['%s L2' % p for p in pads] + ['%s R2' % p for p in pads]
    H = row * len(rows) + 40
    x = lambda t: 150 + (t - t0) / max(t1 - t0, 1e-9) * (W - 170)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="sans-serif"'
           ' font-size="11"><rect width="100%%" height="100%%" fill="white"/>' % (W, H)]
    for i, name in enumerate(rows):
        y = 20 + i * row
        out.append('<text x="4" y="%d">%s</text>' % (y + row // 2, name))
        out.append('<line x1="150" x2="%d" y1="%d" y2="%d" stroke="#ccc"/>' % (W - 20, y + row - 10,
                                                                                y + row - 10))
    def poly(points, color, i, top):
        y = lambda v: 20 + i * row + (row - 10) - min(1, max(0, v / top)) * (row - 20)
        pts = ' '.join('%.1f,%.1f' % (x(t), y(v)) for t, v in points)
        out.append('<polyline fill="none" stroke="%s" stroke-width="1" points="%s"/>' % (color, pts))
    f = [(r['t'], r['f']) for r in teles]
    poly([(t, abs(v.get('speed', 0))) for t, v in f], '#333', 0, 60)
    poly([(t, v.get('brake', v['lt'] / 255)) for t, v in f], '#c00', 1, 1)
    poly([(t, v.get('gas', v['rt'] / 255)) for t, v in f], '#080', 1, 1)
    poly([(t, .9 if v.get('abs') else 0) for t, v in f], '#c00', 2, 1)
    poly([(t, min(1, max(0, -min(v.get('slips', [0])) - 1))) for t, v in f], '#f80', 2, 1)
    poly([(t, min(1, max(0, max(v.get('slips', [0])) - 1))) for t, v in f], '#080', 2, 1)
    for t, v in f:
        if not v['active']:
            out.append('<rect x="%.1f" y="20" width="1" height="%d" fill="#88f" opacity=".15"/>'
                       % (x(t), H - 40))
    for j, pad in enumerate(pads):
        for side, base in ((0, 3), (1, 3 + len(pads))):
            states, pts_f, pts_v = States(outs[pad]), [], []
            for t, _ in f:
                eff = states.at(t + lag)[side]
                pts_f.append((t, eff[1] if eff[0] == 'resist' else 0))
                pts_v.append((t, eff[1] if eff[0] == 'vibrate' else 0))
            poly(pts_f, '#00c', base + j, 8)
            poly(pts_v, '#c0c', base + j, 8)
    out.append('<text x="150" y="%d">time %.0f s; blue: resistance (0-8), magenta: vibration '
               'amplitude (0-8), shaded: not driving; red brake/ABS, green throttle/spin, orange '
               'lock</text>' % (H - 8, t1 - t0))
    out.append('</svg>')
    open(path, 'w').write('\n'.join(out))


def compare(args):
    a, b = load(args.a)[1], load(args.b)[1]
    for pad in sorted(set(a) | set(b)):
        ea = [(r['out'].get('left'), r['out'].get('right')) for r in a.get(pad, [])]
        eb = [(r['out'].get('left'), r['out'].get('right')) for r in b.get(pad, [])]
        same = ea == eb
        print('%s: %d and %d trigger reports, %s' % (
            pad, len(ea), len(eb), 'identical' if same else 'DIFFERENT'))
        if not same:
            for i, (x, y) in enumerate(zip(ea, eb)):
                if x != y:
                    print('  first difference at report %d: %s vs %s' % (i, x, y))
                    break


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('service')
    s.add_argument('--launcher', required=True)
    s.add_argument('--hidraw', required=True)
    s.add_argument('--bus', default='bt')
    s.add_argument('--token', required=True)
    s.add_argument('--settings', default='{}')
    for name in ('live', 'replay'):
        p = sub.add_parser(name)
        p.add_argument('--launcher', action='append', required=True,
                       help='LABEL=DIR of a launcher folder (repeatable)')
        p.add_argument('--bus', choices=('usb', 'bt'), default='bt')
        p.add_argument('--log', required=True)
        p.add_argument('--settings', default='{}', help='haptics settings JSON (enabled=true)')
    live = sub.choices['live']
    live.add_argument('--port', type=int, default=0)
    live.add_argument('--seconds', type=float, default=0)
    live.add_argument('--env-file')
    live.add_argument('--stop-file')
    rp = sub.choices['replay']
    rp.add_argument('recording')
    rp.add_argument('--pedal-scale', type=float, default=1.)
    rp.add_argument('--speed', type=float, default=1.)
    rp.add_argument('--start', type=float, default=0, help='first recording time (its t)')
    rp.add_argument('--end', type=float, default=0)
    r = sub.add_parser('report')
    r.add_argument('log')
    r.add_argument('--svg', help='timeline')
    r.add_argument('--svg-force', help='L2 against brake input')
    r.add_argument('--out')
    r.add_argument('--markers', help='file of "monotonic-time name" lines (pause* = START pressed)')
    r.add_argument('--lag', type=float, default=.1,
                   help='seconds after a frame its effect counts as reached (default 0.1)')
    y = sub.add_parser('synthetic', help='write a scripted drive as a recording for replay')
    y.add_argument('out')
    y.add_argument('--token', default='0000000000000001')
    c = sub.add_parser('compare')
    c.add_argument('a')
    c.add_argument('b')
    args = ap.parse_args()
    {'service': run_service, 'live': run_live, 'replay': run_replay, 'report': report,
     'compare': compare, 'synthetic': run_synthetic}[args.cmd](args)


if __name__ == '__main__':
    main()
