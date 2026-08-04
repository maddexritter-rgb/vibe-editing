#!/usr/bin/env python3
"""reqc — automated per-clip re-QC gate. Run on EVERY delivered clip, EVERY render/revision.

THE LESSON THIS ENCODES (StayInAGreatMood, 2026-06-12): the batch shipped clean the first round
because every clip passed a full QC gate. Then revisions only spot-checked the ONE thing changed,
the gate was skipped, and regressions (double-takes, weak openers, leaked connectives) shipped. An
autonomous workflow CANNOT rely on the operator remembering to re-check — the gate must run itself
on every render and BLOCK delivery on FAIL.

What it checks (by MEASUREMENT on the DELIVERED .mp4, not the cut spec):
  • OPENER     — first audible word is a content/hook word, not a filler/connective fragment
  • LEAD       — first speech within ~150ms of frame 1 (no dead-air / leaked-tail head)
  • DOUBLE-TAKE— consecutive repeated 3–5-word phrases (Whisper exposes restarts as repeats;
                 also catches the "absorbed in a stretched token" false-start that text-reading misses)
  • FILLER     — um/uh/etc. anywhere in the clip
  • MECHANICS  — 2160×3840, video≈audio duration, no stray data stream, ends on a live (non-black) frame

What it CANNOT check — do these by EAR / waveform, NEVER by re-transcript:
  • CLIPPED WORDS — ASR re-reads a chopped word as whole. A re-transcript will say "PASS" on a clip
    whose last consonant is sliced. Confirm word integrity with a spectrogram/waveform or by listening.
    (2026-06-12: an ASR-timestamp "clipped-word scan" flagged every sentence-final word — pure noise.)

Usage:
    python3 reqc.py <delivered.mp4> [--project <clip_project_dir>]   # one clip
    python3 reqc.py --batch <parent_20_DELIVER/vN>                   # every SPEAKER_*.mp4 in a folder
Exit 0 = all PASS · 1 = at least one FAIL (BLOCK delivery) · 2 = setup error.
Generic — no brand baked in.
"""
from __future__ import annotations
# ── vibe-editing portable path bootstrap (auto-inserted) ──
# ── engine bundled-keys autoload (config/keys.env) ──
import os as _ko, pathlib as _kp
def _acq_load_keys():
    d = _kp.Path(__file__).resolve()
    for p in (d, *d.parents):
        if (p / ".claude-plugin").is_dir():
            f = p / "config" / "keys.env"
            if f.is_file():
                for _ln in f.read_text().splitlines():
                    _ln = _ln.strip()
                    if _ln and not _ln.startswith("#") and "=" in _ln:
                        _k, _v = _ln.split("=", 1); _k, _v = _k.strip(), _v.strip()
                        if _k and "PASTE" not in _v and not _ko.environ.get(_k):
                            _ko.environ[_k] = _v
            return
_acq_load_keys()
# ── end keys ──
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
import argparse, json, os, re, subprocess, sys, tempfile
from pathlib import Path

# Words that signal the clip opened on a MID-SENTENCE fragment / leaked connective, not a hook.
# DELIBERATELY conservative: only clear discourse-markers/conjunctions. A pronoun/article/verb
# (you/i/it/the/that/is) is a VALID hook start ("You will die", "It's rarer than…", "The single
# greatest skill…") — flagging those = false FAIL = needless re-cut thrashing (the thing we're
# killing). The human ledger + EAR catch the subtle leaked openers this list intentionally allows.
BAD_OPENERS = {"and","so","but","or","nor","um","uh","ah","mm","like","yeah","yep","well","okay",
               "ok","right","also","because","cause","cool","basically","actually","honestly",
               "literally","anyway","plus","mmhmm","mhm"}
FILLERS = {"um","uh","ah","mm","mmhmm","mhm","uhh","umm","er"}


def _key():
    k = os.environ.get("GROQ_API_KEY")
    if k:
        return k
    zsh = Path.home() / ".zshrc"
    if zsh.exists():
        m = re.search(r'GROQ_API_KEY=["\']?([A-Za-z0-9_\-]+)', zsh.read_text())
        if m:
            return m.group(1)
    return None


def transcribe(mp4: Path, key: str) -> dict:
    mp3 = tempfile.mktemp(suffix=".mp3")
    subprocess.run(["ffmpeg","-y","-v","error","-i",str(mp4),"-ac","1","-ar","16000",
                    "-c:a","libmp3lame","-b:a","64k",mp3], check=True)
    r = subprocess.run(["curl","-s","https://api.groq.com/openai/v1/audio/transcriptions",
        "-H",f"Authorization: Bearer {key}","-F",f"file=@{mp3}","-F","model=whisper-large-v3",
        "-F","response_format=verbose_json","-F","timestamp_granularities[]=word"],
        capture_output=True, text=True)
    os.unlink(mp3)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"words": [], "text": ""}


def double_takes(words):
    norm = [re.sub(r"[^a-z0-9']","",w["word"].lower()) for w in words]
    hits = []
    for n in (5,4,3):                      # longest first
        for i in range(len(norm)-2*n):
            a, b = norm[i:i+n], norm[i+n:i+2*n]
            if a == b and all(a):
                hits.append((round(words[i]["start"],1), " ".join(a)))
    # de-dupe overlapping reports
    seen, out = set(), []
    for t, p in sorted(hits):
        k = round(t)
        if k not in seen:
            seen.add(k); out.append((t, p))
    return out


def probe(mp4: Path) -> dict:
    p = json.loads(subprocess.run(["ffprobe","-v","error","-show_entries",
        "stream=codec_type,width,height","-show_entries","format=duration","-of","json",str(mp4)],
        capture_output=True, text=True).stdout)
    return p


def last_frame_luma(mp4: Path) -> float:
    png = tempfile.mktemp(suffix=".png")
    subprocess.run(["ffmpeg","-y","-v","error","-sseof","-0.15","-i",str(mp4),"-frames:v","1",
                    "-vf","scale=64:114","-update","1",png], capture_output=True)
    if not Path(png).exists():
        return -1.0
    try:
        from PIL import Image
        import statistics
        m = statistics.mean(Image.open(png).convert("L").getdata())
    except Exception:
        m = 99.0
    finally:
        Path(png).unlink(missing_ok=True)
    return m


def _integrated_lufs(mp4: Path):
    """Integrated loudness (LUFS) via ffmpeg loudnorm measurement pass. Returns float or None.
    Added 2026-06-17 after StageQA V2 shipped at -19.9 LUFS (~4dB under the -16 voice target = quiet vs peers)."""
    try:
        r = subprocess.run(["ffmpeg", "-i", str(mp4), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=180)
        m = re.search(r'"input_i"\s*:\s*"?(-?[0-9.]+)"?', r.stderr)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def _ending_chop(mp4: Path):
    """ACOUSTIC end-of-clip check. The TEXT ending gate is fooled when ASR re-reads a clipped word as
    whole ("...people." reads complete even though the audio cut mid-word). So measure the waveform:
    if the final ~0.12s is still at FULL speech energy (no decay as the word completes), the word was
    chopped. Added 2026-06-17 after an example clip V2 ended mid-word on a "complete"-looking
    transcript. Returns an issue string or None. (Music bed ~-29dB won't trip it; we key on voice-level peaks.)"""
    try:
        def _maxvol(args):
            o=subprocess.run(["ffmpeg","-nostdin","-hide_banner",*args,"-af","volumedetect","-f","null","-"],
                             capture_output=True,text=True).stderr
            mx=re.search(r"max_volume:\s*(-?[0-9.]+)",o); return float(mx.group(1)) if mx else None
        clip_max = _maxvol(["-i",str(mp4)])                       # whole clip
        fin_max  = _maxvol(["-sseof","-0.20","-i",str(mp4)])      # last 0.20s (seek-from-end → no dur needed)
        # A word cut at its TRUE acoustic end DECAYS through its final fraction. A SHAVED word (cut 0.1-0.25s
        # early — Whisper's end label runs early) is still at near the clip's PEAK speech level at the cut.
        # RELATIVE measure (robust to per-clip loudness): final-0.2s peak within ~4dB of the clip's loudest
        # peak AND loud in absolute. Calibrated on an example clip V2 (final-0.2s max -3.8 vs clip max -1.8 = shaved).
        if fin_max is not None and clip_max is not None and fin_max > -9.0 and (clip_max - fin_max) < 4.0:
            return (f"ENDING likely CHOPPED/shaved — final 0.20s peaks at {fin_max:.1f}dB, within "
                    f"{clip_max-fin_max:.1f}dB of the clip's loudest speech ({clip_max:.1f}dB): no word-end decay. "
                    f"VERIFY BY EAR, then extend the last keep-span to the word's TRUE acoustic end (precision_cut).")
    except Exception:
        return None
    return None

def qc_one(mp4: Path, key: str, project=None) -> dict:
    issues = []
    d = transcribe(mp4, key)
    ws = d.get("words", [])
    opener = ws[0]["word"] if ws else ""
    if ws:
        first = re.sub(r"[^a-z']","",ws[0]["word"].lower())
        if first in BAD_OPENERS:
            issues.append(f"OPENER='{ws[0]['word']}' (filler/connective, not a hook word)")
        if ws[0]["start"] > 0.15:
            issues.append(f"LEAD={ws[0]['start']:.2f}s of dead air before first word")
        dt = double_takes(ws)
        if dt:
            issues.append("DOUBLE-TAKE @ " + ", ".join(f"{t}s '{p}'" for t, p in dt[:3]))
        fl = sorted({w["word"] for w in ws if re.sub(r"[^a-z']","",w["word"].lower()) in FILLERS})
        if fl:
            issues.append(f"FILLER {fl}")
    else:
        issues.append("NO TRANSCRIPT")
    pr = probe(mp4)
    streams = pr.get("streams", [])
    v = next((s for s in streams if s["codec_type"]=="video"), {})
    a = next((s for s in streams if s["codec_type"]=="audio"), None)
    extra = [s for s in streams if s["codec_type"] not in ("video","audio")]
    # 9:16 verticals ship at 4K OR 1080 — 1080 is the correct call when the manifest's
    # reframe res is 1080 (e.g. extreme-wide source where a 4K output would be upscale mush).
    if (v.get("width"), v.get("height")) not in ((2160, 3840), (1080, 1920)):
        issues.append(f"RES {v.get('width')}x{v.get('height')} (want 2160x3840 or 1080x1920)")
    if extra:
        issues.append(f"{len(extra)} stray non-AV stream(s)")
    if a is None:
        issues.append("no audio stream")
    else:
        # LOUDNESS gate (2026-06-17): a Q&A clip must sit at the -16 LUFS voice target, not play quiet
        # vs platform peers. StageQA V2 shipped at -19.9 LUFS (~4dB under) — caught by ear, not by a gate.
        lufs = _integrated_lufs(mp4)
        if lufs is not None and abs(lufs - (-16.0)) > 2.0:
            issues.append(f"LOUDNESS {lufs:.1f} LUFS (want -16 ±2; off {lufs - (-16.0):+.1f}dB) — re-run the mix/loudnorm to ~-16")
    luma = last_frame_luma(mp4)
    if 0 <= luma <= 16:
        issues.append(f"last frame near-black (luma {luma:.0f}) — fade/black tail")
    # JUMP-CUT gate (2026-06-12): a WITHIN-TAKE silence trim that removed a pause while the subject
    # was MOVING teleports his body across the seam ("you didn't fully make the cut"). 61 of these
    # shipped in one batch from the dead-air splitter. Detect: at each same-take seam (a cut whose
    # removed source gap is PURE SILENCE — no words — i.e. a pause-trim, not a content edit), measure
    # the full-frame pixel delta across the seam; a big delta on a pause-trim = the subject jumped.
    # Content cuts (gap has words) are EXEMPT — their jump is the intended cost of removing a take.
    jumps = _jumpcut_scan(mp4, project)
    if jumps:
        issues.append("JUMP-CUT (within-take pause-trim, subject moved) @ "
                      + ", ".join(f"{t}s diff{d}" for t, d in jumps[:4]))
    # ENDING gate (2026-06-17, CMO note "the AI cuts off the speaker and goes to the end bumper"):
    # a clip must NEVER end mid-sentence. Universal — all domains. Connector endings caught from the
    # clip's own tail; the content-word-continues case needs --project (source words) to see it.
    end_issue = _ending_verdict(ws, project)
    if end_issue:
        issues.append(end_issue)
    chop = _ending_chop(mp4)
    if chop:
        issues.append(chop)
    return {"clip": mp4.name, "verdict": "FAIL" if issues else "PASS",
            "opener": opener, "first6": " ".join(w["word"] for w in ws[:6]), "issues": issues}


def _ending_verdict(ws, project):
    """Return an issue string if the clip ends mid-sentence (false ending), else None. Uses the
    canonical shared rule (_shared/ending_check.py). Tail-only catches a connector ending on ANY
    clip; with --project (source words.json + cuts.json) it also catches a content word where the
    SAME speaker keeps going (the TenOutOfTen '...next level' / GivingItAway '...all away,' class)."""
    try:
        import sys as _sys
        _sys.path.insert(0, VIBE_SHARED)
        from ending_check import tail_only_verdict, ends_complete
    except Exception:
        return None
    verdict, reason = tail_only_verdict(ws[-8:] if ws else [])
    if verdict == "fail":
        return f"FALSE-ENDING — {reason}"
    if project:
        proj = Path(project); cuts_f = proj / "10_WORK/cuts.json"; words_f = proj / "10_WORK/words.json"
        if cuts_f.exists() and words_f.exists():
            try:
                segs = json.loads(cuts_f.read_text()).get("segments", [])
                src = json.loads(words_f.read_text()).get("words", [])
                if segs and src:
                    out_t = float(segs[-1]["out"])
                    kept = [w for w in src if w.get("end", 0) <= out_t + 0.05]
                    if kept:
                        nxt = [w for w in src if w.get("start", 0) >= out_t - 0.05][:15]
                        ok, why = ends_complete(kept[-1], nxt)
                        if not ok:
                            return f"FALSE-ENDING — {why}"
            except Exception:
                pass
    return None


def _jumpcut_scan(mp4, project):
    """Flag same-take (pure-silence) seams with high cross-seam motion. Needs the clip project
    (cuts.json for seam times + the session words.json to classify the gap). No project → skip."""
    if not project:
        return []
    proj = Path(project)
    cuts_f = proj / "10_WORK/cuts.json"
    words_f = proj / "10_WORK/words.json"
    if not cuts_f.exists():
        return []
    try:
        import cv2, numpy as np
    except Exception:
        return []
    segs = json.loads(cuts_f.read_text()).get("segments", [])
    words = json.loads(words_f.read_text()).get("words", []) if words_f.exists() else []
    def gap_has_words(t0, t1):
        return any(t0 - 0.02 <= w["start"] and w["end"] <= t1 + 0.02 and w["start"] < w["end"]
                   for w in words)
    def frame(t):
        p = tempfile.mktemp(suffix=".png")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(mp4),
                        "-frames:v", "1", "-vf", "scale=480:854", "-update", "1", p], capture_output=True)
        img = cv2.imread(p, cv2.IMREAD_GRAYSCALE) if Path(p).exists() else None
        if Path(p).exists(): Path(p).unlink()
        return img
    out, t = [], 0.0
    for i, s in enumerate(segs[:-1]):
        t += float(s["out"]) - float(s["in"])
        gap0, gap1 = float(s["out"]), float(segs[i + 1]["in"])
        gapdur = gap1 - gap0
        if not (0.4 <= gapdur < 2.5) or gap_has_words(gap0, gap1):  # <0.4s removed can't teleport (natural-motion false +)
            continue  # content cut or big skip — jump is intended/exempt
        a, b = frame(max(0, t - 0.10)), frame(t + 0.10)
        if a is None or b is None:
            continue
        import numpy as np
        diff = float(np.mean(np.abs(a.astype(int) - b.astype(int))))
        if diff > 14:
            out.append((round(t, 1), round(diff, 1)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="delivered .mp4")
    ap.add_argument("--project", help="clip project dir (enables the JUMP-CUT gate: needs cuts.json + words.json)")
    ap.add_argument("--batch", help="folder of SPEAKER_*.mp4 to QC")
    a = ap.parse_args()
    key = _key()
    if not key:
        print("ERROR: no GROQ_API_KEY", file=sys.stderr); return 2
    targets = []
    if a.batch:
        targets = sorted(Path(a.batch).glob("*.mp4"))
    elif a.target:
        targets = [Path(a.target)]
    else:
        print("give a .mp4 or --batch <dir>", file=sys.stderr); return 2
    any_fail = False
    for mp4 in targets:
        r = qc_one(mp4, key, a.project)
        if r["verdict"] == "FAIL":
            any_fail = True
        print(f"{r['verdict']:4s} {mp4.name}")
        print(f"      opens: '{r['first6']}'")
        for i in r["issues"]:
            print(f"      ✗ {i}")
    print(f"\n{'BLOCK — fix FAILs before delivery' if any_fail else 'ALL PASS'}")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
