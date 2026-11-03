"""Compare decoded luma frames with a reference: framecmp.py ours.raw ref.raw width height

Aligns our first frame to the reference frame it matches, then reports, per
frame, the share of pixels within +-2 of the reference and the first
mismatching 8x8 block.
"""
import sys

ours_path, ref_path, w, h = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
size = w * h
ours = open(ours_path, "rb").read()
ref = open(ref_path, "rb").read()
n_ours, n_ref = len(ours) // size, len(ref) // size


def frame(buf, i):
    return buf[i * size:(i + 1) * size]


def score(a, b):
    # Sampled: every 7th pixel.
    close = sum(1 for i in range(0, size, 7) if abs(a[i] - b[i]) <= 2)
    return close / len(range(0, size, 7))


first = frame(ours, 0)
offset = max(range(min(n_ref, 30)), key=lambda j: score(first, frame(ref, j)))
print("our frame 0 matches reference frame", offset)
j = offset
for i in range(n_ours):
    a = frame(ours, i)
    # Our dump may repeat a frame (double buffering): match the closer of j, j+1.
    best = max((j, j + 1), key=lambda k: score(a, frame(ref, k)) if k < n_ref else -1)
    b = frame(ref, best)
    s = score(a, b)
    bad = ""
    if s < 0.98:
        for by in range(0, h, 8):
            for bx in range(0, w, 8):
                if any(abs(a[(by + y) * w + bx + x] - b[(by + y) * w + bx + x]) > 8 for y in range(8) for x in range(8)):
                    bad = "first bad block (%d,%d)" % (bx, by)
                    break
            if bad:
                break
    print("ours %3d ~ ref %3d: %.3f %s" % (i, best, s, bad))
    j = best
