#!/usr/bin/env python3
"""ONE-SHOT face-tracked split-screen (LOCKED 2026-06-08).

Takes two horizontal videos (each typically a single subject — host cam + guest cam, or any
two-person setup), face-tracks each one to 1080x1920 with the locked house style (Y-LOCK +
xcenter box), then stacks them into a 50/50 vertical split with the gaussian seam shadow.

Symmetric upper-third face placement in each tile is locked in by per-side eye_y values:
the TOP cam puts the face HIGH in its 1920 frame (eye_y 0.15) and gets cropped y=0..960;
the BOTTOM cam puts the face LOW in its 1920 frame (eye_y 0.65) and gets cropped y=960..1920.
Math: face lands at 0.30 of its 960-tall tile on BOTH sides — upper third, head + chest visible.

Use for ANY 2-person split-screen — podcast, interview, debate, reaction — NOT just Q&A.
For Q&A workshop multicam (where Speaker is on a wide stage with audience), use edit's
qa_assembly.py — its asymmetric ROI presets (stage + guest) handle the stage geometry.

Usage:
    split_facetracked.py --top host.mp4 --bottom guest.mp4 --out split.mp4
                         [--top-preset talking-head] [--bottom-preset talking-head]
                         [--top-eye-y 0.15] [--bottom-eye-y 0.65]
                         [--start 0] [--end <input duration>]

Overrides: pass --top-preset / --bottom-preset to use a different preset per side
(e.g. --top-preset stage when the top cam is the stage cam in a hybrid setup).
"""
import argparse, os, subprocess, sys, tempfile, shutil, json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFRAME = HERE / "qa_reframe_v2.py"
STACK   = HERE / "make_splitscreen.py"

def probe_duration(path):
    out = subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration",
                                   "-of","csv=p=0", str(path)]).decode().strip()
    return float(out)

def run(cmd):
    print(f"$ {' '.join(str(x) for x in cmd[:8])}{'...' if len(cmd)>8 else ''}", flush=True)
    subprocess.run([str(x) for x in cmd], check=True)

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--top",    type=Path, required=True, help="Horizontal video for the TOP tile")
    ap.add_argument("--bottom", type=Path, required=True, help="Horizontal video for the BOTTOM tile")
    ap.add_argument("--out",    type=Path, required=True, help="Output 1080x1920 split-screen MP4")
    ap.add_argument("--top-preset",    default="talking-head", dest="top_preset",
                    help="qa_reframe_v2 --preset for the TOP cam (default talking-head)")
    ap.add_argument("--bottom-preset", default="talking-head", dest="bot_preset",
                    help="qa_reframe_v2 --preset for the BOTTOM cam (default talking-head)")
    ap.add_argument("--top-eye-y",    type=float, default=0.15, dest="top_eye_y",
                    help="Eye-y in TOP-cam reframe output (default 0.15 = face HIGH so it lands in upper-third of TOP tile)")
    ap.add_argument("--bottom-eye-y", type=float, default=0.65, dest="bot_eye_y",
                    help="Eye-y in BOTTOM-cam reframe output (default 0.65 = face LOW so it lands in upper-third of BOTTOM tile)")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end",   type=float, default=None,
                    help="Clip end (default = duration of the SHORTER input)")
    ap.add_argument("--keep-temp", action="store_true", help="Keep the per-side reframed verticals")
    args = ap.parse_args()

    # Default --end = shorter of the two inputs (so both tiles have video for the whole stack)
    if args.end is None:
        args.end = min(probe_duration(args.top), probe_duration(args.bottom))

    work = Path(tempfile.mkdtemp(prefix="_split_ft_"))
    print(f"[split_facetracked] tmp: {work}", flush=True)
    try:
        top_9x16 = work / "top_9x16.mp4"
        bot_9x16 = work / "bottom_9x16.mp4"

        # Reframe each angle. Override eye_y on the preset to bake the symmetric upper-third placement.
        run([sys.executable, REFRAME, args.top,    top_9x16,
             "--preset", args.top_preset, "--eye-y", f"{args.top_eye_y}", "--res", "1080"])
        run([sys.executable, REFRAME, args.bottom, bot_9x16,
             "--preset", args.bot_preset, "--eye-y", f"{args.bot_eye_y}", "--res", "1080"])

        # Stack. TOP gets cropped y=0..960 (its high face lands in upper-third of tile),
        # BOTTOM gets cropped y=960..1920 (its low face lands in upper-third of tile).
        run([sys.executable, STACK,
             "--speaker",  top_9x16, "--guest", bot_9x16,
             "--out",   args.out,
             "--start", f"{args.start}", "--end", f"{args.end}",
             "--crop-y", "0", "--guest-crop-y", "960",
             "--audio", "mix"])
        print(f"[split_facetracked] -> {args.out}", flush=True)
    finally:
        if not args.keep_temp:
            shutil.rmtree(work, ignore_errors=True)

if __name__ == "__main__":
    main()
