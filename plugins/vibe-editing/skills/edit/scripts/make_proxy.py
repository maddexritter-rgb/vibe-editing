#!/usr/bin/env python3
"""Make a 1080p-class PROXY of a 4K source for fast editorial iteration (proxy workflow).

The proxy has the SAME timeline (fps, duration, every timestamp) as the source — only the
pixels shrink — so every cut/filler/sync timestamp maps 1:1. Iterate cuts + captions + review
on the proxy (4x less data per step), then final-render the APPROVED clips from the original
4K (just point --mp4 at the original instead of the proxy). Reusable by any video skill.

    python3 make_proxy.py source_4k.mp4 [--out source.proxy.mp4] [--max 1920]
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
import argparse
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args  # Brand fast-render standard


def find_ffmpeg() -> str:
    for c in [shutil.which("ffmpeg")] + sorted(glob.glob("/opt/homebrew/Cellar/ffmpeg*/*/bin/ffmpeg")):
        if c and Path(c).exists():
            return c
    sys.exit("ffmpeg not found — brew install ffmpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--max", type=int, default=1920,
                    help="Longest side of the proxy in px (default 1920 = 1080p-class).")
    a = ap.parse_args()
    if not a.source.exists():
        sys.exit(f"source not found: {a.source}")
    out = a.out or a.source.with_suffix(".proxy.mp4")
    ff = find_ffmpeg()

    vf = (f"scale=w='min({a.max},iw)':h='min({a.max},ih)':"
          f"force_original_aspect_ratio=decrease:force_divisible_by=2")
    cmd = [ff, "-y", "-hide_banner", "-i", str(a.source), "-vf", vf,
           # proxy = small + fast; bitrate low on purpose (preview only)
           *encoder_args(a.max, int(a.max * 16 / 9), ff, tier="proxy", bitrate="8M"),
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out)]
    print(f"Proxy: {a.source.name} → {out.name} (≤{a.max}px, VideoToolbox)…", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-1500:])
        return r.returncode
    src_mb = a.source.stat().st_size / 1e6
    out_mb = out.stat().st_size / 1e6
    print(f"  ✅ {out}  ({src_mb:.0f}MB → {out_mb:.0f}MB, {src_mb/max(out_mb,0.1):.0f}x smaller)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
