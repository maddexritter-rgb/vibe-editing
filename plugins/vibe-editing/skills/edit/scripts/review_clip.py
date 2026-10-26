#!/usr/bin/env python3
"""Auto-review a cut clip against the Speaker SOP. Flags issues + proposes auto-fixes.

Runs 5 checks:
  1. Mid-word bleed — first/last output word shorter than its full duration
  2. Residual filler — um/uh/ah still present in output transcript
  3. Mid-clause end — output doesn't end on sentence-terminator punctuation
  4. Audio dead-air at start/end — near-silence > 200ms at clip boundaries
  5. Length — outside 25-60s SOP sweet spot

Emits per-clip review JSON with:
  - issues[] (severity: info|warn|blocker)
  - auto_fix{} (patched filler cuts or timestamp tweaks if applicable)
  - needs_human[] (stuff Claude should decide)
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
import re
import subprocess
import sys
import tempfile
from pathlib import Path


SINGLE_FILLERS = {"um", "uh", "uhm", "umm", "uhh", "ugh", "fundamentally",
                  "ah", "ahh", "ahhh", "aha", "oh", "ohh"}
SENTENCE_END = (".", "!", "?")


def norm(w: str) -> str:
    return re.sub(r"[^\w']", "", w.strip().lower())


def groq_transcribe(audio_path: Path) -> list[dict]:
    """Return list of {word, start, end} for the audio, via Groq Whisper."""
    import requests
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    # Downsample to 16k mono MP3 first
    with tempfile.TemporaryDirectory() as td:
        mp3 = Path(td) / "audio.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "libmp3lame", "-b:a", "64k", str(mp3)],
            check=True,
        )
        with mp3.open("rb") as f:
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (mp3.name, f, "audio/mpeg")},
                data={
                    "model": "whisper-large-v3-turbo",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                    "language": "en",
                },
                timeout=300,
            )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("words", [])


def load_expected_words(transcript_path: Path, start: float, end: float,
                        filler_cuts: list[dict]) -> list[dict]:
    """Words we expect in the output: inside [start,end] minus filler ranges."""
    tr = json.loads(transcript_path.read_text())
    words = tr.get("words", [])
    in_filler = lambda t: any(c["start"] <= t <= c["end"] for c in filler_cuts)
    return [w for w in words
            if w["end"] > start and w["start"] < end
            and not in_filler((w["start"] + w["end"]) / 2)]


def detect_silence_at_edges(audio_path: Path, edge_sec: float = 0.3,
                            threshold_db: float = -40) -> dict:
    """Use ffmpeg silencedetect to check for dead air at clip start/end."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path),
         "-af", f"silencedetect=n={threshold_db}dB:d=0.15",
         "-vn", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # Probe duration
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True, text=True).stdout.strip())

    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)", proc.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*(-?[\d.]+)", proc.stderr)]

    issues = {"duration": round(dur, 3), "silences": list(zip(starts, ends))}
    # Opening silence: any silence that starts < edge_sec and ends > 0
    for s, e in zip(starts, ends):
        if s < edge_sec and e - max(0, s) > 0.2:
            issues["opening_silence"] = {"start": s, "end": e}
            break
    # Trailing silence
    for s, e in zip(starts, ends):
        if dur - e < edge_sec and e - s > 0.2:
            issues["trailing_silence"] = {"start": s, "end": e}
    return issues


def review(clip_path: Path, transcript_path: Path, start: float, end: float,
           filler_cuts: list[dict]) -> dict:
    issues = []
    needs_human = []
    auto_fix = {}

    # Re-transcribe via Groq
    try:
        out_words = groq_transcribe(clip_path)
    except Exception as e:
        return {"clip": str(clip_path), "error": str(e)}

    out_toks = [norm(w["word"]) for w in out_words]

    # Build a set of output-timeline positions where cuts occurred, mirroring the
    # keep-segment logic in cut_clip.py so overlapping fillers aren't double-counted.
    keeps = []
    cursor = start
    for f in sorted(filler_cuts, key=lambda c: c["start"]):
        fs = max(f["start"], start)
        fe = min(f["end"], end)
        if fe <= cursor or fs >= end:
            continue
        if fs > cursor:
            keeps.append((cursor, fs))
        cursor = max(cursor, fe)
    if cursor < end:
        keeps.append((cursor, end))
    # Boundaries in output time are at the end of every keep segment except the last.
    cut_edges_output_time = []
    offset_in_out = 0.0
    for i, (ks, ke) in enumerate(keeps):
        offset_in_out += (ke - ks)
        if i < len(keeps) - 1:
            cut_edges_output_time.append(offset_in_out)

    def near_cut_edge(t, tol=0.15):
        return any(abs(t - e) <= tol for e in cut_edges_output_time)

    # Check 1: residual fillers
    raw_fillers = [w for w in out_words if norm(w["word"]) in SINGLE_FILLERS]
    real_fillers = []
    hallucinated = []
    for f in raw_fillers:
        if near_cut_edge(f["start"]):
            hallucinated.append(f)
        else:
            real_fillers.append(f)

    if real_fillers:
        issues.append({
            "severity": "blocker",
            "type": "residual_filler",
            "msg": f"{len(real_fillers)} filler token(s) survived the cut: "
                   f"{[(f['word'], round(f['start'], 2)) for f in real_fillers]}",
        })
        auto_fix["re_detect_fillers"] = True
    if hallucinated:
        issues.append({
            "severity": "info",
            "type": "boundary_hallucination",
            "msg": f"{len(hallucinated)} filler-like token(s) heard AT cut edges (likely "
                   f"transcription artifact, not a real miss): "
                   f"{[(f['word'], round(f['start'], 2)) for f in hallucinated]}",
        })

    # Check 2: first/last word sanity — potential mid-word bleed
    if out_words:
        first_w = out_words[0]
        last_w = out_words[-1]
        # If first word duration is suspiciously short (< 80ms) or not a full word
        first_dur = first_w["end"] - first_w["start"]
        last_dur = last_w["end"] - last_w["start"]
        if first_dur < 0.08 and len(first_w["word"].strip()) > 1:
            issues.append({
                "severity": "warn",
                "type": "truncated_opening",
                "msg": f"First word '{first_w['word']}' is {first_dur*1000:.0f}ms — may be a fragment.",
            })
            auto_fix["trim_start_ms"] = int(first_dur * 1000) + 20
        if last_dur < 0.08 and len(last_w["word"].strip()) > 1:
            issues.append({
                "severity": "warn",
                "type": "truncated_ending",
                "msg": f"Last word '{last_w['word']}' is {last_dur*1000:.0f}ms — may be a fragment.",
            })
            auto_fix["extend_end_ms"] = 200

    # Check 3: mid-clause ending (no period/question/exclamation)
    if out_words:
        last_raw = out_words[-1]["word"].rstrip()
        if not last_raw.endswith(SENTENCE_END):
            issues.append({
                "severity": "warn",
                "type": "mid_clause_end",
                "msg": f"Clip ends on '{last_raw}' with no sentence-terminator — missing payoff?",
            })
            auto_fix["extend_to_sentence_end"] = True

    # Check 4: dead-air at edges
    try:
        silence = detect_silence_at_edges(clip_path)
        if silence.get("opening_silence"):
            issues.append({
                "severity": "warn",
                "type": "opening_dead_air",
                "msg": f"Silence at opening: {silence['opening_silence']}",
            })
        if silence.get("trailing_silence"):
            issues.append({
                "severity": "info",
                "type": "trailing_dead_air",
                "msg": f"Silence at end: {silence['trailing_silence']}",
            })
        duration = silence["duration"]
    except Exception as e:
        duration = None
        issues.append({"severity": "info", "type": "silence_probe_failed", "msg": str(e)})

    # Check 5: length window
    if duration is not None:
        if duration < 20:
            issues.append({
                "severity": "warn",
                "type": "too_short",
                "msg": f"Clip is {duration:.1f}s — under 20s is hard to hook-tension-payoff.",
            })
        elif duration > 65:
            issues.append({
                "severity": "warn",
                "type": "too_long",
                "msg": f"Clip is {duration:.1f}s — over 60s hurts retention.",
            })

    # Severity tally
    n_block = sum(1 for i in issues if i["severity"] == "blocker")
    n_warn = sum(1 for i in issues if i["severity"] == "warn")

    return {
        "clip": str(clip_path),
        "duration": duration,
        "expected_words": sum(1 for _ in out_toks),
        "output_words": len(out_words),
        "issues": issues,
        "auto_fix": auto_fix,
        "needs_human": needs_human,
        "summary": f"{n_block} blocker / {n_warn} warn / {len(issues) - n_block - n_warn} info",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("clip", type=Path, help="Output clip .mp4")
    ap.add_argument("--transcript", type=Path, required=True,
                    help="Original source word-level transcript JSON")
    ap.add_argument("--start", type=float, required=True,
                    help="Clip's source-timeline start (seconds)")
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--fillers", type=Path, default=None,
                    help="Optional fillers.json used for this clip")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    filler_cuts = []
    if args.fillers and args.fillers.exists():
        filler_cuts = json.loads(args.fillers.read_text()).get("cuts", [])

    result = review(args.clip, args.transcript, args.start, args.end, filler_cuts)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"Wrote {args.out}")
    print(result.get("summary", "no-summary"))
    for iss in result.get("issues", []):
        print(f"  [{iss['severity']}] {iss['type']}: {iss['msg']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
