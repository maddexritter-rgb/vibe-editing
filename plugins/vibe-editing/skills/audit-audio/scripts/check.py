#!/usr/bin/env python3
"""audit-audio: independent audio quality gate.

Checks word clipping, pops/clicks, level consistency, buzz/hum, music balance,
and clean open/close on a rendered clip.

Calibrated 2026-06-12 against a 22-clip known-good batch so that FAIL means
something a listener would hear:
  - buzz_hum requires TONALITY (narrowband prominence) + PERSISTENCE across the
    whole clip — a music bed's harmonic content no longer reads as hum.
  - clean_open_close measures the voice-band envelope anchored at t=0 / t=end,
    so a sparse music bed can't fake "dead air".
  - word_clipping treats ASR word durations as hypotheses only; a flag must be
    CONFIRMED acoustically (hot head/tail: full-level voice at the literal clip
    edge) before it fails the gate. Forced-aligner glitches at t=0 are suppressed.
  - music_balance reads the declared music bed (manifest/contract via
    _shared/clip_meta) and measures voice-over-bed separation in speech gaps.
  - clipping_distortion measures TRUE sample-railing at 0dBFS (flat-topped runs
    pinned at full-scale) straight off the decoded samples — the known-good batch
    is 0% railed, an over-driven/distorted export is not.

Genuinely-useful checks are intact: hard clipping/distortion (clipping_distortion),
pops/clicks at splices (pops_clicks), gross level imbalance (level_consistency),
no-audio-stream (decode guard in main), real mains hum (buzz_hum).
"""

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
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

try:
    from scipy import signal as sps
except ImportError:
    print("ERROR: scipy required. pip3 install scipy", file=sys.stderr)
    sys.exit(1)

# Render-pipeline metadata resolver (graceful: gates still run without it)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
try:
    from clip_meta import resolve as resolve_clip_meta
except Exception:
    resolve_clip_meta = None

SR = 48000

# ---- thresholds (calibrated on known-good batch, 2026-06-12) ----
OPEN_WARN_S, OPEN_ERR_S = 0.12, 0.25       # head dead air before first voice
CLOSE_WARN_S, CLOSE_ERR_S = 0.50, 0.90     # trailing dead air after last voice
HUM_PROM_DB = 10.0                          # narrowband prominence to count as tonal
HUM_PERSIST = 0.75                          # fraction of 2s windows the tone persists
HUM_GRID_TOL_HZ = 0.7                       # must sit ON the 50/60Hz mains grid —
                                            # equal-temperament notes all miss this window,
                                            # so sustained musical drones don't qualify
HUM_FAMILY_PROM_DB = 8.0                    # secondary-harmonic evidence bar
HUM_FAMILY_PERSIST = 0.7
HUM_SOLO_PROM_DB = 18.0                     # single grid tone w/o family needs to be loud+steady
HUM_SOLO_PERSIST = 0.9
HUM_ERR_BELOW_SPEECH_DB = 25.0              # hum less than this far under speech = error
HUM_WARN_BELOW_SPEECH_DB = 35.0
HOT_EDGE_DB = 9.0                           # voice within this of speech level at clip edge
LEVEL_WARN_DB, LEVEL_ERR_DB = 3.0, 6.0
MUSIC_ERR_DB, MUSIC_WARN_DB = 6.0, 9.0      # voice-over-bed separation floors
CLIP_FULLSCALE = 0.999                      # |sample| at/above this = railed at 0dBFS
CLIP_FLAT_RUN = 8                           # consecutive railed samples = a flat-topped (clipped) peak
CLIP_FRAC_WARN = 0.0005                     # 0.05% of samples railed in flat runs = audible distortion
CLIP_FRAC_ERR = 0.002                       # 0.2% railed = gross distortion


def groq_key() -> str | None:
    k = os.environ.get("GROQ_API_KEY")
    if k:
        return k
    zsh = Path.home() / ".zshrc"
    if zsh.exists():
        m = re.search(r'GROQ_API_KEY=["\']?([A-Za-z0-9_\-]+)', zsh.read_text())
        if m:
            return m.group(1)
    return None


def load_audio(clip_path: str) -> np.ndarray:
    """Decode to 48k mono float32 via ffmpeg, in-memory."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", clip_path, "-vn", "-ac", "1",
         "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True,
    )
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def get_duration(clip_path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", clip_path],
        capture_output=True, text=True,
    )
    return float(json.loads(r.stdout).get("format", {}).get("duration", 0))


def frame_env_db(y: np.ndarray, hop_ms: float = 10, win_ms: float = 25):
    """Per-frame RMS energy in dB. Returns (db, frame_start_times)."""
    hop = int(SR * hop_ms / 1000)
    win = int(SR * win_ms / 1000)
    if len(y) < win:
        return np.array([-120.0]), np.array([0.0])
    n = (len(y) - win) // hop + 1
    c = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    starts = np.arange(n) * hop
    e = (c[starts + win] - c[starts]) / win
    return 10 * np.log10(e + 1e-12), starts / SR


def voice_band(x: np.ndarray) -> np.ndarray:
    sos = sps.butter(4, [300, 3400], btype="bandpass", fs=SR, output="sos")
    return sps.sosfiltfilt(sos, x).astype(np.float32)


def speech_level_db(db: np.ndarray) -> float:
    """Typical voiced level: median of frames near the loud end."""
    peak = np.percentile(db, 95)
    voiced = db[db >= peak - 20]
    return float(np.median(voiced)) if len(voiced) else float(peak)


def first_sustained(mask: np.ndarray, run: int) -> int:
    """Index of first run of `run` consecutive True, or -1."""
    if len(mask) < run:
        return -1
    conv = np.convolve(mask.astype(int), np.ones(run, dtype=int), mode="valid")
    hits = np.where(conv == run)[0]
    return int(hits[0]) if len(hits) else -1


def last_sustained(mask: np.ndarray, run: int) -> int:
    """Index of the LAST frame of the last run of `run` consecutive True, or -1."""
    rev = first_sustained(mask[::-1], run)
    return len(mask) - 1 - rev if rev >= 0 else -1


# ---------------------------------------------------------------- checks

def transcribe_words(clip_path: str) -> list:
    key = groq_key()
    if not key:
        return []
    import tempfile
    import httpx
    wav16 = tempfile.mktemp(suffix=".wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", clip_path, "-vn", "-ac", "1", "-ar", "16000", wav16],
        capture_output=True,
    )
    try:
        with open(wav16, "rb") as f:
            resp = httpx.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={
                    "model": "whisper-large-v3",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                },
                timeout=120,
            )
        resp.raise_for_status()
        words = resp.json().get("words", [])
        if not words:
            for s in resp.json().get("segments", []):
                words.extend(s.get("words", []))
        return words
    except Exception:
        return []
    finally:
        if os.path.exists(wav16):
            os.unlink(wav16)


def check_word_clipping(clip_path: str, vdb: np.ndarray, speech_db: float) -> dict:
    """Mid-word cuts at the clip edges — corroboration required on BOTH channels.

    Two facts about this pipeline's house style make single-signal detection a
    false-positive machine (verified on the known-good batch):
      - the forced aligner glitches at clip start: the FIRST word is routinely
        reported with a tiny duration on clips whose waveform attack is clean,
        so the first word can never fail this gate on ASR evidence;
      - zero-gap opens and hard-ends legitimately put near-full voice level at
        the literal clip edges (plosive attack at t=0, payoff decay at t=end),
        so a hot edge alone is not evidence of a clipped word either.
    The gate therefore errors only when ASR says the LAST word is truncated AND
    the waveform confirms the audio ends mid-decay. Everything else is reported
    as measurement for the human/agent reviewing the audit.
    """
    issues = []
    suppressed = []

    head_db = float(np.max(vdb[:2])) if len(vdb) >= 2 else -120.0
    tail_db = float(np.max(vdb[-2:])) if len(vdb) >= 2 else -120.0
    tail_hot = tail_db >= speech_db - HOT_EDGE_DB

    words = transcribe_words(clip_path)
    for i in (0, len(words) - 1) if words else []:
        w = words[i]
        word = w.get("word", "").strip()
        core = word.strip(".,;:!?\"'")  # ASR punctuation must not change the length class
        dur = w.get("end", 0) - w.get("start", 0)
        min_dur = 0.08 if len(core) <= 2 else 0.12 if len(core) <= 4 else 0.15
        if not core or not (0 < dur < min_dur):
            continue
        pos = "first" if i == 0 else "last"
        if pos == "last" and tail_hot:
            issues.append({"pos": pos, "word": word, "measured_duration_s": round(dur, 3),
                           "severity": "error",
                           "problem": f"last word '{word}' cut mid-word "
                                      f"(short ASR duration + audio ends at full voice level)"})
        else:
            suppressed.append({"pos": pos, "word": word, "measured_duration_s": round(dur, 3),
                               "note": "aligner edge glitch / clean waveform — not corroborated"})

    out = {"pass": len(issues) == 0, "issues": issues,
           "head_edge_db_below_speech": round(speech_db - head_db, 1),
           "tail_edge_db_below_speech": round(speech_db - tail_db, 1),
           "total_words": len(words)}
    if suppressed:
        out["suppressed_aligner_glitches"] = suppressed
    if not words:
        out["note"] = "no transcription available — edge measurements only"
    return out


def detect_splice_points(wav_path: str) -> list:
    r = subprocess.run(
        ["ffmpeg", "-i", wav_path, "-af", "silencedetect=noise=-35dB:d=0.03", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    return [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", r.stderr)]


def check_pops_clicks(wav_path: str) -> dict:
    """Pops/clicks at splice points via crest factor in a 50ms window."""
    issues = []
    for sp in detect_splice_points(wav_path)[:50]:
        start = max(0, sp - 0.025)
        r = subprocess.run(
            ["ffmpeg", "-ss", str(start), "-t", "0.05", "-i", wav_path,
             "-af", "astats=metadata=1:reset=1", "-f", "null", "-"],
            capture_output=True, text=True,
        )
        m = re.search(r"Crest factor:\s*([\d.]+)", r.stderr)
        if m and float(m.group(1)) > 20:
            issues.append({"time_s": round(sp, 2), "crest_factor": round(float(m.group(1)), 1),
                           "severity": "error"})
    return {"pass": len(issues) == 0, "issues": issues[:10]}


def check_clipping_distortion(x: np.ndarray) -> dict:
    """True sample-railing / hard-clipping at 0dBFS, measured on the decoded samples.

    A clean clip never reaches full-scale (the pipeline limits voice to ~-16 LUFS,
    music ~-30, so peaks sit well under 1.0 — the known-good batch is 0% railed).
    Distortion (over-driven gain, a railed export) flat-tops the waveform: many
    samples pinned at |1.0| in SUSTAINED runs. We require both a non-trivial
    railed FRACTION and that the railing occurs in flat runs (≥CLIP_FLAT_RUN
    consecutive full-scale samples) so a couple of legitimate transient peaks at
    full-scale don't trip it. Independent of ASR and of container peak-smear.
    """
    if len(x) == 0:
        return {"pass": True, "issues": [], "note": "no samples"}
    ax = np.abs(x)
    railed = ax >= CLIP_FULLSCALE
    railed_total = int(railed.sum())
    # fraction of samples that are full-scale AND part of a flat run of >=CLIP_FLAT_RUN
    run = np.convolve(railed.astype(np.int32), np.ones(CLIP_FLAT_RUN, dtype=np.int32), mode="same")
    in_flat_run = run >= CLIP_FLAT_RUN
    flat_railed = int(in_flat_run.sum())
    frac = flat_railed / len(x)
    peak = float(ax.max())

    issues = []
    if frac >= CLIP_FRAC_ERR:
        sev = "error"
    elif frac >= CLIP_FRAC_WARN:
        sev = "warn"
    else:
        sev = None
    if sev:
        issues.append({
            "severity": sev,
            "railed_fraction_pct": round(frac * 100, 3),
            "railed_samples": flat_railed,
            "peak_abs": round(peak, 3),
            "problem": f"audio railed at 0dBFS in flat-topped runs over "
                       f"{round(frac * 100, 3)}% of samples (hard clipping / distortion)",
        })
    return {"pass": sev != "error", "railed_fraction_pct": round(frac * 100, 4),
            "railed_samples_total": railed_total, "peak_abs": round(peak, 3),
            "issues": issues}


def check_level_consistency(vdb: np.ndarray, times: np.ndarray, speech_db: float,
                            duration: float) -> dict:
    """Voiced-frame level per 5s window vs clip average (voice band, music-proof)."""
    issues = []
    window = 5.0
    win_levels = []
    t = 0.0
    while t < duration - 1:
        sel = (times >= t) & (times < t + window) & (vdb >= speech_db - 15)
        if sel.sum() >= 50:  # ≥0.5s of voiced frames
            win_levels.append({"time_s": round(t, 1), "db": float(np.mean(vdb[sel]))})
        t += window
    if len(win_levels) < 2:
        return {"pass": True, "issues": [], "note": "clip too short for level analysis"}
    avg = sum(w["db"] for w in win_levels) / len(win_levels)
    for w in win_levels:
        delta = abs(w["db"] - avg)
        if delta > LEVEL_ERR_DB:
            issues.append({"time_s": w["time_s"], "delta_db": round(delta, 1), "severity": "error"})
        elif delta > LEVEL_WARN_DB:
            issues.append({"time_s": w["time_s"], "delta_db": round(delta, 1), "severity": "warn"})
    has_error = any(i["severity"] == "error" for i in issues)
    rng = max(w["db"] for w in win_levels) - min(w["db"] for w in win_levels)
    return {"pass": not has_error, "avg_voice_db": round(avg, 1),
            "range_db": round(rng, 1), "issues": issues[:10]}


def check_buzz_hum(x: np.ndarray, music_declared: bool) -> dict:
    """Mains hum (50/60Hz families): must be TONAL (narrowband prominence over its
    spectral neighborhood) and PERSISTENT (present in most 2s windows at a stable
    frequency). Music harmonic content is broadband-neighbored and note-varying,
    so it fails both criteria. Severity = how far the tone sits under the speech band.
    """
    issues = []
    nper = min(65536, len(x))
    f, psd = sps.welch(x, fs=SR, nperseg=nper)
    df = f[1] - f[0]
    speech_pow = float(psd[(f >= 300) & (f <= 3400)].sum() * df)

    def prominence(freqs, p, fc):
        d = freqs[1] - freqs[0]
        sel = (freqs >= fc - 3) & (freqs <= fc + 3)
        if not sel.any():
            return None, 0.0, 0.0
        f0 = float(freqs[sel][np.argmax(p[sel])])
        core = (freqs >= f0 - 2.5) & (freqs <= f0 + 2.5)
        band = float(p[core].sum() * d)
        neigh = ((freqs >= f0 - 35) & (freqs <= f0 - 6)) | ((freqs >= f0 + 6) & (freqs <= f0 + 35))
        floor = float(np.median(p[neigh])) * 5.0 if neigh.any() else 1e-18
        return f0, band, 10 * np.log10(band / (floor + 1e-18) + 1e-12)

    # persistence windows
    wlen = 2 * SR
    nwin = max(1, len(x) // wlen)
    win_psds = []
    for i in range(nwin):
        seg = x[i * wlen:(i + 1) * wlen]
        if len(seg) < SR:
            continue
        fw, pw = sps.welch(seg, fs=SR, nperseg=min(32768, len(seg)))
        win_psds.append((fw, pw))

    # Measure every grid harmonic of both mains families
    measured = []  # (base, harmonic_freq, f0, prom, persist, below_speech)
    diag = []
    for base in (50, 60):
        for k in range(1, 13):
            c = k * base
            if c > 620:
                break
            f0, band_pow, prom = prominence(f, psd, c)
            if f0 is None:
                continue
            # mains hum sits ON the grid; musical drones (equal temperament) miss
            # this window — 49.0/51.9/58.3/61.7Hz etc. are all >0.7Hz off-grid
            if abs(f0 - c) > HUM_GRID_TOL_HZ:
                diag.append({"freq_hz": round(f0, 1), "grid_hz": c, "off_grid": True,
                             "prominence_db": round(prom, 1)})
                continue
            persist_hits = 0
            for fw, pw in win_psds:
                _, _, wprom = prominence(fw, pw, f0)
                if wprom >= 6.0:
                    persist_hits += 1
            persist = persist_hits / len(win_psds) if win_psds else 0.0
            below_speech = 10 * np.log10(speech_pow / (band_pow + 1e-18) + 1e-12)
            measured.append((base, c, f0, prom, persist, below_speech))
            diag.append({"freq_hz": round(f0, 1), "grid_hz": c,
                         "prominence_db": round(prom, 1), "persistence": round(persist, 2),
                         "below_speech_db": round(below_speech, 1)})

    for base in (50, 60):
        fam = [m for m in measured if m[0] == base
               and m[3] >= HUM_FAMILY_PROM_DB and m[4] >= HUM_FAMILY_PERSIST]
        for (b, c, f0, prom, persist, below_speech) in measured:
            if b != base:
                continue
            primary = prom >= HUM_PROM_DB and persist >= HUM_PERSIST
            solo_ok = prom >= HUM_SOLO_PROM_DB and persist >= HUM_SOLO_PERSIST
            if not (primary and (len(fam) >= 2 or solo_ok)):
                continue
            if below_speech < HUM_ERR_BELOW_SPEECH_DB:
                sev = "error"
            elif below_speech < HUM_WARN_BELOW_SPEECH_DB:
                sev = "warn"
            else:
                continue  # tonal but buried — inaudible
            issues.append({
                "frequency_hz": round(f0, 1),
                "prominence_db": round(prom, 1),
                "persistence": round(persist, 2),
                "harmonic_family_members": len(fam),
                "hum_vs_speech_db": round(below_speech, 1),
                "severity": sev,
                "problem": f"persistent {round(f0)}Hz mains hum "
                           f"({round(prom, 1)}dB prominent, {round(below_speech, 1)}dB below speech)",
            })

    diag.sort(key=lambda d: -d["prominence_db"])
    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error, "music_declared": music_declared,
            "top_candidates": diag[:3], "issues": issues}


def check_clean_open_close(vdb: np.ndarray, times: np.ndarray, speech_db: float,
                           duration: float) -> dict:
    """Dead air at head/tail, measured on the voice-band envelope anchored at the
    clip edges (a music bed can't register as either voice or 'dead air')."""
    issues = []
    thr = speech_db - 12
    mask = vdb >= thr

    i = first_sustained(mask, 6)  # 60ms sustained voice
    onset_s = float(times[i]) if i >= 0 else duration
    j = last_sustained(mask, 6)
    voice_end_s = float(times[j]) + 0.025 if j >= 0 else 0.0
    tail_s = max(0.0, duration - voice_end_s)

    if onset_s > OPEN_WARN_S:
        issues.append({
            "location": "open", "dead_air_ms": round(onset_s * 1000),
            "severity": "error" if onset_s > OPEN_ERR_S else "warn",
            "problem": f"{round(onset_s * 1000)}ms before first voice",
        })
    if tail_s > CLOSE_WARN_S:
        issues.append({
            "location": "close", "tail_ms": round(tail_s * 1000),
            "severity": "error" if tail_s > CLOSE_ERR_S else "warn",
            "problem": f"{round(tail_s * 1000)}ms trailing after last voice",
        })

    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error, "open_onset_ms": round(onset_s * 1000),
            "close_tail_ms": round(tail_s * 1000), "issues": issues}


def check_music_balance(x: np.ndarray, vdb: np.ndarray, times: np.ndarray,
                        speech_db: float, duration: float, meta: dict) -> dict:
    """If a music bed is declared, measure voice-over-bed separation in speech gaps."""
    music = meta.get("music_path")
    if not music:
        return {"pass": True, "issues": [], "note": "no music bed declared"}

    fdb, ftimes = frame_env_db(x)  # full-band envelope
    n = min(len(fdb), len(vdb))
    fdb, vdb_c, t_c = fdb[:n], vdb[:n], times[:n]

    fade_guard = max(1.5, meta.get("music_fade_out", 0) + 0.3)
    in_body = (t_c >= 1.0) & (t_c <= duration - fade_guard)
    gap_mask = (vdb_c <= speech_db - 22) & in_body
    i = first_sustained(gap_mask, 40)  # ≥0.4s gap
    if i < 0:
        return {"pass": True, "issues": [],
                "note": "music declared but no isolatable speech gap ≥0.4s — manual ear check"}

    # all gap frames (member of any sustained run)
    conv = np.convolve(gap_mask.astype(int), np.ones(40, dtype=int), mode="same")
    gap_frames = conv >= 38
    bed_db = float(np.median(fdb[gap_frames]))
    voiced = vdb_c >= speech_db - 12
    voice_db = float(np.median(fdb[voiced])) if voiced.any() else speech_db
    sep = voice_db - bed_db

    issues = []
    if sep < MUSIC_ERR_DB:
        issues.append({"severity": "error", "voice_over_bed_db": round(sep, 1),
                       "problem": f"music bed only {round(sep, 1)}dB under voice (min {MUSIC_ERR_DB})"})
    elif sep < MUSIC_WARN_DB:
        issues.append({"severity": "warn", "voice_over_bed_db": round(sep, 1),
                       "problem": f"music bed {round(sep, 1)}dB under voice (target 10-13)"})
    has_error = any(i["severity"] == "error" for i in issues)
    return {"pass": not has_error, "voice_over_bed_db": round(sep, 1),
            "bed_measured_in_gaps_s": round(float(gap_frames.sum()) * 0.01, 1),
            "music": os.path.basename(music), "issues": issues}


def main():
    parser = argparse.ArgumentParser(description="Audit audio quality on a rendered clip")
    parser.add_argument("--clip", required=True, help="Path to rendered clip mp4")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args()

    clip = args.clip
    if not os.path.exists(clip):
        print(f"ERROR: clip not found: {clip}", file=sys.stderr)
        sys.exit(1)

    meta = resolve_clip_meta(clip) if resolve_clip_meta else {}
    duration = get_duration(clip)
    print(f"Clip: {clip} ({round(duration, 1)}s) | music bed: "
          f"{'yes' if meta.get('music_path') else 'no/unknown'}")

    x = load_audio(clip)
    if len(x) < SR:
        # No decodable audio (no stream / silent / corrupt) — emit a structured FAIL
        # so the autonomous audit fan-out gets a verdict, not just a non-zero exit.
        fail = {
            "clip": os.path.basename(clip), "duration_s": round(duration, 1),
            "verdict": "FAIL",
            "checks": {"audio_stream": {"pass": False, "issues": [
                {"severity": "error", "problem": "no decodable audio stream (no audio / silent / corrupt)"}]}},
            "summary": "FAIL: audio_stream. First: no decodable audio stream (no audio / silent / corrupt)",
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(fail, f, indent=2)
        print("FAIL: no decodable audio stream", file=sys.stderr)
        print(f"Report: {args.out}")
        sys.exit(1)

    vx = voice_band(x)
    vdb, vtimes = frame_env_db(vx)
    speech_db = speech_level_db(vdb)

    results = {"clip": os.path.basename(clip), "duration_s": round(duration, 1),
               "speech_level_db": round(speech_db, 1)}
    checks = {}

    print("Checking word clipping (ASR hypothesis + acoustic confirmation)...")
    checks["word_clipping"] = check_word_clipping(clip, vdb, speech_db)

    print("Checking pops/clicks...")
    import tempfile
    wav = tempfile.mktemp(suffix=".wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", clip, "-vn", "-ac", "1",
                    "-ar", str(SR), wav], capture_output=True)
    try:
        checks["pops_clicks"] = check_pops_clicks(wav)
    finally:
        if os.path.exists(wav):
            os.unlink(wav)

    print("Checking clipping/distortion (sample railing at 0dBFS)...")
    checks["clipping_distortion"] = check_clipping_distortion(x)

    print("Checking level consistency...")
    checks["level_consistency"] = check_level_consistency(vdb, vtimes, speech_db, duration)

    print("Checking buzz/hum (tonality + persistence)...")
    checks["buzz_hum"] = check_buzz_hum(x, bool(meta.get("music_path")))

    print("Checking clean open/close (voice-band, anchored)...")
    checks["clean_open_close"] = check_clean_open_close(vdb, vtimes, speech_db, duration)

    print("Checking music balance...")
    checks["music_balance"] = check_music_balance(x, vdb, vtimes, speech_db, duration, meta)

    any_fail = any(not c["pass"] for c in checks.values())
    results["verdict"] = "FAIL" if any_fail else "PASS"
    results["checks"] = checks
    results["metadata"] = {
        "music_declared": bool(meta.get("music_path")),
        "segments": len(meta.get("segments", [])),
        "resolved_from": "contract/manifest" if meta.get("has_metadata") else "none",
    }

    failures = [k for k, v in checks.items() if not v["pass"]]
    if failures:
        first = checks[failures[0]]["issues"][0] if checks[failures[0]]["issues"] else {}
        results["summary"] = f"FAIL: {', '.join(failures)}. First: {first.get('problem', 'see details')}"
    else:
        results["summary"] = "All audio checks passed"

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n{results['verdict']}: {results['summary']}")
    print(f"Report: {args.out}")


if __name__ == "__main__":
    main()
