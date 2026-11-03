#!/usr/bin/env python3
"""render_test.py: scripted render regression check (#507, docs/render-tests.md).

Plays the scenes of tools/render_tests/scenes.toml in the TU4 game (scripted pad input through
the FIFO and screenshots through the trigger file, as tools/live_session.sh and live_shot.sh),
holds the time of day by writing guest memory, and compares each capture with stored baselines
using a block SSIM (structure and brightness) plus a chroma check, under per-scene tolerances.

  render_test.py record [--runs 3] [--save DIR --xuid XUID]   store baselines (and their noise)
  render_test.py check  [-- extra game flags]                  compare with the baselines
  render_test.py probe  [--session NAME] [--save DIR --xuid X]  play a session, keep every shot
  render_test.py compare A.png B.png [--scene NAME]            compare two images

Baselines live outside the repository (default ~/.local/share/forza-render-baselines, or
FORZA_RENDER_BASELINES): never commit captures, they are game images. Run folders, the game's
shader caches and the reports go to ~/.cache/forza-render-tests (FORZA_RENDER_WORK).

The idea of fixed scenes with image baselines and a tolerance follows Pinyon Shift's
tools/run-fh1-render-test.py (BSD-3-Clause, LICENSE-Pinyon-Shift); the code is our own.
"""
import argparse
import errno
import hashlib
import html
import json
import math
import os
import shutil
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import tomllib
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageMath, ImageStat
except ImportError:
    sys.exit("render_test.py needs Pillow (python3-pillow / pip install pillow)")

ROOT = Path(__file__).resolve().parent.parent
SCENES = ROOT / "tools" / "render_tests" / "scenes.toml"
HOME = Path.home()
DEFAULT_BASELINES = Path(os.environ.get("FORZA_RENDER_BASELINES")
                         or HOME / ".local/share/forza-render-baselines")
DEFAULT_WORK = Path(os.environ.get("FORZA_RENDER_WORK")
                    or Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / "forza-render-tests")
PROFILE_DIR = "B13EBABEBABEBABE"  # the runtime's local profile folder (tools/play.sh)
TITLE = "4D5309C9"
WIDTH, HEIGHT = 1280, 720         # captures are the guest output at resolution_scale 1


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, file=sys.stderr, flush=True)


class Hang(Exception):
    """The game stopped presenting, exited, or stopped reading input: kill it, retry once."""


OWNER = ".forza-render-test.json"


def is_reparse(path):
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0)
                                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except FileNotFoundError:
        return False


def plain_path(path):
    """Reject aliases before resolving a path used for copying or deleting data."""
    path = Path(path).expanduser().absolute()
    if any(is_reparse(p) for p in (path, *path.parents)):
        raise ValueError(f"refusing a symlink or reparse path: {path}")
    return path.resolve()


def owned(path, kind):
    if is_reparse(path) or is_reparse(path / OWNER):
        return False
    try:
        return json.loads((path / OWNER).read_text()) == {
            "tool": "forza-render-test", "version": 1, "kind": kind}
    except (OSError, ValueError):
        return False


def mark_owned(path, kind):
    (path / OWNER).write_text(json.dumps({"tool": "forza-render-test", "version": 1,
                                         "kind": kind}))


def remove_owned(path, kind, parent):
    path, parent = plain_path(path), plain_path(parent)
    if path.parent != parent or not owned(path, kind):
        raise ValueError(f"refusing to replace an unowned {kind} directory: {path}")
    if any(is_reparse(p) for p in path.rglob("*")):
        raise ValueError(f"refusing to remove a tree containing aliases: {path}")
    shutil.rmtree(path)


def disjoint(a, b):
    a, b = plain_path(a), plain_path(b)
    if a.is_relative_to(b) or b.is_relative_to(a):
        raise ValueError(f"paths must not overlap: {a} and {b}")


def profile_source(path):
    source = plain_path(path)
    if source.name != TITLE:
        source = plain_path(source / TITLE)
    if not (source / "00000001").is_dir():
        raise ValueError(f"{source} is not a Forza Horizon save folder ({TITLE}/00000001)")
    if any(is_reparse(p) for p in source.rglob("*")):
        raise ValueError(f"refusing a save containing symlinks or reparse points: {source}")
    return source


def output_tree(path):
    path = plain_path(path)
    if path.exists() and any(is_reparse(p) for p in path.rglob("*")):
        raise ValueError(f"refusing an output tree containing aliases: {path}")
    return path


# ---------------------------------------------------------------------------------------------
# Guest memory: the time of day (Title Update 4 addresses)

class Guest:
    BASE = 0x100000000  # guest address 0 in the host process (tools/carlog.py)

    def __init__(self, pid):
        self.f = open(f"/proc/{pid}/mem", "r+b", buffering=0)

    def read(self, addr, n):
        self.f.seek(self.BASE + addr)
        return self.f.read(n)

    def u32(self, addr):
        return struct.unpack(">I", self.read(addr, 4))[0]

    def f32(self, addr):
        return struct.unpack(">f", self.read(addr, 4))[0]

    def put_f32(self, addr, value):
        self.f.seek(self.BASE + addr)
        if self.f.write(struct.pack(">f", value)) != 4:
            raise OSError("short guest clock write")

    def close(self):
        self.f.close()


def ok_ptr(p):
    return 0x100000 <= p < 0x80000000 and not p & 3


def clock_address(g):
    """The time of day (float seconds after midnight) in free roam and races, or None.

    TU4: the world is *(*(*0x833CAAE4 + 4) + 4) (the car list's owner, tools/carlog.py); its
    time-of-day object is *(world + 232), which points back at the world in its first word;
    the clock is at +10488 and the game's rate (game seconds per second) at +10492. The game
    advances the clock every frame (CSetTimeOfDay/CLockTimeOfDay in its flow scripts), so a
    held time is written again every few milliseconds.
    """
    try:
        root = g.u32(0x833CAAE4)
        holder = g.u32(root + 4) if ok_ptr(root) else 0
        world = g.u32(holder + 4) if ok_ptr(holder) else 0
        tod = g.u32(world + 232) if ok_ptr(world) else 0
        if not ok_ptr(tod) or g.u32(tod) != world:
            return None
        addr = tod + 10488
        value = g.f32(addr)
        return addr if 0.0 <= value <= 86400.0 else None
    except (OSError, struct.error):
        return None


class ClockHold(threading.Thread):
    """Writes the held time of day about every 15 ms (once a frame at 60 fps) while a target
    is set. Between two writes the game advances it by less than a game second."""

    def __init__(self, pid):
        super().__init__(daemon=True)
        self.g = Guest(pid)
        self.target = None
        self.addr = None
        self.error = None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.trace = None  # (file, t0): "trace" steps log the clock every 20 ms

    def set(self, seconds):
        if seconds is not None and (not math.isfinite(seconds) or not 0 <= seconds <= 86400):
            raise ValueError("clock target must be finite seconds between 0 and 86400")
        with self.lock:
            self.target = seconds
            self.addr = None

    def run(self):
        last_trace = 0.0
        try:
            while not self.stop.is_set():
                with self.lock:
                    target = self.target
                    if self.trace and time.monotonic() - last_trace >= 0.02:
                        last_trace = time.monotonic()
                        a = clock_address(self.g)
                        try:
                            v = self.g.f32(a) if a is not None else -1.0
                        except (OSError, struct.error):
                            v = -1.0
                        self.trace[0].write(f"{last_trace - self.trace[1]:.3f} {v:.2f}\n")
                    # Best-effort validation, not a native object lifetime lock: the game can
                    # still free this object between the pointer reads and the write.
                    self.addr = clock_address(self.g) if target is not None else None
                    if self.addr is not None:
                        self.g.put_f32(self.addr, target)
                self.stop.wait(0.015 if target is not None else 0.005 if self.trace else 0.05)
        except Exception as e:
            self.error = e
        finally:
            self.addr = None
            try:
                self.g.close()
            except Exception as e:
                self.error = self.error or e


# ---------------------------------------------------------------------------------------------
# One game session

def bench_lock_wait(max_wait):
    """Honour tools/bench_lock.sh: wait while someone else holds an unexpired BENCH_LOCK."""
    lock = Path(os.environ.get("BENCH_LOCK") or Path(os.environ.get("TMPDIR") or "/tmp") / "BENCH_LOCK")
    if os.environ.get("FORZA_IGNORE_LOCK") == "1":
        return
    waited, shown = 0, None
    while True:
        try:
            owner, expiry, *purpose = lock.read_text().split(maxsplit=2) + ["", ""]
            held = owner != os.environ.get("BENCH_OWNER", "forza") and int(expiry) > time.time()
        except (OSError, ValueError):
            held = False
        if not held:
            return
        if shown != (owner, expiry):
            shown = (owner, expiry)
            log(f"benchmark lock held by {owner} until {time.strftime('%H:%M:%S', time.localtime(int(expiry)))}"
                f" ({' '.join(purpose).strip()}): waiting")
        if waited >= max_wait:
            sys.exit("benchmark lock still held; giving up (--lock-wait)")
        time.sleep(30)
        waited += 30


def default_sdk():
    for d in ("rexglue-sdk-patched-forza", "rexglue-sdk-patched", "rexglue-sdk-nightly"):
        p = HOME / d / "0.10.0.24/linux-amd64"
        if (p / "lib").is_dir():
            return p
    return HOME / "rexglue-sdk-patched-forza/0.10.0.24/linux-amd64"


class Session:
    def __init__(self, cfg, args, run_dir, save_dir, xuid):
        self.cfg, self.args, self.dir = cfg, args, run_dir
        self.save_dir, self.xuid = save_dir, xuid
        self.proc = None
        self.clock = None
        self.shm = None
        self.frames_size = 0
        self.frames_at = time.monotonic()
        self.shots = {}
        self.burst = int(cfg["game"].get("burst", 1))
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.converting = []
        self.created = False

    # -- start / stop
    def start(self):
        work = output_tree(self.args.work)
        d = plain_path(self.dir)
        runs = plain_path(work / "runs")
        if d.parent != runs:
            raise ValueError(f"run must be a direct child of {runs}")
        source = profile_source(self.save_dir)
        disjoint(source, self.args.work)
        runs.mkdir(parents=True, exist_ok=True)
        d.mkdir()  # Exclusive: even a same-name unfinished run must never be replaced.
        self.dir, self.created = d, True
        mark_owned(d, "run-active")
        avail = int(next(l.split()[1] for l in open("/proc/meminfo") if l.startswith("MemAvailable"))) // 1048576
        if os.environ.get("FORZA_IGNORE_MEM") != "1" and avail < 10:
            raise Hang(f"only {avail} GB of memory available; not starting (FORZA_IGNORE_MEM=1 overrides)")
        ud = d / "ud"
        shutil.copytree(source, ud / PROFILE_DIR / TITLE)
        for sub in ("config", "data", "state"):
            (d / "xdg" / sub).mkdir(parents=True)
        (work / "cache").mkdir(parents=True, exist_ok=True)
        (work / "xdg-cache").mkdir(parents=True, exist_ok=True)
        os.mkfifo(d / "in.fifo")
        env = {k: v for k, v in os.environ.items()
               if k not in ("FORZA_SERVER", "FORZA_NET", "FORZA_AUTOPRESS", "FORZA_SCREENSHOT_BURST")}
        env.update({
            # The player's own settings, saves and caches stay out of the run.
            "XDG_CONFIG_HOME": str(d / "xdg/config"), "XDG_DATA_HOME": str(d / "xdg/data"),
            "XDG_STATE_HOME": str(d / "xdg/state"), "XDG_CACHE_HOME": str(work / "xdg-cache"),
            "FORZA_XUID": self.xuid, "REX_AUDIO_FAIL_OPEN": "all",
            "SDL_GAMECONTROLLER_IGNORE_DEVICES_EXCEPT": "0x0000/0x0000",
            "SDL_JOYSTICK_IGNORE_DEVICES_EXCEPT": "0x0000/0x0000",
            "FORZA_AUTOPRESS": "", "FORZA_AUTOPRESS_FIFO": str(d / "in.fifo"),
            "FORZA_FRAMEDUMP_DIR": str(d), "FORZA_SCREENSHOT_TRIGGER": str(d / "shot.trigger"),
            # Each trigger saves BURST consecutive frames (frame_dump.cpp FORZA_SCREENSHOT_BURST).
            "FORZA_SCREENSHOT_BURST": f"{self.burst}:1",
            # frames.csv grows while frames are presented: the hang watch reads its size.
            "RECOMP_PERF": "1", "RECOMP_PERF_SCENE": "render_test", "RECOMP_PERF_CSV": str(d / "frames.csv"),
            "LD_LIBRARY_PATH": f"{self.args.sdk}/lib:{self.args.build}",
        })
        cmd = [str(self.args.build / "forza"),
               f"--game_data_root={self.args.game_data}", f"--update_data_root={self.args.update_data}",
               f"--user_data_root={ud}", f"--cache_root={work / 'cache'}",
               f"--log_file={d / 'forza.log'}", "--log_level=info",
               *self.cfg["game"]["args"], *self.args.extra]
        (d / "command.txt").write_text(" ".join(cmd) + "\n")
        with open(d / "run.out", "wb") as out:
            self.proc = subprocess.Popen(cmd, cwd=self.args.build, env=env, stdin=subprocess.DEVNULL,
                                         stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
        (d / "pid").write_text(f"{self.proc.pid}\n")
        for _ in range(100):
            try:
                for line in open(f"/proc/{self.proc.pid}/maps"):
                    if "/dev/shm/xenia_memory_" in line:
                        self.shm = line.split()[-1]
                        break
            except OSError:
                pass
            if self.shm or self.proc.poll() is not None:
                break
            time.sleep(0.1)
        self.clock = ClockHold(self.proc.pid)
        self.clock.start()
        log(f"started the game (pid {self.proc.pid}) in {d}")

    def stop(self):
        try:
            self._stop_game()
            for job in self.converting:
                job.result()
            if self.clock and self.clock.error:
                raise Hang(f"clock worker failed: {self.clock.error}")
        finally:
            self.pool.shutdown()
            if (self.created and owned(self.dir, "run-active")
                    and (self.proc is None or self.proc.poll() is not None)
                    and (self.clock is None or not self.clock.is_alive())):
                mark_owned(plain_path(self.dir), "run-finished")

    def _stop_game(self):
        if self.clock:
            self.clock.stop.set()
            if self.clock.ident is not None:
                self.clock.join()  # No more guest-memory writes before process cleanup.
            else:
                self.clock.g.close()
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        # Only this run's guest memory file, once no process maps it any more.
        if self.shm and os.path.exists(self.shm) and os.stat(self.shm).st_uid == os.getuid():
            name = os.path.basename(self.shm)
            mapped = False
            for maps in Path("/proc").glob("[0-9]*/maps"):
                try:
                    if name in maps.read_text():
                        mapped = True
                        break
                except OSError:
                    pass
            if not mapped:
                os.remove(self.shm)
        if self.created:
            fifo = plain_path(self.dir / "in.fifo")
            if fifo.exists():
                fifo.unlink()

    # -- watching
    def check(self):
        if self.clock and (self.clock.error or not self.clock.is_alive()):
            raise Hang(f"clock worker stopped: {self.clock.error or 'unexpected exit'}")
        if self.proc.poll() is not None:
            raise Hang(f"the game exited (status {self.proc.returncode})")
        try:
            size = (self.dir / "frames.csv").stat().st_size
        except OSError:
            size = 0
        now = time.monotonic()
        if size != self.frames_size:
            self.frames_size, self.frames_at = size, now
        elif now - self.frames_at > self.args.hang_secs:
            raise Hang(f"no new frames for {self.args.hang_secs:.0f} s")

    def wait(self, secs):
        end = time.monotonic() + secs
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.5, left))

    def send(self, line):
        deadline = time.monotonic() + 5
        while True:
            try:
                fd = os.open(self.dir / "in.fifo", os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError as e:
                if e.errno != errno.ENXIO or time.monotonic() > deadline:
                    raise Hang(f"the game does not read its input FIFO ({e})")
                time.sleep(0.05)
        try:
            os.write(fd, (line + "\n").encode())
        finally:
            os.close(fd)

    def shot(self, stem):
        """One trigger: the burst's consecutive frames, saved as STEM_0.png, STEM_1.png, ...
        (converted from the game's PPM in the background, so shots keep their timing)."""
        before = set(self.dir.glob("forza_shot_*.ppm"))
        (self.dir / "shot.trigger").touch()
        end = time.monotonic() + 10
        while time.monotonic() < end:
            self.check()
            new = sorted(set(self.dir.glob("forza_shot_*.ppm")) - before)
            if len(new) >= self.burst:
                out = []
                for j, ppm in enumerate(new[:self.burst]):
                    png = self.dir / f"{stem}_{j}.png"
                    ppm = ppm.rename(png.with_suffix(".ppm"))
                    self.converting.append(self.pool.submit(to_png, ppm, png))
                    out.append(png)
                return out
            time.sleep(0.02)
        (self.dir / "shot.trigger").unlink(missing_ok=True)
        raise Hang("no screenshot within 10 s (the game stopped presenting)")

    def wait_world(self, timeout):
        """Waits until free roam runs: the clock resolves (during the load it reads the
        flow's 16:00, then jumps to the save's time and stays there) and then starts to
        advance. world_at is that moment, to 10 ms."""
        end = time.monotonic() + timeout
        g = Guest(self.proc.pid)
        try:
            prev = None
            while True:
                self.check()
                if time.monotonic() > end:
                    raise Hang("the free roam world did not start")
                a = clock_address(g)
                v = g.f32(a) if a is not None else None
                if prev is not None and v is not None and 0 < v - prev <= 10:
                    break
                if v != prev:
                    prev = v
                time.sleep(0.01)
            self.world_at = time.monotonic()
            log(f"world running (clock {prev:.1f})")
        finally:
            g.close()

    # -- steps
    def run_steps(self, steps, wanted):
        last_capture = max((i for i, s in enumerate(steps)
                            if s.split()[0] == "capture" and s.split()[1] in wanted), default=-1)
        for i, step in enumerate(steps):
            if i > last_capture:
                break
            words = step.split()
            op, rest = words[0], words[1:]
            if op == "wait":
                self.wait(float(rest[0]))
            elif op == "first_frame":
                end = time.monotonic() + 120
                while self.frames_size == 0:
                    self.check()
                    if time.monotonic() > end:
                        raise Hang("no frame within 120 s of the start")
                    time.sleep(0.2)
                self.wait(float(rest[0]) if rest else 0)
            elif op == "press":
                for inp in rest:
                    self.send(inp if ":" in inp else inp + ":0.1")
                    self.wait(0.8)
            elif op == "world":
                self.wait_world(float(rest[0]) if rest else 120)
            elif op == "at":
                # Real seconds since the world started running (see wait_world): steps timed
                # this way do not depend on how long the loading took on this machine.
                self.wait(self.world_at + float(rest[0]) - time.monotonic())
            elif op == "clock":
                self.clock.set(None if rest[0] == "off" else parse_time(rest[0]))
            elif op == "capture":
                name = rest[0]
                opts = dict(o.split("=", 1) for o in rest[1:])
                scene = self.cfg["scenes"].get(name, {})
                shots = int(opts.get("shots", scene_value(self.cfg, scene, "shots")))
                interval = float(opts.get("interval", scene_value(self.cfg, scene, "interval")))
                if name not in wanted and name in self.cfg["scenes"]:
                    continue
                if self.clock.target is not None and self.clock.addr is None:
                    raise Hang(f"{name}: the time of day is not held (no world clock)")
                paths = []
                for k in range(shots):
                    if k:
                        self.wait(interval)
                    paths += self.shot(self.dir / f"{name}_{k}")
                self.shots[name] = paths
                log(f"captured {name} ({len(paths)} frames)")
            elif op == "trace":  # debugging: the clock every 20 ms into clock_trace.txt
                if rest[0] == "on":
                    self.clock.trace = (open(self.dir / "clock_trace.txt", "a", buffering=1), time.monotonic())
                else:
                    self.clock.trace = None
            elif op == "log":
                log(" ".join(rest))
            else:
                raise ValueError(f"unknown step: {step}")


def to_png(ppm, png):
    with Image.open(ppm) as im:
        im.convert("RGB").save(png)
    ppm.unlink()


def parse_time(text):
    if ":" in text:
        h, m = text.split(":")
        return int(h) * 3600 + int(m) * 60
    return float(text)


def scene_value(cfg, scene, key):
    return scene.get(key, cfg["defaults"][key])


def play(cfg, args, wanted, label, save_dir, xuid):
    """Runs every session that captures a wanted scene, each again after a hang (--retries).
    Returns ({scene: [png paths]}, {scene: error} for the scenes of a session that failed)."""
    shots, errors = {}, {}
    for sess in cfg["session"]:
        names = [s.split()[1] for s in sess["steps"] if s.split()[0] == "capture"]
        if not set(names) & wanted:
            continue
        for attempt in range(1, args.retries + 2):
            bench_lock_wait(args.lock_wait)
            run_dir = args.work / "runs" / f"{label}-{sess['name']}-{attempt}-{uuid.uuid4().hex}"
            s = Session(cfg, args, run_dir, save_dir, xuid)
            t0 = time.monotonic()
            failure = None
            try:
                s.start()
                s.run_steps(sess["steps"], wanted)
            except Exception as e:
                failure = e
            finally:
                try:
                    s.stop()
                except Exception as e:
                    failure = failure or e
            if failure is None:
                shots.update(s.shots)
                log(f"session {sess['name']} done in {time.monotonic() - t0:.0f} s")
                break
            log(f"session {sess['name']} attempt {attempt}: {failure}")
            if attempt == args.retries + 1:
                for n in set(names) & wanted:
                    errors[n] = f"session {sess['name']} failed {attempt} times ({failure}); log in {run_dir}"
    return shots, errors


# ---------------------------------------------------------------------------------------------
# Image comparison

BLOCK = 8            # SSIM block, in pixels of the half-resolution (640x360) image
C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
BAD_SSIM, BAD_CHROMA = 0.80, 6.0   # a block below this SSIM or above this Cb/Cr shift is "changed"


def mask_image(size, masks):
    """L image: 255 where compared, 0 inside the scene's masks ([x, y, w, h] at 1280x720)."""
    m = Image.new("L", size, 255)
    draw = ImageDraw.Draw(m)
    sx, sy = size[0] / WIDTH, size[1] / HEIGHT
    for x, y, w, h in masks:
        draw.rectangle([x * sx, y * sy, (x + w) * sx - 1, (y + h) * sy - 1], fill=0)
    return m


def fmath(fn, **images):
    return ImageMath.lambda_eval(lambda a: fn(a), **images)


def pixels(im):
    return list(im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata())


def compare(a, b, scene):
    """Metrics of image b against image a (PIL RGB images of the same size): block SSIM at
    1/scale resolution (scene "scale", default 2), with the scene's masks left out."""
    if a.size != b.size:
        raise ValueError(f"image sizes differ: {a.size} vs {b.size}")
    m = mask_image(a.size, scene.get("masks", []))
    black = Image.new("RGB", a.size)
    a, b = Image.composite(a, black, m), Image.composite(b, black, m)
    full_diff = ImageChops.difference(a, b)
    covered = ImageStat.Stat(m).mean[0] / 255 or 1e-9
    mae = sum(ImageStat.Stat(full_diff).mean) / 3 / covered
    scale = scene.get("scale", 2)
    half = (a.size[0] // scale, a.size[1] // scale)
    ya, cba, cra = [c.convert("F") for c in a.resize(half, Image.BOX).convert("YCbCr").split()]
    yb, cbb, crb = [c.convert("F") for c in b.resize(half, Image.BOX).convert("YCbCr").split()]
    r = lambda im: im.reduce(BLOCK)
    mx, my = r(ya), r(yb)
    xx = r(fmath(lambda v: v["x"] * v["x"], x=ya))
    yy = r(fmath(lambda v: v["y"] * v["y"], y=yb))
    xy = r(fmath(lambda v: v["x"] * v["y"], x=ya, y=yb))
    ssim = fmath(lambda v: ((2 * v["mx"] * v["my"] + C1) * (2 * (v["xy"] - v["mx"] * v["my"]) + C2))
                 / ((v["mx"] * v["mx"] + v["my"] * v["my"] + C1)
                    * ((v["xx"] - v["mx"] * v["mx"]) + (v["yy"] - v["my"] * v["my"]) + C2)),
                 mx=mx, my=my, xx=xx, yy=yy, xy=xy)
    chroma = fmath(lambda v: abs(v["a"] - v["b"]), a=r(cba), b=r(cbb))
    chroma2 = fmath(lambda v: abs(v["a"] - v["b"]), a=r(cra), b=r(crb))
    keep = m.resize(half, Image.BOX).convert("F").reduce(BLOCK)
    s_vals, c1, c2, k_vals = (pixels(im) for im in (ssim, chroma, chroma2, keep))
    blocks = [(s, max(u, v)) for s, u, v, k in zip(s_vals, c1, c2, k_vals) if k >= 128]
    n = len(blocks) or 1
    mean_ssim = sum(s for s, _ in blocks) / n
    bad = sum(1 for s, c in blocks if s < BAD_SSIM or c > BAD_CHROMA) / n
    return {"dssim": round(1 - mean_ssim, 5), "bad": round(bad, 5), "mae": round(mae, 3),
            "_ssim_map": ssim, "_chroma": (chroma, chroma2), "_keep": keep}


def thumb(im, scene):
    m = mask_image(im.size, scene.get("masks", []))
    return Image.composite(im, Image.new("RGB", im.size), m).convert("L").reduce(8)


def best_match(baseline_paths, current_paths, scene):
    """The closest pair of a baseline frame and a current frame, with its metrics: the pairs
    are ranked by the mean difference of 1/8 size thumbnails, the closest 6 compared fully."""
    base = [(p, Image.open(p).convert("RGB")) for p in baseline_paths]
    cur = [(p, Image.open(p).convert("RGB")) for p in current_paths]
    bt = [thumb(im, scene) for _, im in base]
    ct = [thumb(im, scene) for _, im in cur]
    ranked = sorted((ImageStat.Stat(ImageChops.difference(x, y)).mean[0], i, j)
                    for i, x in enumerate(bt) for j, y in enumerate(ct))
    best = None
    for _, i, j in ranked[:6]:
        res = compare(base[i][1], cur[j][1], scene)
        if best is None or (res["dssim"], res["bad"]) < (best["dssim"], best["bad"]):
            best = dict(res, baseline=str(base[i][0]), current=str(cur[j][0]))
    return best


def diff_image(res, out):
    """baseline | current | changes (red: lower SSIM or chroma shift; blue: masked)."""
    a = Image.open(res["baseline"]).convert("RGB")
    b = Image.open(res["current"]).convert("RGB")
    w, h = a.size[0] // 2, a.size[1] // 2
    a, b = a.resize((w, h), Image.BOX), b.resize((w, h), Image.BOX)
    ssim, (c1, c2), keep = res["_ssim_map"], res["_chroma"], res["_keep"]
    heat = fmath(lambda v: (1 - v["s"]) * 255 * 4 + v["c1"] * 12 + v["c2"] * 12,
                 s=ssim, c1=c1, c2=c2)
    heat = heat.convert("L").resize((w, h), Image.NEAREST)  # F to L clips to 0..255
    gray = b.convert("L").point(lambda x: x // 3).convert("RGB")
    red = Image.new("RGB", (w, h), (255, 40, 40))
    panel = Image.composite(red, gray, heat)
    masked = keep.convert("L").point(lambda x: 255 if x < 128 else 0).resize((w, h), Image.NEAREST)
    panel = Image.composite(Image.blend(panel, Image.new("RGB", (w, h), (40, 80, 200)), 0.6), panel, masked)
    sheet = Image.new("RGB", (w * 3, h))
    for i, im in enumerate((a, b, panel)):
        sheet.paste(im, (w * i, 0))
    sheet.save(out)


def clean(res):
    return {k: v for k, v in res.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------------------------
# Commands

def load_cfg():
    with open(SCENES, "rb") as f:
        cfg = tomllib.load(f)
    cfg.setdefault("scenes", {})
    return cfg


def selected(cfg, args):
    names = set(cfg["scenes"])
    if args.scenes:
        want = set(args.scenes.split(","))
        unknown = want - names
        if unknown:
            sys.exit(f"unknown scene(s): {', '.join(sorted(unknown))}; known: {', '.join(sorted(names))}")
        names = want
    return names


def provenance(args):
    exe = args.build / "forza"
    h = hashlib.sha256()
    with open(exe, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        rev = ""
    return {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "build": str(args.build),
            "forza_sha256": h.hexdigest()[:16], "sdk": str(args.sdk), "git": rev,
            "extra_flags": args.extra}


def cmd_record(cfg, args):
    names = selected(cfg, args)
    base = output_tree(args.baselines)
    output_tree(args.work)
    disjoint(base, args.work)
    save = plain_path(base / "save")
    source = profile_source(args.save) if args.save else None
    # Validate every replacement before touching either a source save or stored baselines.
    if source:
        disjoint(source, base)
        disjoint(source, args.work)
    for name, kind in [("save", "save")] + [(n, "baseline") for n in names]:
        if Path(name).name != name or name in (".", "..", "save") and kind != "save":
            raise ValueError(f"invalid scene directory: {name}")
        dest = plain_path(base / name)
        if dest.exists() and (kind != "save" or source) and not owned(dest, kind):
            raise ValueError(f"refusing to replace unowned {kind} directory: {dest}")
    if args.save:
        if not args.xuid:
            sys.exit("--save needs --xuid (the profile's XUID, as tools/play.sh --xenia uses)")
        if save.exists():
            remove_owned(save, "save", base)
        save.mkdir(parents=True)
        mark_owned(save, "save")
        shutil.copytree(source, save / TITLE)
        (save / "save.json").write_text(json.dumps({"xuid": args.xuid, "source": str(source)}, indent=1))
    if not (save / "save.json").exists():
        sys.exit(f"no save in {save}: pass --save DIR --xuid XUID once (docs/render-tests.md)")
    xuid = json.loads((save / "save.json").read_text())["xuid"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    runs = []
    for i in range(args.runs):
        log(f"record run {i + 1}/{args.runs}")
        shots, errors = play(cfg, args, names, f"{stamp}-rec{i + 1}", save / TITLE, xuid)
        if errors:
            sys.exit("\n".join(sorted(set(errors.values()))))
        runs.append(shots)
    prov = provenance(args)
    report = []
    for name in sorted(names):
        scene = cfg["scenes"][name]
        d = base / name
        if d.exists():
            remove_owned(d, "baseline", base)
        d.mkdir(parents=True)
        mark_owned(d, "baseline")
        files = []
        for i, r in enumerate(runs):
            files.append([])
            for k, p in enumerate(r[name]):
                dst = d / f"run{i + 1}_{k}.png"
                shutil.copy2(p, dst)
                files[-1].append(dst)
        # Run-to-run noise: each run's frames against the other runs' (what a check of the
        # same build scores at worst).
        noise = []
        for i in range(len(files)):
            others = [p for j, f in enumerate(files) if j != i for p in f]
            if others:
                n = clean(best_match(others, files[i], scene))
                n["baseline"], n["current"] = Path(n["baseline"]).name, Path(n["current"]).name
                noise.append(n)
        files = [p.name for f in files for p in f]
        tol = {"max_dssim": scene_value(cfg, scene, "max_dssim"), "max_bad": scene_value(cfg, scene, "max_bad")}
        worst = {k: max((n[k] for n in noise), default=0) for k in ("dssim", "bad", "mae")}
        meta = {"scene": name, "files": files, "noise": noise, "noise_max": worst,
                "tolerance": tol, **prov}
        (d / "baseline.json").write_text(json.dumps(meta, indent=1))
        flag = ""
        if worst["dssim"] > tol["max_dssim"] or worst["bad"] > tol["max_bad"]:
            flag = "  NOISIER THAN THE TOLERANCE"
        report.append(f"{name:18s} noise: dssim {worst['dssim']:.4f} (tolerance {tol['max_dssim']}),"
                      f" changed {worst['bad']:.2%} ({tol['max_bad']:.2%}), mae {worst['mae']:.2f}{flag}")
    print("\n".join(report))
    if not args.keep_runs:
        prune_runs(args.work)


def cmd_check(cfg, args):
    names = selected(cfg, args)
    base = args.baselines
    missing = [n for n in names if not (base / n / "baseline.json").exists()]
    if missing:
        sys.exit(f"no baseline for {', '.join(sorted(missing))} in {base}: record them first")
    save = base / "save"
    xuid = json.loads((save / "save.json").read_text())["xuid"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    t0 = time.monotonic()
    shots, errors = play(cfg, args, names, f"{stamp}-check", save / TITLE, xuid)
    out = plain_path(args.report if args.report else args.work / "reports" / f"{stamp}-{uuid.uuid4().hex}")
    out.mkdir(parents=True)  # A report is a new directory, never an existing tree to overwrite.
    rows = []
    for name in sorted(names):
        scene = cfg["scenes"][name]
        meta = json.loads((base / name / "baseline.json").read_text())
        tol = {"max_dssim": scene_value(cfg, scene, "max_dssim"), "max_bad": scene_value(cfg, scene, "max_bad")}
        row = {"scene": name, **tol, "noise_max": meta.get("noise_max"),
               "description": scene.get("description", ""),
               "baseline_recorded": meta.get("date"), "baseline_git": meta.get("git")}
        if name not in shots:
            rows.append(dict(row, result="ERROR", error=errors.get(name, "not captured")))
            log(f"{name}: ERROR {rows[-1]['error']}")
            continue
        res = best_match([base / name / f for f in meta["files"]], shots[name], scene)
        ok = res["dssim"] <= tol["max_dssim"] and res["bad"] <= tol["max_bad"]
        diff_image(res, out / f"{name}.png")
        shutil.copy2(res["current"], out / f"{name}.current.png")
        shutil.copy2(res["baseline"], out / f"{name}.baseline.png")
        rows.append(dict(row, result="PASS" if ok else "FAIL", **clean(res)))
        log(f"{name}: {rows[-1]['result']} dssim {res['dssim']:.4f}/{tol['max_dssim']}"
            f" changed {res['bad']:.2%}/{tol['max_bad']:.2%} mae {res['mae']:.2f}")
    info = {**provenance(args), "seconds": round(time.monotonic() - t0), "baselines": str(base)}
    (out / "report.json").write_text(json.dumps({"info": info, "scenes": rows}, indent=1))
    write_html(out / "index.html", info, rows)
    passed = sum(r["result"] == "PASS" for r in rows)
    print(f"{passed}/{len(rows)} scenes pass; report: {out / 'index.html'}")
    if not args.keep_runs:
        prune_runs(args.work)
    if any(r["result"] == "ERROR" for r in rows):
        return 2
    return 0 if passed == len(rows) else 1


def write_html(path, info, rows):
    e = html.escape
    out = ["<!doctype html><meta charset=utf-8><title>Render test report</title>",
           "<style>body{font:14px sans-serif;margin:16px;background:#fff;color:#111}"
           "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left}"
           ".PASS{color:#070}.FAIL,.ERROR{color:#b00;font-weight:bold}img{max-width:100%}"
           "@media(prefers-color-scheme:dark){body{background:#111;color:#ddd}td,th{border-color:#444}"
           ".PASS{color:#6c6}.FAIL,.ERROR{color:#f66}}</style>",
           "<h1>Render test report</h1><p>"]
    for k, v in info.items():
        out.append(f"<b>{e(k)}</b>: {e(str(v))}<br>")
    out.append("</p><table><tr><th>scene</th><th>result</th><th>dssim / tolerance</th>"
               "<th>changed blocks / tolerance</th><th>mean abs diff</th>"
               "<th>noise when recorded (dssim, changed)</th></tr>")
    for r in rows:
        n = r.get("noise_max") or {}
        res = r["result"]
        if res == "ERROR":
            cells = f"<td colspan=3>{e(r['error'])}</td>"
        else:
            cells = (f"<td>{r['dssim']:.4f} / {r['max_dssim']}</td><td>{r['bad']:.2%} / {r['max_bad']:.2%}</td>"
                     f"<td>{r['mae']:.2f}</td>")
        out.append(f"<tr><td><a href='#{e(r['scene'])}'>{e(r['scene'])}</a></td><td class={res}>{res}</td>"
                   f"{cells}<td>{n.get('dssim', 0):.4f}, {n.get('bad', 0):.2%}</td></tr>")
    out.append("</table><p>Each image: baseline | this run | changes (red: lower structural similarity "
               "or a colour shift; blue: masked). Click it for full size.</p>")
    for r in rows:
        if r["result"] == "ERROR":
            continue
        s = e(r["scene"])
        out.append(f"<h2 id='{s}'>{s} <span class={r['result']}>{r['result']}</span></h2><p>{e(r['description'])}</p>"
                   f"<a href='{s}.png'><img src='{s}.png' alt='{s}: baseline, this run, changes'></a><br>"
                   f"<a href='{s}.baseline.png'>baseline</a> · <a href='{s}.current.png'>this run</a>")
    path.write_text("\n".join(out))


def cmd_probe(cfg, args):
    """Plays whole sessions and keeps every capture (also unnamed ones): for writing scenes."""
    if args.save:
        if not args.xuid:
            sys.exit("--save needs --xuid (the profile's XUID)")
        save, xuid = profile_source(args.save), args.xuid
    else:
        save = args.baselines / "save" / TITLE
        xuid = json.loads((args.baselines / "save" / "save.json").read_text())["xuid"]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    failed = False
    for sess in cfg["session"]:
        if args.session and sess["name"] != args.session:
            continue
        names = {s.split()[1] for s in sess["steps"] if s.split()[0] == "capture"}
        one = dict(cfg, session=[sess])
        shots, errors = play(one, args, names, f"{stamp}-probe", save, xuid)
        failed |= bool(errors)
        for err in sorted(set(errors.values())):
            print(err)
        for name, paths in shots.items():
            print(f"{name}: {len(paths)} frames, {paths[0].parent}/{name}_*.png")
    return 2 if failed else 0


def cmd_compare(cfg, args):
    scene = cfg["scenes"].get(args.scene, {}) if args.scene else {}
    res = best_match([Path(args.a)], [Path(args.b)], scene)
    if args.out:
        diff_image(res, args.out)
    print(json.dumps(clean(res), indent=1))


def prune_runs(work):
    """Keep the four newest finished, owned runs; never prune active or unowned data."""
    root = plain_path(plain_path(work) / "runs")
    runs = sorted((p for p in root.glob("*") if p.is_dir() and owned(p, "run-finished")),
                  key=lambda p: p.stat().st_mtime)
    for d in runs[:-4]:
        remove_owned(d, "run-finished", root)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("record", "check", "probe"):
        s = sub.add_parser(name)
        s.add_argument("--scenes", help="comma-separated scene names (default: all)")
        s.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES,
                       help=f"baseline folder (default {DEFAULT_BASELINES})")
        s.add_argument("--work", type=Path, default=DEFAULT_WORK,
                       help=f"run folders, shader caches, reports (default {DEFAULT_WORK})")
        s.add_argument("--build", type=Path, default=ROOT / "forza-recomp/out/build/linux-tu4",
                       help="the TU4 game build folder")
        s.add_argument("--sdk", type=Path, default=Path(os.environ["REXSDK"]) if os.environ.get("REXSDK") else default_sdk(),
                       help="SDK prefix whose lib/ the game loads (default REXSDK or the patched overlay)")
        s.add_argument("--game-data", type=Path, default=Path(os.environ.get("FORZA_DATA") or ROOT / "extracted/game"))
        s.add_argument("--update-data", type=Path, default=Path(os.environ.get("FORZA_UPDATE") or ROOT / "extracted/tu4"),
                       help="the extracted Title Update 4")
        s.add_argument("--hang-secs", type=float, default=40, help="no new frame for this long is a hang")
        s.add_argument("--retries", type=int, default=1, help="new attempts after a hang (#512)")
        s.add_argument("--lock-wait", type=int, default=3600, help="seconds to wait for a benchmark lock")
        s.add_argument("--keep-runs", action="store_true", help="keep every run folder (logs, raw shots)")
        if name in ("record", "probe"):
            s.add_argument("--save", help="a profile's 4D5309C9 save folder (record: stored with the baselines)")
            s.add_argument("--xuid", help="that profile's XUID")
        if name == "record":
            s.add_argument("--runs", type=int, default=3, help="runs to record (their spread is the noise)")
        elif name == "probe":
            s.add_argument("--session", help="only this session")
        else:
            s.add_argument("--report", help="report folder (default WORK/reports/<time>)")
        s.add_argument("extra", nargs="*", help="extra game flags after --, e.g. -- --anisotropic_override=0")
    c = sub.add_parser("compare")
    c.add_argument("a", help="baseline image")
    c.add_argument("b", help="image to check")
    c.add_argument("--scene", help="use this scene's masks")
    c.add_argument("--out", help="write the diff sheet here")
    args = p.parse_args()
    cfg = load_cfg()
    if args.cmd == "compare":
        if args.out:
            args.out = plain_path(args.out)
            if args.out.is_relative_to(ROOT):
                sys.exit("diff images must live outside the repository")
        return cmd_compare(cfg, args)
    for k in ("baselines", "work", "build", "sdk", "game_data", "update_data"):
        setattr(args, k, plain_path(getattr(args, k)))
    args.baselines, args.work = output_tree(args.baselines), output_tree(args.work)
    report = plain_path(getattr(args, "report", None) or args.work / "reports")
    if any(p.is_relative_to(ROOT) for p in (args.baselines, args.work, report)):
        sys.exit("baselines, run folders and reports must live outside the repository (game images)")
    disjoint(args.baselines, args.work)
    disjoint(args.baselines, report)
    if getattr(args, "report", None):
        disjoint(args.work, report)
        args.report = str(report)
    if getattr(args, "save", None):
        source = profile_source(args.save)
        for dest in (args.baselines, args.work, report):
            disjoint(source, dest)
    if not (args.build / "forza").exists():
        sys.exit(f"no game in {args.build} (--build; the TU4 build, docs/PROJECT.md)")
    if args.cmd == "probe":
        args.keep_runs = True
        return cmd_probe(cfg, args)
    return cmd_record(cfg, args) if args.cmd == "record" else cmd_check(cfg, args)


if __name__ == "__main__":
    sys.exit(main() or 0)
