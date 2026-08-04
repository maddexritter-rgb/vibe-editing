#!/usr/bin/env python3
"""Brand BENCH — measure real encoder + decoder performance on THIS machine (locked 2026-06-06).

Built because "VideoToolbox is ~4× faster than libx264" is a useful projection but the
real number depends on your bitrate, source, filters, and contention. This bench replaces
guessing with measuring. Run it once after every infra change to lock the floor.

What it does:
  1. Generate a synthetic 30s 4K test source (testsrc2, real motion + chroma).
  2. Encode it through encoder_args(width, height, tier=...) at every tier.
  3. Same encode WITH `-hwaccel videotoolbox` on the decode side, to check if HW
     decode gives a measurable speedup (it should for 4K source).
  4. Same encode at 1080p (mimicking VIBE_RES=1080 proxy mode).
  5. Compare against an explicit libx264 -slow run (the SW baseline) to verify the
     "~4× faster" claim is still true on the current ffmpeg / OS.

Usage:
    python3 $VIBE_PIPELINE_ROOT/lib/_shared/bench.py                    # full bench
    python3 $VIBE_PIPELINE_ROOT/lib/_shared/bench.py --duration 10      # quicker (10s source)
    python3 $VIBE_PIPELINE_ROOT/lib/_shared/bench.py --skip-sw          # skip the slow libx264 baseline
    python3 $VIBE_PIPELINE_ROOT/lib/_shared/bench.py --json             # machine-readable

Writes intermediate files to /tmp/_acq_bench/ (cleaned up at end).
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
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, VIBE_SHARED)
from fast_encode import encoder_args

WORK = Path(tempfile.gettempdir()) / "_acq_bench"


def _ffmpeg():
    # Prefer ffmpeg-full (has all filters / libass), fall back to system ffmpeg.
    import glob as _g
    for pat in ("/opt/homebrew/Cellar/ffmpeg-full/*/bin/ffmpeg",
                "/usr/local/Cellar/ffmpeg-full/*/bin/ffmpeg"):
        hits = sorted(_g.glob(pat))
        if hits:
            return hits[-1]
    return shutil.which("ffmpeg") or "ffmpeg"


def _run(cmd, label):
    """Run cmd, return (wall_seconds, returncode, stderr_tail)."""
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.monotonic() - t0
    return dt, p.returncode, (p.stderr or "")[-400:]


def make_source(ffmpeg, duration, w, h, out):
    """testsrc2 has real-looking motion + colors so the encoder has to actually work."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"testsrc2=duration={duration}:size={w}x{h}:rate=30",
           "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
           str(out)]
    dt, rc, err = _run(cmd, "make_source")
    if rc != 0:
        sys.exit(f"failed to generate source ({w}x{h}, {duration}s): {err}")
    return dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=30)
    ap.add_argument("--skip-sw", action="store_true",
                    help="Skip the libx264 -slow baseline (it's slow; useful when you just want VT data)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    ffmpeg = _ffmpeg()

    if not args.json:
        print(f"\n📊 Brand encoder/decoder bench  ({args.duration}s sources, {ffmpeg})\n")

    # Two sources: 4K and 1080p. Most pipelines run on 4K. Proxy iteration runs on 1080p.
    sources = {
        "4K (3840x2160)":   (WORK / "src_4k.mp4",   3840, 2160),
        "1080p (1080x1920)": (WORK / "src_1080.mp4", 1080, 1920),
    }
    for label, (path, w, h) in sources.items():
        if not args.json:
            print(f"▶ generating {label} source...")
        make_source(ffmpeg, args.duration, w, h, path)

    results = []

    # Encoder side: every tier at both resolutions.
    # tier name -> args spec
    cases = [
        ("encode", "delivery (VT)",     "vt",   "delivery"),
        ("encode", "intermediate (VT)", "vt",   "intermediate"),
        ("encode", "proxy (VT)",        "vt",   "proxy"),
    ]
    if not args.skip_sw:
        cases.append(("encode", "master (libx264 slow)", "x264", "master"))

    for label, (src, w, h) in sources.items():
        for kind, case_name, encoder, tier in cases:
            env = dict(os.environ)
            if encoder == "x264":
                env["VIBE_ENCODER"] = "x264"
            else:
                env.pop("VIBE_ENCODER", None)
            enc = encoder_args(w, h, ffmpeg, tier=tier)
            out = WORK / f"out_{label.split()[0]}_{case_name.split()[0]}.mp4"
            cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(src), *enc, "-an", str(out)]
            dt, rc, err = _run(cmd, case_name)
            sz = out.stat().st_size if rc == 0 and out.exists() else 0
            results.append({
                "source": label, "stage": "encode-only", "variant": case_name,
                "seconds": round(dt, 3), "ok": rc == 0,
                "out_mb": round(sz / 1024 / 1024, 1),
                "note": err.strip().split("\n")[-1] if rc != 0 else "",
            })

        # HW decode test (most interesting at 4K — decode is heavier there)
        for tier in ("delivery",):
            enc = encoder_args(w, h, ffmpeg, tier=tier)
            for use_hwaccel in (False, True):
                hw_prefix = ["-hwaccel", "auto"] if use_hwaccel else []
                tag = "encode+HW-decode" if use_hwaccel else "encode+SW-decode (control)"
                out = WORK / f"out_{label.split()[0]}_hw{int(use_hwaccel)}.mp4"
                cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                       *hw_prefix, "-i", str(src), *enc, "-an", str(out)]
                dt, rc, err = _run(cmd, tag)
                results.append({
                    "source": label, "stage": "encode+decode", "variant": tag,
                    "seconds": round(dt, 3), "ok": rc == 0,
                    "out_mb": round(out.stat().st_size / 1024 / 1024, 1) if rc == 0 and out.exists() else 0,
                    "note": err.strip().split("\n")[-1] if rc != 0 else "",
                })

    # Final encode + scale (mimics a real reframe step: decode + scale + re-encode)
    src4k = sources["4K (3840x2160)"][0]
    enc_1080 = encoder_args(1080, 1920, ffmpeg, tier="intermediate")
    for use_hwaccel in (False, True):
        hw_prefix = ["-hwaccel", "auto"] if use_hwaccel else []
        tag = "4K→1080p+HW-decode" if use_hwaccel else "4K→1080p+SW-decode"
        out = WORK / f"out_scale_hw{int(use_hwaccel)}.mp4"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
               *hw_prefix, "-i", str(src4k),
               "-vf", "scale=1080:1920",
               *enc_1080, "-an", str(out)]
        dt, rc, err = _run(cmd, tag)
        results.append({
            "source": "4K (3840x2160)", "stage": "decode+scale+encode", "variant": tag,
            "seconds": round(dt, 3), "ok": rc == 0,
            "out_mb": round(out.stat().st_size / 1024 / 1024, 1) if rc == 0 and out.exists() else 0,
            "note": err.strip().split("\n")[-1] if rc != 0 else "",
        })

    # Report
    if args.json:
        print(json.dumps({"duration_s": args.duration, "ffmpeg": ffmpeg, "results": results}, indent=2))
    else:
        print()
        # Group by source for readability.
        for src_label in list(sources) + ["across"]:
            rows = [r for r in results if r["source"] == src_label or
                    (src_label == "across" and r["source"] not in sources)]
            if not rows:
                continue
            print(f"\n── {src_label} ──")
            print(f"{'stage':<22} {'variant':<32} {'sec':>7} {'MB':>7} {'note':<40}")
            print("─" * 110)
            for r in rows:
                ok = "" if r["ok"] else "❌"
                print(f"{r['stage']:<22.22} {r['variant']:<32.32} {r['seconds']:>7.2f} "
                      f"{r['out_mb']:>7.1f} {ok}{r['note'][:38]:<38.38}")

        # Speedup callouts
        def _find(stage, variant_contains, source):
            for r in results:
                if r["source"] == source and r["stage"] == stage and variant_contains in r["variant"]:
                    return r["seconds"]
            return None
        print("\n── verdict ──")
        for src_label in sources:
            sw = _find("encode+decode", "SW-decode", src_label)
            hw = _find("encode+decode", "HW-decode", src_label)
            if sw and hw and hw > 0:
                ratio = sw / hw
                tag = "✅ HW DECODE WINS" if ratio > 1.20 else ("≈ no clear winner" if ratio > 0.90 else "❌ HW decode SLOWER")
                print(f"{src_label:>22}  SW-decode {sw:5.2f}s  vs  HW-decode {hw:5.2f}s  → {ratio:.2f}×  {tag}")
        # Scale case
        sw_scale = next((r["seconds"] for r in results if "SW-decode" in r["variant"] and r["stage"] == "decode+scale+encode"), None)
        hw_scale = next((r["seconds"] for r in results if "HW-decode" in r["variant"] and r["stage"] == "decode+scale+encode"), None)
        if sw_scale and hw_scale and hw_scale > 0:
            r = sw_scale / hw_scale
            tag = "✅ HW DECODE WINS" if r > 1.20 else ("≈ tied" if r > 0.90 else "❌ HW decode hurts")
            print(f"{'4K→1080 scale':>22}  SW {sw_scale:5.2f}s  vs  HW {hw_scale:5.2f}s  → {r:.2f}×  {tag}")
        # VT vs libx264 (master)
        for src_label in sources:
            vt = _find("encode-only", "delivery (VT)", src_label)
            x = _find("encode-only", "master (libx264", src_label)
            if vt and x and vt > 0:
                print(f"{src_label:>22}  VT {vt:5.2f}s  vs  libx264 slow {x:5.2f}s  → {x/vt:.2f}× VT faster")
        print()

    # Cleanup
    for p in WORK.glob("*"):
        try:
            p.unlink()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
