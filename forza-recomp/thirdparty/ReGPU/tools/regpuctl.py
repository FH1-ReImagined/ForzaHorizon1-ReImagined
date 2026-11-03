"""Drives the running game through its debug server (src/debug.cpp), so a test doesn't need a restart.

  regpuctl.py start [NAME=VALUE ...]   start the game ($REGPU_GAME) with REGPU_DEBUG=1 (and those variables), wait for it
  regpuctl.py <command> [args]         one command; prints the reply
  regpuctl.py run <file>               commands from a file, one per line (# comments); stops at the first error
  regpuctl.py run -                    the same from stdin

Commands (sent as typed):
  ping | status                      alive? / frame, fps
  press A [ms] | press A+B 200       tap buttons (default 120 ms): A B X Y START BACK UP DOWN LEFT RIGHT LB RB LS RS
  hold A | release A | release all   hold until released (release all also stops sticks and triggers)
  stick L|R x y [ms]                 x, y in -1..1; without ms it stays until "stick L 0 0" or "release all"
  trigger L|R v [ms]                 v in 0..1
  wait 30 | wait 2.5s                frames, or seconds
  shot [path]                        the next frame as a PNG (default out/live.png)
  capture                            what F12 does (trace the next frame into out/capture)
  rdcapture                          a RenderDoc capture of the next frame (started under RenderDoc: REGPU_WRAP)
  shots N DIR                        N consecutive frames as DIR/s0.png ... (flicker between frames)
  output W H                         the drawing resolution while the game runs
  log [n]                            the last n lines of the log
  peek ADDR [bytes] | poke ADDR HEX  guest memory (hex addresses, big-endian bytes)
  ps HASH EXPR | ps HASH tint R,G,B  that pixel shader returns EXPR (HLSL over r[], input.o[], ...) or a flat
                                     colour, live; "ps HASH" alone goes back to the original
  vs HASH STATEMENT                  that vertex shader also runs STATEMENT at its end (e.g. o[0].zw = r[4].xy;),
                                     to read values with a ps edit; "vs HASH" alone goes back
  hlsl HASH [path]                   save a shader's translated HLSL (default out/hlsl/HASH.hlsl)
  switches                           the on/off REGPU_* switches reached so far, and their state
  switch REGPU_NAME 0|1              flip one live (A/B tests without a restart; one not reached yet is
                                     kept for when it is)
  env NAME [value]                   set an environment variable inside the game (switches with a value,
                                     which are mostly read once at their first use)
  quit                               close the game
Port: REGPU_DEBUG_PORT (default 7341).
"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get('REGPU_DEBUG_PORT', '7341'))


def connect(timeout=5.0):
    s = socket.create_connection(('127.0.0.1', PORT), timeout=timeout)
    s.settimeout(None)
    return s


def send(s, line):
    s.sendall((line.strip() + '\n').encode())
    data = b''
    while not data.endswith(b'\n'):
        chunk = s.recv(65536)
        if not chunk:
            raise ConnectionError('the game closed the connection')
        data += chunk
    return json.loads(data)


def show(reply):
    if reply.get('ok') and 'lines' in reply:
        print('\n'.join(reply['lines']))
    else:
        print(json.dumps(reply))


def start(assignments):
    env = dict(os.environ, REGPU_DEBUG='1')
    for a in assignments:
        k, _, v = a.partition('=')
        env[k] = v
    os.makedirs(os.path.join(ROOT, 'out'), exist_ok=True)
    log = open(os.path.join(ROOT, 'out', 'live.log'), 'w')
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
    game = os.environ.get('REGPU_GAME')
    if not game:
        print(json.dumps({'ok': False, 'error': 'set REGPU_GAME to the game executable'}))
        return 1
    # REGPU_GAME_ARGS: its command-line arguments; REGPU_GAME_CWD: where to run it (default: beside it).
    args = os.environ.get('REGPU_GAME_ARGS', '').split()
    cwd = os.environ.get('REGPU_GAME_CWD') or os.path.dirname(os.path.abspath(game))
    # REGPU_WRAP: a launcher to start the game through, its arguments '|'-separated (RenderDoc:
    # renderdoccmd|capture|-w; then "rdcapture" takes a capture).
    wrap = [w for w in os.environ.get('REGPU_WRAP', '').split('|') if w]
    p = subprocess.Popen(wrap + [os.path.abspath(game)] + args, cwd=cwd, env=env, stdout=log, stderr=log,
                         creationflags=flags)
    for _ in range(120):
        time.sleep(0.5)
        if p.poll() is not None:
            print(json.dumps({'ok': False, 'error': 'the game exited (%s); see out/live.log' % p.returncode}))
            return 1
        try:
            with connect(1) as s:
                print(json.dumps(dict(send(s, 'status'), pid=p.pid)))
                return 0
        except OSError:
            pass
    print(json.dumps({'ok': False, 'error': 'no debug server after 60 s; see out/live.log'}))
    return 1


def main(argv):
    if len(argv) < 2 or argv[1] in ('-h', '--help'):
        print(__doc__)
        return 2
    if argv[1] == 'start':
        return start(argv[2:])
    try:
        s = connect()
    except OSError:
        print(json.dumps({'ok': False, 'error': 'the game is not running with REGPU_DEBUG=1 (regpuctl.py start)'}))
        return 1
    with s:
        if argv[1] == 'run':
            lines = sys.stdin.read().splitlines() if argv[2] == '-' else open(argv[2]).read().splitlines()
            for line in lines:
                line = line.split('#', 1)[0].strip()
                if not line:
                    continue
                reply = send(s, line)
                print('>', line)
                show(reply)
                if not reply.get('ok'):
                    return 1
            return 0
        reply = send(s, ' '.join(argv[1:]))
        show(reply)
        return 0 if reply.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
