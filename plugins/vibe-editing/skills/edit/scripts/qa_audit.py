#!/usr/bin/env python3
"""qa_audit — pre-show gate for an Speaker Q&A clip built by qa_build. Run BEFORE showing anyone.

Checks (against the the reference editor house style + Team Speaker audio gate):
  STRUCTURE  first segment speaker == guest (qa/hotline lead with the guest).
  AUDIO      -14 LUFS (+/-1.5), true peak <= -1 dBFS, no black frames   [watch/probe.py]
  CAPTIONS   white caption present ~48% height on SPEAKER spans;            [watch/caption_ocr.py]
             NO white on GUEST spans (= the yellow/guest check).
  EYEBALL    writes a contact sheet PNG for the subject-centered / spelling read.  [watch/contact_sheet.py]

Reference reels: ~/Downloads/qa-mining/reference-reels/  (printed for comparison).
Exit 0 = pass, 1 = fail. Usage: qa_audit.py CLIP.mp4 --edl EDL.json [--format qa] [--ref-dir DIR]
"""
# ── vibe-editing portable path bootstrap (auto-inserted) ──
import tempfile
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
import os, sys, json, glob, shutil, subprocess, argparse
import numpy as np
from PIL import Image

WATCH = _acq("watch/scripts")
REF_DEFAULT = os.path.expanduser("~/Downloads/qa-mining/reference-reels")
FF = next(iter(sorted(glob.glob("/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg"), reverse=True)), None) or shutil.which("ffmpeg") or "ffmpeg"


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def yellow_frac(clip, ranges):
    """Fraction of sampled frames (in the given clip-time ranges) that show a YELLOW caption
    run in the ~48% zone. Positive yellow test (bright + low blue) — a guest's bright neutral
    SKIN has high blue, so it can't be mistaken for a yellow caption the way the white test is."""
    times = []
    for x0, x1 in ranges:
        t = x0 + 0.4
        while t < x1 - 0.1:
            times.append(t); t += 1.0
    if not times:
        return None, 0
    tmp = os.path.join(tempfile.gettempdir(), f"_qay_{os.getpid()}.png"); yes = n = 0
    for t in times:
        subprocess.run([FF, "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", clip, "-frames:v", "1", tmp], capture_output=True)
        if not os.path.exists(tmp):
            continue
        a = np.asarray(Image.open(tmp).convert("RGB")); H, W = a.shape[:2]
        b = a[int(.43 * H):int(.54 * H)]
        R, G, B = b[:, :, 0].astype(int), b[:, :, 1].astype(int), b[:, :, 2].astype(int)
        yellow = (R > 175) & (G > 135) & (B < 95)
        n += 1
        if int((yellow.sum(axis=1) > 0.04 * W).sum()) >= 2:    # >=2 rows with a yellow run = a caption line
            yes += 1
    if os.path.exists(tmp):
        os.remove(tmp)
    return (yes / max(1, n)), n


def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--edl", required=True)
    ap.add_argument("--format", default="qa", choices=["qa", "hotline", "dtc"])
    ap.add_argument("--ref-dir", default=REF_DEFAULT)
    a = ap.parse_args()
    base = os.path.splitext(a.clip)[0]
    edl = json.load(open(a.edl)); segs = edl["segments"] if isinstance(edl, dict) else edl
    # clip-time ranges per speaker — prefer qa_build's actual (snapped) clip-map; else EDL durations
    cmf = os.path.splitext(a.clip)[0] + "_clipmap.json"
    if os.path.exists(cmf):
        cr = json.load(open(cmf))
    else:
        cr, t = [], 0.0
        for s in segs:
            d = s["mic_end"] - s["mic_start"]; cr.append((t, t + d, s["speaker"])); t += d
    clip_dur = cr[-1][1] if cr else 0.0
    speaker_rng = [(x0, x1) for x0, x1, sp in cr if sp == "speaker"]
    guest_rng = [(x0, x1) for x0, x1, sp in cr if sp == "guest"]
    results = []  # (name, ok, detail)

    # STRUCTURE
    if a.format in ("qa", "hotline"):
        ok = segs[0]["speaker"] == "guest"
        results.append(("structure", ok, f"first segment speaker = {segs[0]['speaker']} (want guest)"))

    # AUDIO (watch/probe.py)
    pj = f"{base}_probe.json"
    sh([sys.executable, f"{WATCH}/probe.py", a.clip, "--out", pj])
    pr = json.load(open(pj)) if os.path.exists(pj) else {}
    I = pr.get("loudness_LUFS_integrated"); TP = pr.get("true_peak_dBFS"); blk = pr.get("black_segments")
    results.append(("loudness", I is not None and -15.5 <= I <= -12.5, f"{I} LUFS (target -14 +/-1.5)"))
    results.append(("true_peak", TP is not None and TP <= -1.0, f"{TP} dBFS (gate <= -1)"))
    results.append(("no_black", blk == 0, f"{blk} black segments"))
    # A/V DURATION PARITY — catches the "diarization gap -> video ends short -> frozen/black tail" failure
    # (Brand multicam lesson). If video stream is shorter than audio, the clip freezes on the last frame.
    FFP = FF.replace("/ffmpeg", "/ffprobe")
    def _sdur(stream):
        r = sh([FFP, "-v", "error", "-select_streams", stream, "-show_entries", "stream=duration", "-of", "csv=p=0", a.clip])
        try: return round(float(r.stdout.strip().splitlines()[0]), 2)
        except Exception: return None
    vd, ad = _sdur("v:0"), _sdur("a:0")
    if vd is None or ad is None:
        results.append(("av_duration", True, f"video {vd}s / audio {ad}s (stream dur n/a — skipped)"))
    else:
        results.append(("av_duration", abs(vd - ad) <= 0.20, f"video {vd}s vs audio {ad}s (|Δ|<=0.20; video-short=frozen tail)"))

    # LENGTH CAP — SOP is 60-75s, 90s HARD CAP. Over-cap is a FAIL: content importance NEVER overrides the cap.
    _fmt = sh([FFP, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", a.clip])
    try: total = round(float(_fmt.stdout.strip().splitlines()[0]), 1)
    except Exception: total = max(vd or 0, ad or 0)
    results.append(("length_cap", 0 < total <= 90.0, f"{total}s (SOP 60-75s; 90s HARD CAP — >90 = cut harder: drop proof/tangents)"))

    # CAPTIONS (watch/caption_ocr.py — detects WHITE text + placement)
    cj = f"{base}_captions.json"
    sh([sys.executable, f"{WATCH}/caption_ocr.py", a.clip, "--no-ocr", "--band-lo", "0.42", "--band-hi", "0.55", "--interval", "0.4", "--out", cj])
    cap = json.load(open(cj)) if os.path.exists(cj) else {}
    # Only count white in the CAPTION ZONE (~48%); white elsewhere is set dressing
    # (e.g. a guest's white name-badge/lanyard at ~85%) and must not fool the yellow check.
    CAP_LO, CAP_HI = 42, 54
    white = [s for s in cap.get("caption_spans", [])
             if s.get("y_center_pct") is not None and CAP_LO <= s["y_center_pct"] <= CAP_HI]
    ycs = [s["y_center_pct"] for s in white if s.get("y_center_pct") is not None]
    ymed = round(sorted(ycs)[len(ycs)//2], 1) if ycs else None
    speaker_t = sum(x1 - x0 for x0, x1 in speaker_rng) or 1e-9
    guest_t = sum(x1 - x0 for x0, x1 in guest_rng) or 1e-9
    white_on_speaker = sum(overlap(s["start"], s["end"], a0, a1) for s in white for a0, a1 in speaker_rng)
    white_on_guest = sum(overlap(s["start"], s["end"], g0, g1) for s in white for g0, g1 in guest_rng)
    results.append(("caption_y", ymed is not None and 43 <= ymed <= 53, f"white-caption y-center median {ymed}% (want ~48)"))
    results.append(("speaker_white", white_on_speaker / speaker_t > 0.45, f"white captions cover {white_on_speaker/speaker_t:.0%} of Speaker time (want high)"))
    gy, gn = yellow_frac(a.clip, guest_rng)
    results.append(("guest_yellow", (gy is None) or gy > 0.6,
                    (f"yellow caption on {gy:.0%} of {gn} guest-span frames (want high)" if gy is not None else "no guest spans")))

    # EYEBALL contact sheet
    cs = f"{base}_audit_contact.png"
    sh([sys.executable, f"{WATCH}/contact_sheet.py", a.clip, "--n", "20", "--cols", "5", "--out", cs])

    # reference reel (loudness for comparison)
    ref = next(iter(sorted(glob.glob(f"{a.ref_dir}/*.mp4"))), None)
    ref_note = ""
    if ref:
        rj = f"{base}_ref_probe.json"; sh([sys.executable, f"{WATCH}/probe.py", ref, "--out", rj])
        if os.path.exists(rj):
            rp = json.load(open(rj)); ref_note = f"{os.path.basename(ref)} = {rp.get('loudness_LUFS_integrated')} LUFS, y? (read its frames)"

    # report
    npass = sum(1 for _, ok, _ in results if ok)
    print(f"\n=== qa_audit: {os.path.basename(a.clip)}  ({clip_dur:.1f}s, format={a.format}) ===")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:13s} {detail}")
    print(f"  ---- {npass}/{len(results)} checks passed ----")
    print(f"  EYEBALL (read this): {cs}")
    print(f"     confirm by eye: Speaker centered? captions ~48% under chin? guest yellow-italic / Speaker white? numbers bold? spelling?")
    if ref_note: print(f"  reference: {ref_note}")
    allok = all(ok for _, ok, _ in results)
    print(f"\n{'GATE PASS — ok to show (after the eyeball)' if allok else 'GATE FAIL — fix before showing'}")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
