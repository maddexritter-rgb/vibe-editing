#!/usr/bin/env python3
"""Tighten boundary [start, end] pairs to drop trailing black frames.

Black-frame separators in finished reels often bleed 1-2 frames INTO the
boundary the detector chose. After burn_captions runs the trim, those frames
appear as a black final frame with caption overlaid — the caption masks the
black from blackdetect, so source-side blackdetect misses it.

Fix: per boundary, decode the last 1.5s at native fps, scan frames brightness,
find the LAST frame with mean luma > THRESHOLD, set new_end to its timestamp.
60fps content typically gets -17ms trim (1 frame); fade-outs get -33-50ms.

Usage:
    python3 tighten_boundaries.py <source.mp4> <boundaries.json> --out tight.json

Boundaries JSON schema (input/output):
    { "clips": [ {"index": 0, "start": float, "end": float}, ... ] }
"""
from __future__ import annotations
import tempfile
import argparse, json, subprocess, sys
from pathlib import Path
from PIL import Image


def find_last_bright_frame(source: str, scan_start: float, scan_end: float,
                            fps: int = 60, threshold: float = 20.0) -> int | None:
    """Decode [scan_start, scan_end] at `fps` and return idx of last frame with
    mean luma > threshold. None if all frames are dark."""
    work = Path(tempfile.gettempdir()) / '_tighten_scan'
    work.mkdir(exist_ok=True)
    for f in work.glob('*.jpg'):
        f.unlink()
    subprocess.run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-ss', f'{scan_start:.3f}', '-to', f'{scan_end:.3f}',
        '-i', source, '-vf', f'fps={fps},scale=64:114',
        '-q:v', '5', str(work / 'f%04d.jpg'),
    ], check=True)
    frames = sorted(work.glob('*.jpg'))
    last_bright = None
    for j, fp in enumerate(frames):
        img = Image.open(fp).convert('L')
        mean = sum(img.getdata()) / (img.size[0] * img.size[1])
        if mean > threshold:
            last_bright = j
    return last_bright


def find_first_bright_frame(source: str, scan_start: float, scan_end: float,
                             fps: int = 60, threshold: float = 20.0) -> int | None:
    work = Path(tempfile.gettempdir()) / '_tighten_scan'
    work.mkdir(exist_ok=True)
    for f in work.glob('*.jpg'):
        f.unlink()
    subprocess.run([
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-ss', f'{scan_start:.3f}', '-to', f'{scan_end:.3f}',
        '-i', source, '-vf', f'fps={fps},scale=64:114',
        '-q:v', '5', str(work / 'f%04d.jpg'),
    ], check=True)
    frames = sorted(work.glob('*.jpg'))
    for j, fp in enumerate(frames):
        img = Image.open(fp).convert('L')
        mean = sum(img.getdata()) / (img.size[0] * img.size[1])
        if mean > threshold:
            return j
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('source', type=str)
    ap.add_argument('boundaries', type=Path)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--fps', type=int, default=60)
    ap.add_argument('--threshold', type=float, default=20.0,
                    help='Mean luma threshold for "content" (0-255 scale)')
    ap.add_argument('--scan-seconds', type=float, default=1.5,
                    help='Window to scan at each edge')
    args = ap.parse_args()

    b = json.loads(args.boundaries.read_text())
    new_clips = []
    for clip in b['clips']:
        i, s, e = clip['index'], clip['start'], clip['end']
        # Trailing
        scan_s = max(s, e - args.scan_seconds)
        scan_e = e + 0.05
        last_idx = find_last_bright_frame(args.source, scan_s, scan_e,
                                          args.fps, args.threshold)
        new_e = e
        if last_idx is not None:
            # Last bright frame's start time = scan_s + idx/fps; it occupies idx → idx+1 frames
            new_e = min(e, scan_s + last_idx / args.fps)
        # Leading
        scan2_s = max(0, s - 0.05)
        scan2_e = min(e, s + args.scan_seconds)
        first_idx = find_first_bright_frame(args.source, scan2_s, scan2_e,
                                            args.fps, args.threshold)
        new_s = s
        if first_idx is not None:
            new_s = max(s, scan2_s + first_idx / args.fps)
        ds, de = (new_s - s) * 1000, (new_e - e) * 1000
        print(f'clip {i:02d}: {s:.3f}→{new_s:.3f} ({ds:+.0f}ms)  '
              f'{e:.3f}→{new_e:.3f} ({de:+.0f}ms)')
        new_clips.append({'index': i, 'start': round(new_s, 3), 'end': round(new_e, 3)})

    args.out.write_text(json.dumps({'clips': new_clips}, indent=2))
    print(f'\nWrote {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
