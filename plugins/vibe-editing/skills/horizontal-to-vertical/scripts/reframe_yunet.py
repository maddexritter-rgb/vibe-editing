#!/usr/bin/env python3
# LEGACY 2026-06-08 — kept only because shortform/pipeline.py + qa_detect_speaker.py still import it. NEW CODE: use qa_reframe_v2.py --preset <name> (Y-LOCK + xcenter box). This script is NOT the canonical face-tracker.
"""Robust 16:9 -> 9:16 reframe using the YuNet DNN face detector (far more reliable than Haar on
bearded/cap/busy-background sets). Per-frame nose landmark -> median-filter -> moving-average smooth
-> crop pinned so the NOSE sits at frame center, Y full-height (zoom 1.0), Lanczos resize to 2160x3840.
Usage: reframe_yunet.py INPUT OUTPUT [--zoom 1.0] [--smooth 41] [--model /tmp/yunet.onnx]"""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
import os as _os, sys as _sys
import pathlib as _pl
def _acq_root():
    r = _os.environ.get("VIBE_PIPELINE_ROOT") or _os.environ.get("CLAUDE_PLUGIN_ROOT")
    if r and _os.path.isdir(_os.path.join(r, ".claude-plugin")):
        return r
    d = _os.path.dirname(_os.path.abspath(__file__))
    while d != _os.path.dirname(d):
        if _os.path.isdir(_os.path.join(d, ".claude-plugin")):
            return d
        d = _os.path.dirname(d)
    return _os.path.dirname(_os.path.abspath(__file__))
VIBE_ROOT = _acq_root()
VIBE_SHARED = _os.path.join(VIBE_ROOT, "lib", "_shared")
VIBE_SKILLS = _os.path.join(VIBE_ROOT, "skills")
VIBE_VAULT  = _os.path.join(VIBE_ROOT, "vault")
VIBE_ASSETS = _os.environ.get("VIBE_ASSETS") or _os.path.join(VIBE_ROOT, "assets")
def _acq(p):
    parts = [x for x in str(p).strip("/").split("/") if x]
    if parts and parts[0] == "_shared":
        return _pl.Path(_os.path.join(VIBE_ROOT, "lib", *parts))
    return _pl.Path(_os.path.join(VIBE_SKILLS, *parts))
def _acqv(p):
    return _pl.Path(_os.path.join(VIBE_VAULT, *[x for x in str(p).strip("/").split("/") if x]))
if VIBE_SHARED not in _sys.path:
    _sys.path.insert(0, VIBE_SHARED)
# ── end bootstrap ──
import cv2, numpy as np, subprocess, argparse, sys, os
sys.path.insert(0, VIBE_SHARED); from fast_encode import encoder_args

ap = argparse.ArgumentParser()
ap.add_argument("inp"); ap.add_argument("out")
ap.add_argument("--zoom", type=float, default=1.0)
ap.add_argument("--smooth", type=int, default=41)
ap.add_argument("--detw", type=int, default=960)
ap.add_argument("--face-y", type=float, default=0.30, help="pin the median nose at this fraction of output height (Y-lock); LOWER = face higher in frame")
ap.add_argument("--pick", choices=["score", "area", "anchor"], default="score",
                help="multi-face subject selection: score (default) | area (largest) | anchor (lock to the dominant-position face, ignore background people)")
ap.add_argument("--anchor-x", type=float, default=None,
                help="force the anchor x (fraction 0-1) instead of auto-detecting it. Use with --pick anchor to lock onto a specific person amid others (e.g. the speaker at the board, ignoring audience). Frames with no face near it HOLD at this x.")
ap.add_argument("--min-score", type=float, default=0.5,
                help="YuNet detection confidence floor; raise (~0.6) to reject hand/background false-positives on busy/gesturing stage footage")
ap.add_argument("--static", action="store_true",
                help="fully LOCK the crop — pin the nose to ONE constant (x,y) for the whole clip (no per-frame X follow). Best for seated speakers who gesture a lot and throw off the tracker.")
ap.add_argument("--model", default=_acq("horizontal-to-vertical/scripts/yunet.onnx"))
# VIBE_RES knob — default = 4K. Set --res 1080/720 (or env VIBE_RES) for fast iteration / proxies.
ap.add_argument("--res", default=os.environ.get("VIBE_RES", "4k"), choices=["720", "1080", "4k"])
a = ap.parse_args()

cap = cv2.VideoCapture(a.inp)
W = int(cap.get(3)); H = int(cap.get(4)); fps = cap.get(5) or 30.0
dw = a.detw; sc = dw / W; dh = int(H * sc)
det = cv2.FaceDetectorYN.create(a.model, "", (dw, dh), a.min_score, 0.3, 5000)

# pass 1: collect ALL faces per frame as (nose_x, nose_y, score, area) in source px
allf = []
while True:
    ok, fr = cap.read()
    if not ok: break
    small = cv2.resize(fr, (dw, dh)); _, faces = det.detect(small)
    fl = []
    if faces is not None and len(faces):
        for f in faces:
            fl.append((float(f[8]) / sc, float(f[9]) / sc, float(f[14]), float(f[2] * f[3]) / (sc * sc)))
    allf.append(fl)
cap.release()
# subject selection. 'anchor' = lock to the dominant-position face (median nose of the
# per-frame best-score face) and IGNORE background people AND waving hands; a frame with no
# face in the locked box -> NaN (interpolated) rather than jumping the crop to a bystander/hand.
best = [max(fl, key=lambda t: t[2]) for fl in allf if fl]   # per-frame best-score face
bx = [b[0] for b in best]
anchor_x = (a.anchor_x * W) if a.anchor_x is not None else (float(np.median(bx)) if bx else W / 2.0)
band = 0.13 * W; bandy = 0.18 * H
# anchor_y = the locked face's vertical band. With a forced anchor_x, derive it from faces near
# that x (so a crowd's median can't drag it); else from the per-frame best faces. A Y-band plus
# a highest-score pick is what rejects a hand gesturing up near the face (lower score, off-band y).
if a.anchor_x is not None:
    nby = [t[1] for fl in allf for t in fl if abs(t[0] - anchor_x) < band]
    anchor_y = float(np.median(nby)) if nby else H / 2.0
else:
    anchor_y = float(np.median([b[1] for b in best])) if best else H / 2.0
xs = []; ys = []
for fl in allf:
    if not fl:
        # no face this frame: hold at forced anchor (lock mode) or interpolate (auto)
        xs.append(anchor_x if a.anchor_x is not None else np.nan); ys.append(np.nan); continue
    if a.pick == "area":
        p = max(fl, key=lambda t: t[3]); xs.append(p[0]); ys.append(p[1])
    elif a.pick == "anchor":
        # in-box = near the anchor in BOTH x and y; among those pick the MOST face-like (highest
        # score). A waving hand detected near his face in x but lower/off in y, or with a weaker
        # score, can no longer steal the crop.
        near = [t for t in fl if abs(t[0] - anchor_x) < band and abs(t[1] - anchor_y) < bandy]
        if near:
            p = max(near, key=lambda t: t[2]); xs.append(p[0]); ys.append(p[1])
        else:  # only far faces / hands -> hold at forced anchor, else interpolate
            xs.append(anchor_x if a.anchor_x is not None else np.nan); ys.append(np.nan)
    else:  # score
        p = max(fl, key=lambda t: t[2]); xs.append(p[0]); ys.append(p[1])
if a.pick == "anchor":
    print(f"anchor-lock: nose ({anchor_x/W:.3f},{anchor_y/H:.3f}) box ±({band/W:.2f},{bandy/H:.2f}) min-score {a.min_score} | faces/frame max {max((len(fl) for fl in allf), default=0)}", flush=True)
xs = np.array(xs, float); n = len(xs)
idx = np.arange(n); good = ~np.isnan(xs)
if good.sum() == 0: sys.exit("no faces detected")
xs = np.interp(idx, idx[good], xs[good])
# median filter (kill single-frame spikes)
mw = 5; xm = xs.copy()
for i in range(n):
    lo = max(0, i - mw // 2); hi = min(n, i + mw // 2 + 1); xm[i] = np.median(xs[lo:hi])
# moving-average smooth
win = max(3, a.smooth | 1); k = np.ones(win) / win
xpad = np.pad(xm, (win // 2, win // 2), mode="edge")
xss = np.convolve(xpad, k, mode="valid")[:n]
print(f"{W}x{H} {fps:.1f}fps {n}f | nose_x range {xss.min()/W:.3f}-{xss.max()/W:.3f} (mean {xss.mean()/W:.3f})", flush=True)
if a.static:                       # LOCK X to a single constant (median of the face-tracked positions); Y is already median-locked -> fully fixed crop
    xc = float(np.median(xss)); xss = np.full(n, xc)
    print(f"  STATIC lock -> nose_x {xc/W:.3f} constant (gestures can't move the crop)", flush=True)
ys = np.array(ys, float); goody = ~np.isnan(ys)
nose_y_med = float(np.median(ys[goody])) if goody.any() else H / 2.0

cropH = min(H, int(round(H / a.zoom))); cropW = min(W, int(round(cropH * 9 / 16)))
# Y-LOCK: pin the (median) nose at face_y of the output height -> face sits high, captions clear the chin.
y0 = int(round(nose_y_med - a.face_y * cropH)); y0 = max(0, min(H - cropH, y0))
print(f"  Y-lock: nose_y_med {nose_y_med/H:.3f} -> y0 {y0} (face_y {a.face_y})", flush=True)
# Output resolution from --res / VIBE_RES env. 4K=2160x3840 (final), 1080=1080x1920, 720=720x1280 (proxy).
OW, OH = {"4k": (2160, 3840), "1080": (1080, 1920), "720": (720, 1280)}[a.res]
ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
    "-s", f"{OW}x{OH}", "-r", f"{fps}", "-i", "-", "-i", a.inp, "-map", "0:v", "-map", "1:a?",
    *encoder_args(OW, OH, "ffmpeg", tier="intermediate"),
    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
cap = cv2.VideoCapture(a.inp)
for i in range(n):
    ok, fr = cap.read()
    if not ok: break
    x0 = int(round(xss[i] - cropW / 2)); x0 = max(0, min(W - cropW, x0))
    crop = fr[y0:y0 + cropH, x0:x0 + cropW]
    ff.stdin.write(cv2.resize(crop, (OW, OH), interpolation=cv2.INTER_LANCZOS4).tobytes())
ff.stdin.close(); ff.wait(); cap.release()
print("reframe_yunet ->", a.out)
