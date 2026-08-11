#!/usr/bin/env python3
"""faded_trim_cut.py — transcript-driven faded-trim-concat cutter that SNAPS every splice to the
inter-word silence (no mid-word cuts, no pops) and PRESERVES the source fps (works on 23.976 etc.,
where _shared/precision_cut.py is unusable — it forces 30fps).

WHY THIS EXISTS (lessons paid for, Speaker PeaceOrPower 2026-06-12):
  - ASR word-label timing is NOT a safe cut point. Depending on the model the label can sit a hair
    EARLY (shaves soft consonants) OR exactly at the NEXT word's start (so a span end on the label
    lands mid-speech). Cutting on labels → chopped words + audible clicks/pops at the joins. The
    user's #1 audio complaint.
  - FIX: end each span at the word's TRUE ACOUSTIC end (energy scan, skipping internal phoneme stops),
    then SNAP the actual cut to the quietest ~10ms window in the trailing/leading silence, and use a
    ≥12ms afade at every splice. Then every join is silence→silence = clean.
  - precision_cut.py forces 30fps (drifts/borks 23.976 sources). This cutter probes & preserves fps.
  - ⚠️ KNOWN LIMIT on NOISY/multi-person footage (events, handheld): `acoustic_word_end` uses a FIXED
    −33 dB floor. When room tone is ~−28 dB (louder than −33) it never sees the inter-turn pause, so a
    bogus-long Whisper final-word label makes the span end OVERSHOOT into the next word / an off-camera
    "Awesome/Thanks" tag. For that footage the fix is an ADAPTIVE floor (≈ tail mean_volume +1 dB) — see
    `build_ad.py tail_clean` (verified on the 2026-06-16 testimonial batch) and the CLIP_CUTTING_PLAYBOOK
    "true-end traps" note. Not yet folded into the default path here (needs re-verify on a clean Q&A source).

Spans = word-index ranges into a word-level transcript JSON (source-time start/end per word).
Usage:
  faded_trim_cut.py <spans.json> <out.mp4> --source <src.mp4> --words <transcript.words.json> [--fade 0.012]
  spans.json = {"spans":[{"a":<start_word_idx>,"b":<end_word_idx>}, ...]}   # plays in listed order
Each span: start snaps to the silence just before word a; end snaps to the silence just after word b.
"""
import argparse, json, os, subprocess, tempfile, shutil
import numpy as np, librosa

try:  # hardware H.264 args (h264_amf on this machine); libx264 fallback inside the helper
    from fast_encode import hw_h264_args
except Exception:
    def hw_h264_args(bitrate="16M", ffmpeg="ffmpeg"):
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]

SRC = None; words = None; FADE = 0.012
def t0(i): x = words[i]; return x.get("start", x.get("s", 0.0))
def t1(i): x = words[i]; return x.get("end",   x.get("e", 0.0))

def probe_fps(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", src],
                       capture_output=True, text=True).stdout.strip() or "30/1"
    n, _, d = r.partition("/"); n = int(n); d = int(d) if d else 1
    ts = n if d > 1 else n * 1000          # fractional fps (24000/1001) -> 24000; integer (30/1) -> 30000
    return r, str(ts)

def quietest(t_lo, t_hi):
    """Time of the quietest ~10ms window in [t_lo,t_hi] — splices land IN inter-word silence."""
    t_lo = max(0.0, t_lo)
    if t_hi - t_lo < 0.02: return t_lo
    y, sr = librosa.load(SRC, sr=16000, offset=t_lo, duration=t_hi - t_lo, mono=True)
    if len(y) == 0: return (t_lo + t_hi) / 2
    fl = int(0.01 * sr); best_db = 1e9; best_t = (t_lo + t_hi) / 2
    for k in range(0, max(1, len(y) - fl), max(1, fl // 2)):
        db = 20 * np.log10(float(np.sqrt(np.mean(y[k:k + fl] ** 2)) + 1e-9))
        if db < best_db: best_db = db; best_t = t_lo + (k + fl / 2) / sr
    return best_t

def acoustic_word_end(b):
    """Where word b's SPEECH actually stops (onset of trailing silence), by ear — not the label."""
    start = t0(b)
    y, sr = librosa.load(SRC, sr=16000, offset=max(0, start - 0.03), duration=1.1, mono=True)
    fl = int(0.02 * sr); need = int(0.14 / 0.02); seen = False; cnt = 0   # 0.14s: skip internal stops (re-PEAT-able)
    for k in range(0, max(0, len(y) - fl), fl):
        db = 20 * np.log10(float(np.sqrt(np.mean(y[k:k + fl] ** 2)) + 1e-9))
        if db > -33: seen = True; cnt = 0
        elif seen:
            cnt += 1
            if cnt >= need: return (start - 0.03) + (k - (need - 1) * fl) / sr
    return t1(b)

def acoustic_word_start(a):
    """Onset of word a's speech near its label start — so a fused preceding word ('So when…') isn't dragged in."""
    base = t0(a); win = max(0, base - 0.14)
    y, sr = librosa.load(SRC, sr=16000, offset=win, duration=0.36, mono=True)
    fl = int(0.01 * sr); prev_silent = True; best = None
    for k in range(0, max(0, len(y) - fl), fl):
        db = 20 * np.log10(float(np.sqrt(np.mean(y[k:k + fl] ** 2)) + 1e-9)); t = win + k / sr
        if db > -33 and prev_silent:
            if best is None or abs(t - base) < abs(best - base): best = t
            prev_silent = False
        if db < -38: prev_silent = True
    return (best if best is not None else base) - 0.03

def snap_end(b):
    ae = acoustic_word_end(b)
    hi = min(ae + 0.20, t0(b + 1) - 0.01) if b + 1 < len(words) else ae + 0.30
    return max(ae + 0.03, quietest(ae + 0.02, max(ae + 0.05, hi)))

def snap_start(a):
    return quietest(t0(a) - 0.10, t0(a) + 0.02)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spans"); ap.add_argument("out")
    ap.add_argument("--source", required=True); ap.add_argument("--words", required=True)
    ap.add_argument("--fade", type=float, default=0.012)
    a = ap.parse_args()
    global SRC, words, FADE
    SRC = a.source; FADE = a.fade
    W = json.load(open(a.words)); words = W if isinstance(W, list) else W["words"]
    fps, ts = probe_fps(SRC)
    spans = json.load(open(a.spans))["spans"]
    tmp = tempfile.mkdtemp(prefix="ftc_"); parts = []
    for i, sp in enumerate(spans):
        ss = acoustic_word_start(sp["a"]) if i == 0 else snap_start(sp["a"])
        to = snap_end(sp["b"]); dur = round(to - ss, 4)
        seg = os.path.join(tmp, f"p{i:02d}.mp4")
        print(f"  span {i}: [{sp['a']}->{sp['b']}] {ss:.3f}->{to:.3f} ({dur:.2f}s)")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.4f}", "-i", SRC, "-t", f"{dur:.4f}",
            "-af", f"afade=t=in:st=0:d={FADE},afade=t=out:st={max(0,dur-FADE):.4f}:d={FADE}",
            "-r", fps, "-vsync", "cfr", *hw_h264_args("16M"),
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-video_track_timescale", ts, seg], check=True)
        parts.append(seg)
    lst = os.path.join(tmp, "list.txt"); open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
        "-r", fps, "-vsync", "cfr", *hw_h264_args("16M"),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-video_track_timescale", ts, a.out], check=True)
    print(f"  -> {a.out}")
    shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
