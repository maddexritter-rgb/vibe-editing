"""
# LEGACY 2026-06-08 — kept only because shortform/pipeline.py + qa_detect_speaker.py still import it. NEW CODE: use qa_reframe_v2.py --preset <name> (Y-LOCK + xcenter box). This script is NOT the canonical face-tracker.
Reframe horizontal 16:9 source → vertical 9:16 with face-tracked X + Y-locked positioning.
- X axis: per-frame smoothed face_cx (face follows speaker as they move)
- Y axis: locked to median face_cy (no bobbing)
- Target nose position in output is configurable (default centered at 540, 719 in 1080x1920 ref)
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
import argparse, json, sys, subprocess, statistics, os
from pathlib import Path
import cv2, numpy as np

# Brand FAST-RENDER STANDARD — VideoToolbox HW encode (~4x faster than libx264).
sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args as _enc_args_full
def _enc_args(w, h, *, tier="delivery"): return _enc_args_full(w, h, "ffmpeg", tier=tier)

def smooth_curve(values, window=21):
    vals = np.array(values, dtype=np.float64)
    if len(vals) <= window:
        return vals
    pad = window // 2
    padded = np.pad(vals, pad, mode='edge')
    kern = np.ones(window) / window
    return np.convolve(padded, kern, mode='valid')

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--face-json", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--out-w", type=int, default=2160)
    p.add_argument("--out-h", type=int, default=3840)
    p.add_argument("--zoom", type=float, default=1.15,
                   help="Zoom factor. 1.0 = full source height covers output. >1.0 zooms in.")
    p.add_argument("--smooth", type=int, default=51,
                   help="X box-car smoothing window in frames. 51 = locked glassy default.")
    p.add_argument("--lock-x", action="store_true",
                   help="Lock X static to the median (no per-frame tracking). Smoothest for short, "
                        "near-static per-segment crops of a seated talker — kills all tracking jitter.")
    p.add_argument("--eye-y-src", type=float, default=None,
                   help="EXPLICIT source eye-line position (fraction 0..1 of src height). Overrides the "
                        "detected median Y anchor — caller measures the eye-line robustly per segment and "
                        "we lock it to --eye-y-out, so eyes sit at the SAME output level on every cut.")
    p.add_argument("--eye-y-out", type=float, default=566.0,
                   help="Output eye-line target in the 1920-tall ref (default 566 ≈ 0.295). Used with --eye-y-src.")
    # Target NOSE position in 1080x1920 reference coords (will be scaled to actual output dims)
    p.add_argument("--nose-x-1080", type=float, default=540, help="Target nose X in 1080-wide ref")
    p.add_argument("--nose-y-1080", type=float, default=719, help="Target nose Y in 1920-tall ref")
    # Nose offset from face_cy in source (small positive value — nose is below bbox center)
    p.add_argument("--nose-offset-pct", type=float, default=0.05,
                   help="Nose Y offset as fraction of face bbox height below face_cy")
    # Anchor source — what point in the face curve to track on
    p.add_argument("--anchor", default="nose",
                   choices=["nose", "left_eye", "right_eye"],
                   help="Anchor point in face curve. 'left_eye'/'right_eye' require track_face_mesh.py output (image-space convention).")
    args = p.parse_args()

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS)
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"src {src_w}x{src_h} @ {fps:.3f} fps, {nframes} frames", file=sys.stderr)

    # Load face curve. Pick the X-anchor source per --anchor option.
    fd = json.load(open(args.face_json))
    curve = fd["curve"]
    confs = [p.get("conf", 0) for p in curve]
    # X anchor source (per-frame tracked)
    if args.anchor == "left_eye":
        x_key = "left_eye_cx"
    elif args.anchor == "right_eye":
        x_key = "right_eye_cx"
    else:
        x_key = "face_cx"
    print(f"X anchor: {args.anchor} (key={x_key})", file=sys.stderr)
    fxs_raw = [p.get(x_key, p.get("face_cx", src_w/2)) for p in curve]
    fys_raw = [p.get("face_cy", src_h/2) for p in curve]
    fhs_raw = [p.get("face_h", 200) for p in curve]
    valid = [(x, y, h) for x, y, h, c in zip(fxs_raw, fys_raw, fhs_raw, confs) if c > 0.3]
    med_x = statistics.median([v[0] for v in valid]) if valid else src_w/2
    med_y = statistics.median([v[1] for v in valid]) if valid else src_h/2
    med_h = statistics.median([v[2] for v in valid]) if valid else 200
    fxs = [x if c > 0.3 else med_x for x, c in zip(fxs_raw, confs)]
    if args.lock_x:
        sx = np.full(len(fxs), med_x)
        print(f"X LOCKED static: {med_x:.0f} (no per-frame tracking)", file=sys.stderr)
    else:
        sx = smooth_curve(fxs, args.smooth)
        print(f"X smoothed: {min(sx):.0f}-{max(sx):.0f}  (per-frame tracking)",
              file=sys.stderr)
    # Y is LOCKED — single fixed value used for every frame.
    if args.eye_y_src is not None:
        # EYE-LOCK: caller passed a robustly-measured source eye-line -> lock it to a fixed output level.
        nose_y_src = args.eye_y_src * src_h
        print(f"Y EYE-LOCK: src eye-line {nose_y_src:.0f} ({args.eye_y_src:.3f}) -> out {args.eye_y_out:.0f}", file=sys.stderr)
    else:
        nose_y_src = med_y + args.nose_offset_pct * med_h
        print(f"Y LOCKED: {nose_y_src:.0f}", file=sys.stderr)

    # Crop region: 9:16 aspect at given zoom
    out_aspect = args.out_w / args.out_h
    crop_h = int(round(src_h / args.zoom))
    crop_w = int(round(crop_h * out_aspect))
    print(f"crop region: {crop_w}x{crop_h} @ zoom {args.zoom}x", file=sys.stderr)

    # Compute target output position (nose anchor, or eye anchor under eye-lock)
    _y_target_1080 = args.eye_y_out if args.eye_y_src is not None else args.nose_y_1080
    target_out_y = _y_target_1080 * (args.out_h / 1920.0)
    target_out_x = args.nose_x_1080 * (args.out_w / 1080.0)
    nose_in_crop_y = target_out_y * (crop_h / args.out_h)
    nose_in_crop_x = target_out_x * (crop_w / args.out_w)
    # Compute LOCKED crop_y0 once (Y axis never moves)
    crop_y0_locked = nose_y_src - nose_in_crop_y
    crop_y0_locked = max(0, min(src_h - crop_h, crop_y0_locked))
    print(f"target nose output: ({target_out_x:.0f}, {target_out_y:.0f}) in {args.out_w}x{args.out_h}",
          file=sys.stderr)
    print(f"crop_y0 (LOCKED, every frame uses this): {crop_y0_locked:.0f}", file=sys.stderr)

    # ffmpeg encoder pipe
    ff = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{args.out_w}x{args.out_h}",
        "-r", f"{fps:.6f}",
        "-i", "-",
        "-i", args.video,
        "-map", "0:v", "-map", "1:a?",
        # Brand fast-render: VideoToolbox HW encode (~4x faster than libx264 -crf14).
        # tier=intermediate because this is the reframed master that downstream caption/grade
        # steps will re-encode anyway; protect against generational loss with generous bitrate.
        *_enc_args(args.out_w, args.out_h, tier="intermediate"),
        "-c:a", "aac", "-b:a", "320k",
        "-movflags", "+faststart",
        args.output,
    ]
    proc = subprocess.Popen(ff, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        i = min(frame_idx, len(sx) - 1)
        # X axis: per-frame face-tracked (keeps face at output x = target_out_x)
        cx = float(sx[i])
        crop_x0 = cx - nose_in_crop_x
        crop_x0 = max(0, min(src_w - crop_w, crop_x0))
        # Y axis: LOCKED (no tracking) — use the single computed crop_y0_locked
        x0i, y0i = int(round(crop_x0)), int(round(crop_y0_locked))
        cw_i = min(crop_w, src_w - x0i)
        ch_i = min(crop_h, src_h - y0i)
        cropped = frame[y0i:y0i+ch_i, x0i:x0i+cw_i]
        if cropped.size == 0:
            cropped = frame
        resized = cv2.resize(cropped, (args.out_w, args.out_h),
                             interpolation=cv2.INTER_LANCZOS4)
        try:
            proc.stdin.write(resized.tobytes())
        except BrokenPipeError:
            break
        frame_idx += 1

    cap.release()
    try:
        proc.stdin.close()
    except Exception:
        pass
    rc = proc.wait()
    if rc != 0:
        err = proc.stderr.read().decode() if proc.stderr else ""
        print(f"ffmpeg exit {rc}\n{err[-1500:]}", file=sys.stderr)
        sys.exit(rc)
    print(f"rendered {frame_idx} frames → {args.output}", file=sys.stderr)

if __name__ == "__main__":
    main()
