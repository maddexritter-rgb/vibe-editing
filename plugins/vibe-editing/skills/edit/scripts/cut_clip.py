#!/usr/bin/env python3
"""Cut a single short-form clip from raw footage, with filler surgery + Speaker-SOP polish.

- Source can be a single synced MP4, OR MP4 video + separate WAV audio (with time offset).
- Clip window is specified in AUDIO-TIMELINE seconds (when audio and video are separate).
- Filler cuts are a list of [start, end] intervals IN AUDIO TIMELINE to remove.
- Applies SOP defaults:
    * 3 video frames of lead-in and tail-out padding (silent).
    * S-curve audio fade on first/last ~70ms (2 frames @ 30fps).
    * Optional LUT via --lut path.
- Output is re-encoded (libx264 CRF 18 + AAC). Preserves source resolution.

Strategy:
Build ONE ffmpeg filter_complex that:
  1. Concatenates the KEEP segments (the inverse of filler intervals within [clip_start, clip_end])
  2. Joins the corresponding audio KEEP segments from the WAV
  3. Pads with N black frames at start and N at end (setpts + concat)
  4. Applies audio fade-in/out
  5. Optional lut3d filter on video
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
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args  # Brand fast-render standard — VideoToolbox hardware encode

SKILL_ROOT = Path(__file__).resolve().parent.parent


def find_ffmpeg() -> str:
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    candidates = [shutil.which("ffmpeg")]
    import glob as _glob
    candidates.extend(sorted(_glob.glob("/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg")))
    candidates.extend(sorted(_glob.glob("/usr/local/Cellar/ffmpeg-full/*/bin/ffmpeg")))
    for c in candidates:
        if c and Path(c).exists():
            return c
    sys.stderr.write("No ffmpeg found on PATH. Install via: brew install ffmpeg-full\n")
    sys.exit(3)


def compute_keeps(clip_start: float, clip_end: float, fillers: list[dict]) -> list[tuple[float, float]]:
    """Subtract filler intervals from [clip_start, clip_end] to get keep segments."""
    keeps: list[tuple[float, float]] = []
    cursor = clip_start
    for f in sorted(fillers, key=lambda c: c["start"]):
        fs = max(f["start"], clip_start)
        fe = min(f["end"], clip_end)
        if fe <= cursor:
            continue
        if fs >= clip_end:
            break
        if fs > cursor:
            keeps.append((cursor, fs))
        cursor = max(cursor, fe)
    if cursor < clip_end:
        keeps.append((cursor, clip_end))
    # Drop zero-length keeps.
    return [(s, e) for s, e in keeps if e - s > 0.02]


def escape_for_filter(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", r"\'").replace(":", r"\:")


def build_filter_complex(
    keeps_video: list[tuple[float, float]],
    keeps_audio: list[tuple[float, float]],
    fps: float,
    pad_frames_lead: int,
    pad_frames_tail: int,
    audio_fade_sec: float,
    lut_path: str | None,
    width: int,
    height: int,
) -> str:
    """Build the filter_complex string.

    Inputs must be:
      0: MP4 video (seek'd already at the beginning of the clip window for efficiency)
      1: WAV audio (seek'd to the audio start of the clip window)
    """
    # The inputs are already seeked to "clip_start" — so all keep ranges are expressed as offsets from there.
    v_parts = []
    a_parts = []
    for i, ((vs, ve), (as_, ae)) in enumerate(zip(keeps_video, keeps_audio)):
        v_parts.append(
            f"[0:v]trim=start={vs:.3f}:end={ve:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        a_parts.append(
            f"[1:a]atrim=start={as_:.3f}:end={ae:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )

    n = len(keeps_video)
    v_concat_in = "".join(f"[v{i}]" for i in range(n))
    a_concat_in = "".join(f"[a{i}]" for i in range(n))
    v_concat = f"{v_concat_in}concat=n={n}:v=1:a=0[vmain]"
    a_concat = f"{a_concat_in}concat=n={n}:v=0:a=1[amain]"

    # Optional LUT on vmain before the lead/tail concat.
    if lut_path:
        lut_escaped = escape_for_filter(str(lut_path))
        lut_filter = f"[vmain]lut3d=file='{lut_escaped}'[vgraded]"
        vmain_label = "[vgraded]"
    else:
        lut_filter = None
        vmain_label = "[vmain]"

    # Audio fade in/out.
    total_audio = sum(ae - as_ for as_, ae in keeps_audio)
    amain_faded = (
        f"[amain]afade=t=in:curve=hsin:st=0:d={audio_fade_sec:.4f},"
        f"afade=t=out:curve=hsin:st={max(0, total_audio - audio_fade_sec):.4f}:d={audio_fade_sec:.4f}[amainfaded]"
    )

    parts = v_parts + a_parts + [v_concat, a_concat]
    if lut_filter:
        parts.append(lut_filter)
    parts.append(amain_faded)

    # Lead/tail pad: only generate black frames if requested.
    lead_dur = pad_frames_lead / fps
    tail_dur = pad_frames_tail / fps
    need_lead = pad_frames_lead > 0
    need_tail = pad_frames_tail > 0
    pad_color_fmt = "color=c=black:s={w}x{h}:r={fps}".format(w=width, h=height, fps=f"{fps}")

    video_inputs = []
    audio_inputs = []
    if need_lead:
        parts.append(f"{pad_color_fmt}:d={lead_dur:.4f},format=yuv420p[vlead]")
        parts.append(f"anullsrc=channel_layout=stereo:sample_rate=44100:duration={lead_dur:.4f}[alead]")
        video_inputs.append("[vlead]")
        audio_inputs.append("[alead]")
    video_inputs.append(vmain_label)
    audio_inputs.append("[amainfaded]")
    if need_tail:
        parts.append(f"{pad_color_fmt}:d={tail_dur:.4f},format=yuv420p[vtail]")
        parts.append(f"anullsrc=channel_layout=stereo:sample_rate=44100:duration={tail_dur:.4f}[atail]")
        video_inputs.append("[vtail]")
        audio_inputs.append("[atail]")

    # If no padding at all, just rename the labels.
    n_cat = len(video_inputs)
    if n_cat > 1:
        v_final = f"{''.join(video_inputs)}concat=n={n_cat}:v=1:a=0[vout]"
        a_final = f"{''.join(audio_inputs)}concat=n={n_cat}:v=0:a=1[aout]"
    else:
        # Just pass through — rename vmain_label to [vout], [amainfaded] to [aout]
        v_final = f"{vmain_label}null[vout]"
        a_final = f"[amainfaded]anull[aout]"
    parts.append(v_final)
    parts.append(a_final)

    return ";".join(parts)


def probe_video(ffprobe: str, path: Path) -> dict:
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    s = data["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    return {"width": s["width"], "height": s["height"], "fps": fps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mp4", type=Path, required=True)
    ap.add_argument("--wav", type=Path, required=True, help="Clean audio WAV (used as output audio)")
    ap.add_argument("--wav-offset", type=float, required=True,
                    help="Seconds to add to a WAV timestamp to get the MP4 timestamp (from sync_audio.py)")
    ap.add_argument("--start", type=float, required=True, help="Clip start in WAV-timeline seconds")
    ap.add_argument("--end", type=float, required=True, help="Clip end in WAV-timeline seconds")
    ap.add_argument("--fillers", type=Path, default=None,
                    help="JSON file with 'cuts': [{start, end}, ...] in WAV-timeline seconds")
    ap.add_argument("--lut", type=Path, default=None, help="Optional .cube/.3dl LUT file")
    ap.add_argument("--pad-lead-frames", type=int, default=0,
                    help="Black frames before clip. Default 0 (no lead pad) — avoids flash-of-black on upload.")
    ap.add_argument("--pad-tail-frames", type=int, default=3)
    ap.add_argument("--audio-fade-sec", type=float, default=0.067,
                    help="SOP says 'first 2 frames' — ~67ms at 30fps")
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--master", action="store_true",
                    help="Final archival master via libx264 (slow, max quality). Default: VideoToolbox (~4x faster).")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    ffmpeg = find_ffmpeg()
    ffprobe = ffmpeg.replace("/ffmpeg", "/ffprobe")
    meta = probe_video(ffprobe, args.mp4)
    width, height, fps = meta["width"], meta["height"], meta["fps"]

    # Load filler cuts and compute keep ranges in WAV timeline.
    filler_cuts: list[dict] = []
    if args.fillers and args.fillers.exists():
        filler_cuts = json.loads(args.fillers.read_text()).get("cuts", [])
        # Scope to clip window.
        filler_cuts = [c for c in filler_cuts if c["end"] > args.start and c["start"] < args.end]
    keeps_audio = compute_keeps(args.start, args.end, filler_cuts)

    if not keeps_audio:
        sys.stderr.write("ERROR: no keep segments after filler removal — check inputs.\n")
        return 1

    # Translate to input-local times.
    # We'll seek both inputs to (start); within the filter graph, times are relative to seek.
    audio_local_start = args.start  # WAV seeked to args.start
    video_local_start = args.start + args.wav_offset  # MP4 seeked to args.start + offset

    # Keep ranges in FILTER-LOCAL coordinates (relative to each seeked input's t=0).
    keeps_audio_local = [(s - audio_local_start, e - audio_local_start) for s, e in keeps_audio]
    keeps_video_local = [(s - audio_local_start, e - audio_local_start) for s, e in keeps_audio]
    # ^ because video input was seeked (args.start + wav_offset) into the MP4; once seeked,
    #   WAV-time X maps to video-local time (X - args.start), same as audio.

    fc = build_filter_complex(
        keeps_video_local, keeps_audio_local, fps,
        args.pad_lead_frames, args.pad_tail_frames, args.audio_fade_sec,
        str(args.lut) if args.lut else None,
        width, height,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-hide_banner",
        "-ss", f"{video_local_start:.3f}", "-i", str(args.mp4),
        "-ss", f"{audio_local_start:.3f}", "-i", str(args.wav),
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        *encoder_args(width, height, ffmpeg, tier=("master" if args.master else "intermediate"), crf=args.crf),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(args.out),
    ]
    print("Cutting:", args.out.name, flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-2500:])
        return proc.returncode
    print(f"  wrote {args.out}  ({len(keeps_audio)} keep segments, "
          f"{len(filler_cuts)} filler cuts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
