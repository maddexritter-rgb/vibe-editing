#!/usr/bin/env python3
"""precision_cut.py — THE canonical transcript-driven precision cut (GLOBAL, all brands).
Engine behind CLIP_CUTTING_PLAYBOOK.md. Raw take + word-timed transcript + keep-spans -> clean cut:
  - each span ENDS at the word's TRUE ACOUSTIC END (the silence after it), not Whisper's early label
    (Whisper labels word-ends ~0.1-0.25s early -> fading from the label shaves "-s/-t/-th/-ing" off);
  - inflated labels (a long pause swallowed inside one word's label) and mid-word stop-closures handled;
    ⚠️ but true-end detection uses a FIXED -50dB floor — on NOISY/multi-person footage (events, handheld)
    the inter-turn pause is room tone ~-28dB (ABOVE -50dB), so it's invisible and the span can overshoot
    into the next word / an off-camera interviewer tag. There, MEASURE an ADAPTIVE floor (≈ tail
    mean_volume +1dB); ref impl `build_ad.py tail_clean` + CLIP_CUTTING_PLAYBOOK "true-end traps" (2026-06-16).
  - dead air removed at a PER-SPEAKER silence floor (jumpcut.py) -- MEASURE it, don't guess;
  - canon audio chain (highpass+loudnorm, TP=-6 ship gate).
NO captions / reframe / music -- those are downstream skills. Endings: hard cut on the last word.

Usage:
  precision_cut.py --src RAW.mov --transcript WORDS.json --keep '[[a,b],[c,d,0.06],...]' \
                   --floor -52dB --out OUT.mp4
keep entries: [start_word_idx, end_word_idx] or [..., ..., tail] (small tail e.g. 0.06 when the END word
is FUSED to a loud next/cut word, to stop a leftover bleed). --floor: set ~7-10 dB below the speaker's
mean_volume (run `ffmpeg -i RAW -af volumedetect -f null -` first). Quiet talker ~-33dB -> -52dB.
"""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
import sys
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
import argparse, json, os, re, subprocess

JUMPCUT = _acq("caption-clips/scripts/jumpcut.py")

try:
    from fast_encode import encoder_args  # Brand render standard: VideoToolbox HW encode (~4x, off-CPU)
except Exception:
    encoder_args = None

def _venc(src, crf):
    """VideoToolbox encoder args sized to the source (libx264 fallback)."""
    if encoder_args:
        try:
            wh = subprocess.check_output(["ffprobe","-v","error","-select_streams","v",
                "-show_entries","stream=width,height","-of","csv=p=0",src]).decode().strip().split(",")
            return list(encoder_args(int(wh[0]), int(wh[1]), "ffmpeg", tier="intermediate", crf=crf))
        except Exception:
            pass
    return ["-c:v","libx264","-crf",str(crf),"-preset","medium","-pix_fmt","yuv420p"]

def dur(p):
    return float(subprocess.check_output(
        ["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",p]).decode().strip())

def run(cmd, label):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        print(f"FAIL {label}:", p.stderr[-400:]); raise SystemExit(1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True); ap.add_argument("--transcript", required=True)
    ap.add_argument("--keep", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--floor", default="-40dB", help="jumpcut silence floor; ~7-10dB below mean_volume")
    ap.add_argument("--max-pause", default="0.15", help="jumpcut max pause (0.15=tight; raise ~0.30 for a more natural/relaxed feel)")
    a = ap.parse_args()
    W = json.load(open(a.transcript)); W = W["words"] if isinstance(W, dict) else W
    keep = json.loads(a.keep); raw = a.src; rd = dur(raw); base = a.out.rsplit(".", 1)[0]

    # raw silences (>=0.05s below -50dB) for TRUE-acoustic-end detection
    slog = subprocess.run(['ffmpeg','-hide_banner','-i',raw,'-af','silencedetect=noise=-50dB:d=0.05','-f','null','-'],
                          capture_output=True, text=True).stderr
    SIL = []; _c = None
    for m in re.finditer(r'silence_(start|end):\s*([\d.]+)', slog):
        if m.group(1) == 'start': _c = float(m.group(2))
        elif _c is not None: SIL.append((_c, float(m.group(2)))); _c = None

    def true_end(bb):
        ws, we = W[bb]['start'], W[bb]['end']
        for ss, se in SIL:                       # INFLATED label: a long (>0.4s) pause inside the label
            if ws <= ss < we and (se - ss) > 0.4: return ss
        for ss, se in SIL:                       # normal: first real pause (>=0.12s) at/after the label END
            if ss >= we - 0.05 and (se - ss) >= 0.12: return ss   # search from END skips mid-word stop-closures
        return None

    spans = []; fades = []
    for x in keep:
        aa, bb = x[0], x[1]; ovr = x[2] if len(x) > 2 else None
        s = max(W[aa]['start'] - 0.04, W[aa - 1]['end'] if aa > 0 else 0.0)
        ts = true_end(bb)
        if ts is not None and ts <= W[bb]['end'] + 0.6:          # real pause follows -> keep the whole word + tail
            e = min(ts + (0.20 if x is keep[-1] else 0.05), rd); fd = 0.04
        else:                                                    # FUSED next word -> branch on the ending phonetics
            lw = re.sub(r'[^a-z]', '', W[bb]['word'].lower())
            if lw.endswith(('s','t','k','p','f','x','z','ch','sh','th','ce','se','ke','st','ct')):
                tl = ovr if ovr is not None else 0.22; fd = 0.04  # long unvoiced release: extend, de-click only
            else:
                tl = ovr if ovr is not None else 0.06; fd = tl    # voiced/nasal: small tail + full fade masks bleed
            e = min(W[bb]['end'] + tl, rd)
        spans.append((round(s, 3), round(e, 3))); fades.append(fd)
    print(a.out, "spans:", spans)

    parts = []; cc = ""
    for i, (s, e) in enumerate(spans):
        d = e - s
        parts.append(f"[0:v]trim={s}:{e},setpts=PTS-STARTPTS[v{i}]")
        fo = 0.01 if i == len(spans) - 1 else fades[i]            # last word rings out; interior seams faded
        parts.append(f"[0:a]atrim={s}:{e},asetpts=PTS-STARTPTS,afade=t=in:d=0.02,"
                     f"afade=t=out:st={max(0, d - fo):.3f}:d={fo}[a{i}]")
        cc += f"[v{i}][a{i}]"
    filt = ";".join(parts) + f";{cc}concat=n={len(spans)}:v=1:a=1[outv][outa]"
    cut, tight = base + "_cut.mp4", base + "_tight.mp4"
    run(["ffmpeg","-y","-loglevel","error","-i",raw,"-filter_complex",filt,"-map","[outv]","-map","[outa]",
         "-r","30",*_venc(raw,18),"-c:a","aac","-b:a","160k",cut],"cut")
    run([sys.executable,JUMPCUT,cut,tight,"--noise",a.floor,"--max-pause",a.max_pause,"--min-detect","0.18","--crf","18"],"jumpcut")
    run(["ffmpeg","-y","-loglevel","error","-i",tight,"-af","highpass=f=80,volume=1.2,loudnorm=I=-16:LRA=11:TP=-6",
         "-c:v","copy","-c:a","aac","-b:a","160k",a.out],"audio")
    for f in (cut, tight):
        if os.path.exists(f): os.remove(f)
    print(f"OK {a.out}  ({dur(a.out):.1f}s)")

if __name__ == "__main__":
    main()
