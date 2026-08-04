#!/usr/bin/env python3
"""testimonial_reframe.py — 2D face-PIN keyframed reframe for Brand testimonial ads.

Footage is already 9:16 (rotated 4K), the camera SHAKES, and frames have the foreground guest +
background attendees. Goal (Operator): guest "completely centered, locked X and Y".

HOW (no vidstab — vidstab rubber-banded/swam the frame):
  pass 1: every frame, YuNet-detect, pick the LARGEST face (= foreground guest); reject outlier
          jumps (a stray background pick); interpolate misses.
  smooth: light (median-3 + short avg) — tight enough to FOLLOW the camera shake and PIN the face,
          loose enough to kill per-frame detection noise.
  pass 2: per-frame 9:16 crop centered so the guest's face maps to (0.5, eye_y) EVERY frame ->
          the guest is pinned dead-center, the shake is cancelled, background drifts naturally.
  Render: crop+resize per frame in OpenCV, pipe raw -> ffmpeg h264_videotoolbox, remux source audio.
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
import argparse, subprocess, sys
import cv2, numpy as np

try:  # hardware H.264 args (h264_amf on this machine); libx264 fallback inside the helper
    from fast_encode import hw_h264_args
except Exception:
    def hw_h264_args(bitrate="16M", ffmpeg="ffmpeg"):
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]

MODEL = _acq("horizontal-to-vertical/scripts/yunet.onnx")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--zoom", type=float, default=1.3)
    ap.add_argument("--eye-y", type=float, default=0.40)
    ap.add_argument("--detw", type=int, default=720); ap.add_argument("--score", type=float, default=0.5)
    ap.add_argument("--smooth", type=int, default=5)
    ap.add_argument("--ow", type=int, default=1080); ap.add_argument("--oh", type=int, default=1920)
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.source)
    W = int(cap.get(3)); H = int(cap.get(4)); fps = cap.get(5) or 30.0
    N = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dw = min(a.detw, W); sc = dw / W; dh = int(H * sc)
    det = cv2.FaceDetectorYN.create(MODEL, "", (dw, dh), a.score, 0.3, 5000)

    # ---- pass 1: detect largest face center per frame (source px) ----
    cxs = np.full(N, np.nan); cys = np.full(N, np.nan); i = 0
    while True:
        ok, fr = cap.read()
        if not ok or i >= N: break
        small = cv2.resize(fr, (dw, dh))
        _, faces = det.detect(small)
        if faces is not None and len(faces):
            f = max(faces, key=lambda f: f[2] * f[3])      # largest = foreground guest
            if f[-1] >= a.score:
                cxs[i] = (f[0] + f[2] / 2) / sc
                cys[i] = (f[1] + f[3] / 2) / sc
        i += 1
    cap.release()
    N = i if i < N else N
    cxs, cys = cxs[:N], cys[:N]

    # ---- outlier rejection: drop frames whose center jumps far from the rolling median ----
    def reject(arr, frac):
        med = np.nanmedian(arr); thr = frac * (W if arr is cxs else H)
        bad = np.abs(arr - med) > thr
        arr[bad] = np.nan
        return arr
    reject(cxs, 0.18); reject(cys, 0.18)

    idx = np.arange(N)
    for arr in (cxs, cys):
        good = ~np.isnan(arr)
        if good.sum() == 0:
            arr[:] = (W / 2 if arr is cxs else H * 0.45)
        else:
            arr[~good] = np.interp(idx[~good], idx[good], arr[good])
    hits = int((~np.isnan(np.where(np.abs(cxs - np.nanmedian(cxs)) <= 1e9, cxs, np.nan))).sum())

    # ---- light smooth: median-3 then short moving-average (FOLLOW the shake, kill noise) ----
    def smooth(arr, win):
        win = max(1, win | 1)
        m = np.array([np.median(arr[max(0, k - 1):k + 2]) for k in range(len(arr))])
        if win <= 1: return m
        pad = np.pad(m, (win // 2, win // 2), mode="edge")
        return np.convolve(pad, np.ones(win) / win, mode="valid")[:len(arr)]
    cxs = smooth(cxs, a.smooth); cys = smooth(cys, a.smooth)

    # ---- crop geometry (9:16), per-frame top-left, clamped ----
    cw = W / a.zoom; ch = cw * a.oh / a.ow
    if ch > H:
        ch = H; cw = ch * a.ow / a.oh
    cw_i, ch_i = int(round(cw)), int(round(ch))
    x0 = np.clip(cxs - cw / 2.0, 0, W - cw).astype(int)
    y0 = np.clip(cys - a.eye_y * ch, 0, H - ch).astype(int)
    print(f"src {W}x{H} N={N} | guest medianC=({np.median(cxs)/W:.3f},{np.median(cys)/H:.3f}) "
          f"| crop {cw_i}x{ch_i} | x-range {x0.min()}-{x0.max()} y-range {y0.min()}-{y0.max()}", flush=True)

    # ---- pass 2: render per-frame crop -> ffmpeg pipe, remux source audio ----
    fr_str = f"{int(round(fps*1000))}/1000"
    ff = subprocess.Popen([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{a.ow}x{a.oh}", "-r", fr_str, "-i", "-",
        "-i", a.source, "-map", "0:v", "-map", "1:a?",
        "-r", fr_str, "-vsync", "cfr", *hw_h264_args("16M"),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-shortest", a.out], stdin=subprocess.PIPE)
    cap = cv2.VideoCapture(a.source); k = 0
    while k < N:
        ok, fr = cap.read()
        if not ok: break
        crop = fr[y0[k]:y0[k] + ch_i, x0[k]:x0[k] + cw_i]
        out = cv2.resize(crop, (a.ow, a.oh), interpolation=cv2.INTER_AREA)
        ff.stdin.write(out.tobytes()); k += 1
    cap.release(); ff.stdin.close(); ff.wait()
    print("->", a.out, "rc", ff.returncode)

if __name__ == "__main__":
    main()
