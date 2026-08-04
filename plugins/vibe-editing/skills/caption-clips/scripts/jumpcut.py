#!/usr/bin/env python3
"""Jump-cut a talking-head clip by CAPPING every pause at a maximum length.

Model (locked 2026-06-02): "no pause longer than --max-pause."
- Detect acoustic silence (conservative threshold so soft speech isn't flagged).
- For each silence longer than max_pause, remove the time from the MIDDLE, keeping
  max_pause/2 of real silence on EACH side. Because we only ever trim the middle of
  confirmed silence, the word that just ended and the word about to start are never
  touched — their natural tail + onset stay intact.
- Pauses already <= max_pause are left alone (these are the "good" beats — breaths,
  dramatic pauses, natural sentence rhythm).

Why not the older approaches:
- Cutting at silence EDGES with a tiny pad clips soft word tails ("cut too early") and
  butts words together at seams ("words over one another").
- Pure transcript word-gaps miss pauses that ASR absorbs into a bloated word timestamp
  (e.g. "it" stretched to span a 4.8s pause).
Capping-the-middle of acoustic silence avoids both.

Usage:
    python3 jumpcut.py <in.mov> <out.mp4> [--max-pause 0.25] [--noise -34dB] [--min-detect 0.20]

Tuning:
- --max-pause  : longest pause allowed to remain (seconds). LOWER = snappier. 0.25 default.
- --noise      : silence dB threshold. More negative = only deader silence counts. -34dB default.
- --min-detect : ignore silences shorter than this entirely (natural micro-gaps). 0.20 default.
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
import argparse, os, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, VIBE_SHARED)
try:
    from fast_encode import encoder_args  # Brand render standard: VideoToolbox HW encode
except Exception:
    encoder_args = None


def probe_dur(path: str) -> float:
    return float(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', path]).decode().strip())


def detect_silences(path: str, noise: str, min_detect: float):
    log = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-af',
        f'silencedetect=noise={noise}:d={min_detect}', '-f', 'null', '-'],
        capture_output=True, text=True).stderr
    sils, cur = [], None
    for m in re.finditer(r'silence_(start|end):\s*([\d.]+)', log):
        if m.group(1) == 'start':
            cur = float(m.group(2))
        elif cur is not None:
            sils.append((cur, float(m.group(2)))); cur = None
    return sils


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('src'); ap.add_argument('out')
    # Defaults retuned 2026-06-02 after comparing to the reference cuts: my -34dB/0.25
    # left 0.5-1.2s breathy pauses she removed. She caps pauses ~0.15s and her
    # threshold catches soft room-tone gaps. -30dB + 0.15 cap matches her pacing.
    ap.add_argument('--max-pause', type=float, default=0.15,
                    help='Longest pause allowed to remain (s). Lower = snappier. 0.15 = the house pacing.')
    ap.add_argument('--noise', default='-30dB',
                    help='Silence threshold. -30dB catches breathy pauses; -34dB (old) missed them.')
    ap.add_argument('--min-detect', type=float, default=0.18)
    ap.add_argument('--crf', type=int, default=18)
    args = ap.parse_args()

    dur = probe_dur(args.src)
    sils = detect_silences(args.src, args.noise, args.min_detect)
    half = args.max_pause / 2.0

    # Build keep-segments: walk the timeline, when a silence exceeds max_pause,
    # keep half on each side and drop the middle.
    keeps, pos = [], 0.0
    cut_total = 0.0
    for s, e in sils:
        L = e - s
        if L <= args.max_pause:
            continue  # good pause — leave it
        keeps.append((pos, s + half))   # up to half-pause after the last word
        pos = e - half                  # resume half-pause before next word
        cut_total += L - args.max_pause
    keeps.append((pos, dur))

    keeps = [(s, e) for s, e in keeps if e - s > 0.10]
    merged = [list(keeps[0])]
    for s, e in keeps[1:]:
        if s <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    kept = sum(e - s for s, e in merged)
    print(f"source {dur:.2f}s → {len(merged)} segments, {kept:.2f}s "
          f"(cut {dur-kept:.2f}s; max-pause={args.max_pause}s)")
    for s, e in merged:
        print(f"  keep {s:6.2f} → {e:6.2f}  ({e-s:.2f}s)")

    # Concat with 8ms a-fades at seams to kill clicks.
    parts = []
    for i, (s, e) in enumerate(merged):
        seg = e - s
        parts.append(
            f"[0:v]trim={s:.3f}:{e:.3f},setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.008,afade=t=out:st={max(0,seg-0.008):.3f}:d=0.008[a{i}]")
    ci = ''.join(f"[v{i}][a{i}]" for i in range(len(merged)))
    fc = ';'.join(parts) + f";{ci}concat=n={len(merged)}:v=1:a=1[v][a]"
    # Brand render standard: VideoToolbox HW encode (~4x faster, off-CPU) via encoder_args; libx264 fallback.
    W = H = 0
    try:
        wh = subprocess.run(['ffprobe','-v','error','-select_streams','v','-show_entries','stream=width,height','-of','csv=p=0',args.src],capture_output=True,text=True).stdout.strip().split(',')
        W, H = int(wh[0]), int(wh[1])
    except Exception:
        pass
    if encoder_args and W and H:
        venc = list(encoder_args(W, H, 'ffmpeg', tier='intermediate', crf=args.crf))
    else:
        venc = ['-c:v', 'libx264', '-preset', 'fast', '-crf', str(args.crf), '-pix_fmt', 'yuv420p']
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', args.src,
           '-filter_complex', fc, '-map', '[v]', '-map', '[a]', *venc,
           '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', args.out]
    if subprocess.run(cmd).returncode != 0:
        sys.exit("ffmpeg failed")
    print(f"✅ {args.out}  ({probe_dur(args.out):.2f}s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
