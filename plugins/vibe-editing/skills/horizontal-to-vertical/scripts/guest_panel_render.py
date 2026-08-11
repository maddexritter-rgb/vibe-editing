#!/usr/bin/env python3
"""Dynamic TARGET-FRAMING guest split-panel renderer (2026-06-17).

Kills the "guest too big / too low / too high" bug class for good. Instead of a FIXED zoom +
eye + panel_y (which lands differently for every guest size/height/camera position — the bug
Operator hit ~10× on Guest), this takes TWO UNIVERSAL TARGETS and solves the crop PER FRAME:

  --target-face-h 0.34   guest's face = 34% of the panel HEIGHT  (constant size, any guest)
  --target-face-y 0.34   face CENTER sits 34% from the panel TOP (constant position)

Per frame it detects the guest's face (YuNet, ROI-guarded against seated audience), then:
  scale   = target_face_h * PANEL_H / detected_face_height     # face is ALWAYS 34% of panel
  crop_w  = PANEL_W / scale ; crop_h = PANEL_H / scale         # crop at PANEL aspect (not 9:16)
  x0      = face_cx - crop_w/2                                 # centered on the face
  y0      = face_cy - target_face_y * PANEL_H / scale          # face at 34% from top
Crops the CCAM directly at the panel's aspect (wider than 9:16 → no letterbox, full freedom),
scales to PANEL_W×PANEL_H. SIZE is constant per camera-segment (from the MEDIAN detected face
height → no per-frame "breathing"); POSITION tracks per-frame (so a camera pan/tilt up/down just
moves the framed crop — the guest stays locked at 34%/34%).

Output: a PANEL_W×PANEL_H video (default 2160×1920 = one half of a 4K split). qa_assembly vstacks
it under the Speaker top panel.

Usage:
  guest_panel_render.py IN OUT --target-face-h 0.34 --target-face-y 0.34 \
      --roi "0.05 0.00 0.55 0.60" [--cut-frames f1,f2] [--panel-w 2160 --panel-h 1920]
"""
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
import cv2, numpy as np, subprocess, sys, os, argparse, glob

def find_bin(name):
    c = sorted(glob.glob(f"/opt/homebrew/Cellar/ffmpeg-full/*/bin/{name}"), reverse=True)
    return c[0] if c else name
FF = find_bin("ffmpeg")
sys.path.insert(0, VIBE_SHARED)
try:
    from fast_encode import encoder_args
except Exception:
    def encoder_args(w, h, ff, tier="delivery"): return ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]

ap = argparse.ArgumentParser()
ap.add_argument("inp"); ap.add_argument("out")
ap.add_argument("--target-face-h", type=float, default=0.34, dest="tfh")
ap.add_argument("--target-face-y", type=float, default=0.34, dest="tfy")
ap.add_argument("--roi", default="0.05 0.00 0.55 0.60")
ap.add_argument("--panel-w", type=int, default=2160)
ap.add_argument("--panel-h", type=int, default=1920)
ap.add_argument("--cut-frames", default=None)
ap.add_argument("--smooth", type=int, default=41)
ap.add_argument("--detw", type=int, default=1280)
ap.add_argument("--score", type=float, default=0.3)
ap.add_argument("--face-conf", type=float, default=0.5, dest="face_conf")
ap.add_argument("--model", default=_acq("horizontal-to-vertical/scripts/yunet.onnx"))
ap.add_argument("--fps", default="30000/1001")
a = ap.parse_args()
PANEL_W, PANEL_H = a.panel_w, a.panel_h
roi = [float(x) for x in a.roi.split()]
cut_frames = set(int(x) for x in a.cut_frames.split(",")) if a.cut_frames else set()

cap = cv2.VideoCapture(a.inp)
W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
dw = min(a.detw, W); sc = dw / W; dh = int(H * sc)
det = cv2.FaceDetectorYN.create(a.model, "", (dw, dh), a.score, 0.3, 5000)
rx0, ry0, rx1, ry1 = roi
R = (rx0*dw, ry0*dh, rx1*dw, ry1*dh)

cxs, cys, fhs, seg_ids = [], [], [], []
fb_cx, fb_cy, fb_fh = [], [], []          # ROI-RELAXED full-frame fallback per frame. Used ONLY to recover
                                          # undetected leading/trailing boundary frames — the leaning-to-mic OPEN
                                          # where the guest's head drops BELOW the ROI height band and detection
                                          # misses him (the t=0 "headless guest" bug). Audience-guard is preserved
                                          # at apply-time by an X-proximity gate to the nearest KNOWN guest X.
last_x, cur_seg, n = None, 0, 0
while True:
    ok, fr = cap.read()
    if not ok: break
    if n in cut_frames and n > 0:
        last_x = None; cur_seg += 1
    small = cv2.resize(fr, (dw, dh))
    _, faces = det.detect(small)
    cand, allf = [], []
    if faces is not None:
        for f in faces:
            if f[-1] < a.face_conf: continue
            allf.append(f)                                 # any confident face (no ROI restriction)
            cx, cy = f[0]+f[2]/2, f[1]+f[3]/2
            if R[0] <= cx <= R[2] and R[1] <= cy <= R[3]:
                cand.append(f)                             # ROI-valid (the normal path)
    pick = None
    if cand:
        if last_x is not None:
            pick = min(cand, key=lambda f: abs((f[0]+f[2]/2) - last_x))
            if abs((pick[0]+pick[2]/2) - last_x) > 0.15*dw:
                pick = max(cand, key=lambda f: f[2]*f[3]*f[-1])
        else:
            pick = max(cand, key=lambda f: f[2]*f[3]*f[-1])
    fbpick = None                                          # ROI-relaxed fallback for THIS frame
    if allf:
        fbpick = (min(allf, key=lambda f: abs((f[0]+f[2]/2) - last_x)) if last_x is not None
                  else max(allf, key=lambda f: f[2]*f[3]*f[-1]))
    if fbpick is not None:
        fb_cx.append((fbpick[0]+fbpick[2]/2)/sc); fb_cy.append((fbpick[1]+fbpick[3]/2)/sc); fb_fh.append(fbpick[3]/sc)
    else:
        fb_cx.append(np.nan); fb_cy.append(np.nan); fb_fh.append(np.nan)
    if pick is not None:
        cxs.append((pick[0]+pick[2]/2)/sc); cys.append((pick[1]+pick[3]/2)/sc); fhs.append(pick[3]/sc)
        last_x = pick[0]+pick[2]/2
    else:
        cxs.append(np.nan); cys.append(np.nan); fhs.append(np.nan)
    seg_ids.append(cur_seg); n += 1
cap.release()
fb_cx, fb_cy, fb_fh = np.array(fb_cx,float), np.array(fb_cy,float), np.array(fb_fh,float)

cxs, cys, fhs = np.array(cxs,float), np.array(cys,float), np.array(fhs,float)
seg_ids = np.array(seg_ids); n = len(cxs); idx = np.arange(n)
good = ~np.isnan(cxs)
if good.sum() == 0:
    sys.exit("ERROR: no guest face detected in ROI across the whole range — widen ROI or check the cam")
nseg = int(seg_ids.max())+1 if n else 1
# RECOVER undetected boundary frames (open/close) from the ROI-relaxed fallback, anchored to the guest's nearest
# KNOWN x (±0.18*W → never lock onto a seated-audience face). Fixes the "guest out of frame at t=0" bug: at the
# open he leans to the mic, his head drops below the ROI height band, detection misses him, and the crop would
# back-fill from a LATER (sat-up) pose so his real head fell below the panel. Now we frame his ACTUAL face.
n_recov = 0
for s in range(nseg):
    seg_abs = np.where(seg_ids == s)[0]
    if seg_abs.size == 0: continue
    g_abs = seg_abs[good[seg_abs]]
    if g_abs.size == 0 or g_abs.size == seg_abs.size: continue
    first_g, last_g = g_abs[0], g_abs[-1]
    for j in seg_abs:
        anchor = cxs[first_g] if j < first_g else (cxs[last_g] if j > last_g else None)
        if anchor is None: continue
        if not np.isnan(fb_cx[j]) and abs(fb_cx[j] - anchor) < 0.18 * W:
            cxs[j], cys[j], fhs[j], good[j] = fb_cx[j], fb_cy[j], fb_fh[j], True; n_recov += 1
if n_recov:
    print(f"  recovered {n_recov} undetected boundary frame(s) via ROI-relaxed fallback (leaning-to-mic open/close)", flush=True)
# OPEN-FRAME GUARD: the first frame of each segment must be a REAL or RECOVERED detection. A pure back-fill at the
# open means the guest was never located there → almost certainly out of frame (the t=0 headless bug). Loud,
# parseable line so qa_assembly's split gate can BLOCK on it.
for s in range(nseg):
    seg_abs = np.where(seg_ids == s)[0]
    if seg_abs.size and not good[seg_abs[0]]:
        print(f"  [guest_panel][OPEN-FAIL] seg{s}: guest face NOT detected/recovered at the open frame "
              f"({int(seg_abs[0])}) — crop back-filled from a later pose, guest likely OUT OF FRAME. "
              f"Widen --roi (lower the bottom of the height band) or check the cam angle.", flush=True)
# interpolate gaps per-segment
for s in range(nseg):
    m = seg_ids == s; gi = np.where(m)[0]; gm = good[m]
    if gm.sum() == 0:
        cxs[m]=np.nanmedian(cxs[good]); cys[m]=np.nanmedian(cys[good]); fhs[m]=np.nanmedian(fhs[good])
    elif gm.sum() < m.sum():
        for arr in (cxs, cys, fhs):
            arr[m] = np.interp(gi, gi[gm], arr[m][gm])

def smooth(arr, win):
    if win < 3 or len(arr) < win: return arr
    win = win | 1; pad = win//2
    k = np.ones(win)/win
    return np.convolve(np.pad(arr, pad, mode="edge"), k, mode="valid")

# POSITION: smooth per-frame (tracks camera). SIZE: ONE scale per segment from MEDIAN fh (no breathing).
cxs_s = np.copy(cxs); cys_s = np.copy(cys)
seg_scale = {}
for s in range(nseg):
    m = seg_ids == s
    cxs_s[m] = smooth(cxs[m], a.smooth); cys_s[m] = smooth(cys[m], a.smooth)
    med_fh = float(np.median(fhs[m]))
    seg_scale[s] = (a.tfh * PANEL_H) / max(med_fh, 1.0)

print(f"guest_panel_render: {W}x{H} -> {PANEL_W}x{PANEL_H}, {n} frames, {nseg} seg(s), "
      f"target face_h={a.tfh:.2f} face_y={a.tfy:.2f}", flush=True)
for s in range(nseg):
    m = seg_ids==s; med=float(np.median(fhs[m]))
    print(f"  seg{s}: median face_h={med:.0f}px ({med/H*100:.1f}% src) -> scale={seg_scale[s]:.3f} "
          f"=> face will be {a.tfh*100:.0f}% of panel", flush=True)

ff = subprocess.Popen([FF, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
    "-s", f"{PANEL_W}x{PANEL_H}", "-r", a.fps, "-i", "-", "-an",
    *encoder_args(PANEL_W, PANEL_H, FF, tier="intermediate"),
    "-movflags", "+faststart", a.out], stdin=subprocess.PIPE)
cap = cv2.VideoCapture(a.inp)
for i in range(n):
    ok, fr = cap.read()
    if not ok: break
    scale = seg_scale[seg_ids[i]]
    crop_w = PANEL_W/scale; crop_h = PANEL_H/scale
    # if the guest is too big to reach target size, the crop would exceed source — clamp (rare)
    crop_w = min(crop_w, W); crop_h = min(crop_h, H)
    x0 = cxs_s[i] - crop_w/2
    y0 = cys_s[i] - a.tfy*PANEL_H/scale
    x0 = int(round(max(0, min(W-crop_w, x0))))
    y0 = int(round(max(0, min(H-crop_h, y0))))
    cw, ch = int(round(crop_w)), int(round(crop_h))
    cw = min(cw, W-x0); ch = min(ch, H-y0)
    sub = fr[y0:y0+ch, x0:x0+cw]
    panel = cv2.resize(sub, (PANEL_W, PANEL_H), interpolation=cv2.INTER_CUBIC)
    ff.stdin.write(panel.tobytes())
ff.stdin.close(); ff.wait()
cap.release()
print(f"  wrote {a.out}", flush=True)
