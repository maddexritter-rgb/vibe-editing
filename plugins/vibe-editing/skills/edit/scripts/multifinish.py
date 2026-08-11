#!/usr/bin/env python3
"""multifinish.py --config CFG <ep> <t0_wav> <t1_wav> <clip.mp4> <out.mp4>
                  --music PATH [--single] [--no-tight] [--speaker-map JSON]
RUTHLESS jumpcut → dual-color SPICE captions (host=white / guest=yellow) → vibe music bed.

The jumpcut compresses the timeline, so SPEAKER color is remapped per word:
  FINAL(tight)-time → continuous-clip time (via jumpcut's kept segments) → wav-time (+t0)
  → clean.json speaker.

Captions are re-transcribed off the FINAL clip via whisper.cpp DTW for accurate timing.
Director_x (LLM, expressive spice) sets weight/size/italic. Its per-word COLOR is STRIPPED
and the SPEAKER governs color via voice_spans — guest runs get yellow, everything else
defaults to host (white) per the spice preset's default_voice.

Leading/trailing filler ("yeah/mm/so/and/well/okay/right/um/like/...") auto-trimmed from
the caption norm + a 0.12 s audio fade-in masks contiguous-filler bleed.

ENDING RULE (locked, NEVER break): hard-cut on the payoff. NO video fade. NO frozen frame.
Only the music gets a 0.6 s tail.

Music: arg is an absolute path OR a filename relative to config.music_folder; loudnorm
I=-29:TP=-3 so every bed sits at a consistent ~13 dB under the −16 LUFS voice.

--single skips the speaker-based dual-color pass (use when the whole clip is one speaker).
--no-tight skips jumpcut (use only when the source is already tight).
--speaker-map gives pre-computed guest(yellow) ranges in CONTINUOUS-clip time (0-based) — recut.py
passes this for multi-segment clips (called with t0=0) so dual-color stays right ACROSS the concat
seam, where the single-offset t0->wav mapping can't. Absent → speaker color comes from clean.json."""
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
import argparse, json, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # .../multicam-podcast-clipper/scripts
SKILL = HERE.parent                              # .../multicam-podcast-clipper
CAP = _acq("caption-clips")
NORMALIZE = CAP / "scripts/normalize_simple.py"
SPICE_NORM = CAP / "scripts/spice_normalize.py"
GEN_SPICE = CAP / "scripts/generate_spice.py"
JUMPCUT = CAP / "scripts/jumpcut.py"
DIRECTOR = HERE / "director_x.py"
PRESET = SKILL / "presets/spice.json"  # ONE preset; generate_spice --burn is resolution-adaptive (1080/4K)
MODEL = Path.home() / ".claude-video-vision/models/ggml-large-v3.bin"
import shutil as _shutil
WCLI = _os.environ.get("WHISPER_CLI") or _shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"


def run(c, **k):
    return subprocess.run([str(x) for x in c], capture_output=True, text=True, **k)


def dur(p):
    return float(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)]).strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("ep")
    ap.add_argument("t0", type=float)
    ap.add_argument("t1", type=float)
    ap.add_argument("clip")
    ap.add_argument("out")
    ap.add_argument("--music", required=True)
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--no-tight", action="store_true")
    ap.add_argument("--speaker-map", type=Path, default=None,
                    help="JSON list of guest(yellow) ranges in CONTINUOUS-clip time (0-based). "
                         "When set, overrides clean.json speaker lookup so dual-color stays correct "
                         "across a multi-segment concat seam (recut.py; call with t0=0).")
    a = ap.parse_args()
    cfg = json.loads(a.config.read_text())
    root = Path(cfg["project_root"]).expanduser()
    work_root = root / "10_WORK"
    capwork = work_root / "capwork"
    capwork.mkdir(parents=True, exist_ok=True)

    vocab = list(cfg.get("vocab", []))
    context = cfg.get("context",
                      "two-person podcast (host + guest) giving punchy conversational advice")
    clip = Path(a.clip)
    cid = clip.stem

    # 0. RUTHLESS jumpcut (cap pauses; solo-clip SOP). Capture kept segments for color remap.
    segs = [(0.0, dur(clip))]
    src = clip
    if not a.no_tight:
        tight = capwork / f"{cid}_tight.mp4"
        r = run([sys.executable, JUMPCUT, clip, tight,
                 "--max-pause", "0.12", "--noise", "-30dB",
                 "--min-detect", "0.15", "--crf", "18"])
        ks = [(float(m[0]), float(m[1])) for m in re.findall(r'keep\s+([\d.]+)[^\d]+([\d.]+)', r.stdout)]
        if tight.exists() and ks:
            segs, src = ks, tight
        else:
            print("jumpcut skipped:", (r.stderr or r.stdout)[-200:])

    def tight_to_wav(tf):
        """FINAL(tight) seconds → wav-time."""
        acc = 0.0
        for s, e in segs:
            L = e - s
            if tf <= acc + L + 1e-6:
                return s + (tf - acc) + a.t0
            acc += L
        return segs[-1][1] + a.t0

    clean = json.loads((work_root / f"transcripts/{a.ep}_clean.json").read_text())
    smap = json.loads(a.speaker_map.read_text()) if a.speaker_map else None

    def speaker_at(wt):
        if smap is not None:                        # pre-computed guest ranges (recut multi-segment, t0=0)
            return "guest" if any(s <= wt <= e for s, e in smap) else "host"
        for u in clean:
            if u["start"] <= wt <= u["end"]:
                return u["speaker"]
        return min(clean, key=lambda u: min(abs(u["start"] - wt), abs(u["end"] - wt)))["speaker"]

    # 1. transcribe the FINAL clip (whisper.cpp DTW → accurate caption timing)
    wav = capwork / f"{cid}.wav"
    run(["ffmpeg", "-y", "-i", src,
         "-af", "loudnorm=I=-18,highpass=f=70",
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav])
    of = capwork / f"{cid}_jf"
    run([WCLI, "-m", MODEL, "-f", wav, "-dtw", "large.v3", "-ojf", "-mc", "0", "-of", of])
    d = json.load(open(str(of) + ".json"))
    words = []
    for seg in d.get("transcription", []):
        for tok in seg.get("tokens", []):
            t = tok.get("text", "")
            if not t or t.startswith("["):
                continue
            off = tok.get("offsets", {})
            if "from" not in off:
                continue
            s, e = off["from"] / 1000.0, off["to"] / 1000.0
            if t.startswith(" ") or not words:
                words.append({"word": t.strip(), "start": s, "end": e})
            else:
                words[-1]["word"] += t
                words[-1]["end"] = e
    words = [w for w in words if w["word"].strip()]
    raw = capwork / f"{cid}_raw.json"
    raw.write_text(json.dumps({"words": words}, indent=1))

    # 2. normalize + proper-noun pass (from config.vocab)
    norm = capwork / f"{cid}_norm.json"
    run([sys.executable, NORMALIZE, raw, norm])
    run([sys.executable, SPICE_NORM, norm, norm])
    dd = json.loads(norm.read_text())
    capn = {n.lower(): n for n in vocab}
    for w in dd["words"]:
        m = re.match(r"^([\$]?[\w'\-\.]+)([.,!?]*)$", w["word"])
        if m and m.group(1).lower() in capn:
            w["word"] = capn[m.group(1).lower()] + m.group(2)
    norm.write_text(json.dumps(dd, indent=1))
    ws = dd["words"]

    # Drop leading/trailing filler so captions never open/close on "yeah/mm/so/and/well/okay..."
    FILL = {"yeah", "mm", "hmm", "mmhmm", "mhm", "so", "and", "well", "okay", "ok",
            "right", "um", "uh", "oh", "like", "totally", "yep", "yes"}
    nzf = lambda w: "".join(c for c in w.lower() if c.isalnum())
    while len(ws) > 2 and nzf(ws[0]["word"]) in FILL:
        ws.pop(0)
    while len(ws) > 2 and nzf(ws[-1]["word"]) in FILL:
        ws.pop()
    dd["words"] = ws
    norm.write_text(json.dumps(dd, indent=1))

    # 3. director_x (weight/size/italic), strip its per-word color — SPEAKER governs color
    style = capwork / f"{cid}_style.json"
    run([sys.executable, DIRECTOR, norm, "--out", style, "--context", context])
    st = json.loads(style.read_text()) if style.exists() else {"words": {}}
    for v in st.get("words", {}).values():
        v.pop("c", None)

    # 4. SPEAKER color: per-word speaker (tight→wav→clean) → contiguous guest runs → voice_spans
    spans = []
    if not a.single:
        runs = []
        for w in ws:
            spk = speaker_at(tight_to_wav((w["start"] + w["end"]) / 2))
            if runs and runs[-1][2] == spk:
                runs[-1][1] = w["end"]
            else:
                runs.append([w["start"], w["end"], spk])
        spans = [[round(s, 2), round(e + 0.05, 2), "guest"] for s, e, sp in runs if sp == "guest"]
    st["voice_spans"] = spans
    style.write_text(json.dumps(st, indent=1))

    # 5. spice + burn onto the FINAL clip
    ass = capwork / f"{cid}.ass"
    capped = capwork / f"{cid}_cap.mp4"
    run([sys.executable, GEN_SPICE, norm,
         "--preset", PRESET, "--out", ass, "--style", style,
         "--burn", src, "--burn-out", capped])
    if not capped.exists():
        sys.exit(f"caption burn FAILED for {cid}")

    # 6. vibe music bed + deliver. Music arg is absolute OR relative to cfg.music_folder.
    # RULE (locked): NEVER fade the video out — hard-end on the payoff. Only the MUSIC gets a
    # short 0.6 s tail so it doesn't click; the picture cuts clean on the last word.
    music_arg = Path(a.music)
    if not music_arg.is_absolute():
        music_arg = Path(cfg["music_folder"]).expanduser() / a.music
    music = music_arg
    D = dur(capped)
    musfade = (f"[1:a]loudnorm=I=-29:TP=-3,afade=t=in:d=0.6,"
               f"afade=t=out:st={max(0.1, D - 0.6):.2f}:d=0.6[m]")
    fc = (musfade +
          ";[0:a][m]amix=inputs=2:duration=first:normalize=0,"
          "afade=t=in:d=0.12,alimiter=limit=0.97[a]")
    r = run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", capped, "-stream_loop", "-1", "-i", music,
             "-filter_complex", fc,
             "-map", "0:v:0", "-map", "[a]",
             "-map_metadata", "-1", "-dn",                  # strip cams' auto timecode data track
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", a.out])
    ok = Path(a.out).exists()
    print(f"{'OK' if ok else 'FAIL'} {a.out}  ({D:.1f}s tight ← {dur(clip):.0f}s, "
          f"music={music.stem[:22]}, guest_spans={len(spans)})")
    if not ok:
        print(r.stderr[-600:])


if __name__ == "__main__":
    main()
