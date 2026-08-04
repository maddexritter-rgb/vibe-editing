#!/usr/bin/env python3
"""Stack Speaker (top) + Guest (bottom) into 1080x1920 split-screen with a CLEAN soft seam.

Per the Speaker Q&A SF Visual Guide (page 2): "Split screen 50/50: Speaker above, Guest
below. Same proportions. Add drop shadow to Speaker's angle."

Inputs are reframed verticals (1080x1920) OR dedicated 1080x960 tiles. Each is cropped
to its 1080x960 tile at a configurable vertical offset so the SUBJECT'S FACE sits high
(NOT a center crop, which grabs the torso). Pure crop = no added softness. If the input
is already 1080x960 (a dedicated split tile), pass crop-y 0 and it's used as-is.

SEAM: a single smooth gaussian black gradient (assets/seam_shadow.png) is overlaid,
centered on the 50% seam — a clean soft "black fade" between the halves (The reference editor's look).
This REPLACES the old stacked-drawbox bands, which read as glitchy hard lines.

Usage:
    python3 make_splitscreen.py --speaker speaker_tile.mp4 --guest guest_tile.mp4 \
        --out split.mp4 --start 17.94 --end 24.6 --crop-y 0 --guest-crop-y 0 \
        --audio none [--shadow-strength 1.0]
"""
from __future__ import annotations

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
import argparse
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Brand FAST-RENDER STANDARD — VideoToolbox HW encode.
sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args as _encoder_args
def _enc_args(w, h, *, tier="delivery"): return _encoder_args(w, h, "ffmpeg", tier=tier)

HERE = Path(__file__).resolve().parent
SEAM_SHADOW = HERE.parent / "assets" / "seam_shadow.png"


def find_ffmpeg() -> str:
    """Locate ffmpeg-full (for libass) or fall back to system ffmpeg."""
    for pattern in ("/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg",
                    "/usr/local/Cellar/ffmpeg-full/*/bin/ffmpeg"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    found = shutil.which("ffmpeg")
    if not found:
        print("ffmpeg not found. Install with: brew install ffmpeg-full", file=sys.stderr)
        sys.exit(2)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--speaker", type=Path, required=True, help="Speaker vertical (1080x1920) or tile (1080x960)")
    ap.add_argument("--guest", type=Path, required=True, help="Guest vertical or tile")
    ap.add_argument("--out", type=Path, required=True, help="Output MP4 (1080x1920)")
    ap.add_argument("--start", type=float, default=0.0, help="Clip start (sec)")
    ap.add_argument("--end", type=float, default=None, help="Clip end (sec)")
    ap.add_argument("--crop-y", type=int, default=480,
                    help="Top Y of the 1080x960 crop into Speaker's input (0 for a 960 tile)")
    ap.add_argument("--guest-crop-y", type=int, default=480,
                    help="Top Y of the 1080x960 crop into the guest's input (0 for a 960 tile)")
    ap.add_argument("--audio", choices=["speaker", "guest", "mix", "none"], default="speaker",
                    help="Which input's audio to use in the output")
    ap.add_argument("--shadow-strength", type=float, default=1.0,
                    help="Seam soft-shadow opacity multiplier (0.0-1.0; 0 = no seam shadow)")
    ap.add_argument("--width", type=int, default=1080, choices=[1080, 2160],
                    help="Output width: 1080 -> 1080x1920 (default, legacy), 2160 -> 2160x3840 "
                         "(4K pipeline — keeps a 4K render chain 4K through the split). Tile height "
                         "and crop-y scale accordingly (tile = width x width*8/9).")
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    W = args.width
    OH = W * 16 // 9          # 1080 -> 1920, 2160 -> 3840
    TILE_H = OH // 2          # 1080 -> 960,  2160 -> 1920
    dur = (args.end - args.start) if args.end is not None else 9999.0
    ay = max(0, min(TILE_H, args.speaker_crop_y))
    gy = max(0, min(TILE_H, args.guest_crop_y))
    s = max(0.0, min(1.0, args.shadow_strength))

    base = (
        f"[0:v]trim=start={args.start}:duration={dur:.3f},setpts=PTS-STARTPTS,"
        f"scale={W}:-1:flags=lanczos,crop={W}:{TILE_H}:0:{ay}[speaker];"
        f"[1:v]trim=start={args.start}:duration={dur:.3f},setpts=PTS-STARTPTS,"
        f"scale={W}:-1:flags=lanczos,crop={W}:{TILE_H}:0:{gy}[guest];"
        f"[speaker][guest]vstack[stk]"
    )

    inputs = ["-i", str(args.speaker), "-i", str(args.guest)]
    if SEAM_SHADOW.exists() and s > 0:
        # Looped static gaussian PNG, alpha-scaled, overlaid centered on the 50% seam.
        inputs += ["-loop", "1", "-i", str(SEAM_SHADOW)]
        # One-sided DROP shadow: PNG top sits ON the 50% seam and falls DOWNWARD onto the
        # guest tile only — Speaker's edge (above) stays clean. (The reference editor's look, user 2026-06-04.)
        # Shadow PNG is authored at 1080 wide — scale to the output width so it spans the seam.
        filter_complex = (
            base + ";"
            f"[2:v]format=yuva420p,scale={W}:-1,colorchannelmixer=aa={s:.3f}[sh];"
            f"[stk][sh]overlay=x=0:y=main_h/2:shortest=1[v]"
        )
    else:
        filter_complex = base.replace("[speaker][guest]vstack[stk]", "[speaker][guest]vstack[v]")

    if args.audio == "speaker":
        audio_map = ["-map", "0:a?"]
    elif args.audio == "guest":
        audio_map = ["-map", "1:a?"]
    elif args.audio == "mix":
        filter_complex += ";[0:a][1:a]amix=inputs=2:duration=shortest[a]"
        audio_map = ["-map", "[a]"]
    else:  # none
        audio_map = ["-an"]

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]",
        *audio_map,
        # Brand fast-render — VideoToolbox HW encode (~4x faster than libx264 -crf16).
        # Splitscreen output is the standard short-form 1080x1920; intermediate tier
        # because captions/grade may re-encode downstream.
        *_enc_args(W, OH, tier="intermediate"),
        "-c:a", "aac", "-b:a", "256k",
        "-movflags", "+faststart",
        str(args.out),
    ]
    print(f"split: speaker@y{ay} (top) / guest@y{gy} (bottom), {args.start}-{args.end}s, "
          f"seam={'soft-shadow' if (SEAM_SHADOW.exists() and s>0) else 'none'}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
