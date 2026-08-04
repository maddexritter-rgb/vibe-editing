#!/usr/bin/env python3
"""Burn a simple caption-style "Comment 'KEYWORD'" CTA over a graded clip.

The CTA is two centered lines above the face that match the clip's existing caption style
(white Montserrat + soft shadow), NOT a sticker/badge/gold-chip/icon. Built 2026-06-06 for the
Money Rules ManyChat comment-to-DM flow — keep simple, not "highlight-y".

  line 1 (Medium):                  Want to watch the full video?
  line 2 (Medium + Bold quoted):    Comment 'KEYWORD'

The keyword is per-video (rules / playbook / secrets / scale / closers / …). Both lines are
the same font size and ride together as one tight block. Sits in the dead space above the
face (centered horizontally, y≈560 at 4K); fades in at --start (default 25s, so the hook
plays naked) and rides to the clip end.

The CTA cues are APPENDED to your existing tabs/captions .ass (written as <name>_cta.ass),
then the result is burned over the graded master. Audio is COPIED from --audio-from (your
approved V1) byte-for-byte — never re-leveled, so the V2 audio == the V1 you signed off.

Usage:
  cta_overlay.py --graded <graded_4K.mov> --ass <tabs+captions.ass> \\
                 --keyword rules \\
                 --audio-from <V1.mp4> \\
                 --out <V2.mp4> \\
                 [--start 25] [--prompt "Want to watch the full video?"]

🛑 LOCAL ONLY. This script writes to disk and stops. Do NOT chain a Frame upload — that's
   a separate, explicitly-approved step (delivery-workflow/deliver.py --approved).
"""
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
import argparse, os, subprocess, sys
from pathlib import Path

# Brand FAST-RENDER STANDARD — VideoToolbox HW encode (~4x), resolution-aware single source of truth.
sys.path.insert(0, VIBE_SHARED)
try:
    from fast_encode import encoder_args_for
except Exception:
    encoder_args_for = None

FD = _acq("caption-clips/fonts/free_font")
SHADOW = r"\shad16\blur18\4c&H00000000&\4a&H00&\3c&H00000000&"
# 4K geometry (auto-scaled below if PlayResY differs)
FS_4K = 130
Y1_4K, Y2_4K = 475, 645   # gap 170, vertical center ≈ 560 / 3840


def ass_time(secs):
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def probe(path, args):
    return subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", args, "-of", "csv=p=0", str(path)]
    ).decode().strip()


def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("--graded", required=True, help="graded master (4K .mov)")
    ap.add_argument("--ass", required=True, help="existing .ass (tabs+captions). CTA cues are appended.")
    ap.add_argument("--keyword", required=True, help="the ManyChat keyword, e.g. rules / playbook / secrets")
    ap.add_argument("--audio-from", required=True, help="approved V1 to copy audio from (byte-identical)")
    ap.add_argument("--out", required=True, help="V2 output path (writes locally, NEVER uploads)")
    ap.add_argument("--start", type=float, default=25.0, help="seconds — CTA fades in here (default 25)")
    ap.add_argument("--prompt", default="Want to watch the full video?",
                    help="line 1 (default 'Want to watch the full video?')")
    a = ap.parse_args()

    # CTA end time = full clip duration
    dur = float(probe(a.graded, "format=duration"))
    t0, t1 = ass_time(a.start), ass_time(dur)

    # auto-scale to the .ass PlayResY (4K geometry by default; will scale down for 1080p)
    play_y = 3840
    for ln in Path(a.ass).read_text().split("\n"):
        if ln.lower().startswith("playresy:"):
            try: play_y = int(ln.split(":", 1)[1].strip())
            except ValueError: pass
            break
    sc = play_y / 3840.0
    fs = int(round(FS_4K * sc))
    y1, y2 = int(round(Y1_4K * sc)), int(round(Y2_4K * sc))
    cx = int(round(1080 * sc))

    kw = a.keyword
    cues = [
        f"Dialogue: 4,{t0},{t1},the reference editor,,0,0,0,,{{\\an5\\pos({cx},{y1})\\fad(350,0){SHADOW}"
        f"\\fs{fs}\\fnMontserrat Medium\\1c&H00FFFFFF&}}{a.prompt}",
        f"Dialogue: 4,{t0},{t1},the reference editor,,0,0,0,,{{\\an5\\pos({cx},{y2})\\fad(350,0){SHADOW}"
        f"\\1c&H00FFFFFF&}}{{\\fnMontserrat Medium\\fs{fs}}}Comment "
        f"{{\\fnMontserrat Bold\\fs{fs}}}'{kw}'",
    ]

    src = Path(a.ass).read_text().splitlines()
    out_ass = Path(a.ass).with_name(Path(a.ass).stem + "_cta.ass")
    merged = []
    for ln in src:
        merged.append(ln)
        if ln.startswith("Format: Layer, Start"):
            merged.extend(cues)
    out_ass.write_text("\n".join(merged) + "\n")
    print(f"appended CTA cues -> {out_ass}")

    # burn over the graded video, copy audio from --audio-from byte-for-byte
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", a.graded, "-i", a.audio_from,
        "-filter_complex", f"[0:v]subtitles=filename='{out_ass}':fontsdir='{FD}'[v]",
        "-map", "[v]", "-map", "1:a",
        # Brand fast-render standard (VideoToolbox HW, res-aware ~50M @ 4K); libx264 fallback if _shared import fails.
        *(list(encoder_args_for(str(a.graded), "ffmpeg", tier="delivery")) if encoder_args_for
          else ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]),
        "-c:a", "copy", "-movflags", "+faststart",
        a.out,
    ]
    subprocess.check_call(cmd)
    print(f"OK  keyword='{kw}'  fadein@{a.start}s  ->  {a.out}")
    print("    (local only — NEVER auto-upload to Frame)")


if __name__ == "__main__":
    main()
