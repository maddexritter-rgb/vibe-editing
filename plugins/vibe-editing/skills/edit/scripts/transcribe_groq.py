#!/usr/bin/env python3
"""Transcribe audio/video via Groq's hosted Whisper API (~40x faster than local).

Reads GROQ_API_KEY from the environment. Falls back to local faster-whisper if
$GROQ_API_KEY is unset — so the skill still works offline.

Input: a single audio or video file (we extract + re-encode to 16kHz mono MP3
to fit Groq's 40MB upload limit) + a boundaries.json describing which clips
to slice.

Output: same per-clip transcript JSON as transcribe.py, so the rest of the
pipeline (detect_fillers, cut_clip, reframe) is unchanged.
"""
from __future__ import annotations

# ── engine bundled-keys autoload (config/keys.env) ──
import os as _ko, pathlib as _kp
def _acq_load_keys():
    d = _kp.Path(__file__).resolve()
    for p in (d, *d.parents):
        if (p / ".claude-plugin").is_dir():
            import sys as _ks
            _ks.path.insert(0, str(p))
            from vibe_credentials import load_keys
            load_keys()
            return
_acq_load_keys()
# ── end keys ──
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# whisper-large-v3-turbo is 2x faster than large-v3 with near-identical quality.
DEFAULT_MODEL = "whisper-large-v3-turbo"
GROQ_MAX_BYTES = 40 * 1024 * 1024  # 40 MB upload limit


def find_ffmpeg() -> str:
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    import glob as _glob
    for c in [shutil.which("ffmpeg")] + sorted(
        _glob.glob("/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg")
    ):
        if c and Path(c).exists():
            return c
    sys.exit("ffmpeg not found")


def downsample_for_upload(input_path: Path, out_dir: Path, ffmpeg: str) -> Path:
    """Re-encode to 16kHz mono MP3 @ 64kbps — Whisper doesn't need more."""
    out = out_dir / "audio_16k_mono.mp3"
    subprocess.run(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(input_path),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k",
         str(out)],
        check=True,
    )
    return out


def groq_transcribe(audio_path: Path, model: str, language: str) -> dict:
    import urllib.request
    import urllib.error
    try:
        import requests  # preferred: handles multipart easily
    except ImportError:
        sys.exit("pip install requests")

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY env var not set")

    size = audio_path.stat().st_size
    if size > GROQ_MAX_BYTES:
        raise RuntimeError(
            f"audio is {size/1024/1024:.1f}MB, over Groq's 40MB limit — "
            "downsample harder (e.g. lower bitrate)."
        )

    with audio_path.open("rb") as f:
        files = {"file": (audio_path.name, f, "audio/mpeg")}
        data = {
            "model": model,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
            "language": language,
        }
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(GROQ_URL, headers=headers, files=files, data=data,
                             timeout=600)
    if resp.status_code != 200:
        raise RuntimeError(f"Groq API {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def groq_to_word_list(result: dict) -> list[dict]:
    """Convert Groq's verbose_json response into our standard word list."""
    words = []
    for w in result.get("words", []):
        words.append({
            "word": w["word"].strip(),
            "start": float(w["start"]),
            "end": float(w["end"]),
            "prob": 1.0,  # Groq doesn't return per-word confidence
        })
    return words


def slice_for_clip(words: list[dict], start: float, end: float) -> list[dict]:
    out = []
    for w in words:
        if w["end"] <= start or w["start"] >= end:
            continue
        ws = max(w["start"], start) - start
        we = min(w["end"], end) - start
        if we - ws < 0.02:
            continue
        out.append({"word": w["word"], "start": round(ws, 3),
                    "end": round(we, 3), "prob": 1.0})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="Source audio or video")
    ap.add_argument("boundaries", type=Path, help="boundaries.json")
    ap.add_argument("--out", type=Path, default=Path("transcripts"))
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    choices=["whisper-large-v3-turbo", "whisper-large-v3",
                             "distil-whisper-large-v3-en"])
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    if not os.environ.get("GROQ_API_KEY"):
        sys.stderr.write(
            "ERROR: GROQ_API_KEY env var not set. Either export it, or use "
            "transcribe.py for the local faster-whisper path.\n"
        )
        return 3
    if not args.input.exists():
        sys.exit(f"Input not found: {args.input}")
    if not args.boundaries.exists():
        sys.exit(f"Boundaries not found: {args.boundaries}")

    ffmpeg = find_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="groq_tx_") as td:
        tdir = Path(td)
        print(f"Downsampling to 16kHz mono MP3…", flush=True)
        small = downsample_for_upload(args.input, tdir, ffmpeg)
        print(f"  {small.stat().st_size/1024/1024:.1f} MB", flush=True)

        print(f"Calling Groq ({args.model})…", flush=True)
        import time
        t0 = time.time()
        result = groq_transcribe(small, args.model, args.language)
        dt = time.time() - t0
        print(f"  done in {dt:.1f}s", flush=True)

    words = groq_to_word_list(result)
    print(f"Got {len(words)} word-level tokens.")

    bounds = json.loads(args.boundaries.read_text())
    clips = bounds.get("clips", [])
    args.out.mkdir(parents=True, exist_ok=True)
    for clip in clips:
        idx = clip["index"]
        clip_words = slice_for_clip(words, clip["start"], clip["end"])
        payload = {
            "clip_index": idx,
            "source_start": clip["start"],
            "source_end": clip["end"],
            "duration": round(clip["end"] - clip["start"], 3),
            "words": clip_words,
        }
        out_path = args.out / f"clip_{idx:02d}.json"
        out_path.write_text(json.dumps(payload, indent=2))
        preview = " ".join(w["word"] for w in clip_words[:12])
        print(f"  clip {idx:02d}: {len(clip_words)} words  → {out_path}")
        print(f"           preview: {preview}…")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
