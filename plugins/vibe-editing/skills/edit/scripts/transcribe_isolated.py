#!/usr/bin/env python3
"""transcribe_isolated — channel-isolated stereo (host vs caller) transcription for Hotline / Q&A.

Ported from Julian's CLIPPER_X (scripts/transcribe.py). The IP we keep verbatim is the
channel-isolation DETECTION: decode L/R stereo, take per-50ms RMS envelopes, and Pearson-correlate
them. Channel-isolated audio (host mic hard-left, caller hard-right) has LOW correlation; cross-talk
or mono-duplicated audio has HIGH correlation. When isolated, we transcribe each channel SEPARATELY
so attribution is exact (speaker is known per channel, no diarizer guessing).

What we CHANGED from CLIPPER_X:
  - Transcription backend is GROQ whisper-large-v3 (our stack), NOT Deepgram. whisper-cli is the
    offline fallback. NO Deepgram dependency is added.
  - Output is our segment shape: {"segments":[{"start","end","text","speaker"}], ...} with
    speaker = "host" (left) / "caller" (right). CLIPPER_X emitted Deepgram utterance entries with
    integer speaker ids; we map L->host, R->caller.
  - Mono / cross-talk fallback: instead of Deepgram diarization, we transcribe the mono mix and
    label every segment speaker="unknown" (a real diarizer can be layered on later). The isolated
    path is the one that matters for Hotline, and it needs no diarizer at all.

Detection thresholds (corr < 0.65 => isolated) are carried over from CLIPPER_X unchanged.

Usage:
  GROQ_API_KEY=... python3 transcribe_isolated.py call.mp4 --out call.transcript.json
  python3 transcribe_isolated.py call.wav --backend whisper-cli --out call.transcript.json
  python3 transcribe_isolated.py call.mp4 --force-mono   # skip isolation, mono mix only
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
import argparse, glob, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"          # task asks large-v3 explicitly (not turbo) for clean attrib
GROQ_MAX_BYTES = 40 * 1024 * 1024


# ---------------------------------------------------------------------------------------------
# ffmpeg / ffprobe discovery (same approach as our transcribe_groq.py).
# ---------------------------------------------------------------------------------------------

def find_bin(name: str) -> str:
    if os.environ.get(name.upper()):
        return os.environ[name.upper()]
    cands = [shutil.which(name)] + sorted(glob.glob(f"/opt/homebrew/Cellar/ffmpeg-full/*/bin/{name}"))
    for c in cands:
        if c and Path(c).exists():
            return c
    sys.exit(f"{name} not found")


FFMPEG = None
FFPROBE = None


def run_out(cmd: list[str]) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "command failed")
    return r.stdout.strip()


# ---------------------------------------------------------------------------------------------
# Channel-isolation detection — ported VERBATIM (logic) from CLIPPER_X transcribe.py.
# ---------------------------------------------------------------------------------------------

def get_channel_count(path: str) -> int:
    try:
        out = run_out([FFPROBE, "-v", "error", "-select_streams", "a:0",
                       "-show_entries", "stream=channels",
                       "-of", "default=noprint_wrappers=1:nokey=1", path])
        return int(out.strip().split("\n")[0])
    except Exception:
        return 1


def _read_stereo_pcm(path: str, max_seconds: int = 300):
    import numpy as np
    result = subprocess.run(
        [FFMPEG, "-nostdin", "-v", "error", "-i", path, "-t", str(max_seconds),
         "-vn", "-ac", "2", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    samples = np.frombuffer(result.stdout, dtype=np.int16)
    if samples.size < 2 or samples.size % 2 != 0:
        return None
    return samples.reshape(-1, 2)


def is_channel_isolated(path: str, threshold: float = 0.65) -> bool:
    """True only when L and R envelopes are clearly complementary (host vs caller on split mics).

    Per-50ms RMS envelope Pearson correlation:
      - channel-isolated  -> low corr (~0.2-0.5) -> True
      - cross-talk stereo  -> high corr (~0.85-0.99) -> False
      - mono-duplicated    -> corr ~1.0 -> False
    Any error / ambiguity -> False (caller falls back to mono).
    """
    try:
        import numpy as np
        pcm = _read_stereo_pcm(path)
        if pcm is None or pcm.shape[0] < 16000 * 5:
            return False
        win = 800  # 50ms at 16kHz
        n_windows = pcm.shape[0] // win
        if n_windows < 50:
            return False
        framed = pcm[: n_windows * win].astype(np.float32).reshape(n_windows, win, 2)
        rms = np.sqrt((framed ** 2).mean(axis=1))
        left_env, right_env = rms[:, 0], rms[:, 1]
        noise_floor = max(50.0, 0.01 * float(rms.max()))
        voiced = (left_env > noise_floor) | (right_env > noise_floor)
        if voiced.sum() < 50:
            return False
        left_env, right_env = left_env[voiced], right_env[voiced]
        if left_env.std() < 1e-3 or right_env.std() < 1e-3:
            return False
        corr = float(np.corrcoef(left_env, right_env)[0, 1])
        if not np.isfinite(corr):
            return False
        return corr < threshold
    except Exception:
        return False


def extract_channel(video_path: str, channel: int, out_dir: Path) -> Path:
    """Extract one stereo channel (0=left/host, 1=right/caller) as 16kHz mono MP3 for upload."""
    label = "FL" if channel == 0 else "FR"
    out = out_dir / f"ch{channel}.mp3"
    r = subprocess.run(
        [FFMPEG, "-i", video_path, "-vn", "-af", f"pan=mono|c0={label}",
         "-ar", "16000", "-b:a", "64k", "-avoid_negative_ts", "make_zero", str(out), "-y"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"channel {channel} extraction failed: {r.stderr.strip()}")
    return out


def extract_mono(path: str, out_dir: Path) -> Path:
    out = out_dir / "mono.mp3"
    r = subprocess.run(
        [FFMPEG, "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
         "-avoid_negative_ts", "make_zero", str(out), "-y"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"mono extraction failed: {r.stderr.strip()}")
    return out


# ---------------------------------------------------------------------------------------------
# Transcription backends — Groq (default) or whisper-cli (offline). Emit our segment shape.
# ---------------------------------------------------------------------------------------------

def groq_segments(audio_path: Path, language: str = "en") -> list[dict]:
    import requests
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set (use --backend whisper-cli for offline)")
    size = audio_path.stat().st_size
    if size > GROQ_MAX_BYTES:
        raise RuntimeError(f"{audio_path.name} is {size/1e6:.1f}MB > Groq 40MB limit; downsample harder")
    with audio_path.open("rb") as f:
        files = {"file": (audio_path.name, f, "audio/mpeg")}
        data = {"model": GROQ_MODEL, "response_format": "verbose_json",
                "timestamp_granularities[]": "segment", "language": language}
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(GROQ_URL, headers=headers, files=files, data=data, timeout=900)
    if resp.status_code != 200:
        raise RuntimeError(f"Groq {resp.status_code}: {resp.text[:400]}")
    j = resp.json()
    segs = []
    for s in j.get("segments", []):
        segs.append({"start": round(float(s["start"]), 3), "end": round(float(s["end"]), 3),
                     "text": str(s.get("text", "")).strip()})
    if not segs and j.get("text"):
        segs = [{"start": 0.0, "end": 0.0, "text": j["text"].strip()}]
    return segs


def whisper_cli_segments(audio_path: Path, language: str = "en") -> list[dict]:
    """Offline fallback via whisper-cli (whisper.cpp). Parses its JSON output."""
    exe = shutil.which("whisper-cli") or shutil.which("whisper")
    if not exe:
        raise RuntimeError("whisper-cli/whisper not found and Groq unavailable")
    with tempfile.TemporaryDirectory(prefix="whcli_") as td:
        stem = Path(td) / "out"
        subprocess.run([exe, "-f", str(audio_path), "-l", language, "-oj", "-of", str(stem)],
                       check=True, capture_output=True, text=True)
        jf = Path(str(stem) + ".json")
        data = json.loads(jf.read_text())
    segs = []
    for t in data.get("transcription", []):
        off = t.get("offsets", {})
        segs.append({"start": round(off.get("from", 0) / 1000.0, 3),
                     "end": round(off.get("to", 0) / 1000.0, 3),
                     "text": str(t.get("text", "")).strip()})
    return segs


def transcribe_file(audio_path: Path, backend: str, language: str) -> list[dict]:
    if backend == "groq":
        try:
            return groq_segments(audio_path, language)
        except Exception as e:
            print(f"[iso] Groq failed ({e}); trying whisper-cli", file=sys.stderr)
            return whisper_cli_segments(audio_path, language)
    return whisper_cli_segments(audio_path, language)


# ---------------------------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------------------------

def main():
    global FFMPEG, FFPROBE
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="stereo audio or video (Hotline call-in)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--backend", default="groq", choices=["groq", "whisper-cli"])
    ap.add_argument("--language", default="en")
    ap.add_argument("--force-mono", action="store_true", help="skip isolation; transcribe mono mix")
    ap.add_argument("--host-channel", type=int, default=0, choices=[0, 1],
                    help="which channel is the host/advisor (default 0 = left)")
    a = ap.parse_args()

    FFMPEG = find_bin("ffmpeg")
    FFPROBE = find_bin("ffprobe")
    src = a.input.expanduser()
    if not src.exists():
        sys.exit(f"[iso] input not found: {src}")
    out_path = (a.out.expanduser() if a.out else src.with_suffix(".transcript.json"))

    channels = get_channel_count(str(src))
    isolated = (not a.force_mono) and channels >= 2 and is_channel_isolated(str(src))
    print(f"[iso] channels={channels} isolated={isolated} backend={a.backend}", file=sys.stderr)

    caller_channel = 1 - a.host_channel
    with tempfile.TemporaryDirectory(prefix="iso_tx_") as td:
        tdir = Path(td)
        if isolated:
            host_audio = extract_channel(str(src), a.host_channel, tdir)
            caller_audio = extract_channel(str(src), caller_channel, tdir)
            host_segs = transcribe_file(host_audio, a.backend, a.language)
            caller_segs = transcribe_file(caller_audio, a.backend, a.language)
            for s in host_segs:
                s["speaker"] = "host"
            for s in caller_segs:
                s["speaker"] = "caller"
            segments = sorted(host_segs + caller_segs, key=lambda s: s["start"])
            mode = "isolated-stereo"
        else:
            mono = extract_mono(str(src), tdir)
            segments = transcribe_file(mono, a.backend, a.language)
            for s in segments:
                s["speaker"] = "unknown"
            mode = "mono-fallback"

    payload = {
        "source": str(src),
        "mode": mode,
        "model": (GROQ_MODEL if a.backend == "groq" else "whisper-cli"),
        "host_channel": a.host_channel,
        "segments": segments,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    host_n = sum(1 for s in segments if s.get("speaker") == "host")
    caller_n = sum(1 for s in segments if s.get("speaker") == "caller")
    print(f"[iso] {len(segments)} segments ({mode}; host={host_n} caller={caller_n}) → {out_path}")


if __name__ == "__main__":
    main()
