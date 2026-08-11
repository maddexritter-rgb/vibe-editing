"""Shared helpers for render stages."""
from __future__ import annotations

import json, subprocess
from pathlib import Path


def enc_v(bitrate: str = "14M", tier: str = "intermediate") -> list:
    """Video encoder args for stage encodes. WINDOWS PORT 2026-08-03: hardware encoder
    (h264_amf on this machine) for intermediate stages; libx264 for tier='delivery'
    (the shipped file) per the user's quality policy. Replaces hardcoded h264_videotoolbox."""
    import os, sys
    shared = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "..", "..", "..", "lib", "_shared"))
    if shared not in sys.path:
        sys.path.insert(0, shared)
    if tier != "delivery":
        try:
            from fast_encode import hw_h264_args
            return hw_h264_args(bitrate) + ["-tag:v", "avc1"]
        except Exception:
            pass
    return ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-tag:v", "avc1"]


def run(cmd, **kw):
    cmd = [str(c) for c in cmd]
    print("  $", " ".join(cmd[:8]) + (" ..." if len(cmd) > 8 else ""), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def resolve_path(p: str | Path, project: Path) -> Path:
    """Resolve a config path. Absolute -> as-is. Relative -> under project root."""
    p = Path(p)
    return p if p.is_absolute() else (project / p)


def ffprobe_duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip().splitlines()[0].strip().strip(","))  # ffprobe csv can trail a comma


def ffprobe_fps(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True)
    # first line only; ffprobe's csv writer can leave a trailing comma
    raw = out.stdout.strip().splitlines()[0].strip().strip(",")
    num, den = raw.split("/")
    return float(num) / float(den)
