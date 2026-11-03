#!/usr/bin/env python3
"""vr_comfort_check.py: measured comfort tests of the VR cockpit (#333, docs/vr.md "Version 8"), from the
eye pictures a headset shows. Needs numpy and Pillow.

  vr_comfort_check.py run --rig emu RUNDIR OUT [--tests 124] [--poses XREMU/poses.py]
  vr_comfort_check.py run --rig headset RUNDIR OUT --fifo FIFO --hs PATH/hs.sh [--tests 124]
  vr_comfort_check.py replay --rig emu|headset RUNDIR OUT TRACE T0 T1 [--step N] [...]
  vr_comfort_check.py analyze OUT [--tests 124]   (again, from the saved pictures)
  vr_comfort_check.py perf FORZA_LOG [--secs 20]

RUNDIR is the game's run folder (in.fifo, forza.log), started in the VR cockpit, the car standing.
--tests takes the test numbers; with "q" only a few turns are captured (a quick check of test 2).

Rigs. emu: the emulated headset (scratchpad/xremu2): the head really moves (poses.py set: OpenXR LOCAL,
x right, y up, z back, metres; yaw left, pitch up, degrees) and poses.py capture saves both eyes as the
compositor shows them (2064 x 2208 each). headset: a real headset lying still; the game's test pose
(FORZA_VR_POSE_FIFO, --vr_head_pose_test=true) moves the game's camera, and hs.sh shot saves both eyes from
the headset's display (lens-shaped: approximate there).

Each pose is captured right after a straight-ahead picture and compared with it (the game's day goes on).
Textured patches of the left eye are found in the right eye: the disparity gives each one's depth, so
where a point fixed to the car must appear after the head moved can be predicted.

 1 translation parallax: head moved 6 cm right, back and up, no turn. Cabin patches (nearer than 1.2 m):
   the measured shift projected on the predicted one ("gain": 1 = fixed to the car, 0 = follows the head).
   PASS: gain 0.8-1.2 and median error under 25 % of the predicted shift, for each move.
 2 lighting: test 1's moves, turns of the head about the measured eye (yaw 30/45 each way, pitch +15,
   -20) and two leans (a 40 degree turn with the head 5 cm aside and 3 back). Each cabin patch found
   again cleanly is compared with itself before: the median ratio is the overall brightness (exposure),
   the 90th percentile of each patch's ratio over that median is the lighting-only change. The metric
   has a floor where the light is exactly the same but the picture changed by the same geometry: the
   left against the right eye of the straight-ahead picture (6.3 cm, shaded once) for moves, that
   picture warped by the turn for turns. PASS: overall within 5 %, lighting-only within 5 points of the
   floor for turns and 20 points for moves (view-dependent sheen and glare; the game's own camera moving
   6 cm outside its 10 cm snap shows 14-19).
 3 no flick: a head path from a VR trace (the user's) replayed in steps of at most 1 cm and 3 degrees;
   the left eye's change per step (mean absolute difference at 1/8 size). PASS: no step changes more
   than 1.75 times the median of its 3 neighbours on each side (the game's own 10 cm snap jumps 1.85-2.16
   times at each crossing).
 4 driving: the throttle held 4 s, the head still: the cabin patches' shift from the standing picture.
   PASS: median under 1 px and most patches under 1 px (no camera shake, lean or bob).
 5 performance: the VR status lines' game frames a second. PASS: at least 70 at 72 Hz.
"""
import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
# Per-eye field of view of the emulator's quest3 profile and of the user's Quest 3 through WiVRn
# (degrees: out, in, up, down).
EYE_FOV = (54.0, 40.0, 44.0, 55.0)
IPD = 0.063
SCALE = 2  # pictures are compared at half size
PATCH = 15  # half-size patch radius... (31 x 31 at half size)
NEAR = 1.2  # metres: the cabin
LIGHT_NCC = 0.92  # lighting compared only on patches found again this well
LIGHT_MIN = 12.0  # ... and not darker than this (8-bit luma): ratios of near-black are noise
LIGHT_GLOBAL = 0.05  # the cabin's overall brightness (exposure) may change this much
# Lighting-only change beyond the metric's floor, in fractions: a head that only turns sees the same
# light (5 points of slack); a head that moves sees view-dependent sheen and glare change, which the
# game's own moving camera shows too (measured: 14-19 points for a 6 cm move outside its 10 cm snap),
# so 20 points; v7's cabin that follows the head while its light stays put measured 29-59.
LIGHT_EXCESS_TURN = 0.05
# Test 3: a step is a jump when it changes the picture this many times as much as its neighbours.
# Measured on the user's version 6 path: the game's own 10 cm snap ("parallax 0") jumps 1.85-2.16 times
# its neighbours at each crossing of 10 cm; version 8 at most 1.64. (Traffic passing a side window can
# also do it: look at the flagged steps' pictures.)
JUMP_RATIO = 1.75
LIGHT_EXCESS_MOVE = 0.20
FAR = 8.0

# Each pose is captured right after a straight-ahead picture ("b_" + name) and compared with it: the
# game's day and night cycle changes the light within minutes.
T1_POSES = [("right6", {"x": 0.06}), ("back6", {"z": 0.06}), ("up6", {"y": 0.06})]


def eye_turn(yaw=0.0, pitch=0.0):
    """A turn of the head about the left eye (the eye the tests measure): the head's centre moves so
    that the left eye stays where it was, so the left eye's picture changes by the turn alone and the
    lighting metric's floor is the picture warped by that turn. The game's camera (the head's centre)
    moves 1-2 cm with it."""
    a = IPD / 2
    t = math.radians(yaw)
    return {"yaw": yaw, "pitch": pitch, "x": -a + a * math.cos(t), "z": -a * math.sin(t), "turn": 1}


T2_POSES = [("yawL30", eye_turn(30)), ("yawR30", eye_turn(-30)), ("yawL45", eye_turn(45)),
            ("yawR45", eye_turn(-45)), ("pitchU15", eye_turn(0, 15)), ("pitchD20", eye_turn(0, -20)),
            ("leanL", {"yaw": 40, "x": -0.05, "z": 0.03}), ("leanR", {"yaw": -40, "x": 0.05, "z": 0.03})]
QUICK_POSES = [("yawR30", eye_turn(-30)), ("yawL30", eye_turn(30)), ("yawR45", eye_turn(-45)), ("pitchU15", eye_turn(0, 15))]


# ----------------------------------------------------------------------------------- rigs
class EmuRig:
    def __init__(self, a):
        self.poses = a.poses
        self.settle = a.settle

    def set(self, yaw=0, pitch=0, roll=0, x=0, y=0, z=0, turn=0):
        cmd = [sys.executable, "-I", self.poses, "set", "yaw=%g" % yaw, "pitch=%g" % pitch,
               "roll=%g" % roll, "x=%g" % x, "y=%g" % y, "z=%g" % z]
        for attempt in range(4):  # the emulator's pose port refuses a connection now and then
            if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise SystemExit("poses.py set failed: " + " ".join(cmd[3:]))
        time.sleep(self.settle)

    def capture(self, out, name):
        r = subprocess.run([sys.executable, "-I", self.poses, "capture", name, "--out", out],
                           capture_output=True, text=True)
        if not os.path.exists(os.path.join(out, name + "_L.png")):
            raise SystemExit("capture failed: " + r.stdout + r.stderr)


class HeadsetRig:
    def __init__(self, a):
        self.fifo = a.fifo
        self.hs = a.hs
        self.settle = a.settle

    def set(self, yaw=0, pitch=0, roll=0, x=0, y=0, z=0, turn=0):
        # The test pose: x right, y up, z FORWARD (the game's camera space).
        with open(self.fifo, "w") as f:
            f.write("%g %g %g %g %g %g\n" % (yaw, pitch, roll, x, y, -z))
        time.sleep(self.settle)

    def capture(self, out, name):
        subprocess.run([self.hs, "shot", out, name], check=True, stdout=subprocess.DEVNULL)
        im = Image.open(os.path.join(out, name + ".png")).convert("RGB")
        w, h = im.size
        im.crop((0, 0, w // 2, h)).save(os.path.join(out, name + "_L.png"))
        im.crop((w // 2, 0, w, h)).save(os.path.join(out, name + "_R.png"))
        os.remove(os.path.join(out, name + ".png"))


class Shifted:
    """A rig whose poses all start from --origin (e.g. x=0.12: the head 12 cm right as test 1's base)."""
    def __init__(self, rig, origin):
        self.rig, self.origin = rig, origin

    def set(self, **kv):
        kv = dict(kv)
        for k, v in self.origin.items():
            kv[k] = kv.get(k, 0) + v
        self.rig.set(**kv)

    def capture(self, out, name):
        self.rig.capture(out, name)


def make_rig(a):
    rig = EmuRig(a) if a.rig == "emu" else HeadsetRig(a)
    origin = dict((k, float(v)) for k, v in (kv.split("=") for kv in (a.origin.split(",") if a.origin else [])))
    return Shifted(rig, origin) if origin else rig


# ------------------------------------------------------------------------------- pictures
def load(out, name, eye="L"):
    im = Image.open(os.path.join(out, "%s_%s.png" % (name, eye))).convert("RGB")
    w, h = im.size
    im = im.resize((w // SCALE, h // SCALE), Image.BILINEAR)
    rgb = np.asarray(im, dtype=np.float32)
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    return lum


def tan_axes(w, h, eye):
    """Pixel centres -> tangent of the angle (x right, y up) for a picture w x h of `eye`."""
    out_, in_, up, down = (math.tan(math.radians(v)) for v in EYE_FOV)
    left, right = (-out_, in_) if eye == "L" else (-in_, out_)
    tx = left + (np.arange(w) + 0.5) / w * (right - left)
    ty = up - (np.arange(h) + 0.5) / h * (up + down)
    return tx, ty, (right - left) / w, (up + down) / h


def patches(lum, mask=None, stride=24, r=PATCH, min_std=6.0):
    h, w = lum.shape
    pts = []
    for y in range(r + 8, h - r - 8, stride):
        for x in range(r + 8, w - r - 8, stride):
            if mask is not None and not mask[y, x]:
                continue
            p = lum[y - r:y + r + 1, x - r:x + r + 1]
            if p.std() >= min_std:
                pts.append((x, y))
    return pts


def ncc_search(a, b, x, y, cx, cy, sx, sy, r=PATCH):
    """Finds a's patch at (x, y) in b around (cx, cy) +- (sx, sy). Returns (bx, by, score) with
    sub-pixel peak, or None."""
    h, w = b.shape
    x0, x1 = int(round(cx)) - sx - r, int(round(cx)) + sx + r + 1
    y0, y1 = int(round(cy)) - sy - r, int(round(cy)) + sy + r + 1
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None
    t = a[y - r:y + r + 1, x - r:x + r + 1].astype(np.float64)
    t = t - t.mean()
    tn = math.sqrt((t * t).sum())
    if tn < 1e-6:
        return None
    reg = b[y0:y1, x0:x1].astype(np.float64)
    win = np.lib.stride_tricks.sliding_window_view(reg, t.shape)
    wm = win.mean(axis=(2, 3), keepdims=True)
    wc = win - wm
    num = (wc * t).sum(axis=(2, 3))
    den = np.sqrt((wc * wc).sum(axis=(2, 3))) * tn + 1e-9
    s = num / den
    iy, ix = np.unravel_index(np.argmax(s), s.shape)
    best = s[iy, ix]

    def sub(v, i, n):
        if 0 < i < n - 1:
            d = v[i - 1] - 2 * v[i] + v[i + 1]
            if d < 0:
                return 0.5 * (v[i - 1] - v[i + 1]) / d
        return 0.0
    fx = ix + sub(s[iy, :], ix, s.shape[1])
    fy = iy + sub(s[:, ix], iy, s.shape[0])
    return x0 + r + fx, y0 + r + fy, best


def hud_mask(out, name, shape):
    """True where the HUD panel (a quad layer) is not: from the capture's layer list (emulator)."""
    h, w = shape
    m = np.ones(shape, bool)
    meta = os.path.join(out, name + ".txt")
    if not os.path.exists(meta):
        # The headset: the panel's place from the settings' defaults (1 m ahead, 14 degrees down,
        # 0.9 m wide): rows 52-80 %, columns 22-82 % of the left eye; the speedometer corners too.
        m[int(0.50 * h):int(0.82 * h), int(0.20 * w):int(0.85 * w)] = False
        return m
    txt = open(meta).read()
    q = re.search(r"quad_size ([\d.]+) ([\d.]+).*\n.*quad pos (\S+) (\S+) (\S+) quat_xyzw (\S+) (\S+) (\S+) (\S+)", txt)
    v = re.search(r"view 0 pos (\S+) (\S+) (\S+) quat_xyzw (\S+) (\S+) (\S+) (\S+)", txt)
    if not q or not v:
        return m
    qw_, qh_ = float(q.group(1)), float(q.group(2))
    qp = np.array([float(q.group(i)) for i in (3, 4, 5)])
    qq = [float(q.group(i)) for i in (6, 7, 8, 9)]
    ep = np.array([float(v.group(i)) for i in (1, 2, 3)])
    eq = [float(v.group(i)) for i in (4, 5, 6, 7)]

    def rot(qv, p):
        x, y, z, ww = qv
        u = np.array([x, y, z])
        return p + 2 * np.cross(u, np.cross(u, p) + ww * p)
    tx, ty, dx, dy = tan_axes(w, h, "L")
    xs, ys = [], []
    for sx in (-1, 1):
        for sy in (-1, 1):
            c = qp + rot(qq, np.array([sx * qw_ / 2, sy * qh_ / 2, 0.0]))
            d = c - ep
            # into eye space: inverse rotation (the eye's pose is taken as the layer view's; the
            # emulator's eye looks the same way, its pitch is what the game drew with)
            x, y, z, ww = eq
            d = rot((-x, -y, -z, ww), d)
            xs.append(d[0] / -d[2]); ys.append(d[1] / -d[2])
    # the eye's own orientation in LOCAL is the head's: the game turned its picture by the
    # frustum's pitch, the emulator's eye is not turned; add that pitch back
    pitch = 2 * math.atan2(eq[0], eq[3])
    ys = [math.tan(math.atan(yy) + pitch) for yy in ys]
    x0 = np.searchsorted(tx, min(xs)) - 6; x1 = np.searchsorted(tx, max(xs)) + 6
    y0 = np.searchsorted(-ty, -max(ys)) - 6; y1 = np.searchsorted(-ty, -min(ys)) + 6
    m[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = False
    return m


# ----------------------------------------------------------------------------------- depth
def depth_map(out, name):
    """Patches of the left eye with their depth (metres, from the right eye's disparity)."""
    L = load(out, name, "L"); R = load(out, name, "R")
    h, w = L.shape
    txL, tyL, dxL, dyL = tan_axes(w, h, "L")
    txR, tyR, dxR, dyR = tan_axes(w, h, "R")
    mask = hud_mask(out, name, L.shape)
    res = []
    for (x, y) in patches(L, mask):
        # A point at depth z: tan_L - tan_R = IPD / z. Search the right eye along the row, from
        # infinity (same tangent) to 0.25 m.
        x_inf = np.searchsorted(txR, txL[x])
        lo = x_inf - int(IPD / 0.25 / dxR) - 4
        cx = (x_inf + 4 + lo) / 2
        sx = int((x_inf + 4 - lo) / 2)
        m = ncc_search(L, R, x, y, cx, y, sx, 2)
        if not m or m[2] < 0.85:
            continue
        bx = m[0]
        t_r = np.interp(bx, np.arange(w), txR)
        disp = txL[x] - t_r
        z = IPD / disp if disp > 1e-4 else 1e3
        res.append({"x": x, "y": y, "z": float(z), "tx": float(txL[x]), "ty": float(tyL[y]), "score": float(m[2])})
    return res, L


def predict(p, w, h, yaw=0.0, pitch=0.0, x=0.0, y=0.0, z=0.0, turn=0):
    """Where the left eye sees a car-fixed point p (from depth_map) after the head moved: yaw (left)
    and pitch (up) in degrees about the head's centre, x/y/z (right, up, back) metres."""
    tx, ty, dx, dy = tan_axes(w, h, "L")
    # the point in head space (the left eye IPD/2 left of the head's centre; forward is -z)
    P = np.array([p["tx"] * p["z"] - IPD / 2, p["ty"] * p["z"], -p["z"]])
    # head moved by (x, y, z), turned by yaw then pitch: the point in the new head space
    P = P - np.array([x, y, z])
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])  # head turned left: inverse
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    P = (Ry @ Rx).T @ P
    E = P + np.array([IPD / 2, 0, 0])  # into the left eye
    if E[2] > -0.02:
        return None
    u, v = E[0] / -E[2], E[1] / -E[2]
    px = (u - tx[0]) / dx
    py = (ty[0] - v) / dy
    if not (0 <= px < w and 0 <= py < h):
        return None
    return px, py


# ----------------------------------------------------------------------------------- tests
def pose_pair(out, name, kv, cache):
    """Cabin and scenery patches of b_NAME found again in NAME: gain, error, lighting."""
    base = "b_" + name
    if base not in cache:
        cache[base] = depth_map(out, base)
    pts, L0 = cache[base]
    h, w = L0.shape
    L1 = load(out, name, "L")
    gains, errs, preds, farshift, ratios = [], [], [], [], []
    r = PATCH
    for p in pts:
        near = p["z"] < NEAR
        if not near and p["z"] < FAR:
            continue
        pr = predict(p, w, h, **kv)
        if pr is None:
            continue
        pv = np.array([pr[0] - p["x"], pr[1] - p["y"]])
        # search round both the prediction and the old place (a cabin that follows the head)
        cx, cy = (pr[0] + p["x"]) / 2, (pr[1] + p["y"]) / 2
        sx = int(abs(pr[0] - p["x"]) / 2) + 10; sy = int(abs(pr[1] - p["y"]) / 2) + 10
        if sx > 300 or sy > 300:
            continue
        m = ncc_search(L0, L1, p["x"], p["y"], cx, cy, sx, sy)
        if not m or m[2] < 0.8:
            continue
        mv = np.array([m[0] - p["x"], m[1] - p["y"]])
        if not near:
            farshift.append(float(np.linalg.norm(mv - pv)))
            continue
        n = float(np.linalg.norm(pv))
        if n >= 3:
            gains.append(float(mv @ pv) / (n * n))
            preds.append(n)
        errs.append(float(np.linalg.norm(mv - pv)))
        # Lighting: only where the patch was found again cleanly (no edge of a nearer part across it
        # that moved by parallax: those change the patch's content, not its light). The light is
        # compared on the found patch, wherever it was found (a cabin that follows the head counts).
        if m[2] < LIGHT_NCC:
            continue
        a = L0[p["y"] - r:p["y"] + r + 1, p["x"] - r:p["x"] + r + 1].mean()
        bx, by = int(round(m[0])), int(round(m[1]))
        if by - r >= 0 and bx - r >= 0 and by + r + 1 <= h and bx + r + 1 <= w and a > LIGHT_MIN:
            ratios.append(L1[by - r:by + r + 1, bx - r:bx + r + 1].mean() / a)
    med = lambda v: float(np.median(v)) if len(v) else float("nan")
    out_ = {"gain": med(gains), "err_px": med(errs), "pred_px": med(preds), "scenery_err_px": med(farshift),
            "n": len(errs)}
    out_.update(light_stats(ratios))
    return out_


def light_stats(ratios):
    if len(ratios) < 8:
        return {}
    ratios = np.array(ratios); g = float(np.median(ratios))
    return {"bright": g - 1, "light90": float(np.percentile(np.abs(ratios / g - 1), 90)), "nlight": int(len(ratios))}


def light_ratio(L0, L1, x, y, m, h, w):
    r = PATCH
    if not m or m[2] < LIGHT_NCC:
        return None
    a = L0[y - r:y + r + 1, x - r:x + r + 1].mean()
    bx, by = int(round(m[0])), int(round(m[1]))
    if by - r < 0 or bx - r < 0 or by + r + 1 > h or bx + r + 1 > w or a <= LIGHT_MIN:
        return None
    return L1[by - r:by + r + 1, bx - r:bx + r + 1].mean() / a


# The lighting metric's own floor: what it reports when the picture changes by the same geometry with
# the light exactly the same. Translation: the left eye against the right eye of the straight-ahead
# picture (one frame, shaded once, the eyes 6.3 cm apart). Rotation: the straight-ahead picture warped
# by the turn (about the eye; the shading is the same by construction).
def floor_parallax(out, base, cache):
    pts, L = cache[base]
    R = load(out, base, "R"); h, w = L.shape
    txR, tyR, dxR, dyR = tan_axes(w, h, "R")
    ratios = []
    for p in pts:
        if p["z"] >= NEAR:
            continue
        px = (p["tx"] - IPD / p["z"] - txR[0]) / dxR
        m = ncc_search(L, R, p["x"], p["y"], px, p["y"], 8, 3)
        q = light_ratio(L, R, p["x"], p["y"], m, h, w)
        if q is not None:
            ratios.append(q)
    return light_stats(ratios).get("light90", float("nan"))


def floor_rotation(out, base, kv, cache):
    yaw, pitch = kv.get("yaw", 0), kv.get("pitch", 0)
    if not yaw and not pitch:
        return 0.0
    pts, L0 = cache[base]
    h, w = L0.shape
    tx, ty, dx, dy = tan_axes(w, h, "L")
    X, Y = np.meshgrid(tx, ty)
    d = np.stack([X, Y, -np.ones_like(X)], -1)
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    cp, sp = math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    Rm = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]) @ np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]])
    db = d @ Rm.T
    with np.errstate(divide="ignore", invalid="ignore"):
        px = ((db[..., 0] / -db[..., 2]) - tx[0]) / dx
        py = (ty[0] - (db[..., 1] / -db[..., 2])) / dy
    ok = (db[..., 2] < -1e-3) & (px >= 0) & (px < w - 1) & (py >= 0) & (py < h - 1)
    px = np.where(ok, px, 0); py = np.where(ok, py, 0)
    x0 = np.floor(px).astype(int); y0 = np.floor(py).astype(int); fx = px - x0; fy = py - y0
    L1 = (L0[y0, x0] * (1 - fx) * (1 - fy) + L0[y0, x0 + 1] * fx * (1 - fy) + L0[y0 + 1, x0] * (1 - fx) * fy
          + L0[y0 + 1, x0 + 1] * fx * fy)
    L1 = np.where(ok, L1, 0).astype(np.float32)
    global IPD
    ipd, IPD = IPD, 0.0  # the warp turns about the eye
    try:
        ratios = []
        for p in pts:
            if p["z"] >= NEAR:
                continue
            pr = predict(p, w, h, yaw=yaw, pitch=pitch)
            if pr is None:
                continue
            m = ncc_search(L0, L1, p["x"], p["y"], pr[0], pr[1], 10, 10)
            q = light_ratio(L0, L1, p["x"], p["y"], m, h, w)
            if q is not None:
                ratios.append(q)
    finally:
        IPD = ipd
    return light_stats(ratios).get("light90", float("nan"))


def test1(out, res, cache):
    lines = []
    ok = True
    res["t1"] = {}
    for name, kv in T1_POSES:
        if not os.path.exists(os.path.join(out, name + "_L.png")):
            continue
        r = pose_pair(out, name, kv, cache)
        good = r["n"] >= 10 and 0.8 <= r["gain"] <= 1.2 and r["err_px"] < 0.25 * r["pred_px"]
        ok &= good
        r["pass"] = good
        res["t1"][name] = r
        lines.append("  %-7s %3d cabin patches: predicted %5.1f px, gain %5.2f (1: fixed to the car, 0: follows the head), error %5.1f px; scenery error %4.1f px  %s"
                     % (name, r["n"], r["pred_px"], r["gain"], r["err_px"], r["scenery_err_px"], "ok" if good else "FAIL"))
    return ok, lines


def test2(out, res, cache):
    lines = []
    ok = True
    res["t2"] = {}
    for name, kv in T1_POSES + T2_POSES:
        if not os.path.exists(os.path.join(out, name + "_L.png")):
            continue
        r = pose_pair(out, name, kv, cache)
        if "bright" not in r:
            lines.append("  %-8s too few cabin patches found again" % name)
            continue
        turned = any(kv.get(k, 0) for k in ("yaw", "pitch"))
        moved = not kv.get("turn") and any(kv.get(k, 0) for k in ("x", "y", "z"))
        floor = max(floor_parallax(out, "b_" + name, cache) if moved else 0.0,
                    floor_rotation(out, "b_" + name, kv, cache) if turned else 0.0)
        r["floor90"] = floor
        r["excess"] = r["light90"] - floor
        limit = LIGHT_EXCESS_MOVE if moved else LIGHT_EXCESS_TURN
        good = abs(r["bright"]) < LIGHT_GLOBAL and r["excess"] < limit
        ok &= good
        r["pass"] = good
        res["t2"][name] = r
        lines.append("  %-8s %3d cabin patches: brightness %+5.1f %%, lighting-only (90th pct) %5.1f %% against the metric's floor %4.1f %%: %+5.1f points (limit %d); cabin gain %5.2f  %s"
                     % (name, r["nlight"], 100 * r["bright"], 100 * r["light90"], 100 * floor, 100 * r["excess"],
                        round(100 * limit), r["gain"], "ok" if good else "FAIL"))
    return ok, lines


def test4(out, res, cache):
    pts, L0 = depth_map(out, "drive0")
    near = [p for p in pts if p["z"] < NEAR]
    lines = []
    shifts = []
    names = sorted(os.path.basename(f)[:-6] for f in glob.glob(os.path.join(out, "drive[1-9]_L.png")))
    for n in names:
        L1 = load(out, n, "L")
        s = []
        for p in near:
            m = ncc_search(L0, L1, p["x"], p["y"], p["x"], p["y"], 20, 20)
            if m and m[2] > 0.8:
                s.append(math.hypot(m[0] - p["x"], m[1] - p["y"]))
        if s:
            shifts += s
            lines.append("  %-7s %3d cabin patches: median shift %.2f px, 90th pct %.2f px" % (n, len(s), np.median(s), np.percentile(s, 90)))
    # did the car move? the far scenery must have changed
    farchg = []
    for n in names:
        L1 = load(out, n, "L")
        farchg.append(float(np.abs(L1 - L0)[: L0.shape[0] // 2].mean()))
    if not shifts:
        return False, ["  no cabin patches"]
    # The median and the 75th percentile: a few cabin patches hold moving things (the gauges' needles
    # and digits, the hands); shake, lean or bob would move them all.
    med, p75, p90 = (float(np.percentile(shifts, q)) for q in (50, 75, 90))
    rigid = float(np.mean(np.array(shifts) < 1.0))
    moved = max(farchg) > 4 if farchg else False
    # Shake, lean or bob would move every cabin patch: the median stays under 1 px and most patches
    # under 1 px. (The rest: the headliner's light changing as trees pass over the car, the gauges.)
    good = med < 1.0 and rigid > 0.5 and moved
    res["t4"] = {"median_px": med, "p75_px": p75, "p90_px": p90, "rigid_share": rigid, "scenery_change": farchg, "pass": good}
    lines.append("  all: median %.2f px, %.0f %% of cabin patches within 1 px (75th pct %.2f, 90th %.2f px); the scenery changed by %s (the car moved: %s)  %s"
                 % (med, 100 * rigid, p75, p90, ", ".join("%.1f" % c for c in farchg), "yes" if moved else "NO", "ok" if good else "FAIL"))
    return good, lines


# ----------------------------------------------------------------------------------- replay
def replay_metric(out):
    """The left eye's change from each step of the replayed path to the next (mean absolute difference
    at 1/8 size), and the steps whose change is a jump: more than JUMP_RATIO times the median of the 3
    steps before and after it (and over 5), between poses at most 1 cm and 3 degrees apart (a step that
    turns the view across detailed scenery changes it as much as its neighbours do)."""
    fs = sorted(glob.glob(os.path.join(out, "full_*_L.png")))
    prev = None; d = []
    for f in fs:
        im = np.asarray(Image.open(f).convert("L").resize((258, 276), Image.BILINEAR), dtype=np.float32)
        if prev is not None:
            d.append(float(np.abs(im - prev).mean()))
        prev = im
    if not d:
        return d, 0.0, []
    med = float(np.median(d))
    flicks = []
    for i, x in enumerate(d):
        around = d[max(0, i - 3):i] + d[i + 1:i + 4]
        limit = JUMP_RATIO * float(np.median(around))
        if x > max(limit, 5.0):
            flicks.append((i + 1, x, limit))
    return d, med, flicks


def analyze_replay(out):
    d, med, flicks = replay_metric(out)
    if not d:
        print("3 no flick: no replay pictures"); return None
    good = not flicks
    big = int(np.argmax(d))
    around = d[max(0, big - 3):big] + d[big + 1:big + 4]
    print("3 no flick (%s): %d steps of at most 1 cm / 3 deg; change per step median %.1f, largest %.1f (its neighbours %.1f); jumps (%.2f x the neighbours): %d%s  %s"
          % (os.path.basename(out), len(d), med, max(d), float(np.median(around)) if around else 0.0, JUMP_RATIO, len(flicks),
             "".join(" [step %d: %.1f]" % f[:2] for f in flicks[:4]), "PASS" if good else "FAIL"))
    json.dump({"changes": d, "median": med, "flicks": flicks, "pass": good}, open(os.path.join(out, "replay.json"), "w"))
    return good


TRACE = re.compile(r"\[(\S+ \S+)\].*VR trace: head yaw (-?\d+) pitch (-?\d+) roll (-?\d+) deg, x (-?[\d.]+) "
                   r"y (-?[\d.]+) z (-?[\d.]+) cm.*?; shows (.*?);")


def load_trace(path):
    """The head poses of a VR trace (forza.log lines "VR trace: head ..."): time, yaw, pitch, roll
    (degrees), x, y, z (cm: right, up, forward) and what the headset showed."""
    rows = []
    for line in open(path, errors="replace"):
        m = TRACE.search(line)
        if m:
            t, yaw, pitch, roll, x, y, z, shows = m.groups()
            rows.append(dict(t=t[11:23], yaw=int(yaw), pitch=int(pitch), roll=int(roll), x=float(x),
                             y=float(y), z=float(z), shows=shows))
    return rows


def fine_steps(rows, max_cm=1.0, max_deg=3.0):
    """The trace's poses with sub-steps in between, none moving the head more than max_cm or turning
    it more than max_deg: a picture that still jumps between two such close poses is a flick."""
    keys = ("yaw", "pitch", "roll", "x", "y", "z")
    out = [dict((k, rows[0][k]) for k in keys)]
    for r0, r1 in zip(rows, rows[1:]):
        move = math.dist([r0[k] for k in ("x", "y", "z")], [r1[k] for k in ("x", "y", "z")])
        turn = max(abs(r1[k] - r0[k]) for k in ("yaw", "pitch", "roll"))
        n = max(1, int(math.ceil(max(move / max_cm, turn / max_deg))))
        for j in range(1, n + 1):
            f = j / n
            out.append(dict((k, r0[k] + (r1[k] - r0[k]) * f) for k in keys))
    return out


def cmd_replay(a):
    rows = [r for r in load_trace(a.trace) if a.t0 <= r["t"] <= a.t1 and r["shows"].startswith("cockpit")]
    rows = fine_steps(rows[::a.step])
    os.makedirs(a.out, exist_ok=True)
    json.dump({"fine": True, "steps": rows}, open(os.path.join(a.out, "steps.json"), "w"))
    rig = make_rig(a)
    for i, r in enumerate(rows):
        # the trace: x right, y up, z forward (cm); the rigs take OpenXR's z back (m)
        rig.set(yaw=r["yaw"], pitch=r["pitch"], roll=r["roll"], x=r["x"] / 100, y=r["y"] / 100, z=-r["z"] / 100)
        rig.capture(a.out, "full_%03d" % i)
    rig.set()
    analyze_replay(a.out)


# ----------------------------------------------------------------------------------- driver
def cmd_run(a):
    os.makedirs(a.out, exist_ok=True)
    rig = make_rig(a)
    poses = (T1_POSES if set(a.tests) & set("12") else []) + (T2_POSES if "2" in a.tests else [])
    if "q" in a.tests:
        poses = QUICK_POSES
    for name, kv in poses:
        rig.set(); rig.capture(a.out, "b_" + name)
        rig.set(**kv); rig.capture(a.out, name)
    rig.set()
    if "4" in a.tests:
        rig.set(); rig.capture(a.out, "drive0")
        with open(os.path.join(a.rundir, "in.fifo"), "w") as f:
            f.write("RT:4.5\n")
        for i in (1, 2, 3):
            time.sleep(0.9)
            rig.capture(a.out, "drive%d" % i)
        time.sleep(1.5)
        with open(os.path.join(a.rundir, "in.fifo"), "w") as f:
            f.write("LT:4\n")
    cmd_analyze(a)


def cmd_analyze(a):
    global SCALE, PATCH
    if getattr(a, "scale", 2) != 2:
        SCALE, PATCH = a.scale, 10
    res = {}
    summary = []
    tests = a.tests if hasattr(a, "tests") else "124"
    cache = {}
    for t, fn, title in (("1", test1, "1 translation parallax"), ("2", test2, "2 lighting"), ("4", test4, "4 driving")):
        if t not in tests:
            continue
        need = {"1": "b_right6_L.png", "2": "b_*_L.png", "4": "drive0_L.png"}[t]
        if not glob.glob(os.path.join(a.out, need)):
            continue
        ok, lines = fn(a.out, res, cache)
        print("%s: %s" % (title, "PASS" if ok else "FAIL"))
        print("\n".join(lines))
        summary.append((title, ok))
    if os.path.exists(os.path.join(a.out, "replay.json")) or glob.glob(os.path.join(a.out, "full_*_L.png")):
        analyze_replay(a.out)
    json.dump(res, open(os.path.join(a.out, "results%s.json" % ("" if SCALE == 2 else "_s%d" % SCALE)), "w"), indent=1)


def cmd_perf(a):
    pat = re.compile(r"\[(\S+ \S+)\].*VR: (\d+) Hz headset, ([\d.]+) game frames/s copied")
    rows = [m.groups() for m in (pat.search(l) for l in open(a.log, errors="replace")) if m]
    rows = rows[-max(1, int(a.secs // 10)):]
    if not rows:
        print("5 performance: no VR status lines"); return
    hz = [int(r[1]) for r in rows]; fps = [float(r[2]) for r in rows]
    good = min(fps) >= 70 and all(h == 72 for h in hz)
    print("5 performance: %s Hz, game frames copied a second %s (mean %.1f)  %s"
          % ("/".join(sorted(set(map(str, hz)))), " ".join("%.1f" % f for f in fps), sum(fps) / len(fps), "PASS" if good else "FAIL"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--rig", choices=("emu", "headset"), default="emu")
        p.add_argument("rundir"); p.add_argument("out")
        if name == "replay":
            p.add_argument("trace"); p.add_argument("t0"); p.add_argument("t1")
            p.add_argument("--step", type=int, default=3)
        p.add_argument("--tests", default="124")
        p.add_argument("--poses", default=os.path.join(os.environ.get("XREMU", os.path.join(HERE, "..", "xremu2")), "poses.py"),
                       help="the emulated headset's poses.py (default: $XREMU/poses.py)")
        p.add_argument("--fifo"); p.add_argument("--hs")
        p.add_argument("--settle", type=float, default=1.2)
        p.add_argument("--origin", default="", help="k=v,...: added to every pose (a control: x=0.12)")
    p = sub.add_parser("analyze"); p.add_argument("out"); p.add_argument("--tests", default="124")
    p.add_argument("--scale", type=int, default=2,
                   help="compare the pictures at 1/SCALE size (4: the scene's aliasing averaged out)")
    p = sub.add_parser("perf"); p.add_argument("log"); p.add_argument("--secs", type=float, default=30)
    a = ap.parse_args()
    {"run": cmd_run, "replay": cmd_replay, "analyze": cmd_analyze, "perf": cmd_perf}[a.cmd](a)


if __name__ == "__main__":
    main()
